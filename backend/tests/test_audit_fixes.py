import asyncio
import json
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from desk_stream.common import Trace
from desk_stream.audio import Whisper
from desk_stream.model import HomeControl
from desk_stream.server import Conversation
from desk_stream.tts_gateway import Jobs, app_for
from desk_stream.speech import Speech

TOKEN = "x" * 32
LEGACY = "y" * 32


class Player:
    async def synthesize(self, text, voice=""):
        return text.encode()

    async def play(self, audio):
        pass


async def test_cancelled_queue_entries_cannot_create_orphans():
    entered, release = asyncio.Event(), asyncio.Event()
    player = Player()
    async def blocked(audio):
        entered.set()
        await release.wait()
    player.play = blocked
    jobs = Jobs(player, tail=0)
    worker = asyncio.create_task(jobs.run())
    try:
        jobs.create("active", "synthetic")
        await entered.wait()
        for i in range(4):
            ident = f"cancelled-{i}"
            jobs.create(ident, "synthetic")
            await jobs.cancel(ident)
        with pytest.raises(web.HTTPTooManyRequests):
            jobs.create("overflow", "synthetic")
        assert "overflow" not in jobs.jobs
        release.set()
        await asyncio.wait_for(jobs.queue.join(), 2)
        assert not jobs.busy
        jobs.create("recovery", "synthetic")
        await asyncio.wait_for(jobs.queue.join(), 2)
        assert jobs.public("recovery")["status"] == "done"
    finally:
        release.set()
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.parametrize("text", ["س" * 1400, "س" * 3000, "😀" * 3000])
async def test_unicode_payload_character_limit_survives_json_escaping(text):
    jobs = Jobs(Player(), tail=0)
    async with TestClient(TestServer(app_for(jobs, TOKEN))) as client:
        async with client.post("/v1/jobs", json={"id": "unicode", "text": text},
                               headers={"Authorization": "Bearer " + TOKEN}) as response:
            assert response.status == 202
        await jobs.queue.join()
        assert jobs.public("unicode")["status"] == "done"


async def test_scoped_legacy_credential_cannot_read_or_create_jobs():
    jobs = Jobs(Player(), tail=0)
    async with TestClient(TestServer(app_for(jobs, TOKEN, legacy=True, legacy_token=LEGACY))) as client:
        body = {"text": "synthetic"}
        assert (await client.post("/speak", json=body)).status == 401
        assert (await client.post("/speak", json=body, headers={"Authorization": "Bearer ☃"})).status == 401
        headers = {"Authorization": "Bearer " + LEGACY}
        assert (await client.post("/speak", json=body, headers=headers)).status == 202
        assert (await client.get("/v1/jobs/unknown", headers=headers)).status == 401
        assert (await client.post("/v1/jobs", json={"id": "denied", **body}, headers=headers)).status == 401
        await jobs.queue.join()


async def test_failure_is_safe_and_visible_without_blocking_recovery(tmp_path):
    class Broken(Player):
        async def synthesize(self, text, voice=""):
            raise aiohttp.ClientResponseError(None, (), status=403, message="SECRET URL AND TRANSCRIPT")
    path = tmp_path / "jobs.jsonl"
    jobs = Jobs(Broken(), tail=0, telemetry=str(path))
    async with TestClient(TestServer(app_for(jobs, TOKEN))) as client:
        jobs.create("failure", "private synthetic text")
        await jobs.queue.join()
        state = await (await client.get("/health")).json()
        assert state["status"] == "ok" and not state["busy"]
        assert state["failure_active"]
        assert state["last_failure"]["http_status"] == 403
        assert state["last_failure"]["failed_stage"] == "synthesis"
        assert "id" not in state["last_failure"]
        data = path.read_text()
        assert "SECRET" not in data and "private synthetic text" not in data
        assert all("ts" in json.loads(line) for line in data.splitlines())
        jobs.player = Player()
        jobs.create("recovery", "synthetic")
        await jobs.queue.join()
        assert not (await (await client.get("/health")).json())["failure_active"]
        jobs.worker.cancel()
        await asyncio.gather(jobs.worker, return_exceptions=True)
        assert (await client.get("/health")).status == 503


async def test_partial_delivery_keeps_only_confirmed_chunk_and_question(monkeypatch):
    class PartialPlayer(Player):
        count = 0
        async def play(self, audio):
            self.count += 1
            if self.count == 2:
                raise RuntimeError("synthetic second chunk failure")
    class Model:
        async def stream(self, messages, tools=()):
            yield {"text": "Dies ist der bestätigte erste Satz. "}
            yield {"text": "Dieser zweite Satz wird nicht bestätigt."}
    monkeypatch.setenv("AUDIT_TOKEN", TOKEN)
    cfg = json.loads((Path(__file__).parents[1] / "config.example.json").read_text())
    jobs = Jobs(PartialPlayer(), tail=0)
    async with TestServer(app_for(jobs, TOKEN)) as server, aiohttp.ClientSession() as http:
        cfg.update(playback="homepod", tts_url=str(server.make_url("")), tts_token_env="AUDIT_TOKEN")
        convo = Conversation(cfg, Model(), HomeControl(http, cfg), http)
        async def send(*args, **kwargs):
            pass
        with pytest.raises(RuntimeError):
            await convo.answer("Synthetic question", Trace("partial"), send)
        assert convo.history[-2]["content"] == "Synthetic question"
        answer = convo.history[-1]["content"]
        assert answer.startswith("Dies ist der bestätigte erste Satz.")
        assert "Dieser zweite Satz" not in answer
        assert "Delivery note:" in answer


@pytest.mark.parametrize("detected,expected", [("ar", ["auto", "fa"]), ("de", ["auto"])])
async def test_persian_hint_retries_only_arabic_ambiguity_and_keeps_language_switch(detected, expected):
    calls = []
    async def endpoint(request):
        data = await request.post()
        calls.append(data["language"])
        assert data["prompt"] == "اسم پروژه من آفتاب است."
        text = "اسم پروژه من چیست؟" if data["language"] == "fa" else "Wie heißt mein Projekt?"
        return web.json_response({"text": text, "detected_language": detected})
    app = web.Application()
    app.router.add_post("/inference", endpoint)
    async with TestServer(app) as server, aiohttp.ClientSession() as http:
        asr = Whisper(http, str(server.make_url("/inference")))
        scoped = asr.contextual("اسم پروژه من آفتاب است.", Trace("language"))
        await scoped.decode(bytes(32000))
        assert calls == expected
        assert asr.language == "auto"


async def test_synthesis_check_is_authenticated_and_never_plays():
    class NoPlayback(Player):
        async def play(self, audio):
            raise AssertionError("health check must not play audio")
    jobs = Jobs(NoPlayback(), tail=0)
    async with TestClient(TestServer(app_for(jobs, TOKEN, legacy=True, legacy_token=LEGACY))) as client:
        assert (await client.post("/v1/check")).status == 401
        assert (await client.post("/v1/check", headers={"Authorization": "Bearer " + LEGACY})).status == 401
        response = await client.post("/v1/check", headers={"Authorization": "Bearer " + TOKEN})
        assert response.status == 200
        assert (await response.json())["synthesis"]
        assert not jobs.jobs and not jobs.busy


async def test_prompt_budget_drops_whole_oldest_pairs_without_changing_current_request():
    class Model:
        messages = None
        async def stream(self, messages, tools=()):
            self.messages = messages
            yield {"text": "Kurze synthetische Antwort."}
    cfg = json.loads((Path(__file__).parents[1] / "config.example.json").read_text())
    model = Model()
    async with aiohttp.ClientSession() as http:
        convo = Conversation(cfg, model, HomeControl(http, cfg), http)
        for i in range(4):
            convo.commit(f"old-{i}" + "س" * 1500, "answer")
        async def send(*args, **kwargs):
            pass
        await convo.answer("Current question", Trace("budget"), send)
        assert model.messages[-1]["content"] == "Current question"
        assert not any(m["content"].startswith("old-0") for m in model.messages)
        assert any(m["content"].startswith("old-3") for m in model.messages)
        assert [m["role"] for m in model.messages[1:-1]] == ["user", "assistant"] * ((len(model.messages) - 2) // 2)
        assert sum(len(m["content"].encode()) for m in model.messages) <= 7000


async def test_cancelling_reply_does_not_start_queued_continuation(monkeypatch):
    entered = asyncio.Event()
    played = []
    class Blocked(Player):
        async def play(self, audio):
            played.append(audio)
            entered.set()
            await asyncio.Event().wait()
    monkeypatch.setenv("AUDIT_TOKEN", TOKEN)
    jobs = Jobs(Blocked(), tail=.01)
    async with TestServer(app_for(jobs, TOKEN)) as server, aiohttp.ClientSession() as http:
        cfg = {"playback": "homepod", "tts_url": str(server.make_url("")), "tts_token_env": "AUDIT_TOKEN"}
        speech = Speech(http, cfg, Trace("cancel-order"))
        first = asyncio.create_task(speech.say("first", 0))
        await entered.wait()
        second = asyncio.create_task(speech.say("second", 1))
        async with asyncio.timeout(2):
            while len(jobs.jobs) < 2:
                await asyncio.sleep(.001)
        await speech.cancel()
        await jobs.queue.join()
        assert played == [b"first"]
        for task in (first, second):
            task.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
