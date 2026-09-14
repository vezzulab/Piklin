"""Automatic backup: after the library changes, and only then.

Nothing here polls. A backup is armed by a real change - photos imported,
an edit, an album, a favourite - and runs once the library has been quiet
for a minute, so an import of 500 photos is one backup, not 500. When
nothing changed, nothing runs: no timer, no network, no disk reading,
which matters on a laptop running on battery.

Before contacting a destination, the local record of what was already
sent there is compared with the library; if they match, the destination
is never contacted. A backup that cannot reach its destination waits for
the network to come back (a signal, not a loop), with a few spaced-out
retries for a NAS that was simply switched off. While battery saver is
on, backups wait for it to be turned off.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from gi.repository import Gio, GLib

from . import remote as remote_mod
from .i18n import _, ngettext, day_month

QUIET_SECONDS = 60
RETRY_MINUTES = (5, 15, 60)


@dataclass
class Outcome:
    ok: bool
    unreachable: bool = False
    message: str = ""
    uploaded: int = 0


def run_backup(root: Path, remotes: list, keep_days: int = 30,
               progress: Callable[[str], None] | None = None) -> Outcome:
    """Back the library up to every destination that is behind."""
    root = Path(root)
    light = remote_mod.library_files(root, include_catalog=False)
    behind = [r for r in remotes if r.backend().has_local_changes(root, light)]
    if not behind:
        return Outcome(True)
    files = remote_mod.library_files(root)          # now with the catalog
    uploaded = 0
    # Each destination on its own: a NAS left at home must not stop the
    # cloud copy made while travelling. The one that couldn't be reached
    # stays behind and catches up by itself once it can be reached again.
    problems, unreachable = [], False
    for r in behind:
        backend = r.backend()
        if progress:
            progress(_("Connecting to {name}…").format(name=r.name))
        test = backend.test()
        if not test.ok:
            problems.append(f"{r.name}: {test.message}")
            unreachable = unreachable or test.unreachable
            continue

        def report(p, name=r.name):
            if progress and p.phase == "listing":
                progress(_("Checking what is already backed up"))
            elif progress and p.phase == "uploading" and p.total_files:
                # the file number, and how far through all the bytes, which
                # keeps moving while one big video goes up
                progress(_("Backing up to {name} — {done} of {total} ({percent}%)").format(
                    name=name, done=f"{min(p.done_files + 1, p.total_files):,}",
                    total=f"{p.total_files:,}", percent=int(p.fraction * 100)))

        p = backend.push(root, files, on_progress=report,
                         keep_versions_days=keep_days)
        uploaded += p.uploaded
        if p.phase != "done" or p.errors:
            message = p.message or ngettext("{count} file couldn't be uploaded",
                                            "{count} files couldn't be uploaded",
                                            p.errors).format(count=p.errors)
            problems.append(f"{r.name}: {message}")
            unreachable = unreachable or p.unreachable
    if problems:
        return Outcome(False, unreachable, "; ".join(problems), uploaded)
    return Outcome(True, uploaded=uploaded)


def describe_last(ts: float) -> str:
    delta = time.time() - ts
    if delta < 90:
        return _("just now")
    if delta < 3600:
        minutes = int(delta // 60)
        return ngettext("{count} minute ago", "{count} minutes ago",
                        minutes).format(count=minutes)
    then, now = time.localtime(ts), time.localtime()
    clock = time.strftime("%H:%M", then)
    if (then.tm_year, then.tm_yday) == (now.tm_year, now.tm_yday):
        return _("today at {time}").format(time=clock)
    if delta < 2 * 86400:
        return _("yesterday at {time}").format(time=clock)
    from datetime import date
    return day_month(date(then.tm_year, then.tm_mon, then.tm_mday))


class AutoBackup:
    def __init__(self, library, settings, on_status: Callable[[str], None]):
        self.library = library
        self.settings = settings
        self.on_status = on_status
        self._timer = 0
        self._running = False
        self._again = False
        self._retry = 0
        self._waiting = ""              # "" | "network" | "power"
        self._problem = ""
        self._progress = ""
        # Kept in .cache, outside the backup: noting that a backup is due
        # must not itself look like a change to back up.
        self._state_path = Path(library.root) / ".cache" / "backups" / "auto.json"
        try:
            self._state = json.loads(self._state_path.read_text())
        except (OSError, ValueError):
            self._state = {}
        self._network = Gio.NetworkMonitor.get_default()
        self._network.connect("network-changed", self._on_network_changed)
        try:
            self._power = Gio.PowerProfileMonitor.dup_default()
            self._power.connect("notify::power-saver-enabled", self._on_power_changed)
        except Exception:
            self._power = None
        # Changes left over from the last session still go out.
        if self.active and self._state.get("pending"):
            self._schedule(QUIET_SECONDS)
        self.refresh_status()

    # -- state ----------------------------------------------------------
    def _set_state(self, **values) -> None:
        self._state.update(values)
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._state))
            tmp.replace(self._state_path)
        except OSError:
            pass

    @property
    def remotes(self) -> list:
        return [remote_mod.Remote.from_dict(r)
                for r in (self.settings.get("remotes") or [])
                if r.get("enabled", True)]

    @property
    def active(self) -> bool:
        return bool(self.settings.get("remote_autosync")) and bool(self.remotes)

    def mark_changed(self, delay: int = QUIET_SECONDS) -> None:
        """Something in the library changed: back up after a quiet minute."""
        if not self.settings.get("remotes"):
            return
        if not self._state.get("pending"):
            self._set_state(pending=True)
        if not self.active:
            self.refresh_status()
            return
        if self._running:
            self._again = True
            return
        self._retry = 0
        if self._waiting != "power":
            self._schedule(delay)
        self.refresh_status()

    def settings_changed(self) -> None:
        """The switch or the destinations changed."""
        if self.active and self._state.get("pending"):
            self._problem = ""
            self._schedule(5)
        elif not self.active:
            self._cancel()
        self.refresh_status()

    def mark_clean(self) -> None:
        """A backup made by hand finished without errors."""
        self._set_state(pending=False)
        self._set_state(last=time.time())
        self._problem = ""
        self._waiting = ""
        self._cancel()
        self.refresh_status()

    # -- scheduling -----------------------------------------------------
    def _cancel(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _schedule(self, seconds: float) -> None:
        self._cancel()
        self._timer = GLib.timeout_add_seconds(max(1, int(seconds)), self._fire)

    def _fire(self):
        self._timer = 0
        self.run()
        return GLib.SOURCE_REMOVE

    def _on_network_changed(self, _monitor, available):
        if self._waiting == "network" and available and not self._running:
            self._schedule(10)

    def _on_power_changed(self, *_):
        if (self._waiting == "power" and self._power is not None
                and not self._power.get_power_saver_enabled()):
            self._waiting = ""
            self._schedule(10)

    # -- running --------------------------------------------------------
    def run(self) -> bool:
        if self._running or not self.active or not self._state.get("pending"):
            return False
        if self._power is not None and self._power.get_power_saver_enabled():
            self._waiting = "power"
            self.refresh_status()
            return False
        remotes = self.remotes
        if (not self._network.get_network_available()
                and any(r.kind != "local" for r in remotes)):
            self._waiting = "network"
            self.refresh_status()
            return False
        self._running, self._again = True, False
        self._waiting, self._problem, self._progress = "", "", ""
        self.refresh_status()
        root = self.library.root
        keep = int(self.settings.get("backup_keep_versions_days", 30) or 0)

        def work():
            try:
                outcome = run_backup(root, remotes, keep, progress=lambda text:
                                     GLib.idle_add(self.refresh_status, text))
            except Exception as exc:
                outcome = Outcome(False, False, str(exc))
            GLib.idle_add(self._finished, outcome)
        threading.Thread(target=work, daemon=True).start()
        return True

    def _finished(self, outcome: Outcome):
        self._running = False
        self._progress = ""
        if outcome.ok:
            self._retry = 0
            if outcome.uploaded or not self._state.get("last"):
                self._set_state(last=time.time())
            if self._again:
                self._schedule(QUIET_SECONDS)       # changed while uploading
            else:
                self._set_state(pending=False)
        elif outcome.unreachable:
            self._problem = outcome.message
            self._waiting = "network"
            if self._retry < len(RETRY_MINUTES):
                self._schedule(RETRY_MINUTES[self._retry] * 60)
            self._retry += 1
        else:
            # e.g. a wrong password: trying again would fail the same way.
            # The next change, or Back Up Now, tries again.
            self._problem = outcome.message
        self.refresh_status()
        return False

    # -- status ---------------------------------------------------------
    def status_text(self) -> str:
        if not self.settings.get("remotes"):
            return ""
        if self._running:
            return self._progress or _("Backing up…")
        if not self.settings.get("remote_autosync"):
            return _("Automatic backup is off")
        if self._waiting == "power":
            return _("Backup waits until battery saver is off")
        if self._waiting == "network":
            return (_("Can't reach {name}, will try again").format(
                        name=self._problem.split(":")[0])
                    if self._problem else _("Backup waits for a connection"))
        if self._problem:
            return _("Backup stopped: {problem}").format(problem=self._problem)
        if self._state.get("pending"):
            return _("Changes waiting to back up")
        last = self._state.get("last") or 0
        return (_("Backed up {when}").format(when=describe_last(last)) if last
                else _("Not backed up yet"))

    def refresh_status(self, progress: str | None = None):
        if progress is not None:
            self._progress = progress
        try:
            self.on_status(self.status_text())
        except Exception:
            pass
        return False
