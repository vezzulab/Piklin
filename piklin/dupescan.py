"""Finding duplicates by reading every file that could be one.

The catalog's quick fingerprint (size plus the head and tail of a file) is
enough to notice a likely copy while scanning, but not to be sure of one. This
looks at every photo and video in the library, hidden ones included, groups
those that are the same size on disk, and reads each of them from the first
byte to the last. Only files whose whole content matches are duplicates.

A file already read stays known while it is unchanged, so a second look only
reads what is new.
"""
from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

CHUNK = 1 << 20


@dataclass
class Progress:
    phase: str = "listing"          # listing | reading | done
    files: int = 0                  # files in the library
    to_read: int = 0                # files that have to be read
    read: int = 0
    bytes_total: int = 0
    bytes_read: int = 0
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

    p.groups = catalog.duplicate_groups()
    p.phase = "done"
    report()
    return p
