#!/usr/bin/env bash
# Build the Piklin .deb.
#
# Targets Ubuntu 24.04+, Linux Mint 22+, Debian 13+ and their derivatives
# (Pop!_OS, Zorin, elementary...). The package uses the system's Python,
# GTK4 and libadwaita, and carries its own copies of the compiled Python
# libraries - one set for each Python version those systems ship, picked
# at launch by /usr/bin/piklin.
#
#   MAINTAINER="Your Name <you@example.com>" ./packaging/build-deb.sh
#
# Output: dist/piklin_<version>_amd64.deb
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
STAGE="$HERE/deb-root"
WHEELS="$HERE/wheel-cache"
ARCH=amd64
PKG=piklin
VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$ROOT/pikalicious/app.py")"
MAINTAINER="${MAINTAINER:-Vezzu Studio <maintainer@example.com>}"
# Ubuntu 24.04 / Mint 22: 3.12. Debian 13, Ubuntu 25.x: 3.13. Newer: 3.14.
PYVERS="${PYVERS:-3.12 3.13 3.14}"

# Pinned: these exact versions are the ones the test suite ran against.
# miniaudio plays video sound through PipeWire, PulseAudio or ALSA, found at
# run time - no audio package needed.
PER_PYTHON=(numpy==2.5.3 pillow==12.3.0 pi-heif==1.4.0 rawpy==0.27.1 miniaudio==1.71)
# OpenCV's wheel uses the stable ABI (abi3): one copy serves every Python.
ABI3=(opencv-python-headless==5.0.0.93)

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
[ -n "$VERSION" ] || { echo "could not read VERSION from app.py"; exit 1; }
case "$MAINTAINER" in *example.com*)
  echo "warning: MAINTAINER is the placeholder '$MAINTAINER'" >&2 ;; esac

say "Building $PKG $VERSION ($ARCH) for Python $PYVERS"
rm -rf "$STAGE"
SHARE="$STAGE/usr/share/$PKG"
LIB="$STAGE/usr/lib/$PKG"
DOC="$STAGE/usr/share/doc/$PKG"
mkdir -p "$STAGE/DEBIAN" "$STAGE/usr/bin" "$SHARE/data" "$LIB/common" "$DOC" \
         "$STAGE/usr/share/applications" "$STAGE/usr/share/metainfo"

# ---------------------------------------------------------------- app code
say "Copying application"
cp -r "$ROOT/pikalicious" "$SHARE/"
find "$SHARE" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
for d in fonts icons models; do cp -r "$ROOT/data/$d" "$SHARE/data/"; done
install -m 755 "$HERE/deb/piklin" "$STAGE/usr/bin/piklin"

# ------------------------------------------------------------ python libs
# pip --platform/--python-version fetches the wheels for a Python other
# than the one running this script, so one machine builds all of them.
pipget() {   # pipget <python-version> <target> <requirements...>
  local v="$1" target="$2"; shift 2
  python3 -m pip install --quiet --no-deps --only-binary=:all: \
      --implementation cp --python-version "$v" \
      --platform manylinux_2_28_x86_64 --platform manylinux_2_27_x86_64 \
      --cache-dir "$WHEELS" --target "$target" "$@"
}
for v in $PYVERS; do
  say "Python libraries for $v"
  pipget "$v" "$LIB/python$v" "${PER_PYTHON[@]}"
done
say "OpenCV (shared by every Python version)"
pipget "${PYVERS%% *}" "$LIB/common" "${ABI3[@]}"

# Video: PyAV built by packaging/build-media.sh against an FFmpeg with no
# GPL code. The PyPI wheel carries x264/x265 (GPL) and must never be used.
MEDIA_WHEEL="$(ls "$HERE"/media-cache/wheels/av-*.whl 2>/dev/null | head -1)"
[ -n "$MEDIA_WHEEL" ] || { echo "Run packaging/build-media.sh first" >&2; exit 1; }
case "$MEDIA_WHEEL" in
  *abi3*) say "PyAV with Piklin's own FFmpeg (LGPL)"
          python3 -m pip install --quiet --no-deps --target "$LIB/common" "$MEDIA_WHEEL" ;;
  *)      echo "PyAV wheel is not abi3 (one per Python version needed): $MEDIA_WHEEL" >&2; exit 1 ;;
esac
if ls "$LIB"/common/av.libs 2>/dev/null | grep -qiE "x264|x265|postproc|fdk"; then
  echo "GPL or non-free codec library found in the media stack - refusing to package" >&2
  exit 1
fi

# Test suites are a large share of numpy and never run on a user's machine.
# ("tests"/"test" only: numpy.testing is a real module and must stay.)
find "$LIB" -type d \( -name tests -o -name test -o -name __pycache__ \) \
     -exec rm -rf {} + 2>/dev/null || true
rm -rf "$LIB"/*/bin

# The native libraries inside the wheels (OpenBLAS, libjpeg, libheif...)
# are byte-identical across the Python versions; only the extension
# modules differ. Keep one real copy and symlink the rest - this takes
# about a hundred megabytes off the installed size.
say "Sharing identical libraries between Python versions"
FIRST="$LIB/python${PYVERS%% *}"
for v in ${PYVERS#* }; do
  [ "$v" = "${PYVERS%% *}" ] && continue
  (cd "$LIB/python$v" && find . -type f -path '*.libs/*') | while read -r f; do
    if [ -f "$FIRST/$f" ] && cmp -s "$FIRST/$f" "$LIB/python$v/$f"; then
      ln -sf "../../python${PYVERS%% *}/${f#./}" "$LIB/python$v/$f"
    fi
  done
done

# ------------------------------------------------------------- desktop bits
say "Desktop entry, icons and metadata"
install -m 644 "$ROOT/data/piklin.desktop" "$STAGE/usr/share/applications/"
for s in 16 24 32 48 64 128 256 512; do
  install -D -m 644 "$ROOT/data/icons/piklin-$s.png" \
      "$STAGE/usr/share/icons/hicolor/${s}x${s}/apps/piklin.png"
done

cat > "$STAGE/usr/share/metainfo/com.envy.Piklin.metainfo.xml" <<XML
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop-application">
  <id>com.envy.Piklin</id>
  <name>Piklin</name>
  <summary>Browse, organise and edit your photographs</summary>
  <metadata_license>CC0-1.0</metadata_license>
  <project_license>LicenseRef-proprietary</project_license>
  <description>
    <p>A photo library and non-destructive editor. Your original files are
    never modified: adjustments are saved beside them as readable files, and
    the catalog can always be rebuilt from your photos.</p>
    <p>Import straight from a connected camera, organise albums inside
    folders, edit with a full set of tools and filters, and export
    compressed copies that keep their quality.</p>
  </description>
  <developer id="studio.vezzu"><name>Vezzu Studio</name></developer>
  <url type="homepage">https://vezzu.studio</url>
  <launchable type="desktop-id">piklin.desktop</launchable>
  <categories>
    <category>Graphics</category>
    <category>Photography</category>
  </categories>
  <content_rating type="oars-1.1"/>
  <releases>
    <release version="$VERSION" date="$(date +%F)"/>
  </releases>
</component>
XML

# ------------------------------------------------------------------ licenses
# Every bundled library's own licence travels with the package.
say "Collecting third-party licences"
mkdir -p "$DOC/third-party"
for info in "$LIB"/python"${PYVERS%% *}"/*.dist-info "$LIB"/common/*.dist-info; do
  name="$(basename "$info" .dist-info)"
  mkdir -p "$DOC/third-party/$name"
  find "$info" -type f \( -iname 'LICENSE*' -o -iname 'COPYING*' -o -iname 'NOTICE*' \) \
       -exec cp {} "$DOC/third-party/$name/" \;
done
cp "$LIB"/common/cv2/LICENSE*.txt "$DOC/third-party/"opencv*/ 2>/dev/null || true
cp "$ROOT"/data/fonts/Inter/*.txt "$DOC/third-party/" 2>/dev/null || true
mkdir -p "$DOC/third-party/ffmpeg"
cp "$HERE/media-cache/FFMPEG-LICENSE.txt" "$DOC/third-party/ffmpeg/COPYING.LGPLv2.1"
cp "$HERE/media-cache/ffmpeg-configure.log" "$DOC/third-party/ffmpeg/configure.log" 2>/dev/null || true
cat > "$DOC/third-party/ffmpeg/SOURCE.txt" <<SRC
Piklin plays and exports video with FFmpeg $(sed -n 's/^FFMPEG=//p' "$HERE/build-media.sh"),
licensed under the GNU Lesser General Public License version 2.1 or later,
built with OpenH264, libvpx, Opus and dav1d (all BSD licensed) and without
any GPL or non-free component. The exact configuration is in configure.log.

The FFmpeg libraries are separate shared libraries in /usr/lib/piklin and
may be replaced with your own build. The complete corresponding source code
is available at https://ffmpeg.org/releases/ and, on request, from the
publisher of Piklin for three years from the date you received this copy.
SRC

cat > "$DOC/copyright" <<COPY
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: Piklin

Files: *
Copyright: $(date +%Y) Vezzu Studio
License: proprietary
 All rights reserved. https://vezzu.studio

Files: usr/lib/piklin/*
Comment: Bundled Python libraries. Their licences are in third-party/.
 numpy (BSD-3-Clause), Pillow (MIT-CMU), OpenCV (Apache-2.0; its bundled
 FFmpeg is LGPL-2.1), PyAV (BSD-3-Clause) with FFmpeg (LGPL-2.1+) built with
 OpenH264, libvpx, Opus and dav1d (BSD), miniaudio (MIT-0 or public domain),
 rawpy (MIT; LibRaw LGPL-2.1 or CDDL-1.0), pi-heif (BSD-3-Clause;
 libheif and libde265 LGPL-3.0), plus the libraries those wheels carry.

Files: usr/share/piklin/data/fonts/*
License: OFL-1.1
Comment: Inter typeface, SIL Open Font License.

Files: usr/share/piklin/data/models/*
License: MIT
Comment: YuNet face detector, from the OpenCV model zoo.
COPY

printf '%s (%s) stable; urgency=medium\n\n  * Release %s.\n\n -- %s  %s\n' \
  "$PKG" "$VERSION" "$VERSION" "$MAINTAINER" "$(date -R)" \
  | gzip -9n > "$DOC/changelog.gz"

# ------------------------------------------------------------------ control
install -m 755 "$HERE/deb/postinst" "$STAGE/DEBIAN/postinst"
install -m 755 "$HERE/deb/prerm"    "$STAGE/DEBIAN/prerm"

cat > "$STAGE/DEBIAN/control" <<CTRL
Package: $PKG
Version: $VERSION
Section: graphics
Priority: optional
Architecture: $ARCH
Maintainer: $MAINTAINER
Installed-Size: $(du -sk --exclude=DEBIAN "$STAGE" | cut -f1)
Depends: python3 (>= 3.12), python3 (<< 3.15), python3-gi (>= 3.42), python3-gi-cairo,
 gir1.2-glib-2.0, gir1.2-gtk-4.0 (>= 4.14), gir1.2-adw-1 (>= 1.5),
 gir1.2-secret-1
Recommends: gvfs, gvfs-backends, fonts-dejavu-core
Replaces: pikalicious
Conflicts: pikalicious
Suggests: rclone
Description: Photo library and editor
 Piklin browses, organises and edits your photographs.
 .
 Originals are never modified: edits are stored beside each photo and the
 library can be rebuilt from your files at any time. Import directly from a
 connected camera, group albums into folders, edit with a complete set of
 tools and filters, and export compressed copies without visible loss.
CTRL

# ------------------------------------------------------------------ build
# Files owned by root and not world-writable, whatever the umask here was.
find "$STAGE" -type d -exec chmod 755 {} +
find "$STAGE" -type f ! -path '*/DEBIAN/*' ! -path '*/usr/bin/*' -exec chmod 644 {} +
find "$LIB" -name '*.so*' -exec chmod 644 {} +

mkdir -p "$ROOT/dist"
OUT="$ROOT/dist/${PKG}_${VERSION}_${ARCH}.deb"
say "Packing $OUT"
dpkg-deb --root-owner-group -Zxz --build "$STAGE" "$OUT" >/dev/null
say "Done: $OUT ($(du -h "$OUT" | cut -f1), installs to $(du -sh --exclude=DEBIAN "$STAGE" | cut -f1))"
