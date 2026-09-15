"""Keeping a library working when it is moved.

The library is one package - "Piklin Library.piklin" - that can be copied
or moved as a unit. The catalog and the JSON
sidecars store photos by absolute path, though, so a moved package still
names its old location everywhere. ``relocate`` notices that on open and
rewrites those paths to where the package is now.

The package records its own last location in its settings, so the check
costs one string comparison when nothing moved. Photos referenced from
folders outside the package keep their paths: they did not move.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

from .paths import LIBRARY_EXT, LIBRARY_NAME, OLD_APP_NAME, pictures_dir

LAST_ROOT_KEY = "library_root_last"


def _swap(value, old: str, new: str):
    """``value`` with every path under ``old`` moved under ``new``."""
    if isinstance(value, str):
        if value == old:
            return new
        if value.startswith(old + "/"):
            return new + value[len(old):]
        return value
    if isinstance(value, list):
        return [_swap(v, old, new) for v in value]
    if isinstance(value, dict):
        # photo-state.json is keyed by path, so keys move too
        return {_swap(k, old, new): _swap(v, old, new)
                for k, v in value.items()}
    return value


def _rewrite_json(path: Path, old: str, new: str) -> bool:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return False
    fixed = _swap(data, old, new)
    if fixed == data:
        return False
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(fixed, indent=2))
        tmp.replace(path)
        return True
    except OSError:
        return False


def relocate(library, catalog, settings) -> int:
    """Point a moved library's stored paths at its current location.

    Returns the number of photos whose path changed (0 when the library is
    where it was last opened).
    """
    new = str(library.root)
    old = str(settings.get(LAST_ROOT_KEY) or "").rstrip("/")
    if not old or old == new:
        if old != new:
            settings.set(LAST_ROOT_KEY, new)
        return 0

    n = len(old) + 1
    under = "path = ? OR substr(path, 1, ?) = ?"
    try:
        with catalog.write() as cur:
            cur.execute(
                "UPDATE photos SET path = ? || substr(path, ?), "
                # thumbnails are cached by path: make them again
                "thumb_state = CASE WHEN thumb_state = 1 THEN 0 "
                "ELSE thumb_state END "
                f"WHERE {under}", (new, n, old, n, old + "/"))
            moved = cur.rowcount
            for table in ("roots", "removed"):
                cur.execute(f"UPDATE {table} SET path = ? || substr(path, ?) "
                            f"WHERE {under}", (new, n, old, n, old + "/"))
    except sqlite3.IntegrityError:
        # A path at the new location is already catalogued; the update
        # rolled back as a whole, so nothing is half-moved. Leave the
        # recorded location alone and let a rescan sort it out.
        return 0

    files = [f for f in library.root.glob("*.json")
             if f.name != library.settings.name]
    files += list(library.albums.glob("*.json"))
    files += list(library.edits.rglob("*.json"))
    for f in files:
        _rewrite_json(f, old, new)
    settings.set(LAST_ROOT_KEY, new)
    return moved


# -- a library restored or copied onto another computer ------------------------
# Albums, edits and marks name each photo by its full path, as the computer
# that wrote them saw it: "/home/ana/Pictures/Piklin Library.piklin/Originals/…"
# on Linux, "/Users/ana/Pictures/…" on a Mac, "C:\Users\Ana\Pictures\…" on
# Windows. Restored anywhere else, none of those paths exist, and a rebuild
# found no photo for any album. ``relocate`` only helps when the library still
# records where it was; a restore never brings settings.json back.

def _parts(text: str) -> list[str]:
    return [p for p in re.split(r"[\\/]", text) if p]


def _library_prefix(path: str) -> str | None:
    """The library a stored photo path belongs to: everything before its
    Originals folder, when that is a Piklin library package."""
    match = re.match(r"^(.*?)[\\/]Originals(?:[\\/]|$)", path)
    if not match:
        return None
    parts = _parts(match.group(1))
    if parts and (parts[-1].endswith(LIBRARY_EXT) or parts[-1] == OLD_APP_NAME):
        return match.group(1)
    return None


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)


def _adopt(value, olds: list[str], new: str):
    """``value`` with every path inside one of ``olds`` moved into ``new``,
    written the way this system writes paths."""
    if isinstance(value, str):
        for old in olds:
            if value == old:
                return new
            if value.startswith(old) and value[len(old):len(old) + 1] in ("/", "\\"):
                return os.path.join(new, *_parts(value[len(old):]))
        return value
    if isinstance(value, list):
        return [_adopt(v, olds, new) for v in value]
    if isinstance(value, dict):
        return {_adopt(k, olds, new): _adopt(v, olds, new) for k, v in value.items()}
    return value


def _path_files(library) -> list[Path]:
    """The library's own files that name photos by path."""
    files = [f for f in library.root.glob("*.json") if f.name != library.settings.name]
    files += list(library.albums.glob("*.json"))
    files += list(library.edits.rglob("*.json"))
    return files


def adopt_restored_paths(library) -> int:
    """Point the albums, edits and marks of a library restored or copied from
    another computer - any system, any user name - at where it is now.

    Run before the catalog is rebuilt from them. Returns the number of files
    rewritten (0 when every path already points here).
    """
    from .settings import Settings
    new = str(library.root)
    files = _path_files(library)
    olds: set[str] = set()
    for f in files:
        try:
            data = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        for text in _strings(data):
            prefix = _library_prefix(text)
            if prefix is not None and prefix != new:
                olds.add(prefix)
    if not olds:
        return 0
    ordered = sorted(olds, key=len, reverse=True)      # the longest, most exact, first
    changed = 0
    for f in files:
        try:
            data = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        fixed = _adopt(data, ordered, new)
        if fixed == data:
            continue
        tmp = f.with_name(f.name + ".tmp")
        try:
            tmp.write_text(json.dumps(fixed, indent=2))
            tmp.replace(f)
            changed += 1
        except OSError:
            pass
    Settings(library.settings).set(LAST_ROOT_KEY, new)
    return changed


def migrate_legacy_library() -> Path | None:
    """Turn ~/Pictures/Pikalicious into ~/Pictures/Piklin Library.piklin.

    A rename within the same folder: instant, and nothing is copied, so
    nothing can be half-done. The old location is written into the
    library's settings first, so the next open rewrites the paths.
    """
    from .settings import Settings
    pics = pictures_dir()
    old, new = pics / OLD_APP_NAME, pics / LIBRARY_NAME
    if new.exists() or not (old / "catalog.db").is_file():
        return None
    try:
        old = old.resolve()
        st = Settings(old / "settings.json")
        if not st.get(LAST_ROOT_KEY):
            st.set(LAST_ROOT_KEY, str(old))
        os.rename(old, new)
    except OSError:
        return None
    return new


def mark_package(root: Path) -> None:
    """Give the package the app's icon in file managers that support it
    (Nemo, Nautilus, Caja). Cosmetic, so any failure is ignored."""
    here = Path(__file__).resolve().parent.parent
    icon = next((p for p in (
        Path("/usr/share/icons/hicolor/256x256/apps/piklin.png"),
        here / "data" / "icons" / "piklin-256.png") if p.is_file()), None)
    if icon is None:
        return
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
        Gio.File.new_for_path(str(root)).set_attribute_string(
            "metadata::custom-icon",
            Gio.File.new_for_path(str(icon)).get_uri(),
            Gio.FileQueryInfoFlags.NONE, None)
    except Exception:
        pass
