import asyncio
import json
import struct
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from desk_stream.audio import AudioBuffer, IncrementalASR, wav_bytes
from desk_stream.common import Trace
from desk_stream.model import HomeControl, Model
from desk_stream.server import Backend, Conversation, app_for
from desk_stream.speech import speak_stream
from desk_stream.tts_gateway import Jobs, app_for as tts_app

TOKEN = "x" * 32


class Vad:
    def reset(self): pass
    def score(self, frame): return 1.0 if any(frame) else 0.0


class ASR:
    def __init__(self): self.calls = []
    async def decode(self, pcm):
        self.calls.append(pcm)
        return "Hallo, wie geht es dir?"


class LLM:
    def __init__(self): self.messages = []
    async def stream(self, messages, tools=()):
        self.messages.append(list(messages))
        yield {"text": "Hallo, mir geht es sehr gut. "}
        yield {"text": "Wie geht es dir?"}


def config():
    return json.loads((Path(__file__).parents[1] / "config.example.json").read_text())


@pytest.fixture
async def client():
    cfg = config()
    async with aiohttp.ClientSession() as http:
        asr, llm = ASR(), LLM()
        conversation = Conversation(cfg, llm, HomeControl(http, cfg), http)
        backend = Backend(cfg, TOKEN, Vad(), asr, conversation)
        async with TestClient(TestServer(app_for(backend))) as client:
            client.backend, client.asr, client.llm = backend, asr, llm
            yield client


async def connect(client):
    ws = await client.ws_connect("/audio", headers={"Authorization": "Bearer " + TOKEN})
    assert (await ws.receive_json())["state"] == "ready"
    return ws


async def until(ws, state):
    async with asyncio.timeout(3):
        while True:
            obj = await ws.receive_json()
            if obj["state"] == state:
                return obj


async def capture(ws, ident="synthetic-001", silence=False):
    await ws.send_json({"type": "start", "id": ident})
    await until(ws, "recording")
    for i in range(10):
        await ws.send_bytes(struct.pack("<I", i) + (bytes(960) if silence else b"\x01\x00" * 480))
    await ws.send_json({"type": "end"})


async def test_auth_before_upgrade(client):
    with pytest.raises(aiohttp.WSServerHandshakeError) as exc:
        await client.ws_connect("/audio")
    assert exc.value.status == 401
    assert not client.backend.connected
    assert not client.asr.calls


async def test_one_client_and_browser_origin_rejected(client):
    ws = await connect(client)
    with pytest.raises(aiohttp.WSServerHandshakeError) as exc:
        await client.ws_connect("/audio", headers={"Authorization": "Bearer " + TOKEN})
    assert exc.value.status == 409
    await ws.close()
    with pytest.raises(aiohttp.WSServerHandshakeError) as exc:
        await client.ws_connect("/audio", headers={"Authorization": "Bearer " + TOKEN, "Origin": "https://example.com"})
    assert exc.value.status == 403


async def test_two_turns_keep_history_and_reset(client):
    ws = await connect(client)
    await capture(ws)
    result = await until(ws, "complete")
    assert result["preview"] is True
    await until(ws, "ready")
    await capture(ws, "synthetic-002")
    await until(ws, "complete")
    await until(ws, "ready")
    assert len(client.llm.messages[1]) == 4
    await ws.send_json({"type": "reset"})
    await until(ws, "reset")
    assert client.backend.conversation.history == []


async def test_reconnect_preserves_completed_conversation(client):
    ws = await connect(client)
    await capture(ws)
    await until(ws, "complete")
    await until(ws, "ready")
    previous = list(client.backend.conversation.history)
    await ws.close()
    async with asyncio.timeout(3):
        while client.backend.connected:
            await asyncio.sleep(.01)
    ws = await connect(client)
    await capture(ws, "synthetic-reconnect")
    await until(ws, "complete")
    assert client.llm.messages[1][1:-1] == previous


async def test_silence_spends_no_asr_or_llm(client):
    ws = await connect(client)
    await capture(ws, silence=True)
    await until(ws, "no_speech")
    assert not client.asr.calls and not client.llm.messages


async def test_duplicate_request_does_not_replay(client):
    ws = await connect(client)
    await capture(ws)
    await until(ws, "complete")
    await until(ws, "ready")
    await ws.send_json({"type": "start", "id": "synthetic-001"})
    assert (await until(ws, "error"))["reason"] == "duplicate_or_rate_limited"
    assert len(client.asr.calls) == 1


async def test_sequence_gap_aborts_before_asr(client):
    ws = await connect(client)
    await ws.send_json({"type": "start", "id": "synthetic-001"})
    await until(ws, "recording")
    await ws.send_bytes(struct.pack("<I", 4) + bytes(960))
    assert (await until(ws, "error"))["reason"] == "invalid_audio"
    assert not client.asr.calls


async def test_stalled_recording_times_out(client):
    ws = await connect(client)
    await ws.send_json({"type": "start", "id": "synthetic-001"})
    await until(ws, "recording")
    assert (await until(ws, "error"))["reason"] == "capture_timeout"
    assert not client.asr.calls


async def test_disconnect_cancels_inflight_and_allows_reconnect(client):
    event = asyncio.Event()
    cancelled = asyncio.Event()
    async def blocked(pcm):
        event.set()
        try: await asyncio.sleep(20)
        finally: cancelled.set()
    client.asr.decode = blocked
    ws = await connect(client)
    await capture(ws)
    await event.wait()
    await ws.close()
    await asyncio.wait_for(cancelled.wait(), 2)
    await asyncio.sleep(0.02)
    ws2 = await connect(client)
    await ws2.close()


async def test_llm_failure_retains_question_with_interruption_note(client):
    async def broken(messages, tools=()):
        raise RuntimeError("synthetic")
        yield
    client.llm.stream = broken
    ws = await connect(client)
    await capture(ws)
    await until(ws, "error")
    await until(ws, "ready")
    assert client.backend.conversation.history[-2]["role"] == "user"
    assert "Delivery note:" in client.backend.conversation.history[-1]["content"]


def test_audio_bound_and_odd_input():
    buf = AudioBuffer(Vad(), 1)
    with pytest.raises(ValueError): buf.append(b"\0")
    for _ in range(33): buf.append(b"\1\0" * 480)
    with pytest.raises(ValueError): buf.append(b"\1\0" * 480)
    assert len(buf.pcm) <= 32000


async def test_preview_reused_only_for_identical_final_audio():
    asr = ASR()
    inc = IncrementalASR(asr, Trace("test"))
    inc.offer(b"first")
    await asyncio.sleep(0)
    await inc.finish(b"first")
    assert len(asr.calls) == 1
    await inc.finish(b"changed")
    assert asr.calls == [b"first", b"changed"]


async def test_first_sentence_starts_before_generation_finishes():
    started = asyncio.Event()
    spoken = []
    class Speaker:
        async def say(self, text, index):
            spoken.append((index, text))
            started.set()
    async def tokens():
        yield "Dies ist ein vollständiger erster Satz. "
        await asyncio.wait_for(started.wait(), 1)
        yield "Noch ein Satz."
    result = await speak_stream(tokens(), Speaker(), 1000)
    assert len(spoken) == 2 and result.endswith("Noch ein Satz.")


async def test_second_chunk_prepares_while_first_is_playing():
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    spoken = []
    class Speaker:
        async def say(self, text, index):
            spoken.append((index, text))
            if index == 0:
                first_started.set()
                await asyncio.wait_for(second_started.wait(), 1)
            else:
                second_started.set()
    async def tokens():
        yield "Dies ist ein vollständiger erster Satz. "
        await asyncio.wait_for(first_started.wait(), 1)
        yield "Noch ein Satz."
    result = await asyncio.wait_for(speak_stream(tokens(), Speaker(), 1000), 1)
    assert spoken == [(0, "Dies ist ein vollständiger erster Satz."), (1, "Noch ein Satz.")]
    assert result.endswith("Noch ein Satz.")


def test_home_actions_disabled_exact_and_duplicate_validation():
    cfg = config()
    control = HomeControl(None, cfg)
    call = {"name": "home_control", "arguments": {"action": "toggle", "entity": "light.hallway"}}
    with pytest.raises(ValueError): control.validate([call])
    cfg.update(ha_enabled=True, ha_entities=["light.hallway", "lock.front"])
    assert control.validate([call]) == [("toggle", "light.hallway")]
    with pytest.raises(ValueError): control.validate([call, call])
    with pytest.raises(ValueError): control.validate([{"name":"home_control", "arguments":{"action":"on", "entity":"lock.front"}}])


async def test_tool_timeout_is_not_retried(monkeypatch):
    class HTTP:
        def __init__(self): self.count = 0
        def post(self, *args, **kwargs):
            self.count += 1
            raise TimeoutError()
    cfg = config(); cfg.update(ha_enabled=True, ha_entities=["light.test"])
    monkeypatch.setenv("HA_TOKEN", "synthetic")
    http = HTTP()
    result = await HomeControl(http, cfg).execute([{"name":"home_control", "arguments":{"action":"toggle", "entity":"light.test"}}])
    assert result[0]["status"] == "unknown" and http.count == 1


class Player:
    def __init__(self): self.played = []; self.active = 0; self.max_active = 0
    async def synthesize(self, text, voice=""):
        if text == "first": await asyncio.sleep(.03)
        return text.encode()
    async def play(self, audio):
        self.active += 1; self.max_active = max(self.max_active, self.active)
        try:
            self.played.append(audio)
            await asyncio.sleep(.02)
        finally: self.active -= 1


async def test_tts_fifo_even_when_second_synthesis_finishes_first():
    player = Player(); jobs = Jobs(player, tail=0)
    worker = asyncio.create_task(jobs.run())
    jobs.create("a", "first"); jobs.create("b", "second")
    await asyncio.wait_for(jobs.queue.join(), 2)
    assert player.played == [b"first", b"second"] and player.max_active == 1
    assert jobs.public("a")["status"] == "done"
    assert "prepare" not in jobs.jobs["a"]
    worker.cancel(); await asyncio.gather(worker, return_exceptions=True)


async def test_tts_api_auth_idempotency_and_cancel():
    player = Player(); jobs = Jobs(player, tail=0)
    async with TestClient(TestServer(tts_app(jobs, TOKEN))) as client:
        assert (await client.post("/v1/jobs", json={})).status == 401
        headers = {"Authorization":"Bearer " + TOKEN}
        data = {"id":"same-id", "text":"first"}
        assert (await client.post("/v1/jobs", headers=headers, json=data)).status == 202
        assert (await client.post("/v1/jobs", headers=headers, json=data)).status == 202
        assert (await client.post("/v1/jobs", headers=headers, json={**data, "text":"different"})).status == 409
        await jobs.queue.join()
        assert player.played == [b"first"]
        assert (await client.post("/speak", json={"text":"test"})).status == 401


async def test_tts_failure_does_not_replay_and_queue_continues():
    player = Player()
    async def fail(audio):
        player.played.append(audio)
        raise RuntimeError("synthetic failure")
    player.play = fail
    jobs = Jobs(player, tail=0); worker = asyncio.create_task(jobs.run())
    jobs.create("a", "first"); jobs.create("b", "second")
    await jobs.queue.join()
    assert player.played == [b"first", b"second"]
    assert all(jobs.public(k)["status"] == "failed" for k in ("a", "b"))
    worker.cancel(); await asyncio.gather(worker, return_exceptions=True)


async def test_tts_cancel_closes_active_playback_before_idle():
    entered=asyncio.Event(); closed=asyncio.Event()
    player=Player()
    async def blocked(audio):
        entered.set()
        try: await asyncio.sleep(20)
        finally:
            await asyncio.sleep(.02)
            closed.set()
    player.play=blocked
    jobs=Jobs(player,tail=.01);worker=asyncio.create_task(jobs.run())
    jobs.create("a","first");await entered.wait()
    cancel=asyncio.create_task(jobs.cancel("a"))
    await asyncio.sleep(.001)
    assert jobs.busy
    await cancel
    assert closed.is_set() and not jobs.busy
    worker.cancel();await asyncio.gather(worker,return_exceptions=True)


async def test_gateway_unavailable_blocks_capture(client):
    client.backend.cfg["playback"]="homepod"
    client.backend.cfg["tts_url"]="http://127.0.0.1:1"
    ws=await connect(client)
    await ws.send_json({"type":"start","id":"synthetic-001"})
    assert (await until(ws,"error"))["reason"]=="speaker_busy_or_unavailable"
    assert not client.asr.calls


async def test_cancel_recording_does_not_transcribe(client):
    ws=await connect(client)
    await ws.send_json({"type":"start","id":"synthetic-001"})
    await until(ws,"recording")
    await ws.send_bytes(struct.pack("<I",0)+b"\1\0"*480)
    await ws.send_json({"type":"cancel"})
    await until(ws,"ready")
    assert not client.asr.calls


async def test_legacy_and_new_jobs_share_one_queue():
    player=Player();jobs=Jobs(player,tail=0)
    async with TestClient(TestServer(tts_app(jobs,TOKEN,legacy=True))) as client:
        assert (await client.post("/speak",json={"text":"first"},headers={"Authorization":"Bearer "+TOKEN})).status==202
        assert (await client.post("/v1/jobs",json={"id":"second","text":"second"},headers={"Authorization":"Bearer "+TOKEN})).status==202
        await jobs.queue.join()
        assert player.played==[b"first",b"second"] and player.max_active==1


def test_rate_budget_bounds_commands():
    backend=Backend(config(),TOKEN,Vad(),ASR(),None)
    assert all(backend.admit(f"synthetic-{i}") for i in range(40))
    assert not backend.admit("synthetic-41")


def test_trace_contains_no_audio_text_or_credentials(tmp_path):
    path=tmp_path/"trace.jsonl"
    trace=Trace("synthetic",str(path));trace.mark("capture_end",audio_bytes=960)
    data=json.loads(path.read_text())
    assert set(data)=={"id","stage","elapsed_ms","audio_bytes","ts"}
    assert path.stat().st_mode & 0o777 == 0o600


async def test_processing_cancel_emits_one_ready_and_records_cancel(client, tmp_path):
    path=tmp_path/"trace.jsonl";client.backend.cfg["telemetry"]=str(path)
    entered=asyncio.Event()
    async def blocked(pcm):
        entered.set();await asyncio.sleep(20)
    client.asr.decode=blocked
    ws=await connect(client);await capture(ws);await entered.wait()
    await ws.send_json({"type":"cancel"})
    await until(ws,"ready")
    with pytest.raises(TimeoutError):await asyncio.wait_for(ws.receive_json(),.05)
    assert sum(json.loads(l)["stage"]=="cancelled" for l in path.read_text().splitlines())==1


@pytest.mark.parametrize("arguments", [None,42,"light.test",["action","entity"]])
def test_invalid_tool_argument_types_are_cleanly_rejected(arguments):
    cfg=config();cfg.update(ha_enabled=True,ha_entities=["light.test"])
    with pytest.raises(ValueError):HomeControl(None,cfg).validate([{"name":"home_control","arguments":arguments}])


def test_ownership_refuses_lookup_errors(monkeypatch):
    import desk_stream.tts_gateway as g
    monkeypatch.setattr(g.subprocess,"run",lambda *a,**k:SimpleNamespace(returncode=5,stdout="",stderr="permission denied"))
    with pytest.raises(SystemExit):g.require_ownership()


def test_ownership_catches_manual_legacy_listener(monkeypatch):
    import desk_stream.tts_gateway as g
    results=iter([SimpleNamespace(returncode=113,stdout="",stderr="Could not find service"),
                  SimpleNamespace(returncode=0,stdout="999999\n",stderr="")])
    monkeypatch.setattr(g.subprocess,"run",lambda *a,**k:next(results))
    with pytest.raises(SystemExit):g.require_ownership()


async def test_real_tls_trust_and_authentication():
    import os,ssl
    cert,key=os.environ.get("DESK_TEST_CERT"),os.environ.get("DESK_TEST_KEY")
    if not cert or not key:pytest.skip("Set certificate paths to exercise real TLS")
    tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(cert,key)
    cfg=config()
    backend=Backend(cfg,TOKEN,Vad(),ASR(),None)
    server=TestServer(app_for(backend),scheme="https")
    await server.start_server(ssl=tls)
    try:
        async with aiohttp.ClientSession() as http:
            url=str(server.make_url("/audio"))
            with pytest.raises(aiohttp.ClientConnectorCertificateError):await http.ws_connect(url)
            trusted=ssl.create_default_context(cafile=cert)
            with pytest.raises(aiohttp.WSServerHandshakeError) as e:await http.ws_connect(url,ssl=trusted)
            assert e.value.status==401
            async with http.ws_connect(url,ssl=trusted,headers={"Authorization":"Bearer "+TOKEN}) as ws:
                assert (await ws.receive_json())["state"]=="ready"
    finally:await server.close()


@pytest.mark.parametrize("provider", ["ollama", "openai-compatible"])
async def test_fragmented_provider_stream(provider):
    async def endpoint(request):
        data = await request.json()
        assert data["stream"] is True
        if provider == "ollama":
            assert data["think"] is False
            payload = '{"message":{"content":"سلام"},"done":false}\n{"done":true}\n'
        else:
            payload = 'data: {"choices":[{"delta":{"content":"سلام"}}]}\n\ndata: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
        response = web.StreamResponse(); await response.prepare(request)
        for byte in payload.encode(): await response.write(bytes([byte]))
        await response.write_eof(); return response
    app = web.Application(); app.router.add_post("/api/chat", endpoint); app.router.add_post("/chat/completions", endpoint)
    async with TestServer(app) as server, aiohttp.ClientSession() as http:
        cfg = config(); cfg.update(provider=provider,llm_url=str(server.make_url("")))
        out = [e async for e in Model(http,cfg).stream([])]
        assert out == [{"text":"سلام"}]


@pytest.mark.parametrize("seconds", [0, 60, 120])
async def test_ollama_warm_and_reply_share_retention(seconds):
    calls = []
    async def endpoint(request):
        data = await request.json()
        calls.append(data)
        assert data["keep_alive"] == seconds
        if request.path == "/api/generate":
            return web.json_response({"done": True})
        assert data["messages"][1]["content"] == "Silberfuchs"
        return web.Response(text='{"message":{"content":"Silberfuchs"},"done":true}\n')
    app = web.Application()
    app.router.add_post("/api/generate", endpoint)
    app.router.add_post("/api/chat", endpoint)
    async with TestServer(app) as server, aiohttp.ClientSession() as http:
        cfg = config()
        cfg.update(llm_url=str(server.make_url("")), llm_keep_alive_seconds=seconds)
        model = Model(http, cfg)
        await model.warm()
        messages = [{"role": "user", "content": "Projektname?"},
                    {"role": "assistant", "content": "Silberfuchs"},
                    {"role": "user", "content": "Wie heißt es?"}]
        assert [e async for e in model.stream(messages)] == [{"text": "Silberfuchs"}]
    assert len(calls) == 2
