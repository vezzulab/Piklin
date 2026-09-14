"""Cameras and phones on a Mac, through Image Capture.

On Linux a camera or phone plugged in over USB is mounted by gvfs and shows
up as a folder (see devices.py). A Mac mounts nothing: an iPhone, a camera
in PTP mode or an Android phone set to "Transfer photos" is reachable only
through Apple's Image Capture. This makes such a device look the way gvfs
presents one, so the rest of Piklin treats both the same:

* each device has a folder of its own in Piklin's caches, standing in for
  the mount point;
* browsing lists its photos under the paths they would have there, with a
  small preview fetched for each so the grid has something to show;
* an original is copied into that folder only when it is needed - to open
  it, or to import it - and removed again once it is in the library;
* deleting after import goes through Image Capture.

Image Capture calls back on the main thread; the blocking helpers here are
for worker threads, which is where Piklin reads devices and copies photos.
Nothing here does anything on a system other than a Mac.
"""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time
import uuid as _uuid
from pathlib import Path

CACHE = Path.home() / "Library" / "Caches" / "Piklin" / "devices"
PREVIEW_SIZE = 400
BATCH = 60

_lock = threading.RLock()
_cameras: dict[str, "_Camera"] = {}
_by_path: dict[str, tuple[str, object]] = {}      # path -> (device uuid, ICCameraFile)
_listeners: list = []
_browser = None
_delegate = None


class _Camera:
    def __init__(self, device):
        self.device = device
        self.uuid = str(device.UUIDString())
        self.name = str(device.name() or "")
        self.root = CACHE / self.uuid
        self.ready = threading.Event()
        self.error: str | None = None

    @property
    def phone(self) -> bool:
        try:
            kind = str(self.device.productKind() or "")
        except Exception:
            kind = ""
        text = f"{kind} {self.name}".lower()
        return any(w in text for w in ("iphone", "ipad", "phone", "android"))


def available() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        import ImageCaptureCore  # noqa: F401
        return True
    except Exception:
        return False


def _notify() -> None:
    for callback in list(_listeners):
        try:
            callback()
        except Exception:
            pass


def _on_main(fn) -> None:
    """Run ``fn`` on the main thread, where Image Capture expects its calls."""
    if threading.current_thread() is threading.main_thread():
        fn()
        return
    from gi.repository import GLib
    GLib.idle_add(lambda: (fn(), False)[1])


def _delegate_class():
    from Foundation import NSObject

    class PiklinImageCaptureDelegate(NSObject):
        def deviceBrowser_didAddDevice_moreComing_(self, browser, device, more):
            cam = _Camera(device)
            with _lock:
                _cameras[cam.uuid] = cam
            device.setDelegate_(self)
            device.requestOpenSession()
            _notify()

        def deviceBrowser_didRemoveDevice_moreGoing_(self, browser, device, more):
            key = str(device.UUIDString())
            with _lock:
                cam = _cameras.pop(key, None)
                for path in [p for p, (u, _f) in _by_path.items() if u == key]:
                    del _by_path[path]
            if cam is not None:
                shutil.rmtree(cam.root, ignore_errors=True)
            _notify()

        def device_didOpenSessionWithError_(self, device, error):
            cam = _cameras.get(str(device.UUIDString()))
            if cam is not None and error is not None:
                # "Please unlock iPhone": the catalogue follows once it is.
                cam.error = str(error.localizedDescription())
                _notify()

        def deviceDidBecomeReadyWithCompleteContentCatalog_(self, device):
            cam = _cameras.get(str(device.UUIDString()))
            if cam is not None:
                cam.error = None
                cam.ready.set()
                _notify()

        def device_didCloseSessionWithError_(self, device, error):
            pass

        def didRemoveDevice_(self, device):
            pass

        def cameraDevice_didAddItems_(self, camera, items):
            pass

        def cameraDevice_didRemoveItems_(self, camera, items):
            pass

    return PiklinImageCaptureDelegate


def start(on_change) -> bool:
    """Watch for cameras and phones; ``on_change`` runs when one comes,
    goes, or becomes readable. Call on the main thread."""
    global _browser, _delegate
    if on_change not in _listeners:
        _listeners.append(on_change)
    if _browser is not None:
        return True
    if not available():
        return False
    try:
        import ImageCaptureCore as ICC
        _delegate = _delegate_class().alloc().init()
        _browser = ICC.ICDeviceBrowser.alloc().init()
        _browser.setDelegate_(_delegate)
        _browser.setBrowsedDeviceTypeMask_(ICC.ICDeviceTypeMaskCamera
                                           | ICC.ICDeviceLocationTypeMaskLocal)
        _browser.start()
    except Exception:
        _browser = _delegate = None
        return False
    return True


def stop_listening(on_change) -> None:
    if on_change in _listeners:
        _listeners.remove(on_change)


def cameras() -> list[dict]:
    """The cameras and phones connected now."""
    with _lock:
        found = []
        for cam in _cameras.values():
            cam.root.mkdir(parents=True, exist_ok=True)
            found.append({"uuid": cam.uuid, "name": cam.name, "phone": cam.phone,
                          "root": cam.root, "ready": cam.ready.is_set(), "error": cam.error})
        return found


def is_camera_path(path: str) -> bool:
    with _lock:
        return str(path) in _by_path


def _wait(start, timeout: float):
    """Call ``start(finish)`` on the main thread and wait for ``finish(value)``."""
    done = threading.Event()
    box: list = [None]

    def finish(value):
        box[0] = value
        done.set()
    _on_main(lambda: start(finish))
    done.wait(timeout)
    return box[0] if done.is_set() else None


def _fetch_previews(pairs: list[tuple[dict, object]], timeout: float = 30) -> None:
    missing = [(rec, f) for rec, f in pairs if not os.path.exists(rec["preview"])]
    if not missing:
        return
    remaining = [len(missing)]
    done = threading.Event()

    def begin():
        import ImageCaptureCore as ICC
        options = {}
        key = getattr(ICC, "ICImageSourceThumbnailMaxPixelSize", None)
        if key is not None:
            options[key] = PREVIEW_SIZE
        for rec, f in missing:
            def completion(data, error, rec=rec):
                try:
                    if data is not None:
                        target = Path(rec["preview"])
                        target.parent.mkdir(parents=True, exist_ok=True)
                        tmp = target.with_name(target.name + ".part")
                        tmp.write_bytes(bytes(data))
                        os.replace(tmp, target)
                finally:
                    remaining[0] -= 1
                    if remaining[0] <= 0:
                        done.set()
            try:
                f.requestThumbnailDataWithOptions_completion_(options, completion)
            except Exception:
                remaining[0] -= 1
        if remaining[0] <= 0:
            done.set()
    _on_main(begin)
    done.wait(timeout)


def scan(key: str, extensions, on_batch=None, limit: int = 10000,
         timeout: float = 30) -> list[dict]:
    """What is on the device, as import records (see devices.scan_device),
    each with a preview on disk. Waits for the device to be readable - an
    iPhone only is once it is unlocked and trusts this Mac."""
    cam = _cameras.get(key)
    if cam is None or not cam.ready.wait(timeout):
        return []
    files = _wait(lambda finish: finish(list(cam.device.mediaFiles() or [])), timeout) or []
    records: list[dict] = []
    batch: list[tuple[dict, object]] = []

    def hand_over():
        _fetch_previews(batch)
        recs = [rec for rec, _f in batch]
        records.extend(recs)
        batch.clear()
        if on_batch and recs:
            on_batch(recs)

    for f in files:
        try:
            name = str(f.name())
            folder = f.parentFolder()
            folder_name = str(folder.name()) if folder is not None else ""
            created = f.creationDate()
            ts = float(created.timeIntervalSince1970()) if created is not None else time.time()
            size = int(f.fileSize())
            width, height = int(f.width() or 0), int(f.height() or 0)
        except Exception:
            continue
        ext = os.path.splitext(name)[1].lower()
        if name.startswith(".") or ext not in extensions:
            continue
        rel = Path(folder_name) / name if folder_name else Path(name)
        path = cam.root / rel
        with _lock:
            _by_path[str(path)] = (cam.uuid, f)
        batch.append(({
            "path": str(path), "filename": name, "ext": ext.lstrip("."),
            "bytes": size, "mtime": ts, "taken_at": ts, "date_source": "mtime",
            "width": width, "height": height, "orientation": 1,
            "camera": cam.uuid,
            "preview": str(cam.root / ".previews" / rel.parent / (rel.name + ".jpg")),
        }, f))
        if len(batch) >= BATCH:
            hand_over()
        if len(records) + len(batch) >= limit:
            break
    if batch:
        hand_over()
    return records


def fetch(path: str, timeout: float = 1800) -> bool:
    """Copy the original at ``path`` off the device, unless it is already
    there. True when the file is in place, complete."""
    target = Path(path)
    if target.is_file():
        return True
    with _lock:
        entry = _by_path.get(str(path))
    if entry is None:
        return False
    _key, camera_file = entry
    # Downloaded into a folder of its own, then moved into place: a copy cut
    # short never sits at the photo's path.
    staging = target.parent / f".downloading-{_uuid.uuid4().hex}"
    staging.mkdir(parents=True, exist_ok=True)
    try:
        def begin(finish):
            import ImageCaptureCore as ICC
            from Foundation import NSURL
            options = {ICC.ICDownloadsDirectoryURL: NSURL.fileURLWithPath_(str(staging)),
                       ICC.ICSaveAsFilename: target.name,
                       ICC.ICOverwrite: True}
            camera_file.requestDownloadWithOptions_completion_(
                options, lambda filename, error: finish((filename, error)))
        result = _wait(begin, timeout)
        if not result or result[1] is not None:
            return False
        got = staging / str(result[0] or target.name)
        if not got.is_file():
            return False
        os.replace(got, target)
        return True
    except Exception:
        return False
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def delete(paths, timeout: float = 600) -> tuple[int, int]:
    """Delete these photos from their devices. (removed, failed)."""
    groups: dict[str, list] = {}
    unknown = 0
    with _lock:
        for p in paths:
            entry = _by_path.get(str(p))
            if entry is None:
                unknown += 1
                continue
            groups.setdefault(entry[0], []).append(entry[1])
    removed = failed = 0
    for key, files in groups.items():
        cam = _cameras.get(key)
        if cam is None:
            failed += len(files)
            continue

        def begin(finish, cam=cam, files=files):
            failures: list[int] = [0]

            def delete_failed(items):
                failures[0] = len(items or {})

            def completion(result, error):
                finish(failures[0] if error is None else len(files))
            cam.device.requestDeleteFiles_deleteFailed_completion_(files, delete_failed, completion)
        not_deleted = _wait(begin, timeout)
        if not_deleted is None:
            failed += len(files)
        else:
            failed += not_deleted
            removed += len(files) - not_deleted
    return removed, failed + unknown
