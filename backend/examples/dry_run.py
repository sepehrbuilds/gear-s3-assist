"""Offline policy example: build plans only, with no network/device execution."""
from dataclasses import asdict
import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desk_stream.home_actions import ActionError, Devices


def main():
    path = Path(__file__).resolve().parents[1] / "watch-config.example.json"
    cfg = json.loads(path.read_text())
    # Enable validation in this in-memory, fictional map only. There is no HTTP
    # client, and this example never invokes execute/preflight or a model.
    cfg["ha_enabled"] = True
    devices = Devices(SimpleNamespace(http=None, targets=cfg["watch_targets"]), cfg)
    calls = [{"name": "apply_home", "arguments": {"actions": [
        {"target": "stehlampe", "action": "set_light", "brightness_pct": 35}
    ]}}]
    plan = devices.validate(calls)
    forbidden = [{"name": "apply_home", "arguments": {"actions": [
        {"target": "door", "action": "unlock"}
    ]}}]
    try:
        devices.validate(forbidden, utterance="Unlock the door")
    except ActionError:
        rejected = True
    else:
        raise RuntimeError("Forbidden request was unexpectedly accepted")
    print(json.dumps({"mode": "validation_only", "executed": False,
                      "light_plan": [asdict(job) for job in plan],
                      "unlock_rejected": rejected}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
