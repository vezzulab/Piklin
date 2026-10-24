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

from . import backuplock
from . import remote as remote_mod
from .i18n import _, ngettext, day_month

QUIET_SECONDS = 60
RETRY_MINUTES = (5, 15, 60)
# How long to wait before asking again when another computer is backing up there.
BUSY_MINUTES = 5
# How often another computer's changes are looked for while Piklin is open.
SYNC_MINUTES = 10
MAX_DEFERRED = 20 * 60          # seconds the backup and the sync wait for copies or videos; then they are owed their turn


@dataclass
class Outcome:
    ok: bool
    unreachable: bool = False
    message: str = ""
    uploaded: int = 0
    # another computer has the turn to back up here: its name; nothing was sent
    busy: str = ""


def _tend_videos(root: Path, backend, problems: list) -> None:
    """Converted videos' originals in this backup: set apart, or let go."""
    from . import logs, videospace
    if not videospace.due(root, backend.remote.id):
        return
    try:
        counts = videospace.tend(root, backend)
    except Exception as exc:
        logs.get("backup").warning("Converted videos not tended: %s", exc)
        return
    if counts["held"] or counts["deleted"]:
        logs.get("backup").info("Converted videos: %d original(s) set apart, %d let go",
                                counts["held"], counts["deleted"])
    if counts["problems"]:
        logs.get("backup").warning("Converted videos: %d original(s) kept, the smaller "
                                   "copy did not check out", counts["problems"])


def _clean_leftovers(root: Path, backend) -> None:
    """Half-made files at the destination (an encode or an upload cut short)
    are removed, once a day, when their finished file is beside them."""
    from . import logs, videospace
    try:
        gone = videospace.clean_destination(root, backend)
    except Exception as exc:
        logs.get("backup").warning("Half-made files not cleared: %s", exc)
        return
    if gone:
        logs.get("backup").info("Half-made files cleared from the backup: %d", gone)


def run_backup(root: Path, remotes: list, keep_days: int = 30,
               progress: Callable[[str], None] | None = None,
               before_push: Callable | None = None) -> Outcome:
    """Back the library up to every destination that is behind.

    ``before_push(remote, backend)``, when given, runs once a destination is
    reached and before anything is sent to it: it brings in what another
    computer sent there, so this backup never overwrites it."""
    from . import videospace
    root = Path(root)
    # Not the large original of a video another computer made smaller: sent,
    # it would undo that computer's work in the backup.
    gone = videospace.superseded(root)

    def kept(files):
        # a file is a path, or a (path, name) pair for the catalog's snapshot
        return [f for f in files
                if Path(f[0] if isinstance(f, tuple) else f) not in gone]
    light = kept(remote_mod.library_files(root, include_catalog=False))
    # A destination with nothing new to send may still have work waiting:
    # a converted video's original to set apart, or to let go after its days.
    behind = [r for r in remotes
              if before_push is not None or videospace.due(root, r.id)
              or r.backend().has_local_changes(root, light)]
    if not behind:
        return Outcome(True)
    files = kept(remote_mod.library_files(root))          # now with the catalog
    uploaded = 0
    # Each destination on its own: a NAS left at home must not stop the
    # cloud copy made while travelling. The one that couldn't be reached
    # stays behind and catches up by itself once it can be reached again.
    problems, unreachable, waiting = [], False, []
    for r in behind:
        backend = r.backend()
        if progress:
            progress(_("Connecting to {name}…").format(name=r.name))
        test = backend.test()
        if not test.ok:
            problems.append(f"{r.name}: {test.message}")
            unreachable = unreachable or test.unreachable
            continue
        if before_push is not None:
            if progress:
                progress(_("Updating from {name}…").format(name=r.name))
            try:
                before_push(r, backend)
            except Exception as exc:
                problems.append(f"{r.name}: {exc}")
                continue
            if not backend.has_local_changes(root, light):
                _tend_videos(root, backend, problems)
                _clean_leftovers(root, backend)
                continue
        elif not backend.has_local_changes(root, light):
            _tend_videos(root, backend, problems)
            _clean_leftovers(root, backend)
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

        # One backup at a time to a destination: if another computer is
        # sending its library there now, this one waits and asks again later.
        try:
            turn = backuplock.take(backend)
        except backuplock.Busy as other:
            waiting.append(other.who)
            continue
        try:
            # Nothing changed there since this computer last looked: what it sent
            # is what is there, and the whole backup needn't be read again.
            try:
                same, _top = backend.unchanged_since_last_look(root)
            except Exception:
                same = False
            known = backend.sent_index(root) if same else None
            p = backend.push(root, files, on_progress=report,
                             keep_versions_days=keep_days, known=known)
            uploaded += p.uploaded
            if p.phase == "done" and not p.errors:
                try:
                    backend.remember_look(root, backend.top_listing(), full=known is None)
                except Exception:
                    pass
                _tend_videos(root, backend, problems)
                _clean_leftovers(root, backend)
            if p.phase != "done" or p.errors:
                message = p.message or ngettext("{count} file couldn't be uploaded",
                                                "{count} files couldn't be uploaded",
                                                p.errors).format(count=p.errors)
                problems.append(f"{r.name}: {message}")
                unreachable = unreachable or p.unreachable
        finally:
            if turn is not None:
                turn.release()
    if problems:
        return Outcome(False, unreachable, "; ".join(problems), uploaded)
    return Outcome(True, uploaded=uploaded, busy=", ".join(dict.fromkeys(waiting)))


def duration_text(seconds: float) -> str:
    """A wait in plain words: "a few minutes", "25 minutes", "4 h 10 min"."""
    minutes = int(round(seconds / 60.0))
    if minutes < 5:
        return _("a few minutes")
    if minutes < 60:
        return ngettext("{count} minute", "{count} minutes", minutes).format(count=minutes)
    minutes = int(round(minutes / 5.0)) * 5               # not 4 h 13 min: nobody counts that closely
    hours, rest = divmod(minutes, 60)
    return _("{hours} h {minutes} min").format(hours=hours, minutes=rest) if rest else \
        _("{hours} h").format(hours=hours)


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
    def __init__(self, library, settings, on_status: Callable[[str], None],
                 sync: Callable | None = None):
        self.library = library
        self.settings = settings
        self.on_status = on_status
        # sync(backend, progress): brings in another computer's changes (sync.pull)
        self._sync = sync
        self._syncing = False
        # True once this session has read what the other computers left in
        # the backup (see nas_read)
        self._read_once = False
        self._timer = 0
        self._running = False
        self._again = False
        self._retry = 0
        self._waiting = ""              # "" | "network" | "power" | "copying" | "videos"
        # Set by the window: says what is changing the library right now
        # ("copying", "videos") or "" when nothing is. A backup never runs
        # while photos are being copied or videos made smaller.
        self.busy_with: Callable[[], str] | None = None
        self._deferred_since = 0.0
        self._sync_deferred_since = 0.0
        self._owed = False           # it has waited its time: the videos let it have its turn
        # and how much is left of it: (videos left, seconds), for the status text
        self.busy_detail: Callable[[], tuple[int, float] | None] | None = None
        # whether a video is being made (or photos copied) at this very moment
        self.encoding_now: Callable[[], bool] | None = None
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
        # Another computer sharing the backup may have changed things: looked
        # for soon after opening, then every few minutes.
        if self._sync is not None:
            GLib.timeout_add_seconds(30, self._sync_tick)
            GLib.timeout_add_seconds(SYNC_MINUTES * 60, self._sync_tick)
        self.refresh_status()

    # -- another computer's changes ----------------------------------------
    @property
    def syncing_on(self) -> bool:
        return (self._sync is not None and self.active
                and bool(self.settings.get("sync_computers", True)))

    def _sync_progress(self, name: str):
        """What updating from ``name`` shows at the bottom of the sidebar, step by
        step: looking, receiving (with how far through), then adding it in."""
        def report(stage, done=0, total=0, percent=0, kind=""):
            if stage == "downloading" and total:
                text = {"photos": _("Receiving photos from {name} — {done} of {total} ({percent}%)"),
                        "videos": _("Receiving videos from {name} — {done} of {total} ({percent}%)"),
                        }.get(kind, _("Receiving from {name} — {done} of {total} ({percent}%)")).format(
                    name=name, done=f"{min(done + 1, total):,}", total=f"{total:,}", percent=percent)
            elif stage == "replacing" and total:
                text = _("Replacing videos with the smaller ones from {name} — {done} of {total} ({percent}%)").format(
                    name=name, done=f"{min(done + 1, total):,}", total=f"{total:,}", percent=percent)
            elif stage == "applying":
                text = _("Adding what came from {name}…").format(name=name)
            else:
                text = _("Updating from {name}…").format(name=name)
            GLib.idle_add(self.refresh_status, text)
        return report

    @property
    def nas_read(self) -> bool:
        """Has everything the other computers left in the backup been read
        yet, in this session? Anything that decides what to do from it - which
        videos still need making smaller - waits for this, or it does work a
        computer had already done. Always true when there is nothing to read."""
        return self._read_once or not self.syncing_on

    def _before_push(self, remote, backend) -> None:
        if self.syncing_on:
            self._sync(backend, self._sync_progress(remote.name))
            self._read_once = True

    def _sync_tick(self):
        """Look for another computer's changes, unless a backup is running
        (it brings them in first anyway). Keeps repeating."""
        if (not self.syncing_on or self._running or self._syncing
                or not self._network.get_network_available()
                or (self._power is not None and self._power.get_power_saver_enabled())):
            return GLib.SOURCE_CONTINUE
        why = self.busy_with() if self.busy_with is not None else ""
        if why:
            # The work on this computer comes first: photos being copied in,
            # videos being made smaller. The sync follows, and is not given up on.
            if not self._sync_deferred_since:
                from . import logs
                self._sync_deferred_since = time.monotonic()
                logs.get("backup").info("Sync waits: %s", why)
            if time.monotonic() - self._sync_deferred_since <= MAX_DEFERRED:
                return GLib.SOURCE_CONTINUE
            self._owed = True
            if self.encoding_now is not None and self.encoding_now():
                GLib.timeout_add_seconds(15, lambda: (self._sync_tick(), False)[1])
                return GLib.SOURCE_CONTINUE
        self._sync_deferred_since = 0.0
        self._owed = False
        self._syncing = True
        remotes = self.remotes

        def work():
            from . import system
            system.lower_thread_priority()
            changed = False
            read_all = True
            for r in remotes:
                backend = r.backend()
                report = self._sync_progress(r.name)
                report("looking")
                try:
                    if not backend.test().ok:
                        read_all = False
                        continue
                    result = self._sync(backend, report)
                    changed = changed or bool(getattr(result, "changed", False))
                except Exception as exc:
                    read_all = False
                    from . import logs
                    logs.get("backup").warning("Updating from %s failed: %s", r.name, exc)
            if read_all:
                self._read_once = True
            GLib.idle_add(self._sync_done, changed)
        threading.Thread(target=work, daemon=True).start()
        return GLib.SOURCE_CONTINUE

    def _sync_done(self, changed: bool):
        self._syncing = False
        self._progress = ""
        if changed:
            self.mark_changed(delay=5)      # what merged here goes back up
        self.refresh_status()
        return False

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
        if self._syncing:
            self._schedule(QUIET_SECONDS)       # after the update in progress
            return False
        why = self.busy_with() if self.busy_with is not None else ""
        if why and self._deferred_since and time.monotonic() - self._deferred_since > MAX_DEFERRED:
            # It has waited its time. The videos stop at the end of the one they are
            # making (they see _owed), and the backup goes then: the other computers
            # see what this one did in minutes, not at the end of a long queue.
            self._owed = True
            if self.encoding_now is not None and self.encoding_now():
                self._schedule(10)
                return False
            from . import logs
            logs.get("backup").info("Automatic backup waited %d minutes for %s; going now",
                                    MAX_DEFERRED // 60, why)
            why = ""
        if why:
            # Files are being written or replaced: sending them now would
            # send half of them. Ask again after a quiet minute.
            if not self._deferred_since:
                from . import logs
                self._deferred_since = time.monotonic()
                logs.get("backup").info("Automatic backup waits: %s", why)
            self._waiting = why
            self._schedule(QUIET_SECONDS)
            self.refresh_status()
            return False
        self._deferred_since = 0.0
        self._owed = False
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
            from . import system
            system.lower_thread_priority()      # whatever the person is doing comes first
            try:
                outcome = run_backup(root, remotes, keep, progress=lambda text:
                                     GLib.idle_add(self.refresh_status, text),
                                     before_push=self._before_push if self.syncing_on else None)
            except Exception as exc:
                outcome = Outcome(False, False, str(exc))
            GLib.idle_add(self._finished, outcome)
        threading.Thread(target=work, daemon=True).start()
        return True

    def _finished(self, outcome: Outcome):
        self._running = False
        self._progress = ""
        from . import logs
        blog = logs.get("backup")
        if outcome.ok and outcome.busy and not outcome.uploaded:
            # Another computer is sending its library there. Nothing is lost by
            # waiting: the changes stay pending and are looked at again soon.
            blog.info("Automatic backup waiting: %s is backing up to the same place",
                      outcome.busy)
            self._waiting, self._problem = "busy", outcome.busy
            self._schedule(BUSY_MINUTES * 60)
            self.refresh_status()
            return False
        if outcome.ok:
            blog.info("Automatic backup finished, %d file(s) uploaded", outcome.uploaded)
        elif outcome.unreachable:
            blog.warning("Automatic backup waiting, will retry (attempt %d): %s",
                         self._retry + 1, outcome.message)
        else:
            blog.error("Automatic backup stopped: %s", outcome.message)
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
        if (self._waiting in ("copying", "videos") and self.busy_with is not None
                and not self.busy_with()):
            self._waiting = ""          # what it waited for is over: do not go on saying so
        if self._running:
            return self._progress or _("Backing up…")
        if self._syncing:
            # receiving what another computer sent: shown as it happens
            return self._progress or _("Updating from your backup…")
        if not self.settings.get("remote_autosync"):
            return _("Automatic backup is off")
        if self._waiting == "busy":
            return _("{name} is backing up to the same place, waiting").format(
                name=self._problem)
        if self._waiting == "copying":
            return _("Backup waits until the photos are copied")
        if self._waiting == "videos":
            text = _("Backup waits until the videos are made smaller")
            left = self.busy_detail() if self.busy_detail is not None else None
            if left and left[0] > 0:
                text += " — " + ngettext("{count} video left", "{count} videos left",
                                         left[0]).format(count=f"{left[0]:,}")
                text += ", " + (_("a few minutes") if left[1] < 270 else
                                _("about {time}").format(time=duration_text(left[1])))
            return text
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
