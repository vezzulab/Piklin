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
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import imagecapture
from . import imageio as iio
from . import system
from .i18n import _

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


def _is_dir(path: Path) -> bool:
    """A folder that is there. A camera or phone mounted by gvfs over
    gphoto2 (an iPhone on Linux) answers a name that is not there with an
    I/O error instead of "not found"; that is not there either."""
    try:
        return path.is_dir()
    except OSError:
        return False


def _looks_like_camera(root: Path) -> bool:
    """A volume is a camera if it carries a picture folder."""
    return any(_is_dir(root / name) for name in PHOTO_DIRS)


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
    # An iPhone or iPad on Linux: gvfs reaches its Camera Roll over Apple's
    # AFC protocol (libimobiledevice), with a DCIM folder like any camera.
    if uri.startswith(("mtp://", "afc://")):
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
        name = mount.get_name() or _("Camera")
        if uri.startswith("afc://"):
            # An iPhone mounts twice over AFC: its Camera Roll, with a DCIM
            # folder, and its apps' documents ("afc://<id>:3/"). Only the
            # first holds photos.
            if path is None or not (path / "DCIM").is_dir():
                continue
        if kind == "storage":
            if path is None:
                continue
            if not _looks_like_camera(path):
                if not _is_external(mount, path):
                    continue
                kind, icon = "drive", "drive-removable-media-symbolic"
                name = mount.get_name() or _("USB Drive")

        seen.add(uri)
        devices.append(Device(
            id=uri, name=name, path=path, uri=uri,
            icon=icon, kind=kind, mount=mount))

    # An iPhone can also show up as a PTP camera beside its AFC mount; the
    # AFC mount already has every photo, so it is not listed twice.
    if any(d.uri.startswith("afc://") for d in devices):
        devices = [d for d in devices if not (
            d.uri.startswith("gphoto2://")
            and any(w in (d.name + " " + d.uri).lower() for w in ("iphone", "ipad", "apple")))]

    # Drives and cards GIO does not report (a Mac's /Volumes), treated the
    # way a card or USB drive mounted in /media is above.
    for name, local in system.external_volumes():
        uri = Gio.File.new_for_path(local).get_uri()
        if uri in seen:
            continue
        path = Path(local)
        if _looks_like_camera(path):
            kind, icon = "storage", "media-flash-symbolic"
        else:
            kind, icon = "drive", "drive-removable-media-symbolic"
        seen.add(uri)
        devices.append(Device(id=uri, name=name, path=path, uri=uri,
                              icon=icon, kind=kind))

    # Cameras and phones a Mac reaches through Image Capture, where Linux
    # has them mounted by gvfs above.
    for cam in imagecapture.cameras():
        uri = f"imagecapture://{cam['uuid']}"
        devices.append(Device(
            id=uri, name=cam["name"] or _("Camera"), path=Path(cam["root"]), uri=uri,
            icon="phone-symbolic" if cam["phone"] else "camera-photo-symbolic",
            kind="phone" if cam["phone"] else "camera"))
    return devices


def photo_dirs(device: Device) -> list[Path]:
    """The folders on the device worth looking in."""
    if device.path is None:
        return []
    found = [device.path / name for name in PHOTO_DIRS
             if _is_dir(device.path / name)]
    # Some cameras write straight to the root of the card.
    return found or [device.path]


def scan_device(device: Device, limit: int = 10000,
                read_metadata: bool = False, on_batch=None) -> list[dict]:
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
    if device.uri.startswith("imagecapture://"):
        return imagecapture.scan(device.uri.split("://", 1)[1], exts,
                                 on_batch=on_batch, limit=limit)
    records: list[dict] = []
    # ``on_batch`` receives what was found so far every few hundred files or
    # fraction of a second, so a big drive shows its first photos at once.
    pending: list[dict] = []
    last = [time.monotonic()]

    def hand_over():
        if on_batch and pending:
            on_batch(pending[:])
            pending.clear()
        last[0] = time.monotonic()

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
                    pending.append(rec)
                    if len(pending) >= 150 or time.monotonic() - last[0] > 0.4:
                        hand_over()
                if len(records) >= limit:
                    hand_over()
                    return records
    hand_over()
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
        return int(system.get_xattr(dest, _SOURCE_SIZE_XATTR).decode())
    except (OSError, ValueError):
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
                  video_profile: str = "original",
                  on_placed=None, cancel: threading.Event | None = None,
                  workers: int | None = None) -> dict:
    """Copy photos into the library, filed by date.

    Files are copied, never moved: taking a photo off someone's camera
    by removing it from the card is not a decision this should make.

    Several files are copied at once. ``on_placed(path)`` is called as each
    one is in the library, so the window can show them while the rest are
    still copying; ``cancel`` stops the import between files, keeping what
    is already copied. Both callbacks run on the calling thread.
    """
    import shutil
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import datetime
    from .video import is_video

    lock = threading.Lock()
    reserved: set[Path] = set()
    to_shrink: list[Path] = []          # videos copied as they are, to make smaller later

    def claim(dest_dir: Path, src: Path) -> tuple[Path, bool]:
        """A free name for src in dest_dir, or (existing copy, True)."""
        with lock:
            dest = dest_dir / src.name
            if dest.exists() and dest not in reserved:
                # Same name, same size: it is the same photo, already here.
                # A copy compressed on import no longer has the camera
                # file's size, so the size it came from is read back from
                # the copy itself.
                src_size = src.stat().st_size
                if (dest.stat().st_size == src_size
                        or _source_size_of(dest) == src_size):
                    return dest, True
            stem, suffix = dest.stem, dest.suffix
            n = 2
            while dest.exists() or dest in reserved:
                dest = dest_dir / f"{stem}-{n}{suffix}"
                n += 1
            reserved.add(dest)
            return dest, False

    def one(rec: dict):
        if cancel is not None and cancel.is_set():
            return None
        src = Path(rec["path"])
        claimed = None
        # A photo on a Mac's camera or phone is copied off it first, into the
        # folder standing in for the device, and removed from there after.
        staged = bool(rec.get("camera"))
        try:
            if staged and not imagecapture.fetch(str(src)):
                return ("failed", src, None)
            # Filing by date means the date the shutter fired, not the
            # file's timestamp - so if this record came from the fast
            # listing, read the real EXIF now, while the file is about to
            # be opened anyway.
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
            dest_dir.mkdir(parents=True, exist_ok=True)

            if is_video(src) and video_profile == "h264":
                # a smaller copy made on an earlier import, recognised by
                # the size of the camera file it came from
                smaller = (dest_dir / src.name).with_suffix(".mp4")
                if smaller.exists() and _source_size_of(smaller) == src.stat().st_size:
                    return ("skipped", src, smaller)

            dest, existing = claim(dest_dir, src)
            if existing:
                return ("skipped", src, dest)
            claimed = dest
            # Photos only: a video re-encoded on import would lose quality
            # for good, so it is copied as it came unless chosen otherwise.
            if profile and profile != "original" and not is_video(src):
                # Stored smaller without visible loss, as chosen in
                # Settings > Storage. Capture date, camera and location stay
                # in the copy; if compressing would not save space the
                # original is kept byte for byte.
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
                    system.set_xattr(dest, _SOURCE_SIZE_XATTR, str(src.stat().st_size).encode())
                except OSError:
                    pass
            elif is_video(src) and video_profile == "h264":
                # Copied as it is first - quick, and the video is in the
                # library at once. Making it smaller takes minutes for a long
                # video; done during the copy it held the whole import at
                # one percentage. The window shrinks it afterwards.
                _copy_whole(src, dest)
                with lock:
                    to_shrink.append(dest)
            else:
                _copy_whole(src, dest)
            return ("copied", src, dest)
        except Exception:
            return ("failed", src, None)
        finally:
            if claimed is not None:
                with lock:
                    reserved.discard(claimed)
            if staged:
                src.unlink(missing_ok=True)

    copied, done_sources, placed = [], [], []
    skipped = failed = done = 0
    total = len(records)
    stopped = False
    # Each file may be a large photo compressed in memory: as many at once as
    # the computer can spare, at low priority (system.work_budget).
    max_workers = workers or system.work_budget("images")
    with ThreadPoolExecutor(max_workers=max_workers,
                            initializer=system.lower_thread_priority) as pool:
        futures = [pool.submit(one, rec) for rec in records]
        for fut in as_completed(futures):
            if cancel is not None and cancel.is_set() and not stopped:
                stopped = True
                for f in futures:
                    f.cancel()
            if fut.cancelled():
                continue
            res = fut.result()
            done += 1
            if res is not None:
                kind, src, dest = res
                if kind == "failed":
                    failed += 1
                else:
                    if kind == "copied":
                        copied.append(dest)
                    else:
                        skipped += 1
                    # The files now safely in the library (copied, or already
                    # there byte for byte) - the only ones "Delete Items" may
                    # ever remove from the card.
                    done_sources.append(src)
                    placed.append(dest)
                    if on_placed:
                        on_placed(dest)
            if on_progress:
                on_progress(done, total)
    placed_set = set(placed)
    return {"copied": copied, "skipped": skipped, "failed": failed,
            "sources": done_sources, "placed": placed,
            "to_shrink": [p for p in to_shrink if p in placed_set],
            "cancelled": bool(cancel is not None and cancel.is_set())}


def shrink_video(library, catalog, photo_id: int, on_progress=None,
                 cancel: threading.Event | None = None, bit_rate: int | None = None,
                 verify=None, set_apart: Path | None = None) -> str:
    """Make a video already in the library smaller (H.264), in place.

    Returns "smaller", "kept" (it would not save a tenth, so the file is
    left as it is), "cancelled", "failed" or "gone". The original is only
    removed once the smaller copy is complete and the catalog points at it,
    so the video is never lost; albums, favourites and edits stay with it.
    """
    from . import video_edit as ve
    from .photo_rename import repoint_photo
    from .video import stream_info
    row = catalog.photo(photo_id)
    if row is None:
        return "gone"
    src = Path(row["path"])
    if not src.is_file():
        return "gone"
    work = src.with_name(f"{src.stem}.smaller.mp4")
    Path(work.with_name(f".{work.name}.part")).unlink(missing_ok=True)   # left by a closed app
    try:
        duration = float(row["duration"] or 0.0) or \
            float((stream_info(src) or {}).get("duration") or 0.0)
        keys = row.keys()
        location = ((row["gps_lat"], row["gps_lon"])
                    if "gps_lat" in keys and row["gps_lat"] is not None else None)
        out = ve.export(src, work, ve.VideoEdit(duration=duration), fmt="mp4",
                        keep_location=True,
                        meta={"taken_at": row["taken_at"], "location": location},
                        on_progress=on_progress, cancel=cancel, bit_rate=bit_rate)
    except ve.Cancelled:
        work.unlink(missing_ok=True)
        return "cancelled"
    except Exception:
        work.unlink(missing_ok=True)
        return "failed"
    st = src.stat()
    if out.stat().st_size >= st.st_size * 0.9:
        out.unlink(missing_ok=True)
        return "kept"
    if verify is not None:
        # The smaller copy has to be the same video - its length, its sound,
        # its picture - before it may take the original's place.
        try:
            ok = bool(verify(src, out))
        except Exception:
            ok = False
        if not ok:
            out.unlink(missing_ok=True)
            return "failed"
    os.utime(out, (st.st_atime, st.st_mtime))
    try:
        # the size of the camera file, so importing it again is recognised
        system.set_xattr(out, _SOURCE_SIZE_XATTR,
                         str(_source_size_of(src) or st.st_size).encode())
    except OSError:
        pass
    final = src.with_suffix(".mp4")
    if final != src:
        n = 2
        while final.exists():
            final = src.with_name(f"{src.stem}-{n}.mp4")
            n += 1
    if final == src:
        if set_apart is not None:                 # kept a while, not overwritten
            set_apart.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, set_apart)
        os.replace(out, final)                    # an .mp4 swapped in place
        repoint_photo(library, catalog, photo_id, src, final,
                      bytes=final.stat().st_size)
        return "smaller"
    os.replace(out, final)
    try:
        repoint_photo(library, catalog, photo_id, src, final,
                      bytes=final.stat().st_size, ext="mp4")
    except Exception:
        final.unlink(missing_ok=True)             # the original stays, untouched
        return "failed"
    if set_apart is not None:
        # No backup keeps the original: it waits in the library for a few
        # days before it goes (see videospace.tend_local).
        set_apart.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, set_apart)
    else:
        src.unlink(missing_ok=True)
    return "smaller"


def _copy_whole(src: Path, dest: Path) -> None:
    """Copy under a hidden temporary name, then rename: a file that is only
    half copied never appears in the library under its real name."""
    import shutil
    tmp = dest.with_name(f".{dest.name}.part")
    try:
        shutil.copy2(src, tmp)
        tmp.replace(dest)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def delete_from_device(paths) -> tuple[int, int]:
    """Remove imported files from the camera or card.

    Only ever called with the "sources" import_photos reported as safely in
    the library, and only after the user chose Delete Items. Goes through
    GIO so it works on a camera mounted over gphoto2 as well as a card.
    """
    removed = failed = 0
    paths = list(paths)
    on_camera = [p for p in paths if imagecapture.is_camera_path(str(p))]
    if on_camera:
        removed, failed = imagecapture.delete(on_camera)
        paths = [p for p in paths if p not in on_camera]
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
        # Where GIO does not announce drives (a Mac), watch the folder they
        # are mounted in instead.
        self._folder_monitor = None
        folder = system.volumes_folder()
        if folder:
            try:
                self._folder_monitor = Gio.File.new_for_path(folder).monitor_directory(
                    Gio.FileMonitorFlags.WATCH_MOUNTS, None)
                self._folder_monitor.connect("changed", self._changed)
            except Exception:
                self._folder_monitor = None
        # Cameras and phones on a Mac (Image Capture); nothing elsewhere.
        imagecapture.start(self._changed)

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
        imagecapture.stop_listening(self._changed)
        if self._folder_monitor is not None:
            self._folder_monitor.cancel()
            self._folder_monitor = None
