#!/usr/bin/env bash
# Build the small OpenCV Piklin ships on Windows: only the modules Piklin calls.
#
# MSYS2's OpenCV is built with everything (VTK, Qt, Ogre, the extra modules) and drags about
# 350 MB of libraries into the installer for functions Piklin never calls. Piklin uses only
# cvtColor, resize, warpAffine, copyMakeBorder, inpaint, VideoCapture and the face
# detector and recogniser, so this builds core, imgproc, imgcodecs, videoio, photo, dnn and
# objdetect, with no video backend (Piklin decodes video with PyAV and OpenCV's reader is only a
# fallback it skips when it cannot open a file) and no other third-party library.
#
# Run it from the MSYS2 "UCRT64" shell, once (about 15 minutes); build-windows.sh uses the result:
#
#   packaging/windows/build-opencv.sh          # -> packaging/windows/opencv-cache/
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION=5.0.0
SHA256=b0528f5a1d379d59d4701cb28c36e22214cc51cf64594e5b56f2d3e6c0233095
WORK="$HERE/opencv-src"
OUT="$HERE/opencv-cache"
PYVER="$(python -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

mkdir -p "$WORK"
TAR="$WORK/opencv-$VERSION.tar.gz"
if [ ! -f "$TAR" ]; then
  say "downloading OpenCV $VERSION"
  curl -fsSL -o "$TAR" "https://github.com/opencv/opencv/archive/refs/tags/$VERSION.tar.gz"
fi
echo "$SHA256  $TAR" | sha256sum --check --status || { echo "OpenCV $VERSION does not match its checksum" >&2; rm -f "$TAR"; exit 1; }
[ -d "$WORK/opencv-$VERSION" ] || tar xf "$TAR" -C "$WORK"

# MinGW has no posix_memalign, which the matrix code (MLAS) falls back to: use the Windows
# aligned allocator it already uses with Microsoft's compiler.
python - "$WORK/opencv-$VERSION/3rdparty/mlas/lib" <<'PY'
import sys
from pathlib import Path
for name in ("mlasi.h", "platform.cpp"):
    p = Path(sys.argv[1]) / name
    s = p.read_text()
    out, i = [], 0
    while True:
        j = s.find("#ifdef _MSC_VER", i)
        if j < 0:
            out.append(s[i:])
            break
        out.append(s[i:j])
        if "ThreadedBuf" in s[j:j + 220]:
            out.append("#if defined(_MSC_VER) || defined(__MINGW32__)")
        else:
            out.append("#ifdef _MSC_VER")
        i = j + len("#ifdef _MSC_VER")
    p.write_text("".join(out))
PY

say "configuring"
rm -rf "$WORK/build" "$OUT"
mkdir -p "$WORK/build"
cd "$WORK/build"
cmake -G Ninja "../opencv-$VERSION" \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$OUT" \
  -DBUILD_SHARED_LIBS=ON \
  -DBUILD_LIST=core,imgproc,imgcodecs,videoio,photo,dnn,objdetect,python3 \
  -DBUILD_TESTS=OFF -DBUILD_PERF_TESTS=OFF -DBUILD_EXAMPLES=OFF -DBUILD_DOCS=OFF -DBUILD_opencv_apps=OFF -DBUILD_JAVA=OFF \
  -DWITH_FFMPEG=OFF -DWITH_MSMF=OFF -DWITH_DSHOW=OFF -DWITH_GSTREAMER=OFF -DWITH_OPENCL=OFF -DWITH_IPP=OFF -DWITH_TBB=OFF \
  -DWITH_OPENEXR=OFF -DWITH_TIFF=OFF -DWITH_WEBP=OFF -DWITH_OPENJPEG=OFF -DWITH_JASPER=OFF -DWITH_AVIF=OFF -DWITH_LAPACK=OFF \
  -DWITH_VTK=OFF -DWITH_QT=OFF -DWITH_GTK=OFF -DWITH_OGRE=OFF -DWITH_WIN32UI=OFF -DWITH_OPENGL=OFF -DWITH_VULKAN=OFF \
  -DBUILD_JPEG=ON -DBUILD_PNG=ON -DBUILD_ZLIB=ON -DBUILD_PROTOBUF=ON -DBUILD_OPENJPEG=OFF -DBUILD_TIFF=OFF -DBUILD_WEBP=OFF \
  -DOPENCV_GENERATE_PKGCONFIG=OFF \
  -DPython3_EXECUTABLE="$(which python)" -DPYTHON3_EXECUTABLE="$(which python)" \
  -DPYTHON3_NUMPY_INCLUDE_DIRS="$(python -c 'import numpy; print(numpy.get_include())')" \
  -DOPENCV_PYTHON_INSTALL_PATH="lib/python$PYVER/site-packages" \
  -DINSTALL_PYTHON_EXAMPLES=OFF -DINSTALL_C_EXAMPLES=OFF

say "building (this takes a while)"
ninja -j"$(nproc)"
ninja install
say "Done: $OUT"
