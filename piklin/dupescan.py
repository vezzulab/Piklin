"""Finding duplicates by looking at what the files really are.

The catalog's quick fingerprint (size plus the head and tail of a file) is
enough to notice a likely copy while scanning, but not to be sure of one. Two
things settle it:

1. Every photo and video, hidden ones included, that is the same size as
   another is read from the first byte to the last: only files whose whole
   content matches are exact copies.
2. Every photo is looked at, and compared with the others for what it shows:
   the same picture saved again, made smaller, sent through a chat or exported
   as a preview is a duplicate even though the files differ. The comparison is
   strict - a shot taken a moment later, an edited version or a frame of a
   burst is not the same picture - and a photo that is flat, a screenshot or a
   RAW file (kept beside its JPEG on purpose) is never compared this way.

A file already read or looked at stays known while it is unchanged, so a second
pass only reads what is new.
"""
from __future__ import annotations

import hashlib
import os
import struct
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

CHUNK = 1 << 20


@dataclass
class Progress:
    phase: str = "listing"          # listing | reading | looking | done
    files: int = 0                  # files in the library
    to_read: int = 0                # files that have to be read
    read: int = 0
    bytes_total: int = 0
    bytes_read: int = 0
    to_look: int = 0                # photos still to be looked at
    looked: int = 0
    missing: int = 0                # in the list but no longer on disk
    unreadable: int = 0
    groups: list = field(default_factory=list)

    @property
    def fraction(self) -> float:
        return min(1.0, self.bytes_read / self.bytes_total) if self.bytes_total else 0.0


def hash_file(path: Path | str, cancel: threading.Event | None = None,
              on_bytes: Callable[[int], None] | None = None) -> str | None:
    """The hash of the whole file, or None when it was cancelled."""
    h = hashlib.blake2b(digest_size=20)
    with open(path, "rb") as fh:
        while True:
            if cancel is not None and cancel.is_set():
                return None
            block = fh.read(CHUNK)
            if not block:
                break
            h.update(block)
            if on_bytes:
                on_bytes(len(block))
    return h.hexdigest()


# -- looking at the picture ---------------------------------------------------
# Measured on a real library (21,000 photos): copies saved again, made smaller
# or sent through a chat differ from the original by under 0.008 on a 64 x 64
# grey copy, once each is brought to the same brightness and contrast. Shots
# of a burst, and an edited version, start above 0.02.
SIG_SIDE = 16                     # the small copy kept in the catalog
CHECK_SIDE = 64                   # the copy two candidates are compared on
LOOK_MAD = 0.008                  # the most two pictures may differ on average
LOOK_NCC = 0.9995                 # and the least they must correlate
LOOK_ASPECT = 0.03                # the shape of the picture may differ this much
LOOK_PREFILTER = 0.10             # a coarse difference worth a closer look
LOOK_SECONDS = 2.0                # a photo with a capture time this far from another's is another shot
FLAT = 3.0                        # a picture with less contrast than this matches anything
BUCKET_MAX = 60                   # a coarse shape this common says nothing
SCREENSHOT_WORDS = ("screenshot", "screen shot", "captura de pantalla", "screencapture", "simulator")


def _skip_look(row) -> bool:
    """Photos that are not compared by their picture: videos (a still of a
    video says little), RAW files (kept next to their JPEG on purpose), and
    screenshots (two screens of one app look alike, with different words)."""
    from . import imageio as iio
    path = str(row["path"])
    ext = os.path.splitext(path)[1].lower()
    if (row["duration"] or 0) > 0 or row["paired_to"] is not None or ext in iio.RAW_EXT:
        return True
    name = os.path.basename(path).lower()
    if any(w in name for w in SCREENSHOT_WORDS):
        return True
    return ext == ".png" and not (row["camera_make"] or row["camera_model"])


def signature(path) -> bytes | None:
    """The shape of the picture and a 16 x 16 grey copy of it, or None when
    it cannot be read. Kept in the catalog, 260 bytes a photo."""
    from PIL import Image
    from . import imageio as iio
    try:
        im = iio.load_pil(path, 96)
        w, h = im.size
        grey = im.convert("L").resize((SIG_SIDE, SIG_SIDE), Image.BILINEAR)
        return struct.pack("<f", w / max(h, 1)) + grey.tobytes()
    except Exception:
        return None


def _grey(path, side: int):
    import numpy as np
    from PIL import Image
    from . import imageio as iio
    try:
        im = iio.load_pil(path, side * 2).convert("L").resize((side, side), Image.BILINEAR)
        return np.asarray(im, np.float32)
    except Exception:
        return None


def same_picture(a, b) -> bool:
    """Whether two grey copies (arrays) are the same picture: the same light
    and dark in the same places, once brightness and contrast are matched."""
    import numpy as np
    if a is None or b is None or a.std() < FLAT or b.std() < FLAT:
        return False
    za = (a - a.mean()) / a.std()
    zb = (b - b.mean()) / b.std()
    return (float(np.abs(za - zb).mean()) <= LOOK_MAD
            and float((za * zb).mean()) >= LOOK_NCC)


def _candidates(sigs, aspects, is_new) -> list[tuple[int, int]]:
    """Pairs of photos (positions) whose small copies are close enough to be
    worth comparing closely, where at least one is new since the last pass.

    A coarse 64-bit shape (which side of each neighbour is brighter) is cut in
    four; two pictures within three bits of each other share at least one whole
    quarter, so only those that share one are compared."""
    import numpy as np
    n = len(sigs)
    if n < 2:
        return []
    x = sigs.astype(np.float32)
    sd = x.std(1, keepdims=True)
    usable = sd[:, 0] >= FLAT
    z = (x - x.mean(1, keepdims=True)) / np.maximum(sd, FLAT)
    grid = x.reshape(n, 8, 2, 8, 2).mean((2, 4))
    grid = np.concatenate([grid, grid[:, :, -1:]], axis=2)
    keys = np.packbits((grid[:, :, 1:] > grid[:, :, :-1]).reshape(n, 64), axis=1)
    found: set[tuple[int, int]] = set()
    for i in (0, 2, 4, 6):
        band = (keys[:, i].astype(np.uint16) << 8) | keys[:, i + 1]
        order = np.argsort(band, kind="stable")
        sorted_band = band[order]
        cuts = np.flatnonzero(np.diff(sorted_band)) + 1
        for chunk in np.split(order, cuts):
            if not 2 <= len(chunk) <= BUCKET_MAX:
                continue
            for u in range(len(chunk)):
                for v in range(u + 1, len(chunk)):
                    a, b = int(chunk[u]), int(chunk[v])
                    if (is_new[a] or is_new[b]) and usable[a] and usable[b]:
                        found.add((min(a, b), max(a, b)))
    if not found:
        return []
    pairs = np.array(sorted(found))
    a, b = pairs[:, 0], pairs[:, 1]
    shape = np.abs(aspects[a] / np.maximum(aspects[b], 1e-6) - 1.0) <= LOOK_ASPECT
    close = np.zeros(len(pairs), bool)
    for start in range(0, len(pairs), 50_000):
        sl = slice(start, start + 50_000)
        close[sl] = np.abs(z[a[sl]] - z[b[sl]]).mean(1) < LOOK_PREFILTER
    keep = pairs[shape & close]
    return [(int(u), int(v)) for u, v in keep]


def _is_export_of(a, b) -> bool:
    """Piklin saves an edited photo or video as "<name>_edited": a file the person made on purpose, not a copy."""
    na = os.path.splitext(os.path.basename(str(a["path"])))[0].lower()
    nb = os.path.splitext(os.path.basename(str(b["path"])))[0].lower()
    return na == nb + "_edited" or nb == na + "_edited" or na.startswith(nb + "_edited") or nb.startswith(na + "_edited")


def _same_moment(a, b) -> bool:
    """False when both photos say, from the camera, that they were taken at
    different moments: the same scene a moment later is another shot."""
    if a["date_source"] == "exif" and b["date_source"] == "exif":
        return abs((a["taken_at"] or 0) - (b["taken_at"] or 0)) <= LOOK_SECONDS
    return True


class _Groups:
    """Photos that turned out to be the same, joined into sets."""

    def __init__(self):
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def join(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)

    def sets(self) -> list[list[int]]:
        out: dict[int, list[int]] = {}
        for x in list(self.parent):
            out.setdefault(self.find(x), []).append(x)
        return [sorted(v) for v in out.values() if len(v) > 1]


def look_pass(catalog, p: Progress, report, cancel: threading.Event | None = None) -> None:
    """Look at every photo that has not been looked at, then work out which
    photos are the same picture and say so in the catalog."""
    import numpy as np
    from concurrent.futures import ThreadPoolExecutor
    from . import system

    rows = [r for r in catalog.q(
        "SELECT id, path, bytes || ':' || mtime AS stamp, duration, paired_to, "
        "camera_make, camera_model, taken_at, date_source, look_sig, look_key, "
        "looked_key, content_hash, hashed_key FROM photos WHERE trashed_at IS NULL")
        if not _skip_look(r)]
    fresh = lambda r: r["looked_key"] == r["stamp"] and r["look_sig"] is not None
    todo = [r for r in rows if not fresh(r)]
    p.to_look, p.looked, p.phase = len(todo), 0, "looking"
    report()
    if not todo:
        return                              # nothing new since the last pass
    workers = max(1, system.work_budget("images"))
    sigs: dict[int, bytes] = {}
    pending: list[tuple] = []

    def flush():
        if pending:
            with catalog.write() as cur:
                cur.executemany("UPDATE photos SET look_sig=?, "
                                "looked_key=bytes || ':' || mtime WHERE id=?", pending)
            pending.clear()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for start in range(0, len(todo), workers * 8):
            if cancel is not None and cancel.is_set():
                break
            batch = todo[start:start + workers * 8]
            for r, sig in zip(batch, pool.map(lambda r: signature(r["path"]), batch)):
                p.looked += 1
                if sig is None:
                    p.unreadable += 1
                    continue
                sigs[r["id"]] = sig
                pending.append((sig, r["id"]))
            flush()
            report()
    flush()
    if cancel is not None and cancel.is_set():
        return

    # Everything that has a small copy now, the ones just made and the ones kept.
    have = []
    for r in rows:
        sig = sigs.get(r["id"]) or (r["look_sig"] if fresh(r) else None)
        if sig is not None and len(sig) == 4 + SIG_SIDE * SIG_SIDE:
            have.append((r, sig))
    if not have:
        return
    ids = [r["id"] for r, _ in have]
    grids = np.array([np.frombuffer(s, np.uint8, offset=4) for _, s in have])
    aspects = np.array([struct.unpack("<f", s[:4])[0] for _, s in have], np.float32)
    is_new = np.array([r["id"] in sigs for r, _ in have])
    by_id = {r["id"]: r for r, _ in have}

    joined = _Groups()
    # what was found before stands, for photos that have not changed
    keep: dict[str, list[int]] = {}
    for r, _ in have:
        if r["look_key"] and fresh(r) and r["id"] not in sigs:
            keep.setdefault(r["look_key"], []).append(r["id"])
    for members in keep.values():
        for other in members[1:]:
            joined.join(members[0], other)
    # exact copies are the same picture
    by_hash: dict[str, list[int]] = {}
    for r, _ in have:
        if r["content_hash"] and r["hashed_key"] == r["stamp"]:
            by_hash.setdefault(r["content_hash"], []).append(r["id"])
    for members in by_hash.values():
        for other in members[1:]:
            joined.join(members[0], other)

    # the new ones against all: a closer look at each likely pair
    pairs = _candidates(grids, aspects, is_new)
    pairs = [(a, b) for a, b in pairs if _same_moment(by_id[ids[a]], by_id[ids[b]])
             and not _is_export_of(by_id[ids[a]], by_id[ids[b]])]
    need = sorted({i for pair in pairs for i in pair})
    pics: dict[int, object] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for start in range(0, len(need), workers * 16):
            if cancel is not None and cancel.is_set():
                return
            part = need[start:start + workers * 16]
            for i, pic in zip(part, pool.map(lambda i: _grey(by_id[ids[i]]["path"], CHECK_SIDE), part)):
                pics[i] = pic
    for a, b in pairs:
        if same_picture(pics.get(a), pics.get(b)):
            joined.join(ids[a], ids[b])

    chosen: dict[int, str] = {}
    for members in joined.sets():
        key = f"v{members[0]}"
        for m in members:
            if m in by_id:
                chosen[m] = key
    changes = [(chosen.get(i), i) for i in ids if chosen.get(i) != by_id[i]["look_key"]]
    if changes:
        with catalog.write() as cur:
            cur.executemany("UPDATE photos SET look_key=? WHERE id=?", changes)


# -- looking at videos ----------------------------------------------------------------
# The same video kept twice - the camera's original and the smaller copy Piklin made, or one
# imported again with another name - is two files that share nothing a hash can see. Two videos
# are the same when they are as long (a third of a second, or four thousandths), have the same
# shape, were not made at different moments, and look alike at five moments of their length:
# each one, and on average, as much as the check Piklin makes before a smaller copy takes the
# place of an original (0.88 at the least: a video made half the size scores about 0.92).
VIDEO_SECONDS = 0.3
VIDEO_MIN = 0.88
VIDEO_MEAN = 0.92
VIDEO_MOMENT = 2.0                 # seconds apart that the camera's own dates may differ
SEEN = b"\x01"                     # in look_sig: this video has been looked at (it has no small copy)


def _same_shape(a, b) -> bool:
    wa, ha, wb, hb = (a["width"] or 0), (a["height"] or 0), (b["width"] or 0), (b["height"] or 0)
    if not (wa and ha and wb and hb):
        return True                  # not known: the pictures decide
    ra, rb = wa / ha, wb / hb
    return abs(ra / rb - 1.0) <= LOOK_ASPECT or abs(ra * rb - 1.0) <= LOOK_ASPECT   # turned a quarter too


def video_pass(catalog, p: Progress, report, cancel: threading.Event | None = None) -> None:
    """Find the videos that are the same video, and say so in the catalog."""
    from . import videospace
    rows = [r for r in catalog.q(
        "SELECT id, path, bytes || ':' || mtime AS stamp, duration, width, height, taken_at, date_source, "
        "look_sig, look_key, looked_key, content_hash, hashed_key FROM photos "
        "WHERE trashed_at IS NULL AND duration > 0 AND paired_to IS NULL") if os.path.isfile(r["path"])]
    fresh = lambda r: r["looked_key"] == r["stamp"] and r["look_sig"] == SEEN
    new = {r["id"] for r in rows if not fresh(r)}
    if not new:
        return
    by_id = {r["id"]: r for r in rows}
    joined = _Groups()
    kept: dict[str, list[int]] = {}
    for r in rows:                                   # what was found before stands, for videos that have not changed
        if r["look_key"] and fresh(r) and r["id"] not in new:
            kept.setdefault(r["look_key"], []).append(r["id"])
    for members in kept.values():
        for other in members[1:]:
            joined.join(members[0], other)
    by_hash: dict[str, list[int]] = {}
    for r in rows:                                   # exact copies are the same video
        if r["content_hash"] and r["hashed_key"] == r["stamp"]:
            by_hash.setdefault(r["content_hash"], []).append(r["id"])
    for members in by_hash.values():
        for other in members[1:]:
            joined.join(members[0], other)
    order = sorted(rows, key=lambda r: r["duration"])
    pairs = []
    for i, a in enumerate(order):
        for b in order[i + 1:]:
            if b["duration"] - a["duration"] > max(VIDEO_SECONDS, 0.004 * a["duration"]):
                break
            if a["id"] not in new and b["id"] not in new:
                continue
            if joined.find(a["id"]) == joined.find(b["id"]) or not _same_shape(a, b) or _is_export_of(a, b):
                continue
            if (a["date_source"] == "metadata" and b["date_source"] == "metadata"
                    and abs((a["taken_at"] or 0) - (b["taken_at"] or 0)) > VIDEO_MOMENT):
                continue                             # made at another moment: another video
            pairs.append((a, b))
    p.to_look += len(pairs)
    p.phase = "looking"
    report()
    for n, (a, b) in enumerate(pairs):
        if cancel is not None and cancel.is_set():
            return
        p.looked += 1
        if n % 5 == 0:
            report()
        try:
            scores = videospace.frame_scores(a["path"], b["path"], same_sound=True)
        except Exception:
            scores = None
        if scores and min(scores) >= VIDEO_MIN and sum(scores) / len(scores) >= VIDEO_MEAN:
            joined.join(a["id"], b["id"])
    chosen: dict[int, str] = {}
    for members in joined.sets():
        key = f"w{members[0]}"
        for m in members:
            if m in by_id:
                chosen[m] = key
    with catalog.write() as cur:
        cur.executemany("UPDATE photos SET look_sig=?, looked_key=bytes || ':' || mtime, look_key=? WHERE id=?",
                        [(SEEN, chosen.get(i), i) for i in new])
        cur.executemany("UPDATE photos SET look_key=? WHERE id=?",
                        [(chosen.get(i), i) for i in by_id if i not in new and chosen.get(i) != by_id[i]["look_key"]])



def repair_fingerprints(catalog, cancel: threading.Event | None = None) -> int:
    """Bring the quick fingerprint of every video up to date. A video replaced by
    its smaller copy kept the fingerprint of the large one, and showed as a
    duplicate of any file that still had it. Returns how many were wrong."""
    from . import imageio as iio
    fixed = []
    for r in catalog.q("SELECT id, path, fingerprint FROM photos WHERE duration > 0"):
        if cancel is not None and cancel.is_set():
            break
        try:
            now = iio.fingerprint(r["path"])
        except OSError:
            continue
        if now != r["fingerprint"]:
            fixed.append((now, r["id"]))
    if fixed:
        with catalog.write() as cur:
            cur.executemany("UPDATE photos SET fingerprint=? WHERE id=?", fixed)
    return len(fixed)


def scan(catalog, on_progress: Callable[[Progress], None] | None = None,
         cancel: threading.Event | None = None) -> Progress:
    """Read everything that could be a copy and return the groups found."""
    p = Progress()

    def report():
        if on_progress:
            on_progress(p)

    rows = catalog.q(
        "SELECT id, path, bytes, (hashed_key = bytes || ':' || mtime "
        "AND content_hash IS NOT NULL) AS fresh FROM photos "
        "WHERE trashed_at IS NULL")
    p.files = len(rows)
    report()

    by_size: dict[int, list] = {}
    for r in rows:
        try:
            size = os.stat(r["path"]).st_size
        except OSError:
            p.missing += 1
            continue
        if size == 0 or size != r["bytes"]:
            # A file changed since it was listed: the next scan of the
            # library brings its size up to date, then it is read here.
            continue
        by_size.setdefault(size, []).append(r)

    todo = [r for group in by_size.values() if len(group) > 1
            for r in group if not r["fresh"]]
    p.to_read = len(todo)
    p.bytes_total = sum(r["bytes"] for r in todo)
    p.phase = "reading"
    report()

    pending: list[tuple[str, int]] = []

    def flush():
        if pending:
            with catalog.write() as cur:
                cur.executemany(
                    "UPDATE photos SET content_hash=?, "
                    "hashed_key=bytes || ':' || mtime WHERE id=?", pending)
            pending.clear()

    def add_bytes(n):
        p.bytes_read += n

    for r in todo:
        if cancel is not None and cancel.is_set():
            break
        try:
            digest = hash_file(r["path"], cancel, add_bytes)
        except OSError:
            p.unreadable += 1
            continue
        if digest is None:
            break
        pending.append((digest, r["id"]))
        p.read += 1
        if len(pending) >= 50:
            flush()
        report()
    flush()

    if cancel is None or not cancel.is_set():
        look_pass(catalog, p, report, cancel)
    if cancel is None or not cancel.is_set():
        video_pass(catalog, p, report, cancel)

    p.groups = catalog.duplicate_groups()
    p.phase = "done"
    report()
    return p
