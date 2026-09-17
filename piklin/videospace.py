"""Room taken by videos: which ones can be made smaller, by how much, and
whether a smaller copy is really the same video.

Only videos inside the library's own Originals folder are ever considered.
A video in a watched folder outside the library belongs to that folder - other
programs may use it, and it has no copy in the backup to fall back on - so it
is never replaced.

A video is made smaller by capping its bits per second by the size of its
picture, at rates where the difference does not show at a normal viewing
distance. Videos already under the cap, or that would save little, are left
alone.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# bits per second for the video track, by the long side of the picture
CAPS = ((854, 1_500_000), (1280, 4_000_000), (1920, 8_000_000), (10_000, 20_000_000))
AUDIO_BPS = 160_000
# not worth the work or the small loss of detail below this
MIN_SAVING_BYTES = 20 * 1024 * 1024
MIN_SAVING_SHARE = 0.25


def cap_for(width: int, height: int) -> int:
    side = max(width or 0, height or 0)
    for limit, bps in CAPS:
        if side <= limit:
            return bps
    return CAPS[-1][1]


@dataclass
class Candidate:
    photo_id: int
    path: str
    bytes: int
    duration: float
    width: int
    height: int
    bit_rate: int               # the video bits per second to ask for
    expected: int               # bytes afterwards, about

    @property
    def saving(self) -> int:
        return max(0, self.bytes - self.expected)

    @property
    def work(self) -> float:
        """How much encoding it takes: pixels times seconds."""
        return max(1, self.width * self.height) * max(self.duration, 0.1)


def plan(catalog, originals: Path | str, min_saving: int = MIN_SAVING_BYTES,
         min_share: float = MIN_SAVING_SHARE) -> list[Candidate]:
    """The library's videos worth making smaller, the most space for the
    least work first."""
    root = Path(originals).resolve()
    out = []
    for r in catalog.q(
            "SELECT id, path, bytes, duration, width, height FROM photos "
            "WHERE duration > 0 AND trashed_at IS NULL AND paired_to IS NULL"):
        try:
            Path(r["path"]).resolve().relative_to(root)
        except ValueError:
            continue                      # outside the library: never touched
        dur = float(r["duration"] or 0)
        size = int(r["bytes"] or 0)
        if dur <= 0 or size <= 0:
            continue
        current = size * 8 / dur
        target = min(cap_for(r["width"], r["height"]), int(current * 0.8))
        expected = int((target + AUDIO_BPS) * dur / 8)
        saving = size - expected
        if saving < min_saving or saving < size * min_share:
            continue
        out.append(Candidate(r["id"], r["path"], size, dur, int(r["width"] or 0),
                             int(r["height"] or 0), target, expected))
    out.sort(key=lambda c: c.saving / c.work, reverse=True)
    return out


def _has_audio(path) -> bool:
    import av
    try:
        with av.open(str(path)) as c:
            return bool(c.streams.audio)
    except Exception:
        return False


def _frames(path, times, side=320):
    import av
    import numpy as np
    shots = []
    with av.open(str(path)) as c:
        v = c.streams.video[0]
        for t in times:
            c.seek(int(t / v.time_base), stream=v, any_frame=False, backward=True)
            got = None
            for frame in c.decode(v):
                if frame.time is not None and frame.time >= t - 0.05:
                    got = frame
                    break
                got = frame
            if got is None:
                return None
            img = got.to_ndarray(format="gray")
            h, w = img.shape
            step = max(1, max(h, w) // side)
            shots.append(np.asarray(img[::step, ::step], dtype=np.float32) / 255.0)
    return shots


def verify(src, out, min_similarity: float = 0.80) -> bool:
    """Whether ``out`` is the same video as ``src``: as long, with sound when
    it had sound, and looking the same at its start, middle and end."""
    from .video import stream_info
    from .quality import ssim
    a, b = stream_info(src) or {}, stream_info(out) or {}
    da, db = float(a.get("duration") or 0), float(b.get("duration") or 0)
    if da <= 0 or db <= 0 or abs(da - db) > max(0.5, da * 0.01):
        return False
    if _has_audio(src) and not _has_audio(out):
        return False
    times = [da * 0.1, da * 0.5, max(0.0, da * 0.9 - 0.1)]
    fa, fb = _frames(src, times), _frames(out, times)
    if not fa or not fb or len(fa) != len(fb):
        return False
    import numpy as np
    for x, y in zip(fa, fb):
        if x.shape != y.shape:
            h, w = min(x.shape[0], y.shape[0]), min(x.shape[1], y.shape[1])
            x, y = x[:h, :w], y[:h, :w]
        if ssim(np.stack([x] * 3, -1), np.stack([y] * 3, -1)) < min_similarity:
            return False
    return True


# -- the backup: the large original set apart, then let go ---------------------
# In the backup, a converted video's original waits in a folder of its own
# for this long, then goes once the smaller one is checked again.
CONVERTED_DIR = "Converted Originals"
HOLD_DAYS = 5
VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".avi", ".mpg", ".mpeg", ".mts", ".m2ts",
              ".3gp", ".mkv", ".wmv", ".webm", ".dv"}


def _ledger_path(root) -> Path:
    return Path(root) / ".cache" / "videos" / "converted.json"


def _load(root) -> dict:
    import json
    try:
        data = json.loads(_ledger_path(root).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(root, data: dict) -> None:
    import json
    path = _ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(path)


def _rel(root, path) -> str:
    return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()


def record_conversion(root, old_path, new_path, old_size: int, duration: float,
                      now: float | None = None) -> None:
    """Remember that ``old_path`` became ``new_path`` in the library, so its
    large original can be set apart in every backup, then let go."""
    import time
    data = _load(root)
    new_size = Path(new_path).stat().st_size if Path(new_path).is_file() else 0
    entry = {"new": _rel(root, new_path), "old_size": int(old_size), "new_size": int(new_size),
             "duration": float(duration or 0), "at": time.time() if now is None else now,
             "remotes": {}}
    data[_rel(root, old_path)] = entry
    _save(root, data)
    _write_shared(root, _rel(root, old_path), entry)


def find_forgotten(root, index: dict, now: float | None = None) -> int:
    """Originals left in a backup by videos made smaller before this was
    kept track of: the backup has ``X.MOV``, the library has only ``X.mp4``
    made from a file of exactly that size. They are recorded like any
    conversion. Returns how many were found."""
    from .devices import _source_size_of
    root = Path(root)
    data = _load(root)
    found = 0
    for rel, (size, _mtime) in index.items():
        p = Path(rel)
        if (rel in data or not rel.startswith("Originals/")
                or p.suffix.lower() not in VIDEO_EXTS or (root / rel).exists()):
            continue
        small = root / p.with_suffix(".mp4")
        if small.is_file() and small.as_posix() != (root / rel).as_posix() \
                and _source_size_of(small) == size:
            record_conversion(root, root / rel, small, size, 0.0, now)
            data = _load(root)
            found += 1
    return found


def due(root, remote_id: str, now: float | None = None) -> bool:
    """Whether a backup has work waiting: an original to set apart, or one
    kept long enough to let go."""
    import time
    now = time.time() if now is None else now
    for entry in _load(root).values():
        st = entry.get("remotes", {}).get(remote_id, {})
        state = st.get("state", "pending")
        if state == "pending":
            return True
        if state == "held" and now - st.get("held_at", now) >= HOLD_DAYS * 86400:
            return True
    return False


def hold_days_for(remote) -> int:
    """How long a backup keeps a converted video's original. A NAS or a drive
    keeps it HOLD_DAYS: the room is there already. A cloud service is paid for
    by the gigabyte, so only the smaller video stays there."""
    return 0 if getattr(remote, "kind", "") == "rclone" else HOLD_DAYS


def local_hold_needed(remotes) -> bool:
    """Whether this computer has to keep a converted video's original for
    HOLD_DAYS itself: when no backup keeps it - no backup at all, or only
    cloud services."""
    return not any(hold_days_for(r) > 0 for r in remotes)


def local_hold_dir(root) -> Path:
    return Path(root) / ".cache" / "converted-originals"


def set_apart_locally(root, rel: str, now: float | None = None) -> Path:
    """Where a converted video's original waits in the library itself."""
    import time
    now = time.time() if now is None else now
    return local_hold_dir(root) / time.strftime("%Y-%m-%d", time.localtime(now)) / rel


def _small_checks_out(root: Path, entry: dict) -> str | None:
    from .video import stream_info
    small = root / entry["new"]
    if not small.is_file():
        return "the smaller video is missing from the library"
    info = stream_info(small) or {}
    length = float(info.get("duration") or 0)
    want = float(entry.get("duration") or 0)
    if length <= 0 or (want and abs(length - want) > max(0.5, want * 0.01)):
        return "the smaller video no longer plays in full"
    return None


def tend(root, backend, now: float | None = None, index: dict | None = None) -> dict:
    """Do what is due in one backup: set apart the originals whose smaller
    copy is already there in full - or, in a cloud service, let them go at
    once - and let go of those kept long enough once the smaller copy is
    checked again, here and there. An original another computer sent back
    after it was set apart is set apart again."""
    import time
    root = Path(root)
    now = time.time() if now is None else now
    rid = backend.remote.id
    days = hold_days_for(backend.remote)
    counts = {"held": 0, "deleted": 0, "waiting": 0, "problems": 0}
    if index is None:
        index = backend.listing()
    find_forgotten(root, index, now)
    data = _load(root)
    stamp = time.strftime("%Y-%m-%d", time.localtime(now))
    for old_rel, entry in data.items():
        st = entry.setdefault("remotes", {}).setdefault(rid, {"state": "pending"})
        if entry["new"] == old_rel:
            # Made smaller under the same name: sending it replaces the
            # original there, and the backup's own older versions keep that.
            # Nothing to move - moving would take the smaller video away.
            st.setdefault("state", "gone")
            if st["state"] != "gone":
                st.update(state="gone", at=now)
            continue
        small = root / entry["new"]
        remote_small = index.get(entry["new"])
        small_up = (small.is_file() and remote_small is not None
                    and remote_small[0] == small.stat().st_size)
        back = index.get(old_rel)
        if st.get("state") in ("held", "gone") and back and back[0] == entry.get("old_size"):
            st["state"] = "pending"                 # sent back: set apart again
        if st.get("state") == "pending":
            if not small_up:
                counts["waiting"] += 1              # the smaller one isn't up in full yet
                continue
            if old_rel not in index:
                st.update(state="gone", at=now)       # never backed up, or gone already
                continue
            if days == 0:
                if backend.delete(old_rel):
                    st.update(state="gone", deleted_at=now)
                    counts["deleted"] += 1
                else:
                    counts["waiting"] += 1
                continue
            held = f"{CONVERTED_DIR}/{stamp}/{old_rel}"
            if st.get("held_rel") == held:
                held = f"{CONVERTED_DIR}/{stamp}-{int(now)}/{old_rel}"
            if backend.move(old_rel, held):
                st.update(state="held", held_rel=held, held_at=now)
                counts["held"] += 1
            else:
                counts["waiting"] += 1
        elif st.get("state") == "held" and now - st.get("held_at", now) >= days * 86400:
            problem = _small_checks_out(root, entry)
            if problem is None and not small_up:
                problem = "the smaller video is not in the backup in full"
            if problem:
                st["problem"] = problem
                counts["problems"] += 1
                continue
            if backend.delete(st["held_rel"]):
                st.update(state="gone", deleted_at=now)
                st.pop("problem", None)
                counts["deleted"] += 1
    _save(root, data)
    return counts


def tend_local(root, now: float | None = None) -> dict:
    """Originals kept in the library itself (no backup keeps them): let go
    after HOLD_DAYS, once the smaller copy is checked again."""
    import time
    root = Path(root)
    now = time.time() if now is None else now
    counts = {"deleted": 0, "problems": 0}
    data = _load(root)
    for entry in data.values():
        st = entry.get("local")
        if not st or st.get("state") != "held" or now - st.get("held_at", now) < HOLD_DAYS * 86400:
            continue
        problem = _small_checks_out(root, entry)
        if problem:
            st["problem"] = problem
            counts["problems"] += 1
            continue
        held = root / st["held_rel"]
        held.unlink(missing_ok=True)
        st.update(state="gone", deleted_at=now)
        counts["deleted"] += 1
    _save(root, data)
    return counts


def note_local_hold(root, old_rel: str, held_path, now: float | None = None) -> None:
    import time
    data = _load(root)
    if old_rel in data:
        data[old_rel]["local"] = {"state": "held", "held_rel": _rel(root, held_path),
                                  "held_at": time.time() if now is None else now}
        _save(root, data)


# -- other computers: the same videos made smaller there too --------------------
SHARED = "converted-videos.json"


def shared_path(root) -> Path:
    return Path(root) / SHARED


def _write_shared(root, old_rel: str, entry: dict) -> None:
    import json
    path = shared_path(root)
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    videos = dict(data.get("videos") or {})
    videos[old_rel] = {k: entry[k] for k in ("new", "old_size", "new_size", "duration", "at")
                       if k in entry}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"format": "piklin-converted-videos", "version": 1,
                               "videos": videos}, indent=1))
    tmp.replace(path)


def merge_shared(mine: dict | None, theirs: dict | None) -> dict:
    """Videos made smaller on either computer: all of them, the latest
    record of each."""
    videos: dict = {}
    for side in (mine or {}, theirs or {}):
        for rel, e in (side.get("videos") or {}).items():
            if isinstance(e, dict) and e.get("new") and (
                    rel not in videos or float(e.get("at") or 0) > float(videos[rel].get("at") or 0)):
                videos[rel] = e
    return {"format": "piklin-converted-videos", "version": 1, "videos": videos}


def incoming(root) -> dict:
    """Videos another computer made smaller that this library still has at
    full size: old path -> record."""
    import json
    root = Path(root)
    try:
        videos = json.loads(shared_path(root).read_text()).get("videos") or {}
    except (OSError, ValueError, AttributeError):
        return {}
    out = {}
    for rel, e in videos.items():
        old = root / rel
        same_name = e["new"] == rel
        if old.is_file() and old.stat().st_size == e.get("old_size") and (
                same_name or not (root / e["new"]).exists()):
            out[rel] = e
    return out


def apply_incoming(library, catalog, backend, index: dict, now: float | None = None) -> dict:
    """Make this library's copies of those videos smaller the same way: the
    smaller video is fetched from the backup and compared with this
    computer's own original - length, sound and picture - and only then takes
    its place, with its albums, marks and edits; the original goes. Nothing
    is touched when they don't match."""
    import time
    from .photo_rename import repoint_photo
    root = Path(library.root)
    now = time.time() if now is None else now
    counts = {"replaced": 0, "problems": 0, "waiting": 0}
    for old_rel, e in incoming(root).items():
        new_rel = e["new"]
        remote = index.get(new_rel)
        if remote is None or (e.get("new_size") and remote[0] != e["new_size"]):
            counts["waiting"] += 1
            continue
        old = root / old_rel
        new = root / new_rel
        tmp = new.with_name(f".{new.name}.incoming")
        tmp.unlink(missing_ok=True)
        try:
            ok = backend.get(new_rel, tmp) and verify(old, tmp)
        except Exception:
            ok = False
        if not ok:
            tmp.unlink(missing_ok=True)
            counts["problems"] += 1
            continue
        row = catalog.photo_by_path(str(old.resolve()))
        same_name = new == old
        try:
            times = (old.stat().st_atime, old.stat().st_mtime)
            os.replace(tmp, new)
            os.utime(new, times)
            if row is not None:
                repoint_photo(library, catalog, row["id"], old, new,
                              bytes=new.stat().st_size, ext=new.suffix.lstrip(".").lower())
        except Exception:
            if not same_name:
                new.unlink(missing_ok=True)
            tmp.unlink(missing_ok=True)
            counts["problems"] += 1
            continue
        if not same_name:
            old.unlink(missing_ok=True)
        data = _load(root)
        data[old_rel] = {"new": new_rel, "old_size": e.get("old_size"),
                         "new_size": new.stat().st_size, "duration": e.get("duration", 0),
                         "at": e.get("at", now), "remotes": {}, "from_elsewhere": True}
        _save(root, data)
        counts["replaced"] += 1
    return counts
