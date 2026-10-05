"""Putting Piklin in the applications menu when it runs from an AppImage.

An AppImage is a file that runs where it is; nothing installs it, so no menu
entry or icon is ever made. Piklin makes them itself, once the person says
yes: a launcher in ~/.local/share/applications that starts this very file,
and the icon beside it. The launcher is kept pointing at the AppImage's
current place, which is also where an update replaces it.

Nothing is written for Piklin from a .deb or a Mac app, which have their own.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .paths import APP_ID

DATA = Path(__file__).resolve().parent.parent / "data"
# In the file Piklin writes, so it never touches a launcher it did not make.
MARKER = "# Written by Piklin so this AppImage is in the applications menu."


def appimage() -> Path | None:
    """The AppImage file this Piklin runs from, or None."""
    if not sys.platform.startswith("linux"):
        return None
    path = os.environ.get("PIKLIN_APPIMAGE")
    if not path or not Path(path).is_file():
        return None
    return Path(path)


def _share() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def entry_path() -> Path:
    return _share() / "applications" / "piklin.desktop"


def icon_path() -> Path:
    return _share() / "icons" / "hicolor" / "256x256" / "apps" / "piklin.png"


def _quoted(path: Path) -> str:
    """The Exec value for a path: quoted, with the characters the desktop
    entry format reserves escaped."""
    # A backslash is escaped twice over: once for the quoting, and once more
    # because the format reads string escapes before it reads the quotes.
    text = str(path).replace("\\", "\\" * 4)
    for ch in ('"', "`", "$"):
        text = text.replace(ch, "\\" + ch)
    return f'"{text}"'


def _bundled_launcher() -> Path:
    """The launcher Piklin ships: in the source tree's data folder, and at the
    top of an AppImage, where the AppImage tools expect it."""
    candidates = [DATA / "piklin.desktop"]
    if os.environ.get("PIKLIN_APPDIR"):
        candidates.append(Path(os.environ["PIKLIN_APPDIR"]) / "piklin.desktop")
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError("piklin.desktop")


def entry_text(image: Path, icon: Path) -> str:
    """The bundled launcher, pointed at ``image``: it names the command
    ``piklin``, which only a package puts on the PATH."""
    lines = []
    for line in _bundled_launcher().read_text(encoding="utf-8").splitlines():
        if line.startswith("TryExec="):
            continue
        if line.startswith("Exec="):
            line = f"Exec={_quoted(image)} %F"
        elif line.startswith("Icon="):
            line = f"Icon={icon}"
        elif line.startswith("StartupWMClass="):
            # the name the window announces itself by, which is how the desktop
            # finds this launcher and its icon for it
            line = f"StartupWMClass={APP_ID}"
        lines.append(line)
    return MARKER + "\n" + "\n".join(lines) + "\n"


def installed() -> bool:
    try:
        return MARKER in entry_path().read_text(encoding="utf-8")
    except OSError:
        return False


def current() -> bool:
    """Installed, and starting the AppImage that is running now."""
    image = appimage()
    if image is None or not installed():
        return False
    try:
        return entry_path().read_text(encoding="utf-8") == entry_text(image, icon_path())
    except OSError:
        return False


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def install() -> bool:
    """Make the launcher and the icon. False when there is no AppImage, or
    the files could not be written."""
    image = appimage()
    if image is None:
        return False
    try:
        _write(icon_path(), (DATA / "icons" / "piklin-256.png").read_bytes())
        _write(entry_path(), entry_text(image, icon_path()).encode("utf-8"))
    except OSError:
        return False
    # Menus notice a changed folder by themselves; this is only to hurry them.
    try:
        subprocess.run(["update-desktop-database", str(entry_path().parent)],
                       capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        pass
    return True


def remove() -> None:
    """Take the launcher and the icon away, if they are Piklin's own."""
    if installed():
        for path in (entry_path(), icon_path()):
            try:
                path.unlink()
            except OSError:
                pass


def refresh() -> None:
    """The launcher made earlier follows the AppImage when it was moved."""
    if installed() and appimage() is not None and not current():
        install()
