"""People, between computers (see people.py).

Piklin looks for the faces on each computer, and the names and decisions of the
person are worth the same on all of them. So every computer writes one file of
its own into the library, People/<its id>.json, which the backup carries to the
destination; the others read it and merge it with what they have. Nobody ever
writes another computer's file, so two cannot overwrite each other.

What travels:

* the faces, each tied to the photo by what is in the photo (its fingerprint,
  the one that finds duplicates) and not by where it is kept, so a photo is the
  same on a Mac and on a Linux laptop whatever folder it sits in; with the 128
  numbers that say whose face it is, so the other computer need not look at that
  photo again - the slow part;
* the people, with the same identity everywhere, their name and when it was
  given, and which were joined into which;
* what the person decided - a face confirmed, taken out of someone, ignored -
  with when: the newest decision wins, whichever computer it was made on;
* the pairs said to be different, and the answers to "is this the same person?",
  which is what Piklin learned from.

What does not travel is what Piklin guessed: each computer groups the faces it
has looked at itself, and the groups agree through the names and the decisions.
"""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import numpy as np

from . import people
from .logs import log
from .video import VIDEO_EXT

# People is for photos only: no video is looked at, and none is taken in from
# another computer.
_VIDEO = tuple(e.lstrip(".") for e in VIDEO_EXT)
_NOT_VIDEO = "p.ext NOT IN (" + ",".join("'%s'" % e for e in _VIDEO) + ")"

FORMAT = "piklin-people"
VERSION = 1
DIR = "People"
CACHE = ("backups", "people")        # under .cache: what other computers sent, kept to read again


def my_id() -> str:
    from .backuplock import me
    return me()[0]


def my_path(library) -> Path:
    return Path(library.root) / DIR / f"{my_id()}.json"


def cache_dir(root) -> Path:
    return Path(root) / ".cache" / "people"


def _round(x, n=4):
    return round(float(x), n)


# -- writing ------------------------------------------------------------------
def export(catalog) -> dict:
    con = catalog.con
    people_rows = con.execute(
        """SELECT pe.uuid, pe.name, pe.name_at, pe.cover_turn, pe.cover_at, ph.fingerprint, f.x, f.y,
                  pe.cover_custom
           FROM people pe LEFT JOIN faces f ON f.id=pe.cover_face AND pe.cover_at>0
           LEFT JOIN photos ph ON ph.id=f.photo_id
           WHERE pe.uuid IS NOT NULL""").fetchall()
    uuid_of = {r[0]: r[1] for r in con.execute("SELECT id, uuid FROM people")}
    faces = []
    for r in con.execute(
            """SELECT p.fingerprint AS fp, f.x, f.y, f.w, f.h, f.score, f.px, f.yaw, f.roll,
                      f.emb, f.person_id, f.state, f.state_at, f.was_person, f.pinned
               FROM faces f JOIN photos p ON p.id=f.photo_id
               WHERE p.fingerprint IS NOT NULL AND """+_NOT_VIDEO+"""
                 AND p.id = (SELECT MIN(q.id) FROM photos q WHERE q.fingerprint=p.fingerprint)
               ORDER BY p.fingerprint, f.x"""):
        emb = np.frombuffer(r["emb"], np.float32).astype(np.float16).tobytes()
        owner = uuid_of.get(r["person_id"]) if r["person_id"] is not None else None
        was = uuid_of.get(r["was_person"]) if r["was_person"] is not None else None
        faces.append([r["fp"], _round(r["x"]), _round(r["y"]), _round(r["w"]), _round(r["h"]),
                      _round(r["score"], 3), int(r["px"]), _round(r["yaw"], 3), _round(r["roll"], 1),
                      base64.b64encode(emb).decode("ascii"), owner, int(r["state"]),
                      _round(r["state_at"], 3), was, int(r["pinned"])])
    # photos looked at that have nobody in them: the other computer need not look
    clean = [r[0] for r in con.execute(
        """SELECT DISTINCT p.fingerprint FROM photos p JOIN face_scans s ON s.photo_id=p.id
           WHERE p.fingerprint IS NOT NULL AND s.version>=? AND """+_NOT_VIDEO+"""
             AND NOT EXISTS (SELECT 1 FROM faces f JOIN photos q ON q.id=f.photo_id
                             WHERE q.fingerprint=p.fingerprint)
           ORDER BY p.fingerprint""", (people.VERSION,))]
    return {
        "format": FORMAT, "version": VERSION, "computer": my_id(),
        "scan_version": people.VERSION,
        "clean": clean,
        "people": [[r["uuid"], r["name"], _round(r["name_at"], 3), int(r["cover_turn"]),
                    _round(r["cover_at"], 3), r["fingerprint"], r["x"], r["y"], r["cover_custom"]]
                   for r in people_rows],
        "merged": [[r[0], r[1], _round(r[2], 3)] for r in
                   con.execute("SELECT old, new, at FROM people_merged ORDER BY old")],
        "apart": [[uuid_of[a], uuid_of[b]] for a, b in
                  con.execute("SELECT a, b FROM people_apart") if a in uuid_of and b in uuid_of],
        "answers": [[_round(r[0], 4), int(r[1]), _round(r[2], 3)] for r in
                    con.execute("SELECT link, same, at FROM people_answers ORDER BY at")],
        "faces": faces,
    }


def write(library, catalog) -> bool:
    """Write this computer's file, if anything in it changed. True when it did."""
    from . import sidecars
    path = my_path(library)
    data = export(catalog)
    if not (data["faces"] or data["people"]):
        return False
    before = None
    try:
        before = path.stat().st_mtime_ns if path.exists() else None
    except OSError:
        pass
    # compact: it is a long list of faces
    text = json.dumps(data, separators=(",", ":"), sort_keys=False)
    try:
        if path.is_file() and path.read_text() == text:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(text)
        tmp.replace(path)
    except OSError as exc:
        log.warning("could not write the people file: %s", exc)
        return False
    return before != (path.stat().st_mtime_ns if path.exists() else None)


# -- reading ----------------------------------------------------------------
def _iou(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _person_for(cur, ids: dict, uuid_: str | None) -> int | None:
    if uuid_ is None:
        return None
    pid = ids.get(uuid_)
    if pid is not None:
        return pid
    # a person the other computer has and this one has not yet: made, so that
    # the faces that belong to them can be put in
    cur.execute("INSERT OR IGNORE INTO people(uuid, created_at) VALUES(?,?)", (uuid_, time.time()))
    row = cur.execute("SELECT id FROM people WHERE uuid=?", (uuid_,)).fetchone()
    ids[uuid_] = row[0]
    return row[0]


def merge_in(catalog, data: dict) -> bool:
    """Merge what another computer wrote into this catalog. True when anything
    here changed. Safe to repeat: the same file twice changes nothing."""
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        return False
    if data.get("computer") == my_id():
        return False                        # this computer's own file says nothing new
    changed = False
    con = catalog.con
    now = time.time()
    with catalog.write() as cur:
        ids = {r[0]: r[1] for r in cur.execute("SELECT uuid, id FROM people")}
        # 1. who the people are, and what they are called: the newest name wins
        for entry in data.get("people") or []:
            uuid_, name, name_at, turn, cover_at, cover_fp, cover_x, cover_y, _custom = (
                list(entry) + [None, 0, 0, 0, 0, None, None, None, None])[:9]
            if not uuid_:
                continue
            if uuid_ not in ids:
                if not name:
                    continue                # an unnamed group: it comes with its faces, below
                _person_for(cur, ids, uuid_)
                cur.execute("UPDATE people SET name=?, name_at=?, cover_turn=? WHERE uuid=?",
                            (name, name_at or 0, int(turn or 0), uuid_))
                changed = True
                continue
            row = cur.execute("SELECT name, name_at FROM people WHERE uuid=?", (uuid_,)).fetchone()
            if name and (name_at or 0) > (row[1] or 0) and name != row[0]:
                cur.execute("UPDATE people SET name=?, name_at=? WHERE uuid=?", (name, name_at, uuid_))
                changed = True
        # 2. who was joined into whom
        for old, new, at in data.get("merged") or []:
            have_old = cur.execute("SELECT id FROM people WHERE uuid=?", (old,)).fetchone()
            if not have_old:
                continue
            target = _person_for(cur, ids, new)
            if target == have_old[0]:
                continue
            cur.execute("UPDATE faces SET person_id=? WHERE person_id=?", (target, have_old[0]))
            nm = cur.execute("SELECT name, name_at FROM people WHERE id=?", (have_old[0],)).fetchone()
            tn = cur.execute("SELECT name FROM people WHERE id=?", (target,)).fetchone()
            if nm and nm[0] and not (tn and tn[0]):
                cur.execute("UPDATE people SET name=?, name_at=? WHERE id=?", (nm[0], nm[1], target))
            cur.execute("DELETE FROM people WHERE id=?", (have_old[0],))
            cur.execute("INSERT OR IGNORE INTO people_merged(old,new,at) VALUES(?,?,?)", (old, new, at))
            ids.pop(old, None)
            changed = True
        # 3. faces
        fp_photos: dict[str, list[int]] = {}
        wanted = {f[0] for f in data.get("faces") or []}
        for pid, fp in cur.execute("SELECT id, fingerprint FROM photos p WHERE fingerprint IS NOT NULL AND "
                                   + _NOT_VIDEO):
            if fp in wanted:
                fp_photos.setdefault(fp, []).append(pid)
        scanned = {r[0] for r in cur.execute("SELECT photo_id FROM face_scans WHERE version>=?",
                                             (people.VERSION,))}
        local_faces: dict[int, list] = {}
        if fp_photos:
            every = [p for ps in fp_photos.values() for p in ps]
            for i in range(0, len(every), 500):
                part = every[i:i + 500]
                for r in cur.execute(
                        f"SELECT id, photo_id, x, y, w, h, state, state_at FROM faces "
                        f"WHERE photo_id IN ({','.join('?' * len(part))})", part):
                    local_faces.setdefault(r["photo_id"], []).append(r)
        theirs = min(int(data.get("scan_version") or 1), people.VERSION)   # the looking they did
        by_fp: dict[str, list] = {}
        for f in data.get("faces") or []:
            by_fp.setdefault(f[0], []).append(f)
        for fp, remote in by_fp.items():
            for photo_id in fp_photos.get(fp, []):
                if photo_id not in scanned and not local_faces.get(photo_id):
                    # not looked at here yet: the other computer's faces are taken whole,
                    # and the photo is not looked at again
                    for f in remote:
                        _, x, y, w, h, score, px, yaw, roll, emb, owner, state, state_at, was = f[:14]
                        pinned = int(f[14]) if len(f) > 14 else 0
                        vec = np.frombuffer(base64.b64decode(emb), np.float16).astype(np.float32)
                        norm = float(np.linalg.norm(vec))
                        if norm == 0:
                            continue
                        cur.execute(
                            "INSERT INTO faces(photo_id,x,y,w,h,score,px,yaw,roll,emb,person_id,state,"
                            "state_at,was_person,pinned) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (photo_id, x, y, w, h, score, px, yaw, roll, (vec / norm).tobytes(),
                             _person_for(cur, ids, owner), int(state), state_at or 0,
                             _person_for(cur, ids, was), pinned))
                    cur.execute("INSERT INTO face_scans(photo_id,version) VALUES(?,?) "
                                "ON CONFLICT(photo_id) DO UPDATE SET version=excluded.version",
                                (photo_id, theirs))
                    scanned.add(photo_id)
                    changed = True
                    continue
                # looked at here too: what the person decided there wins, if it is newer
                mine = local_faces.get(photo_id, [])
                for f in remote:
                    state, state_at = int(f[11]), f[12] or 0
                    if state not in (people.CONFIRMED, people.REMOVED, people.IGNORED):
                        continue
                    best = max(mine, key=lambda m: _iou((f[1], f[2], f[3], f[4]),
                                                        (m["x"], m["y"], m["w"], m["h"])), default=None)
                    if best is None or _iou((f[1], f[2], f[3], f[4]),
                                            (best["x"], best["y"], best["w"], best["h"])) < 0.5:
                        continue
                    if state_at <= (best["state_at"] or 0):
                        continue
                    if state == people.CONFIRMED:
                        owner = _person_for(cur, ids, f[10])
                        cur.execute("UPDATE faces SET person_id=?, state=?, state_at=? WHERE id=?",
                                    (owner, state, state_at, best["id"]))
                    else:
                        was = _person_for(cur, ids, f[13])
                        cur.execute("UPDATE faces SET was_person=COALESCE(person_id, ?), person_id=NULL, "
                                    "state=?, state_at=? WHERE id=?", (was, state, state_at, best["id"]))
                    changed = True
        # the picture the person chose for somebody, if it is newer than this one's
        for entry in data.get("people") or []:
            e = (list(entry) + [None, 0, 0, 0, 0, None, None, None, None])[:9]
            uuid_, cover_at, cover_fp, cover_x, cover_y, custom = e[0], e[4] or 0, e[5], e[6], e[7], e[8]
            if not (uuid_ and cover_at):
                continue
            mine = cur.execute("SELECT id, cover_at FROM people WHERE uuid=?", (uuid_,)).fetchone()
            if mine is None or cover_at <= (mine[1] or 0):
                continue
            if custom:
                # framed by hand on the other computer: the same photo here, by what is in it
                try:
                    d = json.loads(custom)
                    local = cur.execute("SELECT id FROM photos WHERE fingerprint=? LIMIT 1", (d.get("fp"),)).fetchone()
                    if local is not None:
                        d["photo"] = local[0]
                        cur.execute("UPDATE people SET cover_custom=?, cover_turn=0, cover_at=? WHERE id=?",
                                    (json.dumps(d, separators=(",", ":")), cover_at, mine[0]))
                        changed = True
                except (TypeError, ValueError):
                    pass
                continue
            if not cover_fp:
                continue
            face = cur.execute(
                """SELECT f.id FROM faces f JOIN photos p ON p.id=f.photo_id
                   WHERE p.fingerprint=? AND f.person_id=? AND ABS(f.x-?)<0.02 AND ABS(f.y-?)<0.02
                   LIMIT 1""", (cover_fp, mine[0], cover_x, cover_y)).fetchone()
            if face is not None:
                cur.execute("UPDATE people SET cover_face=?, cover_turn=?, cover_custom=NULL, cover_at=? WHERE id=?",
                            (face[0], int(e[3] or 0), cover_at, mine[0]))
                changed = True
        # photos the other computer looked at and found nobody in
        clean = data.get("clean") or [] if theirs >= people.VERSION else []
        for i in range(0, len(clean), 500):
            part = clean[i:i + 500]
            cur.execute(
                f"INSERT INTO face_scans(photo_id, version) SELECT id, ? FROM photos "
                f"WHERE fingerprint IN ({','.join('?' * len(part))}) "
                f"AND id NOT IN (SELECT photo_id FROM face_scans WHERE version>=?) "
                f"AND id NOT IN (SELECT photo_id FROM faces) "
                f"ON CONFLICT(photo_id) DO UPDATE SET version=excluded.version",
                (people.VERSION, *part, people.VERSION))
            changed = changed or cur.rowcount > 0
        # 4. two people with the same name are one
        names: dict[str, list[int]] = {}
        for pid, name in cur.execute("SELECT id, name FROM people WHERE name IS NOT NULL"):
            names.setdefault(" ".join(name.lower().split()), []).append(pid)
        for same in names.values():
            if len(same) > 1:
                keep, rest = same[0], same[1:]
                for other in rest:
                    cur.execute("UPDATE faces SET person_id=? WHERE person_id=?", (keep, other))
                    a = cur.execute("SELECT uuid FROM people WHERE id=?", (keep,)).fetchone()
                    b = cur.execute("SELECT uuid FROM people WHERE id=?", (other,)).fetchone()
                    if a and b:
                        cur.execute("INSERT OR REPLACE INTO people_merged(old,new,at) VALUES(?,?,?)",
                                    (b[0], a[0], now))
                    cur.execute("DELETE FROM people WHERE id=?", (other,))
                    changed = True
        # 5. pairs said to be different, and what was answered
        uuid_id = {r[0]: r[1] for r in cur.execute("SELECT uuid, id FROM people")}
        for ua, ub in data.get("apart") or []:
            a, b = uuid_id.get(ua), uuid_id.get(ub)
            if a is not None and b is not None:
                cur.execute("INSERT OR IGNORE INTO people_apart(a,b) VALUES(?,?)", (min(a, b), max(a, b)))
        have = {(round(r[0], 4), round(r[1], 3)) for r in cur.execute("SELECT link, at FROM people_answers")}
        for link, same, at in data.get("answers") or []:
            if (round(link, 4), round(at, 3)) not in have:
                cur.execute("INSERT INTO people_answers(link,same,at) VALUES(?,?,?)", (link, int(same), at))
                changed = True
    if changed:
        people._tidy(catalog)
        people._covers(catalog)
        people.touch()
    return changed


def read_file(path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def import_all(library, catalog) -> bool:
    """Merge every other computer's file this one has kept (see sync.py): for
    when photos arrive after the file did."""
    changed = False
    folder = cache_dir(library.root)
    if folder.is_dir():
        for f in sorted(folder.glob("*.json")):
            data = read_file(f)
            if data and merge_in(catalog, data):
                changed = True
    return changed


def restore(library, catalog) -> int:
    """After a catalog was rebuilt from the library's files: the people this
    computer knew, from its own file, and whatever the others sent, from theirs
    (a restore brings all of them back)."""
    n = 0
    folder = Path(library.root) / DIR
    if folder.is_dir():
        for f in sorted(folder.glob("*.json")):
            data = read_file(f)
            if not data:
                continue
            data = dict(data)
            data["computer"] = "restore"        # even this computer's own is read: the catalog is empty
            try:
                if merge_in(catalog, data):
                    n += 1
            except Exception as exc:
                log.warning("could not restore people from %s: %s", f.name, exc)
    if import_all(library, catalog):
        n += 1
    return n
