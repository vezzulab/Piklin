"""Mirroring catalog state into readable files.

The library's central promise (see paths.py) is that ``catalog.db`` is a
derived index: delete it, run ``--rebuild-index``, lose nothing.  Album
membership and edits already lived in JSON beside the photos, but three
kinds of state did not, and were silently lost with the database:

* what the user marked - favourite, rating, hidden, in the trash
* the folder tree that organises the albums
* which folders the library watches

That made the README's "you do not need to back it up" actively wrong.
These writers close that gap.  They are deliberately small files:
per-photo state records only photos that deviate from the default, so a
library of 100,000 photos with 400 favourites writes 400 entries, not
100,000.
"""
from __future__ import annotations

import json
from pathlib import Path

FORMAT = "pikalicious-state"
VERSION = 1


def _write_json(path: Path, payload: dict) -> None:
    """Write atomically, so an interrupted save cannot truncate the file.

    A file whose content would not change is left alone: these mirrors are
    rewritten after every change in the library, and a needless rewrite
    would look like a change to the backup and send the file again."""
    text = json.dumps(payload, indent=2, sort_keys=False)
    try:
        if path.is_file() and path.read_text() == text:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text)
        tmp.replace(path)
    except OSError:
        pass


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


# -- per-photo user state -------------------------------------------------
def photo_state_path(library) -> Path:
    return library.root / "photo-state.json"


def write_photo_state(library, catalog) -> None:
    """Record every photo whose state differs from the default.

    Keyed by the photo's path rather than its database id: ids are an
    artefact of the database being rebuilt, paths survive it.
    """
    rows = catalog.q(
        "SELECT path, favorite, rating, hidden, trashed_at, title, caption, "
        "keywords, taken_at, date_source, gps_lat, gps_lon, gps_manual FROM photos "
        "WHERE favorite=1 OR rating>0 OR hidden=1 OR trashed_at IS NOT NULL "
        "OR COALESCE(title,'') != '' OR COALESCE(caption,'') != '' "
        "OR COALESCE(keywords,'') != '' OR date_source='manual' OR gps_manual=1")
    photos = {}
    for r in rows:
        entry = {}
        # Title, caption and keywords are typed by the user and exist
        # nowhere else - the photo file is never written - so they must
        # live in this file to survive a catalog rebuild.
        for key in ("title", "caption", "keywords"):
            if r[key]:
                entry[key] = r[key]
        # A date or location set by hand exists only here and in the catalog.
        if r["date_source"] == "manual" and r["taken_at"] is not None:
            entry["taken_at"] = r["taken_at"]
        if r["gps_manual"]:
            entry["location"] = ([r["gps_lat"], r["gps_lon"]]
                                 if r["gps_lat"] is not None else None)
        if r["favorite"]:
            entry["favorite"] = True
        if r["rating"]:
            entry["rating"] = r["rating"]
        if r["hidden"]:
            entry["hidden"] = True
        if r["trashed_at"] is not None:
            entry["trashed_at"] = r["trashed_at"]
        if entry:
            photos[r["path"]] = entry
    _write_json(photo_state_path(library),
                {"format": FORMAT, "version": VERSION, "photos": photos})


def restore_photo_state(library, catalog) -> int:
    data = _read_json(photo_state_path(library))
    photos = data.get("photos") or {}
    restored = 0
    with catalog.write() as cur:
        for path, entry in photos.items():
            cur.execute(
                "UPDATE photos SET favorite=?, rating=?, hidden=?, trashed_at=?, "
                "title=?, caption=?, keywords=? WHERE path=?",
                (int(bool(entry.get("favorite"))), int(entry.get("rating") or 0),
                 int(bool(entry.get("hidden"))), entry.get("trashed_at"),
                 entry.get("title"), entry.get("caption"), entry.get("keywords"),
                 path))
            restored += cur.rowcount
            if entry.get("taken_at") is not None:
                cur.execute("UPDATE photos SET taken_at=?, date_source='manual' "
                            "WHERE path=?", (entry["taken_at"], path))
            if "location" in entry:
                loc = entry["location"] or [None, None]
                cur.execute("UPDATE photos SET gps_lat=?, gps_lon=?, gps_manual=1 "
                            "WHERE path=?", (loc[0], loc[1], path))
    # The search text is rebuilt from the restored words too.
    if restored:
        catalog.reindex_text()
    return restored


# -- folder tree ----------------------------------------------------------
def folders_path(library) -> Path:
    return library.albums / "_folders.json"


def write_folders(library, catalog) -> None:
    """Mirror the folder tree.

    Parents are referenced by uuid, not by row id, so the file stays
    meaningful after a rebuild hands out different ids.
    """
    rows = catalog.folders()
    by_id = {r["id"]: r["uuid"] for r in rows}
    folders = [{
        "uuid": r["uuid"],
        "name": r["name"],
        "parent_uuid": by_id.get(r["parent_id"]),
        "created_at": r["created_at"],
        "position": r["position"],
    } for r in rows]
    _write_json(folders_path(library),
                {"format": FORMAT, "version": VERSION, "folders": folders})


def restore_folders(library, catalog) -> int:
    """Recreate folders, parents first, and re-attach albums to them."""
    data = _read_json(folders_path(library))
    folders = data.get("folders") or []
    if not folders:
        return 0

    by_uuid = {f["uuid"]: f for f in folders}
    created: dict[str, int] = {}

    def ensure(uuid_: str, guard: set) -> int | None:
        if uuid_ in created:
            return created[uuid_]
        f = by_uuid.get(uuid_)
        if f is None or uuid_ in guard:
            return None                      # missing, or a parent cycle
        guard.add(uuid_)
        parent = f.get("parent_uuid")
        parent_id = ensure(parent, guard) if parent else None
        created[uuid_] = catalog.create_folder(
            f.get("name") or "Untitled Folder", parent_id=parent_id,
            folder_uuid=uuid_)
        return created[uuid_]

    for f in folders:
        ensure(f["uuid"], set())
    return len(created)


# -- watched roots --------------------------------------------------------
def roots_path(library) -> Path:
    return library.root / "watched-folders.json"


def write_roots(library, catalog) -> None:
    rows = catalog.roots(enabled_only=False)
    _write_json(roots_path(library), {
        "format": FORMAT, "version": VERSION,
        "folders": [{"path": r["path"], "in_library": bool(r["in_library"]),
                     "enabled": bool(r["enabled"])} for r in rows],
    })


def read_roots(library) -> list[dict]:
    return _read_json(roots_path(library)).get("folders") or []


# -- smart albums ---------------------------------------------------------
def smart_albums_path(library) -> Path:
    return library.albums / "_smart.json"


def write_smart_albums(library, catalog) -> None:
    """Mirror Smart Albums - a name, a match mode and rules. They hold no
    photos of their own, so this is everything a rebuild needs."""
    folders = {r["id"]: r["uuid"] for r in catalog.folders()}
    items = [{
        "uuid": r["uuid"],
        "name": r["name"],
        "match_mode": r["match_mode"],
        "rules": json.loads(r["rules"] or "[]"),
        "folder_uuid": folders.get(r["folder_id"]),
        "created_at": r["created_at"],
    } for r in catalog.smart_albums()]
    _write_json(smart_albums_path(library),
                {"format": FORMAT, "version": VERSION, "smart_albums": items})


def restore_smart_albums(library, catalog) -> int:
    data = _read_json(smart_albums_path(library))
    folders = {r["uuid"]: r["id"] for r in catalog.folders()}
    have = {r["uuid"] for r in catalog.smart_albums()}
    n = 0
    for item in data.get("smart_albums") or []:
        if item.get("uuid") in have:
            continue
        catalog.create_smart_album(
            item.get("name") or "Smart Album", item.get("rules") or [],
            item.get("match_mode") or "all",
            folder_id=folders.get(item.get("folder_uuid")),
            album_uuid=item.get("uuid"))
        n += 1
    return n


# -- removed from the library ------------------------------------------------
def removed_path(library) -> Path:
    return library.root / "removed-photos.json"


def write_removed(library, catalog) -> None:
    rows = catalog.q("SELECT path, removed_at FROM removed ORDER BY path")
    _write_json(removed_path(library), {
        "format": FORMAT, "version": VERSION,
        "photos": {r["path"]: r["removed_at"] for r in rows}})


def restore_removed(library, catalog) -> int:
    photos = _read_json(removed_path(library)).get("photos") or {}
    entries = [(p, float(t)) for p, t in photos.items()
               if isinstance(p, str) and isinstance(t, (int, float))]
    catalog.set_removed(entries)
    return len(entries)


def write_all(library, catalog) -> None:
    write_photo_state(library, catalog)
    write_removed(library, catalog)
    write_folders(library, catalog)
    write_roots(library, catalog)
    write_smart_albums(library, catalog)
