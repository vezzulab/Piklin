"""Creations as things you keep: written down, drawn into the library,
backed up and carried to your other computers.

A creation is two things at once. There is the picture it produced - a
collage, a poster - which belongs in the library like any other photo, so
that it is backed up, synced, opened, exported and printed by everything
Piklin already does. And there is the recipe behind it, which is what
lets you open it again next month and move a photo.

The picture is filed into Originals by the date it was made; the recipe
lives in the catalog and in one file beside the library, the way albums
and Smart Albums do, so another computer can open the creation too.
"""
from __future__ import annotations

import json
import time
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import create, sidecars

SIDECAR = "creations.json"
FORMAT, VERSION = "piklin-creations", 1

# What can be made. The interface names them; this is what is written
# down, so a kind Piklin does not know about yet still round-trips.
KINDS = ("collage", "poster")


@dataclass
class Creation:
    uuid: str = ""
    name: str = ""
    kind: str = "collage"
    pages: list[create.Page] = field(default_factory=list)
    photo_id: int | None = None
    created_at: float = 0.0
    updated_at: float = 0.0

    @property
    def page(self) -> create.Page:
        if not self.pages:
            self.pages.append(create.Page())
        return self.pages[0]

    def doc(self) -> str:
        return json.dumps({"pages": [p.to_dict() for p in self.pages]})

    @classmethod
    def from_row(cls, row) -> "Creation":
        try:
            doc = json.loads(row["doc"] or "{}")
        except (TypeError, ValueError):
            doc = {}
        return cls(uuid=row["uuid"], name=row["name"], kind=row["kind"],
                   pages=[create.Page.from_dict(p) for p in doc.get("pages") or []],
                   photo_id=row["photo_id"],
                   created_at=row["created_at"], updated_at=row["updated_at"])


def new(kind: str = "collage", name: str = "") -> Creation:
    now = time.time()
    return Creation(uuid=str(_uuid.uuid4()), name=name, kind=kind,
                    created_at=now, updated_at=now)


# ======================================================================
# the catalog
# ======================================================================
def all_creations(catalog) -> list[Creation]:
    return [Creation.from_row(r) for r in catalog.q(
        "SELECT * FROM creations ORDER BY updated_at DESC")]


def get(catalog, uuid_: str) -> Creation | None:
    row = catalog.q1("SELECT * FROM creations WHERE uuid=?", (uuid_,))
    return Creation.from_row(row) if row is not None else None


def count(catalog) -> int:
    return int(catalog.scalar("SELECT COUNT(*) FROM creations", default=0) or 0)


def write(catalog, c: Creation, now: float | None = None) -> Creation:
    """Put the recipe in the catalog, whether it is new or changed."""
    c.updated_at = time.time() if now is None else now
    if not c.uuid:
        c.uuid = str(_uuid.uuid4())
    if not c.created_at:
        c.created_at = c.updated_at
    with catalog.write() as cur:
        cur.execute(
            "INSERT INTO creations(uuid,name,kind,doc,photo_id,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(uuid) DO UPDATE SET name=excluded.name, kind=excluded.kind, "
            "doc=excluded.doc, photo_id=excluded.photo_id, updated_at=excluded.updated_at",
            (c.uuid, c.name, c.kind, c.doc(), c.photo_id, c.created_at, c.updated_at))
    return c


def forget(catalog, uuid_: str) -> None:
    """Drop the recipe. The picture it made is an ordinary photo in the
    library and is left exactly where it is: deleting the creation must
    not take a photo out from under someone."""
    with catalog.write() as cur:
        cur.execute("DELETE FROM creations WHERE uuid=?", (uuid_,))


def photo_ids(catalog) -> list[int]:
    return [int(r["photo_id"]) for r in catalog.q(
        "SELECT photo_id FROM creations WHERE photo_id IS NOT NULL "
        "ORDER BY updated_at DESC") if r["photo_id"]]


# ======================================================================
# the picture, in the library
# ======================================================================
def _free_path(folder: Path, stem: str, suffix: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{stem}{suffix}"
    n = 2
    while dest.exists():
        dest = folder / f"{stem}-{n}{suffix}"
        n += 1
    return dest


def _safe_stem(name: str) -> str:
    keep = [ch if (ch.isalnum() or ch in " -_") else "-" for ch in (name or "")]
    stem = "".join(keep).strip().strip("-") or "Creation"
    return stem[:60]


def render_into_library(library, catalog, c: Creation, indexer=None,
                        suffix: str = ".jpg", long_side: int | None = None,
                        on_progress=None) -> int | None:
    """Draw the creation and file it in the library as a photo.

    The picture replaces the one this creation made before, if it made
    one, so changing a collage leaves one collage in the library and not
    a row of near-copies. Returns the photo's id.
    """
    from . import imageio as iio
    made = datetime.now()
    folder = library.originals / made.strftime("%Y") / made.strftime("%Y-%m-%d")
    old_row = (catalog.photo(c.photo_id) if c.photo_id else None)
    old_path = Path(old_row["path"]) if old_row is not None else None

    dest = (old_path if old_path is not None and old_path.suffix.lower() == suffix
            else _free_path(folder, _safe_stem(c.name), suffix))
    create.save(c.pages, dest, long_side=long_side, on_progress=on_progress)

    if old_path is not None and old_path == dest:
        # Same file, new content: the catalog's size, shape and
        # fingerprint have to follow, or the photo-health check would
        # later read this as a file that changed on its own.
        rec = iio.probe(dest)
        if rec is not None:
            with catalog.write() as cur:
                cur.execute("UPDATE photos SET width=?, height=?, bytes=?, "
                            "fingerprint=?, thumb_state=0 WHERE id=?",
                            (rec.get("width"), rec.get("height"),
                             dest.stat().st_size, rec.get("fingerprint"),
                             c.photo_id))
        return c.photo_id

    if indexer is not None:
        indexer.add_files([str(dest)])
    row = catalog.q1("SELECT id FROM photos WHERE path=?", (str(dest),))
    photo_id = int(row["id"]) if row is not None else None
    c.photo_id = photo_id
    return photo_id


def save(library, catalog, c: Creation, indexer=None, on_progress=None) -> Creation:
    """Everything one Save does: draw it, file it, write it down, and
    leave the file beside the library that the other computers read."""
    render_into_library(library, catalog, c, indexer, on_progress=on_progress)
    write(catalog, c)
    write_sidecar(library, catalog)
    return c


# ======================================================================
# the file the other computers read
# ======================================================================
def sidecar_path(library) -> Path:
    return Path(library.root) / SIDECAR


def write_sidecar(library, catalog) -> None:
    from . import sync
    path = sidecar_path(library)
    items = []
    for c in all_creations(catalog):
        made = catalog.photo(c.photo_id) if c.photo_id else None
        items.append({
            "uuid": c.uuid,
            "name": c.name,
            "kind": c.kind,
            # Photos are named by their file, the way an album names
            # them: a row id is this catalog's alone, and a photo that
            # travelled here in a backup was given a new one on the way.
            # Another computer reads these through the same rewriting
            # that points an album's photos at its own library.
            "doc": json.loads(c.doc()),
            "made": made["path"] if made is not None else None,
            "created_at": c.created_at,
            "modified_at": c.updated_at,
        })
    items = sidecars._keep_unknown(path, "creations", items)
    sidecars._write_json(path, sync.stamp_list(
        sidecars._read_json(path) or None,
        {"format": FORMAT, "version": VERSION, "creations": items}, "creations"))


def restore_sidecar(library, catalog) -> int:
    """Take in creations this catalog does not have - after a restore, or
    when another computer made one. The photos are found again by their
    own uuids, so a creation opens on a computer that keeps its library
    somewhere else entirely."""
    from . import sync
    data = sidecars._read_json(sidecar_path(library))
    # Written on another computer, its library somewhere else entirely:
    # the paths inside are pointed at this one first.
    data = sync.adopt_paths(data, str(Path(library.root)))
    have = {c.uuid: c for c in all_creations(catalog)}
    by_path = {r["path"]: int(r["id"])
               for r in catalog.q("SELECT id, path FROM photos")}
    n = 0
    for item in data.get("creations") or []:
        u = item.get("uuid")
        if not u:
            continue
        mine = have.get(u)
        theirs_at = float(item.get("modified_at") or 0)
        if mine is not None and mine.updated_at >= theirs_at:
            continue
        doc = item.get("doc") or {}
        pages = [create.Page.from_dict(p) for p in doc.get("pages") or []]
        for page in pages:
            for slot in page.slots:
                # The row id is looked up again here; a photo this
                # computer does not have keeps its place on the page and
                # its file name, and comes back when the photo does.
                slot.photo_id = by_path.get(slot.path, 0)
        c = Creation(uuid=u, name=item.get("name") or "",
                     kind=item.get("kind") or "collage", pages=pages,
                     photo_id=by_path.get(item.get("made") or ""),
                     created_at=float(item.get("created_at") or theirs_at),
                     updated_at=theirs_at)
        write(catalog, c, now=theirs_at)
        n += 1
    return n


def merge(mine: dict | None, theirs: dict | None) -> dict:
    """Creations as changed on two computers: the newer wins, one by one."""
    from .sync import merge_list
    return merge_list(mine, theirs, "creations")
