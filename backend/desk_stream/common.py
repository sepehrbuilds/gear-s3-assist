import json
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path


def token_from_env(name):
    value = os.environ.get(name, "")
    if len(value) < 32:
        raise ValueError(f"{name} must contain at least 32 characters")
    return value


def authorized(request, token):
    return secrets.compare_digest(request.headers.get("Authorization", "").encode("utf-8", "surrogatepass"), ("Bearer " + token).encode("utf-8", "surrogatepass"))


class Trace:
    """Content-free, monotonic timings. Never log transcripts, URLs or exceptions."""
    def __init__(self, request_id, path=""):
        self.id = request_id
        self.path = path
        self.start = time.monotonic()
        self.events = []

    def mark(self, stage, **fields):
        # Callers supply only controlled status names and numeric counts.
        record = {"id": self.id, "stage": stage,
                  "elapsed_ms": round((time.monotonic() - self.start) * 1000, 2),
                  "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), **fields}
        self.events.append(record)
        if self.path:
            path = Path(self.path)
            if path.exists() and path.stat().st_size >= 5_000_000:
                path.replace(str(path) + ".1")  # one rotated generation, content-free
            fd = os.open(self.path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            try:
                os.fchmod(fd, 0o600)
                os.write(fd, (json.dumps(record) + "\n").encode())
            finally:
                os.close(fd)


def load_config(path):
    cfg = json.loads(Path(path).read_text())
    if cfg["provider"] not in ("ollama", "openai-compatible"):
        raise ValueError("unsupported provider")
    if cfg["playback"] not in ("preview", "homepod", "watch"):
        raise ValueError("unsupported playback")
    if cfg["playback"] == "watch" and not cfg.get("watch_mode"):
        raise ValueError("watch playback requires dedicated watch mode")
    if cfg.get("watch_mode") and (cfg["playback"] != "watch" or cfg["provider"] != "openai-compatible"):
        raise ValueError("watch mode requires cloud provider and watch output")
    if cfg.get("ha_enabled") and cfg["playback"] == "preview":
        raise ValueError("preview must not enable home-control actions")
    if not 1 <= cfg["max_record_seconds"] <= 30:
        raise ValueError("recording limit must be 1..30 seconds")
    if not 0 <= cfg["history_pairs"] <= 10 or not 100 <= cfg["max_reply_chars"] <= 3000:
        raise ValueError("history/reply bounds invalid")
    keep_alive = cfg.get("llm_keep_alive_seconds", 600)
    if type(keep_alive) is not int or not 0 <= keep_alive <= 600:
        raise ValueError("LLM keep-alive must be 0..600 seconds")
    return cfg


def failure_fields(exc):
    """Safe diagnostic categories only; never serialize exception messages/URLs."""
    fields = {"error_type": type(exc).__name__}
    status = getattr(exc, "status", None)
    if isinstance(status, int) and 100 <= status <= 599:
        fields["http_status"] = status
    return fields
