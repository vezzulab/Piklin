# Third-party software

Piklin is © 2026 Vezzu Studio and licensed under the [Piklin License 1.0](LICENSE).

The Piklin packages include the software below. Each part keeps its own licence, and those licences apply to that part only. The full licence texts are installed with Piklin: on Linux in `/usr/share/doc/piklin/third-party/`, on a Mac inside the app in `Piklin.app/Contents/Resources/third-party/`.

| Component | Used for | Licence |
|---|---|---|
| [FFmpeg](https://ffmpeg.org) 8.0 | Reading and writing video and audio | LGPL-2.1-or-later |
| [OpenH264](https://github.com/cisco/openh264) | H.264 video export | BSD-2-Clause |
| [libvpx](https://chromium.googlesource.com/webm/libvpx) | VP8 and VP9 video | BSD-3-Clause |
| [Opus](https://opus-codec.org) | Opus audio | BSD-3-Clause |
| [dav1d](https://code.videolan.org/videolan/dav1d) | AV1 video decoding | BSD-2-Clause |
| [SVT-AV1](https://gitlab.com/AOMediaCodec/SVT-AV1) | AV1 video encoding, the smallest videos | BSD-3-Clause-Clear, with the AOMedia patent licence |
| [PyAV](https://github.com/PyAV-Org/PyAV) | Python access to FFmpeg | BSD-3-Clause |
| [miniaudio](https://miniaud.io) / [pyminiaudio](https://github.com/irmen/pyminiaudio) | Sound output | Public domain or MIT-0 / MIT |
| [OpenCV](https://opencv.org) | Image processing and face detection | Apache-2.0 |
| [YuNet face detector](https://github.com/opencv/opencv_zoo) | Portrait tools | MIT |
| [NumPy](https://numpy.org) | Image processing | BSD-3-Clause |
| [Pillow](https://python-pillow.org) | Image formats | MIT-CMU |
| [pi-heif](https://github.com/bigcat88/pillow_heif) with libheif and libde265 | HEIC and HEIF photos | BSD-3-Clause; LGPL-3.0 |
| [rawpy](https://github.com/letmaik/rawpy) with LibRaw | Camera RAW photos | MIT; LGPL-2.1 or CDDL-1.0 |
| [Inter](https://rsms.me/inter/) | Typeface | SIL Open Font License 1.1 |
| [cffi](https://cffi.readthedocs.io) | Sound output (on a Mac; Linux provides it) | MIT |
| [rclone](https://rclone.org) | Backups to cloud services (Google Drive, OneDrive, Dropbox, pCloud and more) | MIT |

### On a Mac only

On Linux, Piklin uses the Python, GTK and libraries of the system. The Mac app carries its own:

| Component | Used for | Licence |
|---|---|---|
| [Python](https://www.python.org) 3.14 (python.org) | Runs Piklin | PSF License |
| [GTK 4](https://gtk.org), [libadwaita](https://gnome.pages.gitlab.gnome.org/libadwaita/), [GLib](https://gitlab.gnome.org/GNOME/glib), [Pango](https://pango.gnome.org), [gdk-pixbuf](https://gitlab.gnome.org/GNOME/gdk-pixbuf), [graphene](https://github.com/ebassi/graphene), [AppStream](https://www.freedesktop.org/wiki/Distributions/AppStream/), [libxmlb](https://github.com/hughsie/libxmlb) | The interface | LGPL-2.1-or-later |
| [cairo](https://cairographics.org), [pixman](https://pixman.org) | Drawing | LGPL-2.1 or MPL-1.1; MIT |
| [HarfBuzz](https://harfbuzz.github.io), [FreeType](https://freetype.org), [Fontconfig](https://www.freedesktop.org/wiki/Software/fontconfig/), [FriBidi](https://github.com/fribidi/fribidi) | Text | MIT; FTL; MIT; LGPL-2.1-or-later |
| [librsvg](https://gitlab.gnome.org/GNOME/librsvg) | Icons | LGPL-2.1-or-later |
| [Adwaita icon theme](https://gitlab.gnome.org/GNOME/adwaita-icon-theme) | Icons | LGPL-3.0 or CC-BY-SA-3.0 |
| [libpng](http://www.libpng.org), [libjpeg-turbo](https://libjpeg-turbo.org), [LibTIFF](https://libtiff.gitlab.io/libtiff/), [PCRE2](https://pcre.org), [libepoxy](https://github.com/anholt/libepoxy), [libfyaml](https://github.com/pantoniou/libfyaml) | Libraries the interface uses | libpng; BSD-3-Clause and IJG; libtiff; BSD-3-Clause; MIT; MIT |
| [PyGObject](https://pygobject.gnome.org), [pycairo](https://pycairo.readthedocs.io) | Python access to GTK and cairo | LGPL-2.1-or-later |
| [PyObjC](https://pyobjc.readthedocs.io) | iPhones, iPads and cameras through Image Capture | MIT |
| [certifi](https://github.com/certifi/python-certifi) | The certificates that secure connections (updates, backups) | MPL-2.0 |

These libraries are separate shared libraries inside `Piklin.app/Contents/Resources/runtime-arm64` and `runtime-x86_64`, and may be replaced with your own builds. Their source code is available from the projects above.

## FFmpeg

FFmpeg is built by `packaging/build-media.sh` without any GPL or non-free component. The script stops unless FFmpeg's own configuration reports "LGPL version 2.1 or later". The FFmpeg libraries ship as separate shared libraries and can be replaced with your own build. The corresponding source code is available from [ffmpeg.org](https://ffmpeg.org/releases/). The exact build configuration is installed with Piklin.

## System libraries

Piklin runs on the GTK 4, libadwaita, GLib and Python provided by your distribution. They are not part of the Piklin package.

## Screenshots

The pictures and the video in the screenshots were created by Vezzu Studio for this purpose. They contain no third-party content.
