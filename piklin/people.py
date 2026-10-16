"""People: the faces in the library, grouped by who they are, on this computer.

Nothing leaves it. A small detector (YuNet) finds the faces and a small
recognition model (SFace) turns each into 128 numbers; faces of one person
come out close together. Those numbers are kept in the catalog, and nothing
else is: no face is ever sent to any service.

What the numbers can and cannot do was checked on a real library:

* adults are told apart well; babies and small children are not, and the same
  child at two ages is not the same to the model;
* so Piklin is careful in one direction. It would rather split one person into
  two groups, which one tap puts back together, than mix two people, which
  someone has to find and undo. The groups it makes are suggestions: a person
  has a name, and faces are confirmed, taken out or ignored only by the user,
  and what the user said is never undone by a later grouping.
"""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

import numpy as np

from . import imageio as iio
from .engine import faces as detector
from .logs import log

RECOGNIZER = "face_recognition_sface_2021dec.onnx"
VERSION = 2                 # of the way faces are looked for: 2 also looks for faces lying on their side
ANALYSIS_SIDE = 1280        # the photo is looked at no larger than this
MIN_FACE_PX = 48            # a smaller face is too little to tell anyone by
MIN_SCORE = 0.8             # the detector's own confidence
# How alike faces must be, on average, to be one person (cosine; SFace's own
# line between "same" and "different" is 0.363, which mixes children).
SAME_PERSON = 0.50
# A group is a person once it has this many different photos.
MIN_PHOTOS = 3
# A face turned this far to the side (-1 to 1) says too little about who it is
# to start a person: it can only join one that already exists.
SEED_YAW = 0.6
SEED_ROLL = 45                  # nor does a head turned over more than this start one

# The nearest few faces the user confirmed of a person, this alike, say a face
# is theirs even when it is far from the average of all of them.
KNN_JOIN = 0.50

# A new face joins a person that the user already confirmed a little more
# easily than it starts a new group: the person is known.
JOIN_PERSON = 0.48
# ...and by this much more than to the next person, or it stays loose.
JOIN_MARGIN = 0.04

# Two people this alike, on average, may be one: Piklin asks instead of joining
# them (below SAME_PERSON it would have joined them already).
ASK_FROM = 0.40

# What the user's answers can move the lines to, never past these.
AUTO_JOIN_FLOOR = 0.42      # alike enough to join without asking, at the lowest
MIN_ANSWERS = 6             # answers needed before they change anything
# A face this like one the user took out of a person is the same picture again.
REJECTED_LIKE = 0.8

# A person with this many faces is known well: a new face is put in them by its
# nearest few faces, not by the average (which a child in two poses or a head
# turned does not match), if it is this alike to them and clearly more than to
# any other well known person. Checked on a library of 19 000 faces: right 99.5
# times in 100 with a margin of 0.08, 99.9 with 0.15; the likeness that is asked
# keeps other people out (their faces reach 0.62 or more one time in ten).
ESTABLISHED_FACES = 300
ESTABLISHED_ALIKE = 0.58        # the faces of the person itself reach 0.81 (nine in ten, 0.67); other people's, 0.44
ESTABLISHED_MARGIN = 0.12
ESTABLISHED_NEAREST = 5
ESTABLISHED_SAMPLE = 1500       # of a person's faces, at most this many are compared

# "Find More People" is a little less strict, on request.
RELAX_JOIN = 0.05
MIN_PHOTOS_MORE = 2

# face states
AUTO, CONFIRMED, REMOVED, IGNORED = 0, 1, 2, 3

_local = threading.local()

# When anything that other computers should hear about last changed (see peoplesync.py)
changed_at = 0.0


def touch() -> None:
    global changed_at
    changed_at = time.time()


def model_path() -> Path | None:
    here = detector.model_path()
    if here is None:
        return None
    p = here.with_name(RECOGNIZER)
    return p if p.is_file() else None


def _quiet_opencv() -> None:
    """OpenCV 5 says, for every network it makes, that it ignores the target it
    was asked for. It is true and harmless; the log is not the place for it."""
    try:
        import cv2
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
    except Exception:
        pass


def _recognizer():
    """This thread's recognition network. Each thread has its own: one network
    is not for two threads at once, and sharing one made them wait in line."""
    nets = getattr(_local, "rec", None)
    if nets is None:
        mp = model_path()
        nets = False
        if mp is not None:
            try:
                import cv2
                _quiet_opencv()
                nets = cv2.FaceRecognizerSF.create(str(mp), "")
            except Exception as exc:
                log.warning("face recognition is not available: %s", exc)
        _local.rec = nets
    return nets or None


def _detector():
    net = getattr(_local, "det", None)
    if net is None:
        net = False
        mp = detector.model_path()
        if mp is not None and detector.ops.HAVE_CV:
            try:
                import cv2
                _quiet_opencv()
                net = cv2.FaceDetectorYN.create(str(mp), "", (320, 320), 0.72, 0.3, 5000)
            except Exception:
                net = False
        _local.det = net
    return net or None


def available() -> bool:
    """Whether the models are here. It does not build the networks: that is for
    the threads that look at photos, and takes a moment."""
    return model_path() is not None and detector.model_path() is not None \
        and detector.ops.HAVE_CV


# -- looking at a photo -----------------------------------------------------
# Faces are found on a picture of 1280 pixels: the points the detector gives
# for the eyes, nose and mouth come out different on a smaller one, and the
# numbers that say who it is are made from them (checked: on 640 pixels they
# were 9 % less like those on 1280, enough to put a face in the wrong group).
# A first look at 640 pixels only decides that there is nothing to find, which
# is most of the cost of a photo without people.
FIRST_LOOK_SIDE = 640
FIRST_LOOK_SCORE = 0.5
FIRST_LOOK_PX = 20


def _picture(path: Path):
    """The photo as BGR at most ANALYSIS_SIDE on its longer side, upright. A
    JPEG is decoded already scaled down (a quarter of the work), and reduced
    by averaging; others go the usual way."""
    import cv2
    from PIL import Image, ImageOps
    p = Path(path)
    if p.suffix.lower() in (".jpg", ".jpeg", ".jpe", ".jfif"):
        im = Image.open(p)
        im.draft("RGB", (ANALYSIS_SIDE, ANALYSIS_SIDE))
        im = ImageOps.exif_transpose(im) or im
        im = im.convert("RGB")
        if max(im.size) > ANALYSIS_SIDE:
            k = ANALYSIS_SIDE / max(im.size)
            im = im.resize((round(im.width * k), round(im.height * k)),
                           Image.BILINEAR, reducing_gap=2.0)
    else:
        im = iio.load_pil(p, max_side=ANALYSIS_SIDE).convert("RGB")
    return cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)


def _rgb(path, side: int):
    """The photo as RGB, at most ``side`` on its longer side, upright; a JPEG is
    decoded already scaled down."""
    from PIL import Image, ImageOps
    p = Path(path)
    if p.suffix.lower() in (".jpg", ".jpeg", ".jpe", ".jfif"):
        im = Image.open(p)
        im.draft("RGB", (side, side))
        im = (ImageOps.exif_transpose(im) or im).convert("RGB")
        if max(im.size) > side:
            k = side / max(im.size)
            im = im.resize((round(im.width * k), round(im.height * k)),
                           Image.BILINEAR, reducing_gap=2.0)
    else:
        im = iio.load_pil(p, max_side=side).convert("RGB")
    return np.asarray(im)


class _Rate:
    """A running share (0 to 1) of the photos that have somebody in them."""
    value = 0.5

    def add(self, had_face: bool) -> None:
        self.value += ((1.0 if had_face else 0.0) - self.value) * 0.05


_faces_seen = _Rate()
SKIP_FIRST_LOOK_ABOVE = 0.55


SIDEWAYS_MIN_SCORE = 0.85       # a face found only by turning the photo must be surer than that
WEAK_SCORE = 0.9                # faces no surer than this, and all small, are a reason to look turned
WEAK_PX = 100
UPRIGHT_DEG = 35                # a head turned over more than this is not read as it is


def _detect(bgr, prefilter, min_score: float = MIN_SCORE) -> list[dict]:
    """The faces in a picture, upright in it: box (0..1), the numbers, which way
    the head is turned and tilted. ``prefilter``: look first at a small copy
    (None: only when most photos have nobody in them)."""
    import cv2
    rec = _recognizer()
    det = _detector()
    if rec is None or det is None:
        raise RuntimeError("faces are not available")
    h, w = bgr.shape[:2]
    out = []
    use_first = (_faces_seen.value < SKIP_FIRST_LOOK_ABOVE) if prefilter is None else prefilter
    # The first look pays for itself only when most photos have nobody in them:
    # it costs half of what the full look saves. A library of people skips it,
    # and it is back as soon as photos without faces are common.
    if max(h, w) > FIRST_LOOK_SIDE * 1.25 and use_first:
        k = FIRST_LOOK_SIDE / max(h, w)
        small = cv2.resize(bgr, (round(w * k), round(h * k)), interpolation=cv2.INTER_AREA)
        det.setInputSize((small.shape[1], small.shape[0]))
        det.setScoreThreshold(FIRST_LOOK_SCORE)
        try:
            _, rows = det.detect(small)
        finally:
            det.setScoreThreshold(0.72)
        if rows is None or not any(min(r[2], r[3]) >= FIRST_LOOK_PX for r in rows):
            return []
    det.setInputSize((w, h))
    _, rows = det.detect(bgr)
    if prefilter is None:
        _faces_seen.add(rows is not None and len(rows) > 0)
    if rows is None:
        return []
    for row in rows:
        if row[14] < min_score or min(row[2], row[3]) < MIN_FACE_PX:
            continue
        emb = rec.feature(rec.alignCrop(bgr, row)).flatten().astype(np.float32)
        norm = float(np.linalg.norm(emb))
        if norm == 0:
            continue
        # which way the head is turned and tilted, from eyes and nose
        ex = (row[4] + row[6]) / 2
        span = abs(row[6] - row[4]) or 1e-3
        yaw = float(np.clip((row[8] - ex) / span * 2.0, -1, 1))
        # how far the head is turned over, clockwise, from eyes to mouth: 0 upright,
        # 90 lying with its chin to the left, 180 upside down. The line of the eyes
        # alone cannot tell upright from upside down.
        mx, my = (row[10] + row[12]) / 2, (row[11] + row[13]) / 2
        eyes_x, eyes_y = ex, (row[5] + row[7]) / 2
        roll = float(np.degrees(np.arctan2(-(mx - eyes_x), my - eyes_y)))
        out.append({"x": float(row[0]) / w, "y": float(row[1]) / h,
                    "w": float(row[2]) / w, "h": float(row[3]) / h,
                    "score": float(row[14]), "px": int(row[2]),
                    "yaw": yaw, "roll": roll,
                    "emb": (emb / norm).tobytes()})
    return out


def _needs_turning(faces: list[dict]) -> bool:
    """Nobody found, or only faces Piklin is not sure of, or one that is turned over.
    The detector also sees a face upside down or on its side, but less surely and
    with its eyes and mouth in the wrong places, so the numbers it gives are
    worthless: the right way up it is surer. The photo is then looked at turned."""
    if not faces:
        return True
    if any(abs(f["roll"]) > UPRIGHT_DEG for f in faces):
        return True
    return max(f["score"] for f in faces) < WEAK_SCORE


def _unturn(f: dict, k: int) -> dict:
    """A face found in the photo turned (numpy's rot90: k=3 a quarter clockwise, k=1
    a quarter counter-clockwise, k=2 half a turn), in the photo as it is."""
    fx, fy, fw, fh = f["x"], f["y"], f["w"], f["h"]
    if k == 3:
        out = {"x": fy, "y": 1 - fx - fw, "w": fh, "h": fw, "roll": f["roll"] - 90}
    elif k == 1:
        out = {"x": 1 - fy - fh, "y": fx, "w": fh, "h": fw, "roll": f["roll"] + 90}
    else:
        out = {"x": 1 - fx - fw, "y": 1 - fy - fh, "w": fw, "h": fh, "roll": f["roll"] + 180}
    out["roll"] = (out["roll"] + 180) % 360 - 180
    return {**f, **out}


def _iou(a, b) -> float:
    x1, y1 = max(a["x"], b["x"]), max(a["y"], b["y"])
    x2, y2 = min(a["x"] + a["w"], b["x"] + b["w"]), min(a["y"] + a["h"], b["y"] + b["h"])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


TURN_MARGIN = 0.03              # a turned reading must be this much surer to take the place of one as it is


def _faces_in(bgr) -> list[dict]:
    faces = _detect(bgr, None)
    if not _needs_turning(faces):
        return faces
    # The photo turned each way (a baby lying on its side, a photo whose camera did
    # not say which way up it was), looked at as small copies first, so a photo
    # with nobody in it costs little.
    found = []
    for k in (3, 1, 2):
        turned = np.ascontiguousarray(np.rot90(bgr, k))
        for f in _detect(turned, True, SIDEWAYS_MIN_SCORE):
            if abs(f["roll"]) <= UPRIGHT_DEG:
                found.append(_unturn(f, k))
    # in each place, the reading that is surest
    result = list(faces)
    for c in sorted(found, key=lambda f: -f["score"]):
        over = [g for g in result if _iou(c, g) > 0.3]
        if not over:
            result.append(c)
            continue
        best = max(g["score"] for g in over)
        turned_over = any(abs(g["roll"]) > UPRIGHT_DEG for g in over)
        if c["score"] >= best + TURN_MARGIN or (turned_over and c["score"] >= best):
            result = [g for g in result if g not in over] + [c]
    # a much larger, surer face makes the small doubtful ones in the photo (a
    # button) what they most likely are
    big = max((f["px"] for f in result if f["score"] >= WEAK_SCORE), default=0)
    return [g for g in result if not (g["score"] < 0.88 and g["px"] * 2.5 <= big)]


def look(path: Path | str) -> list[dict]:
    """The faces in one photo: box (0..1), score, width in pixels and the 128
    numbers. [] when there are none."""
    return _faces_in(_picture(path))


_NEEDS_LOOK = """
    ( s.photo_id IS NULL
      OR ( s.version < 2
           AND (SELECT COALESCE(MAX(f.score), 0) FROM faces f WHERE f.photo_id=p.id) < 0.9
           AND (SELECT COALESCE(MAX(f.px), 0) FROM faces f WHERE f.photo_id=p.id) < 100 ) )"""


def pending(catalog, limit: int = 50) -> list:
    """Photos not looked at yet. Hidden and deleted ones are left alone: what
    someone hid is not shown in People either. A photo looked at before faces on
    their side were looked for, with nobody in it or only doubtful faces, is looked
    at again; one with a sure face is not."""
    from .video import VIDEO_EXT
    marks = ",".join("?" * len(VIDEO_EXT))
    return catalog.con.execute(
        f"""SELECT p.id, p.path FROM photos p
            LEFT JOIN face_scans s ON s.photo_id=p.id
            WHERE {_NEEDS_LOOK} AND p.trashed_at IS NULL AND p.hidden=0
              AND p.ext NOT IN ({marks})
            ORDER BY p.taken_at DESC LIMIT ?""",
        (*[e.lstrip(".") for e in VIDEO_EXT], limit)).fetchall()


def remaining(catalog) -> int:
    from .video import VIDEO_EXT
    marks = ",".join("?" * len(VIDEO_EXT))
    return catalog.con.execute(
        f"""SELECT COUNT(*) FROM photos p
            LEFT JOIN face_scans s ON s.photo_id=p.id
            WHERE {_NEEDS_LOOK} AND p.trashed_at IS NULL AND p.hidden=0
              AND p.ext NOT IN ({marks})""",
        tuple(e.lstrip(".") for e in VIDEO_EXT)).fetchone()[0]


def _keep(catalog, photo_id: int, found: list[dict]) -> None:
    with catalog.write() as cur:
        cur.execute("DELETE FROM faces WHERE photo_id=? AND state=0", (photo_id,))
        for f in found:
            cur.execute(
                "INSERT INTO faces(photo_id,x,y,w,h,score,px,yaw,roll,emb) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (photo_id, f["x"], f["y"], f["w"], f["h"], f["score"], f["px"],
                 f["yaw"], f["roll"], f["emb"]))
        cur.execute("INSERT INTO face_scans(photo_id,version) VALUES(?,?) "
                    "ON CONFLICT(photo_id) DO UPDATE SET version=excluded.version",
                    (photo_id, VERSION))


def scan_photo(catalog, photo_id: int, path: Path | str) -> int:
    """Look at one photo and keep what is found. Returns how many faces."""
    try:
        found = look(path)
    except Exception as exc:
        # An unreadable photo is marked as looked at: it would fail the same
        # way every time, and block the rest of the queue.
        log.info("faces: could not read %s: %s", Path(path).name, exc)
        found = []
    _keep(catalog, photo_id, found)
    return len(found)


WORKERS = 6


def _workers() -> int:
    import os
    return max(1, min(WORKERS, (os.cpu_count() or 2) // 2))


_pool_ref = None
_pool_lock = threading.Lock()


def _pool(workers: int):
    """The workers stay between batches: each keeps its own networks, which
    take a moment to load, and a thread that ended would take them with it."""
    global _pool_ref
    from concurrent.futures import ThreadPoolExecutor
    with _pool_lock:
        if _pool_ref is None or _pool_ref[0] != workers:
            _pool_ref = (workers, ThreadPoolExecutor(max_workers=workers,
                                                     thread_name_prefix="faces"))
        return _pool_ref[1]


def scan_photos(catalog, rows, workers: int | None = None, stop=None) -> int:
    """Look at several photos side by side: each worker opens a photo, finds
    its faces and numbers them, with networks of its own. What is found is
    written here, in order. Returns how many faces."""
    # The pool keeps its size (its threads keep their networks); ``workers`` is
    # how many photos are looked at at once, as few as one while someone is
    # using the window.
    pool = _pool(_workers())
    workers = max(1, min(workers or _workers(), _workers()))
    with _networks_single_threaded():
        return _scan(catalog, rows, workers, pool, stop)


def _networks_single_threaded():
    """While photos are being looked at, each network uses one thread: several
    photos side by side already use every core, and a network that also uses
    all of them gets in its own way (checked: a quarter of the processor time,
    for the same photos, spent only waiting for each other)."""
    import contextlib

    @contextlib.contextmanager
    def ctx():
        import cv2
        before = cv2.getNumThreads()
        cv2.setNumThreads(1)
        try:
            yield
        finally:
            cv2.setNumThreads(before)
    return ctx()


def _scan(catalog, rows, workers, pool, stop) -> int:

    def work(row):
        if stop is not None and stop.is_set():
            return None
        try:
            return _faces_in(_picture(row["path"]))
        except Exception as exc:
            log.info("faces: could not read %s: %s", Path(row["path"]).name, exc)
            return []

    found = 0
    ahead = []
    it = iter(rows)
    # as many photos in flight as photos at once; with the whole pool, a few
    # more, so that none of them waits for the next to be opened
    limit = workers if workers < _workers() else workers * 2

    def feed():
        for row in it:
            ahead.append((row, pool.submit(work, row)))
            if len(ahead) >= limit:
                return
    feed()
    while ahead:
        row, fut = ahead.pop(0)
        feed()
        faces = fut.result()
        if faces is None or (stop is not None and stop.is_set()):
            continue                    # stopped: left to look at again
        _keep(catalog, row["id"], faces)
        found += len(faces)
    return found


# -- grouping ---------------------------------------------------------------
def group(embs: np.ndarray, threshold: float = SAME_PERSON,
          photos: list | None = None) -> list[list[int]]:
    """Group faces so that the faces of a group are alike on average.

    ``photos`` says which photo each face is in: two faces of one photo are
    two people, so a group never takes two faces of the same photo. That
    alone keeps brothers and sisters apart, which a model that sees small
    children as alike cannot do.

    Done in two steps so it stays quick on a library of a hundred thousand
    faces: faces are first put in small, tight groups one by one, then groups
    are joined, always the most alike two first, for as long as the average
    likeness between everything in them stays above the threshold. That
    average needs only the sum of each group's faces, which is what makes it
    cheap."""
    n = len(embs)
    if n == 0:
        return []
    if photos is None:
        photos = list(range(n))             # no two faces share a photo
    # 1. small tight groups: join the nearest if it is close, else start one
    sums = np.empty((0, embs.shape[1]), np.float64)
    members: list[list[int]] = []
    shots: list[set] = []                   # the photos each group has faces in
    in_photo: dict = {}                     # photo -> groups with a face in it
    tight = max(threshold + 0.12, 0.6)
    for i in range(n):
        e = embs[i].astype(np.float64)
        ph = photos[i]
        if len(members):
            cents = sums / np.linalg.norm(sums, axis=1, keepdims=True)
            sim = cents @ e
            taken = in_photo.get(ph)
            if taken:
                sim[list(taken)] = -9
            j = int(np.argmax(sim))
            if sim[j] >= tight:
                sums[j] += e
                members[j].append(i)
                shots[j].add(ph)
                in_photo.setdefault(ph, set()).add(j)
                continue
        sums = np.vstack([sums, e[None]])
        members.append([i])
        shots.append({ph})
        in_photo.setdefault(ph, set()).add(len(members) - 1)
    # 2. join groups by average likeness, never two with a photo in common
    k = len(members)
    size = np.array([len(m) for m in members], np.float64)
    live = np.ones(k, bool)
    clash = np.zeros((k, k), bool)
    for groups in in_photo.values():
        if len(groups) > 1:
            g = list(groups)
            clash[np.ix_(g, g)] = True
    with np.errstate(invalid="ignore"):
        avg = (sums @ sums.T) / np.outer(size, size)
    avg[clash] = -9
    np.fill_diagonal(avg, -9)
    while True:
        idx = np.flatnonzero(live)
        if len(idx) < 2:
            break
        sub = avg[np.ix_(idx, idx)]
        a, b = np.unravel_index(int(np.argmax(sub)), sub.shape)
        if sub[a, b] < threshold:
            break
        a, b = idx[a], idx[b]
        sums[a] += sums[b]
        members[a] += members[b]
        size[a] += size[b]
        live[b] = False
        clash[a] |= clash[b]
        clash[:, a] = clash[a]
        row = (sums @ sums[a]) / (size * size[a])
        row[clash[a]] = -9
        avg[a, :] = row
        avg[:, a] = row
        avg[a, a] = -9
        avg[b, :] = -9
        avg[:, b] = -9
    return sorted((members[i] for i in np.flatnonzero(live)), key=len, reverse=True)


def log_answer(catalog, link: float, same: bool) -> None:
    with catalog.write() as cur:
        cur.execute("INSERT INTO people_answers(link,same,at) VALUES(?,?,?)",
                    (float(link), 1 if same else 0, time.time()))
    touch()


def learned_lines(catalog) -> tuple[float, float]:
    """(join_without_asking, ask_from): how alike two groups have to be to be
    joined by Piklin on its own, and to be worth asking about, learned from
    what the user answered.

    The lowest likeness above which the user said yes at least nine times in
    ten (with enough answers to say so) is where Piklin stops asking and joins.
    Below the highest likeness at which every answer was no, it stops asking.
    Both stay between the defaults and a floor, so that a few answers cannot
    send it far."""
    rows = catalog.con.execute("SELECT link, same FROM people_answers ORDER BY link DESC").fetchall()
    join_at, ask_from = SAME_PERSON, ASK_FROM
    if len(rows) < MIN_ANSWERS:
        return join_at, ask_from
    yes = n = 0
    for r in rows:                              # from the likeliest down
        n += 1
        yes += r["same"]
        if n >= MIN_ANSWERS and yes / n >= 0.9 and r["link"] >= AUTO_JOIN_FLOOR:
            join_at = min(join_at, r["link"])
    # answers that were all no, from the least alike up
    nos = 0
    for r in reversed(rows):
        if r["same"]:
            break
        nos += 1
        if nos >= MIN_ANSWERS:
            ask_from = max(ask_from, min(r["link"] + 0.01, join_at - 0.02))
    return join_at, ask_from


def _vec(blob) -> np.ndarray:
    return np.frombuffer(blob, np.float32)


def _place_in_the_well_known(free, known, seen, reject_vecs, updates) -> list:
    """Of the faces in no group, those that are clearly one of the well known
    people's: the person's nearest faces say so (see ESTABLISHED_FACES). Adds
    (face, person) to ``updates``; returns the faces that are left."""
    est = [pid for pid, v in known.items() if len(v) >= ESTABLISHED_FACES]
    if len(est) < 1 or not free:
        return free
    rng = np.random.default_rng(1)
    mats = {}
    for pid in est:
        m = np.asarray(known[pid], np.float32)
        if len(m) > ESTABLISHED_SAMPLE:
            m = m[rng.choice(len(m), ESTABLISHED_SAMPLE, replace=False)]
        mats[pid] = m
    rejects = {pid: np.asarray(v, np.float32) for pid, v in reject_vecs.items() if pid in mats}
    left = []
    k = ESTABLISHED_NEAREST
    for start in range(0, len(free), 400):
        chunk = free[start:start + 400]
        E = np.array([_vec(r["emb"]) for r in chunk], np.float32)
        score = np.full((len(chunk), len(est)), -9.0, np.float32)
        for j, pid in enumerate(est):
            sims = E @ mats[pid].T
            top = np.partition(sims, -min(k, sims.shape[1]), axis=1)[:, -min(k, sims.shape[1]):]
            score[:, j] = top.mean(axis=1)
            # a person already in the photo is someone else; so is who the face was taken out of
            for i, r in enumerate(chunk):
                if r["photo_id"] in seen.get(pid, ()):
                    score[i, j] = -9.0
            if pid in rejects:
                twin = (E @ rejects[pid].T).max(axis=1) >= REJECTED_LIKE
                score[twin, j] = -9.0
        for i, r in enumerate(chunk):
            order = np.argsort(-score[i])
            best = score[i, order[0]]
            # clearly more than the next well known person (with only one, than somebody
            # who is only a little alike)
            second = score[i, order[1]] if len(est) > 1 else 0.30
            if best >= ESTABLISHED_ALIKE and best - second >= ESTABLISHED_MARGIN:
                updates.append((r["id"], est[int(order[0])]))
                seen.setdefault(est[int(order[0])], set()).add(r["photo_id"])
            else:
                left.append(r)
    return left


def regroup(catalog, min_photos: int = MIN_PHOTOS, relaxed: bool = False) -> dict:
    """Put the faces that belong to nobody yet into people.

    First into a person Piklin already has, when they are alike enough to
    the faces that person has (more easily if the user confirmed them); then
    what is left is grouped, and a group of enough photos becomes a new,
    unnamed person. People the user has named or touched are never emptied,
    split or merged here. Returns what changed."""
    con = catalog.con
    rows = con.execute(
        "SELECT f.id, f.photo_id, f.emb, f.person_id, f.state, f.yaw, f.roll, f.was_person FROM faces f "
        "JOIN photos p ON p.id=f.photo_id WHERE p.trashed_at IS NULL AND p.hidden=0").fetchall()
    free = [r for r in rows if r["person_id"] is None and r["state"] == AUTO]
    if not free:
        return {"joined": 0, "new": 0, "merged": 0}
    joined = new = 0
    join_at, _ask = learned_lines(catalog)
    # Asked for ("Find More People"): a face is put in a person at a little less
    # likeness, and two photos are enough to make a person
    join_person = JOIN_PERSON - (RELAX_JOIN if relaxed else 0.0)
    knn_join = KNN_JOIN - (RELAX_JOIN if relaxed else 0.0)
    updates: list[tuple[int, int]] = []
    # 1. faces to the people there are
    known: dict[int, list[np.ndarray]] = {}
    anchors: dict[int, list[np.ndarray]] = {}   # faces the user confirmed
    rejected: dict[int, list[np.ndarray]] = {}  # faces the user took out of a person
    seen: dict[int, set] = {}                   # the photos each person is in
    for r in rows:
        if r["person_id"] is not None and r["state"] in (AUTO, CONFIRMED):
            known.setdefault(r["person_id"], []).append(_vec(r["emb"]))
            seen.setdefault(r["person_id"], set()).add(r["photo_id"])
            if r["state"] == CONFIRMED:
                anchors.setdefault(r["person_id"], []).append(_vec(r["emb"]))
        elif r["state"] == REMOVED and r["was_person"] is not None:
            rejected.setdefault(r["was_person"], []).append(_vec(r["emb"]))
    # 1a. the well known people take what is clearly theirs
    free = _place_in_the_well_known(free, known, seen, reject_vecs=rejected, updates=updates)
    joined += len(updates)
    still = []
    if known:
        pids = list(known)
        sums = np.array([np.sum(known[p], axis=0) for p in pids], np.float64)
        counts = np.array([len(known[p]) for p in pids], np.float64)
        anchor_m = {p: np.array(v, np.float64) for p, v in anchors.items() if len(v) >= 3}
        reject_m = {p: np.array(v, np.float64) for p, v in rejected.items()}
        for r in free:
            e = _vec(r["emb"]).astype(np.float64)
            avg = (sums @ e) / counts
            for q, pid in enumerate(pids):
                if pid in anchor_m:
                    # a person the user has vouched for is known by its nearest
                    # confirmed faces too, not only by the average: a child in
                    # two poses, or two ages, is far from the average of both
                    near = np.sort(anchor_m[pid] @ e)[-3:].mean()
                    if near >= knn_join:
                        avg[q] = max(avg[q], join_person)
                if r["photo_id"] in seen[pid]:      # a person already in this photo is someone else
                    avg[q] = -9
                elif pid in reject_m and float((reject_m[pid] @ e).max()) >= REJECTED_LIKE:
                    avg[q] = -9                     # the user took this very face out of them
            j = int(np.argmax(avg))
            # two people about as alike to it as each other: it is not put in either
            others = np.delete(avg, j) if len(avg) > 1 else np.array([-9.0])
            ambiguous = others.max() >= join_person and avg[j] - others.max() < JOIN_MARGIN
            if avg[j] >= join_person and not ambiguous:
                updates.append((r["id"], pids[j]))
                joined += 1
            else:
                still.append(r)
    else:
        still = free
    # 2. what is left, among themselves
    seeds = [r for r in still if abs(r["yaw"]) <= SEED_YAW and abs(r["roll"]) <= SEED_ROLL]
    if seeds:
        still = seeds
        embs = np.array([_vec(r["emb"]) for r in still])
        for members in group(embs, join_at, photos=[r["photo_id"] for r in still]):
            photos = {still[i]["photo_id"] for i in members}
            if len(photos) < min_photos:
                continue
            with catalog.write() as cur:
                cur.execute("INSERT INTO people(uuid,created_at) VALUES(?,?)",
                            (uuid.uuid4().hex, time.time()))
                pid = cur.lastrowid
            for i in members:
                updates.append((still[i]["id"], pid))
            new += 1
    if updates:
        with catalog.write() as cur:
            cur.executemany("UPDATE faces SET person_id=? WHERE id=? AND state=0 "
                            "AND person_id IS NULL", [(pid, fid) for fid, pid in updates])
    merged = _auto_merge(catalog, join_at) if join_at < SAME_PERSON else 0
    merged += merge_same_names(catalog)
    if updates or merged:
        touch()
    _tidy(catalog)
    _covers(catalog)
    return {"joined": joined, "new": new, "merged": merged}


def _tidy(catalog) -> None:
    """People left with no face are forgotten, unless they have a name."""
    with catalog.write() as cur:
        cur.execute("DELETE FROM people WHERE name IS NULL AND id NOT IN "
                    "(SELECT DISTINCT person_id FROM faces WHERE person_id IS NOT NULL)")


def _covers(catalog) -> None:
    """The face that stands for a person: the largest, clearest one."""
    with catalog.write() as cur:
        cur.execute(
            """UPDATE people SET cover_face=(
                 SELECT f.id FROM faces f JOIN photos p ON p.id=f.photo_id
                 WHERE f.person_id=people.id AND f.state<2
                   AND p.trashed_at IS NULL AND p.hidden=0
                 ORDER BY f.score * f.px * (1.0 - MIN(ABS(f.yaw), 0.8))
                          * (1.0 - MIN(ABS(f.roll) / 90.0, 0.8)) DESC LIMIT 1)
               WHERE cover_face IS NULL OR cover_face NOT IN
                 (SELECT id FROM faces WHERE person_id=people.id AND state<2)""")


# -- what the user does -----------------------------------------------------
def list_people(catalog, include_unnamed: bool = True) -> list[dict]:
    """People, the ones with most photos first. Hidden and deleted photos do
    not count."""
    rows = catalog.con.execute(
        """SELECT pe.id, pe.name, pe.cover_face, pe.cover_turn, pe.cover_custom, pe.hidden,
                  COUNT(DISTINCT f.photo_id) AS photos
           FROM people pe
           JOIN faces f ON f.person_id=pe.id AND f.state<2
           JOIN photos p ON p.id=f.photo_id AND p.trashed_at IS NULL AND p.hidden=0
           WHERE pe.hidden=0 GROUP BY pe.id
           ORDER BY (pe.name IS NULL), photos DESC""").fetchall()
    out = [dict(r) for r in rows]
    return out if include_unnamed else [r for r in out if r["name"]]


def photo_ids(catalog, person_id: int) -> list[int]:
    return [r[0] for r in catalog.con.execute(
        """SELECT DISTINCT f.photo_id FROM faces f JOIN photos p ON p.id=f.photo_id
           WHERE f.person_id=? AND f.state<2 AND p.trashed_at IS NULL AND p.hidden=0
           ORDER BY p.taken_at DESC""", (person_id,))]


def _same_name(a: str | None, b: str | None) -> bool:
    from .textfold import fold
    return bool(a) and bool(b) and " ".join(fold(a).split()) == " ".join(fold(b).split())


def rename(catalog, person_id: int, name: str | None) -> int:
    """Name a person. Two groups with the same name are one person: this one is
    joined with the other that already has it. Returns the person that is left."""
    name = (name or "").strip() or None
    with catalog.write() as cur:
        cur.execute("UPDATE people SET name=?, name_at=? WHERE id=?", (name, time.time(), person_id))
        # What the user named is theirs now: its faces count as confirmed.
        if name:
            cur.execute("UPDATE faces SET state=1, state_at=? WHERE person_id=? AND state=0",
                        (time.time(), person_id))
    touch()
    if not name:
        return person_id
    left = person_id
    for pid, other in catalog.con.execute("SELECT id, name FROM people WHERE id<>? AND name IS NOT NULL",
                                          (person_id,)).fetchall():
        if _same_name(other, name):
            # said as it was said the first time ("Aniella A", not "aniella  a")
            with catalog.write() as cur:
                cur.execute("UPDATE people SET name=?, name_at=? WHERE id=?", (other, time.time(), person_id))
            # the one that was there first stays: it is who the name already meant
            keep, gone = (pid, left) if pid < left else (left, pid)
            merge(catalog, keep, gone)
            left = keep
    return left


def merge_same_names(catalog) -> int:
    """People that have the same name are the same person: joins any that were
    left apart (named before this was so, or on another computer). Returns how
    many were joined."""
    seen: dict[str, int] = {}
    joined = 0
    for pid, name in catalog.con.execute(
            "SELECT id, name FROM people WHERE name IS NOT NULL ORDER BY id").fetchall():
        from .textfold import fold
        key = " ".join(fold(name).split())
        if key in seen:
            merge(catalog, seen[key], pid)
            joined += 1
        else:
            seen[key] = pid
    return joined


def merge(catalog, keep: int, other: int) -> None:
    """Two groups that are one person."""
    if keep == other:
        return
    now = time.time()
    with catalog.write() as cur:
        cur.execute("UPDATE faces SET person_id=? WHERE person_id=?", (keep, other))
        a = cur.execute("SELECT uuid, name, name_at FROM people WHERE id=?", (keep,)).fetchone()
        b = cur.execute("SELECT uuid, name, name_at FROM people WHERE id=?", (other,)).fetchone()
        if b is not None and b[1] and (not a or not a[1] or (b[2] or 0) > (a[2] or 0)):
            cur.execute("UPDATE people SET name=?, name_at=? WHERE id=?", (b[1], b[2] or now, keep))
        if a is not None and b is not None:
            # the other computers join them too (peoplesync.py)
            cur.execute("INSERT OR REPLACE INTO people_merged(old,new,at) VALUES(?,?,?)",
                        (b[0], a[0], now))
        # what was said about the other (different from somebody) is said of the one left
        for x, y in cur.execute("SELECT a, b FROM people_apart WHERE a=? OR b=?", (other, other)).fetchall():
            cur.execute("DELETE FROM people_apart WHERE a=? AND b=?", (x, y))
            z = y if x == other else x
            if z != keep:
                cur.execute("INSERT OR IGNORE INTO people_apart(a,b) VALUES(?,?)", (min(keep, z), max(keep, z)))
        cur.execute("DELETE FROM people WHERE id=?", (other,))
        cur.execute("UPDATE people SET cover_face=NULL WHERE id=?", (keep,))
    _covers(catalog)
    touch()


def not_this_person(catalog, face_ids: list[int]) -> None:
    """Take faces out of their person. They are not put in one again by
    themselves."""
    with catalog.write() as cur:
        cur.executemany("UPDATE faces SET was_person=person_id, person_id=NULL, state=?, state_at=? WHERE id=?",
                        [(REMOVED, time.time(), f) for f in face_ids])
    _tidy(catalog)
    _covers(catalog)
    touch()


def ignore_person(catalog, person_id: int) -> None:
    """Not a person to keep: its faces are ignored and it goes away."""
    with catalog.write() as cur:
        cur.execute("UPDATE faces SET was_person=person_id, person_id=NULL, state=?, state_at=? "
                    "WHERE person_id=?", (IGNORED, time.time(), person_id))
        cur.execute("DELETE FROM people WHERE id=?", (person_id,))
    touch()


def assign(catalog, face_ids: list[int], person_id: int, pinned: bool = False) -> None:
    """The user says these faces are this person. ``pinned``: they chose these faces
    one by one, so Piklin never moves them again."""
    with catalog.write() as cur:
        cur.executemany("UPDATE faces SET person_id=?, state=?, state_at=?, pinned=? WHERE id=?",
                        [(person_id, CONFIRMED, time.time(), 1 if pinned else 0, f) for f in face_ids])
    _tidy(catalog)
    _covers(catalog)
    touch()


AUDIT_MIN_FACES = 30            # a person is judged from this many faces
AUDIT_OTHER = 0.65              # another person must fit the face at least this well...
AUDIT_MARGIN = 0.10             # ...and this much better than the face's own person
AUDIT_MARGIN_CONFIRMED = 0.15   # (more, for a face confirmed by naming the group)
AUDIT_LOOSE = 0.40              # one this little like its own person, like nobody's, goes loose
AUDIT_MOMENT = 900.0            # seconds: photos this close in time are one moment, one burst


def audit(catalog) -> dict:
    """Look through the faces of every person for ones that do not belong, and put
    them right. A face is compared with its own person's faces from other moments
    (the photos of one burst look alike and vouch for each other) and with every
    other person's. One that another person fits clearly better goes to them; one
    that is like nobody's goes loose, to be grouped again. Faces the user chose one
    by one are never moved; those that were confirmed by naming the whole group
    only if it is very clear. Returns how many were moved."""
    rows = catalog.con.execute(
        """SELECT f.id, f.person_id, f.emb, f.state, f.pinned, f.photo_id, COALESCE(p.taken_at, 0) AS t
           FROM faces f JOIN photos p ON p.id=f.photo_id
           WHERE f.person_id IS NOT NULL AND f.state<2 AND p.trashed_at IS NULL AND p.hidden=0""").fetchall()
    by: dict[int, list] = {}
    for r in rows:
        by.setdefault(r["person_id"], []).append(r)
    big = [p for p, v in by.items() if len(v) >= AUDIT_MIN_FACES]
    if len(big) < 1:
        return {"moved": 0, "loose": 0}
    rng = np.random.default_rng(0)
    mats = {p: np.array([_vec(r["emb"]) for r in by[p]], np.float32) for p in big}
    times = {p: np.array([r["t"] for r in by[p]], np.float64) for p in big}

    def nearest(S, mask, k=5):
        S = np.where(mask, S, -9.0)
        kk = min(k, S.shape[1])
        return np.sort(np.partition(S, -kk, axis=1)[:, -kk:], axis=1).mean(axis=1)

    moves, loose = [], []
    seen_q: dict[int, set] = {}
    for p in big:
        m, tp = mats[p], times[p]
        sub = np.arange(len(m)) if len(m) <= 2500 else np.sort(rng.choice(len(m), 2500, replace=False))
        fit = nearest(m @ m[sub].T, np.abs(tp[:, None] - tp[sub][None, :]) > AUDIT_MOMENT)
        best = np.full(len(m), -9.0)
        who = np.full(len(m), -1)
        for q in big:
            if q == p:
                continue
            mq, tq = mats[q], times[q]
            if len(mq) > 1500:
                ix = np.sort(rng.choice(len(mq), 1500, replace=False))
                mq, tq = mq[ix], tq[ix]
            sc = nearest(m @ mq.T, np.abs(tp[:, None] - tq[None, :]) > AUDIT_MOMENT)
            upd = sc > best
            best[upd] = sc[upd]
            who[upd] = q
        for i, r in enumerate(by[p]):
            if r["pinned"]:
                continue
            need = AUDIT_MARGIN if r["state"] == AUTO else AUDIT_MARGIN_CONFIRMED
            if who[i] >= 0 and best[i] >= AUDIT_OTHER and best[i] >= fit[i] + need:
                q = int(who[i])
                if q not in seen_q:
                    seen_q[q] = {x["photo_id"] for x in by[q]}
                if r["photo_id"] not in seen_q[q]:          # a person is not twice in one photo
                    moves.append((q, r["id"], r["state"]))
                    seen_q[q].add(r["photo_id"])
                    continue
            if fit[i] < AUDIT_LOOSE and r["state"] == AUTO:
                loose.append(r["id"])
    if moves or loose:
        with catalog.write() as cur:
            cur.executemany("UPDATE faces SET person_id=? WHERE id=? AND state=?", moves)
            cur.executemany("UPDATE faces SET person_id=NULL WHERE id=? AND state=0", [(i,) for i in loose])
        _tidy(catalog)
        _covers(catalog)
        touch()
    return {"moved": len(moves), "loose": len(loose)}


def leftover(catalog) -> int:
    """Faces that are in no person, that the user has not put away."""
    return catalog.con.execute(
        """SELECT COUNT(*) FROM faces f JOIN photos p ON p.id=f.photo_id
           WHERE f.person_id IS NULL AND f.state=0 AND p.trashed_at IS NULL AND p.hidden=0""").fetchone()[0]


def find_more(catalog) -> dict:
    """Look again at the faces that belong to nobody, a little less strictly: join
    them to the people there are at less likeness, and make a person of faces that
    are alike in two photos and not only in three. Returns what was found."""
    before_people = catalog.con.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    before = leftover(catalog)
    result = regroup(catalog, min_photos=MIN_PHOTOS_MORE, relaxed=True)
    after = leftover(catalog)
    after_people = catalog.con.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    return {"new_people": max(0, after_people - before_people), "placed": max(0, before - after),
            "left": after, "joined": result.get("joined", 0)}


def _person_sums(catalog, ids) -> tuple[dict, dict, dict]:
    """For each person: the sum of its faces, how many, and the photos."""
    sums: dict[int, np.ndarray] = {}
    count: dict[int, int] = {}
    shots: dict[int, set] = {}
    rows = catalog.con.execute(
        """SELECT f.person_id, f.photo_id, f.emb FROM faces f
           JOIN photos p ON p.id=f.photo_id AND p.trashed_at IS NULL AND p.hidden=0
           WHERE f.person_id IS NOT NULL AND f.state<2""").fetchall()
    want = set(ids) if ids is not None else None
    for r in rows:
        pid = r["person_id"]
        if want is not None and pid not in want:
            continue
        v = _vec(r["emb"]).astype(np.float64)
        sums[pid] = sums[pid] + v if pid in sums else v
        count[pid] = count.get(pid, 0) + 1
        shots.setdefault(pid, set()).add(r["photo_id"])
    return sums, count, shots


def link_between(catalog, a: int, b: int) -> float | None:
    """How alike two people are, on average, or None."""
    sums, count, _ = _person_sums(catalog, (a, b))
    if a not in sums or b not in sums:
        return None
    return float(sums[a] @ sums[b]) / (count[a] * count[b])


def _auto_merge(catalog, join_at: float) -> int:
    """People the user's answers have taught Piklin to join: unnamed, no photo
    in common, not said to be different, and alike at least ``join_at``."""
    done = 0
    for _ in range(50):
        found = suggestions(catalog, 1, joining=True)
        if not found:
            break
        merge(catalog, found[0]["a"], found[0]["b"])
        done += 1
    return done


def suggestions(catalog, limit: int = 1, joining: bool = False) -> list[dict]:
    """Pairs of people who may be the same person, the likeliest first: close
    but not close enough to join by themselves. Never two with a photo in
    common (one photo shows two people), two the user has named, or a pair the
    user already said is not the same. Where "close enough" is, and where it is
    not worth asking, are learned from the user's answers (learned_lines).

    With ``joining``, the pairs alike enough that Piklin should join them
    itself, only if neither has a name."""
    join_at, ask_from = learned_lines(catalog)
    sums, count, shots = _person_sums(catalog, None)
    ids = [p for p in sums if len(shots[p]) >= MIN_PHOTOS]
    if len(ids) < 2:
        return []
    S = np.array([sums[p] for p in ids])
    n = np.array([count[p] for p in ids], np.float64)
    link = (S @ S.T) / np.outer(n, n)
    apart = {tuple(r) for r in catalog.con.execute("SELECT a, b FROM people_apart")}
    names = {r[0]: r[1] for r in catalog.con.execute("SELECT id, name FROM people")}
    out = []
    iu = np.triu_indices(len(ids), 1)
    order = np.argsort(-link[iu])
    for k in order:
        a, b = int(iu[0][k]), int(iu[1][k])
        v = float(link[a, b])
        if joining:
            if v < join_at:
                break
        else:
            if v < ask_from:
                break
            if v >= join_at:
                continue
        pa, pb = ids[a], ids[b]
        if (min(pa, pb), max(pa, pb)) in apart or shots[pa] & shots[pb]:
            continue
        if names.get(pa) and (joining or names.get(pb)):
            continue
        if joining and names.get(pb):
            continue
        out.append({"a": pa, "b": pb, "link": v})
        if len(out) >= limit:
            break
    return out


def join_faces(catalog, a: int, b: int, picked: list[int], shown: list[int]) -> int | None:
    """The user picked, among the faces shown of two groups, the ones that are
    the same person. They become one person (the named one, else the one with
    more of the picked faces). A group whose every shown face was picked joins
    whole; if some shown face of it was left out, only the picked faces go and
    the rest stays where it was. Returns the person they are now, or None when
    nothing was joined (a face of each group has to be picked). The answer, and
    how alike the two groups were, is kept: Piklin learns from it."""
    picked, shown = set(picked), set(shown)
    owner = {r[0]: r[1] for r in catalog.con.execute(
        f"SELECT id, person_id FROM faces WHERE id IN ({','.join('?' * len(shown))})",
        list(shown))} if shown else {}
    mine = {p: [f for f in picked if owner.get(f) == p] for p in (a, b)}
    if not mine[a] or not mine[b]:
        return None
    link = link_between(catalog, a, b)
    if link is not None:
        log_answer(catalog, link, True)
    names = {r[0]: r[1] for r in catalog.con.execute(
        "SELECT id, name FROM people WHERE id IN (?,?)", (a, b))}
    count = {r[0]: r[1] for r in catalog.con.execute(
        "SELECT person_id, COUNT(*) FROM faces WHERE person_id IN (?,?) AND state<2 "
        "GROUP BY person_id", (a, b))}
    keep = max((a, b), key=lambda p: (bool(names.get(p)), len(mine[p]), count.get(p, 0)))
    gone = b if keep == a else a
    left_out = [f for f in shown if owner.get(f) == gone and f not in picked]
    if not left_out:
        merge(catalog, keep, gone)
    assign(catalog, mine[a] + mine[b], keep, pinned=True)    # what was picked is confirmed, and is theirs
    return keep


def sample_faces(catalog, person_id: int, count: int = 4) -> list[int]:
    """A few of a person's faces, the clearest first, each from a different
    photo: enough to see who it is."""
    rows = catalog.con.execute(
        """SELECT f.id, f.photo_id FROM faces f
           JOIN photos p ON p.id=f.photo_id AND p.trashed_at IS NULL AND p.hidden=0
           WHERE f.person_id=? AND f.state<2
           ORDER BY f.score * f.px * (1.0 - MIN(ABS(f.yaw), 0.8))
                    * (1.0 - MIN(ABS(f.roll) / 90.0, 0.8)) DESC LIMIT 60""",
        (person_id,)).fetchall()
    out, seen = [], set()
    for r in rows:
        if r["photo_id"] in seen:
            continue
        seen.add(r["photo_id"])
        out.append(r["id"])
        if len(out) >= count:
            break
    return out


def not_same(catalog, a: int, b: int) -> None:
    """The user says these two are different people."""
    link = link_between(catalog, a, b)
    if link is not None:
        log_answer(catalog, link, False)
    with catalog.write() as cur:
        cur.execute("INSERT OR IGNORE INTO people_apart(a,b) VALUES(?,?)", (min(a, b), max(a, b)))


def faces_in_photo(catalog, photo_id: int) -> list[dict]:
    return [dict(r) for r in catalog.con.execute(
        """SELECT f.id, f.x, f.y, f.w, f.h, f.person_id, f.state, pe.name
           FROM faces f LEFT JOIN people pe ON pe.id=f.person_id
           WHERE f.photo_id=? ORDER BY f.x""", (photo_id,))]


def forget_all(catalog) -> None:
    """Delete every face and person, and what was looked at. For anyone who
    turns People off and wants it gone."""
    with catalog.write() as cur:
        cur.execute("DELETE FROM faces")
        cur.execute("DELETE FROM people")
        cur.execute("DELETE FROM face_scans")
        cur.execute("DELETE FROM people_apart")
        cur.execute("DELETE FROM people_answers")
        cur.execute("DELETE FROM people_merged")
    import shutil
    shutil.rmtree(Path(catalog.path).parent / ".cache" / "faces", ignore_errors=True)


def set_cover(catalog, person_id: int, photo_id: int) -> bool:
    """The picture of a person is the face they have in this photo (the largest, if
    there are several). Chosen by the user, so it stays until the face is taken out
    of the person. False when the person has no face in that photo."""
    row = catalog.con.execute(
        "SELECT id FROM faces WHERE person_id=? AND photo_id=? AND state<2 ORDER BY w*h DESC LIMIT 1",
        (person_id, photo_id)).fetchone()
    if row is None:
        return False
    with catalog.write() as cur:
        cur.execute("UPDATE people SET cover_face=?, cover_turn=0, cover_custom=NULL, cover_at=? WHERE id=?",
                    (row[0], time.time(), person_id))
    touch()
    return True


def set_cover_custom(catalog, person_id: int, photo_id: int, cx: float, cy: float, s: float,
                     rot: int = 0) -> bool:
    """The person's picture, framed by hand: a square of the photo, turned by
    ``rot`` degrees clockwise (0, 90, 180, 270) and then cut, its centre at
    (cx, cy) as fractions of the turned photo and its side ``s`` of the photo's
    longer side. Kept with what is in the photo (its fingerprint), so that the
    other computers find the same photo."""
    import json
    row = catalog.con.execute("SELECT fingerprint FROM photos WHERE id=?", (photo_id,)).fetchone()
    if row is None:
        return False
    data = {"photo": int(photo_id), "fp": row[0], "cx": round(float(cx), 4), "cy": round(float(cy), 4),
            "s": round(float(s), 4), "rot": int(rot) % 360}
    face = catalog.con.execute(
        "SELECT id FROM faces WHERE person_id=? AND photo_id=? AND state<2 ORDER BY w*h DESC LIMIT 1",
        (person_id, photo_id)).fetchone()
    with catalog.write() as cur:
        cur.execute("UPDATE people SET cover_custom=?, cover_turn=0, cover_at=? WHERE id=?",
                    (json.dumps(data, separators=(",", ":")), time.time(), person_id))
        if face is not None:
            cur.execute("UPDATE people SET cover_face=? WHERE id=?", (face[0], person_id))
    touch()
    return True


def custom_crop(catalog, custom: str, size: int = 160):
    """The picture framed by hand (see set_cover_custom), or None if its photo is
    not here any more."""
    import json
    from PIL import Image
    try:
        d = json.loads(custom)
    except (TypeError, ValueError):
        return None
    row = catalog.con.execute("SELECT path FROM photos WHERE id=?", (d.get("photo"),)).fetchone()
    if row is None and d.get("fp"):
        row = catalog.con.execute("SELECT path FROM photos WHERE fingerprint=? LIMIT 1", (d["fp"],)).fetchone()
    if row is None:
        return None
    s = max(float(d.get("s") or 0.2), 0.02)
    try:
        im = iio.load_pil(row[0], max_side=int(min(4000, max(900, size * 1.6 / s)))).convert("RGB")
    except Exception:
        return None
    rot = int(d.get("rot") or 0) % 360
    if rot:
        im = im.rotate(-rot, expand=True)                 # PIL turns counter-clockwise
    W, H = im.size
    side = max(8, min(int(s * max(W, H)), W, H))
    x0 = int(min(max(d["cx"] * W - side / 2, 0), W - side))
    y0 = int(min(max(d["cy"] * H - side / 2, 0), H - side))
    return im.crop((x0, y0, x0 + side, y0 + side)).resize((size, size), Image.LANCZOS)


def turn_cover(catalog, person_id: int, quarter_turns: int) -> None:
    """Turn a person's picture a quarter turn (1: to the right, -1: to the
    left) - for a face that is still on its side. It is remembered."""
    with catalog.write() as cur:
        cur.execute("UPDATE people SET cover_turn=((cover_turn + ?) % 360 + 360) % 360 WHERE id=?",
                    (90 * quarter_turns, person_id))
    touch()


def _cover_file(catalog, face_id: int, size: int, turn: int) -> Path:
    """Where a face's picture is kept: named by what is in it (the photo's
    content and where the face is), not by the face's number, which a catalog
    rebuilt or People turned off and on again gives to other faces."""
    import hashlib
    row = catalog.con.execute(
        "SELECT p.fingerprint, f.x, f.y, f.w, f.h, f.roll FROM faces f JOIN photos p ON p.id=f.photo_id "
        "WHERE f.id=?", (face_id,)).fetchone()
    mark = (f"{row[0]}|{row[1]:.3f}|{row[2]:.3f}|{row[3]:.3f}|{row[4]:.3f}|{row[5]:.1f}"
            if row and row[0] else f"id{face_id}")
    key = hashlib.sha1(f"{mark}|{size}|{turn % 360}".encode()).hexdigest()[:20]
    return Path(catalog.path).parent / ".cache" / "faces" / f"{key}.jpg"


def cover_image(catalog, face_id: int, size: int = 160, turn: int = 0, custom: str | None = None):
    """cover_crop, from the disk when it has been made before: opening People
    again does not read two hundred photos again. With ``custom``, the picture
    framed by hand instead."""
    from PIL import Image
    import hashlib
    if custom:
        f = Path(catalog.path).parent / ".cache" / "faces" / (
            hashlib.sha1(f"{custom}|{size}|{turn % 360}".encode()).hexdigest()[:20] + ".jpg")
        try:
            if f.is_file():
                im = Image.open(f)
                im.load()
                return im
        except Exception:
            pass
        img = custom_crop(catalog, custom, size)
        if img is not None:
            if turn % 360:
                img = img.rotate(-(turn % 360))
            try:
                f.parent.mkdir(parents=True, exist_ok=True)
                tmp = f.with_name(f.name + ".part")
                img.convert("RGB").save(tmp, "JPEG", quality=90)
                tmp.replace(f)
            except OSError:
                pass
            return img
        if not face_id:
            return None
    try:
        f = _cover_file(catalog, face_id, size, turn)
    except Exception:
        return cover_crop(catalog, face_id, size, turn)
    try:
        if f.is_file():
            im = Image.open(f)
            im.load()
            return im
    except Exception:
        pass
    img = cover_crop(catalog, face_id, size, turn)
    if img is not None:
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_name(f.name + ".part")
            img.convert("RGB").save(tmp, "JPEG", quality=90)
            tmp.replace(f)
        except OSError:
            pass
    return img


def cover_crop(catalog, face_id: int, size: int = 160, turn: int = 0):
    """A square picture of one face, for its person's tile, or None. A head
    that is tilted is turned upright first, then by ``turn`` degrees clockwise
    (what the user chose).

    A face near the edge of its photo, or a tilted head, would leave empty
    corners; they are filled with the photo's own edge, mirrored, never black."""
    import cv2
    row = catalog.con.execute(
        "SELECT f.x,f.y,f.w,f.h,f.roll,p.path FROM faces f JOIN photos p ON p.id=f.photo_id "
        "WHERE f.id=?", (face_id,)).fetchone()
    if row is None:
        return None
    # as large as this face needs, not as large as the photo: a small picture of
    # a big face is decoded from a fraction of the JPEG
    side = int(min(ANALYSIS_SIDE, max(320, size * 1.8 / max(row["w"], row["h"], 0.02))))
    try:
        arr = _rgb(row["path"], side)
    except Exception:
        return None
    H, W = arr.shape[:2]
    cx, cy = (row["x"] + row["w"] / 2) * W, (row["y"] + row["h"] / 2) * H
    half = max(row["w"] * W, row["h"] * H) * 0.7
    half = min(half, W / 2, H / 2)             # never wider than the photo itself
    # Slide the picture inwards when the face is by an edge: the face is a
    # little off centre, and nothing is made up.
    cx = min(max(cx, half), W - half)
    cy = min(max(cy, half), H - half)
    roll = row["roll"] or 0.0
    # a region wider than the picture will be, so that turning it leaves no gap
    reach = int(np.ceil(half * (1.5 if abs(roll) > 4 else 1.0))) + 2
    x0, y0 = int(round(cx)) - reach, int(round(cy)) - reach
    x1, y1 = int(round(cx)) + reach, int(round(cy)) + reach
    cx0, cy0, cx1, cy1 = max(x0, 0), max(y0, 0), min(x1, W), min(y1, H)
    if cx1 <= cx0 or cy1 <= cy0:
        return None
    region = arr[cy0:cy1, cx0:cx1]
    pad = (cy0 - y0, y1 - cy1, cx0 - x0, x1 - cx1)          # top, bottom, left, right
    if any(pad):
        rh, rw = region.shape[:2]
        mode = (cv2.BORDER_REFLECT_101 if max(pad[0], pad[1]) < rh and max(pad[2], pad[3]) < rw
                else cv2.BORDER_REPLICATE)
        region = cv2.copyMakeBorder(region, *pad, mode)
    if abs(roll) > 4:
        n = region.shape[0]
        m = cv2.getRotationMatrix2D((n / 2, n / 2), roll, 1.0)   # the eye line slopes by roll: turn it back
        region = cv2.warpAffine(region, m, (n, n), flags=cv2.INTER_CUBIC,
                                borderMode=cv2.BORDER_REFLECT_101)
    n = region.shape[0]
    c, h2 = n // 2, int(half)
    out = region[max(c - h2, 0):c + h2, max(c - h2, 0):c + h2]
    from PIL import Image
    img = Image.fromarray(np.ascontiguousarray(out)).resize((size, size), Image.LANCZOS)
    if turn % 360:
        img = img.rotate(-(turn % 360))            # PIL turns counter-clockwise; a quarter turn of a square leaves no gap
    return img
