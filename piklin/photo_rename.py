"""Renaming a photo renames the file itself.

A name changed only in the catalog would be lost the moment the catalog is
rebuilt, and the file manager, a backup or another app would still see the
old name. So the rename goes all the way to the file on disk, and then to
everything that refers to it by path: the catalog row, its search text,
the edit sidecar (moved and repointed), album files, photo-state.json,
and XMP sidecars written by other apps beside the photo.

Order is chosen so a failure never loses the photo: the file is renamed
first (atomic, same folder); if updating the catalog then fails, the file
is renamed back.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .library_move import _rewrite_json
from .i18n import _

# Characters no file name may hold on Linux, plus the ones that break the
# file on the filesystems people copy photos to (FAT/exFAT cards, NTFS disks).
_FORBIDDEN = set('/\\:*?"<>|\0')


class RenameError(Exception):
    """A rename that cannot be done; the message is shown to the user."""


def clean_name(name: str, suffix: str) -> str:
    """The new file stem from what was typed. The extension is kept: typing
    it along with the name is accepted, typing a different one is not a way
    to change the file's format."""
    name = " ".join((name or "").split())
    if suffix and name.lower().endswith(suffix.lower()):
        name = name[: -len(suffix)].rstrip()
    if not name or name in (".", ".."):
        raise RenameError(_("The name can't be empty."))
    bad = sorted({c for c in name if c in _FORBIDDEN or ord(c) < 32})
    if bad:
        shown = " ".join("\\0" if c == "\0" else c for c in bad)
        raise RenameError(_("A file name can't contain {characters}").format(characters=shown))
    if name.startswith("."):
        raise RenameError(_("A name starting with a dot would hide the photo."))
    if len((name + suffix).encode()) > 255:
        raise RenameError(_("That name is too long."))
    return name


def _xmp_pairs(old: Path, new: Path):
    # Both conventions: DSC_1.xmp (darktable, Lightroom) and DSC_1.JPG.xmp
    for o, n in ((old.with_suffix(".xmp"), new.with_suffix(".xmp")),
                 (old.with_name(old.name + ".xmp"), new.with_name(new.name + ".xmp"))):
        if o.is_file() and not n.exists():
            yield o, n


def rename_photo(library, catalog, photo_id: int, new_name: str) -> Path:
    """Rename photo ``photo_id`` on disk and everywhere it is referenced.

    Returns the new path. Raises RenameError with a message for the user.
    """
    row = catalog.photo(photo_id)
    if row is None:
        raise RenameError(_("That photo is no longer in the library."))
    old = Path(row["path"])
    if not old.exists():
        raise RenameError(_("“{name}” isn't on this computer. Is its drive "
                            "connected?").format(name=old.name))
    stem = clean_name(new_name, old.suffix)
    new = old.with_name(stem + old.suffix)
    if new == old:
        return old
    # A change of case only ("img" -> "IMG") is a real rename; anything else
    # already there is another file and is never overwritten.
    if new.exists() and not (new.name.lower() == old.name.lower()
                             and os.path.samefile(new, old)):
        raise RenameError(_("There is already a file named “{name}” in that "
                            "folder.").format(name=new.name))
    if catalog.photo_by_path(str(new)) is not None:
        raise RenameError(_("“{name}” is already in the library.").format(name=new.name))

    try:
        os.rename(old, new)
    except PermissionError:
        raise RenameError(_("This folder can't be changed: “{folder}”.").format(
            folder=old.parent))
    except OSError as exc:
        raise RenameError(_("The file couldn't be renamed: {error}.").format(
            error=exc.strerror))

    try:
        with catalog.write() as cur:
            cur.execute("UPDATE photos SET path=?, filename=?, "
                        # thumbnails are cached by path: make it again
                        "thumb_state=CASE WHEN thumb_state=1 THEN 0 "
                        "ELSE thumb_state END WHERE id=?",
                        (str(new), new.name, photo_id))
    except Exception:
        os.rename(new, old)
        raise RenameError(_("The library couldn't be updated, so the file "
                          "keeps its old name."))
    catalog.reindex_text([photo_id])

    # The edit sidecar mirrors the photo's path: move it, and point it at
    # the new file, or the edits would silently drop off the photo.
    old_sc, new_sc = library.edit_sidecar(old), library.edit_sidecar(new)
    if old_sc.is_file() and old_sc != new_sc:
        try:
            data = json.loads(old_sc.read_text())
            data["source"] = str(new)
            new_sc.parent.mkdir(parents=True, exist_ok=True)
            tmp = new_sc.with_name(new_sc.name + ".tmp")
            tmp.write_text(json.dumps(data, indent=2))
            tmp.replace(new_sc)
            old_sc.unlink()
        except (OSError, ValueError):
            pass

    for o, n in _xmp_pairs(old, new):
        try:
            os.rename(o, n)
        except OSError:
            pass

    # Albums and photo-state.json name photos by path. Only exact matches
    # change: the old path plus "/" can never be the start of another file.
    for f in [*library.albums.glob("*.json"), library.root / "photo-state.json"]:
        if f.is_file():
            _rewrite_json(f, str(old), str(new))
    return new
