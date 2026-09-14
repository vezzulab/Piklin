# Third-party software

Piklin is © 2026 Vezzu Studio and licensed under the [Piklin License 1.0](LICENSE).

The Piklin package includes the software below. Each part keeps its own licence, and those licences apply to that part only. The full licence texts are installed with Piklin in `/usr/share/doc/piklin/third-party/`.

| Component | Used for | Licence |
|---|---|---|
| [FFmpeg](https://ffmpeg.org) 8.0 | Reading and writing video and audio | LGPL-2.1-or-later |
| [OpenH264](https://github.com/cisco/openh264) | H.264 video export | BSD-2-Clause |
| [libvpx](https://chromium.googlesource.com/webm/libvpx) | VP8 and VP9 video | BSD-3-Clause |
| [Opus](https://opus-codec.org) | Opus audio | BSD-3-Clause |
| [dav1d](https://code.videolan.org/videolan/dav1d) | AV1 video decoding | BSD-2-Clause |
| [PyAV](https://github.com/PyAV-Org/PyAV) | Python access to FFmpeg | BSD-3-Clause |
| [miniaudio](https://miniaud.io) / [pyminiaudio](https://github.com/irmen/pyminiaudio) | Sound output | Public domain or MIT-0 / MIT |
| [OpenCV](https://opencv.org) | Image processing and face detection | Apache-2.0 |
| [YuNet face detector](https://github.com/opencv/opencv_zoo) | Portrait tools | MIT |
| [NumPy](https://numpy.org) | Image processing | BSD-3-Clause |
| [Pillow](https://python-pillow.org) | Image formats | MIT-CMU |
| [pi-heif](https://github.com/bigcat88/pillow_heif) with libheif and libde265 | HEIC and HEIF photos | BSD-3-Clause; LGPL-3.0 |
| [rawpy](https://github.com/letmaik/rawpy) with LibRaw | Camera RAW photos | MIT; LGPL-2.1 or CDDL-1.0 |
| [Inter](https://rsms.me/inter/) | Typeface | SIL Open Font License 1.1 |

## FFmpeg

FFmpeg is built by `packaging/build-media.sh` without any GPL or non-free component. The script stops unless FFmpeg's own configuration reports "LGPL version 2.1 or later". The FFmpeg libraries ship as separate shared libraries and can be replaced with your own build. The corresponding source code is available from [ffmpeg.org](https://ffmpeg.org/releases/). The exact build configuration is installed with Piklin.

## System libraries

Piklin runs on the GTK 4, libadwaita, GLib and Python provided by your distribution. They are not part of the Piklin package.

## Screenshots

The pictures and the video in the screenshots were created by Vezzu Studio for this purpose. They contain no third-party content.
