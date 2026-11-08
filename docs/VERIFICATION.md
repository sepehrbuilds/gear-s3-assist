# Verification record

## Clean-copy rerun — 2026-11-08

The supplied ZIP was extracted to a separate working copy after rejecting absolute/traversal paths and symlinks and enforcing entry/expanded-size bounds. All 58 entries in its original hash manifest matched. This rerun used macOS arm64, Python 3.13.18, Node 26.13.0 and ffmpeg 9.0.2, with the 45 pinned Python distributions installed into a fresh isolated environment.

Both Node host tests passed. Python: **192 passed, one optional real-certificate TLS fixture skipped in 12.44 seconds**. The real fragmented MP3 decoder test ran with ffmpeg. `uv pip check` passed. The added offline policy example matched its checked-in expected output exactly. Source syntax, JSON parsing and local Markdown link checks passed.

The Node control/Python socket tests required loopback access outside the desktop sandbox; their initial sandbox failure was not counted as a project regression. No physical watch, production endpoint, HA device, HomePod, cloud key or real model asset was used. The latest end-to-end hardware behavior and legacy Node 4 runtime were not newly verified. The expanded GitHub Actions workflow has not run remotely yet.

The current [publication check](../publication-check.json) supersedes the original export's scan metadata. A secret-pattern scan and private-locator/artifact review cover this candidate; an exact comparison against production credentials was not repeated because those credentials were not accessed. Dependency terms and remaining provenance limitations are recorded in [THIRD-PARTY](../THIRD-PARTY.md).

The backend wheel build included the MIT license and matching SPDX metadata; dependency notices, runtime code and assets were unchanged.

## Hardware proof (from original project record)

The watch is SM-R760, FVG1, Tizen4.0.0.7 with Node4.4.3. The owner confirmed physical Back/bezel use and audible HomePod answers. Wrong pin and wrong bearer token were rejected on target. The non-root process retained UID5001 and zero capabilities; trust policy and watch services survived the earlier intentional reboot. Native capture reached about19 seconds and repeated cancellation passed. A final native spoken turn passed with SDB and temporary relay disconnected.

Later signed UI1.2 and dedicated v2 backend added quiet light/home results, status, TV/apps/PS5, color/brightness and lock-only controls. The owner physically accepted these functions, then Jev routing, shorter-feeling turns and smooth complete streaming HomePod speech with return to ready. These acceptances are distinct from an automated assertion of every device/model state, and do not constitute a fresh reboot or an SDB-disconnected test of the whole latest stack.

## Automated proof

The working backend suite passed **192 tests, one optional external-certificate fixture skipped**. It covers protocol admission/authentication, capture/cancel bounds, language handling/history, exact/compound action validation, fixed door/TV/light boundaries, Jev malformed/partial/low-probability/fallback cases, no replay after effects, streaming FIFO/cancellation/bounds, and actual fragmented MP3 decoding. A clean copy of the public sanitized export passed the same 192 tests with one optional skip; publication-check.json records that result.

Actual target TLS/auth rejection tests are separate from the optional host certificate fixture. Mock tests never prove speaker audibility or actual lock state. Synthetic cloud trials used fake devices and speech: 76/76 paired cases passed both baseline and Jev candidate, with36 accepted Jev fixtures. Reported interpretation speed is not ASR or total turn speed.

## Audio/latency proof

The real installed decoder failed initial incremental MP3 probes; the local adapter then passed real decoding and an audible HomePod canary. Transport started before synthesis completed. The owner confirmed complete, smooth playback and a spoken general-question watch turn.

Pause previews showed synthetic gains but a real resumed-speech latency regression. They were disabled; warm-up retained. Local TTS audition results favored different timbres but the owner ultimately chose Edge for every language and removed alternative environments. Those experiments are not active production features.

See [performance](PERFORMANCE.md), [security](SECURITY.md), [source export](SOURCE-EXPORT.md) and the structured verification record. No raw private recordings, original transcripts or private deployment logs are included.
