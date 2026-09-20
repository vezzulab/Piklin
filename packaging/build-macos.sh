#!/usr/bin/env bash
# Build Piklin.app and its disk image: one app for Apple Silicon and Intel
# Macs running macOS 11 Big Sur or newer.
#
# The app carries two complete native runtimes - GTK 4, libadwaita, FFmpeg
# and the compiled Python libraries, built once for arm64 and once for
# x86_64 - and a universal launcher that starts the one for the Mac it is on,
# so neither kind of Mac runs Piklin translated. Python itself (python.org's
# universal framework) and Piklin's own code are shared by both.
#
#   packaging/build-macos.sh                    # both architectures and the .dmg
#   ARCHS=arm64 packaging/build-macos.sh        # one architecture, for testing
#   SIGN_ID="Developer ID Application: ..." packaging/build-macos.sh
#
# Without SIGN_ID the app is signed ad hoc: it runs, but a Mac that downloads
# it asks the person to allow it in System Settings > Privacy & Security.
#
# Needs: see packaging/macos/build-stack.sh.
# Output: dist/Piklin.app, dist/Piklin-<version>.dmg
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
MAC="$HERE/macos"
CACHE="$HERE/macos-cache"
SRC="$CACHE/src"
ARCHS="${ARCHS:-arm64 x86_64}"
MIN_MACOS="${MIN_MACOS:-11.0}"
SIGN_ID="${SIGN_ID:--}"
PYVER=3.14
PYFULL=3.14.6
VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$ROOT/piklin/app.py")"
HOST_PY=/opt/homebrew/opt/python@3.14/bin/python3.14
APP="$ROOT/dist/Piklin.app"
C="$APP/Contents"

# The same pinned libraries the .deb ships, plus cffi's runtime: miniaudio
# plays sound through it, and on Linux the system provides it
# (python3-cffi-backend) while a Mac has none.
PINNED=($(sed -n 's/^PER_PYTHON=(\(.*\))$/\1/p; s/^ABI3=(\(.*\))$/\1/p' "$HERE/build-deb.sh") cffi==2.1.1)
# Cameras and phones reach a Mac through Image Capture (gvfs does this on
# Linux); PyObjC is how Python talks to it.
PINNED+=(pyobjc-core==12.2.2 pyobjc-framework-Cocoa==12.2.2 pyobjc-framework-ImageCaptureCore==12.2.2)
# The certificates HTTPS trusts (updates, WebDAV backups): python.org's
# Python has none of its own on a Mac without its installer; boot.py points
# SSL_CERT_FILE at these.
PINNED+=(certifi==2026.7.22)

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
[ -n "$VERSION" ] || { echo "could not read VERSION from app.py" >&2; exit 1; }
[ "${#PINNED[@]}" -ge 5 ] || { echo "could not read the pinned libraries from build-deb.sh" >&2; exit 1; }

# ------------------------------------------------------------ native parts
for a in $ARCHS; do
  "$MAC/build-stack.sh" "$a"
  "$MAC/build-media.sh" "$a"
done

say "Piklin $VERSION for $ARCHS"
rm -rf "$APP"
mkdir -p "$C/MacOS" "$C/Frameworks" "$C/Resources/app/data"

# ------------------------------------------------------------------ Python
say "Python $PYFULL (python.org, universal)"
PKG="$SRC/python-$PYFULL-macos11.pkg"
[ -s "$PKG" ] || curl -fL --retry 3 -o "$PKG" "https://www.python.org/ftp/python/$PYFULL/python-$PYFULL-macos11.pkg"
EXP="$CACHE/python-$PYFULL"
if [ ! -d "$EXP/Python_Framework.pkg/Payload/Versions/$PYVER" ]; then
  rm -rf "$EXP"
  pkgutil --expand-full "$PKG" "$EXP"
fi
FW="$C/Frameworks/Python.framework"
PV="$FW/Versions/$PYVER"
mkdir -p "$FW/Versions"
cp -R "$EXP/Python_Framework.pkg/Payload/Versions/$PYVER" "$FW/Versions/"
ln -s "$PYVER" "$FW/Versions/Current"
ln -s Versions/Current/Python "$FW/Python"
ln -s Versions/Current/Resources "$FW/Resources"
STDLIB="$PV/lib/python$PYVER"
# What a photo app never runs: the test suite, Tk, IDLE, pip's bootstrap.
rm -rf "$STDLIB"/{test,idlelib,tkinter,turtledemo,ensurepip,__phello__} "$STDLIB"/site-packages/* \
       "$STDLIB"/config-* "$STDLIB"/lib-dynload/_tkinter* "$PV"/lib/{libtcl*,libtk*,tcl*,tk*,itcl*,pkgconfig} \
       "$PV/share" "$PV/Resources/Python.app" "$PV/bin" "$PV/Frameworks"

# ------------------------------------------------------------- launcher
say "Universal launcher"
lipo_archs=(); for a in $ARCHS; do lipo_archs+=(-arch "$a"); done
clang "${lipo_archs[@]}" -mmacosx-version-min="$MIN_MACOS" -O2 -Wall \
    -I"$PV/include/python$PYVER" "$MAC/launcher.c" "$PV/Python" \
    -Wl,-headerpad_max_install_names -o "$C/MacOS/Piklin"
rm -rf "$PV/include" "$PV/Headers" "$FW/Headers"
cp "$MAC/boot.py" "$C/Resources/boot.py"
# The public half of the release key: updates are installed only when signed with it.
cp "$HERE/deb/release-key.pem" "$C/Resources/release-key.pem"

# -------------------------------------------------------------- runtimes
platforms() {   # pip --platform flags for a Mac of this architecture, newest first
  # Only wheels that run on the oldest macOS Piklin supports: pip otherwise
  # takes the newest it may, and NumPy's for macOS 14 refused to start on 13.
  local v min="${MIN_MACOS%%.*}" tags
  if [ "$1" = arm64 ]; then tags="14_0 13_0 12_0 11_0"; else tags="14_0 13_0 12_0 11_0 10_16 10_15 10_13 10_9"; fi
  for v in $tags; do
    [ "${v%%_*}" -gt "$min" ] && continue
    printf -- '--platform macosx_%s_%s ' "$v" "$1"
  done
  # Wheels built for both processors in one file (PyObjC).
  for v in 11_0 10_15 10_13 10_9; do
    [ "${v%%_*}" -gt "$min" ] && continue
    printf -- '--platform macosx_%s_universal2 ' "$v"
  done
}

for a in $ARCHS; do
  say "Runtime for $a"
  P="$CACHE/$a/prefix"
  R="$C/Resources/runtime-$a"
  mkdir -p "$R/lib" "$R/share/glib-2.0/schemas" "$R/site-packages" "$R/etc/fonts"

  cp -a "$P"/lib/*.dylib "$R/lib/"
  cp -a "$P/lib/girepository-1.0" "$R/lib/"
  # GLib's typelibs name their libraries by full path in the build prefix
  # (the others by file name only), which exists on no other Mac. The names
  # are NUL-terminated strings: the folder is cut out in place and the rest
  # padded with NULs, so the file keeps its size and every offset in it.
  "$HOST_PY" - "$P/lib/" "$R"/lib/girepository-1.0/*.typelib <<'PY'
import sys
prefix = sys.argv[1].encode()
for name in sys.argv[2:]:
    data = bytearray(open(name, "rb").read())
    changed = False
    at = data.find(prefix)
    while at != -1:
        start = data.rfind(b"\0", 0, at) + 1
        end = data.index(b"\0", at)
        old = bytes(data[start:end])
        new = old.replace(prefix, b"")
        data[start:end] = new + b"\0" * (len(old) - len(new))
        changed = True
        at = data.find(prefix, start)
    if changed:
        open(name, "wb").write(data)
PY
  if strings "$R"/lib/girepository-1.0/*.typelib | grep -q "$CACHE"; then
    echo "a typelib still names the build folder - refusing to package" >&2; exit 1
  fi
  # The TLS backend libsoup needs to download the map's tiles over https.
  mkdir -p "$R/lib/gio/modules"
  cp -a "$P"/lib/gio/modules/*.so "$R/lib/gio/modules/"
  # Image loaders, and their list with the location left for boot.py to fill in.
  LOADERS="lib/gdk-pixbuf-2.0/2.10.0"
  mkdir -p "$R/$LOADERS"
  cp -a "$P/$LOADERS/loaders" "$R/$LOADERS/"
  run_arch=(); [ "$a" = x86_64 ] && run_arch=(arch -x86_64)
  GDK_PIXBUF_MODULEDIR="$P/$LOADERS/loaders" "${run_arch[@]}" "$P/bin/gdk-pixbuf-query-loaders" \
    | sed "s#$P#@RUNTIME@#g" > "$R/$LOADERS/loaders.cache.in"

  cp -a "$P/share/icons" "$R/share/"
  cp "$P"/share/glib-2.0/schemas/*.xml "$R/share/glib-2.0/schemas/"
  glib-compile-schemas "$R/share/glib-2.0/schemas"

  # Fonts: the Mac's own, plus the Inter that Piklin registers at start.
  cp -RL "$P/etc/fonts/conf.d" "$R/etc/fonts/"
  cat > "$R/etc/fonts/fonts.conf" <<'CONF'
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig>
  <dir>/System/Library/Fonts</dir>
  <dir>/Library/Fonts</dir>
  <dir>~/Library/Fonts</dir>
  <!-- Contents/Resources/app/data/fonts, from etc/fonts in this runtime: the
       Inter Piklin draws its interface in, found by fontconfig itself so a
       Mac full of installed fonts cannot stand in for it. -->
  <dir prefix="relative">../../../app/data/fonts</dir>
  <cachedir>~/Library/Caches/Piklin/fontconfig</cachedir>
  <include ignore_missing="yes">conf.d</include>
</fontconfig>
CONF

  plat=($(platforms "$a"))
  pins=("${PINNED[@]}")
  if ls "$CACHE/$a"/wheels/opencv_python_headless-*.whl >/dev/null 2>&1; then
    # Built by build-stack.sh (without FFmpeg): never the PyPI wheel.
    pins=($(printf '%s\n' "${PINNED[@]}" | grep -v '^opencv-python-headless=='))
  fi
  "$HOST_PY" -m pip install --quiet --no-deps --only-binary=:all: --implementation cp \
      --python-version "$PYVER" "${plat[@]}" --cache-dir "$CACHE/pip" \
      --find-links "$CACHE/$a/wheels" \
      --target "$R/site-packages" "${pins[@]}" \
      "$CACHE/$a"/wheels/*.whl "$CACHE/$a"/media-wheels/av-*.whl
  find "$R/site-packages" -type d \( -name tests -o -name test -o -name __pycache__ \) \
       -exec rm -rf {} + 2>/dev/null || true
  rm -rf "$R/site-packages/bin"
  if find "$R/site-packages" -iname '*x264*' -o -iname '*x265*' | grep -q .; then
    echo "GPL codec library found in the $a runtime - refusing to package" >&2; exit 1
  fi

  # Cloud backups: the rclone for this processor, fetched and checked by
  # packaging/fetch-rclone.sh, so they work with nothing else to install.
  RCLONE_VERSION="$(sed -n 's/^RCLONE_VERSION=//p' "$HERE/fetch-rclone.sh")"
  RCLONE_ZIP="$HERE/rclone-cache/rclone-$RCLONE_VERSION-osx-$([ "$a" = x86_64 ] && echo amd64 || echo arm64).zip"
  [ -f "$RCLONE_ZIP" ] || { echo "Run packaging/fetch-rclone.sh first ($RCLONE_ZIP is missing)" >&2; exit 1; }
  mkdir -p "$R/bin"
  "$HOST_PY" - "$RCLONE_ZIP" "$R/bin/rclone" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as z:
    name = next(n for n in z.namelist() if n.endswith("/rclone"))
    open(sys.argv[2], "wb").write(z.read(name))
PY
  chmod 755 "$R/bin/rclone"
done

# ------------------------------------------------------------ Piklin itself
say "Application"
cp -R "$ROOT/piklin" "$C/Resources/app/"
for d in fonts icons models; do cp -R "$ROOT/data/$d" "$C/Resources/app/data/"; done
BUILD_ID="$(git -C "$ROOT" rev-parse --short=12 HEAD 2>/dev/null || echo local)-$(date -u +%Y%m%d%H%M%S)"
printf '%s\n' "$BUILD_ID" > "$C/Resources/app/piklin/BUILD_ID"
find "$C/Resources" "$PV/lib" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
# Compiled once here: a signed app cannot write its own bytecode later.
"$HOST_PY" -m compileall -q -j0 "$STDLIB" "$C/Resources/app" "$C/Resources"/runtime-*/site-packages >/dev/null || true

# -------------------------------------------------------------- icon, plist
say "Icon and Info.plist"
ICONSET="$CACHE/Piklin.iconset"
rm -rf "$ICONSET" && mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  cp "$ROOT/data/icons/piklin-$s.png" "$ICONSET/icon_${s}x${s}.png"
  d=$((s * 2)); [ -f "$ROOT/data/icons/piklin-$d.png" ] && cp "$ROOT/data/icons/piklin-$d.png" "$ICONSET/icon_${s}x${s}@2x.png"
done
iconutil -c icns "$ICONSET" -o "$C/Resources/Piklin.icns"

cat > "$C/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>Piklin</string>
  <key>CFBundleIdentifier</key><string>com.envy.Piklin</string>
  <key>CFBundleName</key><string>Piklin</string>
  <key>CFBundleDisplayName</key><string>Piklin</string>
  <key>CFBundleIconFile</key><string>Piklin</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$BUILD_ID</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>LSMinimumSystemVersion</key><string>$MIN_MACOS</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.photography</string>
  <key>LSArchitecturePriority</key><array><string>arm64</string><string>x86_64</string></array>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSSupportsAutomaticGraphicsSwitching</key><true/>
  <key>NSHumanReadableCopyright</key><string>© $(date +%Y) Vezzu Studio</string>
  <key>NSRemovableVolumesUsageDescription</key><string>Piklin shows the photos on the cameras, cards and USB drives you connect.</string>
  <key>NSNetworkVolumesUsageDescription</key><string>Piklin can back up your library to a NAS or server.</string>
  <key>NSDesktopFolderUsageDescription</key><string>Piklin opens the photos you choose from your Desktop.</string>
  <key>NSDocumentsFolderUsageDescription</key><string>Piklin opens the photos you choose from your Documents.</string>
  <key>NSDownloadsFolderUsageDescription</key><string>Piklin opens the photos you choose from your Downloads.</string>
</dict>
</plist>
PLIST
printf 'APPL????' > "$C/PkgInfo"

# ------------------------------------------------------------ licences
say "Third-party licences"
DOC="$C/Resources/third-party"
mkdir -p "$DOC"
cp "$ROOT/NOTICE.md" "$ROOT/LICENSE" "$DOC/"
for a in $ARCHS; do
  for info in "$C/Resources/runtime-$a"/site-packages/*.dist-info; do
    name="$(basename "$info" .dist-info)"
    mkdir -p "$DOC/$name"
    find "$info" -type f \( -iname 'LICENSE*' -o -iname 'COPYING*' -o -iname 'NOTICE*' \) -exec cp {} "$DOC/$name/" \;
  done
  mkdir -p "$DOC/ffmpeg"
  cp "$CACHE/$a/FFMPEG-LICENSE.txt" "$DOC/ffmpeg/COPYING.LGPLv2.1"
  mkdir -p "$DOC/svt-av1"; cp "$CACHE/$a"/SVT-AV1-*.md "$DOC/svt-av1/" 2>/dev/null || true
  cp "$CACHE/$a/ffmpeg-configure.log" "$DOC/ffmpeg/configure-$a.log" 2>/dev/null || true
done
# Every typeface Piklin carries brings its licence with it.
for d in "$ROOT"/data/fonts/*/; do
  cp "$d"*.txt "$DOC/$(basename "$d")-LICENCE.txt" 2>/dev/null || true
done

# ------------------------------------------------------------- relocation
say "Pointing every library at the app"
mapping=("/Library/Frameworks/Python.framework/Versions/$PYVER=@executable_path/../Frameworks/Python.framework/Versions/$PYVER")
for a in $ARCHS; do
  mapping+=("$CACHE/$a/prefix/lib=@executable_path/../Resources/runtime-$a/lib")
done
"$HOST_PY" "$MAC/relocate.py" "$C" "${mapping[@]}"

# ----------------------------------------------------------------- signing
say "Signing ($([ "$SIGN_ID" = - ] && echo "ad hoc" || echo "$SIGN_ID"))"
# Every library first, then the app as a whole (which signs the launcher).
"$HOST_PY" -c "
import sys; sys.path.insert(0, '$MAC')
import relocate
for p in relocate.machos('$C'):
    if not p.endswith('/MacOS/Piklin'):
        print(p)
" | while IFS= read -r f; do codesign --force --sign "$SIGN_ID" "$f" >/dev/null 2>&1 || { echo "could not sign $f" >&2; exit 1; }; done
# The Python framework came sealed by python.org; the parts removed above
# broke that seal, so it is sealed again as a bundle of its own.
codesign --force --sign "$SIGN_ID" "$PV"
codesign --force --sign "$SIGN_ID" "$APP"
codesign --verify --deep --strict "$APP"

# ------------------------------------------------------------------ checks
for a in $ARCHS; do
  say "Checking the $a runtime"
  PIKLIN_BOOT_CHECK=1 arch -"$a" "$C/MacOS/Piklin"
done
arch_list="$(lipo -archs "$C/MacOS/Piklin")"
say "Launcher architectures: $arch_list"

# --------------------------------------------------------------------- dmg
DMG="$ROOT/dist/Piklin-$VERSION.dmg"
say "Disk image"
STAGE="$CACHE/dmg"
rm -rf "$STAGE" "$DMG" && mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -quiet -volname "Piklin" -srcfolder "$STAGE" -fs HFS+ -format UDZO \
    -imagekey zlib-level=9 "$DMG"
[ "$SIGN_ID" = - ] || codesign --force --sign "$SIGN_ID" "$DMG"
# Which build this is, uploaded beside the disk image: the updater tells a
# version published again with fixes apart by it.
printf '%s\n' "$BUILD_ID" > "$DMG.build"
say "Done: $DMG ($(du -h "$DMG" | cut -f1); app $(du -sh "$APP" | cut -f1))"
