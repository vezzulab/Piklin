"""On-disk layout for the Piklin library.

Everything the app owns lives in ONE package under the user's pictures
folder - "Piklin Library.piklin" - that is copied or moved as a unit, so "back up my photos" and "back up Piklin" are the same
action.  The layout is deliberately plain: a person who opens the folder
in a file manager can tell what every item is, and every piece of state
except the cache is a human-readable file.

    ~/Pictures/Piklin Library.piklin/
        README.txt          <- explains all of this, written on first run
        catalog.db          <- SQLite INDEX.  Derived; safe to delete.
        Edits/              <- one .json edit stack per edited photo,
                               mirroring the source tree layout
        Albums/             <- one .json per album (ordered list of photos)
        Originals/          <- photos copied *into* the library on import
                               (files left in place elsewhere are only
                               referenced, never moved)
        Exports/            <- rendered / compressed output, by date
        Trash/              <- recoverable deletions + manifest
        .cache/             <- thumbnails.  Has a CACHEDIR.TAG so backup
                               tools skip it automatically.

The invariant that makes this safe: **catalog.db holds no unique state.**
It is an index over the originals plus the JSON sidecars.  Lose it and
`piklin --rebuild-index` reconstructs it exactly.
"""
from __future__ import annotations

import os
from pathlib import Path

# PIKLIN_APP_ID runs a test build as its own app, beside the installed one,
# instead of handing everything to it. Reopening Piklin keeps the setting.
APP_ID = os.environ.get("PIKLIN_APP_ID") or "com.envy.Piklin"
APP_NAME = "Piklin"
# Where "Support on Ko-fi" in the sidebar leads. Empty hides the button.
SUPPORT_URL = "https://ko-fi.com/vezzustudio"
# The app was called Pikalicious before; a library created then stays
# where it is rather than an empty new one appearing beside it.
OLD_APP_NAME = "Pikalicious"
# The library is one package: move or copy it
# whole and open it from wherever it is.
LIBRARY_EXT = ".piklin"
LIBRARY_NAME = f"{APP_NAME} Library{LIBRARY_EXT}"

# Cache directories that backup tools recognise and skip.
CACHEDIR_TAG = (
    "Signature: 8a477f597d28d172789f06886806bc55\n"
    "# This file is a cache directory tag created by Piklin.\n"
    "# For information about cache directory tags, see:\n"
    "#\thttps://bford.info/cachedir/\n"
)

README = """\
Piklin library
===================

This folder is your photo library.  It is designed so that copying this
one directory is a complete backup, and so that you can understand and
recover everything in it without Piklin installed.

What is in here
---------------

catalog.db
    The search index: which photos exist, their dates, sizes and camera
    details.  This file is DERIVED - everything in it can be rebuilt
    from the files below.  If it is lost or corrupted, run

        piklin --rebuild-index

    and your photos, albums, folders, favourites, ratings and hidden
    marks all come back.

photo-state.json
    What you marked: favourites, star ratings, hidden photos, and what
    is in the trash.  Only photos that differ from the default appear
    here, so this file stays small.  Photos are listed by their path,
    so it keeps working after a rebuild.

watched-folders.json
    The folders Piklin watches for photos.  This is what lets a
    rebuild find your library again.

Edits/
    Your adjustments, one JSON file per edited photo, in a tree that
    mirrors where the photo lives.  Edits are NON-DESTRUCTIVE: your
    original file is never written to.  Each file is readable text
    listing the tools applied and their settings, so an edit made today
    is still legible in ten years.

Albums/
    One JSON file per album, holding the album name, which folder it
    sits in, and the ordered list of photos.  _folders.json holds the
    folder tree itself.  Renaming a file does not rename the album; the
    name is inside the file.

Originals/
    Photos that were imported *into* the library, filed by date as
    Originals/YYYY/YYYY-MM-DD/.  Photos you added by pointing
    Piklin at an existing folder are NOT here - those are left
    exactly where they are and only referenced.

Exports/
    Files you exported or compressed, grouped by date.  Safe to delete.
    Piklin never indexes this folder as part of your library, so
    exporting a photo does not create a duplicate of it.

.cache/
    Thumbnails.  Rebuilt on demand, and tagged so that backup tools
    (borg, restic, tar --exclude-caches) skip it.  Safe to delete.

About the trash
---------------

Photos you move to the trash are hidden from your library but their
files are NOT moved or deleted - Piklin does not take ownership of
photos it only references.  Emptying the trash simply forgets them; if
you want the files gone, delete them in your file manager.

Moving this library
-------------------

This whole package is your library. Close Piklin, then move or copy
it anywhere - another folder, an external disk, another computer -
and open it with Open Library... in the main menu. Photos stored
inside it come along; photos from watched folders outside it stay
where they are.

Nothing here phones home, and no file outside this folder is modified
except the ones you explicitly export.
"""


def pictures_dir() -> Path:
    """The user's pictures folder, honouring XDG then falling back."""
    # xdg-user-dirs writes this; read it directly rather than shelling out
    # to xdg-user-dir, which is not present in a minimal AppImage sandbox.
    cfg = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    dirs_file = cfg / "user-dirs.dirs"
    if dirs_file.is_file():
        try:
            for line in dirs_file.read_text(errors="replace").splitlines():
                line = line.strip()
                if not line.startswith("XDG_PICTURES_DIR"):
                    continue
                value = line.split("=", 1)[1].strip().strip('"')
                value = value.replace("$HOME", str(Path.home()))
                p = Path(value).expanduser()
                if p != Path.home():          # a misconfigured $HOME is not useful
                    return p
        except (OSError, IndexError):
            pass
    return Path.home() / "Pictures"


def _pointer_file() -> Path:
    cfg = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return cfg / "piklin" / "library.json"


def remembered_library() -> Path | None:
    """The library opened last, if it is still there. Kept outside the
    library, because it is what finds the library."""
    import json
    try:
        p = Path(json.loads(_pointer_file().read_text())["library"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return p if (p / "catalog.db").is_file() else None


def remember_library(root: Path | str) -> None:
    import json
    f = _pointer_file()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"library": str(root)}))
    except OSError:
        pass


def default_library_root() -> Path:
    remembered = remembered_library()
    if remembered is not None:
        return remembered
    pics = pictures_dir()
    new, old = pics / LIBRARY_NAME, pics / OLD_APP_NAME
    if not new.exists() and (old / "catalog.db").is_file():
        return old
    return new


class Library:
    """Resolved paths for one library root, with lazy creation."""

    def __init__(self, root: Path | str | None = None):
        if root is None:
            root = (os.environ.get("PIKLIN_LIBRARY")
                    or os.environ.get("PIKALICIOUS_LIBRARY"))
        # Always the real, absolute path: the catalog stores photos by their
        # resolved path, so a relative or symlinked library root made
        # freshly imported photos impossible to look up (a camera photo
        # dropped on an album was imported but never filed into it).
        self.root = (Path(root).expanduser() if root
                     else default_library_root()).resolve()

    # -- locations -------------------------------------------------------
    @property
    def db(self) -> Path:
        return self.root / "catalog.db"

    @property
    def edits(self) -> Path:
        return self.root / "Edits"

    @property
    def albums(self) -> Path:
        return self.root / "Albums"

    @property
    def originals(self) -> Path:
        return self.root / "Originals"

    @property
    def exports(self) -> Path:
        return self.root / "Exports"

    @property
    def trash(self) -> Path:
        return self.root / "Trash"

    @property
    def cache(self) -> Path:
        return self.root / ".cache"

    @property
    def thumbs(self) -> Path:
        return self.cache / "thumbnails"

    @property
    def rebuild_flag(self) -> Path:
        """Set by a restore: the catalog is behind the files on disk until
        the next start rebuilds it from them."""
        return self.cache / "rebuild-after-restore"

    def rebuild_pending(self) -> bool:
        return self.rebuild_flag.exists()

    @property
    def settings(self) -> Path:
        # Settings live with the library, not in ~/.config, so that moving
        # the library to another machine carries them along.
        return self.root / "settings.json"

    # -- setup -----------------------------------------------------------
    def ensure(self) -> "Library":
        """Create the layout if absent.  Idempotent and cheap to re-run."""
        for d in (self.root, self.edits, self.albums, self.originals,
                  self.exports, self.trash, self.cache, self.thumbs):
            d.mkdir(parents=True, exist_ok=True)

        tag = self.cache / "CACHEDIR.TAG"
        if not tag.exists():
            tag.write_text(CACHEDIR_TAG)

        readme = self.root / "README.txt"
        if not readme.exists():
            readme.write_text(README)
        return self

    # -- sidecar mapping -------------------------------------------------
    def edit_sidecar(self, photo_path: Path | str) -> Path:
        """Where the edit stack for ``photo_path`` is stored.

        The library tree is mirrored under Edits/ so the sidecar for a
        photo is findable by hand.  Absolute paths outside the library
        are mapped under Edits/_external/<path-without-leading-slash>,
        which keeps the mirror property for those too.
        """
        p = Path(photo_path).expanduser()
        try:
            rel = p.resolve().relative_to(self.root.resolve())
        except (ValueError, OSError):
            rel = Path("_external") / p.as_posix().lstrip("/")
        return (self.edits / rel).with_suffix(p.suffix + ".json")
