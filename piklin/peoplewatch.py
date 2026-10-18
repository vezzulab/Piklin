"""Looking for faces in the background (see people.py).

Only when People is on, and never in the way: a few photos at a time, only
while the computer is plugged in and battery saver is off, never while a
backup, a sync, a scan or a photo check is reading the disk, and at the lowest
priority. What is left carries on the next time Piklin opens.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from gi.repository import Gio, GLib

from . import people, peoplesync

EVERY_SECONDS = 20
BATCH = 48
# The first photos after People is turned on are looked at at once, whatever
# else the computer is doing and whether or not it is plugged in: a few
# seconds of work that show the first people, instead of nothing for hours.
EAGER_PHOTOS = 600
# How often the faces found so far are grouped: very often at first, so people
# appear at once, then now and then.
REGROUP_FIRST = 96
REGROUP_EVERY = 480
# While someone is using Piklin (a key, a click, the wheel within this many
# seconds) it looks at one photo at a time, a few at a time and without hurry,
# so the window keeps up; when nobody is, it looks at as many as the computer
# can spare. The first photos after People is turned on are exempt.
ACTIVE_SECONDS = 8
BATCH_ACTIVE = 8
PAUSE_ACTIVE = 2          # seconds between small batches while in use
# The file other computers read is written at most this often, after a change.
WRITE_EVERY = 60


class PeopleWatch:
    def __init__(self, library, catalog, settings, autobackup, indexer,
                 health_watch, video_watch, on_change: Callable[[dict], None]):
        self.library = library
        self.catalog = catalog
        self.settings = settings
        self.autobackup = autobackup
        self.indexer = indexer
        self.health_watch = health_watch
        self.video_watch = video_watch
        self.on_change = on_change
        self._running = False
        self._looked = 0
        self._left = 0
        self._grouped_at = 0
        self._eager = False
        self._stop = threading.Event()
        self._last_input = 0.0
        self._written_at = 0.0          # when people.changed_at was last written
        self._write_tried = 0.0
        self._regrouped_at_start = False
        self._writing = False
        try:
            self._power = Gio.PowerProfileMonitor.dup_default()
        except Exception:
            self._power = None
        self._timer = GLib.timeout_add_seconds(EVERY_SECONDS, self._tick)
        GLib.timeout_add_seconds(30, self._first)

    def touch(self) -> None:
        """The person did something in the window."""
        now = time.monotonic()
        self._last_input = now
        # The hour of it, for whoever puts a new version on this computer: it is
        # restarted when nobody is using it. At most every ten seconds.
        if now - getattr(self, "_marked", -99.0) >= 10:
            self._marked = now
            try:
                from . import logs
                mark = logs.log_file().parent / "last-input"
                mark.write_text(str(time.time()))
            except Exception:
                pass

    def _in_use(self) -> bool:
        return time.monotonic() - self._last_input < ACTIVE_SECONDS

    @property
    def enabled(self) -> bool:
        if self.settings.get("people_enabled"):
            return True
        # People another computer found and named arrive with the backup: they
        # are already here to see, and nobody should have to turn People on and
        # look at every photo again on each computer.
        try:
            if self.catalog.scalar("SELECT COUNT(*) FROM people", (), 0):
                self.settings.set("people_enabled", True)
                return True
        except Exception:
            pass
        return False

    def set_enabled(self, on: bool) -> None:
        self.settings.set("people_enabled", bool(on))
        if on:
            self._looked = 0
            self._grouped_at = 0
            self._eager = True
            self._stop.clear()
            GLib.idle_add(self._first)
        else:
            self._stop.set()
            threading.Thread(target=self._forget, daemon=True).start()

    def _forget(self):
        # Turning People off takes everything it made with it, once the few
        # photos being looked at have been let go of.
        import time
        for _ in range(100):
            if not self._running:
                break
            time.sleep(0.2)
        people.forget_all(self.catalog)
        GLib.idle_add(self.on_change, {"state": "off"})

    def remaining(self) -> int:
        try:
            return people.remaining(self.catalog)
        except Exception:
            return 0

    def waiting_for(self) -> str | None:
        from . import system
        if self._power is not None and self._power.get_power_saver_enabled():
            return "power"
        if system.on_battery():
            return "battery"
        ab, hw = self.autobackup, self.health_watch
        thread = getattr(self.indexer, "_thread", None)
        if (getattr(ab, "_running", False) or getattr(ab, "_syncing", False)
                or getattr(hw, "_running", False)
                or getattr(self.video_watch, "_running", False)
                or (thread is not None and thread.is_alive())):
            return "busy"
        return None

    def stop(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _first(self):
        self._tick()
        return GLib.SOURCE_REMOVE

    def _regroup_once(self) -> None:
        """Once after Piklin opens: the faces in no group are looked at again with
        what is known now (the well known people take what is clearly theirs), and
        every person is looked through for faces that do not belong. It costs a few
        seconds in the background and needs nobody to press anything."""
        if self._regrouped_at_start or self._running:
            return
        self._regrouped_at_start = True
        self._running = True

        def work():
            from . import logs, system
            system.lower_thread_priority()
            try:
                people.regroup(self.catalog)
                people.audit(self.catalog)
            except Exception as exc:
                logs.get("people").warning("could not group the loose faces: %s", exc)
            GLib.idle_add(self._regrouped)
        threading.Thread(target=work, name="people-regroup", daemon=True).start()

    def _regrouped(self):
        self._running = False
        self.on_change({"state": "grouped"})
        return False

    def _maybe_write(self) -> None:
        """Say what changed to the other computers: this computer's own file,
        in the library, which the backup sends (peoplesync.py)."""
        now = time.time()
        if (self._writing or people.changed_at <= self._written_at
                or now - self._write_tried < WRITE_EVERY):
            return
        self._writing = True
        self._write_tried = now
        stamp = people.changed_at

        def work():
            from . import logs, system
            system.lower_thread_priority()
            wrote = False
            try:
                wrote = peoplesync.write(self.library, self.catalog)
            except Exception as exc:
                logs.get("people").warning("could not write the people file: %s", exc)
            GLib.idle_add(self._written, stamp, wrote)
        threading.Thread(target=work, name="people-write", daemon=True).start()

    def _written(self, stamp, wrote):
        self._writing = False
        self._written_at = max(self._written_at, stamp)
        if wrote:
            try:
                self.autobackup.mark_changed()      # so that it goes up with the next backup
            except Exception:
                pass
        return False

    def _tick(self):
        if not self._timer:
            return GLib.SOURCE_REMOVE
        if self.enabled:
            self._maybe_write()
            self._regroup_once()
        if self._running or not self.enabled:
            return GLib.SOURCE_CONTINUE
        if self._eager and self._looked >= EAGER_PHOTOS:
            self._eager = False
        if not self._eager and self.waiting_for() is not None:
            return GLib.SOURCE_CONTINUE
        self._running = True

        def work():
            from . import logs, system
            system.lower_thread_priority()
            try:
                if not people.available():
                    GLib.idle_add(self._done, None, 0)
                    return
                busy = self._in_use() and not self._eager
                batch = people.pending(self.catalog, BATCH_ACTIVE if busy else BATCH)
                people.scan_photos(self.catalog, batch, workers=1 if busy else None,
                                   stop=self._stop)
                left = people.remaining(self.catalog) if batch else 0
                self._looked += len(batch)
                every = REGROUP_FIRST if self._grouped_at < REGROUP_FIRST * 3 else REGROUP_EVERY
                if batch and (left == 0 or self._looked - self._grouped_at >= every):
                    people.regroup(self.catalog)
                    if left == 0:
                        people.audit(self.catalog)      # once the looking is over
                    self._grouped_at = self._looked
                GLib.idle_add(self._done, bool(batch), left)
            except Exception as exc:
                logs.get("people").warning("looking for faces stopped: %s", exc)
                GLib.idle_add(self._done, None, 0)

        threading.Thread(target=work, name="people", daemon=True).start()
        return GLib.SOURCE_CONTINUE

    def _done(self, did, left):
        self._running = False
        self._left = left
        if self._stop.is_set():
            return False
        if did:
            self.on_change({"state": "looking" if left else "done", "left": left})
            if left:
                # the next few, without waiting a minute - and without hurry
                # while the window is in use
                if self._in_use() and not self._eager:
                    GLib.timeout_add_seconds(PAUSE_ACTIVE, self._first)
                else:
                    GLib.idle_add(self._first)
        return False
