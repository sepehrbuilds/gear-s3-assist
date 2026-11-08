# Limits and unimplemented work

- One prepared watch was tested. No fleet coverage, battery benchmark, overnight soak, quantified packet-loss study or general security certification is claimed.
- Earlier watch unit/trust/autostart behavior passed an intentional reboot. The final native source ran within that verified boot; a fresh full reboot after every later backend/UI feature was not performed. An earlier spoken turn passed with SDB and relay disconnected; that check was not repeated after the later UI/backend additions.
- Mac mini, Wi-Fi, cloud text services, Home Assistant/TV endpoints and HomePod are dependencies as applicable. No LLM or answer voice runs on the watch. Bluetooth transport was discussed but not implemented or compared.
- There is no universal rooting/signing/provisioning installer, vendor executable set, redistributable firmware or personal signed WGT in this package. The sanitized backend reference still needs private setup and model assets.
- Home batches are not transactions. Some devices may change before another fails. The system reports uncertainty and avoids automatic replay; it does not guarantee a speech/model decision is correct.
- HomePod streaming overlaps synthesized audio and transport, while the complete cloud answer is still collected first. Receiver buffering and network delays remain. The Edge MP3 adapter is tested against a particular format/dependency version; arbitrary MP3 streams are not promised.
- Recognition warm-up is enabled. Pause previews remain disabled after an observed latency regression. True streaming ASR is not implemented.
- Local MOSS/Pocket voice experiments were not deployed and were removed at the owner's request. Edge remains selected for all three tested languages. Shared code includes optional legacy TTS/local-model branches, not an indication that those alternatives are active.
- The circular UI's earlier zero-gain display fallback was corrected in the current source; old milestone notes refer to an older UI. The current export includes result text and connection status, unlike v1.
- The modified root boot path remains separate from retained app/TLS safeguards. There is no exact original FVG1 normal-ramdisk backup.
- Benchmarks are bounded synthetic comparisons or specific accepted turns, not end-to-end latency guarantees. Test counts can change with new code and do not replace hardware acceptance.
- MIT was selected for project-owned source. Third-party terms remain separate; see [THIRD-PARTY](../THIRD-PARTY.md).
