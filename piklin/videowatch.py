"""Making the chosen videos smaller in the background, one at a time (see
videospace.py).

It never gets in the way: a video is converted only while the computer is
plugged in and battery saver is off, never while a backup, a sync, a scan
or a photo check is reading the disk, and at the lowest priority. What is
left carries on the next time Piklin opens. It can be paused at any time.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from gi.repository import Gio, GLib

from . import videospace

EVERY_SECONDS = 60
LOCAL_TEND_SECONDS = 3600


class VideoWatch:
    def __init__(self, library, catalog, settings, autobackup, indexer,
                 health_watch, on_status: Callable[[dict], None]):
        self.library = library
        self.catalog = catalog
        self.settings = settings
        self.autobackup = autobackup
        self.indexer = indexer
        self.health_watch = health_watch
        self.on_status = on_status
        self._running = False
        self._cancel = threading.Event()
        self._freed = 0
        self._done = 0
        self._last_tend = 0.0
        try:
            self._power = Gio.PowerProfileMonitor.dup_default()
        except Exception:
            self._power = None
        self._timer = GLib.timeout_add_seconds(EVERY_SECONDS, self._tick)
        GLib.timeout_add_seconds(20, self._first)

    # -- the queue ----------------------------------------------------------
    @property
    def queue(self) -> list[dict]:
        return list(self.settings.get("videos_to_convert") or [])

    def start(self, candidates) -> None:
        have = {q["id"] for q in self.queue}
        queue = self.queue + [{"id": c.photo_id, "bit_rate": c.bit_rate}
                              for c in candidates if c.photo_id not in have]
        self.settings.set("videos_to_convert", queue)
        self.settings.set("videos_convert_paused", False)
        self._freed = self._done = 0
        GLib.idle_add(self._kick)

    def pause(self, paused: bool = True) -> None:
        self.settings.set("videos_convert_paused", paused)
        if paused:
            self._cancel.set()
        else:
            self._cancel.clear()
            GLib.idle_add(self._kick)

    @property
    def paused(self) -> bool:
        return bool(self.settings.get("videos_convert_paused"))

    def stop(self) -> None:
        self._cancel.set()
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    # -- when --------------------------------------------------------------
    def waiting_for(self) -> str | None:
        """Why nothing is converting right now, or None when it can."""
        from . import system
        if self.paused:
            return "paused"
        if self._power is not None and self._power.get_power_saver_enabled():
            return "power"
        if system.on_battery():
            return "battery"
        ab, hw = self.autobackup, self.health_watch
        thread = getattr(self.indexer, "_thread", None)
        if (getattr(ab, "_running", False) or getattr(ab, "_syncing", False)
                or getattr(hw, "_running", False)
                or (thread is not None and thread.is_alive())):
            return "busy"
        return None

    def _kick(self):
        """Look now, once: _tick itself asks to be called again, which from an
        idle callback would run it without a pause."""
        self._tick()
        return GLib.SOURCE_REMOVE

    def _first(self):
        self._tick()
        return GLib.SOURCE_REMOVE

    def _tick(self):
        if not self._timer and self._cancel.is_set():
            return GLib.SOURCE_REMOVE
        now = time.time()
        if now - self._last_tend >= LOCAL_TEND_SECONDS:
            self._last_tend = now
            threading.Thread(target=self._tend_local, daemon=True).start()
        if self._running or not self.queue:
            return GLib.SOURCE_CONTINUE
        why = self.waiting_for()
        if why is not None:
            self.on_status({"state": "waiting", "why": why, "left": len(self.queue)})
            return GLib.SOURCE_CONTINUE
        self._running = True
        self._cancel.clear()
        item = self.queue[0]
        remotes = list(getattr(self.autobackup, "remotes", []) or [])
        total = self._done + len(self.queue)

        def work():
            from . import logs, system
            system.lower_thread_priority()
            vlog = logs.get("videos")
            result, freed = "gone", 0
            try:
                row = self.catalog.photo(item["id"])
                if row is not None:
                    cand = videospace.Candidate(
                        row["id"], row["path"], int(row["bytes"] or 0), float(row["duration"] or 0),
                        int(row["width"] or 0), int(row["height"] or 0), int(item["bit_rate"]), 0)

                    def progress(fraction, d=self._done, t=total):
                        GLib.idle_add(self.on_status, {"state": "converting", "done": d + 1,
                                                       "total": t, "fraction": fraction})
                    result = videospace.convert_one(self.library, self.catalog, cand, remotes,
                                                    on_progress=progress, cancel=self._cancel)
                    if result == "smaller":
                        after = self.catalog.photo(item["id"])
                        freed = cand.bytes - int(after["bytes"] or 0) if after is not None else 0
            except Exception as exc:
                vlog.warning("A video was not made smaller: %s", exc)
                result = "failed"
            GLib.idle_add(self._finished, item, result, freed)

        threading.Thread(target=work, name="videos", daemon=True).start()
        return GLib.SOURCE_CONTINUE

    def _tend_local(self):
        try:
            videospace.tend_local(self.library.root)
        except Exception:
            pass

    def _finished(self, item, result, freed):
        from . import logs
        self._running = False
        if result == "cancelled":
            self.on_status({"state": "waiting", "why": "paused", "left": len(self.queue)})
            return False
        self.settings.set("videos_to_convert", [q for q in self.queue if q["id"] != item["id"]])
        self._done += 1
        if result == "smaller":
            self._freed += freed
            self.on_status({"state": "converted", "id": item["id"], "freed": freed})
            logs.get("videos").info("A video was made smaller, %d MB freed", freed // (1024 * 1024))
            try:
                self.autobackup.mark_changed()
            except Exception:
                pass
        if not self.queue:
            self.on_status({"state": "finished", "done": self._done, "freed": self._freed})
        else:
            GLib.idle_add(self._kick)
        return False
