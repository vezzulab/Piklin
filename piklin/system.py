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


# -- sharing the computer ---------------------------------------------------------
# Piklin's heavy work - thumbnails, reading photos, compressing, making videos
# smaller, backups - runs beside whatever else the person is doing, on anything
# from a new laptop to an old one with little memory. It takes only what the
# computer can spare, and gives way to everything else: that also keeps the
# processor from racing on a battery.
_MB = 1024 * 1024
# What one large photo can take while decoded and worked on: a 45-megapixel
# image as floating point, with a copy or two along the way.
_PER_IMAGE = 600 * _MB
_KEEP_FREE = 2048 * _MB            # for the system, Piklin's window and everything else
_CAPS = {"thumbs": 6, "probe": 8, "images": 4, "video": 4}
# A computer with little memory does fewer things at once: the background
# work takes longer, and the computer keeps answering. Measured on a laptop
# with 7 GB, six thumbnails and a video at once were what made Piklin's
# memory jump when it opened.
_CAPS_UP_TO_8_GB = {"thumbs": 2, "probe": 3, "images": 2, "video": 1}
_CAPS_UP_TO_16_GB = {"thumbs": 3, "probe": 4, "images": 3, "video": 2}


def total_memory() -> int:
    try:
        return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except (ValueError, OSError, AttributeError):
        return 8192 * _MB


_M_TRIM_THRESHOLD = -1
_M_MMAP_THRESHOLD = -3
_M_ARENA_MAX = -8
_malloc_tuned = False


def tune_malloc() -> bool:
    """Keep the memory Piklin uses close to what it really needs, on Linux.

    glibc gives every thread that allocates its own pool of memory, and
    Piklin's thumbnail, scanning and backup threads each kept theirs after
    their work was done: 632 MB of a library's 782 MB, measured on Linux Mint
    with 5,000 photos. Two pools, large blocks (a decoded thumbnail) handed
    straight back to the system when freed, and free memory at the top of a
    pool returned: 312 MB for the same library. Call before any thread
    starts; does nothing elsewhere."""
    global _malloc_tuned
    if _malloc_tuned or not IS_LINUX:
        return _malloc_tuned
    try:
        libc = ctypes.CDLL("libc.so.6")
        libc.mallopt.argtypes = [ctypes.c_int, ctypes.c_int]
        ok = (libc.mallopt(_M_ARENA_MAX, 2) == 1
              and libc.mallopt(_M_MMAP_THRESHOLD, 128 * 1024) == 1
              and libc.mallopt(_M_TRIM_THRESHOLD, 128 * 1024) == 1)
    except (OSError, AttributeError):
        return False
    _malloc_tuned = ok
    return ok


def release_memory() -> None:
    """Hand memory Piklin has finished with back to the computer.

    Freed memory otherwise stays with the process, ready for reuse: after a
    burst of thumbnails, the activity monitor went on showing gigabytes that
    Piklin no longer used. Cheap; call it when a burst of work ends."""
    try:
        if IS_LINUX:
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        elif IS_MAC:
            libc = ctypes.CDLL(None)
            libc.malloc_zone_pressure_relief.restype = ctypes.c_size_t
            libc.malloc_zone_pressure_relief(None, ctypes.c_size_t(0))
    except (OSError, AttributeError):
        pass


def work_budget(kind: str = "images") -> int:
    """How many pieces of ``kind`` of work may run at once on this computer:
    a core is always left for the person using it, and each large photo in
    progress needs its share of memory. ``kind``: thumbs, probe (reading
    photo details, which needs little memory), images or video."""
    spare_cores = max(1, (os.cpu_count() or 2) - 1)
    spare_memory = max(1, (total_memory() - _KEEP_FREE) // _PER_IMAGE)
    if kind == "probe":
        spare_memory = max(spare_memory, 2)
    memory = total_memory()
    caps = (_CAPS_UP_TO_8_GB if memory <= 8.5 * 1024 * _MB
            else _CAPS_UP_TO_16_GB if memory <= 16.5 * 1024 * _MB else _CAPS)
    return int(max(1, min(spare_cores, spare_memory, caps.get(kind, 2))))


_QOS_CLASS_UTILITY = 0x11


def lower_thread_priority() -> None:
    """Make the calling thread give way to everything else on the computer.
    Threads it starts afterwards (a video encoder's) inherit it."""
    import threading
    try:
        if IS_LINUX:
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
        elif IS_MAC:
            libsystem = ctypes.CDLL(None)
            libsystem.pthread_set_qos_class_self_np(ctypes.c_uint(_QOS_CLASS_UTILITY),
                                                    ctypes.c_int(0))
    except (OSError, AttributeError, ValueError):
        pass


def _nice_child() -> None:
    try:
        os.nice(10)
    except OSError:
        pass


# For subprocess's preexec_fn: a helper program (rclone) at low priority.
lower_process_priority = None if IS_WINDOWS else _nice_child


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


# -- Windows Credential Manager ---------------------------------------------------
# What the login Keychain is on a Mac and libsecret is on Linux: the place
# the system keeps passwords for an application, locked to the account.
# Backup passwords go there and nowhere else - never into Piklin's own
# files, which travel with the library.
_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2
_CRED_TARGET = "Piklin:{account}"


class _Credential(ctypes.Structure):
    _fields_ = [("Flags", ctypes.c_uint32), ("Type", ctypes.c_uint32),
                ("TargetName", ctypes.c_wchar_p), ("Comment", ctypes.c_wchar_p),
                ("LastWritten", ctypes.c_uint64),
                ("CredentialBlobSize", ctypes.c_uint32),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
                ("Persist", ctypes.c_uint32), ("AttributeCount", ctypes.c_uint32),
                ("Attributes", ctypes.c_void_p), ("TargetAlias", ctypes.c_wchar_p),
                ("UserName", ctypes.c_wchar_p)]


def _advapi():
    if not IS_WINDOWS:
        raise OSError("not Windows")
    return ctypes.WinDLL("advapi32", use_last_error=True)


def credential_store(account: str, secret: str) -> bool:
    """Put a password in the Windows Credential Manager."""
    try:
        api = _advapi()
    except (OSError, AttributeError):
        return False
    blob = secret.encode("utf-16-le")
    cred = _Credential(
        Flags=0, Type=_CRED_TYPE_GENERIC,
        TargetName=_CRED_TARGET.format(account=account),
        Comment="Piklin backup", LastWritten=0,
        CredentialBlobSize=len(blob),
        CredentialBlob=ctypes.cast(ctypes.create_string_buffer(blob, len(blob)),
                                   ctypes.POINTER(ctypes.c_char)),
        Persist=_CRED_PERSIST_LOCAL_MACHINE, AttributeCount=0, Attributes=None,
        TargetAlias=None, UserName=account)
    try:
        return bool(api.CredWriteW(ctypes.byref(cred), 0))
    except Exception:
        return False


def credential_load(account: str) -> str | None:
    """The password stored for ``account``, or None."""
    try:
        api = _advapi()
    except (OSError, AttributeError):
        return None
    ptr = ctypes.POINTER(_Credential)()
    try:
        if not api.CredReadW(_CRED_TARGET.format(account=account),
                             _CRED_TYPE_GENERIC, 0, ctypes.byref(ptr)):
            return None
        cred = ptr.contents
        size = int(cred.CredentialBlobSize)
        if not size:
            return ""
        raw = ctypes.string_at(cred.CredentialBlob, size)
        return raw.decode("utf-16-le", "replace")
    except Exception:
        return None
    finally:
        if ptr:
            try:
                api.CredFree(ptr)
            except Exception:
                pass


def credential_forget(account: str) -> bool:
    try:
        api = _advapi()
    except (OSError, AttributeError):
        return False
    try:
        return bool(api.CredDeleteW(_CRED_TARGET.format(account=account),
                                    _CRED_TYPE_GENERIC, 0))
    except Exception:
        return False


def credential_available() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        _advapi()
        return True
    except (OSError, AttributeError):
        return False


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


def on_battery() -> bool:
    """Whether the computer is running on its battery right now. A desktop,
    or anything that can't be read, counts as plugged in."""
    import subprocess
    from pathlib import Path
    try:
        if IS_MAC:
            out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True,
                                 timeout=5).stdout
            return "'Battery Power'" in out
        if IS_LINUX:
            supplies = Path("/sys/class/power_supply")
            if not supplies.is_dir():
                return False
            mains, batteries = [], []
            for s in supplies.iterdir():
                kind = (s / "type").read_text().strip() if (s / "type").exists() else ""
                if kind == "Mains":
                    mains.append((s / "online").read_text().strip() == "1"
                                 if (s / "online").exists() else False)
                elif kind == "Battery":
                    batteries.append(s)
            return bool(batteries) and not any(mains)
        if IS_WINDOWS:
            # GetSystemPowerStatus: ACLineStatus 0 means the mains are
            # not supplying it, 255 that the machine does not know.
            class _Power(ctypes.Structure):
                _fields_ = [("ACLineStatus", ctypes.c_ubyte),
                            ("BatteryFlag", ctypes.c_ubyte),
                            ("BatteryLifePercent", ctypes.c_ubyte),
                            ("SystemStatusFlag", ctypes.c_ubyte),
                            ("BatteryLifeTime", ctypes.c_uint32),
                            ("BatteryFullLifeTime", ctypes.c_uint32)]
            status = _Power()
            if ctypes.WinDLL("kernel32").GetSystemPowerStatus(ctypes.byref(status)):
                # 128 in BatteryFlag means there is no battery at all.
                return status.ACLineStatus == 0 and status.BatteryFlag != 128
            return False
    except (OSError, ValueError, AttributeError, subprocess.SubprocessError):
        pass
    return False
