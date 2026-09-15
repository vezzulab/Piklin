"""Start Piklin inside Piklin.app.

Contents/MacOS/Piklin runs this with the Python the app carries, once it has
chosen the runtime for this Mac's processor. Everything GTK reads - its
libraries, icons, fonts, image loaders - is pointed at that runtime and never
at anything installed on the Mac, then Piklin starts as /usr/bin/piklin
starts it on Linux.

PIKLIN_BOOT_CHECK=1 imports every module and exits instead: the build uses it
to check each runtime.
"""
import os
import sys

CONTENTS = os.environ["PIKLIN_BUNDLE_CONTENTS"]
RUNTIME = os.environ["PIKLIN_RUNTIME"]
RESOURCES = os.path.join(CONTENTS, "Resources")
LIB = os.path.join(RUNTIME, "lib")
SHARE = os.path.join(RUNTIME, "share")

sys.dont_write_bytecode = True           # the app is signed; nothing is written into it
sys.path[:0] = [os.path.join(RESOURCES, "app"), os.path.join(RUNTIME, "site-packages")]
sys.argv[0] = os.path.join(CONTENTS, "MacOS", "Piklin")

env = os.environ
env["XDG_DATA_DIRS"] = SHARE
env["GSETTINGS_SCHEMA_DIR"] = os.path.join(SHARE, "glib-2.0", "schemas")
env["GTK_DATA_PREFIX"] = RUNTIME
env["GI_TYPELIB_PATH"] = os.path.join(LIB, "girepository-1.0")
env["GIO_MODULE_DIR"] = os.path.join(LIB, "gio", "modules")
env["FONTCONFIG_FILE"] = os.path.join(RUNTIME, "etc", "fonts", "fonts.conf")
env["PANGOCAIRO_BACKEND"] = "fontconfig"

# python.org's Python looks for the certificates that let it trust a website
# in /Library/Frameworks, which no Mac has without that Python's installer:
# every HTTPS request - updates, WebDAV backups - failed as "no connection".
# The app carries certifi's list instead.
try:
    import certifi
    env.setdefault("SSL_CERT_FILE", certifi.where())
except ImportError:
    pass


def _pixbuf_loaders() -> str:
    """GdkPixbuf's list of image loaders names each loader by its full path,
    which depends on where the app is; it is written for this location, in
    the user's caches, whenever that changes."""
    template = os.path.join(LIB, "gdk-pixbuf-2.0", "2.10.0", "loaders.cache.in")
    caches = os.path.join(os.path.expanduser("~/Library/Caches/Piklin"),
                          os.path.basename(RUNTIME))
    target = os.path.join(caches, "loaders.cache")
    try:
        with open(template, encoding="utf-8") as f:
            text = f.read().replace("@RUNTIME@", RUNTIME)
        try:
            with open(target, encoding="utf-8") as f:
                if f.read() == text:
                    return target
        except OSError:
            pass
        os.makedirs(caches, exist_ok=True)
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, target)
    except OSError:
        pass
    return target


env["GDK_PIXBUF_MODULE_FILE"] = _pixbuf_loaders()

# An update swaps the new app in and keeps the old one until the new one
# starts: this is the new one starting.
import shutil  # noqa: E402

shutil.rmtree(os.path.join(os.path.dirname(os.path.dirname(CONTENTS)), ".Piklin-previous.app"),
              ignore_errors=True)

import gi  # noqa: E402

_repo = gi.Repository.get_default()
_repo.prepend_search_path(os.path.join(LIB, "girepository-1.0"))
_repo.prepend_library_path(LIB)

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
