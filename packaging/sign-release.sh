#!/usr/bin/env bash
# Sign a built package so installed copies of Piklin accept it as an update.
#
#   packaging/sign-release.sh dist/piklin_1.0.4_amd64.deb
#   packaging/sign-release.sh dist/Piklin-1.0.4.dmg
#
# Writes <file>.sha256 and <file>.sha256.sig beside it; upload both with the
# package to the GitHub release (and the .build file the build wrote). Piklin's
# updater refuses a package without a valid signature, on Linux and on a Mac.
#
# The private key never enters the repository. It lives in
#   ~/.config/vezzu-studio/piklin-release-key.pem   (or $PIKLIN_RELEASE_KEY)
# and its public half is packaging/deb/release-key.pem. Keep a backup of the
# private key somewhere safe: without it, installed copies can no longer
# update themselves.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
KEY="${PIKLIN_RELEASE_KEY:-$HOME/.config/vezzu-studio/piklin-release-key.pem}"
PUB="$HERE/deb/release-key.pem"
FILE="${1:?usage: sign-release.sh path/to/piklin_VERSION_ARCH.deb | path/to/Piklin-VERSION.dmg}"

[ -f "$FILE" ] || { echo "No such package: $FILE" >&2; exit 1; }
[ -f "$KEY" ] || { echo "No release key at $KEY" >&2; exit 1; }

# Ed25519 signing needs OpenSSL 3; a Mac's own openssl is LibreSSL.
OPENSSL="${OPENSSL:-openssl}"
if "$OPENSSL" version 2>/dev/null | grep -q LibreSSL; then
  for o in /opt/homebrew/opt/openssl@3/bin/openssl /usr/local/opt/openssl@3/bin/openssl; do
    [ -x "$o" ] && OPENSSL="$o" && break
  done
fi

dir="$(cd "$(dirname "$FILE")" && pwd)"
name="$(basename "$FILE")"
if command -v sha256sum >/dev/null; then
  (cd "$dir" && sha256sum "$name" > "$name.sha256")
else
  (cd "$dir" && shasum -a 256 "$name" > "$name.sha256")
fi
"$OPENSSL" pkeyutl -sign -inkey "$KEY" -rawin -in "$dir/$name.sha256" -out "$dir/$name.sha256.sig"

# Check with the public key the package ships, exactly as the updater will.
case "$name" in
  *.deb)
    python3 -I "$HERE/deb/piklin-update" --verify-only \
        "$(dpkg-deb -f "$FILE" Version)" "$dir" "$PUB" ;;
  *.dmg)
    PYTHONPATH="$ROOT" python3 -c '
import sys
from pathlib import Path
from piklin.updates import verify_signed
verify_signed(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]).read_text())
' "$dir" "$name" "$PUB" ;;
  *) echo "Not a .deb or .dmg: $name" >&2; exit 1 ;;
esac
echo "Signed: $dir/$name.sha256 and $dir/$name.sha256.sig"
