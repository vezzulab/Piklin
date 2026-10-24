"""A watch on the window: if Piklin stops answering for a few seconds, where it was stuck is written to the log.

The main loop touches a timer every second; if it does not (it is stuck in something), Python's own fault handler
writes the stack of every thread to ``freeze.log`` beside the activity log, again every few seconds while it lasts.
It costs one small timer, and nothing when all is well. It does not stop the freeze: it makes the next one findable.
"""
from __future__ import annotations

import faulthandler
import io

from . import logs

STALL = 6          # seconds without an answer before the stack is written
_file = None


def start() -> None:
    global _file
    try:
        from gi.repository import GLib
        path = logs.log_dir() / "freeze.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 256 * 1024:
            path.write_text("", encoding="utf-8")                      # never more than a few hundred KB
        _file = open(path, "a", encoding="utf-8")
    except Exception:
        return

    def beat():
        try:
            faulthandler.cancel_dump_traceback_later()
            faulthandler.dump_traceback_later(STALL, repeat=True, file=_file)
        except (ValueError, OSError, io.UnsupportedOperation):
            return False
        return True
    beat()
    GLib.timeout_add_seconds(1, beat)
    logs.get("watchdog").info("watching for freezes (%d s)", STALL)
