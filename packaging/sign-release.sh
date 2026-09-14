#!/usr/bin/env bash
# Sign a built .deb so installed copies of Piklin accept it as an update.
#
#   packaging/sign-release.sh dist/piklin_1.0.4_amd64.deb
#
# Writes <deb>.sha256 and <deb>.sha256.sig beside it; upload both with the
# .deb to the GitHub release. Piklin's updater refuses a package without a
# valid signature.
#
# The private key never enters the repository. It lives in
#   ~/.config/vezzu-studio/piklin-release-key.pem   (or $PIKLIN_RELEASE_KEY)
# and its public half is packaging/deb/release-key.pem. Keep a backup of the
# private key somewhere safe: without it, installed copies can no longer
# update themselves.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KEY="${PIKLIN_RELEASE_KEY:-$HOME/.config/vezzu-studio/piklin-release-key.pem}"
PUB="$HERE/deb/release-key.pem"
DEB="${1:?usage: sign-release.sh path/to/piklin_VERSION_ARCH.deb}"

[ -f "$DEB" ] || { echo "No such package: $DEB" >&2; exit 1; }
[ -f "$KEY" ] || { echo "No release key at $KEY" >&2; exit 1; }

dir="$(cd "$(dirname "$DEB")" && pwd)"
name="$(basename "$DEB")"
(cd "$dir" && sha256sum "$name" > "$name.sha256")
openssl pkeyutl -sign -inkey "$KEY" -rawin -in "$dir/$name.sha256" -out "$dir/$name.sha256.sig"
# Check with the public key the package ships, exactly as the updater will.
python3 -I "$HERE/deb/piklin-update" --verify-only \
    "$(dpkg-deb -f "$DEB" Version)" "$dir" "$PUB"
echo "Signed: $dir/$name.sha256 and $dir/$name.sha256.sig"
