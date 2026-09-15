"""Keeping computers that share a backup in step.

Two computers backing up to the same NAS or cloud folder each bring the other
what it lacks: new photos, albums, folders, marks and edits. Nothing is lost
when both change something - these rules decide, for each kind of list the
library keeps in readable files:

* An album remembers when each of its photos was added and removed. Merged,
  a photo is in the album when it was added after it was last removed, on
  either computer. The name, folder and cover come from the latest change.
* Folders and Smart Albums remember when each last changed, and which were
  deleted, and when. A deletion wins unless the item changed after it.
* Marks (favourite, rating, hidden, title...) are per photo: the latest
  change wins, including marks cleared since.
* Photos removed from the library stay removed on every computer.

The files keep the shape older versions of Piklin read: an album's photos
stay a plain list, with the history beside it.

Everything here is plain data in, plain data out - reading and writing the
files, and bringing the catalog in line, happen elsewhere.
"""
from __future__ import annotations

import time

from . import library_move


def _time(entry, key: str = "modified_at") -> float:
    try:
        return float((entry or {}).get(key) or 0)
    except (TypeError, ValueError, AttributeError):
        return 0.0


def adopt_paths(data, library_root: str):
    """``data`` from another computer with its photo paths pointed at this
    library: /home/ana/Pictures/Piklin Library.piklin/Originals/... written
    on Linux names /Users/ana/Pictures/... on a Mac."""
    olds = set()
    for text in library_move._strings(data):
        prefix = library_move._library_prefix(text)
        if prefix is not None and prefix != library_root:
            olds.add(prefix)
    if not olds:
        return data
    return library_move._adopt(data, sorted(olds, key=len, reverse=True), library_root)


# -- albums -------------------------------------------------------------------
def stamp_album(previous: dict | None, current: dict, now: float | None = None) -> dict:
    """The album file ``current``, just written from the catalog, with the
    history a merge needs, carried on from ``previous`` (the file as it was):
    when each photo came in or left, and when the album itself last changed."""
    now = time.time() if now is None else now
    prev = previous or {}
    before = list(prev.get("photos") or [])
    after = list(current.get("photos") or [])
    had, has = set(before), set(after)
    added = dict(prev.get("added") or {})
    removed = dict(prev.get("removed") or {})
    # A photo already in an album written by an older version has no date:
    # it counts from the album's own last change.
    since = _time(prev) if previous else now
    for path in after:
        if path not in had:
            added[path] = now
        else:
            added.setdefault(path, since)
    for path in had - has:
        removed[path] = now
    out = dict(current)
    out["added"] = {p: added[p] for p in after}
    out["removed"] = {p: t for p, t in removed.items() if p not in has}
    changed = previous is None or any(prev.get(k) != current.get(k)
                                      for k in ("name", "folder_uuid", "cover"))
    # Unchanged keeps its date - none, from an older version, counts as the
    # oldest - so a real change made elsewhere is never outranked by a rewrite.
    out["modified_at"] = now if changed else _time(prev)
    return out


def merge_album(mine: dict | None, theirs: dict | None) -> dict | None:
    """One album as changed on two computers."""
    if mine is None or theirs is None:
        return mine if theirs is None else theirs
    newer = theirs if _time(theirs) > _time(mine) else mine
    out = dict(newer)
    added: dict[str, float] = {}
    removed: dict[str, float] = {}
    order: list[str] = []
    for side in (mine, theirs):
        stamps = side.get("added") or {}
        for path in side.get("photos") or []:
            if path not in added:
                order.append(path)
            added[path] = max(added.get(path, 0.0), float(stamps.get(path, 0.0) or 0.0))
        for path, when in (side.get("removed") or {}).items():
            removed[path] = max(removed.get(path, 0.0), float(when or 0.0))
    photos = [p for p in order if added[p] >= removed.get(p, -1.0)]
    kept = set(photos)
    out["photos"] = photos
    out["added"] = {p: added[p] for p in photos}
    out["removed"] = {p: t for p, t in removed.items() if p not in kept}
    out["modified_at"] = max(_time(mine), _time(theirs))
    return out


# -- folders and Smart Albums -------------------------------------------------------
def stamp_list(previous: dict | None, current: dict, key: str,
               now: float | None = None) -> dict:
    """A list file (``key`` "folders" or "smart_albums"), just written from the
    catalog, with when each item last changed and which were deleted."""
    now = time.time() if now is None else now
    prev = previous or {}
    before = {e["uuid"]: e for e in prev.get(key) or []
              if isinstance(e, dict) and e.get("uuid")}

    def content(entry):
        return {k: v for k, v in entry.items() if k != "modified_at"}

    entries = []
    for entry in current.get(key) or []:
        old = before.get(entry.get("uuid"))
        same = old is not None and content(old) == content(entry)
        entries.append(dict(entry, modified_at=_time(old) if same else now))
    present = {e.get("uuid") for e in entries}
    deleted = dict(prev.get("deleted") or {})
    for uuid_ in before:
        if uuid_ not in present:
            deleted.setdefault(uuid_, now)
    out = dict(current)
    out[key] = entries
    out["deleted"] = {u: t for u, t in deleted.items() if u not in present}
    return out


def merge_list(mine: dict | None, theirs: dict | None, key: str) -> dict:
    """Folders or Smart Albums as changed on two computers."""
    best: dict[str, dict] = {}
    order: list[str] = []
    deleted: dict[str, float] = {}
    for side in (mine or {}, theirs or {}):
        for entry in side.get(key) or []:
            if not isinstance(entry, dict) or not entry.get("uuid"):
                continue
            uuid_ = entry["uuid"]
            if uuid_ not in best:
                order.append(uuid_)
                best[uuid_] = entry
            elif _time(entry) > _time(best[uuid_]):
                best[uuid_] = entry
        for uuid_, when in (side.get("deleted") or {}).items():
            deleted[uuid_] = max(deleted.get(uuid_, 0.0), float(when or 0.0))
    entries = [best[u] for u in order
               if not (u in deleted and deleted[u] >= _time(best[u]))]
    present = {e["uuid"] for e in entries}
    out = dict(mine or theirs or {})
    out[key] = entries
    out["deleted"] = {u: t for u, t in deleted.items() if u not in present}
    return out


# -- marks --------------------------------------------------------------------------
def stamp_marks(previous: dict | None, current: dict, now: float | None = None) -> dict:
    """photo-state.json, just written from the catalog, with when each
    photo's marks last changed and which photos had theirs cleared."""
    now = time.time() if now is None else now
    prev = previous or {}
    before = prev.get("photos") or {}
    photos = {}
    for path, entry in (current.get("photos") or {}).items():
        old = before.get(path)
        same = isinstance(old, dict) and {k: v for k, v in old.items() if k != "modified_at"} == entry
        photos[path] = dict(entry, modified_at=_time(old) if same else now)
    cleared = dict(prev.get("cleared") or {})
    for path in before:
        if path not in photos:
            cleared.setdefault(path, now)
    out = dict(current)
    out["photos"] = photos
    out["cleared"] = {p: t for p, t in cleared.items() if p not in photos}
    return out


def merge_marks(mine: dict | None, theirs: dict | None) -> dict:
    best: dict[str, dict] = {}
    cleared: dict[str, float] = {}
    for side in (mine or {}, theirs or {}):
        for path, entry in (side.get("photos") or {}).items():
            if isinstance(entry, dict) and (path not in best or _time(entry) > _time(best[path])):
                best[path] = entry
        for path, when in (side.get("cleared") or {}).items():
            cleared[path] = max(cleared.get(path, 0.0), float(when or 0.0))
    photos = {p: e for p, e in best.items() if not (p in cleared and cleared[p] >= _time(e))}
    out = dict(mine or theirs or {})
    out["photos"] = photos
    out["cleared"] = {p: t for p, t in cleared.items() if p not in photos}
    return out


def merge_removed(mine: dict | None, theirs: dict | None) -> dict:
    """Photos removed from the library, on either computer, stay removed."""
    photos: dict[str, float] = {}
    for side in (mine or {}, theirs or {}):
        for path, when in (side.get("photos") or {}).items():
            try:
                photos[path] = max(photos.get(path, 0.0), float(when))
            except (TypeError, ValueError):
                continue
    out = dict(mine or theirs or {})
    out["photos"] = photos
    return out
