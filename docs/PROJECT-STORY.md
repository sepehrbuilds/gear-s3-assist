# Gear Assist: from a Gear S3 microphone to a home controller

## Current result — 2026-11-08

The Gear S3 Frontier now runs a circular push-to-talk home controller and assistant. It records from its own microphone, uses direct pinned/authenticated Wi-Fi to a dedicated Mac mini backend, displays quiet home-device confirmations, and sends general spoken answers to a HomePod. No LLM runs on the watch.

The current stack is local Whisper ASR, an exact supported light-command route, bounded Jev decisions through OpenRouter, Gemini fallback/general answers, fixed Home Assistant/TV actions and streaming Microsoft Edge TTS. Room and individual lights, capability-limited color/brightness, TV on/off/apps/PS5 and door locking were tested. Door unlock/open is not exposed. Edge remains the selected voice for German, English and Persian; local TTS alternatives were auditioned and removed.

The first thirteen sections below record the earlier microphone/root/capture milestones. Their timing examples describe those particular tests, not today's average. Sections fourteen onward describe the later home-control and streaming upgrades. Earlier watch service reboot/autostart and SDB-disconnected acceptance remain evidence for that watch layer; the latest complete stack has not received a new full reboot/soak qualification.

## 1. Prove the old watch can record usable speech

Before rooting or installing the final app, we tested the ALSA microphone from the normal authorized shell. A ten-second quiet capture produced 960,000 bytes; a thirty-second spoken capture produced 2,880,000 bytes. Both were 48 kHz mono signed 16-bit little-endian PCM and completed without recorder errors.

The raw microphone level was low. The quiet baseline RMS was about 19.8 sample units; spoken RMS was about 155.2, with peak 9,667 out of a nominal 32,768 scale. Gain plus conversion to 16 kHz allowed the local recognizer to understand speech. These were bounded functional tests, not a long-duration microphone qualification. Temporary recording files were used during the initial investigation; no audio is included in this publication package.

The first practical route was a Mac-mediated prototype. Earlier backend traces showed completed Gear-labelled turns while the Mac was relaying capture. Those proved the voice pipeline could work, but were not proof of standalone watch operation.

## 2. Investigate installation before changing firmware

A minimal Tizen wearable Web app was signed with locally generated author certificates and two generic SDK distributor variants. One distributor was expired; the other had a valid date range. The actual watch rejected both with installation error -12 and “Non trusted certificate.”

An important trap: SDB itself exited successfully even though the package-manager callbacks reported installation failure. We treated the package manager's explicit success/failure as evidence rather than assuming a zero process exit code meant the app installed. SDK signing profiles were restored after the tests.

This established failure of these specific signers on this watch. It did not establish that every possible no-root installation method was impossible.

## 3. Inspect the firmware and recovery constraints

We acquired older CQL1 combination and DSA1 stock archives, checked download/archive consistency and inventoried their contents offline. All fifteen live partition geometries matched the corresponding decoded PIT tables. That established image-to-partition relationships, not universal downgrade compatibility or Samsung authenticity.

The combination image contained a setuid su binary and an early-boot pivot_root utility. The older production rootfs had no su, and its normal ramdisk lacked pivot_root at the location needed by the proposed custom init. Full archives also contained bootloader, partition-table and other images, so loading an entire combination bundle would have exceeded the narrowly scoped experiment.

We found a practical problem in a public root toolkit: its supplied replacement init was larger than the original file's allocated extent. We checked actual images rather than treating a README as executable proof. An exact original FVG1 normal-ramdisk backup was not available before the approved experiment. Older stock was a recovery candidate, not a proven exact rollback.

Windows ARM/VMware and NetOdin were investigated as potential flashing/recovery routes. The actual narrowly scoped flash experiments were performed from the Mac mini using Brokkr. Windows and the MacBook were not part of the final client runtime.

## 4. Root with a bounded ramdisk change

Static inspection found an internal root-shell path in the watch's SDB daemon gated by existence of /bin/su. The candidate changed that existence probe to /bin/sh, leaving SDB authentication code intact. A compact replacement early init used the factory ramdisk's utilities, mounted the installed root filesystem initially read-only and bind-mounted the patched daemon before continuing boot.

Two specific RAMDISK1-only images were written with explicit user approval. The second restored missing daemon extended attributes. The procedure did not flash a bootloader, PIT, recovery image or Wear OS image. It changed early boot, including the normal verified-root startup path, so retaining application signature checks and TLS should not be confused with retaining the stock boot integrity chain.

A flasher's successful-transfer report was insufficient. There were post-reset timeouts and difficult SDB reconnections. Root was established only when the real watch returned UID 0 through authenticated SDB. A loopback-only Mac forwarding relay produced a fresh usable transport; it logged packet headers, not authentication payloads. Host SDB authorization keys were preserved.

After root was working, we captured and checksum-verified the current booting normal ramdisk, stock rootfs before the allowlist edit, recovery ramdisk, boot kernel, recovery kernel and modules. These private backups remain local. The saved normal ramdisk is the modified, working root-capable image: **there is no exact original FVG1 normal-ramdisk backup.** A booting snapshot is not a factory-stock recovery guarantee.

## 5. Keep trust checks and use private signing identities

Generic SDK signing had failed, so the installed app uses private project author and distributor chains. Separate roots were necessary: putting the same root in both trust domains caused a rejection. A trust overlay retained the existing Samsung certificate directory and augmented only the relevant developer/public trust domains with the separate private project roots.

The overlay mounts over stock trust locations at startup; private signing keys and signing passwords stay on the Mac. The UI was installed as GearPTT001.Main under package GearPTT001 with normal app signature checking enabled. No global signature-verifier disable was used.

The watch's systemd allowed-units file required an append-only addition for gear-ptt.service and gear-ptt-policy.service. Its original bytes were backed up. That one targeted edit required a temporary read-write rootfs remount; the filesystem was returned to read-only and the exact limited change was verified. It would be inaccurate to say the root filesystem was never written.

## 6. Find the correct process identity

Root on Tizen is not the whole permission model: SDB's root shell still carried a restricted SMACK label. Administrative deployment used bounded systemd jobs with the normal privileged system identity, rather than treating every root shell as unrestricted.

An invented custom client SMACK label could not establish the backend connection despite apparent policy rules. The installed app's registered identity worked. The final client runs as owner UID 5001, group users, with the registered User::Pkg::GearPTT001 label, NoNewPrivileges and zero effective/bounding capabilities. It uses the app's declared Internet privilege.

Node, bash and arecord copies are root-owned and app-read-only labelled. The service has a private temporary directory, read-only system protection, a 96 MB memory limit and a 24 MB V8 old-space limit. Private app-labelled logging avoids an old Node 4 inherited-stream issue that otherwise produced “Unknown stream file type.”

The root policy unit remains an administrative trust-mount component; the audio/network client itself is non-root. The old experimental custom GearPTT rules were absent after reboot. A proposed broad rule was rejected and never executed; connectivity was solved with the registered app identity instead.

## 7. Separate backend credentials from UI control

The backend connection uses WSS with CA/host verification, an exact SHA-256 certificate pin and a bearer token. Actual non-root watch tests accepted the correct credentials, rejected a wrong pin, and rejected a wrong token.

Backend secrets are in a private watch configuration, not inside the UI package. Provisioning used an on-watch RSA key, RSA-OAEP SHA-256 and AES-256-GCM, transferring ciphertext through the deployment channel. The watch's provisioning private key was not copied to the Mac. The private configuration has mode 0400 and an app label.

The UI has a separate watch-local control token and talks only to loopback WebSocket port 8765. The server checks the endpoint, Host, allowed file origins, WebSocket key shape and control subprotocol token. The unauthenticated loopback /status endpoint exposes state and counters, not backend secrets or transcripts.

The public UI source deliberately lacks the generated control-key.js. A Mac browser preview points at the Mac's loopback and does not have the watch's private control token. Its “Connecting” screen cannot be used to diagnose the physical watch display.

## 8. Build the circular interaction

The signed Web UI fits the round screen. Back toggles recording and send while the app is in the foreground; Home retains normal system behavior. The bezel changes gain while idle, complete or error, with a default +18 dB and a bounded 0–30 dB range. The app shows connecting, listening, thinking, replying, complete and error states. Replies are audio on HomePod; the backend does not return reply text to the watch.

The user physically confirmed the screen and bezel. A small zero-gain display fallback in the current UI remains a known minor issue; it was not silently presented as fixed. The installed signed package was preserved during final capture repairs.

## 9. Debug the timeout rather than flash again

The handoff's remaining failure was a real recording followed by capture_timeout, with a report of a screen stuck on Connecting. Logs proved the watch had already completed pinned TLS and reached recording. That redirected the investigation away from rooting, certificates and speculative firmware changes.

We verified that the physical watch could receive state changes. A local start/stop probe delivered audio and an end message, returning no_speech for silence. The user then performed a physical spoken turn and heard the correct answer from HomePod. That was important progress, but not the final acceptance gate.

A control-socket leak was independently reproduced: an upgraded HTTP socket could stay half-open after TCP end, consuming one of two peer slots. Destroying the socket on end/error fixed it. A regression test failed on the original code at the third connection and passed with the fix.

We separated the initial twelve-second socket deadline from capture/response deadlines, enabled TCP_NODELAY, added an explicit approximately nineteen-second capture stop, and bounded recorder shutdown with a 500 ms escalation. A long capture then reached backend processing, but that did not yet prove reliable disconnected operation.

## 10. Reboot and expose the real standalone problem

We enabled the two Gear-specific units with reversible relative links in multi-user.target.wants. Tizen's systemctl is-enabled returned a symlink-resolution error, so we checked actual target dependencies and reboot behavior instead of reporting a guessed enablement state.

An intentional test reboot changed the boot ID. Both units started automatically; trust overlays were mounted; rootfs was read-only; the client retained UID 5001 and zero capabilities. No old custom GearPTT rules remained.

With SDB and the relay disconnected, a physical test failed again: recording started, a second Back press did not stop it, and the watch eventually displayed the generic Wi-Fi/error message. Backend telemetry showed a short initial burst, then capture timeout. Autostart was working, but the project was not finished.

## 11. Measure the full workload

A root benchmark initially processed ten seconds of audio in about 0.735 seconds. It was misleading for the live path because it omitted the real send workload and execution conditions. The running service accumulated 64 KiB recorder chunks; callbacks took roughly 650–890 ms for chunks containing only about 683 ms of 48 kHz audio. Incoming TLS data and button commands could wait while audio processing monopolized the event loop.

We removed modulo indexing from the filter's hot loop and introduced a paused stream pump that yielded via setImmediate between bounded input slices. This restored control responsiveness and passed repeated cancel tests. It still left little throughput margin. A requested Claude Opus consultation reviewed the supplied timing evidence and recommended native resampling; it did not successfully inspect the local files, so its advice was treated as an evidence-based second opinion rather than a completed code review.

The exact reason the first root benchmark differed so much from the service was not conclusively isolated. It would be an overclaim to attribute everything to a proven V8 bug, CPU governor setting or Wi-Fi defect. The measured workload and the verified repair were sufficient to remove the bottleneck.

## 12. Move resampling to native ALSA

The final recorder uses plughw:0,0 at 16 kHz, mono S16_LE. A direct on-watch check produced exactly 32,000 bytes for one second. ALSA now performs conversion in native code. Node only applies bounded microphone gain, clamps samples and emits 30 ms frames. The slow JavaScript FIR was removed from production.

Each binary backend message contains a four-byte little-endian sequence followed by 960 PCM bytes: 480 samples at 16 kHz, or 30 ms. The pump still yields between bounded slices to keep control, TLS and timer events responsive.

On-watch Node 4 tests passed for framing, bounds, masking, PCM chunking, odd-byte carry, gain and clipping. Host tests additionally verify exact zero-gain sample preservation and the control half-close regression.

The final automatic long capture recorded 604,000 raw input bytes, sent 630 padded frames and closed at about 19.055 seconds. Backend end receipt was about 19.129 seconds. It returned no_speech for silence. Two consecutive start/cancel cycles passed.

## 13. Prove the finished path with diagnostics disconnected

We disconnected SDB and stopped the temporary relay again. The user performed a physical Back-button spoken turn using the final native-capture version and confirmed that it worked. The matching completed backend trace contains 184 frames, first audio at 281 ms, end at 5.711 seconds, recognition completed at 8.576 seconds and full turn completion at 25.817 seconds. These are one-turn observations, not benchmark averages. HomePod audibility is supported by the user's physical confirmation, not merely transport logs.

The result: a working watch microphone and physical controls, direct pinned/authenticated Wi-Fi transport, Mac mini AI processing, HomePod spoken reply, reboot/autostart and normal use without SDB. Original backups and secrets were preserved. No new firmware flash was used to solve the audio problem.

## 14. Separate the watch backend and move language decisions to the cloud

A dedicated Gear service and protocol v2 separated the watch from the pre-existing desk microphone backend. This preserved the desk route while allowing short results and status data to return to the watch. The watch endpoint has its own bearer credential; a token for one endpoint does not authorize the other.

Local Whisper remains speech recognition. Cloud Gemini handles fallback interpretation and general assistant answers; the watch no longer waits for a local LLM. Removing local LLM inference did not remove local ASR or its latency. English, German and Persian commands/answers were exercised without claiming exhaustive multilingual accuracy.

## 15. Make home commands explicit and quiet

We built the light map from the owner's room assignments, including group aliases and separately addressable individual lights. A room-off request plus a named-light-on override is valid. Complete batches are validated and preflighted before any side effects. The planner cannot provide arbitrary entity IDs, service names, URLs or TV app IDs.

The result path reports requested values and observed device states on the watch. Device commands stay quiet on HomePod. An HTTP success is not treated as proof that a device reached the requested state, and an unknown outcome is not automatically retried. Later validation is not an all-or-nothing transaction: a device can still fail during an already-started batch.

## 16. Add TV, apps, lock-only door and capable light settings

The TV gained separate on/off, fixed app launch and a configured HDMI input. YouTube, Jellyfin and the PS5 input were physically exercised. The TV's local REST app API worked after an attempted HA pairing flow produced no TV prompt; that temporary flow was closed rather than weakening TV settings. TV-off never wakes the TV, and contradictory TV intents are refused.

Door control exposes only a named lock target and `lock/lock`. An independent positive-utterance gate rejects unlock/open, negation, conditions, questions and quoted instructions. The owner performed the physical closed-door lock test. This is a constrained prototype, not a certified access-control system.

Brightness, RGB and white temperature are allowed only for configured capable lights and then checked against observed state. Zero percent means off. The bathroom was later restricted to on/off by preference even though its hardware supported more. Unsupported room-wide settings are refused rather than silently skipping lights.

## 17. Use Jev as a bounded decision step

The first route is exact local handling for a narrow set of supported light phrases. Other eligible on/off and configured TV app/input commands can use Jev via OpenRouter Decisions. Anything outside its complete supported lexical envelope goes to Gemini before an effect.

Jev uses longest alias matching, complete coverage of named targets, explicit scope judgments, validated probabilities and a conservative winning-probability threshold. Requests have a 1.5-second total timeout and no retry. Every proposed plan must pass the same deterministic home-action validator. No fallback or replay occurs after execution begins.

In a paired synthetic cloud trial, both baseline and candidate passed 76/76 cases. The 36 accepted Jev cases had 181 ms median interpretation time versus 511 ms for the Gemini baseline. Device execution and speech were fake in that trial. These are interpretation measurements, not voice-to-device latency or a universal Jev quality result. The owner later confirmed the physical watch commands worked.

## 18. Measure recognition overlap and keep only the safe latency improvement

Recognition after Send remained a major part of the delay. We added a loopback demand-service warm endpoint and started it when recording begins. It shares worker locking and the existing idle unload behavior; a failed optional warm-up cannot fail the final turn.

A single pause preview could recognize while the owner was still recording, but final reuse required byte-identical audio. Three synthetic language cases saved about 1.4–2.2 seconds after Send. A real resumed-speech turn then exposed an extra approximately 1.5-second wait for an obsolete preview before final decoding. That outweighed a blanket speed claim.

Final setting: warm-up enabled, pause previews disabled. Real recognition in recent accepted turns still took roughly three seconds after Send. A genuine streaming recognizer remains future work. Interim speech never triggers device actions.

## 19. Stream synthesized audio to HomePod in one session

The earlier path waited for complete synthesized chunks and used up to two AirPlay sessions. Gear now submits one full answer as one streaming Edge job. An initial 8192-byte prebuffer bounds startup underruns, and incoming MP3 feeds the transport while synthesis continues. Bounded memory, FIFO jobs, idempotent request IDs, cancellation, transport/tail tracking and no uncertain replay are retained. Other shared-gateway callers keep their previous default behavior.

A real decoder probe rejected the installed pyatv StreamReader wrapper, so a small local adapter provides exact reads and a bounded seekable MP3 prefix without editing the installed library. The tested path targets Edge's MP3 format. An unknown-duration metadata warning can be benign; transport completion alone still does not prove audibility.

A fixed German canary started transport at about 3.2 seconds while complete synthesis took about 6.7 seconds. The owner confirmed smooth audible playback through the final words, then a complete general-question watch turn and return to ready. The cloud answer text is still collected before speech starts to preserve the tool boundary: this is streaming synthesized audio, not native HomePod conversational generation. Receiver buffering remains.

## 20. Audition local voices and choose the working cloud voice

We tested MOSS Nano ONNX and Pocket TTS separately on the 16 GiB M4 Mac mini, using synthetic German/English/Persian where supported. Already-loaded Pocket produced first PCM in 32–72 ms with about 1.05 GiB peak process RAM for one language. A preset-only MOSS configuration used about 1.31 GiB and produced first PCM in 94–208 ms. Initial model loading, recognition and HomePod buffering are excluded from those numbers.

The owner found Pocket more natural but too fast for German and English. Pitch-preserving slower samples led to a preference for Edge German, slower Pocket English and Edge Persian. MOSS's tested Persian preset also performed worse in an imperfect ASR content check. When weighing model loading and resident RAM, the owner chose Edge for all languages. The isolated alternatives and model downloads were removed recoverably; the working voice route was never switched. Test notes and synthetic comparisons remain local.

## Final scope and limits

This is one prepared watch with physically accepted controls and replies, not a fleet, battery, overnight reliability or security certification. The modified boot chain is separate from retained app-signature/TLS checks. The Mac mini, cloud services and applicable HA/HomePod dependencies must be available. Bluetooth was discussed but not implemented or benchmarked. Model/cloud access can incur costs, and Edge service behavior can change.

No private signing material, firmware, runtime secrets, original recordings or raw command logs are part of the public package. The public backend is a sanitized reference with example identifiers, not a copy of the owner's deployment. During release preparation, the owner selected MIT for project-owned source. Third-party components retain their own terms; see [THIRD-PARTY](../THIRD-PARTY.md).
