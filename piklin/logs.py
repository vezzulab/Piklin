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
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "piklin"


def log_file() -> Path:
    return log_dir() / "piklin.log"


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


def get(name: str) -> logging.Logger:
    """A logger for one part of Piklin, such as "backup" or "grid"."""
    return log.getChild(name)


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
