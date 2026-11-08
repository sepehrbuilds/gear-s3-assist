# Third-party dependencies and reuse boundaries

The owner selected MIT for the project-owned code and documentation on 2026-11-08. The [root LICENSE](LICENSE) records attribution as Sepehr (`sepehrbuilds`); `backend/LICENSE` is an identical copy for standalone Python package metadata. This selection does not relicense third-party components, assets, model weights or external services.

Existing source-license notices and the dependency inventory were preserved during release preparation. No dependency code is vendored; third-party components keep their own terms.

## What is distributed

This tree contains project source and an original-source export record, not vendored Python/Node dependencies, model weights, firmware, Samsung/Tizen binaries, signed apps, certificates or recordings. Watch code uses Node built-ins. Historical root tools are credited in [references](docs/REFERENCES.md) and are not bundled. No dependency source was newly copied during release preparation.

## Installed Python dependency inventory

The table records metadata from the exact 45 distributions installed from `backend/requirements-tested.txt` in the isolated macOS Python 3.13 test environment on 2026-11-08. Labels are upstream metadata, not a legal compatibility opinion or a dependency vulnerability audit. Consult each pinned distribution's included LICENSE/COPYING/NOTICE files for full terms, including bundled components.

| Distribution | Version | Declared license metadata |
| --- | --- | --- |
| aiohappyeyeballs | 2.7.1 | Python Software Foundation License |
| aiohttp | 3.14.4 | Apache-2.0 AND MIT |
| aiosignal | 1.4.0 | Apache Software License |
| annotated-types | 0.8.0 | MIT |
| attrs | 26.1.0 | MIT |
| certifi | 2026.10.18 | Mozilla Public License 2.0 (MPL 2.0) |
| cffi | 2.1.1 | MIT-0 |
| chacha20poly1305-reuseable | 0.13.2 | Metadata: Other/Proprietary; bundled LICENSE contains BSD-style and Apache-2.0 texts (review full file) |
| charset-normalizer | 3.5.1 | MIT |
| coloredlogs | 15.0.1 | MIT License |
| cryptography | 50.0.1 | Apache-2.0 OR BSD-3-Clause |
| edge-tts | 7.3.1 | GNU Lesser General Public License v3 (LGPLv3) |
| flatbuffers | 25.12.19 | Apache Software License |
| frozenlist | 1.8.0 | Apache-2.0 |
| humanfriendly | 10.0 | MIT License |
| idna | 3.19 | BSD-3-Clause |
| ifaddr | 0.2.0 | MIT License |
| iniconfig | 2.3.0 | MIT |
| miniaudio | 1.71 | MIT License |
| mpmath | 1.3.0 | BSD License |
| multidict | 6.9.1 | Apache License 2.0 |
| numpy | 2.2.6 | BSD License |
| onnxruntime | 1.22.1 | MIT License |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pluggy | 1.6.0 | MIT License |
| propcache | 0.5.2 | Apache Software License |
| protobuf | 7.36.1 | 3-Clause BSD License |
| pyatv | 0.17.0 | MIT |
| pycparser | 3.0 | BSD-3-Clause |
| pydantic | 2.13.5 | MIT |
| pydantic_core | 2.46.5 | MIT |
| Pygments | 2.21.0 | BSD-2-Clause |
| pytest | 9.0.3 | MIT |
| pytest-asyncio | 1.4.1.post1 | Apache-2.0 |
| requests | 2.34.2 | Apache Software License |
| six | 1.17.0 | MIT License |
| srptools | 1.0.1 | BSD License |
| sympy | 1.14.0 | BSD License |
| tabulate | 0.10.0 | MIT |
| tinytag | 2.3.2 | MIT License |
| typing-inspection | 0.4.4 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| urllib3 | 2.8.0 | MIT |
| yarl | 1.25.1 | Apache-2.0 |
| zeroconf | 0.151.3 | LGPL-2.1-or-later |

`edge-tts` declares LGPLv3 and `zeroconf` LGPL-2.1-or-later; installing them does not relicense their code under this project's MIT license. Binary/app/environment redistribution would need a separate review of the exact bundled artifacts and notices. This release intentionally omits the virtual environment.

## External runtime assets and services

- [whisper.cpp](https://github.com/ggml-org/whisper.cpp) is an external executable; its upstream [license](https://github.com/ggml-org/whisper.cpp/blob/master/LICENSE) is MIT. No executable or Whisper weight is included. Select and review the exact model's own terms separately.
- The VAD wrapper expects the legacy Silero ONNX ABI used by openWakeWord (`input,h,c,sr`). No particular model file, checksum or redistribution right is established here. The [openWakeWord upstream](https://github.com/dscripka/openWakeWord) distinguishes code and model licensing; do not assume its code license covers all weights. This remains a private-setup prerequisite.
- ffmpeg supplies a synthetic test fixture locally; its executable is not bundled. Node, ALSA tools, Tizen SDK/runtime, Apple AirPlay/HomePod and Samsung firmware remain external dependencies; their availability/terms and exact device preparation are not supplied by a project license.
- OpenRouter/Jev/Gemini and Microsoft Edge speech are network services. Their access, quotas, model aliases and terms can change. A source license does not grant service access. Optional ElevenLabs/Gemini speech paths exist in source but were not integration-tested here.
- Home Assistant is an external integration, not a bundled component. YouTube, Jellyfin and PS5 are configured integration names; no affiliation or endorsement is claimed.

Upstream pages were checked on 2026-11-08 where linked above. This inventory supports a source release review; it is not clearance to redistribute omitted vendor files or third-party models.
