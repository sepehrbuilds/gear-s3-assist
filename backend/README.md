# Mac mini backend reference

This is a sanitized snapshot of the working backend, with generic examples. It does not read the author's private environment or infrastructure. The package retains the original desk/prototype path for regression coverage; the Gear configuration selects cloud inference and watch output.

## Requirements

Python 3.11+ (tested with 3.13), native macOS, uv or pip, a compatible legacy Silero ONNX VAD model, a whisper.cpp server binary and compatible local Whisper model, an OpenRouter key, private TLS material, and reachable Home Assistant/HomePod if enabling those features. Model weights and certificates are not bundled. `asr_demand.py` expects `/opt/homebrew/bin/whisper-server`; adapt the binary locator for another installation.

Install/test independently from a production environment:

```sh
uv venv --python 3.13
uv pip install --python .venv/bin/python -r requirements-tested.txt
.venv/bin/python -m pytest -q
```

`requirements-tested.txt` pins the dependency closure to the versions used for the successful public-export tests. The pyproject describes the direct dependencies; updating its transitive dependencies is a separate compatibility change.

Tests bind local sockets and use synthetic fake HA/TV/TTS services. They never need a real HA token or cloud model key. ffmpeg enables the actual MP3 decoder fixture.

## Offline policy example

After installing dependencies, run `.venv/bin/python examples/dry_run.py`. It loads the fictional example map into memory and uses the existing validator to print a light plan and refuse door unlocking. No network client is created and no plan is executed. Compare with [expected output](examples/dry-run-output.json).

## Private setup outline

1. Create `.state/` privately; copy `watch-config.example.json` to `.state/watch.json` and `watch.env.example` to `.state/watch.env`. Protect both with mode 0600.
2. Generate separate random watch-stream and TTS bearer tokens. Fill OpenRouter/HA credentials privately. The empty environment examples deliberately fail credential checks.
3. Place your own TLS certificate/key at the private paths. Configure the host/SAN and watch CA/pin together. Non-loopback microphone serving requires TLS. Example IPs in the repository are documentation ranges and will not reach real devices.
4. Install compatible VAD/Whisper model assets, set `vad_model`, and start ASR with your private model path:

```sh
.venv/bin/python -m desk_stream.asr_demand --model /path/to/whisper-model.bin
```

5. Configure the HomePod address and run the gateway only when no competing playback owner exists:

```sh
.venv/bin/python tools/launch.py --env .state/watch.env desk_stream.tts_gateway \
  --host 127.0.0.1 --port 8583 --homepod 192.0.2.30 --enable-playback
```

Replace the documentation HomePod address privately. The public reference binds the speech API to loopback only. Ownership checks still protect the existing service label / legacy port; see the source before integrating with your own launchd jobs. No production launchd plist is supplied. This reference is macOS-only: ownership checks require launchctl and /usr/sbin/lsof. The retained legacy job label and port are compatibility guards, not credential values.

6. Review the example home map, aliases, light capabilities, TV app IDs/source and lock policy. `ha_enabled` stays **false** until you deliberately configure and authorize your own devices. The fixture map supports unit tests; it is not the owner's home map.
The prototype also pins the exact lock and TV entity IDs in `Devices.validate` in `desk_stream/home_actions.py`. The export uses `lock.example_front_door` and `media_player.example_tv`. For your own devices, review and change those fixed source guards together with your private mapping, then rerun positive and forbidden-action tests. This is intentionally a fixed prototype surface, not arbitrary model-selected devices.

7. Start the Gear backend:

```sh
.venv/bin/python tools/launch.py --env .state/watch.env desk_stream.server --config .state/watch.json
```

The example starts on loopback for setup. A real watch needs your own reachable bind address, valid TLS and provisioned pin/token. Health flags expose warm-up/preview/streaming route selection; they are not proof of microphone audibility or every external dependency.

## Current route and feature flags

Exact supported light command → bounded Jev decision → Gemini fallback/general answer. Jev uses `/api/alpha/decisions`, not chat/completions. Enablement is watch-only. Timeouts, malformed/low-probability answers and unsupported utterances fall back before device execution; nothing retries a partially executed command.

`watch_asr_warm=true`, `watch_asr_overlap=false`, `watch_tts_streaming=true`. Pause-preview code remains available for experiments but was disabled after a real resumed-speech latency regression. Edge is retained for all deployed voices. No MOSS/Pocket dependencies or model files are included.

The MP3 adapter targets the tested Edge format and installed pyatv version. It does not promise arbitrary MP3 stream compatibility. A metadata warning about unknown duration can occur without affecting playback. Cancellation, buffers, FIFO jobs and delivery status need hardware validation on your own speaker.
