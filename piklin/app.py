"""Application entry point."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

if sys.platform == "darwin":
    # Pango finds fonts through CoreText on a Mac, where a font added for this
    # process alone (Inter, below) is never picked up and Helvetica stands in.
    # Its fontconfig backend finds fonts the way it does on Linux. Set before
    # anything loads Pango.
    os.environ.setdefault("PANGOCAIRO_BACKEND", "fontconfig")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .paths import APP_ID, LIBRARY_EXT, Library, remember_library

VERSION = "1.0.4"


def _load_bundled_fonts():
    """Register the app's bundled fonts for this process only.

    Inter (SIL OFL - free to redistribute) is registered with fontconfig
    at runtime via FcConfigAppFontAddDir, so the font is available to
    Pango/GTK the moment the process starts, whether or not the host has
    it installed, and without writing anything into the user's font
    directories. This is the standard technique portable Linux apps use
    to bundle a typeface: fontconfig, not GTK, owns font discovery, and
    it exposes exactly this hook for "add fonts for just this process".

    A platform vendor's own system typeface is not an option here - its
    licence restricts it to that vendor's platforms, so shipping it in a
    Linux package would not be a legal distribution.  Inter was
    designed for UI text and reads as the same family of typeface;
    pairing it with the interface's neutral grey/orange palette is what
    actually carries the "premium" feel, not the specific letterforms.
    """
    import ctypes
    import ctypes.util
    from pathlib import Path as _P

    candidates = []
    if os.environ.get("APPDIR"):
        candidates.append(_P(os.environ["APPDIR"]) /
                          "usr/share/piklin/data/fonts")
    candidates.append(_P(__file__).resolve().parent.parent / "data" / "fonts")
    candidates.append(_P("/usr/share/piklin/fonts"))

    font_dir = next((c for c in candidates if c.is_dir()), None)
    if font_dir is None:
        return False

    lib_name = ctypes.util.find_library("fontconfig")
    if not lib_name:
        return False
    try:
        fc = ctypes.CDLL(lib_name)
        fc.FcConfigAppFontAddDir.restype = ctypes.c_int
        ok = fc.FcConfigAppFontAddDir(
            None, ctypes.c_char_p(str(font_dir).encode()))
        return bool(ok)
    except OSError:
        return False


def _load_css():
    _add_css(Path(__file__).parent / "ui" / "style.css")
    if sys.platform == "darwin":
        # Window buttons drawn as a Mac draws them; one step above
        # style.css so these rules win where both set a property.
        _add_css(Path(__file__).parent / "ui" / "style-macos.css", extra=1)


def _add_css(css: Path, extra: int = 0):
    if not css.is_file():
        return
    provider = Gtk.CssProvider()
    try:
        provider.load_from_path(str(css))
    except Exception:
        return
    display = Gdk.Display.get_default()
    if display is not None:
        # USER priority, not APPLICATION.  libadwaita resolves its named
        # colours (window_bg_color, headerbar_bg_color, accent_bg_color…)
        # from the provider chain, and a user's GTK theme supplies them
        # too.  At APPLICATION priority a dark desktop theme still won and
        # repainted the whole app; USER sits above both, so the palette
        # below is what actually renders on every desktop.
        # One above USER, not USER itself: GTK loads the user's own
        # ~/.config/gtk-4.0/gtk.css at exactly USER, and between equal
        # priorities the result depends on load order - a theme installed
        # there (Cipher, with its magenta fields and selections) still
        # won wherever it set a property we also set.
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1 + extra)


class PikaliciousApp(Adw.Application):
    def __init__(self, library_root=None):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.library_root = library_root
        self.window = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        _load_bundled_fonts()
        _load_css()
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Primary>q"])

    def do_activate(self):
        if self.window is None:
            from .ui.window import MainWindow
            from . import library_move
            if self.library_root is None and not (
                    os.environ.get("PIKLIN_LIBRARY")
                    or os.environ.get("PIKALICIOUS_LIBRARY")):
                library_move.migrate_legacy_library()
            library = Library(self.library_root).ensure()
            # Only the library opened by default is remembered here; one
            # named with --library (a test, a one-off) must not replace it.
            # Open Library... and opening a package remember theirs.
            if self.library_root is None:
                remember_library(library.root)
            library_move.mark_package(library.root)
            self.window = MainWindow(self, library)
            self._apply_theme(library)
        self.window.present()

    def do_open(self, files, n_files, hint):
        """Opened with file arguments: index their folders, or open a
        library package."""
        for f in files:
            p = f.get_path()
            if p and p.rstrip("/").endswith(LIBRARY_EXT):
                if self.window is None:
                    self.library_root = p
                    remember_library(Path(p).resolve())
                    self.do_activate()
                else:
                    self.window._switch_library(p)
                return
        self.do_activate()
        folders = []
        for f in files:
            p = f.get_path()
            if not p:
                continue
            p = Path(p)
            folders.append(str(p if p.is_dir() else p.parent))
        if folders and self.window is not None:
            self.window._start_scan(folders)

    def _apply_theme(self, library):
        from .settings import Settings
        # Light by default: a photo library is a white gallery wall, and
        # dark chrome around a bright photograph makes the photo look
        # washed out by comparison.
        theme = Settings(library.settings).get("theme", "light")
        mgr = Adw.StyleManager.get_default()
        mgr.set_color_scheme({
            "light": Adw.ColorScheme.FORCE_LIGHT,
            "dark": Adw.ColorScheme.FORCE_DARK,
        }.get(theme, Adw.ColorScheme.PREFER_LIGHT))


def rebuild_index(library: Library) -> int:
    """Reconstruct catalog.db from the originals and sidecars."""
    import json
    from .catalog import Catalog
    from .indexer import Indexer
    from .thumbs import ThumbCache

    print(f"Rebuilding index for {library.root}")
    if library.db.exists():
        backup = library.db.with_suffix(".db.bak")
        library.db.replace(backup)
        # its write-ahead log belongs to the old file, never the new one
        for suffix in ("-wal", "-shm"):
            side = Path(str(library.db) + suffix)
            if side.exists():
                side.replace(Path(str(backup) + suffix))
        print(f"  previous index moved to {backup.name}")

    catalog = Catalog(library.db)
    indexer = Indexer(catalog, ThumbCache(library.thumbs),
                      library_root=library.root)

    # The watched folders are recorded in watched-folders.json precisely
    # so a rebuild can find them again: deriving them from edit sidecars
    # alone only ever recovered folders that happened to contain an
    # edited photo, silently dropping the rest of the library.
    from . import sidecars
    roots = [library.originals] if library.originals.is_dir() else []
    for entry in sidecars.read_roots(library):
        rp = Path(entry.get("path", "")).expanduser()
        if rp.is_dir():
            roots.append(rp)
    external = {}
    for sidecar in library.edits.rglob("*.json"):
        try:
            data = json.loads(sidecar.read_text())
        except (OSError, ValueError):
            continue
        src = data.get("source")
        if src and Path(src).exists():
            external[str(Path(src).parent)] = True
    # A folder holding an edited photo is usually already inside a watched
    # folder - it is only a root of its own when it is not. Adding it anyway
    # registered a second, nested root over the same files: they were
    # scanned twice ("indexed 63" for a 42-photo library) and forgetting
    # the outer folder later left the inner one's photos behind.
    def covered(p: Path) -> bool:
        return any(p == r or p.is_relative_to(r) for r in roots)
    for p in external:
        p = Path(p)
        if not covered(p):
            roots.append(p)
    if not roots:
        print("  nothing to rebuild from")
        return 1

    # Before scanning, so photos removed from the library are not added back.
    n_removed = sidecars.restore_removed(library, catalog)
    if n_removed:
        print(f"  kept {n_removed} removed photos out of the library")
    progress = indexer.scan(roots, lambda p: None)
    print(f"  indexed {progress.added} photos")

    restored = 0
    for album_file in library.albums.glob("*.json"):
        try:
            data = json.loads(album_file.read_text())
        except (OSError, ValueError):
            continue
        if data.get("format") != "pikalicious-album":
            continue
        aid = catalog.create_album(data.get("name", album_file.stem),
                                   data.get("uuid"))
        ids = []
        for p in data.get("photos", []):
            row = catalog.photo_by_path(p)
            if row:
                ids.append(row["id"])
        if ids:
            catalog.album_add(aid, ids)
        cover = catalog.photo_by_path(data["cover"]) if data.get("cover") else None
        if cover is not None:
            catalog.set_album_cover(aid, cover["id"])
        restored += 1
    print(f"  restored {restored} albums")

    marked = 0
    for sidecar in library.edits.rglob("*.json"):
        try:
            data = json.loads(sidecar.read_text())
        except (OSError, ValueError):
            continue
        src = data.get("source")
        if not src:
            continue
        row = catalog.photo_by_path(src)
        if row:
            # photo edits list layers; video edits count their changes
            catalog.note_edit(row["id"], len(data.get("layers") or [])
                              or int(data.get("changes") or 0))
            marked += 1
    print(f"  reattached {marked} edit stacks")

    n_folders = sidecars.restore_folders(library, catalog)
    print(f"  restored {n_folders} folders")
    n_smart = sidecars.restore_smart_albums(library, catalog)
    print(f"  restored {n_smart} smart albums")

    # Re-attach albums to the folders they belonged to, by uuid.
    import json as _json
    attached = 0
    for album_file in library.albums.glob("*.json"):
        if album_file.name == "_folders.json":
            continue
        try:
            data = _json.loads(album_file.read_text())
        except (OSError, ValueError):
            continue
        folder_uuid = data.get("folder_uuid")
        if not folder_uuid:
            continue
        arow = catalog.q1("SELECT id FROM albums WHERE uuid=?",
                         (data.get("uuid"),))
        frow = catalog.q1("SELECT id FROM folders WHERE uuid=?", (folder_uuid,))
        if arow and frow:
            catalog.move_album_to_folder(arow["id"], frow["id"])
            attached += 1
    print(f"  re-filed {attached} albums into folders")

    n_state = sidecars.restore_photo_state(library, catalog)
    print(f"  restored marks (favourites, ratings, hidden) on {n_state} photos")
    print("Done.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="piklin", description="Photo library and editor")
    parser.add_argument("paths", nargs="*", help="folders or photos to open")
    parser.add_argument("--library", help="use a different library folder")
    parser.add_argument("--rebuild-index", action="store_true",
                        help="rebuild catalog.db from originals and sidecars")
    parser.add_argument("--version", action="version",
                        version=f"Piklin {VERSION}")
    args = parser.parse_args(argv)

    # The language comes first: every window and message is built in it.
    from . import i18n
    i18n.setup()

    library = Library(args.library).ensure()

    # Set after files were restored into an empty library: the catalog is
    # rebuilt from them before the window opens.
    restored = library.root / ".cache" / "rebuild-after-restore"
    if args.rebuild_index or restored.exists():
        restored.unlink(missing_ok=True)
        status = rebuild_index(library)
        if args.rebuild_index:
            return status

    app = PikaliciousApp(args.library)
    files = [Gio.File.new_for_path(p) for p in args.paths]
    status = app.run([sys.argv[0]] + args.paths if files else [sys.argv[0]])
    # Open Library...: the window is bound to one catalog, so another
    # library opens in a fresh process once this one has fully quit.
    target = getattr(app, "relaunch_library", None)
    if target:
        from .updates import launcher
        start = launcher()
        if getattr(app, "relaunch_update", False) and os.access(start, os.X_OK):
            # Just updated: start through the new package's own launcher, which
            # picks the libraries that version shipped.
            os.execv(start, [start, "--library", target])
        os.execv(sys.executable, [sys.executable, "-s", "-m",
                                  "piklin.app", "--library", target])
    return status


if __name__ == "__main__":
    sys.exit(main())
