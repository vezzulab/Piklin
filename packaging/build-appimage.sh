#!/usr/bin/env bash
# Build Piklin as an AppImage for amd64 or arm64: one file that runs on most
# Linux systems from 2024 on (Ubuntu 24.04, Linux Mint 22, Debian 13, Fedora
# 40 and newer), without installing anything.
#
# It is the .deb's Piklin - the same code, compiled libraries and FFmpeg
# without GPL code - with Ubuntu 24.04's own Python, GTK 4 and libadwaita
# carried inside. Graphics drivers, glibc, fonts and sound come from the
# computer, as for any AppImage.
#
#   packaging/build-appimage.sh arm64
#   packaging/build-appimage.sh amd64
#
# Runs in a clean Ubuntu 24.04 container (Docker). Needs build-media.sh's
# PyAV wheel for that architecture in packaging/media-cache (amd64) or
# packaging/media-cache-aarch64 (arm64), and packaging/appimage-cache with
# appimagetool-<arch>.AppImage, runtime-<arch> and excludelist from
#   https://github.com/AppImage/appimagetool/releases (continuous)
#   https://github.com/AppImage/type2-runtime/releases (continuous)
#   https://github.com/AppImageCommunity/pkg2appimage (excludelist)
#
# Output: dist/Piklin-<version>-<x86_64|aarch64>.AppImage and its .build
set -euo pipefail

ARCH="${1:?usage: build-appimage.sh amd64|arm64}"
case "$ARCH" in
  amd64) APPARCH=x86_64;  TRIPLE=x86_64-linux-gnu;  MEDIA=media-cache ;;
  arm64) APPARCH=aarch64; TRIPLE=aarch64-linux-gnu; MEDIA=media-cache-aarch64 ;;
  *) echo "unsupported architecture: $ARCH" >&2; exit 2 ;;
esac
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
TOOLS="$HERE/appimage-cache"
OWNER="$(id -u):$(id -g)"
VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$ROOT/piklin/app.py")"
say() { printf '\033[1;36m==>\033[0m [%s] %s\n' "$ARCH" "$*"; }

ls "$HERE/$MEDIA"/wheels/av-*.whl >/dev/null 2>&1 \
  || { echo "No PyAV wheel in packaging/$MEDIA: run build-media.sh for $ARCH first" >&2; exit 1; }
for f in "appimagetool-$APPARCH.AppImage" "runtime-$APPARCH" excludelist; do
  [ -s "$TOOLS/$f" ] || { echo "Missing packaging/appimage-cache/$f (see the top of this script)" >&2; exit 1; }
done

say "Piklin $VERSION in Ubuntu 24.04"
docker run --rm --platform "linux/$ARCH" -v "$ROOT":/src -w /src \
    -e ARCH="$ARCH" -e APPARCH="$APPARCH" -e TRIPLE="$TRIPLE" -e VERSION="$VERSION" -e MAINTAINER \
    -e DEBIAN_FRONTEND=noninteractive ubuntu:24.04 bash -euc "
  apt-get update -qq
  apt-get install -y -qq --no-install-recommends python3 python3-venv python3-pip \
      python3-gi python3-gi-cairo python3-cffi-backend gir1.2-glib-2.0 gir1.2-gtk-4.0 \
      gir1.2-adw-1 gir1.2-secret-1 libgtk-4-1 libadwaita-1-0 librsvg2-common \
      libgdk-pixbuf-2.0-0 adwaita-icon-theme hicolor-icon-theme patchelf file git \
      ca-certificates dpkg-dev >/dev/null
  git config --global --add safe.directory /src
  python3 -m venv --system-site-packages /tmp/venv
  export PATH=/tmp/venv/bin:\$PATH
  trap 'chown -R $OWNER /src/dist /src/packaging 2>/dev/null || true' EXIT
  STAGE_ONLY=1 PYVERS=3.12 packaging/build-deb.sh
  /usr/bin/python3 packaging/appimage/make-appdir.py
  mkdir -p dist
  OUT=dist/Piklin-\$VERSION-\$APPARCH.AppImage
  # An AppImage marks itself with three bytes in its header, which an
  # emulated build (amd64 on an ARM Mac) refuses as a bad executable: the
  # copy run here has them cleared.
  cp packaging/appimage-cache/appimagetool-\$APPARCH.AppImage /tmp/appimagetool
  printf '\0\0\0' | dd of=/tmp/appimagetool bs=1 seek=8 conv=notrunc status=none
  ARCH=\$APPARCH /tmp/appimagetool --appimage-extract-and-run \
      --no-appstream --runtime-file packaging/appimage-cache/runtime-\$APPARCH \
      packaging/AppDir \"\$OUT\" >/tmp/appimagetool.log 2>&1 || { tail -40 /tmp/appimagetool.log; exit 1; }
  cp packaging/AppDir/usr/share/piklin/piklin/BUILD_ID \"\$OUT.build\"
  PIKLIN_BOOT_CHECK=1 packaging/AppDir/AppRun"
say "Done: $(ls "$ROOT"/dist/Piklin-"$VERSION"-"$APPARCH".AppImage) ($(du -h "$ROOT"/dist/Piklin-"$VERSION"-"$APPARCH".AppImage | cut -f1))"
