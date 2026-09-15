"""Assemble packaging/AppDir, the contents of the AppImage.

Run by packaging/build-appimage.sh inside Ubuntu 24.04, after build-deb.sh
has staged the .deb's tree in packaging/deb-root. It adds Ubuntu's own
Python 3.12, PyGObject, GTK 4, libadwaita and every library they need,
except the ones an AppImage must take from the computer (glibc, graphics
drivers, fonts, sound: the AppImage project's excludelist).

Each library is given a search path relative to itself, so the AppImage
needs no LD_LIBRARY_PATH - which would also reach every program Piklin
opens and break them.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

PACKAGING = Path(__file__).resolve().parent.parent
STAGE = PACKAGING / "deb-root"
APPDIR = PACKAGING / "AppDir"
TOOLS = PACKAGING / "appimage-cache"
TRIPLE = os.environ["TRIPLE"]
SYSLIB = Path("/usr/lib") / TRIPLE
PY = "python3.12"

USR = APPDIR / "usr"
LIB = USR / "lib"
SHARE = USR / "share"

# Needed by the Python standard library, never run by Piklin.
PY_SKIP = {"test", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3",
           "__pycache__", "venv", "pydoc_data"}
# Nothing beyond the AppImage project's own list. GTK 4 is linked against
# the Vulkan loader, which not every system has (Fedora without a Vulkan
# driver package), and the bundled loader still finds the computer's own
# drivers where there are some.
EXTRA_EXCLUDE: set[str] = set()
# The type libraries Piklin uses. Copying every one Ubuntu has also brought
# the libraries they name (systemd, curl, Kerberos...), none of them needed.
TYPELIBS = ("GLib-2.0", "GObject-2.0", "Gio-2.0", "GModule-2.0", "GIRepository-2.0",
            "Gtk-4.0", "Gdk-4.0", "GdkX11-4.0", "GdkWayland-4.0", "Gsk-4.0",
            "Graphene-1.0", "Pango-1.0", "PangoCairo-1.0", "PangoFT2-1.0",
            "PangoFc-1.0", "PangoOT-1.0", "HarfBuzz-0.0", "cairo-1.0", "freetype2-2.0",
            "fontconfig-2.0", "GdkPixbuf-2.0", "Adw-1", "Secret-1", "xlib-2.0",
            "xfixes-4.0", "xft-2.0", "xrandr-1.3", "win32-1.0")


def say(text: str) -> None:
    print(f"\033[1;36m==>\033[0m {text}", flush=True)


def excluded() -> set[str]:
    names = set(EXTRA_EXCLUDE)
    for line in (TOOLS / "excludelist").read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.add(line)
    return names


def copytree(src: Path, dst: Path, skip: set[str] = frozenset()) -> None:
    shutil.copytree(src, dst, symlinks=True, dirs_exist_ok=True,
                    ignore=lambda _d, names: [n for n in names if n in skip])


def is_elf(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"\x7fELF"
    except OSError:
        return False


def needed(path: Path) -> list[Path]:
    """The libraries ``path`` loads, as the computer resolves them."""
    out = subprocess.run(["ldd", str(path)], capture_output=True, text=True).stdout
    found = []
    for line in out.splitlines():
        match = re.search(r"=>\s+(/\S+)", line)
        if match:
            found.append(Path(match.group(1)))
    return found


def main() -> None:
    shutil.rmtree(APPDIR, ignore_errors=True)
    for d in (USR / "bin", LIB, SHARE):
        d.mkdir(parents=True)

    say("Piklin, from the .deb's tree")
    copytree(STAGE / "usr/share/piklin", SHARE / "piklin")
    copytree(STAGE / "usr/lib/piklin", LIB / "piklin")
    (LIB / "piklin" / "piklin-update").unlink(missing_ok=True)   # that helper is the .deb's
    copytree(STAGE / "usr/share/doc/piklin", SHARE / "doc/piklin")
    copytree(STAGE / "usr/share/icons", SHARE / "icons")
    shutil.copy2(PACKAGING / "appimage/boot.py", LIB / "piklin/boot.py")

    say("Python 3.12 and PyGObject")
    shutil.copy2(f"/usr/bin/{PY}", USR / "bin" / PY)
    shutil.copytree(f"/usr/lib/{PY}", LIB / PY, symlinks=False, ignore_dangling_symlinks=True,
                    ignore=lambda d, names: [n for n in names if n in PY_SKIP
                                             or n.startswith("config-")])
    dist = LIB / "python3" / "dist-packages"
    dist.mkdir(parents=True)
    system_dist = Path("/usr/lib/python3/dist-packages")
    for name in ("gi", "cairo"):
        copytree(system_dist / name, dist / name, skip={"__pycache__"})
    for so in system_dist.glob("_cffi_backend*.so"):
        shutil.copy2(so, dist / so.name)

    say("GTK 4, libadwaita: type libraries, image loaders, settings, icons")
    (LIB / "girepository-1.0").mkdir()
    for name in TYPELIBS:
        typelib = SYSLIB / "girepository-1.0" / f"{name}.typelib"
        if typelib.exists():
            shutil.copy2(typelib, LIB / "girepository-1.0" / typelib.name)
    copytree(SYSLIB / "gdk-pixbuf-2.0", LIB / "gdk-pixbuf-2.0")
    copytree(Path("/usr/share/glib-2.0/schemas"), SHARE / "glib-2.0/schemas")
    copytree(Path("/usr/share/icons/Adwaita"), SHARE / "icons/Adwaita")
    shutil.copy2("/usr/share/icons/hicolor/index.theme", SHARE / "icons/hicolor/index.theme")

    loaders = sorted((LIB / "gdk-pixbuf-2.0/2.10.0/loaders").glob("*.so"))
    query = SYSLIB / "gdk-pixbuf-2.0/gdk-pixbuf-query-loaders"
    cache = subprocess.run([str(query), *map(str, loaders)], capture_output=True,
                           text=True, check=True).stdout
    (LIB / "gdk-pixbuf-2.0/2.10.0/loaders.cache.in").write_text(
        cache.replace(str(APPDIR), "@APPDIR@"))
    (LIB / "gdk-pixbuf-2.0/2.10.0/loaders.cache").unlink(missing_ok=True)

    say("Libraries")
    skip = excluded()
    # What loads libraries: Python and its modules, the image loaders, and
    # every library a type library names (GTK, libadwaita, libsecret...).
    seeds = [USR / "bin" / PY, *loaders]
    seeds += [p for p in (LIB / PY / "lib-dynload").glob("*.so")]
    seeds += [p for p in dist.rglob("*.so")]
    for typelib in (LIB / "girepository-1.0").glob("*.typelib"):
        for name in set(re.findall(rb"lib[\w.+-]+?\.so(?:\.\d+)*", typelib.read_bytes())):
            path = SYSLIB / name.decode()
            if path.exists():
                seeds.append(path)
    copied: set[str] = set()
    todo = list(seeds)
    while todo:
        for dep in needed(todo.pop()):
            if dep.name in skip or dep.name.startswith("ld-linux") or dep.name in copied:
                continue
            copied.add(dep.name)
            shutil.copy2(dep.resolve(), LIB / dep.name)
            todo.append(dep)
    for path in seeds:                      # a type library's own libraries
        if path.parent == SYSLIB and path.name not in copied and path.name not in skip:
            copied.add(path.name)
            shutil.copy2(path.resolve(), LIB / path.name)
    print(f"   {len(copied)} libraries")

    say("Search paths relative to each library")
    wheel_libs = LIB / "piklin"               # auditwheel already set theirs
    for path in USR.rglob("*"):
        if (path.is_symlink() or not path.is_file() or wheel_libs in path.parents
                or not is_elf(path)):
            continue
        rel = os.path.relpath(LIB, path.parent)
        rpath = "$ORIGIN" if rel == "." else f"$ORIGIN/{rel}"
        subprocess.run(["patchelf", "--set-rpath", rpath, str(path)], check=True)

    say("AppRun, desktop entry and icon")
    shutil.copy2(PACKAGING / "appimage/AppRun", APPDIR / "AppRun")
    os.chmod(APPDIR / "AppRun", 0o755)
    shutil.copy2(STAGE / "usr/share/applications/piklin.desktop", APPDIR / "piklin.desktop")
    shutil.copy2(STAGE / "usr/share/icons/hicolor/256x256/apps/piklin.png", APPDIR / "piklin.png")
    os.symlink("piklin.png", APPDIR / ".DirIcon")
    for path in APPDIR.rglob("*"):
        if path.is_dir() and not path.is_symlink():
            os.chmod(path, 0o755)


if __name__ == "__main__":
    main()
