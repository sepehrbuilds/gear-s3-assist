from contextlib import aclosing
import argparse
import asyncio
import contextlib
import json
import re
import ssl
import struct
import time
from collections import OrderedDict

import aiohttp
from aiohttp import web

from .audio import AudioBuffer, IncrementalASR, Silero, Whisper
from .common import Trace, authorized, load_config, token_from_env, failure_fields
from .model import HomeControl, Model, SYSTEM
from .speech import Speech, speak_stream


class Conversation:
    def __init__(self, cfg, model, control, http):
        self.cfg, self.model, self.control, self.http = cfg, model, control, http
        self.history = []
        self.last = 0

    def reset(self):
        self.history.clear()
        self.last = 0

    def commit(self, text, reply):
        self.history.extend([{"role": "user", "content": text}, {"role": "assistant", "content": reply}])
        keep = self.cfg["history_pairs"] * 2
        self.history = self.history[-keep:] if keep else []
        self.last = time.monotonic()

    async def answer(self, text, trace, send):
        if time.monotonic() - self.last > self.cfg["history_ttl"]:
            self.reset()
        history = list(self.history)
        instruction = SYSTEM + " Preserve project and personal names exactly as written; never translate or transliterate them. The current user message determines the reply language, even after a language switch."
        if any("\u0600" <= c <= "\u06ff" for c in text):
            instruction += " For THIS turn, reply only in Persian (فارسی), using Persian script."
        def prompt():
            return [{"role": "system", "content": instruction}] + history + [{"role": "user", "content": text}]
        # Conservative UTF-8 byte upper bound for the active 8192-token model,
        # leaving space for generation and role/template overhead. Drop whole
        # oldest pairs; never silently truncate a user's current request.
        while history and sum(len(m["content"].encode("utf-8")) for m in prompt()) > 7000:
            history = history[2:]
        messages = prompt()
        if sum(len(m["content"].encode("utf-8")) for m in messages) > 7000:
            raise ValueError("current request exceeds prompt budget")
        speech = Speech(self.http, self.cfg, trace)
        actions_attempted = False

        async def tokens():
            nonlocal actions_attempted
            trace.mark("llm_start", history_pairs=(len(messages) - 2) // 2)
            first = True
            schema = self.control.schema()
            held, calls = "", []
            async with aclosing(self.model.stream(messages, schema)) as events:
                async for event in events:
                    if "tool" in event:
                        calls.append(event["tool"])
                        continue
                    if first:
                        first = False
                        trace.mark("llm_first_token")
                    if schema:
                        held += event["text"]
                        if len(held) > self.cfg["max_reply_chars"]:
                            raise ValueError("reply too long")
                    else:
                        yield event["text"]
            if calls:
                # No model-written success is spoken before execution. Report a
                # deterministic status, rather than ask a model to embellish it.
                actions_attempted = True
                results = await self.control.execute(calls)
                trace.mark("ha_result", accepted=sum(x["status"] == "accepted" for x in results), count=len(results))
                fa = any("\u0600" <= c <= "\u06ff" for c in text)
                ok = all(x["status"] == "accepted" for x in results)
                yield ("درخواست ارسال شد." if ok else "نتیجهٔ درخواست مشخص نیست. دوباره اجرا نکردم.") if fa else (
                    "Die Anfrage wurde angenommen." if ok else "Die Anfrage wurde nicht vollständig bestätigt. Ich habe sie nicht wiederholt.")
            elif schema:
                yield held
            trace.mark("llm_complete")

        try:
            await send("responding")
            reply = await speak_stream(tokens(), speech, self.cfg["max_reply_chars"])
            if not reply:
                raise ValueError("empty reply")
            self.commit(text, reply)
            return reply
        except BaseException:
            delivered = " ".join(speech.completed[k] for k in sorted(speech.completed))
            note = "[Delivery note: Previous reply interrupted. Only the preceding text completed playback; the remainder was not confirmed delivered. Do not read this note aloud.]"
            if actions_attempted:
                note += " [A device request was attempted. Check its outcome; never retry it automatically.]"
            self.commit(text, (delivered + "\n" + note).strip())
            trace.mark("history_partial", completed_chunks=len(speech.completed), actions_attempted=actions_attempted)
            await speech.cancel()
            raise


class Backend:
    def __init__(self, cfg, token, vad, asr, conversation):
        self.cfg, self.token, self.vad, self.asr, self.conversation = cfg, token, vad, asr, conversation
        self.connected = False
        self.seen = OrderedDict()
        self.accepted = []
        self.last_failure = None
        self.last_success = None
        self.failure_active = False
        self.active_turn = False

    def admit(self, ident):
        now = time.monotonic()
        self.accepted[:] = [t for t in self.accepted if now - t < 600]
        if len(self.accepted) >= 40 or ident in self.seen:  # 40/10 min: a follow-up every 15 s
            return False
        self.accepted.append(now)
        self.seen[ident] = now
        while len(self.seen) > 1024:
            self.seen.popitem(last=False)
        return True

    async def playback_idle(self):
        if self.cfg["playback"] in ("preview", "watch"):
            return True
        try:
            async with self.conversation.http.get(self.cfg["tts_url"].rstrip("/") + "/health",
                    timeout=aiohttp.ClientTimeout(total=2)) as response:
                response.raise_for_status()
                status = await response.json()
                return status.get("status") == "ok" and status.get("busy") is False
        except (aiohttp.ClientError, TimeoutError, ValueError):
            return False  # uncertain playback state must not reopen listening

    async def socket(self, request):
        if not authorized(request, self.token):
            raise web.HTTPUnauthorized()
        if request.headers.get("Origin"):
            raise web.HTTPForbidden()  # firmware/CLI only, no browser origins
        if self.connected:
            raise web.HTTPConflict()
        self.connected = True
        ws = web.WebSocketResponse(max_msg_size=4100, heartbeat=10, compress=False)
        turn = None
        work = None
        phase = "ready"
        seq = 0
        started = last_audio = 0
        last_preview = 0
        mode = "ptt"

        async def send(state, **extra):
            await ws.send_json({"state": state, **extra})

        async def abort():
            nonlocal work, turn, phase
            current = turn
            if current:
                current["cancelled"] = True
                current["trace"].mark("cancelled")
            if work:
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)
                work = None
            if turn:
                if turn.get('asr_warm'):
                    turn['asr_warm'].cancel()
                    await asyncio.gather(turn['asr_warm'], return_exceptions=True)
                if turn.get("warm"):
                    turn["warm"].cancel()
                    await asyncio.gather(turn["warm"], return_exceptions=True)
                await turn["incremental"].close()
                turn = None
            phase = "ready"
            self.active_turn = False

        async def finish():
            nonlocal turn, phase
            current = turn
            try:
                async with asyncio.timeout(150):
                    buf, trace = current["buffer"], current["trace"]
                    trace.mark("capture_end", audio_bytes=len(buf.pcm), speech_bytes=buf.voiced)
                    trace.mark("vad_last_speech", audio_offset_ms=round(buf.last_voice / 32))
                    if not buf.has_speech:
                        trace.mark("no_speech")
                        await send("no_speech")
                        return
                    await send("transcribing")
                    text = await current["incremental"].finish(buf.snapshot())
                    if not text or not any(c.isalpha() for c in text):
                        await send("no_speech")
                        return
                    if current.get("warm"):
                        await asyncio.gather(current["warm"], return_exceptions=True)
                    # Transcripts never sent back to firmware or persisted by default.
                    await self.conversation.answer(text, trace, send)
                    trace.mark("turn_complete", preview=self.cfg["playback"] == "preview")
                    self.last_success = {"ts": trace.events[-1]["ts"]}
                    self.failure_active = False
                    await send("complete", preview=self.cfg["playback"] == "preview",
                               timings=trace.events if request.query.get("timings") == "1" else [])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                current["trace"].mark("failed", **failure_fields(exc))
                self.last_failure = {k: v for k, v in current["trace"].events[-1].items() if k in ("ts", "error_type", "http_status")}
                self.failure_active = True
                with contextlib.suppress(ConnectionError):
                    await send("error", reason="turn_failed")
            finally:
                if current.get('asr_warm'):
                    current['asr_warm'].cancel()
                    await asyncio.gather(current['asr_warm'], return_exceptions=True)
                if current.get("warm"):
                    current["warm"].cancel()
                    await asyncio.gather(current["warm"], return_exceptions=True)
                await current["incremental"].close()
                turn = None
                phase = "ready"
                self.active_turn = False
                if not ws.closed and not current.get("cancelled"):
                    with contextlib.suppress(ConnectionError):
                        await send("ready")

        try:
            await ws.prepare(request)
            await send("ready", version=2 if self.cfg.get("watch_mode") else 1)
            while not ws.closed:
                try:
                    msg = await ws.receive(timeout=1)
                except TimeoutError:
                    if phase == "recording" and (time.monotonic() - last_audio > 2 or time.monotonic() - started > self.cfg["max_record_seconds"] + 1):
                        turn["trace"].mark("capture_timeout", frames=seq)
                        await abort()
                        await send("error", reason="capture_timeout")
                        await send("ready")
                    continue
                if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        command = data["type"]
                        if command == "status" and self.cfg.get("watch_mode") and phase == "ready":
                            await send("status", services=await self.conversation.connection_status())
                            continue
                        if command == "cancel":
                            if turn:
                                turn["trace"].mark("client_cancel", frames=seq)
                            await abort()
                            await send("ready")
                        elif command == "reset" and phase == "ready":
                            self.conversation.reset()
                            await send("reset")
                        elif command == "start" and phase == "ready":
                            if self.cfg.get("watch_mode") and data.get("protocolVersion") != 2:
                                raise ValueError()
                            ident = data["id"]
                            mode = data.get("mode", "ptt")
                            if not isinstance(ident, str) or not re.fullmatch(r"[a-zA-Z0-9-]{8,64}", ident) or mode not in ("ptt", "vad"):
                                raise ValueError()
                            if not await self.playback_idle():
                                await send("error", reason="speaker_busy_or_unavailable")
                                await send("ready")
                                continue
                            if not self.admit(ident):
                                await send("error", reason="duplicate_or_rate_limited")
                                await send("ready")
                                continue
                            trace = Trace(ident, self.cfg.get("telemetry", ""))
                            trace.mark("capture_start")
                            asr = self.asr
                            convo = self.conversation
                            if isinstance(asr, Whisper) and convo.history and time.monotonic() - convo.last <= min(120, self.cfg["history_ttl"]):
                                previous = next((m["content"] for m in reversed(convo.history) if m["role"] == "user"), "")
                                asr = asr.contextual(previous, trace)
                            turn = {"trace": trace, "buffer": AudioBuffer(self.vad, self.cfg["max_record_seconds"]),
                                    "incremental": IncrementalASR(asr, trace), 'pause_previews': 0}
                            if (self.cfg.get('watch_mode')
                                    and (self.cfg.get('watch_asr_warm') or self.cfg.get('watch_asr_overlap'))
                                    and isinstance(self.asr, Whisper)):
                                turn['asr_warm'] = asyncio.create_task(self.asr.warm(trace))
                            self.active_turn = True
                            if hasattr(self.conversation.model, "warm"):
                                turn["warm"] = asyncio.create_task(self.conversation.model.warm())
                            phase = "recording"
                            seq = 0
                            started = last_audio = last_preview = time.monotonic()
                            await send("recording")
                        elif command == "end" and phase == "recording":
                            turn["trace"].mark("end_received", frames=seq)
                            phase = "processing"
                            work = asyncio.create_task(finish())
                        else:
                            await send("busy" if phase != "ready" else "error")
                    except (ValueError, KeyError, TypeError):
                        await abort()
                        await send("error", reason="invalid_control")
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    if phase != "recording":
                        continue  # never buffer audio during processing/playback
                    try:
                        data = msg.data
                        if len(data) < 6 or struct.unpack("<I", data[:4])[0] != seq:
                            raise ValueError("sequence gap")
                        if seq == 0:
                            turn["trace"].mark("first_audio")
                        seq += 1
                        turn["buffer"].append(data[4:])
                        last_audio = time.monotonic()
                        if last_audio - started > self.cfg["max_record_seconds"] + 1:
                            raise ValueError("capture wall time exceeded")
                        buf = turn["buffer"]
                        # Opt-in watch optimization: decode only a stable pause,
                        # not every prefix. One attempt maximum per recording.
                        # snapshot() stops changing after its 210ms padding; final
                        # reuse still requires byte-for-byte equality after Send.
                        if (self.cfg.get('watch_mode') and self.cfg.get('watch_asr_overlap')
                                and mode == 'ptt'
                                and buf.has_speech and buf.silence >= .36
                                and turn['pause_previews'] < 1):
                            if turn['incremental'].offer(buf.snapshot()):
                                turn['pause_previews'] += 1
                        # Decode at pauses or periodically during longer speech.
                        if (self.cfg["partial_interval"] > 0 and buf.has_speech and
                            last_audio - last_preview >= self.cfg["partial_interval"]):
                            turn["incremental"].offer(buf.snapshot())
                            last_preview = last_audio
                        if mode == "vad" and buf.has_speech and buf.silence >= self.cfg["endpoint_silence"]:
                            phase = "processing"
                            work = asyncio.create_task(finish())
                            await send("endpoint")
                    except ValueError:
                        turn["trace"].mark("invalid_audio", frames=seq)
                        await abort()
                        await send("error", reason="invalid_audio")
                        await send("ready")
        finally:
            # Mark the socket closed before cancelling the worker: otherwise its
            # final READY write can race the client's closing handshake.
            try:
                await ws.close()
                await abort()
            finally:
                self.connected = False
        return ws


def app_for(backend):
    app = web.Application(client_max_size=4096)
    app.router.add_get("/audio", backend.socket)

    async def health(request):
        result = {"status": "ok", "connected": backend.connected,
                                  "busy": backend.active_turn,
                                  "playback": backend.cfg["playback"],
                                  "history_pairs": len(backend.conversation.history) // 2,
                                  "last_failure": backend.last_failure,
                                  "last_success": backend.last_success,
                                  "failure_active": backend.failure_active}
        if backend.cfg.get("watch_mode"):
            result["home_decisions"] = "jev-with-gemini-fallback" if backend.cfg.get("watch_jev_enabled") else "gemini"
            result['asr_overlap'] = bool(backend.cfg.get('watch_asr_overlap'))
            result['asr_warmup'] = bool(backend.cfg.get('watch_asr_warm') or backend.cfg.get('watch_asr_overlap'))
            result['streaming_replies'] = bool(backend.cfg.get('watch_tts_streaming'))
        return web.json_response(result)
    app.router.add_get("/health", health)
    async def lifecycle(app):
        async def expire_history():
            while True:
                await asyncio.sleep(30)
                convo = backend.conversation
                if convo and time.monotonic() - convo.last > backend.cfg["history_ttl"]:
                    convo.reset()
        task = asyncio.create_task(expire_history())
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if backend.conversation:
                backend.conversation.reset()
    app.cleanup_ctx.append(lifecycle)
    return app


async def application(cfg):
    http = aiohttp.ClientSession()
    try:
        conversation_type = Conversation
        if cfg.get("watch_mode"):
            from .gear import GearConversation
            conversation_type = GearConversation
        backend = Backend(cfg, token_from_env(cfg["token_env"]), Silero(cfg["vad_model"]),
                          Whisper(http, cfg["asr_url"], cfg["asr_language"]),
                          conversation_type(cfg, Model(http, cfg), HomeControl(http, cfg), http))
        app = app_for(backend)
    except BaseException:
        await http.close()
        raise
    async def cleanup(app):
        await http.close()
    app.on_cleanup.append(cleanup)
    return app


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    args = p.parse_args()
    cfg = load_config(args.config)
    tls = None
    if cfg.get("tls_cert") and cfg.get("tls_key"):
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.minimum_version = ssl.TLSVersion.TLSv1_2
        tls.load_cert_chain(cfg["tls_cert"], cfg["tls_key"])
    if cfg["host"] not in ("127.0.0.1", "::1") and tls is None:
        p.error("LAN microphone streaming requires TLS")
    web.run_app(application(cfg), host=cfg["host"], port=cfg["port"], ssl_context=tls, access_log=None)


if __name__ == "__main__":
    main()
