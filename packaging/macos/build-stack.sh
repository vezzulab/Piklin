#!/usr/bin/env bash
# Build the GTK 4 and libadwaita stack Piklin.app carries, for one
# architecture, for macOS 14 and newer.
#
# Homebrew's libraries are built for the macOS the build machine runs, so an
# app made from them refuses to start on anything older. This builds the same
# versions from source with a fixed deployment target into a prefix of its
# own, which packaging/build-macos.sh then copies into the app.
#
#   packaging/macos/build-stack.sh arm64
#   packaging/macos/build-stack.sh x86_64     (runs itself under Rosetta)
#
# Needs, on the build machine only: Xcode; Homebrew in /opt/homebrew with
# meson ninja cmake pkgconf nasm cargo-c python@3.14; for x86_64 also Intel
# Homebrew in /usr/local with meson ninja cmake pkgconf python@3.14; rustup
# with the x86_64-apple-darwin target. Sources come from
# packaging/macos-cache/src (see fetch below).
#
# Output: packaging/macos-cache/<arch>/prefix and .../wheels (pycairo, PyGObject)
set -euo pipefail

# Run from a copy: bash reads a script as it runs it, so editing this file
# during a build that takes an hour would derail the build.
if [ -z "${PIKLIN_STACK_HERE:-}" ]; then
  copy="$(mktemp -t piklin-build-stack)"
  cp "${BASH_SOURCE[0]}" "$copy"
  PIKLIN_STACK_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" exec /bin/bash "$copy" "$@"
fi

ARCH="${1:?usage: build-stack.sh arm64|x86_64}"
case "$ARCH" in arm64|x86_64) ;; *) echo "unknown architecture: $ARCH" >&2; exit 2 ;; esac
# An Intel build runs whole under Rosetta, so every tool and compiler it
# starts builds for x86_64 natively - no cross-compiling.
if [ "$ARCH" = x86_64 ] && [ "$(uname -m)" != x86_64 ]; then
  exec arch -x86_64 /bin/bash "$0" "$@"
fi

HERE="$PIKLIN_STACK_HERE"
CACHE="$(dirname "$HERE")/macos-cache"
SRC="$CACHE/src"
WORK="$CACHE/$ARCH/build"
PREFIX="$CACHE/$ARCH/prefix"
WHEELS="$CACHE/$ARCH/wheels"
STAMPS="$PREFIX/.built"
LOGS="$CACHE/$ARCH/logs"
MIN_MACOS="${MIN_MACOS:-14.0}"
JOBS="$(sysctl -n hw.ncpu)"

if [ "$ARCH" = arm64 ]; then
  BREW=/opt/homebrew; TRIPLE=aarch64-apple-darwin
else
  BREW=/usr/local; TRIPLE=x86_64-apple-darwin
fi
BASE_PY="$BREW/opt/python@3.14/bin/python3.14"
PY="$CACHE/$ARCH/buildpy/bin/python3"
SDK="$(xcrun --show-sdk-path)"

# Tools from this architecture's Homebrew first; nasm and cargo-c are only
# tools, so the Apple Silicon ones serve an Intel build as well.
# GNU Bison 3 for gobject-introspection: macOS's own bison is 2.3.
export PATH="$PREFIX/bin:$BREW/bin:/opt/homebrew/opt/bison/bin:$HOME/.cargo/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"
export MACOSX_DEPLOYMENT_TARGET="$MIN_MACOS"
export SDKROOT="$SDK"
# Only this prefix is visible to pkg-config and CMake: a library quietly
# found in Homebrew would work here and be missing on everyone else's Mac.
export PKG_CONFIG_LIBDIR="$PREFIX/lib/pkgconfig:$PREFIX/share/pkgconfig"
export PKG_CONFIG_PATH="$PKG_CONFIG_LIBDIR"
export CMAKE_PREFIX_PATH="$PREFIX"
export CMAKE_IGNORE_PREFIX_PATH="/opt/homebrew;/usr/local"
export CFLAGS="-arch $ARCH -mmacosx-version-min=$MIN_MACOS -O2"
export CXXFLAGS="$CFLAGS"
export OBJCFLAGS="$CFLAGS"
# Room in every binary's header for build-macos.sh to rewrite library paths.
export LDFLAGS="-arch $ARCH -mmacosx-version-min=$MIN_MACOS -L$PREFIX/lib -Wl,-headerpad_max_install_names"
export CARGO_BUILD_TARGET="$TRIPLE"
export RUSTFLAGS="-C link-arg=-Wl,-headerpad_max_install_names"

say() { printf '\033[1;36m==>\033[0m [%s] %s\n' "$ARCH" "$*"; }
mkdir -p "$WORK" "$PREFIX/lib/pkgconfig" "$STAMPS" "$WHEELS" "$LOGS"

# gobject-introspection's scanner and the Python bindings build with
# setuptools, which Homebrew's Python no longer carries: a venv of our own.
if [ ! -x "$PY" ]; then
  say "build Python"
  "$BASE_PY" -m venv "$CACHE/$ARCH/buildpy"
  "$PY" -m pip install --quiet setuptools wheel packaging
fi

unpack() {   # unpack <archive> -> prints the fresh source directory
  local dir="$WORK/src-$(basename "$1" | sed -E 's/\.tar\.(gz|xz|bz2)$//')"
  rm -rf "$dir"; mkdir -p "$dir"
  tar -xf "$SRC/$1" -C "$dir" --strip-components=1
  # Some archives (AppStream) hold "./name-version/...": one level deeper.
  local inner
  if [ ! -e "$dir/meson.build" ] && [ ! -e "$dir/CMakeLists.txt" ] && [ ! -e "$dir/configure" ] \
      && [ "$(find "$dir" -mindepth 1 -maxdepth 1 | wc -l)" -eq 1 ]; then
    inner="$(find "$dir" -mindepth 1 -maxdepth 1 -type d)"
    [ -n "$inner" ] && { mv "$inner" "$dir.inner" && rmdir "$dir" && mv "$dir.inner" "$dir"; }
  fi
  echo "$dir"
}

built() { [ -f "$STAMPS/$1" ]; }
done_() { touch "$STAMPS/$1"; }

run_logged() {   # run_logged <name> <command...>: quiet unless it fails
  local log="$LOGS/$1.log"; shift
  if ! "$@" >>"$log" 2>&1; then
    echo "---- $log (last 60 lines) ----" >&2; tail -60 "$log" >&2; exit 1
  fi
}

meson_pkg() {   # meson_pkg <name> <archive> [meson options...]
  local name="$1" archive="$2"; shift 2
  built "$name" && return 0
  say "$name"
  : > "$LOGS/$name.log"
  local dir; dir="$(unpack "$archive")"
  run_logged "$name" meson setup "$dir/_build" "$dir" --prefix="$PREFIX" --libdir=lib \
      --buildtype=release -Ddefault_library=shared --wrap-mode=default \
      -Dpkg_config_path="$PKG_CONFIG_LIBDIR" -Dcmake_prefix_path="$PREFIX" "$@"
  run_logged "$name" ninja -C "$dir/_build" -j"$JOBS"
  run_logged "$name" ninja -C "$dir/_build" install
  done_ "$name"
}

cmake_pkg() {   # cmake_pkg <name> <archive> [cmake options...]
  local name="$1" archive="$2"; shift 2
  built "$name" && return 0
  say "$name"
  : > "$LOGS/$name.log"
  local dir; dir="$(unpack "$archive")"
  run_logged "$name" cmake -S "$dir" -B "$dir/_build" -G Ninja \
      -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_INSTALL_LIBDIR=lib \
      -DCMAKE_BUILD_TYPE=Release -DCMAKE_OSX_ARCHITECTURES="$ARCH" \
      -DCMAKE_OSX_DEPLOYMENT_TARGET="$MIN_MACOS" -DCMAKE_INSTALL_NAME_DIR="$PREFIX/lib" \
      -DBUILD_SHARED_LIBS=ON "$@"
  run_logged "$name" cmake --build "$dir/_build" -j "$JOBS"
  run_logged "$name" cmake --install "$dir/_build"
  done_ "$name"
}

configure_pkg() {   # configure_pkg <name> <archive> [configure options...]
  local name="$1" archive="$2"; shift 2
  built "$name" && return 0
  say "$name"
  : > "$LOGS/$name.log"
  local dir; dir="$(unpack "$archive")"
  (cd "$dir" && run_logged "$name" ./configure --prefix="$PREFIX" --disable-static "$@" \
     && run_logged "$name" make -j"$JOBS" && run_logged "$name" make install)
  done_ "$name"
}

# ------------------------------------------------------------------ sources
fetch() {   # fetch <url> <file>
  [ -s "$SRC/$2" ] || { say "downloading $2"; curl -fL --retry 3 -o "$SRC/$2" "$1"; }
}
mkdir -p "$SRC"
G=https://download.gnome.org/sources
fetch "https://github.com/PCRE2Project/pcre2/releases/download/pcre2-10.47/pcre2-10.47.tar.bz2" pcre2-10.47.tar.bz2
fetch "$G/glib/2.86/glib-2.86.4.tar.xz" glib-2.86.4.tar.xz
fetch "$G/gobject-introspection/1.86/gobject-introspection-1.86.0.tar.xz" gobject-introspection-1.86.0.tar.xz
fetch "https://download.sourceforge.net/libpng/libpng-1.6.55.tar.xz" libpng-1.6.55.tar.xz
fetch "https://github.com/libjpeg-turbo/libjpeg-turbo/releases/download/3.1.3/libjpeg-turbo-3.1.3.tar.gz" libjpeg-turbo-3.1.3.tar.gz
fetch "https://download.osgeo.org/libtiff/tiff-4.7.1.tar.xz" tiff-4.7.1.tar.xz
fetch "https://download.savannah.gnu.org/releases/freetype/freetype-2.14.2.tar.xz" freetype-2.14.2.tar.xz
fetch "https://gitlab.freedesktop.org/api/v4/projects/890/packages/generic/fontconfig/2.17.1/fontconfig-2.17.1.tar.xz" fontconfig-2.17.1.tar.xz
fetch "https://cairographics.org/releases/pixman-0.46.4.tar.gz" pixman-0.46.4.tar.gz
fetch "https://github.com/fribidi/fribidi/releases/download/v1.0.16/fribidi-1.0.16.tar.xz" fribidi-1.0.16.tar.xz
fetch "https://github.com/harfbuzz/harfbuzz/releases/download/12.3.2/harfbuzz-12.3.2.tar.xz" harfbuzz-12.3.2.tar.xz
fetch "https://cairographics.org/releases/cairo-1.18.4.tar.xz" cairo-1.18.4.tar.xz
fetch "$G/pango/1.57/pango-1.57.0.tar.xz" pango-1.57.0.tar.xz
fetch "$G/graphene/1.10/graphene-1.10.8.tar.xz" graphene-1.10.8.tar.xz
fetch "https://github.com/anholt/libepoxy/archive/refs/tags/1.5.10.tar.gz" libepoxy-1.5.10.tar.gz
fetch "$G/gdk-pixbuf/2.44/gdk-pixbuf-2.44.5.tar.xz" gdk-pixbuf-2.44.5.tar.xz
fetch "https://github.com/pantoniou/libfyaml/releases/download/v0.9/libfyaml-0.9.tar.gz" libfyaml-0.9.tar.gz
fetch "https://github.com/hughsie/libxmlb/releases/download/0.3.25/libxmlb-0.3.25.tar.xz" libxmlb-0.3.25.tar.xz
fetch "https://www.freedesktop.org/software/appstream/releases/AppStream-1.1.2.tar.xz" AppStream-1.1.2.tar.xz
fetch "$G/librsvg/2.62/librsvg-2.62.0.tar.xz" librsvg-2.62.0.tar.xz
fetch "$G/gtk/4.20/gtk-4.20.4.tar.xz" gtk-4.20.4.tar.xz
fetch "$G/libadwaita/1.8/libadwaita-1.8.4.tar.xz" libadwaita-1.8.4.tar.xz
fetch "$G/adwaita-icon-theme/49/adwaita-icon-theme-49.0.tar.xz" adwaita-icon-theme-49.0.tar.xz
fetch "https://files.pythonhosted.org/packages/source/p/pycairo/pycairo-1.29.0.tar.gz" pycairo-1.29.0.tar.gz
fetch "$G/pygobject/3.56/pygobject-3.56.0.tar.gz" pygobject-3.56.0.tar.gz

# --------------------------------------------------- libraries macOS provides
# zlib, bzip2, expat, libffi, libxml2 and curl are part of macOS itself, but
# come without pkg-config files; describe them so the builds link to the
# system's copies instead of building or bundling their own.
sdk_pc() {   # sdk_pc <name> <version> <cflags> <libs>
  cat > "$PREFIX/lib/pkgconfig/$1.pc" <<PC
Name: $1
Description: $1 from the macOS SDK
Version: $2
Cflags: $3
Libs: $4
PC
}
sdk_pc zlib 1.2.12 "" "-lz"
sdk_pc bzip2 1.0.8 "" "-lbz2"
sdk_pc expat 2.5.0 "" "-lexpat"
sdk_pc libffi 3.4.0 "-I$SDK/usr/include/ffi" "-lffi"
sdk_pc libxml-2.0 2.9.13 "-I$SDK/usr/include/libxml2" "-lxml2"
sdk_pc libcurl 8.7.1 "" "-lcurl"

# ------------------------------------------------------------------- build
cmake_pkg pcre2 pcre2-10.47.tar.bz2 -DBUILD_STATIC_LIBS=OFF -DPCRE2_BUILD_TESTS=OFF \
    -DPCRE2_BUILD_PCRE2GREP=OFF -DPCRE2_SUPPORT_JIT=ON -DPCRE2_SUPPORT_UNICODE=ON

GLIB_OPTS=(-Dtests=false -Dinstalled_tests=false -Ddocumentation=false -Dman-pages=disabled
           -Dsysprof=disabled -Dselinux=disabled -Dlibelf=disabled -Dnls=disabled)
# GLib's introspection data needs gobject-introspection, which needs GLib:
# GLib first without it, then again with it once the scanner exists.
meson_pkg glib glib-2.86.4.tar.xz "${GLIB_OPTS[@]}" -Dintrospection=disabled
meson_pkg gobject-introspection gobject-introspection-1.86.0.tar.xz \
    -Dpython="$PY" -Dcairo=disabled -Ddoctool=disabled -Dgtk_doc=false
meson_pkg glib-gir glib-2.86.4.tar.xz "${GLIB_OPTS[@]}" -Dintrospection=enabled

cmake_pkg libpng libpng-1.6.55.tar.xz -DPNG_STATIC=OFF -DPNG_TESTS=OFF -DPNG_TOOLS=OFF -DPNG_FRAMEWORK=OFF
cmake_pkg libjpeg-turbo libjpeg-turbo-3.1.3.tar.gz -DENABLE_STATIC=OFF -DWITH_TURBOJPEG=OFF \
    -DCMAKE_ASM_NASM_COMPILER=/opt/homebrew/bin/nasm
cmake_pkg libtiff tiff-4.7.1.tar.xz -Dtiff-tools=OFF -Dtiff-tests=OFF -Dtiff-docs=OFF \
    -Dtiff-contrib=OFF -Dcxx=OFF -Djpeg=ON -Dzlib=ON -Dlzma=OFF -Dzstd=OFF -Dwebp=OFF \
    -Djbig=OFF -Dlerc=OFF -Dlibdeflate=OFF -Dpixarlog=OFF
meson_pkg freetype freetype-2.14.2.tar.xz -Dharfbuzz=disabled -Dbrotli=disabled \
    -Dbzip2=disabled -Dpng=enabled -Dtests=disabled
meson_pkg fontconfig fontconfig-2.17.1.tar.xz -Ddoc=disabled -Dtests=disabled \
    -Dtools=disabled -Dcache-build=disabled -Dnls=disabled
meson_pkg pixman pixman-0.46.4.tar.gz -Dtests=disabled -Ddemos=disabled -Dgtk=disabled \
    -Dlibpng=disabled -Dopenmp=disabled
meson_pkg fribidi fribidi-1.0.16.tar.xz -Ddocs=false -Dtests=false -Dbin=false
meson_pkg harfbuzz harfbuzz-12.3.2.tar.xz -Dtests=disabled -Ddocs=disabled \
    -Dutilities=disabled -Dbenchmark=disabled -Dintrospection=enabled -Dglib=enabled \
    -Dgobject=enabled -Dfreetype=enabled -Dcoretext=enabled -Dcairo=disabled \
    -Dicu=disabled -Dchafa=disabled
meson_pkg cairo cairo-1.18.4.tar.xz -Dtests=disabled -Dquartz=enabled -Dxlib=disabled \
    -Dxcb=disabled -Dfreetype=enabled -Dfontconfig=enabled -Dpng=enabled -Dglib=enabled \
    -Dspectre=disabled -Dsymbol-lookup=disabled -Dlzo=disabled
meson_pkg pango pango-1.57.0.tar.xz -Dintrospection=enabled -Dfontconfig=enabled \
    -Dcairo=enabled -Dfreetype=enabled -Dbuild-testsuite=false -Dbuild-examples=false \
    -Ddocumentation=false -Dman-pages=false -Dlibthai=disabled -Dsysprof=disabled -Dxft=disabled
meson_pkg graphene graphene-1.10.8.tar.xz -Dtests=false -Dinstalled_tests=false \
    -Dintrospection=enabled
meson_pkg libepoxy libepoxy-1.5.10.tar.gz -Dtests=false -Ddocs=false -Dx11=false \
    -Degl=no -Dglx=no
meson_pkg gdk-pixbuf gdk-pixbuf-2.44.5.tar.xz -Dtests=false -Dinstalled_tests=false \
    -Dman=false -Ddocumentation=false -Dintrospection=enabled -Dglycin=disabled \
    -Dthumbnailer=disabled -Dpng=enabled -Djpeg=enabled -Dtiff=enabled -Dothers=enabled

# libadwaita reads app descriptions through AppStream, which needs these.
configure_pkg libfyaml libfyaml-0.9.tar.gz --disable-network
meson_pkg libxmlb libxmlb-0.3.25.tar.xz -Dgtkdoc=false -Dintrospection=false -Dtests=false \
    -Dcli=false -Dlzma=disabled -Dzstd=disabled
meson_pkg appstream AppStream-1.1.2.tar.xz -Dstemming=false -Dsystemd=false -Dvapi=false \
    -Dqt=false -Dcompose=false -Dbash-completion=false -Dapt-support=false -Dgir=false \
    -Dsvg-support=false -Dzstd-support=false -Ddocs=false -Dapidocs=false \
    -Dinstall-docs=false -Dman=false

# Icons are SVG; librsvg draws them (and is a Rust project).
meson_pkg librsvg librsvg-2.62.0.tar.xz -Dintrospection=enabled -Dpixbuf=enabled \
    -Dpixbuf-loader=enabled -Drsvg-convert=disabled -Ddocs=disabled -Dvala=disabled \
    -Dtests=false -Davif=disabled -Dtriplet="$TRIPLE"

meson_pkg gtk gtk-4.20.4.tar.xz -Dmacos-backend=true -Dx11-backend=false \
    -Dwayland-backend=false -Dbroadway-backend=false -Dmedia-gstreamer=disabled \
    -Dvulkan=disabled -Dprint-cups=disabled -Dprint-cpdb=disabled -Dcolord=disabled \
    -Dcloudproviders=disabled -Dsysprof=disabled -Dtracker=disabled -Daccesskit=disabled \
    -Dintrospection=enabled -Ddocumentation=false -Dscreenshots=false -Dman-pages=false \
    -Dbuild-demos=false -Dbuild-testsuite=false -Dbuild-examples=false -Dbuild-tests=false
meson_pkg libadwaita libadwaita-1.8.4.tar.xz -Dintrospection=enabled -Dvapi=false \
    -Ddocumentation=false -Dtests=false -Dexamples=false
# Every icon Piklin shows (trash, star, camera, menu...) comes from this theme;
# a Linux desktop has it installed, a Mac does not.
meson_pkg adwaita-icon-theme adwaita-icon-theme-49.0.tar.xz

# ------------------------------------------------------------- Python side
if ! built python-bindings; then
  say "pycairo and PyGObject"
  : > "$LOGS/python-bindings.log"
  rm -f "$WHEELS"/pycairo-*.whl "$WHEELS"/pygobject-*.whl
  # Built by this architecture's Python 3.14 against the libraries above.
  # Extension modules on macOS resolve Python's symbols when loaded, so these
  # work with the python.org Python the app carries.
  # Wheels are named for the macOS they target, not the Mac building them.
  export _PYTHON_HOST_PLATFORM="macosx-$MIN_MACOS-$ARCH"
  run_logged python-bindings "$PY" -m pip wheel --no-deps --no-cache-dir \
      "$SRC/pycairo-1.29.0.tar.gz" -w "$WHEELS"
  run_logged python-bindings "$PY" -m pip wheel --no-deps --no-cache-dir \
      --find-links "$WHEELS" "$SRC/pygobject-3.56.0.tar.gz" -w "$WHEELS"
  # rawpy publishes no wheel for Intel Macs: build it, with its LibRaw, here.
  if [ "$ARCH" = x86_64 ]; then
    RAWPY="$(sed -n 's/.*rawpy==\([0-9.]*\).*/\1/p' "$(dirname "$HERE")/build-deb.sh")"
    rm -f "$WHEELS"/rawpy-*.whl
    run_logged python-bindings "$PY" -m pip wheel --no-deps --no-cache-dir \
        --no-binary rawpy "rawpy==$RAWPY" -w "$WHEELS"
    # Its LibRaw uses Little CMS, JasPer and libjpeg from Intel Homebrew:
    # carried inside the wheel, as rawpy's own Apple Silicon wheel carries
    # them - but only if they run on the oldest macOS Piklin supports.
    for lib in /usr/local/opt/little-cms2/lib/liblcms2.2.dylib \
               /usr/local/opt/jasper/lib/libjasper.7.dylib \
               /usr/local/opt/jpeg-turbo/lib/libjpeg.8.dylib; do
      minos="$(otool -l "$lib" | awk '/LC_BUILD_VERSION/{b=1} b&&/minos/{print $2; exit}')"
      if [ "$(printf '%s\n%s\n' "$minos" "$MIN_MACOS" | sort -V | tail -1)" != "$MIN_MACOS" ]; then
        echo "$lib needs macOS $minos, newer than $MIN_MACOS - refusing to bundle it" >&2; exit 1
      fi
    done
    run_logged python-bindings "$PY" -m pip install --quiet delocate
    raw="$(ls "$WHEELS"/rawpy-*.whl)"
    rm -rf "$WORK/rawpy-delocated"
    run_logged python-bindings "$CACHE/$ARCH/buildpy/bin/delocate-wheel" \
        --require-archs "$ARCH" -w "$WORK/rawpy-delocated" "$raw"
    mv -f "$WORK"/rawpy-delocated/rawpy-*.whl "$raw"
  fi
  unset _PYTHON_HOST_PLATFORM
  done_ python-bindings
fi

# OpenCV's wheel for Apple Silicon Macs carries an FFmpeg built with x264 and
# x265 (GPL), which Piklin cannot ship. Build it here without FFmpeg - video
# goes through PyAV and Piklin's own LGPL FFmpeg, as on an Intel Mac, whose
# OpenCV wheel has no FFmpeg at all - and without formats Piklin never asks
# OpenCV to read, so it links to nothing but macOS.
if [ "$ARCH" = arm64 ] && ! built opencv; then
  say "OpenCV without FFmpeg"
  : > "$LOGS/opencv.log"
  OPENCV="$(sed -n 's/.*opencv-python-headless==\([0-9.]*\).*/\1/p' "$(dirname "$HERE")/build-deb.sh")"
  rm -f "$WHEELS"/opencv_python_headless-*.whl
  # In a bare environment - nothing of this script's compiler flags, search
  # paths or tools - which is the only way OpenCV's build recognised this Mac
  # as arm64 (with them it built Intel assembly). Homebrew stays out because
  # every optional library it could offer is switched off.
  run_logged opencv env -i HOME="$HOME" PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin" \
      MACOSX_DEPLOYMENT_TARGET="$MIN_MACOS" _PYTHON_HOST_PLATFORM="macosx-$MIN_MACOS-$ARCH" \
      ENABLE_HEADLESS=1 ENABLE_CONTRIB=0 \
      CMAKE_ARGS="-DWITH_FFMPEG=OFF -DWITH_GSTREAMER=OFF -DWITH_AVIF=OFF -DWITH_OPENEXR=OFF -DWITH_WEBP=OFF -DWITH_OPENJPEG=OFF -DWITH_JASPER=OFF -DWITH_TIFF=OFF -DBUILD_PNG=ON -DBUILD_JPEG=ON -DBUILD_ZLIB=ON -DBUILD_TESTS=OFF -DBUILD_PERF_TESTS=OFF" \
      "$PY" -m pip wheel --no-deps --no-cache-dir --no-binary opencv-python-headless \
      "opencv-python-headless==$OPENCV" -w "$WHEELS"
  # Nothing but macOS itself may be linked.
  check="$(mktemp -d)"
  unzip -q "$(ls "$WHEELS"/opencv_python_headless-*.whl | head -1)" -d "$check"
  if otool -L "$check"/cv2/cv2*.so | tail -n +2 | grep -vqE "/usr/lib/|/System/|@loader_path" \
      || find "$check" -iname '*x26*' -o -iname '*avcodec*' | grep -q .; then
    echo "OpenCV links to libraries outside macOS, or to FFmpeg - refusing to use it" >&2; exit 1
  fi
  rm -rf "$check"
  done_ opencv
fi

# ----------------------------------------------------------------- checks
say "Checking the result"
bad=0
while IFS= read -r f; do
  if otool -L "$f" 2>/dev/null | tail -n +2 | grep -qE "/opt/homebrew|/usr/local"; then
    echo "links to Homebrew: $f" >&2; bad=1
  fi
  minos="$(otool -l "$f" | awk '/LC_BUILD_VERSION/{b=1} b&&/minos/{print $2; exit}')"
  if [ -n "$minos" ] && [ "$(printf '%s\n%s\n' "$minos" "$MIN_MACOS" | sort -V | tail -1)" != "$MIN_MACOS" ]; then
    echo "needs macOS $minos: $f" >&2; bad=1
  fi
done < <(find "$PREFIX/lib" -type f \( -name '*.dylib' -o -name '*.so' \))
[ "$bad" = 0 ] || { echo "stack is not self-contained - see above" >&2; exit 1; }
say "Done: $PREFIX"
