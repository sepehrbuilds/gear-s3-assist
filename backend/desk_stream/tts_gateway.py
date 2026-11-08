"""Queued speech gateway with legacy single-owner compatibility guards.

Legacy POST /speak is optional. New authenticated jobs expose completion and
serialize ALL accepted playback. Never run live beside the old TTS process.
"""
import argparse
import asyncio
import base64
import contextlib
import hashlib
import io
import os
import re
import subprocess
import time
import uuid

import aiohttp
from aiohttp import web

from .common import Trace, authorized, token_from_env, failure_fields
from .mp3_stream import MP3Stream


def voice_for(text):
    if os.environ.get("TTS_AUTO_VOICE", "1") == "0":
        return os.environ.get("TTS_VOICE", "de-DE-SeraphinaMultilingualNeural")
    if any("\u0600" <= c <= "\u06ff" for c in text):
        return os.environ.get("TTS_VOICE_FA", "fa-IR-DilaraNeural")
    en = {"the", "you", "your", "is", "are", "this", "that", "can", "hello", "it"}
    de = {"die", "der", "das", "ist", "du", "ich", "ein", "und", "nicht", "kann"}
    words = set(re.findall(r"\w+", text.lower()))
    return os.environ.get("TTS_VOICE_EN", "en-US-AvaMultilingualNeural") if len(words & en) > len(words & de) else os.environ.get("TTS_VOICE_DE", os.environ.get("TTS_VOICE", "de-DE-SeraphinaMultilingualNeural"))


class HomePod:
    def __init__(self, host):
        self.host, self.device = host, None

    async def audio_chunks(self, text, voice=''):
        """MP3 only; preserve the working providers for ordinary buffered jobs."""
        import edge_tts
        async with asyncio.timeout(25):
            async for part in edge_tts.Communicate(text, voice or voice_for(text)).stream():
                if part['type'] == 'audio':
                    yield part['data']

    async def synthesize(self, text, voice="", provider="edge"):
        if provider == "elevenlabs":
            key = os.environ.get("ELEVENLABS_API_KEY", "")
            if not key:
                raise ValueError("ElevenLabs credential missing")
            voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "cgSgspJ2msm6clMCkdW9")
            if not re.fullmatch(r"[a-zA-Z0-9_-]+", voice_id):
                raise ValueError("invalid voice id")
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as http:
                async with http.post("https://api.elevenlabs.io/v1/text-to-speech/" + voice_id,
                                     headers={"xi-api-key": key}, json={"text":text,
                                     "model_id":os.environ.get("ELEVENLABS_MODEL", "eleven_turbo_v2_5"),
                                     "output_format":"mp3_44100_128"}) as response:
                    response.raise_for_status()
                    audio = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        audio.extend(chunk)
                        if len(audio) > 4_000_000:
                            raise ValueError("TTS output too large")
                    if not audio:
                        raise ValueError("no TTS audio")
                    return bytes(audio)
        if provider == "gemini":
            try:
                return await self.synthesize_gemini(text)
            except Exception as error:
                # Free tier is 10 requests/day/model: 429s and outages must never silence a reply.
                print("[TTS] gemini failed (%s); falling back to edge" % type(error).__name__, flush=True)
        import edge_tts
        audio = bytearray()
        async with asyncio.timeout(25):
            async for part in edge_tts.Communicate(text, voice or voice_for(text)).stream():
                if part["type"] == "audio":
                    audio.extend(part["data"])
                    if len(audio) > 4_000_000:
                        raise ValueError("TTS output too large")
        if not audio:
            raise ValueError("no TTS audio")
        return bytes(audio)

    async def synthesize_gemini(self, text):
        key = os.environ.get("GEMINI_API_KEY", "")
        model = os.environ.get("GEMINI_TTS_MODEL", "gemini-3.8-flash-lite-tts")
        voice = os.environ.get("GEMINI_TTS_VOICE", "Kore")
        if not key:
            raise ValueError("Gemini credential missing")
        if not re.fullmatch(r"[a-zA-Z0-9._-]+", model) or not re.fullmatch(r"[A-Za-z]+", voice):
            raise ValueError("invalid model or voice")
        body = {"contents": [{"parts": [{"text": text}]}],
                "generationConfig": {"responseModalities": ["AUDIO"],
                                     "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as http:
            async with http.post("https://generativelanguage.googleapis.com/v1beta/models/" + model + ":generateContent",
                                 headers={"x-goog-api-key": key}, json=body) as response:
                if response.status != 200:
                    raise RuntimeError("HTTP %d" % response.status)
                data = await response.json()
        part = data["candidates"][0]["content"]["parts"][0]["inlineData"]
        if part.get("mimeType") != "audio/wav":
            raise ValueError("unexpected audio type %s" % part.get("mimeType"))
        audio = base64.b64decode(part["data"], validate=True)
        if not audio.startswith(b"RIFF") or len(audio) > 4_000_000:
            raise ValueError("bad TTS audio")
        return audio

    async def play(self, audio):
        import pyatv
        if self.device is None:
            devices = await pyatv.scan(asyncio.get_running_loop(), timeout=5, hosts=[self.host])
            if not devices:
                raise RuntimeError("HomePod unavailable")
            self.device = devices[0]
        atv = None
        try:
            atv = await pyatv.connect(self.device, asyncio.get_running_loop())
            # Completion means pyatv finished streaming, NOT microphone-measured
            # audible completion. Hardware validation must establish tail padding.
            source = MP3Stream(audio) if isinstance(audio, asyncio.StreamReader) else io.BytesIO(audio)
            await atv.stream.stream_file(source)
        except BaseException:
            self.device = None
            raise  # no automatic replay after uncertain/partial playback
        finally:
            if atv:
                tasks = atv.close()
                if tasks:
                    with contextlib.suppress(TimeoutError):
                        async with asyncio.timeout(3):
                            await asyncio.gather(*tasks, return_exceptions=True)


class Jobs:
    def __init__(self, player, limit=4, tail=0.5, telemetry=""):
        self.player, self.limit, self.tail = player, limit, tail
        self.jobs = {}
        self.queue = asyncio.Queue(maxsize=limit)
        self.worker = None
        self.telemetry = telemetry
        self.last_failure = None
        self.last_success = None
        self.failure_active = False

    @property
    def busy(self):
        return any(j["status"] not in ("done", "failed", "cancelled") for j in self.jobs.values())

    def create(self, ident, text, voice="", provider="edge", streaming=False):
        now = time.monotonic()
        for key in list(self.jobs):
            j = self.jobs[key]
            if now - j["created"] > 600 and j["status"] in ("done", "failed", "cancelled"):
                del self.jobs[key]
        digest = hashlib.sha256((text + "\0" + voice + "\0" + provider).encode()).hexdigest()
        if streaming:
            if provider != 'edge':
                raise web.HTTPBadRequest()
            digest += '-stream'
        if ident in self.jobs:
            if self.jobs[ident]["digest"] != digest:
                raise web.HTTPConflict()
            return self.public(ident)
        if self.queue.full() or len(self.jobs) >= 256 or sum(j["status"] not in ("done", "failed", "cancelled") for j in self.jobs.values()) >= self.limit:
            raise web.HTTPTooManyRequests()
        trace = Trace(ident, self.telemetry)
        trace.mark("tts_accepted")
        job = {"id": ident, "status": "queued", "created": now, "digest": digest,
               "trace": trace, "cancel": False, "play_task": None}
        self.jobs[ident] = job

        async def prepare():
            trace.mark("tts_start")
            if streaming:
                reader = job['reader'] = asyncio.StreamReader()
                ready = asyncio.get_running_loop().create_future()
                async def produce():
                    size = 0
                    try:
                        async for chunk in self.player.audio_chunks(text, voice):
                            if not chunk:
                                continue
                            size += len(chunk)
                            if size > 4_000_000:
                                raise ValueError('TTS output too large')
                            reader.feed_data(chunk)
                            if not any(e['stage'] == 'tts_first_audio' for e in trace.events):
                                trace.mark('tts_first_audio', audio_bytes=len(chunk))
                            # About one second of Edge MP3; final EOF also makes
                            # short answers ready. Bounds startup underruns.
                            if not ready.done() and size >= 8192:
                                ready.set_result(None)
                        if not size:
                            raise ValueError('no TTS audio')
                        trace.mark('tts_ready', audio_bytes=size)
                        if not ready.done():
                            ready.set_result(None)
                    except BaseException:
                        if not ready.done():
                            # Original exception is retrieved from producer below.
                            ready.set_result(None)
                        raise
                    finally:
                        reader.feed_eof()
                job['producer'] = asyncio.create_task(produce())
                await ready
                if job['producer'].done():
                    await job['producer']
                return reader
            audio = await self.player.synthesize(text, voice) if provider == "edge" else await self.player.synthesize(text, voice, provider)
            trace.mark("tts_ready", audio_bytes=len(audio))
            return audio

        self.queue.put_nowait(job)
        job["prepare"] = asyncio.create_task(prepare())
        return self.public(ident)

    def public(self, ident):
        job = self.jobs[ident]
        return {"id": ident, "status": job["status"], "events": job["trace"].events}

    async def run(self):
        while True:
            job = await self.queue.get()
            try:
                if job["cancel"]:
                    continue
                job["status"] = "preparing"
                audio = await job["prepare"]
                if job["cancel"]:
                    continue
                job["status"] = "playing"
                job["trace"].mark("playback_transport_start")
                job["play_task"] = asyncio.create_task(self.player.play(audio))
                async with asyncio.timeout(70):
                    await job["play_task"]
                    if job.get('producer'):
                        await job['producer']
                job["trace"].mark("playback_transport_complete")
                await asyncio.sleep(self.tail)
                job["trace"].mark("playback_guard_complete")
                self.last_success = {"ts": job["trace"].events[-1]["ts"]}
                self.failure_active = False
                job["status"] = "done"
            except asyncio.CancelledError:
                if not job["cancel"]:
                    job["status"] = "cancelled"
                if asyncio.current_task().cancelling():
                    raise
                if job["play_task"] is not None:
                    await asyncio.sleep(self.tail)
            except Exception as exc:
                job["status"] = "failed"
                stage = "synthesis" if not any(e["stage"] == "playback_transport_start" for e in job["trace"].events) else "playback"
                job["trace"].mark("playback_failed", failed_stage=stage, **failure_fields(exc))
                self.last_failure = dict(job["trace"].events[-1])
                self.failure_active = True
            finally:
                if job.get('reader'):
                    job['reader'].feed_eof()
                producer = job.pop('producer', None)
                if producer:
                    producer.cancel()
                    await asyncio.gather(producer, return_exceptions=True)
                prepared = job.pop("prepare", None)
                if prepared:
                    prepared.cancel()
                    await asyncio.gather(prepared, return_exceptions=True)
                job["play_task"] = None
                # Completed-job metadata must not retain the task's audio result.
                prepared = audio = None
                producer = None
                job.pop('reader', None)
                self.queue.task_done()

    async def cancel(self, ident):
        job = self.jobs[ident]
        if job["status"] in ("done", "failed", "cancelled"):
            return
        job["cancel"] = True
        job["status"] = "stopping"
        if job.get('reader'):
            job['reader'].feed_eof()
        tasks = [job["prepare"]] if job.get("prepare") else []
        if job.get('producer'):
            tasks.append(job['producer'])
        was_playing = job["play_task"] is not None
        if job["play_task"]:
            tasks.append(job["play_task"])
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if was_playing:
            await asyncio.sleep(self.tail)
        job["status"] = "cancelled"


def app_for(jobs, token, legacy=False, legacy_token=""):
    # Worst-case JSON escaping uses 12 bytes per Unicode codepoint.
    app = web.Application(client_max_size=65536)
    last_check = 0

    async def create(request):
        old = request.path == "/speak"
        permitted = authorized(request, token) or (old and bool(legacy_token) and authorized(request, legacy_token))
        if (old and not legacy) or not permitted:
            raise web.HTTPUnauthorized()
        try:
            data = await request.json()
            ident = uuid.uuid4().hex if old else data["id"]
            text = data["text"]
            voice = data.get("voice", "")
            if not isinstance(ident, str) or not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", ident):
                raise ValueError()
            if not isinstance(text, str) or not 1 <= len(text.strip()) <= 3000:
                raise ValueError()
            if not isinstance(voice, str) or len(voice) > 100:
                raise ValueError()
            # Legacy callers are scoped to speech submission and cannot choose a provider.
            provider = os.environ.get("TTS_PROVIDER", "edge") if old else data.get("provider", os.environ.get("TTS_PROVIDER", "edge"))
            if provider not in ("edge", "elevenlabs", "gemini"):
                raise ValueError()
            streaming = False if old else data.get('streaming', False)
            if type(streaming) is not bool or (streaming and provider != 'edge'):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise web.HTTPBadRequest() from None
        job = jobs.create(ident, text, voice, provider, streaming=streaming)
        return web.json_response({"status": "accepted", "id": ident} if old else job, status=202)

    async def status(request):
        if not authorized(request, token):
            raise web.HTTPUnauthorized()
        ident = request.match_info["ident"]
        if ident not in jobs.jobs:
            raise web.HTTPNotFound()
        if request.method == "DELETE":
            await jobs.cancel(ident)
        return web.json_response(jobs.public(ident))

    async def health(request):
        running = jobs.worker is not None and not jobs.worker.done()
        return web.json_response({"status": "ok" if running else "degraded",
                                  "streaming_audio": True,
                                  "busy": jobs.busy, "worker_running": running,
                                  "last_success": jobs.last_success,
                                  "failure_active": jobs.failure_active,
                                  "last_failure": ({k: jobs.last_failure[k] for k in ("ts", "error_type", "http_status", "failed_stage") if k in jobs.last_failure}
                                                   if jobs.last_failure else None)}, status=200 if running else 503)

    async def check(request):
        nonlocal last_check
        if not authorized(request, token):
            raise web.HTTPUnauthorized()
        if jobs.busy:
            raise web.HTTPConflict()
        if time.monotonic() - last_check < 10:
            raise web.HTTPTooManyRequests()
        last_check = time.monotonic()
        trace = Trace("synthesis-check-" + uuid.uuid4().hex[:12], jobs.telemetry)
        trace.mark("synthesis_check_start")
        try:
            # Fixed synthetic text only. No HomePod connection or playback.
            async with asyncio.timeout(25):
                provider = os.environ.get("TTS_PROVIDER", "edge")
                audio = (await jobs.player.synthesize("Dies ist eine Sprachprüfung.", "") if provider == "edge"
                         else await jobs.player.synthesize("Dies ist eine Sprachprüfung.", "", provider))
            trace.mark("synthesis_check_complete", audio_bytes=len(audio))
            # A synthesis check can recover a synthesis error, never a failed
            # HomePod transport. Preserve transport failures until real playback.
            if jobs.last_failure and jobs.last_failure.get("failed_stage") == "synthesis":
                jobs.failure_active = False
            return web.json_response({"status": "ok", "synthesis": True, "audio_bytes": len(audio)})
        except Exception as exc:
            trace.mark("synthesis_check_failed", failed_stage="synthesis", **failure_fields(exc))
            jobs.last_failure = dict(trace.events[-1])
            jobs.failure_active = True
            return web.json_response({"status": "degraded", **failure_fields(exc)}, status=502)

    async def start(app):
        jobs.worker = asyncio.create_task(jobs.run())

    async def stop(app):
        for ident in list(jobs.jobs):
            await jobs.cancel(ident)
        jobs.worker.cancel()
        await asyncio.gather(jobs.worker, return_exceptions=True)

    app.router.add_post("/v1/jobs", create)
    app.router.add_post("/speak", create)
    app.router.add_get("/v1/jobs/{ident}", status)
    app.router.add_delete("/v1/jobs/{ident}", status)
    app.router.add_get("/health", health)
    app.router.add_post("/v1/check", check)
    app.on_startup.append(start)
    app.on_cleanup.append(stop)
    return app


def require_ownership():
    result = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/com.homepod-tts-server"],
                            capture_output=True, text=True, timeout=3)
    if result.returncode and "Could not find service" not in result.stderr:
        raise SystemExit("Cannot verify old TTS service state; refusing playback startup.")
    match = re.search(r"^\s*pid = (\d+)", result.stdout, re.M)
    if match and int(match[1]) != os.getpid():
        raise SystemExit("Old HomePod TTS service is running. Approve and perform coordinated cutover first.")
    # Also catch a manually launched/renamed server using the legacy endpoint.
    ports = subprocess.run(["/usr/sbin/lsof", "-nP", "-iTCP:8125", "-sTCP:LISTEN", "-t"],
                           capture_output=True, text=True, timeout=3)
    if ports.returncode not in (0, 1) or ports.stderr:
        raise SystemExit("Cannot verify legacy TTS listener; refusing startup.")
    if any(int(pid) != os.getpid() for pid in ports.stdout.split()):
        raise SystemExit("Legacy TTS port is still owned by another process; refusing startup.")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8583)
    p.add_argument("--homepod", default="192.0.2.30")
    p.add_argument("--enable-playback", action="store_true")
    p.add_argument("--legacy", action="store_true")
    p.add_argument("--telemetry", default="", help="content-free job/error JSONL")
    args = p.parse_args()
    if not args.enable_playback:
        p.error("Live gateway requires --enable-playback after deployment approval")
    if args.host != "127.0.0.1":
        p.error("Public reference gateway must bind loopback")
    require_ownership()
    legacy_token = os.environ.get("DESK_TTS_LEGACY_TOKEN", "")
    if legacy_token and len(legacy_token) < 32:
        p.error("Legacy speech token must contain at least 32 characters")
    if legacy_token and legacy_token == token_from_env("DESK_TTS_TOKEN"):
        p.error("Legacy and full-access speech credentials must be distinct")
    web.run_app(app_for(Jobs(HomePod(args.homepod), telemetry=args.telemetry), token_from_env("DESK_TTS_TOKEN"), args.legacy, legacy_token),
                host=args.host, port=args.port, access_log=None)


if __name__ == "__main__":
    main()
