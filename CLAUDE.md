# Piklin

## Making a release

Any computer can make one; the release key lives in the repo secret `PIKLIN_RELEASE_KEY`.

1. On each computer that builds (Mac for the .dmg, Linux for .deb/.AppImage, Windows for the .msi), run `packaging/upload-release.sh`. It uploads the unsigned packages and `.build` files of `VERSION` (from `piklin/app.py`) to the draft release `v<VERSION>`.
2. On the last one, run `packaging/upload-release.sh --sign`. It starts `.github/workflows/sign-release.yml`, which signs every package, verifies it as the updater does, and publishes the release.

Never upload or print the private keys. The recovery key stays offline. Nothing here runs by itself: the workflow starts only when asked.
