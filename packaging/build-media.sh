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
#             AV1 through SVT-AV1 (BSD), Opus (BSD), FFmpeg's own AAC and GIF
#             encoders
#
# The codec libraries are linked statically into FFmpeg, so the only
# shared libraries that travel are FFmpeg's own, renamed by auditwheel so
# they can never be confused with a system FFmpeg.
#
# Builds for the machine it runs on: x86_64 (amd64) or aarch64 (arm64).
#
# Needs, on the build machine only: gcc make nasm meson ninja cmake patchelf
# pkg-config curl.     Output: packaging/media-cache/wheels/av-*.whl
# (packaging/media-cache-aarch64/wheels/ on an arm64 machine)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
MACHINE="$(uname -m)"
case "$MACHINE" in
  x86_64)  OPENH264_ARCH=x86_64; CACHE="$HERE/media-cache" ;;
  aarch64) OPENH264_ARCH=arm64;  CACHE="$HERE/media-cache-aarch64" ;;
  *) echo "unsupported machine: $MACHINE" >&2; exit 1 ;;
esac
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
SVTAV1=3.0.2
PYAV=18.1.0

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
fetch() {   # fetch <url> <file>
  [ -s "$SRC/$2" ] || curl -fL --retry 3 -o "$SRC/$2" "$1"
}
mkdir -p "$SRC" "$PREFIX" "$OUT"
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig:$PREFIX/lib/$MACHINE-linux-gnu/pkgconfig"
export CFLAGS="-O2 -fPIC" CXXFLAGS="-O2 -fPIC"

say "Downloading sources"
fetch "https://ffmpeg.org/releases/ffmpeg-$FFMPEG.tar.xz" "ffmpeg-$FFMPEG.tar.xz"
fetch "https://github.com/cisco/openh264/archive/refs/tags/v$OPENH264.tar.gz" "openh264-$OPENH264.tar.gz"
fetch "https://github.com/webmproject/libvpx/archive/refs/tags/v$LIBVPX.tar.gz" "libvpx-$LIBVPX.tar.gz"
fetch "https://downloads.xiph.org/releases/opus/opus-$OPUS.tar.gz" "opus-$OPUS.tar.gz"
fetch "https://code.videolan.org/videolan/dav1d/-/archive/$DAV1D/dav1d-$DAV1D.tar.gz" "dav1d-$DAV1D.tar.gz"
fetch "https://gitlab.com/AOMediaCodec/SVT-AV1/-/archive/v$SVTAV1/SVT-AV1-v$SVTAV1.tar.gz" "svtav1-$SVTAV1.tar.gz"

build_dir() { rm -rf "$SRC/$1"; mkdir -p "$SRC/$1"; tar -xf "$SRC/$2" -C "$SRC/$1" --strip-components=1; }

if [ ! -f "$PREFIX/lib/libopenh264.a" ]; then
  say "OpenH264 $OPENH264 (BSD)"
  build_dir openh264 "openh264-$OPENH264.tar.gz"
  make -C "$SRC/openh264" -j"$JOBS" OS=linux ARCH="$OPENH264_ARCH" PREFIX="$PREFIX" install-static >/dev/null
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

if [ ! -f "$PREFIX/lib/libSvtAv1Enc.a" ]; then
  say "SVT-AV1 $SVTAV1 (BSD)"
  build_dir svtav1 "svtav1-$SVTAV1.tar.gz"
  cmake -S "$SRC/svtav1" -B "$SRC/svtav1/_build" -G Ninja -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_INSTALL_LIBDIR=lib \
      -DBUILD_SHARED_LIBS=OFF -DBUILD_APPS=OFF -DBUILD_DEC=OFF -DBUILD_TESTING=OFF \
      -DCMAKE_POSITION_INDEPENDENT_CODE=ON >/dev/null \
   && cmake --build "$SRC/svtav1/_build" -j"$JOBS" >/dev/null \
   && cmake --install "$SRC/svtav1/_build" >/dev/null
fi

# (rebuilt when a build made before AV1 was added is found)
if [ ! -f "$PREFIX/lib/libavcodec.so" ] || ! grep -q "libsvtav1" "$CACHE/ffmpeg-configure.log" 2>/dev/null; then
  say "FFmpeg $FFMPEG (LGPL)"
  build_dir ffmpeg "ffmpeg-$FFMPEG.tar.xz"
  # --disable-autodetect: nothing from the build machine sneaks in as a
  # runtime dependency the user would then have to have installed.
  (cd "$SRC/ffmpeg" && ./configure --prefix="$PREFIX" \
      --enable-shared --disable-static --enable-pic \
      --disable-programs --disable-doc --disable-debug \
      --disable-autodetect --enable-zlib \
      --enable-libopenh264 --enable-libvpx --enable-libopus --enable-libdav1d \
      --enable-libsvtav1 \
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
  --plat "manylinux_2_39_$MACHINE" -w "$OUT" "$CACHE"/pyav-build/av-*.whl >/dev/null
cp "$SRC/ffmpeg/COPYING.LGPLv2.1" "$CACHE/FFMPEG-LICENSE.txt"
# SVT-AV1 asks for its licence and its patent licence to travel with it.
for f in LICENSE.md PATENTS.md; do cp "$SRC/svtav1/$f" "$CACHE/SVT-AV1-$f" 2>/dev/null || true; done
say "Done: $(ls "$OUT"/av-*.whl)"
