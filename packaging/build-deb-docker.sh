#!/usr/bin/env bash
# Build the Piklin .deb for amd64 or arm64 inside a clean Ubuntu 24.04
# container, on any machine with Docker - the same steps as a native build:
# build-media.sh (FFmpeg and PyAV without GPL code), then build-deb.sh.
#
#   packaging/build-deb-docker.sh arm64
#   packaging/build-deb-docker.sh amd64
#
# The architecture of the host runs natively; the other one runs under
# emulation and takes much longer (FFmpeg especially).
#
# Output: dist/piklin_<version>_<arch>.deb
set -euo pipefail

ARCH="${1:?usage: build-deb-docker.sh amd64|arm64}"
case "$ARCH" in
  amd64) MANYLINUX=quay.io/pypa/manylinux_2_28_x86_64;  WHEEL_ARCH=x86_64 ;;
  arm64) MANYLINUX=quay.io/pypa/manylinux_2_28_aarch64; WHEEL_ARCH=aarch64 ;;
  *) echo "unsupported architecture: $ARCH" >&2; exit 2 ;;
esac
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
OWNER="$(id -u):$(id -g)"
say() { printf '\033[1;36m==>\033[0m [%s] %s\n' "$ARCH" "$*"; }

# miniaudio publishes no arm64 Linux wheels: build them for each Python the
# .deb supports, in the manylinux image that carries all of them.
if [ "$ARCH" = arm64 ] && ! ls "$HERE/wheel-local-$WHEEL_ARCH"/miniaudio-*cp314*.whl >/dev/null 2>&1; then
  say "miniaudio wheels for Python 3.12, 3.13, 3.14"
  docker run --rm --platform "linux/$ARCH" -v "$ROOT":/src "$MANYLINUX" bash -euc "
    for v in cp312-cp312 cp313-cp313 cp314-cp314; do
      /opt/python/\$v/bin/pip wheel --quiet --no-deps miniaudio==1.71 -w /tmp/raw
    done
    for w in /tmp/raw/*.whl; do auditwheel repair \"\$w\" -w /src/packaging/wheel-local-$WHEEL_ARCH >/dev/null; done
    chown -R $OWNER /src/packaging/wheel-local-$WHEEL_ARCH"
fi

say "FFmpeg, PyAV and the .deb in Ubuntu 24.04"
docker run --rm --platform "linux/$ARCH" -v "$ROOT":/src -w /src \
    -e MAINTAINER -e ARCH="$ARCH" -e DEBIAN_FRONTEND=noninteractive ubuntu:24.04 bash -euc "
  apt-get update -qq
  apt-get install -y -qq --no-install-recommends python3-venv python3-pip python3-dev \
      python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 build-essential nasm meson \
      ninja-build cmake patchelf pkg-config curl ca-certificates xz-utils dpkg-dev git >/dev/null
  git config --global --add safe.directory /src
  python3 -m venv --system-site-packages /tmp/venv
  export PATH=/tmp/venv/bin:\$PATH PY=/tmp/venv/bin/python
  trap 'chown -R $OWNER /src/dist /src/packaging 2>/dev/null || true' EXIT
  packaging/build-media.sh
  packaging/build-deb.sh"
say "Done: $(ls "$ROOT"/dist/piklin_*_"$ARCH".deb 2>/dev/null | tail -1)"
