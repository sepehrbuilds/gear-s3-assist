"""Continuous audio must retain FIFO, cancellation and no-replay guarantees."""
import asyncio
import pytest
from aiohttp.test_utils import TestClient, TestServer
from desk_stream.tts_gateway import Jobs, app_for


FIRST = b'first' + b'x'*8187


class Player:
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.played = []
    async def audio_chunks(self, text, voice):
        yield FIRST
        await self.release.wait()
        yield b'last'
    async def play(self, reader):
        assert isinstance(reader, asyncio.StreamReader)
        first = await reader.read(8192)
        self.entered.set()
        self.played.append(first + await reader.read())


async def test_audio_playback_starts_before_generation_finishes():
    p = Player(); jobs = Jobs(p, tail=0)
    worker = asyncio.create_task(jobs.run())
    try:
        jobs.create('stream', 'synthetic', streaming=True)
        await asyncio.wait_for(p.entered.wait(), 1)
        assert jobs.public('stream')['status'] == 'playing'
        assert not any(e['stage']=='tts_ready' for e in jobs.public('stream')['events'])
        p.release.set()
        await asyncio.wait_for(jobs.queue.join(), 1)
        assert p.played == [FIRST+b'last']
        assert jobs.public('stream')['status'] == 'done'
        assert not {'reader','producer','prepare'} & jobs.jobs['stream'].keys()
    finally:
        worker.cancel(); await asyncio.gather(worker, return_exceptions=True)


async def test_stream_cancel_unblocks_reader_and_stops_producer():
    p = Player(); jobs = Jobs(p, tail=0)
    worker = asyncio.create_task(jobs.run())
    try:
        jobs.create('stream', 'synthetic', streaming=True)
        await asyncio.wait_for(p.entered.wait(), 1)
        producer = jobs.jobs['stream']['producer']
        await asyncio.wait_for(jobs.cancel('stream'), 1)
        await asyncio.wait_for(jobs.queue.join(), 1)
        assert producer.done()
        assert jobs.public('stream')['status'] == 'cancelled'
        assert not jobs.busy
    finally:
        worker.cancel(); await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.parametrize('fail_after_audio', [False, True])
async def test_stream_failure_never_replays(fail_after_audio):
    p = Player(); p.release.set()
    async def chunks(text, voice):
        if fail_after_audio: yield FIRST
        raise RuntimeError('synthetic')
    p.audio_chunks = chunks
    jobs = Jobs(p, tail=0); worker = asyncio.create_task(jobs.run())
    try:
        jobs.create('stream', 'synthetic', streaming=True)
        await asyncio.wait_for(jobs.queue.join(), 1)
        assert jobs.public('stream')['status'] == 'failed'
        assert len(p.played) <= 1
        assert not {'reader','producer','prepare'} & jobs.jobs['stream'].keys()
    finally:
        worker.cancel(); await asyncio.gather(worker, return_exceptions=True)


async def test_stream_bound_and_idempotency():
    p = Player()
    async def too_big(text, voice): yield bytes(4_000_001)
    p.audio_chunks = too_big
    jobs = Jobs(p, tail=0); worker = asyncio.create_task(jobs.run())
    try:
        jobs.create('stream', 'synthetic', streaming=True)
        await asyncio.wait_for(jobs.queue.join(), 1)
        assert jobs.public('stream')['status']=='failed'
        assert not p.played
        from aiohttp import web
        with pytest.raises(web.HTTPConflict): jobs.create('stream', 'synthetic')
    finally:
        worker.cancel(); await asyncio.gather(worker, return_exceptions=True)


async def test_stream_api_accepts_only_boolean_edge_and_keeps_default():
    p = Player(); p.release.set(); jobs = Jobs(p, tail=0)
    token = 'x'*32
    headers = {'Authorization':'Bearer '+token}
    async with TestClient(TestServer(app_for(jobs, token))) as client:
        for flag in ('true', 1, None):
            r = await client.post('/v1/jobs', headers=headers,
                json={'id':'synthetic','text':'synthetic','streaming':flag})
            assert r.status == 400
        r = await client.post('/v1/jobs', headers=headers,
            json={'id':'synthetic','text':'synthetic','streaming':True,'provider':'gemini'})
        assert r.status == 400
        r = await client.post('/v1/jobs', headers=headers,
            json={'id':'synthetic','text':'synthetic','streaming':True})
        assert r.status == 202
        await jobs.queue.join()
        assert p.played == [FIRST+b'last']


async def test_mp3_adapter_reads_exact_bytes_and_rewinds_prefix():
    import io
    from desk_stream.mp3_stream import MP3Stream
    reader = asyncio.StreamReader()
    source = MP3Stream(reader)
    payload = bytes(range(256))*300
    async def produce():
        for i in range(0, len(payload), 700):
            reader.feed_data(payload[i:i+700]); await asyncio.sleep(0)
        reader.feed_eof()
    producer = asyncio.create_task(produce())
    assert await asyncio.to_thread(source.read, 100) == payload[:100]
    assert source.seek(0) == 0
    assert await asyncio.to_thread(source.read, 65536) == payload[:65536]
    assert await asyncio.to_thread(source.read, 20000) == payload[65536:]
    assert len(source.head) == 65536
    with pytest.raises(io.UnsupportedOperation): source.seek(70000)
    with pytest.raises(io.UnsupportedOperation): source.seek(0, io.SEEK_END)
    await producer


async def test_actual_mp3_decoder_handles_fragmented_stream():
    import shutil
    from pyatv.protocols.raop.audio_source import open_source
    from desk_stream.mp3_stream import MP3Stream
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg: pytest.skip('ffmpeg needed for synthetic MP3 fixture')
    process = await asyncio.create_subprocess_exec(ffmpeg, '-v', 'error', '-f', 'lavfi',
        '-i', 'sine=frequency=440:duration=1', '-ar', '24000', '-ac', '1',
        '-b:a', '48k', '-id3v2_version', '0', '-write_xing', '0', '-f', 'mp3', 'pipe:1',
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    payload, _ = await process.communicate()
    assert process.returncode == 0
    reader = asyncio.StreamReader()
    async def produce():
        for i in range(0,len(payload),333):
            reader.feed_data(payload[i:i+333]); await asyncio.sleep(.001)
        reader.feed_eof()
    producer = asyncio.create_task(produce())
    source = None
    try:
        async with asyncio.timeout(3):
            source = await open_source(MP3Stream(reader), 44100, 2, 2)
            size = 0
            while frame := await source.readframes(352): size += len(frame)
            assert size >= 44100*2*2
    finally:
        reader.feed_eof(); producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)
        if source: await source.close()
