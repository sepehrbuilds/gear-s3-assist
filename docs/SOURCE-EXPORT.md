# Export boundary and transformations

This tree was assembled from an explicit allowlist, without any Git history. The original project and private backups remain local. The watch runtime modules/UI come from the current source; the public control regression generates its nonce rather than embedding an RFC test literal. Backend modules/tests come from the current tested source.

Publication transformations:

- Backend examples replace the original home path/LAN locators with relative model paths, loopback URLs and RFC documentation addresses.
- Real home entity identifiers are replaced with `example_` identifiers consistently across fixed runtime guards, fixture data and tests; aliases/room keys remain illustrative. The watch configuration's TV app/source IDs are placeholders. Device control is disabled in the example.
- The public speech gateway uses a documentation-only HomePod default and accepts only loopback binding. The deployed gateway's owner-specific tailnet allowlist is not exported. Its core speech lifecycle is otherwise unchanged. A pre-existing legacy job label and port remain as intentional single-owner compatibility checks; no private plist or environment is distributed.
- No private launcher/plist, signing/provisioning helper, generated UI credential, first-install automation, vendor executable, model weight, personal package, raw telemetry or credential file is copied.
- The generic local-model/prototype branch remains in the shared backend code for regression coverage. The selected Gear configuration uses cloud models and Edge speech; MOSS/Pocket were only separate experiments and are absent.

The local provenance/hash record maps source and exported file hashes. The public manifest hashes the exported package only. A changed public example does not imply that the live production configuration changed. Unit tests validate this export independently; they cannot reproduce the owner's private signing and hardware preparation.
