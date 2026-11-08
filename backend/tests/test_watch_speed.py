"""Pause recognition must never spend a command before explicit Send."""
import asyncio
import struct

import aiohttp
from aiohttp.test_utils import TestClient, TestServer

from desk_stream.asr_demand import Demand, application
from desk_stream.audio import IncrementalASR
from desk_stream.common import Trace
from test_pipeline import client, connect, until


async def test_pause_preview_waits_for_send_and_uses_identical_final(client):
    client.backend.cfg.update(watch_mode=True, watch_asr_overlap=True)
    ws = await connect(client)
    await ws.send_json({'type':'start', 'id':'synthetic-pause', 'protocolVersion':2})
    await until(ws, 'recording')
    for i in range(22):
        await ws.send_bytes(struct.pack('<I', i) + (b'\1\0'*480 if i < 10 else bytes(960)))
    async with asyncio.timeout(1):
        while not client.asr.calls:
            await asyncio.sleep(.005)
    assert not client.llm.messages
    await ws.send_json({'type':'end'})
    await until(ws, 'complete')
    assert len(client.asr.calls) == 1
    assert len(client.llm.messages) == 1


async def test_speech_resumes_after_preview_final_covers_all_audio(client):
    client.backend.cfg.update(watch_mode=True, watch_asr_overlap=True)
    ws = await connect(client)
    await ws.send_json({'type':'start', 'id':'synthetic-resume', 'protocolVersion':2})
    await until(ws, 'recording')
    for i in range(32):
        speech = i < 10 or i >= 22
        await ws.send_bytes(struct.pack('<I', i) + (b'\1\0'*480 if speech else bytes(960)))
    await ws.send_json({'type':'end'})
    await until(ws, 'complete')
    assert len(client.asr.calls) == 2
    assert len(client.asr.calls[-1]) == 32 * 960


async def test_cancel_closes_speculative_decode_without_command(client):
    client.backend.cfg.update(watch_mode=True, watch_asr_overlap=True)
    entered, cancelled = asyncio.Event(), asyncio.Event()
    async def blocked(pcm):
        entered.set()
        try: await asyncio.sleep(20)
        finally: cancelled.set()
    client.asr.decode = blocked
    ws = await connect(client)
    await ws.send_json({'type':'start', 'id':'synthetic-cancel', 'protocolVersion':2})
    await until(ws, 'recording')
    for i in range(22):
        await ws.send_bytes(struct.pack('<I', i) + (b'\1\0'*480 if i < 10 else bytes(960)))
    await asyncio.wait_for(entered.wait(), 1)
    await ws.send_json({'type':'cancel'})
    await until(ws, 'ready')
    assert cancelled.is_set() and not client.llm.messages


async def test_warm_serializes_with_inference_and_refuses_origin():
    class Fake(Demand):
        def __init__(self):
            super().__init__([], 'http://127.0.0.1:1')
            self.starts = 0
        async def start(self, http):
            self.starts += 1
        async def stop(self): pass
    demand = Fake()
    async with TestClient(TestServer(application(demand))) as client:
        assert (await client.post('/warm', headers={'Origin':'https://example.org'})).status == 403
        assert demand.starts == 0
        await demand.lock.acquire()
        task = asyncio.create_task(client.post('/warm'))
        await asyncio.sleep(.02)
        assert demand.starts == 0
        demand.lock.release()
        response = await task
        assert response.status == 200 and demand.starts == 1
        assert (await response.json())['model_loaded']


async def test_speculative_request_refused_while_worker_busy():
    class Fake(Demand):
        async def stop(self): pass
    demand = Fake([], 'http://127.0.0.1:1')
    async with TestClient(TestServer(application(demand))) as client:
        await demand.lock.acquire()
        try:
            response = await client.post('/inference', data=b'synthetic',
                headers={'Content-Type':'audio/wav', 'X-ASR-Speculative':'1'})
            assert response.status == 429
        finally: demand.lock.release()


async def test_preview_failure_recovers_using_final_decode():
    class ASR:
        async def preview(self, pcm): raise RuntimeError('synthetic')
        async def decode(self, pcm): return 'final'
    inc = IncrementalASR(ASR(), Trace('synthetic'))
    inc.offer(b'first')
    assert await inc.finish(b'first') == 'final'
    await inc.close()


async def test_warm_only_never_previews_or_delays_final(client):
    from desk_stream.audio import Whisper
    from test_pipeline import ASR
    warm_started, warm_cancelled = asyncio.Event(), asyncio.Event()
    class WarmASR(Whisper, ASR):
        def __init__(self): ASR.__init__(self)
        async def warm(self, trace):
            warm_started.set()
            try: await asyncio.sleep(20)
            finally: warm_cancelled.set()
        async def decode(self, pcm): return await ASR.decode(self, pcm)
    asr = WarmASR()
    client.backend.asr = asr
    client.backend.cfg.update(watch_mode=True, watch_asr_warm=True, watch_asr_overlap=False)
    ws = await connect(client)
    await ws.send_json({'type':'start','id':'synthetic-warm-only','protocolVersion':2})
    await until(ws, 'recording')
    await asyncio.wait_for(warm_started.wait(), 1)
    for i in range(22):
        await ws.send_bytes(struct.pack('<I',i)+(b'\1\0'*480 if i < 10 else bytes(960)))
    await asyncio.sleep(.02)
    assert not asr.calls and not client.llm.messages
    await ws.send_json({'type':'end'})
    await until(ws, 'complete')
    await until(ws, 'ready')
    assert len(asr.calls) == 1 and warm_cancelled.is_set()
