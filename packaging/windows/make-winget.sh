#!/usr/bin/env bash
# Write the description files winget needs for a Piklin release, from the .msi that is
# going to be published, and check them with winget itself.
#
#   packaging/windows/make-winget.sh dist/Piklin-2.0.6-x64.msi
#
# Output: dist/winget/manifests/v/VezzuStudio/Piklin/<version>/ (three YAML files).
# Why winget: it downloads the installer itself, so Windows does not mark the file as
# "downloaded from the internet" and SmartScreen does not warn - for free, with no certificate.
# To publish, copy that folder into a fork of https://github.com/microsoft/winget-pkgs under
# manifests/v/VezzuStudio/Piklin/<version>/ and open a pull request. Run this only AFTER the
# release is public, with the very .msi that was uploaded: the link and the checksum must match it.
#
# Run from an MSYS2 UCRT64 shell (needs python); `winget validate` is used when winget is there.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
MSI="${1:?usage: make-winget.sh path/to/Piklin-VERSION-x64.msi}"
[ -f "$MSI" ] || { echo "No such installer: $MSI" >&2; exit 1; }
MSI="$(cd "$(dirname "$MSI")" && pwd)/$(basename "$MSI")"

ID=VezzuStudio.Piklin
REPO=vezzulab/Piklin
SHA="$(sha256sum --text "$MSI" | cut -d' ' -f1 | tr 'a-f' 'A-F')"
read -r VERSION CODE UPGRADE < <(PYTHONPATH="$ROOT" python - "$(cygpath -m "$MSI")" <<'PY'
import sys
from pathlib import Path
from piklin.winupdate import msi_properties
p = msi_properties(Path(sys.argv[1]))
print(p["ProductVersion"], p["ProductCode"], p["UpgradeCode"])
PY
)
OUT="$ROOT/dist/winget/manifests/v/VezzuStudio/Piklin/$VERSION"
rm -rf "$OUT"; mkdir -p "$OUT"
URL="https://github.com/$REPO/releases/download/v$VERSION/$(basename "$MSI")"

cat > "$OUT/$ID.yaml" <<YAML
# yaml-language-server: \$schema=https://aka.ms/winget-manifest.version.1.6.0.schema.json
PackageIdentifier: $ID
PackageVersion: $VERSION
DefaultLocale: en-US
ManifestType: version
ManifestVersion: 1.6.0
YAML

cat > "$OUT/$ID.installer.yaml" <<YAML
# yaml-language-server: \$schema=https://aka.ms/winget-manifest.installer.1.6.0.schema.json
PackageIdentifier: $ID
PackageVersion: $VERSION
MinimumOSVersion: 10.0.0.0
InstallerType: wix
Scope: machine
UpgradeBehavior: install
Installers:
- Architecture: x64
  InstallerUrl: $URL
  InstallerSha256: $SHA
  ProductCode: '$CODE'
  AppsAndFeaturesEntries:
  - DisplayName: Piklin
    Publisher: Vezzu Studio
    DisplayVersion: $VERSION
    ProductCode: '$CODE'
    UpgradeCode: '$UPGRADE'
ManifestType: installer
ManifestVersion: 1.6.0
YAML

cat > "$OUT/$ID.locale.en-US.yaml" <<YAML
# yaml-language-server: \$schema=https://aka.ms/winget-manifest.defaultLocale.1.6.0.schema.json
PackageIdentifier: $ID
PackageVersion: $VERSION
PackageLocale: en-US
Publisher: Vezzu Studio
PublisherUrl: https://vezzu.studio
PackageName: Piklin
PackageUrl: https://github.com/$REPO
License: Piklin License 1.0
LicenseUrl: https://github.com/$REPO/blob/main/LICENSE
Copyright: Copyright (c) 2026 Vezzu Studio
ShortDescription: A free and private photo and video library. Organize, edit and share your memories; they never leave your computer.
Description: Piklin organizes your photos and videos by years, months and days, albums, places on a map and people, edits them without ever changing the originals, and backs them up to your NAS or the cloud. Everything stays on your computer.
Moniker: piklin
Tags:
- photos
- videos
- photo-library
- photo-editor
- gallery
- privacy
ReleaseNotesUrl: https://github.com/$REPO/releases/tag/v$VERSION
ManifestType: defaultLocale
ManifestVersion: 1.6.0
YAML

echo "Written: $OUT"
echo "  version $VERSION | product code $CODE | sha256 $SHA"
echo "  installer link: $URL"
if command -v winget >/dev/null 2>&1; then
  winget validate --manifest "$(cygpath -w "$OUT")"
else
  echo "(winget not found: the files were not validated)"
fi
