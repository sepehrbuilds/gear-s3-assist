"""Loopback-only Whisper request listener; model process exists only while needed."""
import argparse
import asyncio
import os
import subprocess
import time

import aiohttp
from aiohttp import web

HTTP = web.AppKey("http", aiohttp.ClientSession)


class Demand:
    def __init__(self, command, url, idle=60, startup=20, worker_log=""):
        self.command, self.url = command, url
        self.idle, self.startup = idle, startup
        self.worker_log = worker_log
        self.lock = asyncio.Lock()
        self.process = None
        self.last = time.monotonic()

    async def stop(self):
        if self.process is not None:
            if self.process.returncode is None:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), 5)
                except TimeoutError:
                    self.process.kill()
                    await self.process.wait()
            self.process = None

    def occupant(self):
        """PID and command of whatever listens on the private worker port, if anything."""
        port = self.url.rsplit(':', 1)[1]
        pids = subprocess.run(['/usr/sbin/lsof', '-nP', '-t', f'-iTCP:{port}', '-sTCP:LISTEN'],
                              capture_output=True, text=True, timeout=3).stdout.split()
        if not pids:
            return None, ''
        pid = int(pids[0])
        command = subprocess.run(['/bin/ps', '-o', 'command=', '-p', str(pid)],
                                 capture_output=True, text=True, timeout=3).stdout.strip()
        return pid, command

    async def reclaim(self):
        """Kill an orphaned worker from an earlier listener; never touch anything else.

        Without this, a listener that died without SIGTERM leaves a worker holding
        the port and every later request fails with 503 until someone intervenes.
        """
        pid, command = self.occupant()
        if pid is None:
            return
        # ps reports argv[0] as launched (framework Python re-executes under a
        # different name), so match the binary basename plus every plain argument
        # of our own worker command: model path, host, port and flags.
        argv0 = os.path.basename(command.split(' ', 1)[0]).lower() if command else ''
        tokens = [t for t in self.command[1:] if '\n' not in t]
        if os.path.basename(self.command[0]).lower() not in argv0 or not all(t in command for t in tokens):
            raise RuntimeError('private ASR port occupied by a foreign process')
        os.kill(pid, 15)
        for _ in range(50):
            await asyncio.sleep(.1)
            if self.occupant()[0] is None:
                return
        os.kill(pid, 9)
        await asyncio.sleep(.5)
        if self.occupant()[0] is not None:
            raise RuntimeError('orphaned ASR worker would not exit')

    async def start(self, http):
        if self.process is not None and self.process.returncode is None:
            return
        await self.reclaim()
        stderr = asyncio.subprocess.DEVNULL
        if self.worker_log:
            # whisper-server stderr carries load/timing diagnostics, no transcript text.
            stderr = os.open(self.worker_log, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        try:
            self.process = await asyncio.create_subprocess_exec(
                *self.command, stdout=asyncio.subprocess.DEVNULL, stderr=stderr)
        finally:
            if stderr is not asyncio.subprocess.DEVNULL:
                os.close(stderr)
        try:
            async with asyncio.timeout(self.startup):
                while True:
                    if self.process.returncode is not None:
                        raise RuntimeError('Whisper exited during startup')
                    try:
                        async with http.get(self.url + '/health', timeout=aiohttp.ClientTimeout(total=1)) as r:
                            if r.status == 200:
                                return
                    except (aiohttp.ClientError, TimeoutError):
                        pass
                    await asyncio.sleep(.1)
        except BaseException:
            await self.stop()
            raise

    async def infer(self, request):
        if "Content-Type" not in request.headers:
            raise web.HTTPBadRequest(text="Content-Type required")
        body = await request.read()
        # Speculation is expendable: never queue it ahead of real recognition.
        if request.headers.get('X-ASR-Speculative') == '1' and self.lock.locked():
            raise web.HTTPTooManyRequests()
        async with self.lock:
            try:
                async with asyncio.timeout(30):
                    await self.start(request.app[HTTP])
                    return await self.forward(request, body)
            except (aiohttp.ClientError, TimeoutError, RuntimeError):
                await self.stop()
                raise web.HTTPServiceUnavailable(text='Speech model unavailable')
            finally:
                self.last = time.monotonic()

    async def warm(self, request):
        # Loopback listener only. Refuse browser-triggered warm-ups; never accept
        # a model path, command, transcript or inference options from this call.
        if request.headers.get('Origin'):
            raise web.HTTPForbidden()
        async with self.lock:
            try:
                async with asyncio.timeout(self.startup + 1):
                    await self.start(request.app[HTTP])
                return web.json_response({'status': 'ok', 'model_loaded': True})
            except (aiohttp.ClientError, TimeoutError, RuntimeError):
                await self.stop()
                raise web.HTTPServiceUnavailable(text='Speech model unavailable')
            finally:
                self.last = time.monotonic()

    async def forward(self, request, body):
        async with request.app[HTTP].post(
                self.url + '/inference', data=body,
                headers={'Content-Type': request.headers['Content-Type']},
                timeout=aiohttp.ClientTimeout(total=30)) as r:
            return web.Response(
                body=await r.read(), status=r.status,
                headers={'Content-Type': r.headers.get('Content-Type', 'application/json')})

    async def reap(self):
        while True:
            await asyncio.sleep(min(1, self.idle))
            if self.lock.locked():
                continue
            async with self.lock:
                if time.monotonic() - self.last >= self.idle:
                    await self.stop()


def application(demand):
    app = web.Application(client_max_size=16 * 1024 * 1024)
    app.router.add_post('/inference', demand.infer)
    app.router.add_post('/warm', demand.warm)
    async def health(request):
        return web.json_response({'status': 'ok', 'model_loaded':
            demand.process is not None and demand.process.returncode is None})
    app.router.add_get('/health', health)
    async def lifecycle(app):
        async with aiohttp.ClientSession() as http:
            app[HTTP] = http
            task = asyncio.create_task(demand.reap())
            try:
                yield
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await demand.stop()
    app.cleanup_ctx.append(lifecycle)
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--idle-seconds', type=float, default=60)
    parser.add_argument('--port', type=int, default=8584)
    parser.add_argument('--worker-port', type=int, default=8585)
    parser.add_argument('--model', required=True)
    parser.add_argument('--worker-log', default='', help='append whisper-server stderr here (0600)')
    args = parser.parse_args()
    command = ['/opt/homebrew/bin/whisper-server', '-m', args.model,
               '--host', '127.0.0.1', '--port', str(args.worker_port),
               '-l', 'auto', '-nt', '-sns']
    web.run_app(application(Demand(command, f'http://127.0.0.1:{args.worker_port}',
                                  idle=args.idle_seconds, worker_log=args.worker_log)),
                host='127.0.0.1', port=args.port, access_log=None)


if __name__ == '__main__':
    main()
