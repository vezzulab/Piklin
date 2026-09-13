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
import sqlite3
from pathlib import Path

from .paths import LIBRARY_NAME, OLD_APP_NAME, pictures_dir

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
