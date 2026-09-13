<p align="center">
  <img src="data/icons/piklin-256.png" width="128" height="128" alt="Piklin icon">
</p>

<h1 align="center">Piklin</h1>

<p align="center">
  <strong>A free and private photo and video library for Linux.</strong><br>
  Organize, edit and share your memories. They never leave your computer.
</p>

<p align="center">
  <a href="https://github.com/vezzulab/Piklin/releases"><img src="https://img.shields.io/badge/download-.deb-0b0b0b?style=flat-square" alt="Download"></a>
  <img src="https://img.shields.io/badge/platform-Linux-0b0b0b?style=flat-square" alt="Platform: Linux">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-PolyForm%20Strict-0b0b0b?style=flat-square" alt="License: PolyForm Strict"></a>
  <a href="https://ko-fi.com/vezzustudio"><img src="https://img.shields.io/badge/support-Ko--fi-0b0b0b?style=flat-square" alt="Support on Ko-fi"></a>
</p>

<p align="center">
  <img src="docs/screenshots/library.png" alt="The Piklin library" width="900">
</p>

## Features

### A library that stays yours

- **One package for everything.** Your library is a single `Piklin Library.piklin` package. Move it to another disk, copy it to a new computer and open it again.
- **Years, Months, Days or All Photos.** Filters and search cover favourites, videos, screenshots, location and more.
- **Albums, folders and smart albums.** Smart albums fill themselves from rules such as camera, date, rating, media type or video length.
- **Import from cameras and cards.** Drag photos straight onto an album. Duplicates are recognised, and photos can be stored smaller without visible loss.
- **Useful views.** Recently Deleted keeps items for 30 days; Duplicates, Hidden and Imports are one click away.

### Editing without fear

- **28 professional tools and Looks.** Tune, curves, selective adjustments, healing, portrait tools and more.
- **Non-destructive.** Your original file is never changed, and every edit can be undone.
- **Export your way.** JPEG, WebP, AVIF or PNG, at the size you choose, with or without location and metadata.

<p align="center">
  <img src="docs/screenshots/editor.png" alt="Editing a photo" width="900">
</p>

### Video, built in

- **Plays everything.** MP4, MOV, MKV, WebM, AVI, MTS and more, with sound, at speeds from 0.25× to 2× and frame by frame.
- **Previews in the grid.** Rest the pointer on a video and it plays silently.
- **Edit precisely.** Trim the ends, cut pieces out of the middle, rotate, flip, crop, change the speed or mute.
- **Save any frame** as a full-resolution photo.
- **Export** as MP4 (H.264), WebM (VP9) or an animated GIF, from 480p to 4K.
- **Live photos** appear as one item and play with a click.

<p align="center">
  <img src="docs/screenshots/video-editor.png" alt="Editing a video" width="900">
</p>

### Private by design

- No accounts, no ads, no analytics, no telemetry.
- Photos, videos, edits and face detection stay on your computer.
- Piklin connects to the internet only when you set up a backup destination yourself.

## Install

Download the latest `.deb` from **[Releases](https://github.com/vezzulab/Piklin/releases)**, then install it:

```bash
sudo apt install ./piklin_1.0.0_amd64.deb
```

Piklin brings everything it needs for photos, video and sound. It uses your system's GTK 4 and libadwaita.

**Supported systems:** Ubuntu 24.04 or newer, Linux Mint 22 or newer, Debian 13 or newer, and distributions based on them, on 64-bit PCs (x86-64).

Your library is created in your Pictures folder the first time you open Piklin.

## Keyboard shortcuts

| Action | Keys |
|---|---|
| Years / Months / Days / All Photos | <kbd>Ctrl</kbd> + <kbd>1</kbd> … <kbd>4</kbd> |
| Open, or play and pause a video | <kbd>Space</kbd> |
| Edit | <kbd>Return</kbd> |
| Favourite | <kbd>.</kbd> |
| Rename | <kbd>F2</kbd> |
| Hide | <kbd>Ctrl</kbd> + <kbd>L</kbd> |
| Export | <kbd>Ctrl</kbd> + <kbd>E</kbd> |
| Search | <kbd>Ctrl</kbd> + <kbd>F</kbd> |
| Full screen | <kbd>F11</kbd> |
| Previous / next video frame | <kbd>,</kbd> / <kbd>.</kbd> (in a video) |
| Trim start / end at the playhead | <kbd>I</kbd> / <kbd>O</kbd> (video editor) |

## Build from source

The PolyForm Strict License lets you build Piklin for your own use; see [License](#license) for what it doesn't allow. On Ubuntu 24.04 or Linux Mint 22:

```bash
sudo apt install python3-venv python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 \
                 build-essential nasm meson ninja-build cmake patchelf pkg-config curl
python3 -m venv --system-site-packages .venv
.venv/bin/pip install numpy pillow pi-heif rawpy opencv-python-headless miniaudio auditwheel

packaging/build-media.sh      # builds FFmpeg and PyAV once, without GPL components
./run.sh                      # run from the source tree
packaging/build-deb.sh        # build the .deb in dist/
```

## Support Piklin

Piklin is free. If it helps you, you can **[buy us a coffee on Ko-fi](https://ko-fi.com/vezzustudio)** ☕. It helps us keep improving Piklin.

Found a bug or have an idea? [Open an issue](https://github.com/vezzulab/Piklin/issues/new/choose).

## License

Piklin is © 2026 [Vezzu Studio](https://vezzu.studio) and is source-available under the **[PolyForm Strict License 1.0.0](LICENSE)**.

- You may use Piklin for free and read its source code.
- You may not modify, redistribute or sell Piklin or works based on it.

Third-party components keep their own licences; see [NOTICE.md](NOTICE.md).

Piklin and the Piklin logo are trademarks of Vezzu Studio.

---

<p align="center">Made with care by <a href="https://vezzu.studio">Vezzu Studio</a> in Boston.</p>
