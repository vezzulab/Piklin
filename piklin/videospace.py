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
import re
from dataclasses import dataclass
from pathlib import Path

# bits per second for the video track, by the long side of the picture
CAPS = ((854, 1_500_000), (1280, 4_000_000), (1920, 8_000_000), (10_000, 20_000_000))
AUDIO_BPS = 160_000
# not worth the work or the small loss of detail below this
MIN_SAVING_BYTES = 20 * 1024 * 1024
MIN_SAVING_SHARE = 0.25
# AV1 is asked for by quality, the best first: the smallest file is tried, and
# if it is not the same video the next is (see devices.shrink_video). Measured
# on phone and camera videos, these leave about a quarter to a half of the size.
AV1_CRFS = (32, 28, 24)
AV1_EXPECTED_SHARE = 0.40
# a video already this thin has little left to take out
AV1_MIN_BPS = 1_200_000
# frames compared on the way back: a little stricter than for H.264
AV1_MIN_SIMILARITY = 0.92


@dataclass(frozen=True)
class Profile:
    """How hard a video is squeezed. ``balanced`` keeps the picture size and
    is what Optimize Videos has always done; ``compressed`` is the choice a
    person makes in Preferences to keep videos small: nothing wider than
    1080p, and a rate low enough that a phone's 4K shrinks to a few percent."""
    id: str
    caps: tuple                    # (long side, bits per second) for H.264
    av1_crfs: tuple                # AV1 qualities, the smallest file first
    av1_share: float               # about how much of the size is left, in AV1
    av1_min_bps: int               # a video already this thin is left alone
    av1_min_similarity: float
    max_side: int | None           # longest edge allowed, or None to keep it
    preset: int                    # SVT-AV1 speed: higher is faster, a little larger
    av1_bps_1080: int = 0          # AV1 rate asked at 1080p, to plan with (0: use the share)


BALANCED = Profile("balanced", CAPS, AV1_CRFS, AV1_EXPECTED_SHARE, AV1_MIN_BPS,
                   AV1_MIN_SIMILARITY, None, 6)
COMPRESSED = Profile("compressed", ((854, 900_000), (1280, 2_200_000), (1920, 3_500_000),
                                    (10_000, 3_500_000)),
                     (38, 34, 30), 0.15, 1_000_000, 0.88, 1920, 8, av1_bps_1080=2_000_000)
PROFILES = {"balanced": BALANCED, "compressed": COMPRESSED}
MODES = ("original", "compressed")        # what a person picks in Preferences


def profile_for(name) -> Profile:
    return PROFILES.get(name) or BALANCED


def av1_available() -> bool:
    """Whether this computer can make AV1, the codec that saves the most."""
    try:
        from . import video_edit as ve
        return ve.encoder_for("video", "av1") is not None
    except Exception:
        return False


def cap_for(width: int, height: int, profile: Profile = BALANCED) -> int:
    side = max(width or 0, height or 0)
    if profile.max_side:
        side = min(side, profile.max_side)       # it will not be larger than this
    for limit, bps in profile.caps:
        if side <= limit:
            return bps
    return profile.caps[-1][1]


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
    profile: str = "balanced"   # how hard to squeeze it (see PROFILES)

    @property
    def saving(self) -> int:
        return max(0, self.bytes - self.expected)

    @property
    def work(self) -> float:
        """How much encoding it takes: pixels times seconds."""
        return max(1, self.width * self.height) * max(self.duration, 0.1)


def _expected_av1(profile: Profile, size: int, dur: float, width: int, height: int,
                  current: float) -> int:
    """About how many bytes an AV1 copy takes."""
    if not profile.av1_bps_1080:
        return int(size * profile.av1_share)
    # a picture is brought down to ``max_side``; the rate follows its pixels
    w, h = max(width, 1), max(height, 1)
    if profile.max_side and max(w, h) > profile.max_side:
        k = profile.max_side / max(w, h)
        w, h = w * k, h * k
    bps = min(current, profile.av1_bps_1080 * min(1.0, w * h / (1920 * 1080)))
    return int(min(size * 0.6, (bps + AUDIO_BPS) * dur / 8))


def _candidate(r, root: Path, prof: Profile, av1: bool, elsewhere: set,
               min_saving: int, min_share: float) -> Candidate | None:
    """The candidate for one catalog row, or None when it is not worth
    making smaller (or is not Piklin's to touch)."""
    try:
        Path(r["path"]).resolve().relative_to(root)
    except ValueError:
        return None                       # outside the library: never touched
    if str(Path(r["path"]).resolve()) in elsewhere:
        return None
    if already_smaller(root, r["path"], int(r["bytes"] or 0)):
        return None
    dur = float(r["duration"] or 0)
    size = int(r["bytes"] or 0)
    if dur <= 0 or size <= 0:
        return None
    current = size * 8 / dur
    if av1:
        if current < prof.av1_min_bps:
            return None
        target = 0                         # by quality, not by a rate
        expected = _expected_av1(prof, size, dur, int(r["width"] or 0),
                                 int(r["height"] or 0), current)
    else:
        target = min(cap_for(r["width"], r["height"], prof), int(current * 0.8))
        expected = int((target + AUDIO_BPS) * dur / 8)
    saving = size - expected
    if saving < min_saving or saving < size * min_share:
        return None
    return Candidate(r["id"], r["path"], size, dur, int(r["width"] or 0),
                     int(r["height"] or 0), target, expected, prof.id)


def plan(catalog, originals: Path | str, min_saving: int = MIN_SAVING_BYTES,
         min_share: float = MIN_SAVING_SHARE, profile: str = "balanced",
         skip: dict | None = None) -> list[Candidate]:
    """The library's videos worth making smaller, the most space for the
    least work first. ``skip`` is what an earlier try learned (see
    ``skipped``): those videos are not tried again."""
    root = Path(originals).resolve()
    prof = profile_for(profile)
    av1 = av1_available()
    out = []
    # Another computer already made these smaller: this one fetches them.
    elsewhere = {str((root / rel).resolve()) for rel in incoming(root)}
    for r in catalog.q(
            "SELECT id, path, bytes, duration, width, height FROM photos "
            "WHERE duration > 0 AND trashed_at IS NULL AND paired_to IS NULL"):
        if skip and skipped(skip, r["id"], int(r["bytes"] or 0)):
            continue
        cand = _candidate(r, root, prof, av1, elsewhere, min_saving, min_share)
        if cand is not None:
            out.append(cand)
    out.sort(key=lambda c: c.saving / c.work, reverse=True)
    return out


def plan_paths(catalog, originals: Path | str, paths, profile: str = "balanced",
               min_saving: int = 5 * 1024 * 1024) -> tuple[list[Candidate], list[str]]:
    """The candidates among these files (a few videos just copied in), and
    the paths the catalog does not know yet: they are asked about again."""
    root = Path(originals).resolve()
    prof = profile_for(profile)
    av1 = av1_available()
    elsewhere = {str((root / rel).resolve()) for rel in incoming(root)}
    found, unknown = [], []
    for path in paths:
        row = catalog.photo_by_path(str(path))
        if row is None:
            unknown.append(str(path))
            continue
        cand = _candidate(row, root, prof, av1, elsewhere, min_saving, MIN_SAVING_SHARE)
        if cand is not None:
            found.append(cand)
    return found, unknown


# -- what an earlier try learned ---------------------------------------------------
# A video that came out no smaller, or that could not be made smaller, is not
# tried again on every look through the library: each try is minutes of the
# computer's work. Remembered by the video and its size, so a changed file is
# looked at again.
RETRY_FAILED_DAYS = 7


def note_skipped(skip: dict, photo_id: int, size: int, result: str, now: float | None = None) -> dict:
    import time
    skip = dict(skip or {})
    skip[str(photo_id)] = {"size": int(size), "result": result,
                           "at": time.time() if now is None else now}
    return skip


def skipped(skip: dict, photo_id: int, size: int, now: float | None = None) -> bool:
    import time
    e = (skip or {}).get(str(photo_id))
    if not isinstance(e, dict) or int(e.get("size") or -1) != int(size):
        return False
    if e.get("result") == "kept":
        return True                        # it would not save enough: that stays true
    now = time.time() if now is None else now
    return now - float(e.get("at") or 0) < RETRY_FAILED_DAYS * 86400


# -- the same, at a backup destination -----------------------------------------------
LEFTOVER_EVERY = 86400.0           # a destination is looked through this often
LEFTOVER_AGE = 86400.0             # and only what has lain there this long goes
_HALF_MADE = re.compile(r"^\.(?P<stem>.+)\.smaller\.mp4\.part$")


def stray_parts(index: dict, now: float | None = None, older_than: float = LEFTOVER_AGE) -> list[str]:
    """The half-made files in a destination's listing (path -> (size, time)):
    an encode that was cut short (".X.smaller.mp4.part") whose video is beside
    it, or a file whose upload was cut short ("X.jpg.part") whose finished
    copy is beside it. Only in Originals, and only those old enough that nobody
    is writing them."""
    import time
    now = time.time() if now is None else now
    names: dict[str, set] = {}
    for rel in index:
        folder, _, name = rel.rpartition("/")
        names.setdefault(folder, set()).add(name)
    out = []
    for rel, (_size, stamp) in index.items():
        if not rel.startswith("Originals/") or not rel.endswith(".part"):
            continue
        if now - float(stamp or 0) < older_than:
            continue
        folder, _, name = rel.rpartition("/")
        beside = names.get(folder, set())
        m = _HALF_MADE.match(name)
        if m:
            stem = m.group("stem")
            if any(n != name and (n == stem or n.startswith(stem + ".") or n.startswith(stem + "-"))
                   for n in beside):
                out.append(rel)
        elif name[:-len(".part")] in beside:
            out.append(rel)
    return sorted(out)


def clean_destination(root, backend, now: float | None = None, index: dict | None = None) -> int:
    """Delete the half-made files found at a backup destination, at most once
    a day for each. Returns how many went."""
    import json
    import time
    root = Path(root)
    now = time.time() if now is None else now
    path = root / ".cache" / "videos" / "leftovers.json"
    try:
        done = json.loads(path.read_text())
    except (OSError, ValueError):
        done = {}
    key = backend.remote.id
    if now - float(done.get(key) or 0) < LEFTOVER_EVERY:
        return 0
    gone = 0
    for rel in stray_parts(backend.listing() if index is None else index, now):
        try:
            if backend.delete(rel):
                gone += 1
        except Exception:
            continue
    done[key] = now
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(done))
    except OSError:
        pass
    return gone


def clear_leftovers(originals: Path | str, older_than: float = 86400.0,
                    now: float | None = None) -> int:
    """Remove the half-made files an encode or a copy leaves when Piklin is
    closed in the middle of it. Only those older than ``older_than`` seconds:
    one being written now is younger. Returns how many went."""
    import time
    now = time.time() if now is None else now
    gone = 0
    for p in Path(originals).rglob(".*"):
        if not p.is_file() or not (p.name.endswith((".smaller.mp4.part", ".incoming"))
                                   or p.name.endswith(".smaller.mp4")):
            continue                        # only hidden ones: rglob(".*") finds nothing else
        try:
            if now - p.stat().st_mtime >= older_than:
                p.unlink()
                gone += 1
        except OSError:
            continue
    return gone


def _has_audio(path) -> bool:
    import av
    try:
        with av.open(str(path)) as c:
            return bool(c.streams.audio)
    except Exception:
        return False


def _container_duration(path) -> float:
    import av
    try:
        with av.open(str(path)) as c:
            if c.duration:
                return float(c.duration) / 1_000_000.0
            v = c.streams.video[0]
            if v.duration and v.time_base:
                return float(v.duration * v.time_base)
    except Exception:
        pass
    return 0.0


def _frames(path, times, side=320, around=0):
    """The frame nearest each moment, as a small grey picture. With ``around``,
    for each moment a list of that frame and the ``around`` before and after
    it: two videos of the same thing seldom put a frame at the very same
    instant, and in a scene with movement one frame apart already looks
    different."""
    import collections

    import av
    import numpy as np

    def small(frame):
        # Made smaller by averaging, not by taking every sixth pixel: a video
        # that is stored sideways and turned for showing has its pixels in
        # another grid, and a grid that starts a few pixels off made the same
        # picture look different.
        import cv2
        img = frame.to_ndarray(format="gray")
        h, w = img.shape
        step = max(1, max(h, w) // side)
        if step > 1:
            img = cv2.resize(img, (max(1, w // step), max(1, h // step)),
                             interpolation=cv2.INTER_AREA)
        return np.asarray(img, dtype=np.float32) / 255.0

    shots = []
    with av.open(str(path)) as c:
        v = c.streams.video[0]
        for t in times:
            c.seek(int(t / v.time_base), stream=v, any_frame=False, backward=True)
            # The frame nearest the moment asked for, in both videos. Taking the
            # first one from 50 ms before it gave a frame three apart when one
            # video has a key frame exactly there and the other does not.
            before = collections.deque(maxlen=around + 1)
            frames, after_left, got = [], around, None
            for frame in c.decode(v):
                if got is None:
                    if frame.time is None or frame.time >= t - 1e-3:
                        prev = before[-1] if before else None
                        if (prev is not None and frame.time is not None
                                and abs(prev.time - t) < abs(frame.time - t)):
                            # the one before is nearer: it is the centre
                            frames = list(before)
                            got = prev
                            after_left = around - 1
                            frames.append(frame)
                        else:
                            frames = list(before) + [frame]
                            got = frame
                        if after_left <= 0:
                            break
                        continue
                    before.append(frame)
                else:
                    frames.append(frame)
                    after_left -= 1
                    if after_left <= 0:
                        break
            if got is None:
                got = before[-1] if before else None
                frames = list(before)
            if got is None:
                return None
            shots.append([small(f) for f in frames] if around else small(got))
    return shots


def frame_scores(src, out, same_sound: bool = False) -> list[float] | None:
    """How alike two videos look at their start, middle and end: the best match of
    each of five moments (0..1), or None when they cannot be the same video - not as
    long, or ``out`` has no sound where ``src`` had (with ``same_sound``, any
    difference in having sound)."""
    from .video import stream_info
    from .quality import ssim
    # How long each is, as the file says. Frames divided by the frame rate is
    # wrong for a video whose rate varies (a screen recording): it made one video
    # two percent longer than another that was the same.
    da, db = _container_duration(src), _container_duration(out)
    if da <= 0 or db <= 0:
        a, b = stream_info(src) or {}, stream_info(out) or {}
        da, db = float(a.get("duration") or 0), float(b.get("duration") or 0)
    if da <= 0 or db <= 0 or abs(da - db) > max(0.5, da * 0.01):
        return None
    sound_a, sound_b = _has_audio(src), _has_audio(out)
    if (sound_a and not sound_b) or (same_sound and sound_a != sound_b):
        return None
    times = [da * 0.1, da * 0.3, da * 0.5, da * 0.7, max(0.0, da * 0.9 - 0.1)]
    fa, fb = _frames(src, times, around=2), _frames(out, times)
    if not fa or not fb or len(fa) != len(fb):
        return None
    import numpy as np
    scores = []
    for xs, y in zip(fa, fb):
        # A phone's upright video is stored sideways with a rotation flag;
        # its frames decode sideways while the smaller copy is written
        # upright. Whichever turn lines them up is the one that counts - and
        # whichever of the frames about that moment, for the two videos need
        # not put a frame at the very same instant.
        best = 0.0
        for x in xs:
            for k in range(4):
                z = np.rot90(y, k)
                if abs(z.shape[0] - x.shape[0]) > 2 or abs(z.shape[1] - x.shape[1]) > 2:
                    # a copy brought down to a smaller size has the same shape
                    # sampled a little differently: line it up, if it is the same shape
                    if abs(z.shape[0] / z.shape[1] - x.shape[0] / x.shape[1]) > 0.03:
                        continue
                    z = z[np.linspace(0, z.shape[0] - 1, x.shape[0]).astype(int)][
                        :, np.linspace(0, z.shape[1] - 1, x.shape[1]).astype(int)]
                h, w = min(x.shape[0], z.shape[0]), min(x.shape[1], z.shape[1])
                best = max(best, ssim(np.stack([x[:h, :w]] * 3, -1), np.stack([z[:h, :w]] * 3, -1)))
        scores.append(float(best))
    return scores


def verify(src, out, min_similarity: float = 0.80) -> bool:
    """Whether ``out`` is the same video as ``src``: as long, with sound when
    it had sound, and looking the same at its start, middle and end."""
    scores = frame_scores(src, out)
    return scores is not None and min(scores) >= min_similarity


# -- the backup: the large original set apart, then let go ---------------------
# In the backup, a converted video's original waits in a folder of its own
# for this long, then goes once the smaller one is checked again.
CONVERTED_DIR = "Converted Originals"
HOLD_DAYS = 7
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
    from .backuplock import me
    entry = {"new": _rel(root, new_path), "old_size": int(old_size), "new_size": int(new_size),
             "duration": float(duration or 0), "at": time.time() if now is None else now,
             "remotes": {}, "by": me()[0]}
    # Made smaller again under the same name: a computer that still has the
    # first big original, and one that already took the first smaller copy,
    # both have to recognise this record. Every size the file has had is kept.
    before = data.get(_rel(root, old_path))
    if before and before.get("new") == entry["new"] and int(before.get("new_size") or 0) == int(old_size):
        entry["old_sizes"] = sorted({*before.get("old_sizes", [before.get("old_size")]), int(old_size)} - {None})
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
    videos[old_rel] = {k: entry[k] for k in ("new", "old_size", "old_sizes", "new_size",
                                             "duration", "at", "by") if k in entry}
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
            if not (isinstance(e, dict) and e.get("new")):
                continue
            have = videos.get(rel)
            if have is None:
                videos[rel] = e
                continue
            newer, older = (e, have) if float(e.get("at") or 0) > float(have.get("at") or 0) else (have, e)
            winner = dict(newer)
            if older.get("new") == winner.get("new"):
                # Two computers made the same video smaller, each its own way.
                # The later record stands, and the other's smaller video is one
                # of the sizes it replaces: a computer holding that one takes
                # the later one, and the two never send theirs over each other.
                sizes = {winner.get("old_size"), older.get("old_size"), older.get("new_size"),
                         *(winner.get("old_sizes") or []), *(older.get("old_sizes") or [])}
                sizes.discard(None)
                sizes.discard(0)
                sizes.discard(winner.get("new_size"))
                if sizes:
                    winner["old_sizes"] = sorted(sizes)
            videos[rel] = winner
    return {"format": "piklin-converted-videos", "version": 1, "videos": videos}


def settled(root, path, size: int | None = None) -> bool:
    """Is there nothing left to do for this video here: it is the smaller one
    already, or another computer has made it smaller and it comes from the
    backup. Said from this computer's own record, with no turn to take."""
    root = Path(root)
    try:
        rel = Path(path).resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return False
    return already_smaller(root, path, size) or rel in incoming(root)


# Each computer keeps its own file of the videos it made smaller at the
# destination, and no other computer ever writes to it: nothing can be written
# over by a computer that had an older copy. Everyone reads them all.
LEDGER_DIR = "Smaller Videos"


def _ledger_files_seen(root) -> dict:
    import json
    try:
        data = json.loads((Path(root) / ".cache" / "videos" / "ledgers-seen.json").read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _merge_into_local(root, theirs: dict) -> None:
    import json
    root = Path(root)
    try:
        mine = json.loads(shared_path(root).read_text())
    except (OSError, ValueError):
        mine = {}
    merged = merge_shared(mine, theirs)
    if merged != mine:
        tmp = shared_path(root).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(merged, indent=1))
        tmp.replace(shared_path(root))


def refresh_ledger(root, backend) -> bool:
    """Read what the other computers say they made smaller, at the destination,
    and merge it into this computer's record. True when it has been read (or
    there is nothing there yet); False when it could not be, and nothing should
    be decided from it."""
    import json
    import shutil
    import tempfile
    root = Path(root)
    folder = Path(tempfile.mkdtemp(prefix="piklin-ledger-"))
    try:
        # one file from each computer that has made a video smaller
        try:
            files = backend.list_files(LEDGER_DIR)
        except NotImplementedError:
            files = None
        except Exception:
            return False
        seen = _ledger_files_seen(root)
        if files is not None:
            from .backuplock import me
            own = f"{me()[0]}.json"
            for name, (size, ts) in files.items():
                if name == own or not name.endswith(".json"):
                    continue
                if seen.get(name) == [size, ts]:
                    continue
                target = folder / "ledger.json"
                target.unlink(missing_ok=True)
                if not backend.get(f"{LEDGER_DIR}/{name}", target) or not target.is_file():
                    return False
                _merge_into_local(root, json.loads(target.read_text()))
                seen[name] = [size, ts]
            try:
                path = root / ".cache" / "videos" / "ledgers-seen.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(seen))
            except OSError:
                pass
        # and the one file earlier versions shared, still read
        top = backend.top_listing()
        if SHARED in top:
            target = folder / "shared.json"
            if not backend.get(SHARED, target) or not target.is_file():
                return False
            _merge_into_local(root, json.loads(target.read_text()))
        return True
    except (OSError, ValueError, AttributeError):
        return False
    except Exception:
        return False
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def publish_ledger(root, backend) -> bool:
    """Put what this computer made smaller at the destination, in its own file,
    for the next computer to read before it decides anything. Videos recorded
    before computers were told apart are included: they are everyone's."""
    import json
    import shutil
    import tempfile
    from .backuplock import me
    ident = me()[0]
    try:
        videos = (json.loads(shared_path(root).read_text()).get("videos") or {})
    except (OSError, ValueError, AttributeError):
        return False
    def has_it(e) -> bool:
        # Recorded before computers were told apart: said by a computer that
        # holds the smaller video, which can vouch for it.
        try:
            return (root / e["new"]).stat().st_size == int(e["new_size"])
        except (OSError, KeyError, TypeError, ValueError):
            return False
    root = Path(root)
    mine = {rel: e for rel, e in videos.items()
            if isinstance(e, dict) and (e.get("by") == ident
                                        or (e.get("by") is None and has_it(e)))}
    if not mine:
        return True
    folder = Path(tempfile.mkdtemp(prefix="piklin-ledger-"))
    try:
        source = folder / "ledger.json"
        source.write_text(json.dumps({"format": "piklin-converted-videos", "version": 2,
                                      "videos": mine}))
        return bool(backend.put(source, f"{LEDGER_DIR}/{ident}.json"))
    except Exception:
        return False
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def publish_if_new(root, backend) -> bool:
    """Say at the destination what this computer knows about, if that is not
    what it said last time: before anything is decided, so another computer
    that reads there next does not do it again."""
    import hashlib
    import json
    try:
        videos = json.loads(shared_path(root).read_text()).get("videos") or {}
    except (OSError, ValueError, AttributeError):
        return True
    stamp = hashlib.sha1(json.dumps(videos, sort_keys=True).encode()).hexdigest()
    path = Path(root) / ".cache" / "videos" / "published.json"
    try:
        done = json.loads(path.read_text())
    except (OSError, ValueError):
        done = {}
    key = backend.remote.id
    if done.get(key) == stamp:
        return True
    if not publish_ledger(root, backend):
        return False
    done[key] = stamp
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(done))
    except OSError:
        pass
    return True


def recheck_products(root, backend, every: float = 600.0) -> int:
    """Look, folder by folder, at this computer's smaller videos at the
    destination; any that is not there, or is there at another size, is
    forgotten as sent so the next backup sends it. The way a destination is
    changed under a computer - another computer sending its large original over
    a smaller video - shows nowhere at its top, so the top cannot be trusted
    to say nothing changed. Looked at most every ``every`` seconds. Returns how
    many were found."""
    import json
    import time
    root = Path(root)
    path = root / ".cache" / "videos" / "rechecked.json"
    try:
        done = json.loads(path.read_text())
    except (OSError, ValueError):
        done = {}
    key = backend.remote.id
    now = time.time()
    if now - float(done.get(key) or 0) < every:
        return 0
    try:
        videos = json.loads(shared_path(root).read_text()).get("videos") or {}
    except (OSError, ValueError, AttributeError):
        return 0
    groups: dict[str, list] = {}
    for e in videos.values():
        if not isinstance(e, dict) or not e.get("new") or not e.get("new_size"):
            continue
        try:
            size = (root / e["new"]).stat().st_size
        except OSError:
            continue
        if size == int(e["new_size"]) and "/" in e["new"]:
            folder, name = e["new"].rsplit("/", 1)
            groups.setdefault(folder, []).append((e["new"], name, size))
    if not groups:
        return 0
    manifest = backend._load_manifest(root)
    found = 0
    for folder, items in groups.items():
        try:
            listed = backend.list_files(folder)
        except NotImplementedError:
            return 0
        except Exception:
            return found                    # could not read it: nothing is decided
        for rel, name, size in items:
            have = listed.get(name)
            if (have is None or have[0] != size) and rel in manifest:
                del manifest[rel]
                found += 1
    if found:
        backend._save_manifest(root, manifest)
    done[key] = now
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(done))
    except OSError:
        pass
    return found


def already_smaller(root, path, size: int | None = None) -> bool:
    """Is this video itself a smaller one Piklin made, here or on another
    computer? Such a video is not made smaller again: that was work done
    twice, and every time a computer woke up to find the same videos waiting."""
    import json
    root = Path(root)
    try:
        videos = json.loads(shared_path(root).read_text()).get("videos") or {}
        rel = Path(path).resolve().relative_to(root.resolve()).as_posix()
        size = Path(path).stat().st_size if size is None else size
    except (OSError, ValueError, AttributeError):
        return False
    return any(isinstance(e, dict) and e.get("new") == rel
               and int(e.get("new_size") or 0) == int(size) for e in videos.values())


def repair_backup(root, index: dict, manifest: dict) -> int:
    """Smaller videos this computer holds that the backup does not have, or has
    at another size: forget that they were sent, so the next backup sends them.

    It happens when another computer, still holding the large original, sends
    it over the smaller video; this one believed the smaller one was there and
    never looked again, and every computer waiting for it waited for good.
    Returns how many were found."""
    import json
    root = Path(root)
    try:
        videos = json.loads(shared_path(root).read_text()).get("videos") or {}
    except (OSError, ValueError, AttributeError):
        return 0
    found = 0
    for e in videos.values():
        if not isinstance(e, dict) or not e.get("new") or not e.get("new_size"):
            continue
        rel = e["new"]
        try:
            size = (root / rel).stat().st_size
        except OSError:
            continue
        have = index.get(rel)
        if size == int(e["new_size"]) and (have is None or have[0] != size) and rel in manifest:
            del manifest[rel]
            found += 1
    return found


def superseded(root) -> set:
    """Videos this computer still has at full size that another computer made
    smaller: they are not sent to the backup, or they would undo that."""
    return {Path(root) / rel for rel in incoming(root)}


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
        sizes = {e.get("old_size"), *(e.get("old_sizes") or [])}
        if old.is_file() and old.stat().st_size in sizes and (
                same_name or not (root / e["new"]).exists()):
            out[rel] = e
    return out


def apply_incoming(library, catalog, backend, index: dict, now: float | None = None,
                   on_progress=None) -> dict:
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
    todo = incoming(root)
    for n, (old_rel, e) in enumerate(todo.items()):
        if on_progress is not None:
            on_progress(n, len(todo))
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


# -- doing it --------------------------------------------------------------------
def convert_one(library, catalog, candidate: Candidate, remotes, now: float | None = None,
                on_progress=None, cancel=None, pause=None) -> str:
    """Make one planned video smaller, with everything that goes with it:
    the check that the smaller copy is the same video, the original kept in
    the library when no backup keeps it, and the record other backups and
    computers follow. Returns what devices.shrink_video returns."""
    import time
    from . import devices
    root = Path(library.root)
    now = time.time() if now is None else now
    old = Path(candidate.path)
    if not old.is_file():
        return "gone"
    rel = _rel(root, old)
    if rel in incoming(root):
        return "gone"          # another computer did it: the smaller one comes from the backup
    if already_smaller(root, old):
        return "gone"          # this is the smaller one already: it is not made smaller twice
    size = old.stat().st_size
    hold = set_apart_locally(root, rel, now) if local_hold_needed(remotes) else None
    av1 = av1_available()
    prof = profile_for(candidate.profile)
    result = devices.shrink_video(
        library, catalog, candidate.photo_id, on_progress=on_progress,
        cancel=cancel, bit_rate=candidate.bit_rate,
        verify=((lambda a, b: verify(a, b, prof.av1_min_similarity)) if av1 else verify),
        set_apart=hold, fmt="av1" if av1 else "mp4", crfs=prof.av1_crfs if av1 else (),
        max_side=prof.max_side, preset=prof.preset, pause=pause)
    if result == "smaller":
        row = catalog.photo(candidate.photo_id)
        new = Path(row["path"]) if row is not None else old
        record_conversion(root, old, new, size, candidate.duration, now)
        if hold is not None and hold.is_file():
            note_local_hold(root, rel, hold, now)
    return result


def open_turn(library, remotes, turns: list) -> str:
    """Take this computer's turn at every destination and read what the others
    have done there. "ok" when it can go on; "busy" when another computer has
    the turn; "unread" when a destination could not be read - nothing is
    decided without reading it."""
    from . import backuplock
    for r in remotes:
        try:
            backend = r.backend()
            try:
                turn = backuplock.take(backend, backuplock.VIDEOS_LOCK)
            except backuplock.Busy:
                return "busy"
            turns.append((backend, turn))
            if not refresh_ledger(library.root, backend):
                return "unread"
        except Exception:
            return "unread"
    return "ok"


def make_smaller(library, catalog, remotes, item: dict, cancel=None, on_progress=None, pause=None):
    """One waiting video, from start to finish, however many computers share
    the destination: it takes the turn (one computer at a time does this),
    reads what the others have done there, decides from that, makes the video
    smaller, says so at the destination before the turn ends, and lets the
    turn go. Returns (result, bytes freed); the result is what convert_one
    returns, or "busy" (another computer has the turn) or "unread" (the
    destination could not be read) - the video then waits."""
    result, freed = "gone", 0
    turns: list = []
    try:
        row = catalog.photo(item["id"])
        if row is None or settled(library.root, row["path"], int(row["bytes"] or 0)):
            return "gone", 0                   # nothing to do: no turn needed
        gate = open_turn(library, remotes, turns)
        if gate != "ok":
            return gate, 0
        row = catalog.photo(item["id"])
        cand = Candidate(row["id"], row["path"], int(row["bytes"] or 0),
                         float(row["duration"] or 0), int(row["width"] or 0),
                         int(row["height"] or 0), int(item["bit_rate"]), 0,
                         item.get("profile") or "balanced")
        result = convert_one(library, catalog, cand, remotes,
                             on_progress=on_progress, cancel=cancel, pause=pause)
        if result == "smaller":
            after = catalog.photo(item["id"])
            freed = cand.bytes - int(after["bytes"] or 0) if after is not None else 0
            # Said at once, before the turn ends: the next computer reads it
            # and does not do this one again.
            for backend, _turn in turns:
                publish_ledger(library.root, backend)
        return result, freed
    finally:
        for _backend, turn in turns:
            if turn is not None:
                turn.release()


def measure_speed(candidate: Candidate, seconds: float = 5.0) -> float:
    """How fast this computer makes video smaller: pixels of picture encoded
    per second of waiting, measured on a few seconds of a real video."""
    import tempfile
    import time
    from . import video_edit as ve
    start = min(max(0.0, candidate.duration / 2 - seconds / 2), max(0.0, candidate.duration - seconds))
    end = min(candidate.duration, start + seconds)
    with tempfile.TemporaryDirectory() as tmp:
        t0 = time.monotonic()
        av1 = av1_available()
        prof = profile_for(candidate.profile)
        ve.export(candidate.path, Path(tmp) / "sample.mp4",
                  ve.VideoEdit(duration=candidate.duration, start=start, end=end),
                  fmt="av1" if av1 else "mp4", bit_rate=candidate.bit_rate,
                  crf=prof.av1_crfs[0] if av1 else None, max_side=prof.max_side,
                  preset=prof.preset)
        spent = max(time.monotonic() - t0, 0.05)
    return max(1, candidate.width * candidate.height) * (end - start) / spent


def estimate_seconds(candidates, speed: float) -> float:
    """How long making all of ``candidates`` smaller would take at ``speed``."""
    if speed <= 0:
        return 0.0
    return sum(c.work for c in candidates) / speed
