# Latency, streaming and retained choices

All numbers below are from this project's recorded trials, not vendor guarantees. Different stage medians and different samples must not be added into a fictional end-to-end benchmark.

## Current production choices

Local Whisper ASR, exact supported light phrases, bounded Jev decisions through OpenRouter, Gemini fallback/general answers, Edge voices for German/English/Persian. `watch_asr_warm=true`, `watch_asr_overlap=false`, `watch_tts_streaming=true`. The disabled pause-preview branch remains experimental code.

## Jev interpretation

A paired synthetic comparison passed 76/76 fixtures on both baseline and candidate. Jev accepted 36 eligible fixtures; their median was 181 ms versus 511 ms with Gemini. Actual device writes and speech were replaced by fakes. The broader candidate median was 428.5 ms versus baseline 519 ms. It does not establish recognition or physical-device speed.

## Recognition

An earlier sample of home turns put recognition after Send near 2.9 seconds, interpretation near 0.6 seconds and overall home results near 4.7 seconds. Later physical samples still showed roughly 2.7–3.4 seconds for recognition.

Three isolated synthetic cold-worker comparisons with a fixed one-second release pause produced the same transcripts and showed 1.4–2.2 second savings from warm-up plus pause previews. Real resumed speech exposed about 1.5 seconds waiting for an obsolete preview before a new final decode. Previews were disabled. Retained warm-up can hide some loading while the owner speaks, but does not eliminate recognition computation. No device action runs on interim recognition.

## HomePod audio

A fixed German live canary started transport at approximately 3157 ms, while synthesis completed at 6715 ms. Transport completed near 20196 ms and the tail guard near 20700 ms. The owner confirmed smooth playback through the last words, then an actual general-question watch reply and ready state. These are one-canary stage times; transport start is not a measured first audible sample.

Edge generation feeds one continuous MP3/AirPlay session rather than two separately connected chunks. The cloud answer text is still held until the tool decision completes. Receiver buffering and network variability remain; see the [pyatv streaming documentation](https://pyatv.dev/development/stream/).

## Local TTS audition, then removal

Already-loaded Pocket (German/English) produced first PCM in 32–72 ms, with about 1.05 GiB peak process RAM for one resident language. A preset-only MOSS Nano configuration produced first PCM in 94–208 ms with about 1.31 GiB peak RAM. Cached model setup still took approximately 2.6 seconds for Pocket and 1.2–2.0 seconds for MOSS. These exclude ASR, AirPlay and HomePod delay. Two repeats per case and one synthetic phrase per language are not a robust quality/soak benchmark.

The owner preferred Pocket naturalness but found its original pace too fast. After slower samples, German/Persian favored Edge and English favored Pocket. The final choice was Edge for every language to retain the working pace, avoid local loading/residency tradeoffs and keep the setup simple. MOSS/Pocket were not integrated into the live gateway; their isolated environments/model downloads were subsequently removed recoverably. The source export contains no such dependencies or model files.

## Next meaningful measurements

Native streaming ASR with final Send validation; request-to-audible measurements on the real speaker; sustained reconnect/cancel tests; and warm/cold latency distributions. No such unperformed experiment is presented as completed work.
