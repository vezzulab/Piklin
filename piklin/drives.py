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


def _windows_drives() -> list[Path]:
    """The drive letters in use, other than the one Windows is on."""
    import ctypes
    own = (os.environ.get("SystemDrive") or "C:").upper()[:2]
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    return [Path(f"{chr(65 + i)}:/") for i in range(26)
            if mask & (1 << i) and f"{chr(65 + i)}:" != own]


def _windows_label(root: str) -> str:
    """The name a drive was given (Explorer shows it before the letter)."""
    import ctypes
    label = ctypes.create_unicode_buffer(261)
    try:
        if ctypes.windll.kernel32.GetVolumeInformationW(
                ctypes.c_wchar_p(root), label, 261, None, None, None, None, 0):
            return label.value
    except (OSError, AttributeError):
        pass
    return ""


def is_removable(path: Path | str) -> bool:
    """Whether the drive holding this path is one that gets plugged in: a card
    or a USB stick (a removable drive), or a disk on a USB cable. Windows does
    not say so for the second kind - a USB hard disk is "fixed" to it - so the
    bus the disk sits on is asked. Always False off Windows."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes
    drive = Path(path).drive
    if len(drive) != 2 or drive[1] != ":":
        return False
    k32 = ctypes.windll.kernel32
    kind = k32.GetDriveTypeW(drive + "\\")
    if kind == 2:                                   # DRIVE_REMOVABLE
        return True
    if kind != 3:                                   # not a local disk
        return False
    k32.CreateFileW.restype = wintypes.HANDLE
    handle = k32.CreateFileW("\\.\\" + drive, 0, 3, None, 3, 0, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        return False
    try:
        query = (ctypes.c_ubyte * 12)()             # StorageDeviceProperty, PropertyStandardQuery
        out = (ctypes.c_ubyte * 1024)()
        got = wintypes.DWORD(0)
        if not k32.DeviceIoControl(handle, 0x2D1400, query, 12, out, 1024, ctypes.byref(got), None):
            return False
        bus = int.from_bytes(bytes(out[28:32]), "little")     # STORAGE_BUS_TYPE
        return bus == 7                                       # BusTypeUsb
    finally:
        k32.CloseHandle(handle)


def mount_points() -> list[Path]:
    """The folders in which a computer shows its drives."""
    if sys.platform == "win32":
        return _windows_drives()
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
    if sys.platform == "win32":
        # Another drive letter than Windows' own, or a network share.
        return bool(p.drive) and (p.drive.startswith("\\\\") or
                                  p.drive.upper() != (os.environ.get("SystemDrive") or "C:").upper())
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
    if sys.platform == "win32" and p.drive:
        label = "" if p.drive.startswith("\\\\") else _windows_label(p.drive + "\\")
        return f"{label} ({p.drive})" if label else p.drive
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
