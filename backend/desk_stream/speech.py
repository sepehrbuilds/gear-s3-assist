import asyncio
import re

import aiohttp

from .common import token_from_env


def first_sentence(text):
    # Sentence chunks, not tokens: only one early chunk to bound AirPlay handshakes.
    for match in re.finditer(r"[.!?؟。](?:\s|$)", text):
        if match.end() >= 25:
            return text[:match.end()].strip(), text[match.end():]
    return None


class Speech:
    def __init__(self, http, cfg, trace):
        self.http, self.cfg, self.trace = http, cfg, trace
        self.jobs = []
        self.completed = {}

    async def say(self, text, index):
        if not text.strip():
            return
        if self.cfg["playback"] == "preview":
            self.trace.mark("preview_text_ready", chunk=index, chars=len(text))
            self.completed[index] = text
            return
        token = token_from_env(self.cfg["tts_token_env"])
        headers = {"Authorization": "Bearer " + token}
        url = self.cfg["tts_url"].rstrip("/")
        job_id = f"{self.trace.id}-{index}"
        # Track before POST: a timed-out response may still have created a job.
        self.jobs.append(job_id)
        payload = {"id": job_id, "text": text}
        if self.cfg.get('watch_mode') and self.cfg.get('watch_tts_streaming'):
            payload.update(streaming=True, provider='edge')
        async with self.http.post(url + "/v1/jobs", headers=headers,
                                  json=payload,
                                  timeout=aiohttp.ClientTimeout(total=5)) as r:
            r.raise_for_status()
            job = await r.json()
        if job["id"] != job_id:
            raise RuntimeError("unexpected playback job")
        seen = set()
        async with asyncio.timeout(90):
            while True:
                async with self.http.get(url + "/v1/jobs/" + job_id, headers=headers,
                                          timeout=aiohttp.ClientTimeout(total=5)) as r:
                    r.raise_for_status()
                    state = await r.json()
                for event in state.get("events", []):
                    stage = event["stage"]
                    if stage not in seen:
                        seen.add(stage)
                        diagnostic = {k: event[k] for k in ("error_type", "http_status", "failed_stage") if k in event}
                        self.trace.mark(stage, chunk=index, job_elapsed_ms=event["elapsed_ms"], **diagnostic)
                if state["status"] == "done":
                    self.completed[index] = text
                    return
                if state["status"] in ("failed", "cancelled"):
                    raise RuntimeError("playback failed")
                await asyncio.sleep(0.1)

    async def cancel(self):
        if not self.jobs:
            return
        headers = {"Authorization": "Bearer " + token_from_env(self.cfg["tts_token_env"])}
        # Cancel queued continuation first so stopping chunk zero cannot release
        # an already-prepared chunk one into playback.
        for job in reversed(self.jobs):
            try:
                async with self.http.delete(self.cfg["tts_url"].rstrip("/") + "/v1/jobs/" + job,
                                             headers=headers, timeout=aiohttp.ClientTimeout(total=5)):
                    pass
            except (aiohttp.ClientError, TimeoutError):
                pass


async def speak_stream(tokens, speech, limit):
    """Consume LLM while first sentence plays; at most two serialized audio jobs."""
    whole, pending = "", ""
    first = second = None
    try:
        async for token in tokens:
            if len(whole) + len(token) > limit:
                raise ValueError("reply exceeds limit")
            whole += token
            pending += token
            if first is None:
                split = first_sentence(pending)
                if split:
                    sentence, pending = split
                    first = asyncio.create_task(speech.say(sentence, 0))
        if first:
            # Submit the remainder while chunk zero is still playing. The
            # gateway preserves FIFO order but can synthesize queued audio now,
            # removing the avoidable TTS-generation gap between chunks.
            if pending.strip():
                second = asyncio.create_task(speech.say(pending.strip(), 1))
            await first
            if second:
                await second
        else:
            await speech.say(pending.strip(), 0)
        return whole.strip()
    finally:
        tasks = [task for task in (first, second) if task]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
