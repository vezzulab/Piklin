#!/usr/bin/env bash
# Download the rclone Piklin carries, for every system it ships on, into
# packaging/rclone-cache - and check each file before it goes anywhere near
# a package: the checksum list must carry a good signature from rclone's
# release key, and every download must match that list.
#
#   packaging/fetch-rclone.sh
#
# Needs curl, gpg and shasum (or sha256sum). The keyring used is a throwaway
# one: nothing is added to yours.
set -euo pipefail

RCLONE_VERSION=v1.75.1
# rclone's release signing key (Nick Craig-Wood), as published on rclone.org/KEYS
RCLONE_KEY=FBF737ECE9F8AB18604BD2AC93935E02FF3B54FA
TARGETS=(osx-arm64 osx-amd64 linux-amd64 linux-arm64)

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE="$HERE/rclone-cache"
BASE="https://github.com/rclone/rclone/releases/download/$RCLONE_VERSION"
say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
mkdir -p "$CACHE"
cd "$CACHE"

say "rclone $RCLONE_VERSION"
for t in "${TARGETS[@]}"; do
  f="rclone-$RCLONE_VERSION-$t.zip"
  [ -s "$f" ] || curl -fsSL --retry 3 -o "$f.part" "$BASE/$f" && { [ -s "$f" ] || mv "$f.part" "$f"; }
done
curl -fsSL --retry 3 -o SHA256SUMS "$BASE/SHA256SUMS"
curl -fsSL --retry 3 -o rclone-KEYS.asc https://rclone.org/KEYS
# rclone's licence, from the same release, to travel with every package
curl -fsSL --retry 3 -o COPYING "https://raw.githubusercontent.com/rclone/rclone/$RCLONE_VERSION/COPYING"
grep -q "MIT License\|Permission is hereby granted" COPYING \
  || { echo "rclone's COPYING is not the MIT licence text expected" >&2; exit 1; }

say "Checking the signature of the checksum list"
GNUPGHOME="$(mktemp -d)"; export GNUPGHOME
trap 'rm -rf "$GNUPGHOME"' EXIT
gpg --quiet --import rclone-KEYS.asc 2>/dev/null
status="$(gpg --status-fd 1 --verify SHA256SUMS 2>/dev/null || true)"
echo "$status" | grep -q "^\[GNUPG:\] VALIDSIG $RCLONE_KEY " \
  || { echo "SHA256SUMS is not signed by rclone's release key $RCLONE_KEY" >&2; exit 1; }

say "Checking every download against it"
for t in "${TARGETS[@]}"; do
  f="rclone-$RCLONE_VERSION-$t.zip"
  want="$(grep " $f\$" SHA256SUMS | awk '{print $1}')"
  if command -v sha256sum >/dev/null; then have="$(sha256sum "$f" | awk '{print $1}')"
  else have="$(shasum -a 256 "$f" | awk '{print $1}')"; fi
  [ -n "$want" ] && [ "$want" = "$have" ] || { echo "$f does not match its signed checksum" >&2; rm -f "$f"; exit 1; }
  echo "  $f"
done
say "Done: $CACHE"
