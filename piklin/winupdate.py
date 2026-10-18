"""Updating Piklin on Windows.

Linux and a Mac replace Piklin's files while it runs. Windows does not allow
that: the files of a program in use are locked. So an update goes in turns:

1. The Piklin that is open downloads the .msi and checks it - signed with
   Vezzu Studio's release key, and really Piklin at that version (``stage``).
2. Still in the window the person just clicked in, Piklin asks Windows for
   permission: the one prompt, a UAC window naming Piklin, in front where it
   cannot be missed (``authorize``). A Piklin run with that permission checks
   the .msi AGAIN, on a copy in a folder only an administrator can change
   (``apply``) - so nothing running as the person can swap the file between
   the check and the installation - and leaves a script that waits for Piklin
   to close, then runs msiexec.
3. Piklin closes when nothing would be cut short. A small script
   (winupdate.ps1) waits for msiexec to finish and opens Piklin again, in the
   new version. What happened is written to a file the new Piklin reads
   (``take_result``).

Nothing here does anything on a system other than Windows.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from . import updates
from .updates import UpdateError

# Must be the UpgradeCode in packaging/windows/piklin.wxs: the same product, upgraded.
UPGRADE_CODE = "{6D1F3A52-8B0E-4C7A-9F43-2A5E1B7C9D10}"
PRODUCT_NAME = "Piklin"
_NO_WINDOW = 0x08000000
_NEW_GROUP = 0x00000200
_BREAKAWAY = 0x01000000        # CREATE_BREAKAWAY_FROM_JOB
# The only exit codes msiexec reports as done: 0, and 3010 (done, restart wanted).
_DONE = (0, 3010)

_staged: Path | None = None


# -- where things are --------------------------------------------------------------
def root() -> Path | None:
    """The Piklin folder, when this Piklin runs from its installation."""
    found = os.environ.get("PIKLIN_APPDIR")
    if not found:
        return None
    path = Path(found)
    return path if (path / "runtime" / "bin" / "Piklin.exe").is_file() else None


def launcher() -> str:
    base = root()
    return str(base / "runtime" / "bin" / "Piklin.exe") if base else ""


def _powershell() -> str:
    system = os.environ.get("SystemRoot") or r"C:\Windows"
    return str(Path(system) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")


def available() -> bool:
    """Whether this Piklin can update itself: it is the installed program (not
    the source tree), and carries the key that checks an update."""
    if sys.platform != "win32":
        return False
    base = root()
    return bool(base and (base / "release-key.pem").is_file() and Path(_powershell()).is_file())


def msi_names(version: str) -> tuple[str, str, str]:
    msi = f"Piklin-{version}-x64.msi"
    return msi, msi + ".sha256", msi + ".sha256.sig"


def _local() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Piklin"


def updates_dir() -> Path:
    return _local() / "Updates"


def result_file() -> Path:
    return _local() / "update-result.json"


def installed_version() -> str:
    """The version this Piklin is, read from its own file rather than by
    importing the whole application (the administrator's step must stay small)."""
    text = (Path(__file__).resolve().parent / "app.py").read_text(encoding="utf-8")
    match = re.search(r'^VERSION = "([^"]+)"', text, re.M)
    return match.group(1) if match else "0"


# -- the package -------------------------------------------------------------------
def msi_properties(path: Path) -> dict[str, str]:
    """Properties of a .msi, read without installing anything."""
    msi = ctypes.windll.msi
    u32, p32 = ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)
    msi.MsiOpenDatabaseW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, p32]
    msi.MsiDatabaseOpenViewW.argtypes = [u32, ctypes.c_wchar_p, p32]
    msi.MsiViewExecute.argtypes = [u32, u32]
    msi.MsiViewFetch.argtypes = [u32, p32]
    msi.MsiRecordGetStringW.argtypes = [u32, u32, ctypes.c_wchar_p, p32]
    msi.MsiCloseHandle.argtypes = [u32]
    db = u32(0)
    code = msi.MsiOpenDatabaseW(str(path), None, ctypes.byref(db))        # read only
    if code != 0:
        raise UpdateError("verification", f"not a Windows Installer package (error {code})")
    found: dict[str, str] = {}
    try:
        for name in ("ProductName", "ProductVersion", "UpgradeCode", "ProductCode"):
            view, record = u32(0), u32(0)
            query = f"SELECT `Value` FROM `Property` WHERE `Property`='{name}'"
            if msi.MsiDatabaseOpenViewW(db.value, query, ctypes.byref(view)) != 0:
                continue
            try:
                if msi.MsiViewExecute(view.value, 0) == 0 and msi.MsiViewFetch(view.value, ctypes.byref(record)) == 0:
                    size = u32(512)
                    buf = ctypes.create_unicode_buffer(512)
                    if msi.MsiRecordGetStringW(record.value, 1, buf, ctypes.byref(size)) == 0:
                        found[name] = buf.value
                    msi.MsiCloseHandle(record.value)
            finally:
                msi.MsiCloseHandle(view.value)
        return found
    finally:
        msi.MsiCloseHandle(db.value)


def check_package(msi: Path, version: str) -> None:
    """It must be Piklin, the same product that is installed, at this version."""
    props = msi_properties(msi)
    if (props.get("ProductName") != PRODUCT_NAME
            or props.get("UpgradeCode", "").upper() != UPGRADE_CODE
            or props.get("ProductVersion") != version):
        raise UpdateError("verification", f"the installer is not Piklin {version} ({props})")


def _require_newer(version: str, new_build: str) -> None:
    """Newer, or the same version published again as a different build. Never
    an older one: a signed installer from last year must not take its place."""
    if not updates.update_available(updates.Release(version, "", build=new_build),
                                    installed_version()):
        raise UpdateError("not-newer", f"Piklin {version} is already installed")


# -- 1. in the Piklin that is open --------------------------------------------------
def stage(version: str) -> Path:
    """Download and check the update. Raises UpdateError. Nothing is installed:
    the installer waits in a folder of its own until Piklin has closed."""
    global _staged
    if not updates._VERSION_RE.match(version):
        raise UpdateError("install", "not a version number")
    base = root()
    if base is None or not available():
        raise UpdateError("install", "this Piklin cannot update itself where it is")
    try:
        key_pem = (base / "release-key.pem").read_text()
    except OSError as exc:
        raise UpdateError("verification", f"no release key in Piklin: {exc}")
    work = updates_dir() / version
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    names = msi_names(version)
    url = f"https://github.com/{updates.REPO}/releases/download/v{version}/"
    try:
        for name in (*names, names[0] + ".build"):
            updates._download(url + name, work / name)
        updates.verify_signed(work, names[0], key_pem)
        check_package(work / names[0], version)
        new_build = (work / (names[0] + ".build")).read_text(errors="replace").strip()
        _require_newer(version, new_build)
        (work / "staged.json").write_text(json.dumps({"version": version, "build": new_build}))
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    _staged = work
    return work


def authorize() -> None:
    """Ask Windows for permission to install the staged update, from the window the
    person clicked in. Raises UpdateError("cancelled") when they say no."""
    work = _find_staged()
    if work is None or root() is None:
        raise UpdateError("install", "nothing is staged")
    version = json.loads((work / "staged.json").read_text())["version"]

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint32), ("fMask", ctypes.c_uint32), ("hwnd", ctypes.c_void_p),
                    ("lpVerb", ctypes.c_wchar_p), ("lpFile", ctypes.c_wchar_p),
                    ("lpParameters", ctypes.c_wchar_p), ("lpDirectory", ctypes.c_wchar_p),
                    ("nShow", ctypes.c_int), ("hInstApp", ctypes.c_void_p), ("lpIDList", ctypes.c_void_p),
                    ("lpClass", ctypes.c_wchar_p), ("hkeyClass", ctypes.c_void_p),
                    ("dwHotKey", ctypes.c_uint32), ("hIcon", ctypes.c_void_p), ("hProcess", ctypes.c_void_p)]

    ctypes.windll.ole32.CoInitializeEx(None, 2)                 # apartment threaded
    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x40 | 0x100                                  # keep the process; finish before returning
    info.lpVerb = "runas"
    info.lpFile = launcher()
    info.lpParameters = f'-m piklin.winupdate apply "{work}" {version} {os.getpid()}'
    info.nShow = 0
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
        if ctypes.get_last_error() == 1223 or ctypes.GetLastError() == 1223:    # the person said no
            raise UpdateError("cancelled", "permission was not given")
        raise UpdateError("install", "Windows would not start the installer")
    kernel = ctypes.windll.kernel32
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    code = ctypes.c_uint32(1)
    if kernel.WaitForSingleObject(info.hProcess, 5 * 60 * 1000) == 0:
        kernel.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
    kernel.CloseHandle(info.hProcess)
    if code.value != 0:
        raise UpdateError("verification" if code.value == 3 else "install",
                          f"the installer was not accepted (code {code.value})")


def _find_staged() -> Path | None:
    if _staged is not None and (_staged / "staged.json").is_file():
        return _staged
    try:
        found = sorted(updates_dir().glob("*/staged.json"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return None
    return found[-1].parent if found else None


def start_installer(library: str = "") -> bool:
    """Called as Piklin closes: start the script that waits for the (already
    authorized) installation to finish and opens Piklin again on ``library``.
    True when it was started."""
    work = _find_staged()
    base = root()
    if work is None or base is None or not (work / "apply.json").is_file():
        return False                       # nothing authorized: nothing to wait for
    try:
        version = json.loads((work / "staged.json").read_text())["version"]
        if not updates._VERSION_RE.match(version):
            return False
        script = work / "apply.ps1"
        shutil.copyfile(Path(__file__).with_name("winupdate.ps1"), script)
        command = [_powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                   "-WindowStyle", "Hidden", "-File", str(script),
                   "-ParentPid", str(os.getpid()), "-Root", str(base), "-Stage", str(work),
                   "-Version", version, "-Library", library or "", "-ResultFile", str(result_file())]
        quiet = dict(close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
        flags = _NO_WINDOW | _NEW_GROUP
        try:
            # Out of the job Piklin may have been started in: when that ends (a terminal
            # closing, a launcher that tidies up) it would take this with it.
            subprocess.Popen(command, creationflags=flags | _BREAKAWAY, **quiet)
        except OSError:
            subprocess.Popen(command, creationflags=flags, **quiet)     # the job does not allow it
        return True
    except (OSError, ValueError, KeyError):
        return False


def take_result() -> dict | None:
    """What the last update did - {"version", "ok", "code"} - once, or None."""
    path = result_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    try:
        path.unlink()
    except OSError:
        pass
    return data if isinstance(data, dict) else None


# -- 3. with the person's permission: a Piklin run as administrator -------------------
def apply(stage_dir: str, version: str, wait_pid: str) -> int:
    """Check the staged installer again, in a folder only administrators can
    change, and leave its installation waiting for Piklin (process ``wait_pid``)
    to close. Exit code 0 when it is in place."""
    if not ctypes.windll.shell32.IsUserAnAdmin():
        return 5
    if not updates._VERSION_RE.match(version) or not wait_pid.isdigit():
        return 2
    base = root()
    if base is None:
        return 2
    try:
        key_pem = (base / "release-key.pem").read_text()
    except OSError:
        return 3
    stage_path = Path(stage_dir)
    folder = Path(os.environ.get("ProgramData") or r"C:\ProgramData") / "Piklin" / "Updates"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.iterdir():            # what earlier updates left behind
        try:
            if old.is_dir() and time.time() - old.stat().st_mtime > 24 * 3600:
                shutil.rmtree(old, ignore_errors=True)
        except OSError:
            pass
    safe = folder / uuid.uuid4().hex
    safe.mkdir()
    # Administrators and the system change it; everyone else can only read.
    subprocess.run(["icacls", str(safe), "/inheritance:r", "/grant:r",
                    "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F", "*S-1-5-32-545:(OI)(CI)RX"],
                   check=True, capture_output=True, creationflags=_NO_WINDOW)
    msi, manifest, signature = msi_names(version)
    try:
        for name in (msi, manifest, signature, msi + ".build"):
            if (stage_path / name).is_file():
                shutil.copyfile(stage_path / name, safe / name)
        updates.verify_signed(safe, msi, key_pem)
        check_package(safe / msi, version)
        build = (safe / (msi + ".build")).read_text(errors="replace").strip() \
            if (safe / (msi + ".build")).is_file() else ""
        _require_newer(version, build)
    except (UpdateError, OSError):
        shutil.rmtree(safe, ignore_errors=True)
        return 3
    result = safe / "result.txt"
    script = safe / "install.cmd"
    # Each line is read on its own, so %errorlevel% is msiexec's. The wait lets this
    # process end first: msiexec cannot replace the files of a program that runs.
    script.write_text(
        "@echo off\r\n"
        # Piklin is still open: wait (up to half an hour) until its process has ended.
        f'for /L %%i in (1,1,1800) do (\r\n'
        f'  tasklist /FI "PID eq {wait_pid}" /FO CSV /NH | find """{wait_pid}""" >nul || goto go\r\n'
        f'  ping -n 2 127.0.0.1 >nul\r\n'
        f')\r\n'
        ':go\r\n'
        "ping -n 3 127.0.0.1 >nul\r\n"
        f'msiexec /i "{safe / msi}" /qb /norestart /l*v "{safe / "install.log"}"\r\n'
        f'>"{result}" echo %errorlevel%\r\n', encoding="ascii")
    # A console of its own, hidden: with none at all (DETACHED_PROCESS) the pipes in
    # install.cmd do not work and it waits for ever.
    subprocess.Popen(["cmd.exe", "/d", "/c", str(script)],
                     creationflags=_NO_WINDOW | _NEW_GROUP, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (stage_path / "apply.json").write_text(
        json.dumps({"result": str(result), "log": str(safe / "install.log")}))
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "apply":
        sys.exit(apply(sys.argv[2], sys.argv[3], sys.argv[4]))
    sys.exit(64)
