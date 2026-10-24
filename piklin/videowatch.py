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
from pathlib import Path
from typing import Callable

from gi.repository import Gio, GLib

from . import videospace

EVERY_SECONDS = 60
LOCAL_TEND_SECONDS = 3600
# In "compressed" mode: how often the library looks for videos that came in
# since, and the least a pass is worth starting for.
PLAN_EVERY_SECONDS = 6 * 3600
# the videos give way to a backup or a scan for this long, at most
BUSY_PATIENCE = 10 * 60


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
        self._planning = False
        self._pruned = False
        self._began = 0.0
        self._thread: threading.Thread | None = None
        self._noted = ""
        self._busy_since = 0.0
        # Set by the window: True while photos are being copied in. The encoder is
        # stopped for as long as it says so, and goes on after.
        self.hold = None
        self._spent: list[float] = []       # seconds each video took in this session
        # There is no button to pause it any more: it looks after itself. One
        # left paused by an earlier version would stay so for good.
        if settings.get("videos_convert_paused"):
            settings.set("videos_convert_paused", False)
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
        queue = self.queue + [{"id": c.photo_id, "bit_rate": c.bit_rate, "profile": c.profile}
                              for c in candidates if c.photo_id not in have]
        self.settings.set("videos_to_convert", queue)
        self.settings.set("videos_convert_paused", False)
        self._freed = self._done = 0
        GLib.idle_add(self._kick)

    def add_paths(self, paths) -> list[str]:
        """Videos just copied in, to be made smaller with all the others: one
        at a time, only when it is the right moment, checked and with the
        original kept. Returns the paths the catalog does not know yet."""
        found, unknown = videospace.plan_paths(self.catalog, self.library.originals, paths)
        if found:
            self.start(found)
        return unknown

    # -- the mode: keep the originals, or keep videos small -------------------
    @property
    def mode(self) -> str:
        mode = self.settings.get("video_mode")
        return mode if mode in videospace.MODES else "original"

    def set_mode(self, mode: str) -> None:
        """Original: nothing more is made smaller, and what is waiting is
        forgotten (videos already made smaller stay so). Compressed: the library
        is looked through now, and again as videos come in."""
        mode = mode if mode in videospace.MODES else "original"
        self.settings.set("video_mode", mode)
        if mode == "original":
            self.settings.set("videos_to_convert", [])
            self._cancel.set()
            self.on_status({"state": "finished", "done": 0, "freed": 0})
        else:
            self._cancel.clear()
            self.settings.set("videos_convert_paused", False)
            self.settings.set("videos_planned_at", 0.0)
            GLib.idle_add(self._kick)

    def _plan_new(self) -> None:
        """Compressed mode, nothing waiting: queue the videos that came in
        (or were left out) since the last look."""
        self._planning = True

        def work():
            try:
                found = videospace.plan(self.catalog, self.library.originals,
                                        profile="compressed",
                                        skip=self.settings.get("videos_skipped") or {})
            except Exception:
                found = []
            GLib.idle_add(self._planned, found)
        threading.Thread(target=work, daemon=True, name="videos-plan").start()

    def _planned(self, found):
        self._planning = False
        if self.mode != "compressed":
            return False                    # changed its mind while looking
        self.settings.set("videos_planned_at", time.time())
        if found:
            self.start(found)
        return False

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
        # The work on this computer comes first; the sync and the backup follow.
        # Nothing is lost by not reading the backup first: before each video,
        # make_smaller takes the turn and reads what the other computers have
        # done (videospace.open_turn), so none is made smaller twice.
        if self.busy_with():
            return "busy"
        return None

    def busy_with(self) -> list[str]:
        """What is reading the disk now that the videos should give way to:
        a backup, a sync, the photo check, or a scan of the library."""
        ab, hw = self.autobackup, self.health_watch
        thread = getattr(self.indexer, "_thread", None)
        return [name for name, on in (
            ("backup or sync owed its turn", getattr(ab, "_owed", False)),
            ("backup", getattr(ab, "_running", False)),
            ("sync", getattr(ab, "_syncing", False)),
            ("photo check", getattr(hw, "_running", False)),
            ("library scan", thread is not None and thread.is_alive())) if on]

    def remaining(self) -> tuple[int, float]:
        """How many videos are left and about how many seconds they take: at the
        pace of this session's last videos, else four minutes each."""
        each = sum(self._spent[-10:]) / len(self._spent[-10:]) if self._spent else 240.0
        left = len(self.queue)
        return left, left * each

    def working(self) -> bool:
        """Whether a video is being made smaller now, or the next one is about
        to be: the backup waits for it, or it sends half-made videos."""
        return self._running or (bool(self.queue) and self.waiting_for() is None)

    def _kick(self):
        """Look now, once: _tick itself asks to be called again, which from an
        idle callback would run it without a pause."""
        self._tick()
        return GLib.SOURCE_REMOVE

    def _first(self):
        self._tick()
        # half-made files from a Piklin closed in the middle of a video
        threading.Thread(target=self._clear_leftovers, daemon=True).start()
        return GLib.SOURCE_REMOVE

    def _clear_leftovers(self):
        try:
            videospace.clear_leftovers(self.library.originals)
        except Exception:
            pass

    def _note(self, state: str) -> None:
        """Say in the log why the videos are not going on, once for each reason:
        a person asking why nothing happens can be answered from it."""
        if state != self._noted:
            self._noted = state
            from . import logs
            logs.get("videos").info("Videos: %s (%d waiting)", state, len(self.queue))

    def _tick(self):
        """One look at the queue. An error in it is logged and the timer goes on:
        a timer that raises is removed by GLib, and the videos would wait for good."""
        try:
            return self._look()
        except Exception:
            from . import logs
            logs.get("videos").exception("The videos' timer met an error")
            self._running = False
            return GLib.SOURCE_CONTINUE

    def _look(self):
        if not self._timer and self._cancel.is_set():
            return GLib.SOURCE_REMOVE
        if (self._running and self._thread is not None and not self._thread.is_alive()
                and time.monotonic() - self._began > 30):
            # the video's thread ended without saying so: do not wait for it for good
            from . import logs
            logs.get("videos").warning("A video's work ended without a result; going on")
            self._running = False
        now = time.time()
        if now - self._last_tend >= LOCAL_TEND_SECONDS:
            self._last_tend = now
            threading.Thread(target=self._tend_local, daemon=True).start()
        if (self.mode == "compressed" and not self.queue and not self._running
                and not self._planning and not self.paused
                and now - float(self.settings.get("videos_planned_at") or 0) >= PLAN_EVERY_SECONDS
                and self.waiting_for() is None):
            self._plan_new()
        if (not self._pruned and not self._running
                and getattr(self.autobackup, "nas_read", True)
                and not getattr(self.autobackup, "_syncing", False)):
            self._pruned = True
            self._prune_queue()
        if self._running or not self.queue:
            self._note("encoding" if self._running else "nothing waiting")
            return GLib.SOURCE_CONTINUE
        why = self.waiting_for()
        if why == "busy":
            # Give way to a backup or a scan - but not for good: what is only
            # marked as busy would hold the videos, and the backup that waits for
            # them, for ever.
            began = self._busy_since or time.monotonic()
            self._busy_since = began
            if time.monotonic() - began > BUSY_PATIENCE and not (
                    {"backup", "backup or sync owed its turn"} & set(self.busy_with())):
                from . import logs
                logs.get("videos").warning("Videos waited %d minutes for %s; going on",
                                           BUSY_PATIENCE // 60, ", ".join(self.busy_with()))
                why = None
        else:
            self._busy_since = 0.0
        if why is not None:
            self._note(f"waiting, {why}" + (f" ({', '.join(self.busy_with())})" if why == "busy" else ""))
            self.on_status({"state": "waiting", "why": why, "left": len(self.queue)})
            return GLib.SOURCE_CONTINUE
        self._note("starting")
        self._running = True
        self._began = time.monotonic()
        self._cancel.clear()
        item = self._next_item()
        remotes = list(getattr(self.autobackup, "remotes", []) or [])
        total = self._done + len(self.queue)

        def work():
            from . import logs, system
            try:
                system.lower_thread_priority()
            except Exception:
                pass

            def progress(fraction, d=self._done, t=total):
                GLib.idle_add(self.on_status, {"state": "converting", "done": d + 1,
                                               "total": t, "fraction": fraction})
            try:
                result, freed = videospace.make_smaller(
                    self.library, self.catalog, remotes, item,
                    cancel=self._cancel, on_progress=progress, pause=self.hold)
            except Exception as exc:
                logs.get("videos").warning("A video was not made smaller: %s", exc)
                result, freed = "failed", 0
            GLib.idle_add(self._finished, item, result, freed)

        self._thread = threading.Thread(target=work, name="videos", daemon=True)
        self._thread.start()
        return GLib.SOURCE_CONTINUE

    def _prune_queue(self) -> None:
        """Once the backup has been read: forget the waiting videos that are
        already the smaller one, or that another computer has made smaller, so
        the count is true and nothing is looked at twice."""
        queue = self.queue
        keep = []
        try:
            elsewhere = {str((Path(self.library.root) / rel).resolve())
                         for rel in videospace.incoming(self.library.root)}
        except Exception:
            elsewhere = set()
        for item in queue:
            row = self.catalog.photo(item["id"])
            if row is None:
                continue
            path = row["path"]
            if (videospace.already_smaller(self.library.root, path, int(row["bytes"] or 0))
                    or str(Path(path).resolve()) in elsewhere):
                continue
            keep.append(item)
        if len(keep) != len(queue):
            self.settings.set("videos_to_convert", keep)

    def _next_item(self) -> dict:
        """The next video to make smaller. Every computer sees the same list
        in the same order, so each one began with the same video at the same
        time and they did the same work side by side; each takes them in an
        order of its own instead, and the work divides itself."""
        import hashlib
        from .backuplock import me
        ident = me()[0]
        return min(self.queue, key=lambda q: hashlib.sha1(
            f"{ident}:{q['id']}".encode()).digest())

    def _tend_local(self):
        try:
            videospace.tend_local(self.library.root)
        except Exception:
            pass

    def _finished(self, item, result, freed):
        from . import logs
        self._running = False
        if result in ("busy", "unread"):
            # Not this computer's turn, or the destination could not be read:
            # the video stays where it is and it asks again in a minute.
            self.on_status({"state": "waiting", "why": "others", "left": len(self.queue)})
            return False
        if result == "cancelled":
            self.on_status({"state": "waiting", "why": "paused", "left": len(self.queue)})
            return False
        self.settings.set("videos_to_convert", [q for q in self.queue if q["id"] != item["id"]])
        self._spent.append(max(1.0, time.monotonic() - self._began))
        if result in ("kept", "failed"):
            # Not looked at again at every pass through the library (see
            # videospace.skipped): each try is minutes of work.
            row = self.catalog.photo(item["id"])
            if row is not None:
                self.settings.set("videos_skipped", videospace.note_skipped(
                    self.settings.get("videos_skipped") or {}, item["id"],
                    int(row["bytes"] or 0), result))
            if result == "failed":
                logs.get("videos").warning("A video could not be made smaller (it stays as it is)")
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
