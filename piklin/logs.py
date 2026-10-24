"""Piklin's activity log: what happened, kept on this computer to help find
the cause of a problem.

*Small, always.* One file of at most 512 KB and two older ones, so the log
never takes more than about 1.5 MB, however long Piklin runs.

*Private.* Nothing is ever sent anywhere. Messages name photos, albums and
backup destinations by number, never by name, path, address or user name,
so the log can be pasted into a public problem report as it is.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import platform
import re
import sys
import threading
from pathlib import Path

MAX_BYTES = 512 * 1024
KEEP_OLD = 2
ISSUES_URL = "https://github.com/vezzulab/Piklin/issues/new"

log = logging.getLogger("piklin")
_ready = False


def log_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Piklin" / "Logs"
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "piklin"


def log_file() -> Path:
    return log_dir() / "piklin.log"


def crash_file() -> Path:
    return log_dir() / "crash.log"


def running_file() -> Path:
    return log_dir() / "running.json"




_HOME = os.path.expanduser("~")
_PATTERNS = [
    # addresses with a user or password in them, then any address
    (re.compile(r"[a-z][a-z0-9+.-]*://[^\s'\"]+", re.I), "<address>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"), "<ip>"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"),
]


def scrub(text: str) -> str:
    """Remove what could identify a person or a place from a message."""
    text = str(text)
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    # a file path under the home folder keeps only its file extension
    text = re.sub(re.escape(_HOME) + r"/[^\s'\":,)]*",
                  lambda m: "~/…" + (os.path.splitext(m.group(0))[1] or ""), text)
    return text


class _Scrubbed(logging.Formatter):
    def format(self, record):
        return scrub(super().format(record))


def setup(version: str) -> None:
    """Start writing the log. Safe to call more than once."""
    global _ready
    if _ready:
        return
    try:
        log_dir().mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_file(), maxBytes=MAX_BYTES, backupCount=KEEP_OLD, encoding="utf-8")
    except OSError:
        return                          # no log rather than no Piklin
    handler.setFormatter(_Scrubbed("%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                                   "%Y-%m-%d %H:%M:%S"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False
    _ready = True

    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gtk
        toolkit = (f"GTK {Gtk.get_major_version()}.{Gtk.get_minor_version()}."
                   f"{Gtk.get_micro_version()}, libadwaita {Adw.get_major_version()}."
                   f"{Adw.get_minor_version()}")
    except Exception:
        toolkit = "GTK unknown"
    if sys.platform == "darwin":
        # A Mac has no os-release: it was logged as "Linux".
        chip = "Apple Silicon" if platform.machine() == "arm64" else "Intel"
        distro = f"macOS {platform.mac_ver()[0] or ''} ({chip})".replace("  ", " ")
        session = "Aqua"
    elif sys.platform == "win32":
        distro = f"Windows {platform.release()} ({platform.version()})"
        session = "Windows"
    else:
        try:
            distro = platform.freedesktop_os_release().get("PRETTY_NAME", "Linux")
        except Exception:
            distro = "Linux"
        session = os.environ.get("XDG_SESSION_TYPE", "unknown session")
    build = ""
    try:
        from . import updates
        build = updates.installed_build()
    except Exception:
        pass
    log.info("---- Piklin %s%s started · %s · Python %s · %s · %s",
             version, f" (build {build})" if build else "", distro,
             platform.python_version(), toolkit, session)

    def crashed(kind, value, tb):
        log.critical("Unexpected error", exc_info=(kind, value, tb))
        sys.__excepthook__(kind, value, tb)
    sys.excepthook = crashed

    def thread_crashed(args):
        if args.exc_type is SystemExit:
            return
        log.critical("Unexpected error in a background task (%s)",
                     getattr(args.thread, "name", "thread"),
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    threading.excepthook = thread_crashed
    try:
        _watch_crashes()
    except Exception:                   # a log that cannot watch is not worth stopping Piklin for
        log.debug("crash watch failed", exc_info=True)


# -- crashes ---------------------------------------------------------------
# Python reports its own errors (above). A crash in a native library (the toolkit, the
# video codecs) kills the process with nothing to report, so three small things catch it,
# and the next start says so in this log:
#   * a marker, running.json, that exists only while Piklin runs: found at start, the last run did
#     not close properly (a crash, a force quit, a power cut);
#   * Python's fault handler, which writes the stack of every thread to crash.log as the process dies;
#   * on a Mac, the crash report the system wrote (the symbols of the stack, no files, no names).
CRASH_LINES = 80            # of crash.log put in the activity log


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)     # PROCESS_QUERY_LIMITED_INFORMATION
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
        return bool(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True                     # exists, but is not ours to signal
    return True


def _mac_crash_reports(since: float) -> list[str]:
    """One line for each crash report the system wrote for Piklin since ``since``."""
    import json
    out = []
    folder = Path.home() / "Library" / "Logs" / "DiagnosticReports"
    try:
        reports = sorted((p for p in folder.glob("Piklin-*.ips") if p.stat().st_mtime >= since),
                         key=lambda p: p.stat().st_mtime)[-3:]
    except OSError:
        return out
    for path in reports:
        try:
            head, body = path.read_text(encoding="utf-8", errors="replace").split("\n", 1)
            report = json.loads(body)
            exc = report.get("exception") or {}
            images = report.get("usedImages") or []
            thread = (report.get("threads") or [{}])[report.get("faultingThread", 0)]
            frames = []
            for f in (thread.get("frames") or [])[:8]:
                image = images[f["imageIndex"]].get("name", "?") if "imageIndex" in f else "?"
                frames.append(f"{image}:{f.get('symbol') or '?'}")
            note = (report.get("asi") or {})
            note = "; ".join(v if isinstance(v, str) else " ".join(map(str, v))
                             for vals in note.values() for v in (vals if isinstance(vals, list) else [vals]))
            out.append(f"{path.name}: {exc.get('type', '?')} {exc.get('signal', '')}"
                       f"{' (' + note[:120] + ')' if note else ''} in {thread.get('name') or 'thread'}: "
                       + " < ".join(frames))
        except Exception:
            out.append(f"{path.name}: a crash report the system wrote")
    return out


def _watch_crashes() -> None:
    import atexit
    import faulthandler
    import json
    import time

    previous = None
    try:
        previous = json.loads(running_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if isinstance(previous, dict) and previous.get("pid") != os.getpid() and _alive(int(previous.get("pid") or 0)):
        return                          # another Piklin is running: it is its marker, its crash file

    started = float(previous.get("started", 0)) if isinstance(previous, dict) else 0.0
    # what the last run left behind
    text = ""
    try:
        text = crash_file().read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        pass
    if previous is not None or text:
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)) if started else "an earlier time"
        log.warning("The last run of Piklin (started %s) did not close properly: it crashed, was force quit, "
                    "or the computer lost power", when)
    if text:
        lines = text.splitlines()
        log.critical("Crash of the last run, the stack of each thread when it died:\n%s",
                     "\n".join(lines[:CRASH_LINES]) + (f"\n… ({len(lines) - CRASH_LINES} more lines)"
                                                         if len(lines) > CRASH_LINES else ""))
    if sys.platform == "darwin" and started:
        for line in _mac_crash_reports(started):
            log.critical("Crash report from the system: %s", line)

    try:
        running_file().write_text(json.dumps({"pid": os.getpid(), "started": time.time()}), encoding="utf-8")
    except OSError:
        return
    try:
        crash_file().write_text("", encoding="utf-8")
        handle = open(crash_file(), "a", encoding="utf-8")
        faulthandler.enable(file=handle, all_threads=True)
        globals()["_crash_handle"] = handle             # kept open for as long as the process lives
    except Exception:
        pass

    def closed_properly():
        try:
            running_file().unlink(missing_ok=True)
            if crash_file().stat().st_size == 0:
                crash_file().unlink(missing_ok=True)
        except OSError:
            pass
    atexit.register(closed_properly)


def get(name: str) -> logging.Logger:
    """A logger for one part of Piklin, such as "backup" or "grid"."""
    return log.getChild(name)


_SEEN: set = set()


def once(logger: logging.Logger, key: str, level: int, message: str, *args, **kw) -> None:
    """Say a thing once per run: a page drawn again at every move must not fill the log with the same
    complaint."""
    if key in _SEEN or len(_SEEN) > 500:
        return
    _SEEN.add(key)
    logger.log(level, message, *args, **kw)


def read_all(max_lines: int = 2000) -> str:
    """The newest lines of the log, oldest file first."""
    lines: list[str] = []
    for i in range(KEEP_OLD, -1, -1):
        path = log_file() if i == 0 else log_file().with_name(f"piklin.log.{i}")
        try:
            lines.extend(path.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            continue
    return "\n".join(lines[-max_lines:])




def size_bytes() -> int:
    total = 0
    for path in log_dir().glob("piklin.log*"):
        try:
            total += path.stat().st_size
        except OSError:
            pass
    return total


def clear() -> None:
    for handler in log.handlers:
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            handler.acquire()
            try:
                handler.stream.seek(0)
                handler.stream.truncate()
            finally:
                handler.release()
    for path in log_dir().glob("piklin.log.*"):
        try:
            path.unlink()
        except OSError:
            pass
    log.info("Activity log cleared")
