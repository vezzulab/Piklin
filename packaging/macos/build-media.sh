#!/usr/bin/env bash
# The media stack inside Piklin.app: FFmpeg and PyAV with no GPL code, for
# one architecture. The macOS counterpart of packaging/build-media.sh - same
# versions (read from that script), same FFmpeg configuration, same licence
# check - so video behaves the same on Linux and on a Mac.
#
#   packaging/macos/build-media.sh arm64
#   packaging/macos/build-media.sh x86_64     (runs itself under Rosetta)
#
# Needs, on the build machine only: Xcode, Homebrew meson ninja nasm, and the
# build Python packaging/macos/build-stack.sh sets up.
#
# Output: packaging/macos-cache/<arch>/media-wheels/av-*.whl
set -euo pipefail

# Run from a copy: bash reads a script as it runs it, so editing this file
# during a long build would derail the build.
if [ -z "${PIKLIN_MEDIA_HERE:-}" ]; then
  copy="$(mktemp -t piklin-build-media)"
  cp "${BASH_SOURCE[0]}" "$copy"
  PIKLIN_MEDIA_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" exec /bin/bash "$copy" "$@"
fi

ARCH="${1:?usage: build-media.sh arm64|x86_64}"
case "$ARCH" in arm64|x86_64) ;; *) echo "unknown architecture: $ARCH" >&2; exit 2 ;; esac
if [ "$ARCH" = x86_64 ] && [ "$(uname -m)" != x86_64 ]; then
  exec arch -x86_64 /bin/bash "$0" "$@"
fi

HERE="$PIKLIN_MEDIA_HERE"
PACKAGING="$(dirname "$HERE")"
SRC="$PACKAGING/macos-cache/src"
CACHE="$PACKAGING/macos-cache/$ARCH"
WORK="$CACHE/media-build"
PREFIX="$CACHE/media-prefix"
OUT="$CACHE/media-wheels"
MIN_MACOS="${MIN_MACOS:-11.0}"
JOBS="$(sysctl -n hw.ncpu)"
[ "$ARCH" = arm64 ] && BREW=/opt/homebrew || BREW=/usr/local
PY="$CACHE/buildpy/bin/python3"

# One set of versions for every system.
ver() { sed -n "s/^$1=//p" "$PACKAGING/build-media.sh"; }
FFMPEG="$(ver FFMPEG)"; OPENH264="$(ver OPENH264)"; LIBVPX="$(ver LIBVPX)"
OPUS="$(ver OPUS)"; DAV1D="$(ver DAV1D)"; PYAV="$(ver PYAV)"

export PATH="$BREW/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"
export MACOSX_DEPLOYMENT_TARGET="$MIN_MACOS"
export SDKROOT="$(xcrun --show-sdk-path)"
export PKG_CONFIG_LIBDIR="$PREFIX/lib/pkgconfig"
export PKG_CONFIG_PATH="$PKG_CONFIG_LIBDIR"
export CFLAGS="-arch $ARCH -mmacosx-version-min=$MIN_MACOS -O2 -fPIC"
export CXXFLAGS="$CFLAGS"
export LDFLAGS="-arch $ARCH -mmacosx-version-min=$MIN_MACOS -Wl,-headerpad_max_install_names"
# The PyAV wheel is named for the macOS it targets, not the Mac building it.
export _PYTHON_HOST_PLATFORM="macosx-$MIN_MACOS-$ARCH"

say() { printf '\033[1;36m==>\033[0m [%s] %s\n' "$ARCH" "$*"; }
# Downloaded under a temporary name, then renamed: two builds sharing the
# download folder never see each other's half-written archive.
fetch() {
  [ -s "$SRC/$2" ] && return 0
  curl -fsSL --retry 3 -o "$SRC/$2.$$.part" "$1" && mv "$SRC/$2.$$.part" "$SRC/$2"
}
build_dir() { rm -rf "$WORK/$1"; mkdir -p "$WORK/$1"; tar -xf "$SRC/$2" -C "$WORK/$1" --strip-components=1; }
mkdir -p "$SRC" "$WORK" "$PREFIX" "$OUT"

if [ ! -x "$PY" ]; then
  "$BREW/opt/python@3.14/bin/python3.14" -m venv "$CACHE/buildpy"
  "$PY" -m pip install --quiet setuptools wheel packaging
fi

say "Downloading sources"
fetch "https://ffmpeg.org/releases/ffmpeg-$FFMPEG.tar.xz" "ffmpeg-$FFMPEG.tar.xz"
fetch "https://github.com/cisco/openh264/archive/refs/tags/v$OPENH264.tar.gz" "openh264-$OPENH264.tar.gz"
fetch "https://github.com/webmproject/libvpx/archive/refs/tags/v$LIBVPX.tar.gz" "libvpx-$LIBVPX.tar.gz"
fetch "https://downloads.xiph.org/releases/opus/opus-$OPUS.tar.gz" "opus-$OPUS.tar.gz"
fetch "https://code.videolan.org/videolan/dav1d/-/archive/$DAV1D/dav1d-$DAV1D.tar.gz" "dav1d-$DAV1D.tar.gz"

if [ ! -f "$PREFIX/lib/libopenh264.a" ]; then
  say "OpenH264 $OPENH264 (BSD)"
  build_dir openh264 "openh264-$OPENH264.tar.gz"
  make -C "$WORK/openh264" -j"$JOBS" OS=darwin ARCH="$ARCH" PREFIX="$PREFIX" install-static >/dev/null
fi

if [ ! -f "$PREFIX/lib/libvpx.a" ]; then
  say "libvpx $LIBVPX (BSD)"
  build_dir libvpx "libvpx-$LIBVPX.tar.gz"
  (cd "$WORK/libvpx" && ./configure --target="$ARCH-darwin23-gcc" --prefix="$PREFIX" \
      --enable-pic --enable-static --disable-shared --disable-examples --disable-tools \
      --disable-docs --disable-unit-tests --enable-vp9-highbitdepth >/dev/null \
   && make -j"$JOBS" >/dev/null && make install >/dev/null)
fi

if [ ! -f "$PREFIX/lib/libopus.a" ]; then
  say "Opus $OPUS (BSD)"
  build_dir opus "opus-$OPUS.tar.gz"
  # Every ARM Mac has NEON, and Opus cannot probe for it at run time on macOS.
  rtcd=(); [ "$ARCH" = arm64 ] && rtcd=(--disable-rtcd)
  (cd "$WORK/opus" && ./configure --prefix="$PREFIX" --with-pic --enable-static \
      --disable-shared --disable-doc --disable-extra-programs ${rtcd[@]+"${rtcd[@]}"} >/dev/null \
   && make -j"$JOBS" >/dev/null && make install >/dev/null)
fi

if [ ! -f "$PREFIX/lib/libdav1d.a" ]; then
  say "dav1d $DAV1D (BSD)"
  build_dir dav1d "dav1d-$DAV1D.tar.gz"
  (cd "$WORK/dav1d" && meson setup build --prefix="$PREFIX" --libdir=lib \
      --default-library=static --buildtype=release -Denable_tools=false \
      -Denable_tests=false >/dev/null && ninja -C build >/dev/null \
   && ninja -C build install >/dev/null)
fi

if [ ! -f "$PREFIX/lib/libavcodec.dylib" ]; then
  say "FFmpeg $FFMPEG (LGPL)"
  build_dir ffmpeg "ffmpeg-$FFMPEG.tar.xz"
  # The same configuration as on Linux. --disable-autodetect also keeps
  # Apple's VideoToolbox and AudioToolbox out, so decoding and encoding are
  # FFmpeg's own on both systems.
  (cd "$WORK/ffmpeg" && ./configure --prefix="$PREFIX" \
      --enable-shared --disable-static --enable-pic \
      --disable-programs --disable-doc --disable-debug \
      --disable-autodetect --enable-zlib \
      --enable-libopenh264 --enable-libvpx --enable-libopus --enable-libdav1d \
      --arch="$ARCH" --cc=clang \
      --extra-cflags="-I$PREFIX/include $CFLAGS" --extra-ldflags="-L$PREFIX/lib $LDFLAGS" \
      --pkg-config-flags="--static" | tee "$CACHE/ffmpeg-configure.log" | grep -E "^License:")
  grep -q "^License: LGPL version 2.1 or later" "$CACHE/ffmpeg-configure.log" \
    || { echo "FFmpeg did not configure as LGPL - refusing to continue" >&2; exit 1; }
  make -C "$WORK/ffmpeg" -j"$JOBS" >/dev/null
  make -C "$WORK/ffmpeg" install >/dev/null
fi

# Already built against this FFmpeg: nothing to do.
# (|| true: with no wheel yet, ls fails, and under pipefail that stopped the build silently)
wheel="$(ls "$OUT"/av-"$PYAV"-*.whl 2>/dev/null | head -1 || true)"
if [ -n "$wheel" ] && [ "$wheel" -nt "$PREFIX/lib/libavcodec.dylib" ]; then
  say "Done: $wheel (already built)"
  exit 0
fi

say "PyAV $PYAV against that FFmpeg"
rm -rf "$WORK/pyav-build" && mkdir -p "$WORK/pyav-build"
# Never from pip's cache: a wheel built earlier, for another macOS and another
# FFmpeg, came back from it looking like a fresh build.
"$PY" -m pip wheel --quiet --no-deps --no-cache-dir --no-binary=av "av==$PYAV" -w "$WORK/pyav-build"
"$PY" -m pip install --quiet delocate
say "Bundling FFmpeg into the wheel"
rm -f "$OUT"/av-*.whl
"$CACHE/buildpy/bin/delocate-wheel" --require-archs "$ARCH" -w "$OUT" "$WORK"/pyav-build/av-*.whl >/dev/null
if unzip -l "$OUT"/av-*.whl | grep -qiE "x264|x265|postproc|fdk"; then
  echo "GPL or non-free codec library found in the media stack - refusing to continue" >&2
  exit 1
fi
cp "$WORK/ffmpeg/COPYING.LGPLv2.1" "$CACHE/FFMPEG-LICENSE.txt"
say "Done: $(ls "$OUT"/av-*.whl)"
