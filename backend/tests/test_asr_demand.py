import asyncio
import sys

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer, TestClient

from desk_stream.asr_demand import Demand, application

FAKE_SERVER = '''
from aiohttp import web
import asyncio, sys
async def health(r): return web.json_response({'status':'ok'})
async def infer(r):
    await asyncio.sleep(.2)
    return web.Response(body=await r.read(), content_type='application/json')
a=web.Application(); a.router.add_get('/health',health); a.router.add_post('/inference',infer)
web.run_app(a, host='127.0.0.1', port=int(sys.argv[1]), access_log=None)
'''


@pytest.mark.asyncio
async def test_lazy_start_reuse_idle_and_reload(unused_tcp_port):
    command = [sys.executable, '-c', FAKE_SERVER, str(unused_tcp_port)]
    demand = Demand(command, f'http://127.0.0.1:{unused_tcp_port}', idle=.1, startup=5)
    async with TestClient(TestServer(application(demand))) as client:
        assert demand.process is None
        async def infer():
            response = await client.post('/inference', data=b'{"text":"synthetic"}',
                                         headers={'Content-Type': 'application/json'})
            assert response.status == 200
            assert await response.json() == {'text': 'synthetic'}
        await infer()
        first = demand.process
        # Idle time must not kill an active request, even though it exceeds TTL.
        await infer()
        assert demand.process is first
        async with asyncio.timeout(3):
            while demand.process is not None:
                await asyncio.sleep(.05)
        assert first.returncode is not None
        await infer()
        second = demand.process
        assert second.pid != first.pid
    assert second.returncode is not None


@pytest.mark.asyncio
async def test_startup_failure_leaves_no_child(unused_tcp_port):
    demand = Demand([sys.executable, '-c', 'raise SystemExit(1)'],
                    f'http://127.0.0.1:{unused_tcp_port}', startup=2)
    async with aiohttp.ClientSession() as http:
        with pytest.raises(RuntimeError):
            await demand.start(http)
    assert demand.process is None


async def _wait_listening(http, url):
    async with asyncio.timeout(5):
        while True:
            try:
                async with http.get(url + '/health'):
                    return
            except aiohttp.ClientConnectorError:
                await asyncio.sleep(.05)


@pytest.mark.asyncio
async def test_orphaned_worker_is_reclaimed(unused_tcp_port):
    url = f'http://127.0.0.1:{unused_tcp_port}'
    command = [sys.executable, '-c', FAKE_SERVER, str(unused_tcp_port)]
    orphan = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.DEVNULL,
                                                  stderr=asyncio.subprocess.DEVNULL)
    try:
        async with aiohttp.ClientSession() as http:
            await _wait_listening(http, url)
            demand = Demand(command, url, startup=5)
            await demand.start(http)
            assert orphan.returncode is not None or await asyncio.wait_for(orphan.wait(), 2) is not None
            assert demand.process is not None and demand.process.pid != orphan.pid
            await demand.stop()
    finally:
        if orphan.returncode is None:
            orphan.kill()
            await orphan.wait()


@pytest.mark.asyncio
async def test_foreign_listener_is_not_touched(unused_tcp_port):
    url = f'http://127.0.0.1:{unused_tcp_port}'
    foreign = await asyncio.create_subprocess_exec(sys.executable, '-c', FAKE_SERVER, str(unused_tcp_port),
                                                   stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    try:
        async with aiohttp.ClientSession() as http:
            await _wait_listening(http, url)
            demand = Demand(['/nonexistent/whisper-server'], url, startup=2)
            with pytest.raises(RuntimeError):
                await demand.start(http)
            assert foreign.returncode is None
            assert demand.process is None
    finally:
        foreign.kill()
        await foreign.wait()
