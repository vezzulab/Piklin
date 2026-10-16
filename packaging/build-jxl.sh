#!/usr/bin/env bash
# Build the JPEG XL tools Piklin carries: cjxl and djxl, from libjxl
# (BSD-3-Clause), linked statically so they need nothing else installed.
#
# Piklin keeps a photo as JPEG XL when asked to ("Compressed" photos): a JPEG
# becomes about a fifth smaller and turns back into the very same JPEG. The
# Python wheels that do this (pillow-jxl-plugin) are GPL and cannot go inside
# a closed, paid package; libjxl itself is BSD.
#
#   packaging/build-jxl.sh            for the machine it runs on
#   ARCH=x86_64 packaging/build-jxl.sh   on a Mac, for the other processor
#
# Output: packaging/jxl-cache/<linux|macos>-<amd64|arm64>/{cjxl,djxl} and the
# licence texts. Needs git, cmake, a C++ compiler and (Linux) nothing more:
# libjxl brings brotli, highway and lcms2 in its own tree. PNG, GIF, JPEG and
# OpenEXR support is switched off on purpose: it would link the system's copy
# and the tool would no longer run on another computer. Piklin only gives
# cjxl a JPEG and reads djxl's JPEG or PPM.
set -euo pipefail

LIBJXL=v0.11.2

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "$(uname -s)" in
  Darwin) OS=macos ;;
  Linux)  OS=linux ;;
  *) echo "unsupported system: $(uname -s)" >&2; exit 1 ;;
esac
MACHINE="${ARCH:-$(uname -m)}"
case "$MACHINE" in
  x86_64|amd64)  NAME=amd64 ;;
  arm64|aarch64) NAME=arm64 ;;
  *) echo "unsupported machine: $MACHINE" >&2; exit 1 ;;
esac
OUT="$HERE/jxl-cache/$OS-$NAME"
SRC="$HERE/jxl-cache/src/libjxl-$LIBJXL"
BUILD="$HERE/jxl-cache/build-$OS-$NAME"
JOBS="$( (nproc || sysctl -n hw.ncpu) 2>/dev/null || echo 4)"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
if [ -x "$OUT/cjxl" ] && [ -x "$OUT/djxl" ]; then
  say "libjxl $LIBJXL already built for $OS-$NAME"; exit 0
fi

mkdir -p "$HERE/jxl-cache/src" "$OUT"
if [ ! -d "$SRC/.git" ]; then
  say "Fetching libjxl $LIBJXL"
  # skia.googlesource.com (skcms) answers 503 now and then: try again.
  for try in 1 2 3 4 5; do
    rm -rf "$SRC"
    git clone --quiet --depth 1 --branch "$LIBJXL" --recurse-submodules --shallow-submodules \
        https://github.com/libjxl/libjxl.git "$SRC" 2>/dev/null && break
    [ "$try" = 5 ] && { echo "could not fetch libjxl" >&2; exit 1; }
    sleep $((try * 5))
  done
fi

EXTRA=()
# A fully static binary exists on Linux only; macOS always links its own
# system libraries, which every Mac has.
[ "$OS" = linux ] && EXTRA+=(-DJPEGXL_STATIC=ON)
if [ "$OS" = macos ]; then
  # The oldest macOS Piklin supports, and the processor asked for.
  EXTRA+=(-DCMAKE_OSX_ARCHITECTURES="$([ "$NAME" = amd64 ] && echo x86_64 || echo arm64)"
          -DCMAKE_OSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-11.0}")
fi

say "Building cjxl and djxl for $OS-$NAME"
cmake -S "$SRC" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_SHARED_LIBS=OFF -DBUILD_TESTING=OFF \
  -DJPEGXL_ENABLE_TOOLS=ON -DJPEGXL_ENABLE_MANPAGES=OFF -DJPEGXL_ENABLE_DOXYGEN=OFF \
  -DJPEGXL_ENABLE_BENCHMARK=OFF -DJPEGXL_ENABLE_EXAMPLES=OFF -DJPEGXL_ENABLE_FUZZERS=OFF \
  -DJPEGXL_ENABLE_VIEWERS=OFF -DJPEGXL_ENABLE_PLUGINS=OFF -DJPEGXL_ENABLE_JNI=OFF \
  -DJPEGXL_ENABLE_SJPEG=OFF -DJPEGXL_ENABLE_OPENEXR=OFF -DJPEGXL_ENABLE_JPEGLI=OFF \
  -DJPEGXL_ENABLE_SKCMS=ON -DJPEGXL_ENABLE_TCMALLOC=OFF \
  -DCMAKE_DISABLE_FIND_PACKAGE_PNG=ON -DCMAKE_DISABLE_FIND_PACKAGE_GIF=ON \
  -DCMAKE_DISABLE_FIND_PACKAGE_JPEG=ON -DCMAKE_DISABLE_FIND_PACKAGE_OpenEXR=ON \
  -DCMAKE_DISABLE_FIND_PACKAGE_Brotli=ON -DCMAKE_DISABLE_FIND_PACKAGE_HWY=ON \
  "${EXTRA[@]}" >/dev/null
cmake --build "$BUILD" -j"$JOBS" --target cjxl djxl >/dev/null

cp "$BUILD/tools/cjxl" "$BUILD/tools/djxl" "$OUT/"
strip "$OUT/cjxl" "$OUT/djxl" 2>/dev/null || true
cp "$SRC/LICENSE" "$OUT/LIBJXL-LICENSE"
cp "$SRC/PATENTS" "$OUT/LIBJXL-PATENTS" 2>/dev/null || true
for t in brotli highway lcms skcms; do
  for f in LICENSE COPYING LICENSE.md LICENSE.txt; do
    [ -f "$SRC/third_party/$t/$f" ] && cp "$SRC/third_party/$t/$f" "$OUT/$t-$f" && break
  done
done

# What a package may not carry: a library from the system.
case "$OS" in
  macos) LIBS="$(otool -L "$OUT/cjxl" "$OUT/djxl" | grep -v ':$' | grep -v '/usr/lib/\|/System/' || true)" ;;
  linux) LIBS="$(ldd "$OUT/cjxl" "$OUT/djxl" | grep -v ':$\|not a dynamic\|linux-vdso\|libc\.\|libm\.\|libstdc++\|libgcc_s\|libpthread\|libdl\|ld-linux\|librt' || true)" ;;
esac
[ -z "$LIBS" ] || { echo "cjxl/djxl depend on libraries that will not be there:" >&2; echo "$LIBS" >&2; exit 1; }

say "Checking a round trip"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
python3 - "$T/a.jpg" <<'PY'
import sys
from PIL import Image
im = Image.effect_noise((256, 256), 60).convert("RGB")
im.save(sys.argv[1], quality=90)
PY
"$OUT/cjxl" "$T/a.jpg" "$T/a.jxl" --quiet
SAID="$("$OUT/djxl" "$T/a.jxl" "$T/b.jpg" -v 2>&1)"
case "$SAID" in *"Reconstructed to JPEG"*) ;; *) echo "djxl did not rebuild the JPEG" >&2; exit 1 ;; esac
cmp "$T/a.jpg" "$T/b.jpg"
say "Done: $OUT"
