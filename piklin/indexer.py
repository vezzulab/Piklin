"""Library scanning.

Runs entirely off the UI thread and reports progress through a callback.
Three properties matter more than raw speed:

*Incremental.*  A rescan only re-reads files whose size or mtime changed.
Re-probing 60,000 unchanged photos on every launch would make the app
feel broken.

*Non-destructive.*  Scanning never writes to a photo and never discards
user state.  A file that disappeared is marked missing, not deleted, and
a file that moved is recognised by content and re-pointed rather than
re-imported as a new photo that has lost its rating and album membership.

*Cancellable.*  Every loop checks a cancel flag, so closing the window
does not leave a worker chewing through a network mount.
"""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator

from . import imageio as iio
from .catalog import Catalog
from .thumbs import GRID_SIZE, ThumbCache

# Directories that never contain photos worth indexing, and that are
# expensive or confusing to walk into.
SKIP_DIRS = {
    ".git", ".svn", ".hg", "node_modules", "__pycache__", ".cache",
    ".thumbnails", ".Trash", ".Trash-1000", "lost+found", ".stversions",
    ".stfolder", "@eaDir",          # Synology
    ".@__thumb", "@Recycle",        # QNAP
}


@dataclass
class Progress:
    phase: str = "idle"            # scanning | probing | thumbnails | done
    total: int = 0
    done: int = 0
    current: str = ""
    added: int = 0
    updated: int = 0
    missing: int = 0
    moved: int = 0
    errors: int = 0
    started_at: float = field(default_factory=time.time)

    @property
    def fraction(self) -> float:
        return (self.done / self.total) if self.total else 0.0

    @property
    def elapsed(self) -> float:
        return time.time() - self.started_at


class Indexer:
    def __init__(self, catalog: Catalog, thumbs: ThumbCache | None = None,
                 workers: int = 0, follow_symlinks: bool = False,
                 library_root: Path | str | None = None):
        self.catalog = catalog
        self.thumbs = thumbs
        from . import system
        self.workers = workers or system.work_budget("probe")
        self.follow_symlinks = follow_symlinks
        # The library lives at ~/Pictures/Piklin by default - that
        # is *inside* ~/Pictures, which is also the folder the first-run
        # button offers to scan. Without this exclusion the scanner walks
        # into the library's own Exports/ and Originals/ and indexes them
        # as if they were the user's photos: every photo you export comes
        # back as a new library entry on the next scan, and the duplicate
        # finder then reports your own exports as duplicates of the
        # originals they came from.
        self.library_root = (Path(library_root).expanduser().resolve()
                             if library_root else None)
        self.cancel = threading.Event()
        self.progress = Progress()
        self._thread: threading.Thread | None = None

    # -- walking ---------------------------------------------------------
    def _walk(self, root: Path) -> Iterator[tuple[Path, os.stat_result]]:
        exts = iio.supported_extensions()
        seen_dirs: set[tuple[int, int]] = set()
        for dirpath, dirnames, filenames in os.walk(
                root, followlinks=self.follow_symlinks):
            if self.cancel.is_set():
                return
            # Other Piklin libraries (*.piklin) are never photos of this one.
            dirnames[:] = [d for d in dirnames
                           if d not in SKIP_DIRS and not d.startswith(".")
                           and not d.endswith(".piklin")]
            if self.library_root is not None:
                # Prune the library's own directory wherever it turns up,
                # rather than matching on its name: the user can put the
                # library anywhere, and a folder that merely happens to
                # be called "Piklin" is still their photos.
                keep = []
                for d in dirnames:
                    try:
                        if (Path(dirpath) / d).resolve() != self.library_root:
                            keep.append(d)
                    except OSError:
                        keep.append(d)
                dirnames[:] = keep
            # Guard against symlink loops when the user opted into them.
            if self.follow_symlinks:
                try:
                    st = os.stat(dirpath)
                    ident = (st.st_dev, st.st_ino)
                    if ident in seen_dirs:
                        dirnames[:] = []
                        continue
                    seen_dirs.add(ident)
                except OSError:
                    continue
            for name in filenames:
                if name.startswith("."):
                    continue
                if os.path.splitext(name)[1].lower() not in exts:
                    continue
                p = Path(dirpath) / name
                try:
                    yield p, p.stat()
                except OSError:
                    continue

    # -- main pass -------------------------------------------------------
    def scan(self, roots: Iterable[Path | str] | None = None,
             on_progress: Callable[[Progress], None] | None = None,
             batch_size: int = 256) -> Progress:
        """Scan roots and bring the catalog up to date."""
        self.cancel.clear()
        p = self.progress = Progress(phase="scanning")

        def report():
            if on_progress:
                try:
                    on_progress(p)
                except Exception:
                    pass

        if roots is None:
            rows = self.catalog.roots()
            root_ids = {Path(r["path"]): r["id"] for r in rows}
        else:
            root_ids = {}
            for r in roots:
                rp = Path(r).expanduser().resolve()
                # The library's own Originals folder is where imports land;
                # marking it lets the Imports view tell them apart.
                inside = bool(self.library_root) and rp.is_relative_to(
                    Path(self.library_root).expanduser().resolve())
                root_ids[rp] = self.catalog.add_root(rp, in_library=inside)
        # Decided before Originals is added below, so scanning one folder
        # never marks photos under other watched folders missing.
        full_scan = roots is None or len(root_ids) >= len(self.catalog.roots())

        # The library's own Originals always belong to the library. When the
        # library sits inside a watched folder (~/Pictures), that folder's
        # walk prunes the library, and imported photos were never seen -
        # each scan flagged them missing and they showed as blank tiles.
        if self.library_root is not None:
            originals = self.library_root / "Originals"
            if originals.is_dir() and originals not in root_ids:
                root_ids[originals] = self.catalog.add_root(
                    originals, in_library=True)

        # Registering a folder can take over another one given here - the
        # library's Originals inside a watched ~/Pictures - and that root's
        # row is gone. Each folder is filed under the root that covers it
        # now: photos written with the removed id failed the whole scan
        # ("FOREIGN KEY constraint failed"), and a restore rebuilt nothing.
        current = [(Path(r["path"]), int(r["id"])) for r in self.catalog.roots()]
        for folder in list(root_ids):
            covering = [(path, rid) for path, rid in current
                        if folder == path or folder.is_relative_to(path)]
            if covering:
                root_ids[folder] = min(covering, key=lambda c: len(c[0].parts))[1]

        # What the catalog already knows, so unchanged files can be skipped
        # without opening them.
        known: dict[str, tuple[int, float, int]] = {
            r["path"]: (r["id"], r["mtime"], r["bytes"])
            for r in self.catalog.q(
                "SELECT id, path, mtime, bytes FROM photos")}
        # Rows an older version stored wrongly are probed again even though
        # the file itself has not changed: unreadable files it kept as
        # zero-size "photos", and EXIF-rotated photos whose width and
        # height were recorded unrotated.
        reprobe = {
            r["path"] for r in self.catalog.q(
                "SELECT path FROM photos WHERE width = 0 OR height = 0 "
                "OR (orientation BETWEEN 5 AND 8 AND width > height)")}

        found: list[tuple[Path, os.stat_result, int]] = []
        for root, rid in root_ids.items():
            for path, st in self._walk(root):
                found.append((path, st, rid))
                p.done = len(found)
                p.current = path.name
                if len(found) % 200 == 0:
                    report()
        if self.cancel.is_set():
            p.phase = "cancelled"
            report()
            return p

        # Photos the user removed stay removed, although their files are
        # still here: treated as not present at all.
        removed = set(self.catalog.removed_paths())
        if removed:
            found = [f for f in found if str(f[0]) not in removed]
        seen_paths = {str(f[0]) for f in found}
        # A photo flagged missing (drive unplugged, folder briefly renamed)
        # that is back on disk gets its thumbnail again. An unchanged file
        # is not re-probed below, so without this it would stay a blank
        # "missing" tile forever.
        back = [known[p][0] for p in seen_paths if p in known]
        if back:
            with self.catalog.write() as cur:
                cur.executemany(
                    "UPDATE photos SET thumb_state=0 "
                    "WHERE id=? AND thumb_state=3", [(i,) for i in back])
        stale = [(path, st, rid) for path, st, rid in found
                 if str(path) not in known
                 or str(path) in reprobe
                 or abs(known[str(path)][1] - st.st_mtime) > 0.001
                 or known[str(path)][2] != st.st_size]
        not_photos: list[int] = []

        p.phase = "probing"
        p.total = len(stale)
        p.done = 0
        report()

        # Probing is header reads plus EXIF parsing: I/O bound, so threads
        # help even though the work is in Python.
        batch: list[dict] = []
        from . import system
        with ThreadPoolExecutor(max_workers=self.workers,
                                initializer=system.lower_thread_priority) as pool:
            futures = {pool.submit(iio.probe, path, rid): path
                       for path, st, rid in stale}
            for fut in as_completed(futures):
                if self.cancel.is_set():
                    break
                path = futures[fut]
                p.done += 1
                p.current = path.name
                try:
                    rec = fut.result()
                except Exception:
                    rec = None
                if rec is None:
                    p.errors += 1
                    # Known to the catalog but not a readable photo (now,
                    # or ever - older versions kept such files): drop the
                    # row so it stops showing as a blank tile.
                    if str(path) in known:
                        not_photos.append(known[str(path)][0])
                else:
                    if str(path) in known:
                        p.updated += 1
                    else:
                        p.added += 1
                    batch.append(rec)
                if len(batch) >= batch_size:
                    self.catalog.upsert_photos(batch)
                    batch = []
                    report()
                elif p.done % 50 == 0:
                    report()
        if batch:
            self.catalog.upsert_photos(batch)
        if not_photos:
            self.catalog.delete_photos(not_photos)
            p.updated += len(not_photos)
        try:
            self.catalog.pair_live_photos()
        except Exception:
            pass

        if self.cancel.is_set():
            p.phase = "cancelled"
            report()
            return p

        # Files that are gone from disk.  Recognise moves first: a photo
        # that reappeared elsewhere with the same fingerprint keeps its
        # rating, albums and edits instead of arriving as a stranger.
        if full_scan:
            self._reconcile_missing(known, seen_paths, p)

        p.phase = "done"
        p.current = ""
        report()
        return p

    def _reconcile_missing(self, known: dict, seen_paths: set, p: Progress
                           ) -> None:
        gone = [path for path in known if path not in seen_paths]
        if not gone:
            return
        # fingerprint -> id for everything currently present
        fresh = {}
        for row in self.catalog.q(
                "SELECT id, path, fingerprint FROM photos "
                "WHERE fingerprint IS NOT NULL"):
            if row["path"] in seen_paths:
                fresh.setdefault(row["fingerprint"], []).append(row["id"])

        moved, missing = [], []
        for path in gone:
            row = self.catalog.photo_by_path(path)
            if row is None:
                continue
            fp = row["fingerprint"]
            if fp and fresh.get(fp):
                moved.append((row["id"], fresh[fp][0]))
            else:
                missing.append(row["id"])

        if moved:
            # The new row already carries the correct path; carry the old
            # row's user state onto it, then drop the stale row.
            with self.catalog.write() as cur:
                for old_id, new_id in moved:
                    cur.execute(
                        "UPDATE photos SET rating=(SELECT rating FROM photos WHERE id=?),"
                        " favorite=(SELECT favorite FROM photos WHERE id=?),"
                        " hidden=(SELECT hidden FROM photos WHERE id=?)"
                        " WHERE id=?", (old_id, old_id, old_id, new_id))
                    cur.execute(
                        "UPDATE OR IGNORE album_items SET photo_id=? WHERE photo_id=?",
                        (new_id, old_id))
                    cur.execute("DELETE FROM photos WHERE id=?", (old_id,))
            p.moved = len(moved)
        if missing:
            # Not deleted: a disconnected drive or unmounted NAS must not
            # silently erase part of the library.
            with self.catalog.write() as cur:
                cur.executemany("UPDATE photos SET thumb_state=3 WHERE id=?",
                                [(i,) for i in missing])
            p.missing = len(missing)

    # -- thumbnails ------------------------------------------------------
    def add_files(self, paths: Iterable[Path | str]) -> int:
        """Index files that were just copied into the library's Originals.

        Used while an import is still running, so photos appear as they
        arrive: only these files are read, not the whole library.
        """
        if self.library_root is None:
            return 0
        originals = self.library_root / "Originals"
        root_id = self.catalog.add_root(originals, in_library=True)
        removed = set(self.catalog.removed_paths())
        records = []
        back = []
        for path in paths:
            path = Path(path).resolve()
            if str(path) in removed:
                # Imported again on purpose: a photo removed earlier comes
                # back, instead of being copied and then silently hidden.
                back.append(str(path))
            try:
                rec = iio.probe(path, root_id)
            except Exception:
                rec = None
            if rec:
                records.append(rec)
        if back:
            with self.catalog.write() as cur:
                cur.executemany("DELETE FROM removed WHERE path=?", [(p,) for p in back])
        if records:
            self.catalog.upsert_photos(records)
            try:
                self.catalog.pair_live_photos()
            except Exception:
                pass
        return len(records)

    def build_thumbnails(self, limit: int = 100_000,
                         on_progress: Callable[[Progress], None] | None = None
                         ) -> Progress:
        """Generate missing grid thumbnails, newest photos first."""
        if self.thumbs is None:
            return self.progress
        p = self.progress
        p.phase = "thumbnails"
        p.done = 0
        rows = self.catalog.paths_missing_thumbs(limit)
        p.total = len(rows)
        if on_progress:
            on_progress(p)

        def work(row):
            if self.cancel.is_set():
                return row["id"], 2
            out = self.thumbs.generate(row["path"], GRID_SIZE)
            return row["id"], (1 if out else 2)

        results: list[tuple[int, int]] = []
        from . import system
        with ThreadPoolExecutor(max_workers=self.workers,
                                initializer=system.lower_thread_priority) as pool:
            for fut in as_completed([pool.submit(work, r) for r in rows]):
                if self.cancel.is_set():
                    break
                try:
                    results.append(fut.result())
                except Exception:
                    p.errors += 1
                p.done += 1
                if len(results) >= 128:
                    with self.catalog.write() as cur:
                        cur.executemany(
                            "UPDATE photos SET thumb_state=? WHERE id=?",
                            [(s, i) for i, s in results])
                    results = []
                    if on_progress:
                        on_progress(p)
        if results:
            with self.catalog.write() as cur:
                cur.executemany("UPDATE photos SET thumb_state=? WHERE id=?",
                                [(s, i) for i, s in results])
        # decoding every photo left freed memory with Piklin: give it back
        system.release_memory()
        p.phase = "done"
        if on_progress:
            on_progress(p)
        return p

    # -- async wrapper ---------------------------------------------------
    def start(self, roots=None, on_progress=None, with_thumbnails=True
              ) -> threading.Thread:
        def run():
            self.scan(roots, on_progress)
            if with_thumbnails and not self.cancel.is_set():
                self.build_thumbnails(on_progress=on_progress)
        self._thread = threading.Thread(target=run, name="indexer", daemon=True)
        self._thread.start()
        return self._thread

    def stop(self, timeout: float = 5.0) -> None:
        self.cancel.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
