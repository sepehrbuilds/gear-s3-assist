# Prepared-watch deployment scope

This is a tested source reference for one prepared SM-R760 / FVG1 / Tizen 4.0.0.7 watch. It does not turn a stock watch into the completed setup automatically. Routine app/backend updates require no new firmware flash. Do not flash from project prose.

The working watch has a normally registered GearPTT001 package / GearPTT001.Main app, private author/distributor signing identities, a device-specific trust overlay retaining Samsung roots, allowed service entries and reversible target wants links. Root-owned Node/bash/arecord executables and source use the app's read-only SMACK labels; the voice process runs under the registered app identity without capabilities. Those private preparation steps and vendor binaries are not distributed.

## Watch source/build requirements

- Current `client.js`, `audio.js` and `websocket.js`, plus the service templates.
- Your private `config.json`, matching the example keys and dedicated v2 backend port, with your valid CA and exact backend pin.
- A privately generated `ui/control-key.js` carrying the same private control token, inserted into the private UI build. Never commit it.
- Your own normal signed UI package; `ui/config.xml` is v1.2.0. The public source has no installable personal WGT.
- Your own backed-up `policy/apply-policy.sh` and service policy. The policy template depends on target-specific preparation and must not be blindly enabled on an unprepared device.

The package does not issue Samsung certificates or disable any verifier. The detailed story records why earlier SDK signing failed on the original device; it is not a universal certificate workaround.

## Backend requirements

See [backend setup](../backend/README.md). Separate ASR, Gear conversation and speech jobs as needed. The example TV sources map is empty; add a fixed verified source privately before attempting input selection. App-ID placeholders deliberately fail validation. Keep the dedicated watch token distinct from other microphone endpoints. Configure your own HA map/capabilities/TV app IDs, keep the lock-only gate, and enable real home writes only after reviewing the fixed example configuration.

The source includes a generic environment-file launcher, not the author's production plists or credential-import helpers. Example credentials are empty; examples cannot be used as actual device configuration without deliberate private setup.

## Acceptance on your device

Verify app signature acceptance, process UID/capabilities/SMACK labels, correct/wrong CA-pin-token behavior, physical Back/touch/bezel and bounded capture/cancel. Check short device results against actual states. Test a spoken general answer through the actual HomePod, correct ready-state recovery, reboot/autostart, and a final turn with SDB/relays disconnected. Browser preview and HTTP/transport success are not substitutes for those physical checks.

Snapshot matching source/config/UI sets and preserve owner/mode/SMACK attributes before updates. Never use routine source rollback as a reason to reflash firmware. This project has no exact original FVG1 normal-ramdisk backup; the preserved working boot image is modified.
