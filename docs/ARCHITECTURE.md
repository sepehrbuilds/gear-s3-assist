# Architecture and protocol v2

## Watch boundary

A privately signed circular Tizen Web app handles Back, touch and rotarydetent. A non-root Node 4.4.3 process captures from `arecord -D plughw:0,0 -r 16000 -c 1 -f S16_LE`, applies gain/clipping, frames PCM and yields between bounded slices. ALSA does resampling; no JS FIR runs in production. Capture automatically stops at approximately 19 seconds and has bounded recorder shutdown.

The UI connects to authenticated watch-loopback `/control` on port 8765 using `gearptt.<private-control-token>` as the WebSocket subprotocol. Host/origin/key/peer checks apply. Start, stop, cancel, gain and status are supported. The plain `/status` response is content-free; authenticated control messages can contain result/answer text. A browser preview addresses its own loopback, lacks the private key, and cannot independently reach the watch service.

## Backend wire contract

The watch validates CA/host and the exact DER SHA-256 certificate pin, then connects to WSS `/audio` using a separate bearer credential. A v2 ready response is required. Capture starts only after:

```json
{"type":"start","id":"gear-<random-id>","mode":"ptt","protocolVersion":2}
```

A full binary message contains a four-byte little-endian sequence plus 960 bytes of 16 kHz mono PCM16LE (30 ms). Sequences start at zero. The final partial audio frame is zero-padded. `{"type":"end"}` submits final audio; `{"type":"cancel"}` cancels the turn. `{"type":"status"}` requests read-only connection information while ready.

States include ready, recording, transcribing, responding, result, complete, no_speech and error. A result can carry `kind: home|answer`, short text and bounded structured action rows. Unknown/unconfirmed outcomes stay distinct from confirmed state. The status screen's cloud label means configured, not a fresh paid inference health check. No backend bearer token is sent to the UI.

## Recognition, decisions and effects

The Mac demand listener loads Whisper at recording start via an optional loopback `/warm`. Recognition happens from final audio after Send. Warm-up and final inference share worker lifecycle/locking; health alone does not load the model. Idle unloading remains enabled.

The exact light grammar handles a narrow supported subset locally. Jev receives only a completely supported utterance envelope and typed questions; low/invalid/missing answers, unsupported clauses and timeouts use Gemini before effects. The final plan passes deterministic fixed target/service/capability validation and preflight. Jev never executes actions itself.

Room actions can be overridden by a specific named light. Complete validation precedes device writes, but batches are not transactional. Once a bounded action batch starts, watch disconnect does not cancel it halfway or cause replay. Results are retained in temporary conversation state. Observe desired state after HTTP acceptance; do not turn unknown into success.

## Output paths

Home actions return short quiet watch rows. General answer text appears on the watch and is submitted as one Edge streaming speech job. The answer text is collected after resolving model/tool output; audio generation and AirPlay transport overlap. This is not token-by-token LLM-to-HomePod speech.

The gateway keeps FIFO, idempotent IDs, at most four active jobs and a 4 MB bound per job. Gear opts into Edge streaming with an 8192-byte prebuffer and a local MP3 read/seek adapter. Transport completion and a tail guard are tracked separately from user-confirmed audibility. On uncertain playback, no automatic replay is attempted.

The shared desk path and non-Gear gateway callers retain their prior default behavior. The reference export includes those branches for regression coverage; the selected watch config uses cloud inference and Edge voices.
