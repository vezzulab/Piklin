"""Start Piklin inside its AppImage.

AppRun runs this with the Python the AppImage carries. GTK, libadwaita and
their image loaders come from the AppImage; the computer's own settings,
schemas and icons are still read first, so Piklin looks like every other app
on that desktop. Nothing set here is passed on to programs Piklin opens (a
file manager, a browser): those get the computer's environment as it was.

PIKLIN_BOOT_CHECK=1 imports every module and exits instead: the build uses it
to check the AppImage.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))              # usr/lib/piklin
APPDIR = os.environ.get("APPDIR") or os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
USR = os.path.join(APPDIR, "usr")
LIB = os.path.join(USR, "lib")
SHARE = os.path.join(USR, "share")
PYVER = "python%d.%d" % sys.version_info[:2]

sys.dont_write_bytecode = True           # the AppImage is read-only
sys.path[:0] = [os.path.join(SHARE, "piklin"), os.path.join(LIB, "piklin", PYVER),
                os.path.join(LIB, "piklin", "common"),
                os.path.join(LIB, "python3", "dist-packages")]

env = os.environ
if env.get("APPIMAGE"):
    # Which file to replace on an update, and to start again afterwards.
    env["PIKLIN_APPIMAGE"] = env["APPIMAGE"]
    sys.argv[0] = env["APPIMAGE"]
env["PIKLIN_APPDIR"] = APPDIR

# Read only while GTK starts, then taken out again (see the end).
_host_data_dirs = env.get("XDG_DATA_DIRS")
_startup_only = {
    "GI_TYPELIB_PATH": os.path.join(LIB, "girepository-1.0"),
    # The computer's schemas and icons first; the AppImage's own after them,
    # for whatever the computer lacks.
    "XDG_DATA_DIRS": (_host_data_dirs or "/usr/local/share:/usr/share") + os.pathsep + SHARE,
}


def _pixbuf_loaders() -> str:
    """GdkPixbuf's list of image loaders names each loader by its full path,
    which changes every time the AppImage is opened; it is written for this
    run, in the user's cache."""
    template = os.path.join(LIB, "gdk-pixbuf-2.0", "2.10.0", "loaders.cache.in")
    cache = os.path.join(env.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "piklin")
    target = os.path.join(cache, "appimage-loaders.cache")
    try:
        with open(template, encoding="utf-8") as f:
            text = f.read().replace("@APPDIR@", APPDIR)
        os.makedirs(cache, exist_ok=True)
        tmp = "%s.%d" % (target, os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, target)
    except OSError:
        pass
    return target


_startup_only["GDK_PIXBUF_MODULE_FILE"] = _pixbuf_loaders()
env.update(_startup_only)

import gi  # noqa: E402


def _library_path() -> None:
    """GTK, libadwaita and the rest are loaded by name when their type
    library is first used. A computer that has its own copies would hand
    over those instead, and one without them would find nothing: the
    AppImage's own folder is searched first. (_gi has already loaded
    libgirepository from the AppImage, so this reaches that copy.)"""
    import ctypes
    repo = ctypes.CDLL(os.path.join(LIB, "libgirepository-1.0.so.1"))
    repo.g_irepository_prepend_library_path.argtypes = [ctypes.c_char_p]
    repo.g_irepository_prepend_library_path(LIB.encode())


_library_path()
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "4.0")
from gi.repository import GdkPixbuf, Gtk  # noqa: E402,F401

GdkPixbuf.Pixbuf.get_formats()           # the loaders are read now, once
try:
    Gtk.Settings.get_default()           # and GTK's schemas
except Exception:
    pass

# Programs Piklin opens must not inherit any of it: a file manager given the
# AppImage's older image loaders or schemas could fail to start.
for _name in _startup_only:
    env.pop(_name, None)
if _host_data_dirs is not None:
    env["XDG_DATA_DIRS"] = _host_data_dirs

if env.get("PIKLIN_BOOT_CHECK"):
    import importlib
    import pkgutil
    import platform

    import piklin
    failed = []
    for mod in pkgutil.walk_packages(piklin.__path__, "piklin."):
        try:
            importlib.import_module(mod.name)
        except Exception as exc:              # report every one, not just the first
            failed.append(f"{mod.name}: {exc}")
    from piklin import imageio
    from piklin.ui import player
    print(f"{platform.machine()}: modules {'OK' if not failed else 'FAILED'}; "
          f"codecs missing: {imageio.missing_codecs() or 'none'}; "
          f"video: {player.playback_engine()}")
    for line in failed:
        print("  " + line)
    sys.exit(1 if failed else 0)

from piklin.app import main  # noqa: E402

sys.exit(main(sys.argv[1:]))
