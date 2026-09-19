"""SQLite index over the library.

Design rule (see paths.py): this database is *derived*.  Every column in
it is either read back out of a photo file, or mirrored into a JSON
sidecar under Edits/ and Albums/.  That is what lets a user delete
catalog.db and lose nothing.

Concurrency: the indexer writes from a worker thread while the UI reads
from the main thread, so the database runs in WAL mode and each thread
gets its own connection.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from .video import VIDEO_EXT
from typing import Any, Iterable, Iterator, Sequence
from .i18n import N_, month_year

SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Folders the user has pointed us at.  in_library=1 means the folder is
-- inside our own Originals/ tree (we own those files); 0 means we only
-- reference the photos where they already live and never write there.
CREATE TABLE IF NOT EXISTS roots (
    id          INTEGER PRIMARY KEY,
    path        TEXT NOT NULL UNIQUE,
    in_library  INTEGER NOT NULL DEFAULT 0,
    enabled     INTEGER NOT NULL DEFAULT 1,
    added_at    REAL NOT NULL,
    scanned_at  REAL
);

CREATE TABLE IF NOT EXISTS photos (
    id           INTEGER PRIMARY KEY,
    uuid         TEXT NOT NULL UNIQUE,
    path         TEXT NOT NULL UNIQUE,
    root_id      INTEGER REFERENCES roots(id) ON DELETE SET NULL,
    filename     TEXT NOT NULL,
    ext          TEXT NOT NULL,
    bytes        INTEGER NOT NULL DEFAULT 0,
    mtime        REAL NOT NULL DEFAULT 0,
    -- cheap content fingerprint: size + first/last 64KiB, used to spot
    -- duplicates and moved files without hashing whole RAW files
    fingerprint  TEXT,
    width        INTEGER NOT NULL DEFAULT 0,
    height       INTEGER NOT NULL DEFAULT 0,
    orientation  INTEGER NOT NULL DEFAULT 1,
    taken_at     REAL,              -- EXIF DateTimeOriginal, else mtime
    date_source  TEXT,              -- 'exif' | 'filename' | 'mtime'
    camera_make  TEXT,
    camera_model TEXT,
    lens         TEXT,
    iso          INTEGER,
    f_number     REAL,
    exposure     REAL,
    focal_length REAL,
    gps_lat      REAL,
    gps_lon      REAL,
    rating       INTEGER NOT NULL DEFAULT 0,   -- 0..5
    favorite     INTEGER NOT NULL DEFAULT 0,
    hidden       INTEGER NOT NULL DEFAULT 0,
    trashed_at   REAL,
    added_at     REAL NOT NULL,
    -- mirror of the Edits/ sidecar, for "show only edited" without
    -- stat()ing thousands of files
    edit_version INTEGER NOT NULL DEFAULT 0,
    edited_at    REAL,
    -- 0 unscanned, 1 thumbnails present, 2 unreadable/broken
    thumb_state  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS photos_taken   ON photos(taken_at DESC);
CREATE INDEX IF NOT EXISTS photos_added   ON photos(added_at DESC);
CREATE INDEX IF NOT EXISTS photos_fav     ON photos(favorite) WHERE favorite=1;
CREATE INDEX IF NOT EXISTS photos_trash   ON photos(trashed_at);
CREATE INDEX IF NOT EXISTS photos_fprint  ON photos(fingerprint);
CREATE INDEX IF NOT EXISTS photos_edited  ON photos(edit_version) WHERE edit_version>0;

-- Folders group albums into a tree: a
-- folder can hold albums and other folders, nested arbitrarily.  A NULL
-- parent_id means "at the top level of the sidebar".
CREATE TABLE IF NOT EXISTS folders (
    id         INTEGER PRIMARY KEY,
    uuid       TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    parent_id  INTEGER REFERENCES folders(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    position   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS folders_parent ON folders(parent_id);

CREATE TABLE IF NOT EXISTS albums (
    id         INTEGER PRIMARY KEY,
    uuid       TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    created_at REAL NOT NULL,
    cover_id   INTEGER REFERENCES photos(id) ON DELETE SET NULL,
    sort_key   TEXT NOT NULL DEFAULT 'manual',
    folder_id  INTEGER REFERENCES folders(id) ON DELETE CASCADE,
    position   INTEGER NOT NULL DEFAULT 0
);
-- The index on albums.folder_id is created in _migrate(), not here: on a
-- database that predates folders, this CREATE TABLE IF NOT EXISTS is a
-- no-op (the table already exists without that column), so an index
-- statement referencing it in the same script would fail before
-- _migrate() ever gets a chance to add the column.

-- A Smart Album stores a query, not a list of photos: its contents are
-- whatever currently matches, recomputed every time it is opened. That
-- is the whole point - "everything I marked 5 stars this year" should
-- answer itself as the library changes, without anyone re-filing.
CREATE TABLE IF NOT EXISTS smart_albums (
    id         INTEGER PRIMARY KEY,
    uuid       TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    match_mode TEXT NOT NULL DEFAULT 'all',   -- 'all' | 'any'
    rules      TEXT NOT NULL DEFAULT '[]',    -- JSON list of conditions
    folder_id  INTEGER REFERENCES folders(id) ON DELETE CASCADE,
    position   INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS album_items (
    album_id INTEGER NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (album_id, photo_id)
);
CREATE INDEX IF NOT EXISTS album_items_photo ON album_items(photo_id);

-- Free-text search over the few fields worth searching.  Kept as a
-- plain table rather than FTS5 so the index survives SQLite builds
-- without the FTS extension compiled in.
CREATE TABLE IF NOT EXISTS photo_text (
    photo_id INTEGER PRIMARY KEY REFERENCES photos(id) ON DELETE CASCADE,
    haystack TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS photo_text_hay ON photo_text(haystack);

-- Something made out of photos: a collage, a poster, a calendar. What is
-- kept is the recipe - which photos, where on the page, in what words -
-- and not only the picture it produced, so a creation can be opened and
-- changed again months later, on this computer and on the others.
CREATE TABLE IF NOT EXISTS creations (
    id         INTEGER PRIMARY KEY,
    uuid       TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'collage',
    doc        TEXT NOT NULL DEFAULT '{}',    -- JSON: the pages
    photo_id   INTEGER REFERENCES photos(id) ON DELETE SET NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

-- Photos the user removed from the library whose files are still in a
-- watched folder. Without this list the next scan finds the file again
-- and adds it back as a brand-new photo, as if it had never been removed.
CREATE TABLE IF NOT EXISTS removed (
    path       TEXT PRIMARY KEY,
    removed_at REAL NOT NULL
);
"""

# Columns the UI asks for when filling the grid.  Kept narrow on purpose:
# the grid renders tens of thousands of rows and does not need EXIF.
GRID_FIELDS = ("id", "uuid", "path", "filename", "width", "height",
               "orientation", "taken_at", "favorite", "rating",
               "edit_version", "thumb_state", "bytes", "duration", "has_live",
               "fingerprint", "place_name")
GRID_COLUMNS = ", ".join(GRID_FIELDS)


def natural_key(text: str) -> tuple:
    """Sort names the way people count: "Birthday 2" before "Birthday 10".

    Runs of digits compare as numbers and the rest ignoring case, so a
    folder of "Trip 1" to "Trip 12" reads 1, 2, 3 ... 12 instead of
    1, 10, 11, 12, 2."""
    parts = re.split(r"(\d+)", text or "")
    return tuple((0, int(p), "") if p.isdigit() else (1, 0, p.casefold())
                 for p in parts if p != "")


def search_text(rec: dict) -> str:
    """Flatten one photo record into the text the search box matches.

    Includes several spellings of the capture date (year, month name,
    abbreviation, ISO form) so that "oct", "October 2024" and "2024-10"
    all find the same photos.
    """
    parts = [rec.get("filename") or "",
             Path(rec.get("path") or "").parent.name,
             rec.get("title") or "",
             rec.get("caption") or "",
             rec.get("keywords") or "",
             rec.get("camera_make") or "",
             rec.get("camera_model") or "",
             rec.get("lens") or ""]
    ts = rec.get("taken_at")
    if ts:
        try:
            d = datetime.fromtimestamp(float(ts))
            parts += [d.strftime("%Y"), d.strftime("%B"), d.strftime("%b"),
                      d.strftime("%Y-%m"), d.strftime("%Y-%m-%d"),
                      d.strftime("%A")]
        except (ValueError, OSError, OverflowError):
            pass
    return " ".join(p for p in parts if p).lower()


# ==========================================================================
# Smart album rules
#
# A rule is {"field": ..., "op": ..., "value": ...}.  Fields map to real
# columns, so a Smart Album is compiled into ordinary SQL and costs the
# same as any other view - it is a saved query, not a scan in Python.
# ==========================================================================
SMART_FIELDS = {
    # key            (column,          label,            kind)
    "filename":      ("p.filename",     N_("Filename"),       "text"),
    "folder":        ("p.path",         N_("Folder path"),    "text"),
    "camera_make":   ("p.camera_make",  N_("Camera make"),    "text"),
    "camera_model":  ("p.camera_model", N_("Camera model"),   "text"),
    "lens":          ("p.lens",         N_("Lens"),           "text"),
    "rating":        ("p.rating",       N_("Rating"),         "number"),
    "iso":           ("p.iso",          "ISO",            "number"),
    "f_number":      ("p.f_number",     N_("Aperture (f/)"),  "number"),
    "focal_length":  ("p.focal_length", N_("Focal length"),   "number"),
    "width":         ("p.width",        N_("Width"),          "number"),
    "height":        ("p.height",       N_("Height"),         "number"),
    "bytes":         ("p.bytes",        N_("File size"),      "number"),
    "favorite":      ("p.favorite",     N_("Favourite"),      "bool"),
    "edited":        ("p.edit_version", N_("Edited"),         "bool"),
    "has_location":  ("p.gps_lat",      N_("Has location"),   "bool_null"),
    "taken_at":      ("p.taken_at",     N_("Date taken"),     "date"),
    "ext":           ("p.ext",          N_("File type"),      "text"),
    "media_type":    ("p.ext",          N_("Media type"),     "media"),
    "duration":      ("p.duration",     N_("Video length (seconds)"), "number"),
}

SMART_OPS = {
    "text":      [N_("is"), N_("is not"), N_("contains"), N_("does not contain")],
    "number":    [N_("is"), N_("is not"), N_("greater than"), N_("less than")],
    "bool":      [N_("is true"), N_("is false")],
    "bool_null": [N_("is true"), N_("is false")],
    "date":      [N_("in the last days"), N_("before"), N_("after")],
    "media":     [N_("is video"), N_("is photo")],
}


def compile_rule(rule: dict) -> tuple[str, list]:
    """Turn one rule into an SQL fragment plus its parameters.

    Returns ``("", [])`` for anything unrecognised, so a rule saved by a
    newer version simply does not narrow the result rather than making
    the whole Smart Album fail to open.
    """
    spec = SMART_FIELDS.get(rule.get("field", ""))
    if spec is None:
        return "", []
    col, _label, kind = spec
    op = rule.get("op", "")
    val = rule.get("value")

    if kind == "media":
        # decided by file type, the same test the Videos view uses
        return (VIDEO_SQL if op == "is video" else f"NOT {VIDEO_SQL}"), []

    if kind in ("bool", "bool_null"):
        truthy = op == "is true"
        if kind == "bool_null":
            return (f"{col} IS NOT NULL" if truthy else f"{col} IS NULL"), []
        return (f"COALESCE({col},0) > 0" if truthy
                else f"COALESCE({col},0) = 0"), []

    if kind == "date":
        try:
            if op == "in the last days":
                days = float(val)
                return f"{col} >= ?", [time.time() - days * 86400]
            ts = float(val) if isinstance(val, (int, float)) else \
                datetime.fromisoformat(str(val)).timestamp()
        except (TypeError, ValueError):
            return "", []
        if op == "before":
            return f"{col} < ?", [ts]
        if op == "after":
            return f"{col} > ?", [ts]
        return "", []

    if kind == "number":
        try:
            num = float(val)
        except (TypeError, ValueError):
            return "", []
        return {
            "is":           (f"{col} = ?", [num]),
            "is not":       (f"COALESCE({col},-1) != ?", [num]),
            "greater than": (f"{col} > ?", [num]),
            "less than":    (f"{col} < ?", [num]),
        }.get(op, ("", []))

    text = str(val or "")
    return {
        "is":               (f"LOWER(COALESCE({col},'')) = ?", [text.lower()]),
        "is not":           (f"LOWER(COALESCE({col},'')) != ?", [text.lower()]),
        "contains":         (f"LOWER(COALESCE({col},'')) LIKE ?",
                             [f"%{text.lower()}%"]),
        "does not contain": (f"LOWER(COALESCE({col},'')) NOT LIKE ?",
                             [f"%{text.lower()}%"]),
    }.get(op, ("", []))


def compile_rules(rules: list, match_mode: str = "all") -> tuple[str, list]:
    """Combine rules with AND ("all") or OR ("any")."""
    parts, params = [], []
    for rule in rules or []:
        frag, prm = compile_rule(rule)
        if frag:
            parts.append(f"({frag})")
            params.extend(prm)
    if not parts:
        return "", []
    joiner = " OR " if match_mode == "any" else " AND "
    return "(" + joiner.join(parts) + ")", params


# Media type and utility views. A screenshot is known
# by the name every desktop and phone gives one; a duplicate is a photo
# whose content fingerprint another live photo shares; an import is a file
# Pikalicious copied into the library's own Originals folder.
SCREENSHOT_SQL = ("(lower(p.filename) LIKE 'screenshot%' "
                  "OR lower(p.filename) LIKE 'screen shot%' "
                  "OR lower(p.filename) LIKE 'captura de pantalla%' "
                  "OR lower(p.filename) LIKE 'screencapture%')")
DUPLICATE_SQL = ("p.fingerprint IN (SELECT fingerprint FROM photos "
                 "WHERE trashed_at IS NULL AND fingerprint IS NOT NULL "
                 "GROUP BY fingerprint HAVING COUNT(*) > 1)")
IMPORT_SQL = ("p.root_id IN (SELECT id FROM roots WHERE in_library=1)")
# A video is known by its file type, the same test the scanner uses.
VIDEO_SQL = ("lower(p.ext) IN ("
             + ",".join(f"'{e[1:]}'" for e in sorted(VIDEO_EXT)) + ")")

FILTER_SQL = {
    "favorites": "p.favorite=1",
    "edited": "p.edit_version>0",
    "screenshots": SCREENSHOT_SQL,
    "videos": VIDEO_SQL,
    "photos": f"NOT {VIDEO_SQL}",
    "not_in_album": "NOT EXISTS (SELECT 1 FROM album_items fa WHERE fa.photo_id=p.id)",
    "has_location": "p.gps_lat IS NOT NULL",
}


class Catalog:
    """Thread-affine SQLite wrapper.

    One ``Catalog`` object is safe to share between threads: each thread
    transparently gets its own connection to the same file.
    """

    def __init__(self, db_path: Path | str):
        self.path = Path(db_path)
        self._local = threading.local()
        self._write_lock = threading.Lock()
        self._init_once()

    # -- connection management -------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(
            self.path,
            timeout=15.0,
            isolation_level=None,          # explicit transactions only
            check_same_thread=False,
        )
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=15000")
        # 64 MiB page cache: the grid's date-range queries stay in memory
        # even on a 100k-photo library.
        cur.execute("PRAGMA cache_size=-65536")
        cur.execute("PRAGMA temp_store=MEMORY")
        cur.execute("PRAGMA mmap_size=268435456")
        return con

    @property
    def con(self) -> sqlite3.Connection:
        con = getattr(self._local, "con", None)
        if con is None:
            con = self._local.con = self._connect()
        return con

    def close(self) -> None:
        con = getattr(self._local, "con", None)
        if con is not None:
            con.close()
            self._local.con = None

    def _init_once(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.write() as cur:
            cur.executescript(SCHEMA)
            self._migrate(cur)
            cur.execute(
                "INSERT INTO meta(key,value) VALUES('schema_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )

    def _migrate(self, cur: sqlite3.Cursor) -> None:
        """Add columns a pre-existing database predates.

        CREATE TABLE IF NOT EXISTS only helps a brand-new file - a
        catalog.db from before folders existed already has an ``albums``
        table, just without ``folder_id``/``position``, and no CREATE
        statement touches an existing table's columns.  This runs every
        startup and is a no-op once the columns are already there, which
        is what makes it safe to call unconditionally rather than only
        when the version number changes.
        """
        # Title, caption and keywords, as in the Info panel. Typed
        # by the user; mirrored to photo-state.json by sidecars.py.
        pcols = {row[1] for row in cur.execute("PRAGMA table_info(photos)")}
        for col in ("title", "caption", "keywords"):
            if col not in pcols:
                cur.execute(f"ALTER TABLE photos ADD COLUMN {col} TEXT")
        # 1 when the location was assigned or removed by hand (Adjust
        # Location); a rescan then keeps it instead of re-reading EXIF.
        # Length in seconds; NULL for photos.
        if "duration" not in pcols:
            cur.execute("ALTER TABLE photos ADD COLUMN duration REAL")
        # Live photos: the short video points at its still (paired_to) and
        # the still knows it moves (has_live).
        if "paired_to" not in pcols:
            cur.execute("ALTER TABLE photos ADD COLUMN paired_to INTEGER")
        if "has_live" not in pcols:
            cur.execute("ALTER TABLE photos ADD COLUMN has_live INTEGER "
                        "NOT NULL DEFAULT 0")
        if "gps_manual" not in pcols:
            cur.execute("ALTER TABLE photos ADD COLUMN gps_manual INTEGER "
                        "NOT NULL DEFAULT 0")
        # The name a person gave a group of photos when placing them
        # ("Norwood Center"); an album shows the photos under that name.
        if "place_name" not in pcols:
            cur.execute("ALTER TABLE photos ADD COLUMN place_name TEXT")

        cols = {row[1] for row in cur.execute("PRAGMA table_info(albums)")}
        if "folder_id" not in cols:
            cur.execute(
                "ALTER TABLE albums ADD COLUMN folder_id INTEGER "
                "REFERENCES folders(id) ON DELETE CASCADE")
        if "position" not in cols:
            cur.execute(
                "ALTER TABLE albums ADD COLUMN position INTEGER "
                "NOT NULL DEFAULT 0")
        # Safe to (re)create now: the column above is guaranteed to exist
        # by this point, whether it was just added or was already there.
        cur.execute(
            "CREATE INDEX IF NOT EXISTS albums_folder ON albums(folder_id)")

    # -- transactions ----------------------------------------------------
    class _WriteCtx:
        def __init__(self, cat: "Catalog"):
            self.cat = cat

        def __enter__(self) -> sqlite3.Cursor:
            self.cat._write_lock.acquire()
            self.cur = self.cat.con.cursor()
            self.cur.execute("BEGIN IMMEDIATE")
            return self.cur

        def __exit__(self, exc_type, exc, tb) -> bool:
            try:
                if exc_type is None:
                    self.cat.con.commit()
                else:
                    self.cat.con.rollback()
            finally:
                self.cat._write_lock.release()
            return False

    def write(self) -> "_WriteCtx":
        """``with cat.write() as cur:`` — one serialised write transaction."""
        return Catalog._WriteCtx(self)

    def q(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return self.con.execute(sql, params).fetchall()

    def q1(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
            return self.con.execute(sql, params).fetchone()

    def scalar(self, sql: str, params: Sequence[Any] = (), default=None):
        row = self.q1(sql, params)
        return default if row is None or row[0] is None else row[0]

    # -- roots -----------------------------------------------------------
    def add_root(self, path: Path | str, in_library: bool = False) -> int:
        """Register a watched folder, keeping roots from nesting.

        A folder inside an existing root is already covered: its id is the
        outer root's. A folder that contains existing roots takes them
        over - their photos move to it and the inner rows go - so every
        file belongs to exactly one root and "forget this folder" means
        what it says.
        """
        rp = Path(path).expanduser().resolve()
        p = str(rp)
        for r in self.q("SELECT id, path FROM roots WHERE enabled=1"):
            existing = Path(r["path"])
            if rp != existing and rp.is_relative_to(existing):
                return int(r["id"])
        with self.write() as cur:
            cur.execute(
                "INSERT INTO roots(path,in_library,added_at) VALUES(?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET enabled=1, "
                "in_library=MAX(in_library, excluded.in_library)",
                (p, int(in_library), time.time()),
            )
            new_id = int(cur.execute("SELECT id FROM roots WHERE path=?",
                                     (p,)).fetchone()[0])
            inner = [row[0] for row in cur.execute(
                "SELECT id, path FROM roots WHERE id != ?", (new_id,))
                if Path(row[1]).is_relative_to(rp)]
            for rid in inner:
                cur.execute("UPDATE photos SET root_id=? WHERE root_id=?",
                            (new_id, rid))
                cur.execute("DELETE FROM roots WHERE id=?", (rid,))
        return new_id

    def roots(self, enabled_only: bool = True) -> list[sqlite3.Row]:
        sql = "SELECT * FROM roots"
        if enabled_only:
            sql += " WHERE enabled=1"
        return self.q(sql + " ORDER BY path")

    def forget_root(self, root_id: int, drop_photos: bool = True) -> None:
        with self.write() as cur:
            if drop_photos:
                cur.execute("DELETE FROM photos WHERE root_id=?", (root_id,))
            cur.execute("DELETE FROM roots WHERE id=?", (root_id,))

    def delete_photos(self, ids: Iterable[int]) -> int:
        """Drop catalog rows (never files). Album membership and search
        text go with them through ON DELETE CASCADE."""
        ids = list(ids)
        if not ids:
            return 0
        with self.write() as cur:
            cur.executemany("DELETE FROM photos WHERE id=?",
                            [(i,) for i in ids])
        return len(ids)

    # -- photo upsert ----------------------------------------------------
    def upsert_photos(self, records: Iterable[dict]) -> int:
        """Insert or refresh a batch of scanned photos.

        Deliberately does NOT touch user-owned columns (rating, favorite,
        hidden, album membership) so that a rescan never discards
        something the user set.
        """
        records = list(records)
        if not records:
            return 0
        cols = ("uuid", "path", "root_id", "filename", "ext", "bytes", "mtime",
                "fingerprint", "width", "height", "orientation", "taken_at",
                "date_source", "camera_make", "camera_model", "lens", "iso",
                "f_number", "exposure", "focal_length", "gps_lat", "gps_lon",
                "added_at", "thumb_state", "duration")
        placeholders = ",".join("?" * len(cols))
        def _update(c):
            # A date or location adjusted by hand outranks what the file
            # says; otherwise the next rescan would silently undo it.
            if c in ("taken_at", "date_source"):
                return (f"{c}=CASE WHEN photos.date_source='manual' "
                        f"THEN photos.{c} ELSE excluded.{c} END")
            if c in ("gps_lat", "gps_lon"):
                return (f"{c}=CASE WHEN photos.gps_manual=1 "
                        f"THEN photos.{c} ELSE excluded.{c} END")
            return f"{c}=excluded.{c}"
        updates = ",".join(
            _update(c) for c in cols
            if c not in ("uuid", "path", "added_at")
        )
        sql = (f"INSERT INTO photos({','.join(cols)}) VALUES({placeholders}) "
               f"ON CONFLICT(path) DO UPDATE SET {updates}")
        now = time.time()
        # Columns the schema declares NOT NULL: a scanner that could not
        # read a field must still produce a storable row, so fill these
        # rather than letting the batch fail on one bad file.
        defaults = {"root_id": None, "bytes": 0, "mtime": 0.0, "width": 0,
                    "height": 0, "orientation": 1, "thumb_state": 0}
        rows = []
        for r in records:
            r.setdefault("uuid", uuid.uuid4().hex)
            r.setdefault("added_at", now)
            rows.append(tuple(
                r[c] if r.get(c) is not None else defaults.get(c)
                for c in cols))

        # The haystack is built here rather than in SQL because SQLite's
        # strftime has no month-name specifier, and "october" is exactly
        # the kind of thing people type into a photo search box.
        haystacks = [(search_text(r), r["path"]) for r in records]

        with self.write() as cur:
            cur.executemany(sql, rows)
            # The scanned record knows nothing of the title, caption or
            # keywords the user typed; append them from the row so a
            # rescan does not make them unsearchable.
            cur.executemany(
                "INSERT INTO photo_text(photo_id,haystack) "
                "SELECT id, rtrim(? || ' ' || lower(COALESCE(title,'') || ' ' || "
                "COALESCE(caption,'') || ' ' || COALESCE(keywords,''))) "
                "FROM photos WHERE path=? "
                "ON CONFLICT(photo_id) DO UPDATE SET haystack=excluded.haystack",
                haystacks,
            )
        # A date adjusted by hand is not in the scanned record, so the
        # haystack just written spells the file's date. Rebuild those rows
        # from the catalog so the adjusted date stays searchable.
        paths = [r["path"] for r in records]
        manual = []
        for i in range(0, len(paths), 400):
            part = paths[i:i + 400]
            manual += [r["id"] for r in self.q(
                "SELECT id FROM photos WHERE date_source='manual' AND path IN "
                f"({','.join('?' * len(part))})", part)]
        if manual:
            self.reindex_text(manual)
        return len(rows)

    def set_text_fields(self, photo_ids: Sequence[int], *,
                        title: str | None = None, caption: str | None = None,
                        keywords: str | None = None) -> None:
        """Set the title, caption or keywords of one or more photos.

        Only the fields passed are changed, so a batch edit of the caption
        leaves each photo's own title alone. Empty text clears a field.
        """
        sets, values = [], []
        for col, val in (("title", title), ("caption", caption),
                         ("keywords", keywords)):
            if val is not None:
                sets.append(f"{col}=?")
                values.append(val.strip() or None)
        if not sets or not photo_ids:
            return
        with self.write() as cur:
            cur.executemany(
                f"UPDATE photos SET {', '.join(sets)} WHERE id=?",
                [(*values, pid) for pid in photo_ids])
        self.reindex_text(photo_ids)

    def set_taken_at(self, photo_ids: Sequence[int], ts: float,
                     shift: bool = False) -> None:
        """Image > Adjust Date and Time. With ``shift`` the first photo is
        moved to ``ts`` and the rest by the same amount, keeping the order
        and spacing of a batch."""
        ids = list(photo_ids)
        if not ids:
            return
        with self.write() as cur:
            if shift:
                marks = ",".join("?" * len(ids))
                first = cur.execute(
                    f"SELECT MIN(taken_at) FROM photos WHERE id IN ({marks})",
                    ids).fetchone()[0]
                delta = ts - (first if first is not None else ts)
                cur.execute(
                    f"UPDATE photos SET taken_at=COALESCE(taken_at, ?) + ?, "
                    f"date_source='manual' WHERE id IN ({marks})",
                    [ts, delta, *ids])
            else:
                cur.executemany(
                    "UPDATE photos SET taken_at=?, date_source='manual' WHERE id=?",
                    [(ts, pid) for pid in ids])
        self.reindex_text(ids)

    def set_location(self, photo_ids: Sequence[int],
                     lat: float | None, lon: float | None) -> None:
        """Assign a location, or remove it with ``None`` (Hide Location).
        The photo file is not written; the choice is kept in the catalog
        and in photo-state.json."""
        ids = list(photo_ids)
        if not ids:
            return
        with self.write() as cur:
            cur.executemany(
                "UPDATE photos SET gps_lat=?, gps_lon=?, gps_manual=1 WHERE id=?",
                [(lat, lon, pid) for pid in ids])

    def set_group_name(self, photo_ids: Sequence[int], name: str | None) -> None:
        """Name a group of photos, or take them out of theirs with ``None``.
        Photos with the same name are shown together in their album."""
        ids = list(photo_ids)
        name = " ".join((name or "").split()) or None
        if not ids:
            return
        with self.write() as cur:
            cur.executemany("UPDATE photos SET place_name=? WHERE id=?",
                            [(name, pid) for pid in ids])

    def album_has_groups(self, album_id: int) -> bool:
        """Whether any photo of the album has been given a group name."""
        return self.q1(
            "SELECT 1 FROM album_items ai JOIN photos p ON p.id=ai.photo_id "
            "WHERE ai.album_id=? AND p.place_name IS NOT NULL AND p.trashed_at IS NULL "
            "LIMIT 1", (album_id,)) is not None

    def reindex_text(self, photo_ids: Sequence[int] | None = None) -> int:
        """Rebuild the search text from the rows as they are now."""
        if photo_ids is None:
            rows = self.q("SELECT * FROM photos")
        else:
            ids = list(photo_ids)
            if not ids:
                return 0
            marks = ",".join("?" * len(ids))
            rows = self.q(f"SELECT * FROM photos WHERE id IN ({marks})", ids)
        pairs = [(search_text(dict(r)), r["id"]) for r in rows]
        with self.write() as cur:
            cur.executemany(
                "INSERT INTO photo_text(photo_id,haystack) VALUES(?,?) "
                "ON CONFLICT(photo_id) DO UPDATE SET haystack=excluded.haystack",
                [(pid, hay) for hay, pid in pairs])
        return len(pairs)

    def albums_for_photo(self, photo_id: int) -> list[sqlite3.Row]:
        """The albums a photo is in, by name - the Info window lists them."""
        return self.q(
            "SELECT a.id, a.name FROM albums a "
            "JOIN album_items ai ON ai.album_id=a.id "
            "WHERE ai.photo_id=? ORDER BY a.name COLLATE NOCASE", (photo_id,))

    def set_thumb_state(self, photo_id: int, state: int) -> None:
        with self.write() as cur:
            cur.execute("UPDATE photos SET thumb_state=? WHERE id=?",
                        (state, photo_id))

    def paths_missing_thumbs(self, limit: int = 512) -> list[sqlite3.Row]:
        return self.q(
            "SELECT id, path, orientation FROM photos "
            "WHERE thumb_state=0 AND trashed_at IS NULL "
            "ORDER BY taken_at DESC LIMIT ?", (limit,))

    # -- user state ------------------------------------------------------
    def set_favorite(self, photo_ids: Sequence[int], value: bool) -> None:
        with self.write() as cur:
            cur.executemany("UPDATE photos SET favorite=? WHERE id=?",
                            [(int(value), i) for i in photo_ids])

    def set_rating(self, photo_ids: Sequence[int], stars: int) -> None:
        stars = max(0, min(5, int(stars)))
        with self.write() as cur:
            cur.executemany("UPDATE photos SET rating=? WHERE id=?",
                            [(stars, i) for i in photo_ids])

    def set_hidden(self, photo_ids: Sequence[int], value: bool) -> None:
        with self.write() as cur:
            cur.executemany("UPDATE photos SET hidden=? WHERE id=?",
                            [(int(value), i) for i in photo_ids])

    def trash(self, photo_ids: Sequence[int]) -> None:
        now = time.time()
        with self.write() as cur:
            cur.executemany("UPDATE photos SET trashed_at=? WHERE id=?",
                            [(now, i) for i in photo_ids])

    def forget_expired_trash(self, days: int = 30) -> int:
        """Remove photos that have sat in Recently Deleted longer than
        ``days``. Files on disk are not touched - Piklin indexes photos
        where they live and only erases a file when asked to."""
        cutoff = time.time() - days * 86400
        ids = [r["id"] for r in self.q(
            "SELECT id FROM photos WHERE trashed_at IS NOT NULL "
            "AND trashed_at < ?", (cutoff,))]
        return self.forget_photos(ids)

    # -- removed from the library ------------------------------------------
    def forget_photos(self, ids: Iterable[int]) -> int:
        """Remove photos from the library and keep them out: their paths
        are remembered so a scan does not add the files back."""
        ids = list(ids)
        if not ids:
            return 0
        now = time.time()
        with self.write() as cur:
            for i in ids:
                cur.execute("INSERT OR REPLACE INTO removed(path, removed_at) "
                            "SELECT path, ? FROM photos WHERE id=?", (now, i))
                cur.execute("DELETE FROM photos WHERE id=?", (i,))
        return len(ids)

    def removed_paths(self) -> list[str]:
        return [r[0] for r in self.q("SELECT path FROM removed ORDER BY path")]

    def set_removed(self, entries: Iterable[tuple[str, float]]) -> None:
        """Restore the list from removed-photos.json (rebuild)."""
        with self.write() as cur:
            cur.executemany("INSERT OR REPLACE INTO removed(path, removed_at) "
                            "VALUES(?, ?)", list(entries))

    def restore_removed(self) -> int:
        """Let every removed photo come back on the next scan."""
        n = int(self.scalar("SELECT COUNT(*) FROM removed", (), 0))
        with self.write() as cur:
            cur.execute("DELETE FROM removed")
        return n

    def untrash(self, photo_ids: Sequence[int]) -> None:
        with self.write() as cur:
            cur.executemany("UPDATE photos SET trashed_at=NULL WHERE id=?",
                            [(i,) for i in photo_ids])

    def note_edit(self, photo_id: int, version: int) -> None:
        with self.write() as cur:
            cur.execute(
                "UPDATE photos SET edit_version=?, edited_at=? WHERE id=?",
                (version, time.time(), photo_id))

    # -- live photos -----------------------------------------------------
    LIVE_STILLS = ("heic", "heif", "jpg", "jpeg")

    def pair_live_photos(self) -> int:
        """Join each short video to the photo of the same name beside it.

        A phone's live photo arrives as IMG_1234.HEIC plus IMG_1234.MOV of
        about three seconds; shown as two items it is clutter.
        """
        videos = self.q(
            f"SELECT id, path FROM photos p WHERE {VIDEO_SQL} "
            "AND p.paired_to IS NULL AND p.duration IS NOT NULL "
            "AND p.duration <= 4.5")
        pairs = []
        for v in videos:
            base = os.path.splitext(v["path"])[0]
            names = [f"{base}.{e}" for e in self.LIVE_STILLS]
            names += [f"{base}.{e.upper()}" for e in self.LIVE_STILLS]
            still = self.q1(
                f"SELECT id FROM photos WHERE path IN ({','.join('?' * len(names))})",
                names)
            if still is not None:
                pairs.append((still["id"], v["id"]))
        if pairs:
            with self.write() as cur:
                for still_id, video_id in pairs:
                    cur.execute("UPDATE photos SET paired_to=? WHERE id=?",
                                (still_id, video_id))
                    cur.execute("UPDATE photos SET has_live=1 WHERE id=?",
                                (still_id,))
        return len(pairs)

    def live_video_for(self, photo_id: int) -> sqlite3.Row | None:
        return self.q1("SELECT id, path, duration FROM photos "
                       "WHERE paired_to=? LIMIT 1", (photo_id,))

    # -- queries ---------------------------------------------------------
    def photo(self, photo_id: int) -> sqlite3.Row | None:
        return self.q1("SELECT * FROM photos WHERE id=?", (photo_id,))

    def photo_by_path(self, path: str) -> sqlite3.Row | None:
        return self.q1("SELECT * FROM photos WHERE path=?", (str(path),))

    def browse(
        self,
        *,
        scope: str = "library",
        album_id: int | None = None,
        smart_id: int | None = None,
        search: str | None = None,
        order: str = "taken_desc",
        filters: Iterable[str] | None = None,
        photo_ids: Sequence[int] | None = None,
        by_group: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[sqlite3.Row]:
        """The one query the grid uses, for every view in the sidebar."""
        # The moving half of a live photo belongs to its still, never alone.
        where = ["p.paired_to IS NULL"]
        params: list[Any] = []
        joins = ""

        if scope == "trash":
            where.append("p.trashed_at IS NOT NULL")
        else:
            where.append("p.trashed_at IS NULL")
            if scope != "hidden":
                where.append("p.hidden=0")

        if scope == "favorites":
            where.append("p.favorite=1")
        elif scope == "edited":
            where.append("p.edit_version>0")
        elif scope == "hidden":
            where.append("p.hidden=1")
        elif scope == "screenshots":
            where.append(SCREENSHOT_SQL)
        elif scope == "videos":
            where.append(VIDEO_SQL)
        elif scope == "duplicates":
            where.append(DUPLICATE_SQL)
        elif scope == "imports":
            where.append(IMPORT_SQL)
        elif scope == "creations":
            # The pictures Piklin's own Create made, newest first.
            where.append("p.id IN (SELECT photo_id FROM creations "
                         "WHERE photo_id IS NOT NULL)")
        elif scope == "ids":
            # A named set of photos: what a place on the map stands for.
            chosen = list(photo_ids or ())
            where.append(f"p.id IN ({','.join('?' * len(chosen))})" if chosen else "0")
            params.extend(chosen)
        elif scope == "album" and album_id is not None:
            joins += " JOIN album_items ai ON ai.photo_id=p.id AND ai.album_id=?"
            params.append(album_id)
        elif scope == "smart" and smart_id is not None:
            row = self.q1("SELECT rules, match_mode FROM smart_albums WHERE id=?",
                          (smart_id,))
            if row is not None:
                try:
                    rules = json.loads(row["rules"])
                except ValueError:
                    rules = []
                frag, prm = compile_rules(rules, row["match_mode"])
                if frag:
                    where.append(frag)
                    params.extend(prm)

        # The toolbar's Filter menu: choosing several shows
        # the photos that are any of them (Favourites or Edited), inside
        # whatever view is open.
        wanted = [FILTER_SQL[f] for f in (filters or ()) if f in FILTER_SQL]
        if wanted:
            where.append("(" + " OR ".join(wanted) + ")")

        if search:
            joins += " JOIN photo_text t ON t.photo_id=p.id"
            for term in search.lower().split():
                where.append("t.haystack LIKE ?")
                params.append(f"%{term}%")

        orders = {
            "taken_desc": "p.taken_at DESC, p.id DESC",
            "taken_asc": "p.taken_at ASC, p.id ASC",
            "added_desc": "p.added_at DESC, p.id DESC",
            "name_asc": "p.filename COLLATE NOCASE ASC",
            "size_desc": "p.bytes DESC",
            "rating_desc": "p.rating DESC, p.taken_at DESC",
            "manual": "ai.position ASC" if scope == "album" else "p.taken_at DESC",
        }
        select = ", ".join(f"p.{c}" for c in GRID_FIELDS)
        ordering = self._scope_order(scope, order, orders)
        if scope == "album" and album_id is not None and by_group:
            # Named groups side by side, in the order they were visited
            # (the date of each group's earliest photo), the photos with no
            # group last; inside a group, the album's usual order.
            ordering = ("p.place_name IS NULL, "
                        "MIN(p.taken_at) OVER (PARTITION BY p.place_name), "
                        "p.place_name, " + ordering)
        sql = (f"SELECT {select} "
               f"FROM photos p{joins} WHERE {' AND '.join(where)} "
               f"ORDER BY {ordering}")
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params += [limit, offset]
        return self.q(sql, params)

    def summary(self, mode: str = "year", filters=None, per_group: int = 4
                ) -> list[dict]:
        """Years and Months: one card per period.

        Each group carries its label, how many photos it holds, its time
        span, and up to ``per_group`` key photos - favourites first, then
        the most recent - to put on the card.
        """
        fmt = "%Y" if mode == "year" else "%Y-%m"
        where = ["p.trashed_at IS NULL", "p.hidden=0", "p.taken_at IS NOT NULL",
                 "p.paired_to IS NULL"]
        wanted = [FILTER_SQL[f] for f in (filters or ()) if f in FILTER_SQL]
        if wanted:
            where.append("(" + " OR ".join(wanted) + ")")
        cond = " AND ".join(where)
        groups = self.q(f"""
            SELECT strftime('{fmt}', p.taken_at, 'unixepoch', 'localtime') AS k,
                   COUNT(*) AS n, MIN(p.taken_at) AS first, MAX(p.taken_at) AS last
            FROM photos p WHERE {cond}
            GROUP BY k ORDER BY k DESC""")
        keys = self.q(f"""
            SELECT k, id, path FROM (
              SELECT strftime('{fmt}', p.taken_at, 'unixepoch', 'localtime') AS k,
                     p.id, p.path,
                     ROW_NUMBER() OVER (
                       PARTITION BY strftime('{fmt}', p.taken_at, 'unixepoch', 'localtime')
                       ORDER BY p.favorite DESC, p.taken_at DESC) AS rn
              FROM photos p WHERE {cond})
            WHERE rn <= ? ORDER BY k DESC, rn""", (per_group,))
        by_key: dict[str, list] = {}
        for r in keys:
            by_key.setdefault(r["k"], []).append({"id": r["id"], "path": r["path"]})
        out = []
        for g in groups:
            if mode == "year":
                label = g["k"]
            else:
                label = month_year(datetime.strptime(g["k"], "%Y-%m"))
            out.append({"key": g["k"], "label": label, "count": int(g["n"]),
                        "first": g["first"], "last": g["last"],
                        "photos": by_key.get(g["k"], [])})
        return out

    @staticmethod
    def _scope_order(scope, order, orders):
        # Some views have one natural order, whatever the sort menu says:
        # copies side by side, the latest edit or import first.
        if scope == "duplicates":
            return "p.fingerprint, p.added_at ASC, p.id ASC"
        if scope == "edited" and order == "taken_desc":
            return "p.edited_at DESC, p.id DESC"
        if scope == "imports" and order == "taken_desc":
            return "p.added_at DESC, p.id DESC"
        return orders.get(order, orders["taken_desc"])

    def counts(self) -> dict[str, int]:
        extra = self.q1(f"""
            SELECT
              SUM(p.trashed_at IS NULL AND p.hidden=0 AND {SCREENSHOT_SQL}) AS screenshots,
              SUM(p.trashed_at IS NULL AND p.hidden=0 AND {DUPLICATE_SQL})  AS duplicates,
              SUM(p.trashed_at IS NULL AND p.hidden=0 AND {IMPORT_SQL})     AS imports,
              SUM(p.trashed_at IS NULL AND p.hidden=0 AND {VIDEO_SQL})     AS videos
            FROM photos p WHERE p.paired_to IS NULL""")
        row = self.q1("""
            SELECT
              SUM(trashed_at IS NULL AND hidden=0)                AS library,
              SUM(trashed_at IS NULL AND hidden=0 AND favorite=1) AS favorites,
              SUM(trashed_at IS NULL AND hidden=0 AND gps_lat IS NOT NULL
                  AND NOT (gps_lat=0 AND gps_lon=0))                AS located,
              SUM(trashed_at IS NULL AND edit_version>0)          AS edited,
              SUM(trashed_at IS NULL AND hidden=1)                AS hidden,
              SUM(trashed_at IS NOT NULL)                         AS trash,
              SUM(trashed_at IS NULL AND hidden=0) FILTER (WHERE 1) AS total,
              (SELECT COALESCE(SUM(bytes),0) FROM photos)         AS bytes
            FROM photos WHERE paired_to IS NULL""")
        out = {k: int(row[k] or 0) for k in row.keys()}
        out.update({k: int(extra[k] or 0) for k in extra.keys()})
        out["creations"] = int(self.scalar(
            "SELECT COUNT(*) FROM creations WHERE photo_id IS NOT NULL", default=0) or 0)
        return out

    def date_buckets(self, scope: str = "library") -> list[sqlite3.Row]:
        """Photo counts per month, for the timeline scrubber."""
        return self.q("""
            SELECT strftime('%Y-%m', taken_at, 'unixepoch') AS ym,
                   COUNT(*) AS n
            FROM photos
            WHERE trashed_at IS NULL AND hidden=0 AND taken_at IS NOT NULL
            GROUP BY ym ORDER BY ym DESC""")

    # -- folders -----------------------------------------------------------
    def create_folder(self, name: str, parent_id: int | None = None,
                      folder_uuid: str | None = None) -> int:
        pos = int(self.scalar(
            "SELECT COALESCE(MAX(position),-1) FROM folders "
            "WHERE parent_id IS ?", (parent_id,), -1)) + 1
        with self.write() as cur:
            cur.execute(
                "INSERT INTO folders(uuid,name,parent_id,created_at,position) "
                "VALUES(?,?,?,?,?)",
                (folder_uuid or uuid.uuid4().hex, name, parent_id,
                 time.time(), pos))
            return int(cur.lastrowid)

    def folders(self) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM folders ORDER BY parent_id, position, "
                      "name COLLATE NOCASE")

    def rename_folder(self, folder_id: int, name: str) -> None:
        with self.write() as cur:
            cur.execute("UPDATE folders SET name=? WHERE id=?",
                       (name, folder_id))

    def delete_folder(self, folder_id: int) -> None:
        """Delete a folder and everything inside it.

        Both ``folders.parent_id`` and ``albums.folder_id`` cascade, so
        one DELETE removes the whole subtree - nested folders, their
        albums, and (via album_items' own cascade) the albums'
        memberships.  The photos themselves are never touched; only the
        organisation is removed.
        """
        with self.write() as cur:
            cur.execute("DELETE FROM folders WHERE id=?", (folder_id,))

    def folder_and_inside(self, folder_id: int) -> set[int]:
        """The folder and every folder inside it, however deep."""
        found, todo = {folder_id}, [folder_id]
        while todo:
            for r in self.q("SELECT id FROM folders WHERE parent_id=?", (todo.pop(),)):
                if r["id"] not in found:
                    found.add(r["id"])
                    todo.append(r["id"])
        return found

    def move_folder(self, folder_id: int, new_parent_id: int | None) -> bool:
        """Move a folder into another one, or to the top with None.

        A folder can't go inside itself or inside a folder it holds - that
        would cut the whole branch off the tree - so that move is refused
        and returns False.
        """
        if new_parent_id is not None and new_parent_id in self.folder_and_inside(folder_id):
            return False
        pos = int(self.scalar(
            "SELECT COALESCE(MAX(position),-1) FROM folders "
            "WHERE parent_id IS ?", (new_parent_id,), -1)) + 1
        with self.write() as cur:
            cur.execute(
                "UPDATE folders SET parent_id=?, position=? WHERE id=?",
                (new_parent_id, pos, folder_id))
        return True

    def move_smart_album_to_folder(self, smart_id: int,
                                   folder_id: int | None) -> None:
        pos = int(self.scalar(
            "SELECT COALESCE(MAX(position),-1) FROM smart_albums "
            "WHERE folder_id IS ?", (folder_id,), -1)) + 1
        with self.write() as cur:
            cur.execute(
                "UPDATE smart_albums SET folder_id=?, position=? WHERE id=?",
                (folder_id, pos, smart_id))

    def move_album_to_folder(self, album_id: int,
                             folder_id: int | None) -> None:
        pos = int(self.scalar(
            "SELECT COALESCE(MAX(position),-1) FROM albums "
            "WHERE folder_id IS ?", (folder_id,), -1)) + 1
        with self.write() as cur:
            cur.execute(
                "UPDATE albums SET folder_id=?, position=? WHERE id=?",
                (folder_id, pos, album_id))

    def tree(self, sort: str = "az") -> list[dict]:
        """Folders and albums assembled into a nested structure.

        Each node is ``{"kind": "folder"|"album", "row": <sqlite3.Row>,
        "children": [...]}``.  Folders and albums share one A to Z order within the same
        parent, the usual order for a folder's contents. ``sort`` is "az",
        "za", or "count_desc" / "count_asc" by how many photos each holds
        (a folder counts everything inside it).
        """
        folders = self.folders()
        albums = self.albums()
        by_parent: dict[int | None, list[dict]] = {}
        for f in folders:
            by_parent.setdefault(f["parent_id"], []).append(
                {"kind": "folder", "row": f, "children": []})
        for a in albums:
            by_parent.setdefault(a["folder_id"], []).append(
                {"kind": "album", "row": a, "children": []})
        # Smart Albums list with the other albums; they were
        # stored and queryable but never reached the sidebar.
        for sa in self.smart_albums():
            by_parent.setdefault(sa["folder_id"], []).append(
                {"kind": "smart", "row": sa, "children": []})

        def photos_in(node) -> int:
            if node["kind"] == "folder":
                return sum(photos_in(c) for c in node["children"])
            row = node["row"]
            return int(row["n"]) if "n" in row.keys() and row["n"] else 0

        def build(parent_id):
            items = by_parent.get(parent_id, [])
            for n in items:
                if n["kind"] == "folder":
                    n["children"] = build(n["row"]["id"])
            # One list, folders and albums together: putting every folder
            # first read as out of order ("Paternos" above "Maternos").
            items.sort(key=lambda n: natural_key(n["row"]["name"]),
                       reverse=(sort == "za"))
            if sort == "count_desc":
                items.sort(key=photos_in, reverse=True)      # stable: A to Z on ties
            elif sort == "count_asc":
                items.sort(key=photos_in)
            return items
        return build(None)

    # -- albums ----------------------------------------------------------
    def create_album(self, name: str, album_uuid: str | None = None,
                     folder_id: int | None = None) -> int:
        pos = int(self.scalar(
            "SELECT COALESCE(MAX(position),-1) FROM albums "
            "WHERE folder_id IS ?", (folder_id,), -1)) + 1
        with self.write() as cur:
            cur.execute(
                "INSERT INTO albums(uuid,name,created_at,folder_id,position) "
                "VALUES(?,?,?,?,?)",
                (album_uuid or uuid.uuid4().hex, name, time.time(),
                 folder_id, pos))
            return int(cur.lastrowid)

    def albums(self) -> list[sqlite3.Row]:
        # An album's count and cover are the photos it shows (see photos():
        # not in Recently Deleted, not hidden, not the moving half of a live
        # photo). Counting every row it had kept a deleted photo in the
        # number, so deleting from an album never brought the count down.
        return self.q("""
            SELECT a.*, COUNT(p.id) AS n,
                   -- how many of them are on the map, so an album can show
                   -- at a glance whether its place has been said yet
                   SUM(CASE WHEN p.gps_lat IS NOT NULL
                             AND NOT (p.gps_lat = 0 AND p.gps_lon = 0)
                            THEN 1 ELSE 0 END) AS placed,
                   -- and how far apart those places are, so an album that
                   -- is placed but still scattered can be told from one
                   -- that is settled. The spread of the coordinates says
                   -- it without the cost of counting distinct ones.
                   MAX(p.gps_lat) - MIN(p.gps_lat) AS lat_spread,
                   MAX(p.gps_lon) - MIN(p.gps_lon) AS lon_spread,
                   COALESCE(
                     -- the cover chosen with Make Album Cover, while it is still in the album
                     (SELECT c.path FROM album_items x JOIN photos c ON c.id=x.photo_id
                       WHERE x.album_id=a.id AND x.photo_id=a.cover_id
                         AND c.paired_to IS NULL AND c.trashed_at IS NULL AND c.hidden=0),
                     (SELECT c.path FROM album_items x JOIN photos c ON c.id=x.photo_id
                       WHERE x.album_id=a.id
                         AND c.paired_to IS NULL AND c.trashed_at IS NULL AND c.hidden=0
                       ORDER BY x.position LIMIT 1)) AS cover_path
            FROM albums a
            LEFT JOIN album_items ai ON ai.album_id=a.id
            LEFT JOIN photos p ON p.id=ai.photo_id AND p.paired_to IS NULL
                              AND p.trashed_at IS NULL AND p.hidden=0
            GROUP BY a.id ORDER BY a.folder_id, a.position, a.name COLLATE NOCASE""")

    def album_add(self, album_id: int, photo_ids: Sequence[int]) -> None:
        # Only photos the library has: one without a row - still on a camera
        # or phone (a negative id), or removed meanwhile - failed the whole
        # add on the foreign key, and nothing went into the album.
        wanted = [int(p) for p in photo_ids if int(p) > 0]
        known: set[int] = set()
        for i in range(0, len(wanted), 400):
            part = wanted[i:i + 400]
            known.update(r["id"] for r in self.q(
                f"SELECT id FROM photos WHERE id IN ({','.join('?' * len(part))})", part))
        photo_ids = [p for p in wanted if p in known]
        if not photo_ids:
            return
        base = int(self.scalar(
            "SELECT COALESCE(MAX(position),-1) FROM album_items WHERE album_id=?",
            (album_id,), -1)) + 1
        with self.write() as cur:
            cur.executemany(
                "INSERT INTO album_items(album_id,photo_id,position) VALUES(?,?,?) "
                "ON CONFLICT DO NOTHING",
                [(album_id, pid, base + i) for i, pid in enumerate(photo_ids)])

    def album_remove(self, album_id: int, photo_ids: Sequence[int]) -> None:
        with self.write() as cur:
            cur.executemany(
                "DELETE FROM album_items WHERE album_id=? AND photo_id=?",
                [(album_id, pid) for pid in photo_ids])

    def set_album_cover(self, album_id: int, photo_id: int | None) -> None:
        """The photo shown for the album; None goes back to its first photo."""
        with self.write() as cur:
            cur.execute("UPDATE albums SET cover_id=? WHERE id=?", (photo_id, album_id))

    def rename_album(self, album_id: int, name: str) -> None:
        with self.write() as cur:
            cur.execute("UPDATE albums SET name=? WHERE id=?", (name, album_id))

    def delete_album(self, album_id: int) -> None:
        with self.write() as cur:
            cur.execute("DELETE FROM albums WHERE id=?", (album_id,))

    def album_photo_paths(self, album_id: int) -> list[str]:
        return [r[0] for r in self.q(
            "SELECT p.path FROM album_items ai JOIN photos p ON p.id=ai.photo_id "
            "WHERE ai.album_id=? ORDER BY ai.position", (album_id,))]

    # -- smart albums ------------------------------------------------------
    def create_smart_album(self, name: str, rules: list,
                           match_mode: str = "all",
                           folder_id: int | None = None,
                           album_uuid: str | None = None) -> int:
        with self.write() as cur:
            cur.execute(
                "INSERT INTO smart_albums(uuid,name,match_mode,rules,"
                "folder_id,created_at) VALUES(?,?,?,?,?,?)",
                (album_uuid or uuid.uuid4().hex, name, match_mode,
                 json.dumps(rules), folder_id, time.time()))
            return int(cur.lastrowid)

    def smart_albums(self) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM smart_albums "
                      "ORDER BY folder_id, position, name COLLATE NOCASE")

    def update_smart_album(self, smart_id: int, name: str, rules: list,
                           match_mode: str) -> None:
        with self.write() as cur:
            cur.execute(
                "UPDATE smart_albums SET name=?, rules=?, match_mode=? "
                "WHERE id=?",
                (name, json.dumps(rules), match_mode, smart_id))

    def delete_smart_album(self, smart_id: int) -> None:
        with self.write() as cur:
            cur.execute("DELETE FROM smart_albums WHERE id=?", (smart_id,))

    def smart_album_count(self, smart_id: int) -> int:
        return len(self.browse(scope="smart", smart_id=smart_id))

    # -- duplicates ------------------------------------------------------
    def merge_duplicates(self, photo_ids: Sequence[int]) -> dict:
        """"Merge N Items": keep one copy of each selected group.

        Within every set of selected photos that share a fingerprint, the
        copy kept is the one the user has invested in - favourite, edited,
        in albums - and otherwise the largest file, then the earliest
        added. The others move to Recently Deleted (never erased), and
        whatever they carried is handed to the keeper first: album
        membership, favourite, rating.
        """
        ids = list(photo_ids)
        if not ids:
            return {"kept": [], "trashed": []}
        marks = ",".join("?" * len(ids))
        rows = self.q(
            f"SELECT p.id, p.fingerprint, p.favorite, p.rating, p.bytes, "
            f"p.added_at, p.edit_version, "
            f"(SELECT COUNT(*) FROM album_items ai WHERE ai.photo_id=p.id) AS n_albums "
            f"FROM photos p WHERE p.id IN ({marks}) AND p.trashed_at IS NULL "
            f"AND p.fingerprint IS NOT NULL", ids)
        groups: dict[str, list] = {}
        for r in rows:
            groups.setdefault(r["fingerprint"], []).append(r)
        kept, trashed = [], []
        with self.write() as cur:
            for group in groups.values():
                if len(group) < 2:
                    continue
                group.sort(key=lambda r: (-(r["favorite"] or 0),
                                          -(r["edit_version"] > 0),
                                          -r["n_albums"], -r["bytes"],
                                          r["added_at"], r["id"]))
                keeper, others = group[0], group[1:]
                other_ids = [o["id"] for o in others]
                omarks = ",".join("?" * len(other_ids))
                cur.execute(
                    f"INSERT OR IGNORE INTO album_items(album_id, photo_id, position) "
                    f"SELECT album_id, ?, position FROM album_items "
                    f"WHERE photo_id IN ({omarks})", [keeper["id"], *other_ids])
                if any(o["favorite"] for o in others):
                    cur.execute("UPDATE photos SET favorite=1 WHERE id=?",
                                (keeper["id"],))
                best_rating = max(r["rating"] or 0 for r in group)
                cur.execute("UPDATE photos SET rating=? WHERE id=?",
                            (best_rating, keeper["id"]))
                cur.execute(
                    f"UPDATE photos SET trashed_at=? WHERE id IN ({omarks})",
                    [time.time(), *other_ids])
                kept.append(keeper["id"])
                trashed.extend(other_ids)
        return {"kept": kept, "trashed": trashed}

    def duplicate_groups(self) -> list[list[sqlite3.Row]]:
        rows = self.q("""
            SELECT id, path, filename, bytes, fingerprint, taken_at
            FROM photos
            WHERE fingerprint IS NOT NULL AND trashed_at IS NULL
              AND fingerprint IN (
                SELECT fingerprint FROM photos
                WHERE fingerprint IS NOT NULL AND trashed_at IS NULL
                GROUP BY fingerprint HAVING COUNT(*)>1)
            ORDER BY fingerprint, added_at""")
        groups: dict[str, list] = {}
        for r in rows:
            groups.setdefault(r["fingerprint"], []).append(r)
        return list(groups.values())
