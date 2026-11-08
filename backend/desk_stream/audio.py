import asyncio
import io
import wave

import aiohttp
import numpy as np
import onnxruntime as ort

RATE = 16000
FRAME_BYTES = 960  # 30ms, mono signed 16-bit little endian


def wav_bytes(pcm):
    out = io.BytesIO()
    with wave.open(out, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes(pcm)
    return out.getvalue()


class Silero:
    """Existing openWakeWord legacy Silero ONNX model; explicitly check its ABI."""
    def __init__(self, path):
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self.model = ort.InferenceSession(path, sess_options=options, providers=["CPUExecutionProvider"])
        if {i.name for i in self.model.get_inputs()} != {"input", "h", "c", "sr"}:
            raise ValueError("Expected the legacy openWakeWord Silero model (input,h,c,sr)")
        self.reset()

    def reset(self):
        self.h = np.zeros((2, 1, 64), dtype=np.float32)
        self.c = np.zeros((2, 1, 64), dtype=np.float32)

    def score(self, frame):
        x = np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768
        out, self.h, self.c = self.model.run(None, {
            "input": x[None, :], "h": self.h, "c": self.c, "sr": np.array(RATE, dtype=np.int64)})
        return float(out[0][0])


class AudioBuffer:
    def __init__(self, vad, limit_seconds=20):
        self.vad = vad
        vad.reset()
        self.limit = int(limit_seconds * RATE * 2)
        self.pcm = bytearray()
        self.checked = 0
        self.voiced = 0
        self.last_voice = 0

    def append(self, data):
        if len(data) % 2 or not data or len(data) > 4096:
            raise ValueError("invalid PCM frame")
        if len(self.pcm) + len(data) > self.limit:
            raise ValueError("recording too long")
        self.pcm.extend(data)
        while self.checked + FRAME_BYTES <= len(self.pcm):
            frame = bytes(self.pcm[self.checked:self.checked + FRAME_BYTES])
            self.checked += FRAME_BYTES
            if self.vad.score(frame) >= 0.5:
                self.voiced += FRAME_BYTES
                self.last_voice = self.checked

    @property
    def has_speech(self):
        return self.voiced >= FRAME_BYTES * 6  # >=180ms speech, before spending ASR/LLM

    @property
    def silence(self):
        return (len(self.pcm) - self.last_voice) / (RATE * 2)

    def snapshot(self):
        # Preserve leading context and 210ms trailing padding. Exact snapshots can
        # reuse already-completed decoding; never execute on an interim transcript.
        end = min(len(self.pcm), self.last_voice + FRAME_BYTES * 7)
        return bytes(self.pcm[:end]) if self.has_speech else b""


class Whisper:
    def __init__(self, http, url, language="auto"):
        self.http, self.url, self.language = http, url, language
        self.lock = asyncio.Lock()

    async def warm(self, trace):
        trace.mark('asr_warm_start')
        try:
            async with self.http.post(self.url.rsplit('/', 1)[0] + '/warm',
                                      timeout=aiohttp.ClientTimeout(total=22)) as response:
                response.raise_for_status()
                await response.read()
            trace.mark('asr_warm_complete')
        except (aiohttp.ClientError, TimeoutError):
            # Optional optimization. Final inference remains authoritative and
            # may start/recover the worker through the normal listener.
            trace.mark('asr_warm_unavailable')

    def contextual(self, previous_text, trace):
        owner = self
        # Per-turn immutable context; never mutate the shared recognizer's language.
        class Turn:
            async def decode(self, pcm):
                return await owner.decode(pcm, previous_text=previous_text, trace=trace)
            async def preview(self, pcm):
                return await owner.decode(pcm, previous_text=previous_text, trace=trace, speculative=True)
        return Turn()

    async def preview(self, pcm):
        return await self.decode(pcm, speculative=True)

    async def request(self, pcm, language, prompt="", *, speculative=False):
        form = aiohttp.FormData()
        form.add_field("file", wav_bytes(pcm), filename="memory.wav", content_type="audio/wav")
        form.add_field("response_format", "verbose_json")
        form.add_field("language", language)
        form.add_field("temperature", "0")
        form.add_field("temperature_inc", "0")
        if prompt:
            form.add_field("prompt", prompt)
        async with self.http.post(self.url, data=form,
                                  headers={'X-ASR-Speculative': '1'} if speculative else {},
                                  timeout=aiohttp.ClientTimeout(total=35)) as r:
            r.raise_for_status()
            return await r.json()

    async def decode(self, pcm, *, previous_text="", trace=None, speculative=False):
        async with self.lock:
            prompt = previous_text[-256:]
            data = await self.request(pcm, self.language, prompt, speculative=speculative)
            detected = str(data.get("detected_language") or data.get("language", "")).lower()
            previous_fa = any("\u0600" <= c <= "\u06ff" for c in previous_text)
            # Arabic/Persian ambiguity was reproduced on short Persian follow-ups.
            # German or any other newly detected language always keeps auto detection.
            if self.language == "auto" and previous_fa and len(pcm) <= 6 * RATE * 2 and detected in ("ar", "arabic"):
                if trace:
                    trace.mark("asr_language_retry", language="fa")
                candidate = await self.request(pcm, "fa", prompt, speculative=speculative)
                if any("\u0600" <= c <= "\u06ff" for c in str(candidate.get("text", ""))):
                    data = candidate
        text = str(data.get("text", "")).strip()
        if len(text) > 3000:
            raise ValueError("transcript too long")
        return text


class IncrementalASR:
    """Bounded whole-prefix previews with a warm server, not native token ASR.

    One request in flight; no queued snapshots. Final audio is authoritative.
    Useful at pauses; final decoding may still be necessary after button release.
    """
    def __init__(self, asr, trace):
        self.asr, self.trace = asr, trace
        self.task = None
        self.audio = b""

    def offer(self, pcm):
        if not pcm or pcm == self.audio or (self.task and not self.task.done()):
            return False
        if self.task:
            # Retrieve a failed preview; final decoding can still recover.
            try:
                self.task.result()
            except Exception:
                pass
        self.audio = pcm
        self.trace.mark("asr_preview_start", audio_bytes=len(pcm))
        decode = getattr(self.asr, 'preview', self.asr.decode)
        self.task = asyncio.create_task(decode(pcm))
        return True

    async def finish(self, pcm):
        if self.task:
            self.trace.mark('asr_preview_wait_start', matching=pcm == self.audio)
            try:
                text = await self.task
                self.trace.mark('asr_preview_wait_complete')
                if pcm == self.audio:
                    self.trace.mark("asr_final", reused=True)
                    return text
            except Exception:
                pass
        self.trace.mark("asr_final_start")
        text = await self.asr.decode(pcm)
        self.trace.mark("asr_final", reused=False)
        return text

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
        if self.task:
            await asyncio.gather(self.task, return_exceptions=True)
