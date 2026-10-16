"""Photos kept on a drive.

Someone with all their photos on an external hard drive does not have to bring
them into Piklin: the folder on the drive is added where it is, and Piklin
works with the photos there - it never copies or moves them. What Piklin keeps
(the list of photos, the previews, albums, edits, people) is on the computer,
so the drive can be unplugged and the photos browsed, searched and found on the
map; the original is opened when the drive is back.

This knows where drives are mounted, whether a folder is on one, and whether it
is there now.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def mount_points() -> list[Path]:
    """The folders in which a computer shows its drives."""
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    if sys.platform == "darwin":
        bases = ["/Volumes"]
    else:
        bases = [f"/run/media/{user}", f"/media/{user}", "/media", "/mnt"]
    return [Path(b) for b in bases if b and os.path.isdir(b)]


def start_folder() -> Path | None:
    """Where a dialog to choose a drive's folder should open."""
    for base in mount_points():
        try:
            if any(base.iterdir()):
                return base
        except OSError:
            continue
    return None


def _under(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def is_external(path: Path | str) -> bool:
    """Whether a folder is on a drive that can be unplugged (or a network
    share), judging by where it is mounted. The computer's own disk is not."""
    p = Path(path)
    for base in mount_points():
        if _under(p, base):
            rest = p.relative_to(base).parts
            if not rest:
                return False
            # /Volumes/Macintosh HD is the computer's own disk, a link to /
            return os.path.realpath(base / rest[0]) != "/"
    return False


def name(path: Path | str) -> str:
    """The drive's name as the computer shows it: the folder under the mount
    point; for a folder elsewhere, the folder's own name."""
    p = Path(path)
    for base in mount_points():
        if _under(p, base):
            rest = p.relative_to(base).parts
            if rest:
                return rest[0]
    return p.name or str(p)


def connected(path: Path | str) -> bool:
    """Whether the folder is there now: its drive is plugged in."""
    try:
        return os.path.isdir(path)
    except OSError:
        return False


def status(roots) -> list[dict]:
    """For each watched folder (catalog rows): where it is, its drive's name,
    whether it is on one that can be unplugged, and whether it is there now."""
    out = []
    for r in roots:
        path = r["path"]
        out.append({"id": r["id"], "path": path, "name": name(path),
                    "external": is_external(path), "connected": connected(path)})
    return out
