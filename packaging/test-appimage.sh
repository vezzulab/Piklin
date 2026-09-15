#!/usr/bin/env bash
# Check that an AppImage starts on other Linux systems, not just the Ubuntu
# it was built in: each clean container gets only what every desktop has
# (fonts, X11/Wayland, graphics, sound), then runs the AppImage's own check.
#
#   packaging/test-appimage.sh dist/Piklin-1.0.5-aarch64.AppImage
#   packaging/test-appimage.sh dist/Piklin-1.0.5-x86_64.AppImage
set -euo pipefail

[ -f "${1:?usage: test-appimage.sh path/to/Piklin-VERSION-ARCH.AppImage}" ] \
  || { echo "No such AppImage: $1" >&2; exit 2; }
IMAGE="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
case "$IMAGE" in
  *-x86_64.AppImage)  PLATFORM=linux/amd64 ;;
  *-aarch64.AppImage) PLATFORM=linux/arm64 ;;
  *) echo "not a Piklin AppImage: $IMAGE" >&2; exit 2 ;;
esac

DEBIAN_DESKTOP="libfontconfig1 libfreetype6 libharfbuzz0b libfribidi0 zlib1g libexpat1 libx11-6 \
  libxcb1 libegl1 libgl1 libwayland-client0 libgmp10 libcom-err2 libgpg-error0 libgcc-s1 \
  libstdc++6 libuuid1"
FEDORA_DESKTOP="fontconfig freetype harfbuzz fribidi zlib expat libX11 libxcb mesa-libEGL \
  mesa-libGL libglvnd-glx libwayland-client gmp libcom_err libgpg-error libgcc libstdc++ libuuid"

status=0
for distro in ubuntu:24.04 debian:trixie fedora:40 fedora:42; do
  case "$distro" in
    fedora*) prepare="dnf install -y -q $FEDORA_DESKTOP alsa-lib >/dev/null 2>&1" ;;
    debian*) prepare="apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $DEBIAN_DESKTOP libasound2t64 >/dev/null 2>&1" ;;
    *)       prepare="apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $DEBIAN_DESKTOP libasound2t64 >/dev/null 2>&1" ;;
  esac
  printf '%-15s ' "$distro"
  # A copy with the AppImage marker bytes cleared: an emulated architecture
  # refuses to start the file otherwise (see build-appimage.sh).
  if out="$(docker run --rm --platform "$PLATFORM" -v "$IMAGE":/image/Piklin.AppImage:ro \
        -e PIKLIN_BOOT_CHECK=1 "$distro" bash -c "$prepare
          cp /image/Piklin.AppImage /tmp/Piklin.AppImage
          printf '\0\0\0' | dd of=/tmp/Piklin.AppImage bs=1 seek=8 conv=notrunc status=none
          cd /tmp && ./Piklin.AppImage --appimage-extract >/dev/null && squashfs-root/AppRun" 2>&1)"; then
    echo "$out" | tail -1
  else
    echo "FAILED"; echo "$out" | tail -15 | sed 's/^/    /'; status=1
  fi
done
exit $status
