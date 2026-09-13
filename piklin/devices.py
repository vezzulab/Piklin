"""Cameras, phones and memory cards.

Connecting a camera should make it appear in the sidebar and let you
look through what is on it before anything is copied - the device is a
place you browse, not part of your library. Nothing on the card is
indexed into the catalog: photos only enter the library when you
explicitly import them - browsing a card is kept apart from the
library proper.

Detection goes through GIO's VolumeMonitor rather than udev directly, so
one code path covers all three shapes a camera actually arrives in:

* a card reader or a camera in "mass storage" mode - an ordinary
  filesystem mount with a DCIM folder on it
* a phone or camera over MTP/PTP - a gvfs mount (mtp:// or gphoto2://)
  that GIO presents like any other
* a card that the desktop has detected but not yet mounted, which we can
  mount on demand
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import imageio as iio

# Where cameras put pictures. DCIM is the one the standard actually
# mandates; the others are what phones do in practice.
PHOTO_DIRS = ("DCIM", "Pictures", "PICTURES", "Camera", "CAMERA", "Photos")


@dataclass
class Device:
    """A connected camera, phone or card, as the sidebar shows it."""
    id: str
    name: str
    path: Path | None            # None for MTP/PTP mounts with no local path
    uri: str
    icon: str = "camera-photo-symbolic"
    kind: str = "storage"        # storage | camera | phone
    mount: object = field(default=None, repr=False)

    @property
    def available(self) -> bool:
        return self.path is not None and self.path.is_dir()


def _looks_like_camera(root: Path) -> bool:
    """A volume is a camera if it carries a picture folder."""
    try:
        for name in PHOTO_DIRS:
            if (root / name).is_dir():
                return True
    except OSError:
        pass
    return False


def _is_external(mount, path: Path) -> bool:
    """A USB stick or an external disk that the desktop mounted for the user.

    Such a drive holds photos in folders of any name - "Golden_Hour_Upload",
    "Trip 2024" - so it is offered whether or not it looks like a camera.
    """
    try:
        drive = mount.get_drive()
        if drive is not None and drive.is_removable():
            return True
    except Exception:
        pass
    return str(path).startswith(("/media/", "/run/media/"))


# Folders on a drive that never hold the user's photos.
_SKIP_DIRS = {"$RECYCLE.BIN", "System Volume Information", "lost+found"}


def _skip_dir(name: str) -> bool:
    return name.startswith(".") or name in _SKIP_DIRS or name.endswith(".piklin")


def _quick_record(full: Path, st) -> dict:
    """What is known about a file without opening it."""
    return {
        "path": str(full), "filename": full.name,
        "ext": full.suffix.lower().lstrip("."),
        "bytes": st.st_size, "mtime": st.st_mtime,
        # The file's own date is the best guess available without opening
        # it; the real EXIF date is read at import.
        "taken_at": st.st_mtime, "date_source": "mtime",
        "width": 0, "height": 0, "orientation": 1,
    }


def _classify(mount) -> tuple[str, str]:
    uri = ""
    try:
        uri = mount.get_root().get_uri() or ""
    except Exception:
        pass
    if uri.startswith("gphoto2://"):
        return "camera", "camera-photo-symbolic"
    if uri.startswith("mtp://"):
        return "phone", "phone-symbolic"
    return "storage", "media-flash-symbolic"


def list_devices() -> list[Device]:
    """Every connected device that looks like it holds photos."""
    monitor = Gio.VolumeMonitor.get()
    devices: list[Device] = []
    seen: set[str] = set()

    for mount in monitor.get_mounts():
        try:
            root = mount.get_root()
            uri = root.get_uri() or ""
            local = root.get_path()
        except Exception:
            continue
        if uri in seen:
            continue

        kind, icon = _classify(mount)
        path = Path(local) if local else None

        # A plain filesystem mount counts when it is a card with a picture
        # folder, or a USB stick or external disk - never the system's own
        # disks, which the desktop does not list as mounts for the user.
        name = mount.get_name() or "Camera"
        if kind == "storage":
            if path is None:
                continue
            if not _looks_like_camera(path):
                if not _is_external(mount, path):
                    continue
                kind, icon = "drive", "drive-removable-media-symbolic"
                name = mount.get_name() or "USB Drive"

        seen.add(uri)
        devices.append(Device(
            id=uri, name=name, path=path, uri=uri,
            icon=icon, kind=kind, mount=mount))
    return devices


def photo_dirs(device: Device) -> list[Path]:
    """The folders on the device worth looking in."""
    if device.path is None:
        return []
    found = [device.path / name for name in PHOTO_DIRS
             if (device.path / name).is_dir()]
    # Some cameras write straight to the root of the card.
    return found or [device.path]


def scan_device(device: Device, limit: int = 5000,
                read_metadata: bool = False) -> list[dict]:
    """Read what is on the device, without touching the catalog.

    By default this only lists the files - name, size, modification time
    - which is a directory read and returns instantly. Opening each file
    to parse its EXIF costs about two thirds of a second *per photo*
    over USB (measured on a camera over gphoto2), so a card of 82 photos
    would sit blank for the best part of a minute, and a full card for
    twenty minutes. Pass ``read_metadata=True`` only where that cost is
    worth paying - at import, when the file is being copied anyway.
    """
    exts = iio.supported_extensions()
    records: list[dict] = []
    for base in photo_dirs(device):
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if not _skip_dir(d))
            for name in sorted(filenames):
                if name.startswith("."):
                    continue
                if os.path.splitext(name)[1].lower() not in exts:
                    continue
                full = Path(dirpath) / name
                if read_metadata:
                    rec = iio.probe(full)
                else:
                    try:
                        st = full.stat()
                    except OSError:
                        continue
                    rec = _quick_record(full, st)
                if rec:
                    records.append(rec)
                if len(records) >= limit:
                    return records
    return records


def files_from_paths(paths, limit: int = 20000) -> list[dict]:
    """The photos and videos among files and folders dropped on the window.

    Folders are looked through, with their subfolders; anything that is not
    a photo or a video is left out. Returns import records, like
    ``scan_device``.
    """
    exts = iio.supported_extensions()
    records: list[dict] = []
    seen: set[str] = set()

    def add(full: Path) -> None:
        if full.name.startswith(".") or full.suffix.lower() not in exts:
            return
        key = str(full)
        if key in seen:
            return
        try:
            st = full.stat()
        except OSError:
            return
        seen.add(key)
        records.append(_quick_record(full, st))

    for item in paths:
        item = Path(item)
        if item.is_dir():
            for dirpath, dirnames, filenames in os.walk(item):
                dirnames[:] = sorted(d for d in dirnames if not _skip_dir(d))
                for name in sorted(filenames):
                    add(Path(dirpath) / name)
                    if len(records) >= limit:
                        return records
        elif item.is_file():
            add(item)
        if len(records) >= limit:
            break
    return records


def already_imported(catalog, records: list[dict]) -> set[str]:
    """Which of these are already in the library, by content fingerprint.

    Matching on content rather than filename is what makes re-importing
    a card safe: cameras reuse names like IMG_0001.JPG on every card, and
    a photo already imported should not come in twice just because it
    still sits on the card.
    """
    prints = [r.get("fingerprint") for r in records if r.get("fingerprint")]
    if not prints:
        return set()
    known: set[str] = set()
    chunk = 400
    for i in range(0, len(prints), chunk):
        part = prints[i:i + chunk]
        rows = catalog.q(
            "SELECT fingerprint FROM photos WHERE fingerprint IN "
            f"({','.join('?' * len(part))})", part)
        known.update(r["fingerprint"] for r in rows)
    return known


_SOURCE_SIZE_XATTR = "user.pikalicious.source_size"


def _source_size_of(dest: Path) -> int | None:
    """The size of the camera file a library copy was made from, recorded
    on the copy when it was compressed on import."""
    try:
        return int(os.getxattr(dest, _SOURCE_SIZE_XATTR).decode())
    except (OSError, ValueError, AttributeError):
        return None


def _compress_video(src: Path, dest: Path, rec: dict) -> Path | None:
    """A smaller H.264 copy of a camera video, or None to keep the
    original - when encoding fails, or would not save at least a tenth."""
    from . import video_edit as ve
    from .video import stream_info
    target = dest.with_suffix(".mp4")
    n = 2
    while target.exists():
        target = dest.with_name(f"{dest.stem}-{n}.mp4")
        n += 1
    try:
        duration = float(rec.get("duration") or 0.0) or \
            float((stream_info(src) or {}).get("duration") or 0.0)
        location = ((rec["gps_lat"], rec["gps_lon"])
                    if rec.get("gps_lat") is not None else None)
        out = ve.export(src, target, ve.VideoEdit(duration=duration), fmt="mp4",
                        keep_location=True,
                        meta={"taken_at": rec.get("taken_at"), "location": location})
    except Exception:
        return None
    if out.stat().st_size >= src.stat().st_size * 0.9:
        out.unlink(missing_ok=True)
        return None
    st = src.stat()
    os.utime(out, (st.st_atime, st.st_mtime))
    return out


def import_photos(library, records: list[dict], on_progress=None,
                  profile: str = "original",
                  video_profile: str = "original") -> dict:
    """Copy photos off the device into the library, filed by date.

    Files are copied, never moved: taking a photo off someone's camera
    by removing it from the card is not a decision this should make.
    """
    import shutil
    from datetime import datetime

    copied, skipped, failed = [], 0, 0
    # The camera files that are now safely in the library (copied, or
    # already there byte for byte) - the only ones "Delete Items" may
    # ever remove from the card.
    done_sources = []
    # Where each of those now lives in the library, copied or already there
    # - what an album drop needs in order to file them.
    placed = []
    total = len(records)
    for i, rec in enumerate(records):
        src = Path(rec["path"])
        # Filing by date means the date the shutter fired, not the file's
        # timestamp - so if this record came from the fast listing, read
        # the real EXIF now, while the file is about to be opened anyway.
        if rec.get("date_source") == "mtime":
            full = iio.probe(src)
            if full:
                rec = full
        ts = rec.get("taken_at") or 0
        try:
            day = datetime.fromtimestamp(ts)
        except (ValueError, OSError, OverflowError):
            day = datetime.now()
        dest_dir = library.originals / day.strftime("%Y") / day.strftime("%Y-%m-%d")
        dest = dest_dir / src.name

        from .video import is_video as _is_video
        if _is_video(src) and video_profile == "h264":
            # a smaller copy made on an earlier import, recognised by the
            # size of the camera file it came from
            smaller = dest.with_suffix(".mp4")
            try:
                if smaller.exists() and _source_size_of(smaller) == src.stat().st_size:
                    skipped += 1
                    done_sources.append(src)
                    placed.append(smaller)
                    continue
            except OSError:
                pass
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                # Same name, same size: it is the same photo, already here.
                # A copy compressed on import no longer has the camera
                # file's size, so the size it came from is read back from
                # the copy itself.
                src_size = src.stat().st_size
                if (dest.stat().st_size == src_size
                        or _source_size_of(dest) == src_size):
                    skipped += 1
                    done_sources.append(src)
                    placed.append(dest)
                    continue
                stem, suffix = dest.stem, dest.suffix
                n = 2
                while dest.exists():
                    dest = dest_dir / f"{stem}-{n}{suffix}"
                    n += 1
            from .video import is_video
            # Photos only: a video re-encoded on import would lose
            # quality for good, so it is always copied as it came.
            if profile and profile != "original" and not is_video(src):
                # Stored smaller without visible loss, as chosen in
                # Settings > Storage. Capture date, camera and location
                # stay in the copy; if compressing would not save space
                # the original is kept byte for byte.
                from . import compress as cz
                result = cz.compress_to(src, dest, profile, "keep")
                if not result.ok:
                    raise OSError(result.note or "compression failed")
                if result.output and Path(result.output) != dest:
                    dest = Path(result.output)
                try:
                    shutil.copystat(src, dest)
                except OSError:
                    pass
                try:
                    os.setxattr(dest, _SOURCE_SIZE_XATTR,
                                str(src.stat().st_size).encode())
                except (OSError, AttributeError):
                    pass
            elif is_video(src) and video_profile == "h264":
                smaller = _compress_video(src, dest, rec)
                if smaller is None:
                    shutil.copy2(src, dest)
                else:
                    dest = smaller
                    try:
                        os.setxattr(dest, _SOURCE_SIZE_XATTR,
                                    str(src.stat().st_size).encode())
                    except (OSError, AttributeError):
                        pass
            else:
                shutil.copy2(src, dest)
            copied.append(dest)
            done_sources.append(src)
            placed.append(dest)
        except OSError:
            failed += 1
        if on_progress:
            on_progress(i + 1, total)
    return {"copied": copied, "skipped": skipped, "failed": failed,
            "sources": done_sources, "placed": placed}


def delete_from_device(paths) -> tuple[int, int]:
    """Remove imported files from the camera or card.

    Only ever called with the "sources" import_photos reported as safely in
    the library, and only after the user chose Delete Items. Goes through
    GIO so it works on a camera mounted over gphoto2 as well as a card.
    """
    removed = failed = 0
    for path in paths:
        try:
            Gio.File.new_for_path(str(path)).delete(None)
            removed += 1
        except Exception:
            failed += 1
    return removed, failed


class DeviceWatcher:
    """Tells the window when a device is plugged in or removed."""

    def __init__(self, on_change):
        self._monitor = Gio.VolumeMonitor.get()
        self._on_change = on_change
        self._handlers = [
            self._monitor.connect("mount-added", self._changed),
            self._monitor.connect("mount-removed", self._changed),
            self._monitor.connect("volume-added", self._changed),
            self._monitor.connect("volume-removed", self._changed),
        ]

    def _changed(self, *_args):
        # A mount is announced before its filesystem is necessarily
        # readable; a short beat avoids scanning a half-ready card.
        GLib.timeout_add(400, self._fire)

    def _fire(self):
        try:
            self._on_change()
        except Exception:
            pass
        return GLib.SOURCE_REMOVE

    def stop(self):
        for h in self._handlers:
            try:
                self._monitor.disconnect(h)
            except Exception:
                pass
        self._handlers = []
