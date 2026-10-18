#!/usr/bin/env bash
# Build Piklin for Windows 10 and 11 (64-bit): a folder with the program and
# the GTK runtime it needs, and Piklin-<version>-x64.msi.
#
# Run it from the MSYS2 "UCRT64" shell (https://www.msys2.org), after
#
#   pacman -S --needed $(grep -v '^#' packaging/windows/msys2-packages.txt)
#
# and with WiX Toolset 3.14 unpacked in $WIX (default C:\piklin-build\wix).
#
#   packaging/windows/build-windows.sh
#
# Output: dist/windows/Piklin (the program), dist/Piklin-<version>-x64.msi
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
U="${MINGW_PREFIX:-/ucrt64}"
OUT="$ROOT/dist/windows"
APP="$OUT/Piklin"
RT="$APP/runtime"
PYVER="$(python -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$ROOT/piklin/app.py")"
WIX="$(cygpath -u "${WIX:-C:\\piklin-build\\wix}")"
RCLONE_VERSION="$(sed -n 's/^RCLONE_VERSION=//p' "$ROOT/packaging/fetch-rclone.sh")"
say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

# Code signing (optional): Windows trusts - and SmartScreen stops warning about - a program only
# when it is signed with a certificate it knows. Two ways, picked by what is set:
#   PIKLIN_SIGN_PFX=cert.pfx [PIKLIN_SIGN_PASSWORD=...]      a certificate file (osslsigncode)
#   PIKLIN_SIGNTOOL=signtool.exe PIKLIN_SIGN_DLIB=Azure.CodeSigning.Dlib.dll PIKLIN_SIGN_METADATA=metadata.json
#                                                             Azure Artifact Signing (signtool)
# PIKLIN_SIGN_TIMESTAMP picks the time server (a signature outlives the certificate that made it).
# Nothing set: nothing is signed. See trabajoparahacer.md, "Instalador firmado".
SIGNING=no
if [ -n "${PIKLIN_SIGN_PFX:-}" ] || [ -n "${PIKLIN_SIGNTOOL:-}" ]; then SIGNING=yes; fi
sign() {
  [ "$SIGNING" = yes ] || return 0
  local f stamp="${PIKLIN_SIGN_TIMESTAMP:-http://timestamp.digicert.com}"
  for f in "$@"; do
    say "signing $(basename "$f")"
    if [ -n "${PIKLIN_SIGNTOOL:-}" ]; then
      "$PIKLIN_SIGNTOOL" sign //fd SHA256 //tr "${PIKLIN_SIGN_TIMESTAMP:-http://timestamp.acs.microsoft.com}" //td SHA256 \
          //dlib "$PIKLIN_SIGN_DLIB" //dmdf "$PIKLIN_SIGN_METADATA" "$(cygpath -w "$f")" >/dev/null
    else
      osslsigncode sign -pkcs12 "$PIKLIN_SIGN_PFX" -pass "${PIKLIN_SIGN_PASSWORD:-}" -h sha256 \
          -n "Piklin" -i "https://github.com/vezzulab/Piklin" -ts "$stamp" -in "$f" -out "$f.signed" >/dev/null
      mv "$f.signed" "$f"
    fi
  done
}
[ -n "$VERSION" ] || { echo "could not read VERSION from app.py" >&2; exit 1; }
say "Piklin $VERSION for Windows (Python $PYVER)"

rm -rf "$OUT"; mkdir -p "$RT"/{bin,lib,share,etc} "$APP/app" "$APP/data"

# ---------------------------------------------------------------- Python
say "Python and its libraries"
cp "$U/bin/libpython$PYVER.dll" "$RT/bin/"
rsync -a --exclude '__pycache__' --exclude 'test' --exclude 'tests' --exclude 'idlelib' \
  --exclude 'tkinter' --exclude 'turtledemo' --exclude 'ensurepip' --exclude 'site-packages' \
  --exclude 'dist-packages' --exclude 'config-*' --exclude 'Tools' --exclude 'lib2to3' \
  "$U/lib/python$PYVER/" "$RT/lib/python$PYVER/"
SRC_SP="$U/lib/python$PYVER/site-packages"
SP="$RT/lib/python$PYVER/site-packages"; mkdir -p "$SP"
for m in gi cairo numpy PIL av cffi pycparser; do
  [ -e "$SRC_SP/$m" ] && cp -r "$SRC_SP/$m" "$SP/"
done
cp "$SRC_SP/"_cffi_backend*.pyd "$SP/" 2>/dev/null || true
# OpenCV: the small one built by build-opencv.sh (only what Piklin calls: no VTK, Qt, Ogre or extra
# modules - about 120 MB less), else MSYS2's, which drags all of those along.
CV="$HERE/opencv-cache"
if [ -d "$CV/lib/python$PYVER/site-packages/cv2" ]; then
  say "OpenCV (small build)"
  cp -r "$CV/lib/python$PYVER/site-packages/cv2" "$SP/"
  cp "$CV/bin/"libopencv_*.dll "$RT/bin/"
else
  echo "warning: no small OpenCV (run packaging/windows/build-opencv.sh): the installer will be much larger" >&2
  for m in cv2; do [ -e "$SRC_SP/$m" ] && cp -r "$SRC_SP/$m" "$SP/"; done
  cp "$SRC_SP/"cv2*.pyd "$SP/" 2>/dev/null || true
fi
find "$SP" -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
find "$SP" -type d -name 'tests' -prune -exec rm -rf {} + 2>/dev/null || true

# What MSYS2 does not package, built here against its libraries.
EXTRA="$OUT/extra"
python -m pip install --quiet --no-deps --no-build-isolation --target "$EXTRA" pi-heif==1.4.0 rawpy==0.27.1 miniaudio==1.71
cp -r "$EXTRA"/pi_heif "$EXTRA"/rawpy "$EXTRA"/miniaudio.py "$SP/"
cp "$EXTRA"/_miniaudio* "$EXTRA"/_pi_heif* "$SP/"

# ---------------------------------------------------------------- launcher
say "Piklin.exe"
# The window shown while Piklin loads is the banner of the README (this release's
# docs/screenshots/hero.png), carried inside Piklin.exe as a bitmap.
python - "$(cygpath -m "$ROOT/docs/screenshots/hero.png")" "$(cygpath -m "$OUT/splash.bmp")" <<'PY'
import sys
from PIL import Image
Image.open(sys.argv[1]).convert("RGB").resize((1440, 720), Image.LANCZOS).save(sys.argv[2], "BMP")
PY
{ echo "1 ICON \"$(cygpath -m "$HERE/Piklin.ico")\""
  echo "2 BITMAP \"$(cygpath -m "$OUT/splash.bmp")\""; } > "$OUT/icon.rc"
windres "$OUT/icon.rc" -O coff -o "$OUT/icon.o"
gcc -O2 -mwindows -municode -I"$U/include/python$PYVER" "$HERE/launcher.c" "$OUT/icon.o" \
    -L"$U/lib" -lpython$PYVER -o "$RT/bin/Piklin.exe"
strip "$RT/bin/Piklin.exe"
sign "$RT/bin/Piklin.exe"

# What reads phones and cameras through Windows Portable Devices (piklin/wpd.py).
say "piklin-wpd.exe"
gcc -O2 -Wall -municode -DCOBJMACROS "$HERE/piklin-wpd.c" -o "$RT/bin/piklin-wpd.exe"     -lole32 -loleaut32 -luuid
strip "$RT/bin/piklin-wpd.exe"
sign "$RT/bin/piklin-wpd.exe"

# ---------------------------------------------------------------- GTK data
say "GTK data"
cp "$U/bin/"{cjxl.exe,djxl.exe,gspawn-win64-helper.exe,gspawn-win64-helper-console.exe} "$RT/bin/"
# GLib starts the session bus with this: what lets a second Piklin hand over to the first.
cp "$U/bin/gdbus.exe" "$RT/bin/"
cp -r "$U/lib/girepository-1.0" "$RT/lib/"
cp -r "$U/lib/gdk-pixbuf-2.0" "$U/lib/gio" "$RT/lib/"
[ -d "$U/lib/libheif" ] && cp -r "$U/lib/libheif" "$RT/lib/"
mkdir -p "$RT/share/glib-2.0" "$RT/share/icons" "$RT/etc/ssl/certs"
cp -r "$U/share/glib-2.0/schemas" "$RT/share/glib-2.0/"
cp -r "$U/share/icons/Adwaita" "$U/share/icons/hicolor" "$RT/share/icons/"
cp -r "$U/share/mime" "$RT/share/"
cp -r "$U/etc/fonts" "$RT/etc/"
cp "$U/etc/ssl/certs/ca-bundle.crt" "$RT/etc/ssl/certs/" 2>/dev/null \
  || cp "$U/ssl/certs/ca-bundle.crt" "$RT/etc/ssl/certs/"

# The ones Python opens by name through the typelibs, which nothing links to.
for lib in libgtk-4-1 libadwaita-1-0 libshumate-1.0-1 libsecret-1-0 libsoup-3.0-0 \
           libgirepository-2.0-0 librsvg-2-2 libjson-glib-1.0-0 libgraphene-1.0-0; do
  cp "$U/bin/$lib.dll" "$RT/bin/"
done

# Every library those need, and the ones those need in turn.
say "Libraries they depend on"
while :; do
  new=0
  while read -r dll; do
    [ -n "$dll" ] || continue
    [ -f "$RT/bin/$(basename "$dll")" ] && continue
    cp "$dll" "$RT/bin/"; new=1
  done < <(find "$RT" \( -name '*.dll' -o -name '*.pyd' -o -name '*.exe' \) -print0 \
           | xargs -0 ntldd -R 2>/dev/null | sed -n 's/.*=> \(.*\.dll\) (.*/\1/p' \
           | grep -i "ucrt64" | sort -u | while read -r p; do cygpath -u "$p"; done)
  [ "$new" = 1 ] || break
done

# ---------------------------------------------------------------- Piklin
say "Piklin's own files"
rsync -a --exclude '__pycache__' --exclude 'style-macos.css' "$ROOT/piklin" "$APP/app/"
rsync -a "$ROOT/data/" "$APP/data/"
cp "$ROOT/LICENSE" "$ROOT/NOTICE.md" "$ROOT/CHANGELOG.md" "$APP/"
# The key that checks an update: only Piklin's own releases, signed by Vezzu Studio,
# are accepted (piklin/winupdate.py). Under Program Files, so only an administrator
# can change it. $RELEASE_KEY_PEM replaces it for a test of the updater.
cp "${RELEASE_KEY_PEM:-$HERE/../deb/release-key.pem}" "$APP/release-key.pem"
# Which build this is: a fix published again under the same version is an update.
BUILD_ID="$(git -C "$ROOT" rev-parse --short=12 HEAD 2>/dev/null || echo local)-$(date -u +%Y%m%d%H%M%S)"
printf '%s\n' "$BUILD_ID" > "$APP/app/piklin/BUILD_ID"
# Bytecode that is never checked against the source file. Windows Installer stores
# file times to the nearest two seconds, which made every .pyc look out of date and
# Python compile all of Piklin again at each start (1.3 s, more on a cold disk).
python -m compileall -q -j 0 --invalidation-mode unchecked-hash "$APP/app" "$RT/lib/python$PYVER" >/dev/null || true

# ---------------------------------------------------------------- rclone
RZIP="$ROOT/packaging/rclone-cache/rclone-$RCLONE_VERSION-windows-amd64.zip"
if [ ! -f "$RZIP" ]; then
  mkdir -p "$(dirname "$RZIP")"
  curl -fsSL -o "$RZIP" "https://github.com/rclone/rclone/releases/download/$RCLONE_VERSION/rclone-$RCLONE_VERSION-windows-amd64.zip"
fi
say "rclone $RCLONE_VERSION"
python - "$(cygpath -m "$RZIP")" "$(cygpath -m "$APP")" <<'PY'
import sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
n = next(n for n in z.namelist() if n.endswith("/rclone.exe"))
open(sys.argv[2] + "/rclone.exe", "wb").write(z.read(n))
PY

# ---------------------------------------------------------------- check
say "Checking that it starts"
# From another folder and without MSYS2 on the PATH, as a person's computer has it.
( cd "$OUT" && env PATH="/c/Windows/System32" USERPROFILE="$(cygpath -w ~)" APPDATA="$(cygpath -w ~)\AppData\Roaming" LOCALAPPDATA="$(cygpath -w ~)\AppData\Local" "$RT/bin/Piklin.exe" -c "
import gi
gi.require_version('Gtk','4.0'); gi.require_version('Adw','1'); gi.require_version('Shumate','1.0')
from gi.repository import Gtk, Adw, Shumate
import numpy, cv2, av, PIL, pi_heif, rawpy, miniaudio, cffi
import piklin.app, piklin.ui.window, piklin.people
assert piklin.people.available(), 'the face models are not found where Piklin looks for them'
open(r'$(cygpath -m "$OUT")/check.txt','w').write('runtime ok')" ) || true
[ "$(cat "$OUT/check.txt" 2>/dev/null)" = "runtime ok" ] || { echo "the packaged runtime does not start" >&2; exit 1; }
rm -rf "$EXTRA" "$OUT/check.txt" "$OUT/icon."* "$OUT/splash.bmp"

# ---------------------------------------------------------------- MSI
say "Piklin-$VERSION-x64.msi"
WORK="$OUT/wix"; mkdir -p "$WORK"
python - "$(cygpath -m "$ROOT/LICENSE")" "$(cygpath -m "$WORK/license.rtf")" <<'PY'
import sys
t = open(sys.argv[1], encoding="utf-8").read()
out = []
for c in t:
    if c in "\\{}":
        out.append("\\" + c)
    elif c == "\n":
        out.append("\\par\n")
    elif ord(c) < 128:
        out.append(c)
    else:
        out.append("\\u%d?" % (ord(c) if ord(c) < 32768 else ord(c) - 65536))
open(sys.argv[2], "w", encoding="ascii").write("{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0 Segoe UI;}}\\fs20 " + "".join(out) + "}")
PY
cp "$HERE/Piklin.ico" "$HERE/piklin.wxs" "$WORK/"
sed -i "s/@VERSION@/$VERSION/g" "$WORK/piklin.wxs"
"$WIX/heat.exe" dir "$(cygpath -w "$APP")" -nologo -cg PiklinFiles -dr INSTALLFOLDER -gg -g1 -sfrag -srd \
    -var var.AppDir -platform x64 -out "$(cygpath -w "$WORK/files.wxs")" >/dev/null
cd "$WORK"
"$WIX/candle.exe" -nologo -arch x64 -dAppDir="$(cygpath -w "$APP")" -ext WixUIExtension piklin.wxs files.wxs
"$WIX/light.exe" -nologo -sval -ext WixUIExtension -cultures:en-us \
    -out "$(cygpath -w "$ROOT/dist/Piklin-$VERSION-x64.msi")" piklin.wixobj files.wixobj
# The installer last, so the signature covers what is shipped (sign-release.sh comes after this).
sign "$ROOT/dist/Piklin-$VERSION-x64.msi"
printf '%s\n' "$BUILD_ID" > "$ROOT/dist/Piklin-$VERSION-x64.msi.build"
say "Done: dist/Piklin-$VERSION-x64.msi (and .build; sign both with packaging/sign-release.sh)"
