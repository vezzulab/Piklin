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

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

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
               now: float | None = None, note_missing: bool = False) -> dict:
    """A list file (``key`` "folders" or "smart_albums"), just written from the
    catalog, with when each item last changed and which were deleted.

    An item that is in the file but not in the catalog is *not* taken as
    deleted: a library whose catalog has not been rebuilt yet - after a
    restore, or after catalog.db was thrown away - knows nothing of the
    folders its own files describe, and inferring deletions from that
    emptiness wiped restored folders and then sent the wipe to every other
    computer through the backup. Deletions are recorded where they happen,
    by ``sidecars.note_folder_deleted``. ``note_missing`` is for the tests
    that check the old inference still behaves."""
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
    if note_missing:
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


def merge_deleted_albums(mine: dict | None, theirs: dict | None) -> dict:
    albums: dict[str, float] = {}
    for side in (mine or {}, theirs or {}):
        for uuid_, when in (side.get("albums") or {}).items():
            try:
                albums[uuid_] = max(albums.get(uuid_, 0.0), float(when))
            except (TypeError, ValueError):
                continue
    out = dict(mine or theirs or {})
    out["albums"] = albums
    return out


def album_deleted_after_change(album: dict, deleted_at: float | None) -> bool:
    """Whether an album's deletion came after everything done to it."""
    if deleted_at is None:
        return False
    latest = max([_time(album)] + [float(t or 0) for t in (album.get("added") or {}).values()])
    return float(deleted_at) >= latest


# ==========================================================================
# bringing in what another computer changed
# ==========================================================================
@dataclass
class SyncResult:
    ok: bool = True
    message: str = ""
    unreachable: bool = False
    photos: int = 0          # photos and videos brought in
    changed: bool = False    # anything in this library changed


_LISTS = {
    "Albums/_folders.json": lambda a, b: merge_list(a, b, "folders"),
    "Albums/_smart.json": lambda a, b: merge_list(a, b, "smart_albums"),
    "Albums/_deleted.json": merge_deleted_albums,
    "photo-state.json": merge_marks,
    "removed-photos.json": merge_removed,
}


def _read(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _seen_path(root: Path, remote_id: str) -> Path:
    return Path(root) / ".cache" / "backups" / f"{remote_id}-seen.json"


def pull(library, catalog, backend, progress=None) -> SyncResult:
    """Bring into this library what another computer sent to ``backend``.

    Only what changed at the destination since it was last looked at is
    read. The library's files are merged first; the catalog is then brought
    in line with them (see apply)."""
    from . import sidecars
    root = Path(library.root)
    # A handful of files first: when they are as this computer last saw them,
    # nobody sent anything, and the backup isn't read through.
    try:
        same, top = backend.unchanged_since_last_look(root)
    except Exception as exc:
        return SyncResult(False, str(exc), unreachable=True)
    if same:
        return SyncResult()
    try:
        index = backend.listing()
    except Exception as exc:
        return SyncResult(False, str(exc), unreachable=True)
    seen_file = _seen_path(root, backend.remote.id)
    seen = _read(seen_file)
    new_seen = dict(seen)
    work = root / ".cache" / "sync"

    def changed(rel: str) -> bool:
        return rel in index and list(index[rel]) != list(seen.get(rel) or [])

    def fetch_json(rel: str):
        target = work / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if not backend.get(rel, target):
                return None
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        finally:
            target.unlink(missing_ok=True)
        return adopt_paths(data, str(root)) if isinstance(data, dict) else None

    result = SyncResult()
    if progress:
        progress("lists")
    for rel, merge in _LISTS.items():
        if not changed(rel):
            continue
        theirs = fetch_json(rel)
        if theirs is None:
            continue
        local = root / rel
        mine = _read(local) or None
        merged = merge(mine, theirs)
        if merged != mine:
            sidecars._write_json(local, merged)
            result.changed = True
        new_seen[rel] = list(index[rel])

    deleted = (_read(sidecars.deleted_albums_path(library)).get("albums") or {})
    local_albums = {}
    for f in library.albums.glob("*.json"):
        if not f.name.startswith("_"):
            uuid_ = _read(f).get("uuid")
            if uuid_:
                local_albums[uuid_] = f
    for rel in sorted(index):
        name = rel.rsplit("/", 1)[-1]
        if (not rel.startswith("Albums/") or name.startswith("_") or not name.endswith(".json")
                or not changed(rel)):
            continue
        theirs = fetch_json(rel)
        new_seen[rel] = list(index[rel])
        if not theirs or theirs.get("format") != "pikalicious-album" or not theirs.get("uuid"):
            continue
        uuid_ = theirs["uuid"]
        mine_path = local_albums.get(uuid_)
        mine = _read(mine_path) if mine_path else None
        merged = merge_album(mine, theirs)
        if album_deleted_after_change(merged, deleted.get(uuid_)):
            if mine_path is not None:
                mine_path.unlink(missing_ok=True)
                result.changed = True
            continue
        if merged != mine:
            target = library.albums / sidecars.album_file_name(merged.get("name", ""), uuid_)
            sidecars._write_json(target, merged)
            if mine_path is not None and mine_path != target:
                mine_path.unlink(missing_ok=True)
            result.changed = True

    # Edits: the other computer's, unless this one changed the same photo later.
    manifest = backend._load_manifest(root)
    todo = []
    edits = []
    for rel in sorted(index):
        if not rel.startswith("Edits/") or not changed(rel):
            continue
        size, when = index[rel]
        local = root / rel
        if local.exists():
            st = local.stat()
            sent = manifest.get(rel)
            unsent = not sent or sent[0] != st.st_size or abs(sent[1] - st.st_mtime) >= 1
            if unsent and st.st_mtime >= when:
                new_seen[rel] = list(index[rel])     # this computer's is newer: it goes up
                continue
        todo.append((rel, size))
        edits.append(rel)
    # Photos and videos this computer doesn't have, unless removed on purpose.
    removed = {os.path.normpath(p) for p in
               (_read(sidecars.removed_path(library)).get("photos") or {})}
    originals = [(rel, index[rel][0]) for rel in sorted(index)
                 if rel.startswith("Originals/") and not (root / rel).exists()
                 and os.path.normpath(str(root / rel)) not in removed]
    todo += originals
    if todo:
        from .remote import SyncProgress
        p = SyncProgress(phase="downloading", total_files=len(todo),
                         total_bytes=sum(max(s, 0) for _r, s in todo))

        def report(q):
            if progress:
                progress("downloading", q.done_files, q.total_files, int(q.fraction * 100))
        backend._fetch_many(root, todo, p, manifest, report)
        backend._save_manifest(root, manifest)
        if p.phase == "cancelled":
            result.ok, result.message = False, "cancelled"
    brought = [rel for rel, _s in originals if (root / rel).exists()]
    fresh_edits = [root / rel for rel in edits if (root / rel).exists()]
    for rel in edits:
        if (root / rel).exists():
            new_seen[rel] = list(index[rel])
    result.photos = len(brought)
    result.changed = result.changed or bool(brought) or bool(fresh_edits)
    try:
        seen_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = seen_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(new_seen))
        tmp.replace(seen_file)
    except OSError:
        pass
    if result.changed:
        if progress:
            progress("applying")
        apply(library, catalog, new_photos=bool(brought), edits=fresh_edits)
    if result.ok:
        backend.remember_look(root, top, full=True)
    return result


def apply(library, catalog, new_photos: bool = False, edits=()) -> None:
    """Bring the catalog in line with the library's files, after they were
    merged with another computer's. The order matters: albums move out of a
    folder before the folder is deleted, as deleting a folder deletes what is
    still inside it."""
    from . import sidecars
    root = Path(library.root)
    if new_photos:
        from .indexer import Indexer
        Indexer(catalog, None, library_root=root).scan([library.originals])

    # removed photos
    removed = _read(sidecars.removed_path(library)).get("photos") or {}
    known = set(catalog.removed_paths())
    fresh = []
    for path, when in removed.items():
        if path not in known:
            try:
                fresh.append((path, float(when)))
            except (TypeError, ValueError):
                continue
    if fresh:
        ids = [row["id"] for row in (catalog.photo_by_path(p) for p, _w in fresh) if row]
        catalog.forget_photos(ids)
        catalog.set_removed(fresh)

    # folders: created and changed (deleted at the end)
    folders_data = _read(sidecars.folders_path(library))
    wanted = {e["uuid"]: e for e in folders_data.get("folders") or []
              if isinstance(e, dict) and e.get("uuid")}
    rows = {r["uuid"]: r for r in catalog.folders()}

    def ensure(uuid_, guard):
        if uuid_ in rows:
            return rows[uuid_]["id"]
        entry = wanted.get(uuid_)
        if entry is None or uuid_ in guard:
            return None
        guard.add(uuid_)
        parent = entry.get("parent_uuid")
        parent_id = ensure(parent, guard) if parent else None
        fid = catalog.create_folder(entry.get("name") or "Untitled Folder",
                                    parent_id=parent_id, folder_uuid=uuid_)
        rows[uuid_] = {"id": fid, "uuid": uuid_, "name": entry.get("name"),
                       "parent_id": parent_id}
        return fid
    for uuid_ in wanted:
        ensure(uuid_, set())
    rows = {r["uuid"]: r for r in catalog.folders()}
    for uuid_, entry in wanted.items():
        row = rows.get(uuid_)
        if row is None:
            continue
        if entry.get("name") and entry["name"] != row["name"]:
            catalog.rename_folder(row["id"], entry["name"])
        parent = rows.get(entry.get("parent_uuid")) if entry.get("parent_uuid") else None
        parent_id = parent["id"] if parent else None
        if parent_id != row["parent_id"]:
            catalog.move_folder(row["id"], parent_id)
    folder_ids = {u: r["id"] for u, r in rows.items()}

    # Smart Albums
    smart_data = _read(sidecars.smart_albums_path(library))
    smart_wanted = {e["uuid"]: e for e in smart_data.get("smart_albums") or []
                    if isinstance(e, dict) and e.get("uuid")}
    smart_rows = {r["uuid"]: r for r in catalog.smart_albums()}
    for uuid_, entry in smart_wanted.items():
        folder_id = folder_ids.get(entry.get("folder_uuid"))
        row = smart_rows.get(uuid_)
        if row is None:
            catalog.create_smart_album(entry.get("name") or "Smart Album", entry.get("rules") or [],
                                       entry.get("match_mode") or "all", folder_id=folder_id,
                                       album_uuid=uuid_)
            continue
        if (entry.get("name"), json.dumps(entry.get("rules") or []), entry.get("match_mode")) != \
                (row["name"], row["rules"], row["match_mode"]):
            catalog.update_smart_album(row["id"], entry.get("name") or row["name"],
                                       entry.get("rules") or [], entry.get("match_mode") or "all")
        if folder_id != row["folder_id"]:
            catalog.move_smart_album_to_folder(row["id"], folder_id)
    for uuid_ in smart_data.get("deleted") or {}:
        if uuid_ in smart_rows and uuid_ not in smart_wanted:
            catalog.delete_smart_album(smart_rows[uuid_]["id"])

    # albums
    files = {}
    for f in library.albums.glob("*.json"):
        if f.name.startswith("_"):
            continue
        data = _read(f)
        if data.get("format") == "pikalicious-album" and data.get("uuid"):
            files[data["uuid"]] = data
    album_rows = {r["uuid"]: r for r in catalog.q("SELECT id, uuid, name, folder_id FROM albums")}
    for uuid_, data in files.items():
        folder_id = folder_ids.get(data.get("folder_uuid"))
        row = album_rows.get(uuid_)
        if row is None:
            album_id = catalog.create_album(data.get("name") or "Album", uuid_, folder_id=folder_id)
        else:
            album_id = row["id"]
            if data.get("name") and data["name"] != row["name"]:
                catalog.rename_album(album_id, data["name"])
            if folder_id != row["folder_id"]:
                catalog.move_album_to_folder(album_id, folder_id)
        present = {r["path"]: r["photo_id"] for r in catalog.q(
            "SELECT p.path, ai.photo_id FROM album_items ai JOIN photos p ON p.id=ai.photo_id "
            "WHERE ai.album_id=?", (album_id,))}
        add = []
        for path in data.get("photos") or []:
            if path not in present:
                photo = catalog.photo_by_path(path)
                if photo is not None:
                    add.append(photo["id"])
        if add:
            catalog.album_add(album_id, add)
        gone = [pid for path, pid in present.items()
                if path in (data.get("removed") or {}) and path not in set(data.get("photos") or [])]
        if gone:
            catalog.album_remove(album_id, gone)
        if data.get("cover"):
            cover = catalog.photo_by_path(data["cover"])
            if cover is not None:
                catalog.set_album_cover(album_id, cover["id"])
    deleted_albums = _read(sidecars.deleted_albums_path(library)).get("albums") or {}
    for uuid_ in deleted_albums:
        if uuid_ in album_rows and uuid_ not in files:
            catalog.delete_album(album_rows[uuid_]["id"])

    # folders deleted, now that their albums have moved out
    for uuid_ in folders_data.get("deleted") or {}:
        if uuid_ in rows and uuid_ not in wanted:
            catalog.delete_folder(rows[uuid_]["id"])

    # marks
    marks = _read(sidecars.photo_state_path(library))
    if marks.get("photos"):
        sidecars.restore_photo_state(library, catalog)
    cleared = [p for p in (marks.get("cleared") or {}) if p not in (marks.get("photos") or {})]
    if cleared:
        with catalog.write() as cur:
            cur.executemany(
                "UPDATE photos SET favorite=0, rating=0, hidden=0, trashed_at=NULL, "
                "title=NULL, caption=NULL, keywords=NULL WHERE path=?", [(p,) for p in cleared])

    # edits made on the other computer
    for sidecar in edits:
        data = _read(sidecar)
        source = data.get("source")
        if not source:
            continue
        source = adopt_paths({"source": source}, str(root))["source"]
        photo = catalog.photo_by_path(source)
        if photo is not None:
            catalog.note_edit(photo["id"], len(data.get("layers") or []) or int(data.get("changes") or 0))
