"""Checking the library's photos for damage in the background, a little at a
time, and mending what it finds from the backup (see health.py).

It stays out of the way: a short slice every twenty minutes at the lowest
priority, never while a backup, a sync or a scan is reading the disk, never
while battery saver is on, and mending only when the backup can be reached.
"""
from __future__ import annotations

import os
import threading
from typing import Callable

from gi.repository import Gio, GLib

from . import health as health_mod

FIRST_SECONDS = 5 * 60          # after Piklin opens: its own start-up work first
EVERY_SECONDS = 20 * 60
SLICE_SECONDS = 45
SLICE_BYTES = 4 * 1024 ** 3


class HealthWatch:
    def __init__(self, library, catalog, settings, autobackup, indexer,
                 on_result: Callable[[health_mod.Report, health_mod.Mending], None]):
        self.library = library
        self.catalog = catalog
        self.settings = settings
        self.autobackup = autobackup
        self.indexer = indexer
        self.on_result = on_result
        self._running = False
        self._cancel = threading.Event()
        self._health: health_mod.Health | None = None
        self._network = Gio.NetworkMonitor.get_default()
        try:
            self._power = Gio.PowerProfileMonitor.dup_default()
        except Exception:
            self._power = None
        first = int(os.environ.get("PIKLIN_HEALTH_FIRST_SECONDS", FIRST_SECONDS))
        self._timers = [GLib.timeout_add_seconds(max(1, first), self._first),
                        GLib.timeout_add_seconds(EVERY_SECONDS, self._tick)]

    @property
    def health(self) -> health_mod.Health:
        if self._health is None:
            self._health = health_mod.Health(self.library)
        return self._health

    def summary(self) -> dict:
        try:
            return self.health.summary()
        except Exception:
            return {"files": 0, "last": None, "damaged": 0, "mended": 0}

    def stop(self) -> None:
        self._cancel.set()
        for t in self._timers:
            if t:
                GLib.source_remove(t)
        self._timers = []

    def _first(self):
        self._tick()
        if self._timers:
            self._timers[0] = 0
        return GLib.SOURCE_REMOVE

    def _busy(self) -> bool:
        ab = self.autobackup
        thread = getattr(self.indexer, "_thread", None)
        return bool(getattr(ab, "_running", False) or getattr(ab, "_syncing", False)
                    or (thread is not None and thread.is_alive()))

    def _tick(self):
        if self._cancel.is_set():
            return GLib.SOURCE_REMOVE
        if (self._running or not self.settings.get("health_check", True)
                or self._busy()
                or (self._power is not None and self._power.get_power_saver_enabled())):
            return GLib.SOURCE_CONTINUE
        self._running = True
        remotes = list(getattr(self.autobackup, "remotes", []) or [])
        online = self._network.get_network_available()

        def work():
            from . import logs, system
            system.lower_thread_priority()
            hlog = logs.get("health")
            report, mending = health_mod.Report(), health_mod.Mending()
            try:
                paths = [r["path"] for r in self.catalog.q("SELECT path FROM photos")]
                report = self.health.check(paths, SLICE_BYTES, seconds=SLICE_SECONDS,
                                           cancel=self._cancel)
                if report.damaged:
                    hlog.warning("Photo health: %d photo(s) found damaged", len(report.damaged))
                pending = self.health.rows(health_mod.DAMAGED) + self.health.rows(health_mod.NO_COPY)
                if pending and not self._cancel.is_set():
                    backends = [r.backend() for r in remotes
                                if online or r.kind == "local"]
                    mending = self.health.mend(backends, cancel=self._cancel)
                    if mending.mended:
                        hlog.info("Photo health: %d damaged photo(s) mended from the backup",
                                  len(mending.mended))
                    if mending.no_copy:
                        hlog.warning("Photo health: %d damaged photo(s) with no good copy "
                                     "in the backup", len(mending.no_copy))
            except Exception as exc:
                hlog.warning("Photo health check stopped: %s", exc)
            GLib.idle_add(self._done, report, mending)

        threading.Thread(target=work, name="health", daemon=True).start()
        return GLib.SOURCE_CONTINUE

    def _done(self, report, mending):
        self._running = False
        if not self._cancel.is_set():
            self.on_result(report, mending)
        return False


def damaged_rows(health: health_mod.Health):
    """What to show for the damaged photos: those still damaged first."""
    rows = health.rows(health_mod.NO_COPY) + health.rows(health_mod.DAMAGED)
    return sorted(rows, key=lambda r: (r["state"] != health_mod.NO_COPY, r["key"]))
