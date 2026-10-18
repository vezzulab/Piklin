"""Cameras and phones on Windows, through Windows Portable Devices.

On Linux a camera or phone plugged in over USB is mounted by gvfs and shows up
as a folder (see devices.py); on a Mac, Image Capture reaches it (see
imagecapture.py). Windows mounts nothing: an iPhone, an Android phone set to
"File transfer" and a camera in PTP mode are reachable only through WPD. This
makes such a device look the way the others do, so the rest of Piklin treats
all three the same:

* each device has a folder of its own in Piklin's caches, standing in for the
  mount point;
* browsing lists its photos under the paths they would have there, with a small
  preview fetched for each so the grid has something to show;
* an original is copied into that folder only when it is needed - to open it, or
  to import it - and removed again once it is in the library;
* nothing is ever deleted from the device: Piklin only reads it.

The WPD calls are made by ``piklin-wpd.exe`` (packaging/windows/piklin-wpd.c),
a process of its own: a phone that stops answering cannot freeze Piklin's
window. Nothing here does anything on a system other than Windows.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid as _uuid
from pathlib import Path

PREVIEW_BATCH = 40
_NO_WINDOW = 0x08000000                     # CREATE_NO_WINDOW
# A phone that is unplugged, or goes to sleep, in the middle of a call leaves the
# helper waiting for ever. It is cut off once it has gone quiet for this long (a
# listing keeps printing; a copy keeps growing its file), and what it had read stays.
IDLE_SECONDS = 45.0
STALL_SECONDS = 60.0

_lock = threading.RLock()
# A phone answers one transfer at a time: two at once, and one of them fails.
# Everything that opens a device waits its turn here.
_device_lock = threading.Lock()
_by_path: dict[str, tuple[str, str, int]] = {}   # path -> (device id, object id, size)
_known: dict[str, dict] = {}                # device id -> what list said of it
_listeners: list = []
_poller: threading.Thread | None = None
_stop = threading.Event()


def _cache_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Piklin" / "Devices"


def _tool() -> str | None:
    """piklin-wpd.exe: the one Piklin carries, beside its Python."""
    if sys.platform != "win32":
        return None
    candidates = []
    if os.environ.get("PIKLIN_WPD"):
        candidates.append(Path(os.environ["PIKLIN_WPD"]))
    if os.environ.get("PIKLIN_RUNTIME"):
        candidates.append(Path(os.environ["PIKLIN_RUNTIME"]) / "bin" / "piklin-wpd.exe")
    candidates.append(Path(sys.executable).with_name("piklin-wpd.exe"))
    found = next((str(c) for c in candidates if c.is_file()), None)
    return found or shutil.which("piklin-wpd")


def available() -> bool:
    return _tool() is not None


def _command(exe: str, args: list[str]) -> list[str]:
    return [exe, *args]


def _kill(proc) -> None:
    try:
        proc.kill()
        proc.wait(5)
    except (OSError, subprocess.SubprocessError):
        pass


def _run(args: list[str], stdin: str | None = None, timeout: float = 60, on_line=None) -> list[dict]:
    """Run the helper; the JSON objects it printed. ``on_line`` is called with each one
    as it arrives. Cut off when it says nothing for IDLE_SECONDS or runs past
    ``timeout``: what it printed until then is kept."""
    exe = _tool()
    if exe is None:
        return []
    import queue
    lines: list[dict] = []
    # Listing the devices does not open one; everything else does.
    with (_device_lock if args[0] != "list" else contextlib.nullcontext()):
        try:
            proc = subprocess.Popen(_command(exe, args), stdin=subprocess.PIPE if stdin is not None
                                    else subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        except OSError:
            return []
        printed: "queue.Queue[bytes | None]" = queue.Queue()

        def read():
            try:
                for raw in proc.stdout:
                    printed.put(raw)
            except (OSError, ValueError):
                pass
            printed.put(None)

        def feed():
            try:
                proc.stdin.write(stdin.encode("utf-8"))
                proc.stdin.close()
            except (OSError, ValueError):
                pass

        threading.Thread(target=read, daemon=True).start()
        if stdin is not None:
            threading.Thread(target=feed, daemon=True).start()
        end = time.monotonic() + timeout
        while True:
            wait = min(IDLE_SECONDS, end - time.monotonic())
            if wait <= 0:
                _kill(proc)
                break
            try:
                raw = printed.get(timeout=wait)
            except queue.Empty:
                _kill(proc)                         # gone quiet, or out of time
                break
            if raw is None:
                break
            try:
                line = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            lines.append(line)
            if on_line is not None:
                on_line(line)
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            _kill(proc)
    return lines


def _lines(text: str) -> list[dict]:
    out = []
    for line in (text or "").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _looks_like_phone(name: str, maker: str, desc: str) -> bool:
    text = f"{name} {maker} {desc}".lower()
    if any(w in text for w in ("canon", "nikon", "sony", "fujifilm", "olympus", "panasonic",
                               "gopro", "camera", "dslr")):
        return not any(w in text for w in ("phone", "iphone", "galaxy", "pixel"))
    return True


def _is_media_device(device_id: str) -> bool:
    """A phone or camera, not a USB stick or card reader: Piklin already lists
    those as drives (their letter), and listing them twice would offer the same
    photos in two places."""
    low = device_id.lower()
    return low.startswith("\\\\?\\usb#") and "usbstor" not in low and "swd\\wpdbusenum" not in low


def devices() -> list[dict]:
    """The phones and cameras connected now."""
    found = []
    for d in _run(["list"], timeout=20):
        did = d.get("id") or ""
        if not did or not _is_media_device(did):
            continue
        name = d.get("name") or d.get("desc") or ""
        root = _cache_root() / hashlib.sha1(did.encode("utf-8")).hexdigest()[:16]
        info = {"id": did, "name": name, "root": root,
                "phone": _looks_like_phone(name, d.get("maker", ""), d.get("desc", ""))}
        found.append(info)
    with _lock:
        _known.clear()
        _known.update({d["id"]: d for d in found})
    for d in found:
        d["root"].mkdir(parents=True, exist_ok=True)
    _tidy_cache({d["root"].name for d in found})
    return found


_tidied = False
STALE_AFTER = 24 * 3600


def _tidy_cache(current: set[str]) -> None:
    """Once per run, remove what earlier runs left for phones that are not connected
    (their small pictures, and the photos copied off to open them). A day's grace, so
    a phone that was just unplugged, or one the list missed for a moment, keeps its
    files."""
    global _tidied
    if _tidied:
        return
    _tidied = True
    try:
        for child in _cache_root().iterdir():
            if (child.is_dir() and child.name not in current
                    and time.time() - child.stat().st_mtime > STALE_AFTER):
                shutil.rmtree(child, ignore_errors=True)
    except OSError:
        pass


def is_camera_path(path: str) -> bool:
    with _lock:
        return str(path) in _by_path


# -- coming and going ------------------------------------------------------
def _notify() -> None:
    try:
        from gi.repository import GLib
    except ImportError:
        GLib = None
    for callback in list(_listeners):
        if GLib is not None:
            GLib.idle_add(lambda cb=callback: (cb(), False)[1])
        else:
            try:
                callback()
            except Exception:
                pass


def _poll() -> None:
    """Windows has no signal that reaches Python for a phone being plugged in,
    so the list is looked at every few seconds, for as long as Piklin is open."""
    seen: set[str] | None = None
    while not _stop.wait(4.0):
        ids = {d["id"] for d in devices()}
        if seen is not None and ids != seen:
            _notify()
        seen = ids


def start(on_change) -> bool:
    """Watch for phones and cameras; ``on_change`` runs when one comes or goes."""
    global _poller
    if on_change not in _listeners:
        _listeners.append(on_change)
    if not available():
        return False
    if _poller is None or not _poller.is_alive():
        _stop.clear()
        _poller = threading.Thread(target=_poll, name="wpd-watch", daemon=True)
        _poller.start()
    return True


def stop_listening(on_change) -> None:
    if on_change in _listeners:
        _listeners.remove(on_change)
    if not _listeners:
        _stop.set()


# -- browsing --------------------------------------------------------------
# Photos for which a sharp small picture is made from the photo itself: the phone's own is a
# hundred and sixty pixels wide, and does not follow the note in the file that says how the
# photo is turned (an iPhone's HEIC taken upright). Larger than this, the photo is not fetched.
_SHARPEN = {".heic", ".heif", ".jpg", ".jpeg"}
_SHARPEN_MAX = 20 * 1024 * 1024


def _sharpen_previews(pairs: list[tuple[str, str]]) -> None:
    """Make each small picture from its whole photo (``<picture>.original``, saved with
    it), turned the right way up; the photo is then removed. If that fails, the phone's
    own picture stays."""
    from concurrent.futures import ThreadPoolExecutor
    from . import imageio as iio

    def one(pair):
        target, original = pair
        try:
            if not os.path.exists(original):
                return
            im = iio.load_pil(Path(original), max_side=PREVIEW_SIZE)
            im.convert("RGB").save(target, "JPEG", quality=88)
        except Exception:
            pass
        finally:
            try:
                os.unlink(original)
            except OSError:
                pass

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(one, pairs))


def _fetch_previews(device_id: str, pairs: list[tuple[dict, str]]) -> int:
    """A small picture for each of these, on disk where the record says. Returns how many
    could not be made because the phone has locked itself."""
    todo = [(obj, rec["preview"],
             os.path.splitext(rec["filename"])[1].lower() in _SHARPEN and int(rec.get("bytes") or 0) <= _SHARPEN_MAX)
            for rec, obj in pairs if not os.path.exists(rec["preview"])]
    if not todo:
        return 0
    for _obj, target, _whole in todo:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    # A third column asks the helper for the whole photo as well.
    rows = [f"{obj}" + chr(9) + f"{target}" + (chr(9) + f"{target}.original" if whole else "") + chr(10)
            for obj, target, whole in todo]
    said = _run(["thumbs", device_id], stdin="".join(rows), timeout=600)
    locked = sum(1 for line in said if line.get("e") == _LOCKED)
    _sharpen_previews([(target, target + ".original") for _obj, target, whole in todo if whole])
    if locked:
        return locked                      # a locked phone gives nothing, so do not ask it for more
    # Android gives no small picture: make one from the photo itself.
    for rec, obj in pairs:
        if not os.path.exists(rec["preview"]):
            _preview_from_original(device_id, obj, rec)
    return 0


# Larger than this, a file is not copied just to draw its tile.
_PREVIEW_MAX_PHOTO = 40 * 1024 * 1024
_PREVIEW_MAX_VIDEO = 80 * 1024 * 1024
PREVIEW_SIZE = 400


def _preview_from_original(device_id: str, obj: str, rec: dict) -> bool:
    """A phone that has no small picture of a photo (Android): copy the photo
    to a temporary folder, make the picture from it, and remove the copy. Only
    ever reads from the phone."""
    exe = _tool()
    size = int(rec.get("bytes") or 0)
    from .video import is_video as _is_video
    video = _is_video(rec["filename"])
    if exe is None or size > (_PREVIEW_MAX_VIDEO if video else _PREVIEW_MAX_PHOTO):
        return False
    work = Path(rec["preview"]).parent / f".making-{_uuid.uuid4().hex}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        got = work / rec["filename"]
        if not _fetch_to(device_id, obj, got, 600) or (size and got.stat().st_size != size):
            return False
        if video:
            from .video import poster
            im = poster(got, PREVIEW_SIZE)
        else:
            from . import imageio as iio
            im = iio.load_pil(got, max_side=PREVIEW_SIZE)
        if im is None:
            return False
        im = im.convert("RGB")
        im.thumbnail((PREVIEW_SIZE, PREVIEW_SIZE))
        out = Path(rec["preview"])
        part = out.with_name(out.name + ".part")
        im.save(part, "JPEG", quality=88)
        os.replace(part, out)
        return True
    except Exception:
        return False
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _could_be_sharper(rec: dict, preview: Path) -> bool:
    """A picture kept from an earlier visit that is the phone's own tiny one, of a photo
    that can now be made sharp (and turned the right way up)."""
    if os.path.splitext(rec["filename"])[1].lower() not in _SHARPEN or int(rec.get("bytes") or 0) > _SHARPEN_MAX:
        return False
    try:
        from PIL import Image
        with Image.open(preview) as im:
            return max(im.size) < 300
    except Exception:
        return True


PREVIEW_LIMIT = 640         # a small picture is at most this on its longest side


def _usable_preview(path: Path) -> bool:
    """Whether the small picture the phone gave can be shown. Some phones give the whole
    photo for a "small picture" (3024x4032): that is made small here, and one that
    is cut short (the phone locked halfway) is removed."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.load()
            if max(im.size) > PREVIEW_LIMIT:
                small = im.convert("RGB")
                small.thumbnail((PREVIEW_LIMIT, PREVIEW_LIMIT))
                small.save(path, "JPEG", quality=88)
        return True
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        return False


# What the helper says when a phone has locked itself: it still lists what it has, but will
# not give the contents (ERROR_DEVICE_UNREACHABLE).
_LOCKED = "0x80070141"
_problems: dict[str, str] = {}


def take_problem(device_id: str) -> str | None:
    """Why the last reading of this device was not complete ("locked"), once."""
    with _lock:
        return _problems.pop(device_id, None)


def scan(device_id: str, extensions, on_batch=None, limit: int = 10000,
         timeout: float = 1800, on_previews=None) -> list[dict]:
    """What is on the device, as import records (see devices.scan_device).

    As in Apple's Photos, the device is read in two steps: first what is on it - names,
    sizes, dates, which a phone gives quickly - which is handed to ``on_batch`` at once;
    then the small picture of each, in short turns of their own (a phone does not answer
    two calls at once, and a long call is where it gives up), each turn reported to
    ``on_previews`` with the records whose picture is now on disk. An iPhone answers
    only once it is unlocked and trusts this computer, and only while it stays unlocked."""
    with _lock:
        info = _known.get(device_id)
        _problems.pop(device_id, None)
    if info is None:
        devices()
        with _lock:
            info = _known.get(device_id)
    if info is None:
        return []
    root: Path = info["root"]
    previews = root / ".previews"
    previews.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    pending: list[dict] = []
    state = {"last": time.monotonic()}

    def flush():
        if pending and on_batch:
            on_batch(pending[:])
        pending.clear()
        state["last"] = time.monotonic()

    objects: dict[str, str] = {}                  # path -> the phone's name for the object

    def one(line):
        if "error" in line or "o" not in line or len(records) >= limit:
            return
        name = line.get("n") or ""
        ext = os.path.splitext(name)[1].lower()
        if name.startswith(".") or ext not in extensions:
            return
        rel = Path(line.get("d") or "") / name
        path = root / rel
        ts = float(line.get("t") or 0) or time.time()
        preview = previews / rel.parent / (rel.name + ".jpg")
        with _lock:
            _by_path[str(path)] = (device_id, line["o"], int(line.get("s") or 0))
        rec = {"path": str(path), "filename": name, "ext": ext.lstrip("."),
               "bytes": int(line.get("s") or 0), "mtime": ts, "taken_at": ts,
               "date_source": "mtime", "width": 0, "height": 0, "orientation": 1,
               "camera": device_id, "preview": str(preview)}
        objects[str(path)] = line["o"]
        records.append(rec)
        pending.append(rec)
        if len(pending) >= 40 or time.monotonic() - state["last"] > 0.3:
            flush()

    _run(["scan", device_id], timeout=timeout, on_line=one)
    flush()

    # The pictures, in turns. One kept from an earlier visit is used if it can be shown.
    todo = []
    ready = []
    for rec in records:
        preview = Path(rec["preview"])
        if preview.is_file() and _usable_preview(preview) and not _could_be_sharper(rec, preview):
            ready.append(rec)
        else:
            if preview.is_file():
                preview.unlink(missing_ok=True)       # the phone's tiny one: made again, sharp
            todo.append((rec, objects[rec["path"]]))
    if ready and on_previews:
        on_previews(ready)
    for i in range(0, len(todo), PREVIEW_BATCH):
        batch = todo[i:i + PREVIEW_BATCH]
        if _fetch_previews(device_id, batch):
            with _lock:
                _problems[device_id] = "locked"
            break
        made = [rec for rec, _obj in batch if Path(rec["preview"]).is_file() and _usable_preview(Path(rec["preview"]))]
        if made and on_previews:
            on_previews(made)
    return records


def _fetch_to(device_id: str, obj: str, dest: Path, timeout: float = 1800) -> bool:
    """Copy one object off the device to ``dest``. A copy whose file stops growing for
    STALL_SECONDS (the phone was unplugged, or fell asleep) is cut off and fails."""
    exe = _tool()
    if exe is None:
        return False
    part = Path(str(dest) + ".part")
    with _device_lock:
        try:
            proc = subprocess.Popen(_command(exe, ["fetch", device_id, obj, str(dest)]),
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        except OSError:
            return False
        start = last = time.monotonic()
        seen = -1
        while True:
            try:
                code = proc.wait(0.4)
                break
            except subprocess.TimeoutExpired:
                pass
            now = time.monotonic()
            try:
                size = part.stat().st_size
            except OSError:
                size = 0
            if size != seen:
                seen, last = size, now
            if now - last > STALL_SECONDS or now - start > timeout:
                _kill(proc)
                return False
    return code == 0 and dest.is_file()


def _renumber(device_id: str, path: str) -> bool:
    """List the device again and take the objects' names as they are now. True when the photo
    at ``path`` has a different one than before."""
    with _lock:
        info = _known.get(device_id)
        before = _by_path.get(path)
    if info is None or before is None:
        return False
    root: Path = info["root"]
    for line in _run(["scan", device_id], timeout=120):
        if "o" not in line:
            continue
        key = str(root / (Path(line.get("d") or "") / (line.get("n") or "")))
        with _lock:
            if key in _by_path:
                _by_path[key] = (device_id, line["o"], int(line.get("s") or 0))
    with _lock:
        now = _by_path.get(path)
    return now is not None and now[1] != before[1]


def fetch(path: str, timeout: float = 1800) -> bool:
    """Copy the original at ``path`` off the device, unless it is already there.
    True when the file is in place, complete."""
    target = Path(path)
    if target.is_file():
        return True
    with _lock:
        entry = _by_path.get(str(path))
    exe = _tool()
    if entry is None or exe is None:
        return False
    device_id, obj, size = entry
    # Downloaded into a folder of its own, then moved into place: a copy cut
    # short never sits at the photo's path.
    staging = target.parent / f".downloading-{_uuid.uuid4().hex}"
    staging.mkdir(parents=True, exist_ok=True)
    try:
        got = staging / target.name
        if not _fetch_to(device_id, obj, got, timeout):
            # An iPhone that was only just plugged in is still loading its library, and
            # numbers its objects again as it goes: ask what is there now, and try once more.
            if not _renumber(device_id, str(path)):
                return False
            with _lock:
                entry = _by_path.get(str(path))
            if entry is None or not _fetch_to(device_id, entry[1], got, timeout):
                return False
            obj, size = entry[1], entry[2]
        # The size the phone listed: a copy of another size is not this photo.
        if size and got.stat().st_size != size:
            return False
        os.replace(got, target)
        return True
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        shutil.rmtree(staging, ignore_errors=True)
