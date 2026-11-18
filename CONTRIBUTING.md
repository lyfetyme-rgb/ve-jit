# Contributing

Thanks for your interest. Before contributing, please read and agree to the
[Contributor License Agreement](CLA.md). By opening a pull request you confirm
that you have read and agree to the CLA, including the copyright assignment.

All contributions are accepted only under the CLA.

## Never share secrets
Production signing keys (`POLICY_SIGNING_KEY`, `CLOUD_SIGNER_TOKEN`, `CLOUD_SYNC_TOKEN`,
database credentials, service tokens) must never appear in commits, issues, PRs, screenshots
or chat. Tests generate ephemeral keys at runtime (`Ed25519PrivateKey.generate()`); no real
key is needed to develop or test anything here. If you accidentally commit a secret, notify
the maintainers immediately — rotation is the only remedy, deletion is not enough. The CI
runs `python tools/scan_secrets.py` on every push and will fail on committed secrets.
