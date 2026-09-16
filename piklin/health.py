"""Photo health: finding originals that changed on their own, and mending
them from the backup.

Disks lose bits over the years - a weak sector, a copy cut short, a drive
growing old - and a photo damaged that way looks like any other until the
day it is opened. Such damage keeps the file's size and date, which is also
why the backup never sends it again: the copy in the backup stays good.

How it works
------------
* Every original gets a digest of its whole content, recorded with its size
  and date in ``.cache/health/health.db``. That is outside the catalog, so a
  rebuild keeps it, and outside the backup, because it describes this disk.
* A check reads a slice of the library at a time - never-checked files
  first, then the ones checked longest ago - so a library of any size is
  covered over days without anyone noticing it.
* A file whose size or date changed was changed on purpose: an edit, a
  program writing its metadata. Its new digest is simply recorded.
* A file with the same size and date and a different digest was damaged.
  It is read a second time first, so a moment's read error is never taken
  for damage.
* Mending fetches the backup's copy beside the photo, and only when that
  copy's digest is the one recorded does it take the photo's place, with the
  photo's date put back, so the next backup has nothing to send. The damaged
  file is kept aside, never thrown away.
* Photos outside the library (a watched folder) are checked too, but there
  is no copy of them in the backup to mend them with: they are reported.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

# Every photo is read again at most this often: often enough to catch damage
# while the backup still holds a good copy, rarely enough to stay out of the way.
RECHECK_DAYS = 30
# A file changed this recently may still be being written (an import, a copy
# from a phone): it waits for a later check.
SETTLE_SECONDS = 10 * 60
CHUNK = 1024 * 1024
# A damaged photo with no good copy in the backup is looked for again once a
# day - a backup that was out of reach may be back - not at every check.
NO_COPY_RETRY_SECONDS = 24 * 3600

OK, DAMAGED, MENDED, NO_COPY = "ok", "damaged", "mended", "no_copy"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    key        TEXT PRIMARY KEY,     -- library-relative path, or absolute outside it
    size       INTEGER NOT NULL,
    mtime_ns   INTEGER NOT NULL,
    ctime_ns   INTEGER NOT NULL,     -- when the file itself last changed: see check()
    digest     TEXT NOT NULL,
    checked_at REAL NOT NULL,
    state      TEXT NOT NULL DEFAULT 'ok',
    damaged_at REAL,
    mended_at  REAL
);
CREATE INDEX IF NOT EXISTS files_checked ON files(checked_at);
CREATE INDEX IF NOT EXISTS files_state ON files(state) WHERE state != 'ok';
"""


def health_dir(library) -> Path:
    return Path(library.root) / ".cache" / "health"


def digest(path: Path) -> str:
    """The whole file's content, as hex. Read in pieces, and without
    filling the computer's file cache with a library's worth of photos."""
    h = hashlib.blake2b(digest_size=32)
    with open(path, "rb", buffering=0) as fh:
        _no_cache(fh)
        while True:
            chunk = fh.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
        _drop_cache(fh)
    return h.hexdigest()


def _no_cache(fh) -> None:
    if sys.platform == "darwin":
        try:
            import fcntl
            fcntl.fcntl(fh.fileno(), 48, 1)          # F_NOCACHE
        except (OSError, ImportError, ValueError):
            pass


def _drop_cache(fh) -> None:
    if hasattr(os, "posix_fadvise"):
        try:
            os.posix_fadvise(fh.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
        except (OSError, AttributeError):
            pass


@dataclass
class Report:
    checked: int = 0                 # files read this time
    bytes: int = 0
    recorded: int = 0                # first seen, or changed on purpose
    damaged: list[str] = field(default_factory=list)   # keys found damaged now
    missing: int = 0                 # in the catalog, gone from the disk
    skipped: int = 0                 # still settling, or changing while read
    due: int = 0                     # left for later checks
    stopped: bool = False            # the budget ran out or it was cancelled


@dataclass
class Mending:
    mended: list[str] = field(default_factory=list)
    no_copy: list[str] = field(default_factory=list)     # nothing good to mend with
    unreachable: bool = False                             # no backup could be reached


class Health:
    """The record of every original's content, for one library."""

    def __init__(self, library):
        self.library = library
        self.root = Path(library.root).resolve()
        folder = health_dir(library)
        folder.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._con = sqlite3.connect(folder / "health.db", check_same_thread=False,
                                    timeout=30)
        self._con.row_factory = sqlite3.Row
        with self._lock, self._con:
            self._con.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._con.close()

    # -- keys --------------------------------------------------------------
    def key(self, path: str | Path) -> str:
        p = Path(path)
        try:
            return p.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return str(p.resolve())

    def path(self, key: str) -> Path:
        p = Path(key)
        return p if p.is_absolute() else self.root / key

    def in_library(self, key: str) -> bool:
        return not Path(key).is_absolute()

    # -- queries -----------------------------------------------------------
    def rows(self, state: str | None = None) -> list[sqlite3.Row]:
        with self._lock:
            if state is None:
                return self._con.execute("SELECT * FROM files").fetchall()
            return self._con.execute("SELECT * FROM files WHERE state=?",
                                     (state,)).fetchall()

    def summary(self) -> dict:
        with self._lock:
            row = self._con.execute(
                "SELECT COUNT(*) AS files, MAX(checked_at) AS last, "
                "SUM(state IN ('damaged','no_copy')) AS damaged, "
                "SUM(state='mended') AS mended FROM files").fetchone()
        return {"files": row["files"] or 0, "last": row["last"],
                "damaged": row["damaged"] or 0, "mended": row["mended"] or 0}

    def _get(self, key: str):
        with self._lock:
            return self._con.execute("SELECT * FROM files WHERE key=?", (key,)).fetchone()

    def _record(self, key, st, dig, now, state=OK) -> None:
        with self._lock, self._con:
            self._con.execute(
                "INSERT INTO files(key,size,mtime_ns,ctime_ns,digest,checked_at,state) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET size=excluded.size, "
                "mtime_ns=excluded.mtime_ns, ctime_ns=excluded.ctime_ns, "
                "digest=excluded.digest, checked_at=excluded.checked_at, "
                "state=excluded.state, damaged_at=NULL, mended_at=NULL",
                (key, st.st_size, st.st_mtime_ns, st.st_ctime_ns, dig, now, state))

    @staticmethod
    def _same_file(row, st) -> bool:
        """Size, date and change time as recorded. The change time is the
        system's own, set whenever a program writes the file or even puts
        its date back afterwards, and nobody can set it by hand: a photo a
        program rewrote on purpose always differs here, so it is never taken
        for damage and never "mended" back. Damage from the disk changes
        none of the three."""
        return (row["size"], row["mtime_ns"], row["ctime_ns"]) == (
            st.st_size, st.st_mtime_ns, st.st_ctime_ns)

    def _touch(self, key, now) -> None:
        with self._lock, self._con:
            self._con.execute("UPDATE files SET checked_at=? WHERE key=?", (now, key))

    def _set_state(self, key, state, now) -> None:
        column = {DAMAGED: "damaged_at", NO_COPY: "damaged_at", MENDED: "mended_at"}.get(state)
        with self._lock, self._con:
            if column:
                self._con.execute(
                    f"UPDATE files SET state=?, {column}=COALESCE({column}, ?), checked_at=? "
                    "WHERE key=?", (state, now, now, key))
            else:
                self._con.execute("UPDATE files SET state=?, checked_at=? WHERE key=?",
                                  (state, now, key))

    # -- checking ----------------------------------------------------------
    def check(self, paths, byte_budget: int, seconds: float = 90.0,
              now: float | None = None, cancel: threading.Event | None = None) -> Report:
        """Read up to ``byte_budget`` bytes of the files that are due, and
        say which of them were damaged since they were last read."""
        now = time.time() if now is None else now
        started = time.monotonic()
        report = Report()
        with self._lock:
            known = {r["key"]: r["checked_at"] for r in
                     self._con.execute("SELECT key, checked_at FROM files")}
        horizon = now - RECHECK_DAYS * 86400
        due = []
        for p in paths:
            k = self.key(p)
            last = known.get(k)
            if last is None or last < horizon:
                due.append((last or 0.0, k))
        due.sort()                      # never checked first, then oldest
        report.due = len(due)

        for _last, k in due:
            if cancel is not None and cancel.is_set():
                report.stopped = True
                break
            if report.bytes >= byte_budget or time.monotonic() - started > seconds:
                report.stopped = True
                break
            path = self.path(k)
            try:
                st = path.stat()
            except OSError:
                report.missing += 1
                continue
            # Written minutes ago - an import keeps the photo's own date, so
            # the change time is what shows a copy still arriving.
            if now - max(st.st_mtime, st.st_ctime) < SETTLE_SECONDS:
                report.skipped += 1
                continue
            try:
                dig = digest(path)
                after = path.stat()
            except OSError:
                report.skipped += 1
                continue
            if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
                    st.st_size, st.st_mtime_ns, st.st_ctime_ns):
                report.skipped += 1                 # changing while it was read
                continue
            report.checked += 1
            report.bytes += st.st_size
            report.due -= 1
            row = self._get(k)
            if row is None or not self._same_file(row, st):
                # first seen, or changed on purpose: this is its content now
                self._record(k, st, dig, now)
                report.recorded += 1
            elif dig == row["digest"]:
                if row["state"] in (DAMAGED, NO_COPY):
                    # put right some other way - by hand, from another copy
                    self._set_state(k, OK, now)
                else:
                    self._touch(k, now)
            else:
                # Same size, same date, other content. Read it once more: a
                # read that went wrong for a moment is not damage.
                try:
                    again = digest(path)
                except OSError:
                    report.skipped += 1
                    continue
                if again == row["digest"]:
                    self._touch(k, now)
                elif again == dig:
                    if row["state"] not in (DAMAGED, NO_COPY):
                        report.damaged.append(k)
                    self._set_state(k, row["state"] if row["state"] == NO_COPY else DAMAGED, now)
                else:
                    report.skipped += 1             # not reading the same twice
        return report

    # -- mending -----------------------------------------------------------
    def mend(self, backends, now: float | None = None,
             cancel: threading.Event | None = None) -> Mending:
        """Put back, from the backup, every damaged photo whose copy there is
        the one recorded. ``backends``: the destinations to look in, in order."""
        now = time.time() if now is None else now
        result = Mending()
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
        reached = False
        for row in self.rows(DAMAGED) + self.rows(NO_COPY):
            if cancel is not None and cancel.is_set():
                break
            k = row["key"]
            if row["state"] == NO_COPY and now - row["checked_at"] < NO_COPY_RETRY_SECONDS:
                continue            # looked for already today: not fetched again and again
            if not self.in_library(k):
                if row["state"] != NO_COPY:
                    self._set_state(k, NO_COPY, now)
                    result.no_copy.append(k)
                continue
            target = self.path(k)
            try:
                st = target.stat()
            except OSError:
                continue                                  # gone: nothing to mend
            if not self._same_file(row, st):
                continue            # changed since: the next check decides what it is
            incoming = health_dir(self.library) / "incoming" / k
            good = False
            for backend in backends:
                incoming.unlink(missing_ok=True)
                try:
                    fetched = backend.get(k, incoming)
                except Exception:
                    fetched = False
                if not fetched:
                    continue
                reached = True
                try:
                    good = (incoming.stat().st_size == row["size"]
                            and digest(incoming) == row["digest"])
                except OSError:
                    good = False
                if good:
                    break
            if not good:
                incoming.unlink(missing_ok=True)
                if not reached:
                    continue                  # no backup answered: tried again next time
                if row["state"] != NO_COPY:
                    self._set_state(k, NO_COPY, now)
                    result.no_copy.append(k)
                else:
                    self._touch(k, now)       # looked for again today, still nothing good
                continue
            aside = health_dir(self.library) / "damaged" / stamp / k
            try:
                aside.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, aside)               # the damaged file, kept
                os.replace(incoming, target)              # the good copy, in its place
                os.utime(target, ns=(st.st_atime_ns, row["mtime_ns"]))
                placed = target.stat()
                ok = ((placed.st_size, placed.st_mtime_ns) == (row["size"], row["mtime_ns"])
                      and digest(target) == row["digest"])
            except OSError:
                ok = False
            if ok:
                # putting it back changed its change time: that is the file now
                self._record(k, placed, row["digest"], now, state=MENDED)
                with self._lock, self._con:
                    self._con.execute("UPDATE files SET damaged_at=?, mended_at=? WHERE key=?",
                                      (row["damaged_at"], now, k))
                result.mended.append(k)
            else:
                # Never leave it worse: the damaged file goes back as it was,
                # and stays known as damaged - putting it back changed its
                # change time, which must not pass for a change on purpose.
                try:
                    if aside.is_file() and not (
                            target.is_file() and digest(target) == row["digest"]):
                        shutil.copy2(aside, target)
                        os.utime(target, ns=(st.st_atime_ns, row["mtime_ns"]))
                    back = target.stat()
                    with self._lock, self._con:
                        self._con.execute(
                            "UPDATE files SET ctime_ns=?, checked_at=? WHERE key=?",
                            (back.st_ctime_ns, now, k))
                except OSError:
                    pass
            incoming.unlink(missing_ok=True)
        result.unreachable = bool(backends) and not reached and bool(
            self.rows(DAMAGED))
        return result
