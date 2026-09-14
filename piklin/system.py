"""What differs between the systems Piklin runs on, kept in one place.

Everything else in Piklin is the same code on every system. Where a system
does something its own way - where passwords are kept, how a file carries a
private note - the rest of the app asks here, so Linux, macOS and later
Windows behave the same from the outside.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys

IS_LINUX = sys.platform.startswith("linux")
IS_MAC = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"


# -- extended attributes ------------------------------------------------------
# A short note stored on a file itself, such as the size of the camera file a
# smaller library copy was made from. Linux has os.getxattr; Python on a Mac
# does not, though macOS has the same calls (with two extra arguments).
_libc = None


def _mac_libc():
    global _libc
    if _libc is None:
        _libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        _libc.setxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                                   ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
        _libc.setxattr.restype = ctypes.c_int
        _libc.getxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                                   ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
        _libc.getxattr.restype = ctypes.c_ssize_t
    return _libc


def set_xattr(path, name: str, value: bytes) -> None:
    """Store ``value`` on the file. Raises OSError when it can't."""
    if IS_MAC:
        rc = _mac_libc().setxattr(os.fsencode(path), name.encode(), value, len(value), 0, 0)
        if rc != 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err), str(path))
        return
    if not hasattr(os, "setxattr"):
        raise OSError("extended attributes are not supported here")
    os.setxattr(path, name, value)


def get_xattr(path, name: str) -> bytes:
    """The value stored on the file. Raises OSError when there is none."""
    if IS_MAC:
        libc = _mac_libc()
        raw, key = os.fsencode(path), name.encode()
        size = libc.getxattr(raw, key, None, 0, 0, 0)
        if size < 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err), str(path))
        buf = ctypes.create_string_buffer(size)
        size = libc.getxattr(raw, key, buf, size, 0, 0)
        if size < 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err), str(path))
        return buf.raw[:size]
    if not hasattr(os, "getxattr"):
        raise OSError("extended attributes are not supported here")
    return os.getxattr(path, name)


# -- passwords ----------------------------------------------------------------
# On a Mac, backup passwords go in the login Keychain, as the system keyring
# holds them on Linux (see remote.py). Never in a settings file.
_KEYCHAIN_SERVICE = b"Piklin backup"
_ERR_NOT_FOUND = -25300
_security = None


def _mac_security():
    global _security
    if _security is None:
        sec = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        u32, vp = ctypes.c_uint32, ctypes.c_void_p
        sec.SecKeychainFindGenericPassword.argtypes = [
            vp, u32, ctypes.c_char_p, u32, ctypes.c_char_p,
            ctypes.POINTER(u32), ctypes.POINTER(vp), ctypes.POINTER(vp)]
        sec.SecKeychainAddGenericPassword.argtypes = [
            vp, u32, ctypes.c_char_p, u32, ctypes.c_char_p, u32, ctypes.c_char_p,
            ctypes.POINTER(vp)]
        sec.SecKeychainItemModifyAttributesAndData.argtypes = [vp, vp, u32, ctypes.c_char_p]
        sec.SecKeychainItemFreeContent.argtypes = [vp, vp]
        for fn in (sec.SecKeychainFindGenericPassword, sec.SecKeychainAddGenericPassword,
                   sec.SecKeychainItemModifyAttributesAndData, sec.SecKeychainItemFreeContent):
            fn.restype = ctypes.c_int32
        cf.CFRelease.argtypes = [vp]
        _security = (sec, cf)
    return _security


def keychain_available() -> bool:
    if not IS_MAC:
        return False
    try:
        _mac_security()
        return True
    except OSError:
        return False


def keychain_store(account: str, secret: str) -> bool:
    """Put a password in the login Keychain, replacing one already there."""
    try:
        sec, cf = _mac_security()
    except OSError:
        return False
    acct, data = account.encode(), secret.encode()
    item = ctypes.c_void_p()
    status = sec.SecKeychainFindGenericPassword(
        None, len(_KEYCHAIN_SERVICE), _KEYCHAIN_SERVICE, len(acct), acct,
        None, None, ctypes.byref(item))
    try:
        if status == 0:
            return sec.SecKeychainItemModifyAttributesAndData(item, None, len(data), data) == 0
        if status != _ERR_NOT_FOUND:
            return False
        return sec.SecKeychainAddGenericPassword(
            None, len(_KEYCHAIN_SERVICE), _KEYCHAIN_SERVICE, len(acct), acct,
            len(data), data, None) == 0
    finally:
        if item.value:
            cf.CFRelease(item)


def keychain_load(account: str) -> str | None:
    """The password stored for ``account``, or None."""
    try:
        sec, _cf = _mac_security()
    except OSError:
        return None
    acct = account.encode()
    length = ctypes.c_uint32()
    data = ctypes.c_void_p()
    status = sec.SecKeychainFindGenericPassword(
        None, len(_KEYCHAIN_SERVICE), _KEYCHAIN_SERVICE, len(acct), acct,
        ctypes.byref(length), ctypes.byref(data), None)
    if status != 0 or not data.value:
        return None
    try:
        return ctypes.string_at(data, length.value).decode("utf-8", "replace")
    finally:
        sec.SecKeychainItemFreeContent(None, data)


# -- drives -----------------------------------------------------------------------
# USB drives, memory cards and cameras in mass-storage mode. On Linux GIO
# reports them (the desktop mounts them in /media); a Mac mounts them in
# /Volumes, which GIO does not look at.
_MNT_ROOTFS = 0x00004000
_MNT_DONTBROWSE = 0x00100000


class _Statfs(ctypes.Structure):
    _fields_ = [("f_bsize", ctypes.c_uint32), ("f_iosize", ctypes.c_int32),
                ("f_blocks", ctypes.c_uint64), ("f_bfree", ctypes.c_uint64),
                ("f_bavail", ctypes.c_uint64), ("f_files", ctypes.c_uint64),
                ("f_ffree", ctypes.c_uint64), ("f_fsid", ctypes.c_int32 * 2),
                ("f_owner", ctypes.c_uint32), ("f_type", ctypes.c_uint32),
                ("f_flags", ctypes.c_uint32), ("f_fssubtype", ctypes.c_uint32),
                ("f_fstypename", ctypes.c_char * 16), ("f_mntonname", ctypes.c_char * 1024),
                ("f_mntfromname", ctypes.c_char * 1024), ("f_flags_ext", ctypes.c_uint32),
                ("f_reserved", ctypes.c_uint32 * 7)]


def external_volumes() -> list[tuple[str, str]]:
    """Disks the system mounted for the person, as (name, path), where GIO
    does not already report them. Empty on Linux."""
    if not IS_MAC:
        return []
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"))
    except OSError:
        return []
    libc.getmntinfo_r_np.argtypes = [ctypes.POINTER(ctypes.POINTER(_Statfs)), ctypes.c_int]
    libc.getmntinfo_r_np.restype = ctypes.c_int
    libc.free.argtypes = [ctypes.c_void_p]
    table = ctypes.POINTER(_Statfs)()
    count = libc.getmntinfo_r_np(ctypes.byref(table), 2)          # MNT_NOWAIT
    found = []
    try:
        for i in range(max(count, 0)):
            fs = table[i]
            where = fs.f_mntonname.decode("utf-8", "replace")
            source = fs.f_mntfromname.decode("utf-8", "replace")
            # A physical disk under /Volumes: not the startup disk, not a
            # network share or a cloud drive (those are not devices on Linux
            # either), not one the system hides.
            if (where.startswith("/Volumes/") and source.startswith("/dev/disk")
                    and not fs.f_flags & (_MNT_ROOTFS | _MNT_DONTBROWSE)):
                found.append((os.path.basename(where), where))
    finally:
        if table:
            libc.free(ctypes.cast(table, ctypes.c_void_p))
    return found


def volumes_folder() -> str | None:
    """The folder whose contents change as drives come and go, when GIO
    does not announce them itself."""
    return "/Volumes" if IS_MAC else None


# -- language -------------------------------------------------------------------
def preferred_languages() -> list[str]:
    """The languages chosen in the Mac's settings, most preferred first, as
    gettext names ("es_ES", "en_US").

    An app opened from the Finder has no LANG to read, which is where Linux
    keeps the same choice. Empty anywhere else.
    """
    if not IS_MAC:
        return []
    try:
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    except OSError:
        return []
    vp = ctypes.c_void_p
    cf.CFLocaleCopyPreferredLanguages.restype = vp
    cf.CFArrayGetCount.argtypes = [vp]
    cf.CFArrayGetCount.restype = ctypes.c_long
    cf.CFArrayGetValueAtIndex.argtypes = [vp, ctypes.c_long]
    cf.CFArrayGetValueAtIndex.restype = vp
    cf.CFStringGetCString.argtypes = [vp, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
    cf.CFStringGetCString.restype = ctypes.c_bool
    cf.CFRelease.argtypes = [vp]
    utf8 = 0x08000100
    array = cf.CFLocaleCopyPreferredLanguages()
    if not array:
        return []
    found = []
    try:
        for i in range(cf.CFArrayGetCount(array)):
            buf = ctypes.create_string_buffer(64)
            if cf.CFStringGetCString(cf.CFArrayGetValueAtIndex(array, i), buf, len(buf), utf8):
                found.append(buf.value.decode().replace("-", "_"))
    finally:
        cf.CFRelease(array)
    return found


# -- installing helpers ---------------------------------------------------------
def rclone_install_command() -> str:
    """How to install rclone on this system, as typed in a terminal."""
    return "brew install rclone" if IS_MAC else "sudo apt install rclone"
