#!/usr/bin/env bash
# Upload what this computer built to the draft release of this version, then
# (with --sign) ask GitHub to sign and publish it. Needs only `gh`, logged in.
#
#   packaging/upload-release.sh              # the packages and .build files of this version in dist/
#   packaging/upload-release.sh --sign       # ... and then sign and publish the release
#
# Run it on every computer that builds (Linux, Mac, Windows), then --sign on the
# last one. The release key is a secret of the repository: see
# .github/workflows/sign-release.yml.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$ROOT/piklin/app.py")"
TAG="v$VERSION"
cd "$ROOT/dist"

files=()
for f in piklin_"$VERSION"_*.deb Piklin-"$VERSION"-*.AppImage Piklin-"$VERSION".dmg Piklin-"$VERSION"-x64.msi; do
  [ -f "$f" ] || continue
  files+=("$f")
  [ -f "$f.build" ] && files+=("$f.build")
done
[ ${#files[@]} -gt 0 ] || { echo "Nothing to upload for $VERSION in dist/" >&2; exit 1; }

gh release view "$TAG" >/dev/null 2>&1 || gh release create "$TAG" --draft --title "Piklin $VERSION" --notes "" --target "$(git -C "$ROOT" rev-parse HEAD)"
gh release upload "$TAG" "${files[@]}" --clobber
echo "Uploaded to $TAG: ${files[*]}"

if [ "${1:-}" = "--sign" ]; then
  gh workflow run sign-release.yml -f tag="$TAG" -f publish=true
  echo "Signing and publishing $TAG on GitHub: gh run watch"
fi
