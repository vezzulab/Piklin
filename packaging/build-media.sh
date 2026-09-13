#!/usr/bin/env bash
# Build the media stack Piklin ships inside its .deb: FFmpeg and PyAV, with
# no GPL code in them.
#
# The ready-made PyAV wheels on PyPI carry an FFmpeg built with x264 and
# x265, which makes that build GPL - it cannot go inside a closed, paid
# package. This builds the same thing licensed LGPL-2.1+:
#
#   decoding  every format FFmpeg reads natively (H.264, HEVC, ProRes,
#             MPEG-2/4, VP8/9 via libvpx, AV1 via dav1d, AAC, MP3, Opus...)
#   encoding  H.264 through OpenH264 (BSD), VP9 through libvpx (BSD),
#             Opus (BSD), FFmpeg's own AAC and GIF encoders
#
# The codec libraries are linked statically into FFmpeg, so the only
# shared libraries that travel are FFmpeg's own, renamed by auditwheel so
# they can never be confused with a system FFmpeg.
#
# Needs, on the build machine only: gcc make nasm meson ninja cmake patchelf
# pkg-config curl.     Output: packaging/media-cache/wheels/av-*.whl
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
CACHE="$HERE/media-cache"
SRC="$CACHE/src"
PREFIX="$CACHE/prefix"
OUT="$CACHE/wheels"
JOBS="$(nproc)"
PY="${PY:-$ROOT/.venv/bin/python}"

FFMPEG=8.0
OPENH264=2.6.0
LIBVPX=1.15.2
OPUS=1.5.2
DAV1D=1.5.1
PYAV=18.1.0

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
fetch() {   # fetch <url> <file>
  [ -s "$SRC/$2" ] || curl -fL --retry 3 -o "$SRC/$2" "$1"
}
mkdir -p "$SRC" "$PREFIX" "$OUT"
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig:$PREFIX/lib/x86_64-linux-gnu/pkgconfig"
export CFLAGS="-O2 -fPIC" CXXFLAGS="-O2 -fPIC"

say "Downloading sources"
fetch "https://ffmpeg.org/releases/ffmpeg-$FFMPEG.tar.xz" "ffmpeg-$FFMPEG.tar.xz"
fetch "https://github.com/cisco/openh264/archive/refs/tags/v$OPENH264.tar.gz" "openh264-$OPENH264.tar.gz"
fetch "https://github.com/webmproject/libvpx/archive/refs/tags/v$LIBVPX.tar.gz" "libvpx-$LIBVPX.tar.gz"
fetch "https://downloads.xiph.org/releases/opus/opus-$OPUS.tar.gz" "opus-$OPUS.tar.gz"
fetch "https://code.videolan.org/videolan/dav1d/-/archive/$DAV1D/dav1d-$DAV1D.tar.gz" "dav1d-$DAV1D.tar.gz"

build_dir() { rm -rf "$SRC/$1"; mkdir -p "$SRC/$1"; tar -xf "$SRC/$2" -C "$SRC/$1" --strip-components=1; }

if [ ! -f "$PREFIX/lib/libopenh264.a" ]; then
  say "OpenH264 $OPENH264 (BSD)"
  build_dir openh264 "openh264-$OPENH264.tar.gz"
  make -C "$SRC/openh264" -j"$JOBS" OS=linux ARCH=x86_64 PREFIX="$PREFIX" install-static >/dev/null
fi

if [ ! -f "$PREFIX/lib/libvpx.a" ]; then
  say "libvpx $LIBVPX (BSD)"
  build_dir libvpx "libvpx-$LIBVPX.tar.gz"
  (cd "$SRC/libvpx" && ./configure --prefix="$PREFIX" --enable-pic --enable-static \
      --disable-shared --disable-examples --disable-tools --disable-docs \
      --disable-unit-tests --enable-vp9-highbitdepth >/dev/null \
   && make -j"$JOBS" >/dev/null && make install >/dev/null)
fi

if [ ! -f "$PREFIX/lib/libopus.a" ]; then
  say "Opus $OPUS (BSD)"
  build_dir opus "opus-$OPUS.tar.gz"
  (cd "$SRC/opus" && ./configure --prefix="$PREFIX" --with-pic --enable-static \
      --disable-shared --disable-doc --disable-extra-programs >/dev/null \
   && make -j"$JOBS" >/dev/null && make install >/dev/null)
fi

if ! ls "$PREFIX"/lib/libdav1d.a "$PREFIX"/lib/*/libdav1d.a >/dev/null 2>&1; then
  say "dav1d $DAV1D (BSD)"
  build_dir dav1d "dav1d-$DAV1D.tar.gz"
  (cd "$SRC/dav1d" && meson setup build --prefix="$PREFIX" --libdir=lib \
      --default-library=static --buildtype=release -Denable_tools=false \
      -Denable_tests=false >/dev/null && ninja -C build >/dev/null \
   && ninja -C build install >/dev/null)
fi

if [ ! -f "$PREFIX/lib/libavcodec.so" ]; then
  say "FFmpeg $FFMPEG (LGPL)"
  build_dir ffmpeg "ffmpeg-$FFMPEG.tar.xz"
  # --disable-autodetect: nothing from the build machine sneaks in as a
  # runtime dependency the user would then have to have installed.
  (cd "$SRC/ffmpeg" && ./configure --prefix="$PREFIX" \
      --enable-shared --disable-static --enable-pic \
      --disable-programs --disable-doc --disable-debug \
      --disable-autodetect --enable-zlib \
      --enable-libopenh264 --enable-libvpx --enable-libopus --enable-libdav1d \
      --extra-cflags="-I$PREFIX/include" --extra-ldflags="-L$PREFIX/lib" \
      --pkg-config-flags="--static" | tee "$CACHE/ffmpeg-configure.log" | grep -E "^License:")
  grep -q "^License: LGPL version 2.1 or later" "$CACHE/ffmpeg-configure.log" \
    || { echo "FFmpeg did not configure as LGPL - refusing to continue" >&2; exit 1; }
  make -C "$SRC/ffmpeg" -j"$JOBS" >/dev/null
  make -C "$SRC/ffmpeg" install >/dev/null
fi

say "PyAV $PYAV against that FFmpeg"
rm -rf "$CACHE/pyav-build" && mkdir -p "$CACHE/pyav-build"
PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig" LD_LIBRARY_PATH="$PREFIX/lib" \
  "$PY" -m pip wheel --no-deps --no-binary=av "av==$PYAV" -w "$CACHE/pyav-build" >/dev/null
"$PY" -m pip install --quiet auditwheel
say "Bundling FFmpeg into the wheel"
rm -f "$OUT"/av-*.whl
LD_LIBRARY_PATH="$PREFIX/lib" "$PY" -m auditwheel repair \
  --plat "manylinux_2_39_x86_64" -w "$OUT" "$CACHE"/pyav-build/av-*.whl >/dev/null
cp "$SRC/ffmpeg/COPYING.LGPLv2.1" "$CACHE/FFMPEG-LICENSE.txt"
say "Done: $(ls "$OUT"/av-*.whl)"
