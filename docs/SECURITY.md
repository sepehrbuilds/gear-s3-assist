# Security and publication boundaries

## Retained checks

The watch validates TLS CA/host and an exact certificate pin, then authenticates using a bearer token. Wrong-pin and wrong-token attempts were rejected as expected on the actual watch. Watch and desk endpoints have separate tokens. The UI has an independent authenticated loopback control token and never receives the backend credential. Generated `control-key.js` belongs only in a private signed UI build.

The watch client runs as non-root UID 5001, with its registered app SMACK label, NoNewPrivileges, zero effective/bounding capabilities and app-read-only executable/source labels. The administrative trust-overlay unit is separate. Stock Samsung roots were retained and the privately signed app passed normal signature checks. The boot path was modified during rooting; these safeguards do not restore stock verified boot or certify an old Node/OpenSSL platform.

Only fixed configured home actions are exposed. Plans are validated/preflighted before effects. The door surface is lock-only with a separate positive-utterance gate. Unknown device/playback outcomes are not automatically retried, and there is no second-model fallback after execution begins. This is not a security certification; language recognition can be wrong and batches are not atomic.

## Data flow

Microphone audio goes over pinned/authenticated Wi-Fi to local Whisper. Cloud models receive transcript text, bounded conversation context and configured home catalog/capability metadata. The selected Gear route sends generated answer text to Edge for speech. The shared code also contains optional ElevenLabs/Gemini TTS branches, which receive answer text if explicitly selected; those branches are not the chosen Gear route. Requesting provider data-collection restrictions is a routing configuration, not an independently audited promise of provider retention behavior.

Operational traces are designed to contain timing/status/counts, not raw audio, transcripts, replies, credentials or exception messages. Conversation history is temporary in-process state, not a durable command archive or automatic model training. Authenticated watch-local control does carry short result/answer text for display; the unauthenticated status response is content-free.

## Excluded material

No real tokens/API keys, passwords, private keys, signing chains, personal certificates, signed WGT, encrypted provisioning blobs, vendor executables, firmware/partition dumps, original voice recordings, raw private logs, runtime configurations, private launchd plists, real home entity IDs or private host addresses are included. Examples use fictional identifiers, loopback and documentation IP ranges. Private backups remain local and unchanged.

The public gateway binds loopback only. Deploying across hosts needs an explicitly reviewed transport/access boundary; do not remove certificate or authentication checks to make it connect. Old first-install/root/provisioning helpers are not represented as current reusable installers.

The package is an allowlisted directory export without upstream Git history. Future commits/assets can introduce new disclosures: scan the exact tree/archive and review screenshots/videos before uploading. Gitleaks is one check, not a proof that publication can never expose private information. The owner selected MIT for project-owned source; dependency licenses and asset provenance remain separate (see [THIRD-PARTY](../THIRD-PARTY.md)).
