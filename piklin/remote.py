"""Backing the library up to somewhere else.

Three backends cover essentially everything people actually have:

``local``   Any mounted path.  This is the one that quietly covers the
            most ground - a QNAP or Synology over NFS/SMB, a USB disk,
            pCloud Drive or Dropbox's own filesystem mount, an sshfs
            mount.  If the OS can see it as a directory, this works, with
            no credentials for us to hold.

``webdav``  Spoken natively by QNAP, Synology, Nextcloud/ownCloud, pCloud
            and Box.  Implemented here directly over urllib so it needs
            no external binary and no extra Python package.

``rclone``  Delegates to rclone when it is installed, which brings S3,
            Backblaze B2, Google Drive, OneDrive, Dropbox, Azure, SFTP,
            Mega and some seventy others in one adapter.  This is the
            answer to "and more which I don't know of": rather than
            writing a backend per provider, we hand off to the tool whose
            entire job is being that list.

Two rules across all of them:

*Passwords go in the system keyring* (libsecret), never into
settings.json.  A config file that quietly contains a cloud password is a
trap, especially in a folder the user has been told to back up.

*Sync is one-way by default.*  Pushing a library to a remote cannot
delete local originals, and the offload path keeps a local proxy so the
grid still works when the network is gone.
"""
from __future__ import annotations

import datetime
import functools
import hashlib
import http.client
import ipaddress
import json
import os
import re
import shutil
import ssl
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from base64 import b64encode
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable
from .i18n import _, ngettext

SERVICE = "Pikalicious"


# ==========================================================================
# credentials
# ==========================================================================
def _keyring():
    try:
        import gi
        gi.require_version("Secret", "1")
        from gi.repository import Secret
        return Secret
    except Exception:
        return None


SCHEMA_ATTRS = {"application": "pikalicious", "remote": ""}


def _schema(Secret):
    return Secret.Schema.new(
        "com.envy.Pikalicious", Secret.SchemaFlags.NONE,
        {"application": Secret.SchemaAttributeType.STRING,
         "remote": Secret.SchemaAttributeType.STRING})


def _unlock_default_keyring(Secret) -> bool:
    """Ask the desktop to unlock the default keyring.

    The login keyring is often left locked - after an automatic login, or
    when its password differs from the account's - and nothing can be
    stored in or read from a locked keyring. The desktop shows its own
    password prompt for it. True when the keyring is unlocked afterwards.
    """
    try:
        service = Secret.Service.get_sync(Secret.ServiceFlags.LOAD_COLLECTIONS, None)
        default = Secret.Collection.for_alias_sync(
            service, "default", Secret.CollectionFlags.NONE, None)
        if default is None:
            return False
        if default.get_locked():
            service.unlock_sync([default], None)
            default = Secret.Collection.for_alias_sync(
                service, "default", Secret.CollectionFlags.NONE, None)
        return default is not None and not default.get_locked()
    except Exception:
        return False


def store_secret(remote_id: str, secret: str) -> bool:
    """Put a password in the system keyring. False if that is impossible."""
    Secret = _keyring()
    if Secret is None:
        return False
    attrs = {"application": "pikalicious", "remote": remote_id}
    label = f"Piklin remote: {remote_id}"
    try:
        schema = _schema(Secret)
        try:
            return bool(Secret.password_store_sync(
                schema, attrs, Secret.COLLECTION_DEFAULT, label, secret, None))
        except Exception:
            if not _unlock_default_keyring(Secret):
                return False
            return bool(Secret.password_store_sync(
                schema, attrs, Secret.COLLECTION_DEFAULT, label, secret, None))
    except Exception:
        return False


def load_secret(remote_id: str) -> str | None:
    Secret = _keyring()
    if Secret is None:
        return None
    attrs = {"application": "pikalicious", "remote": remote_id}
    try:
        schema = _schema(Secret)
        value = Secret.password_lookup_sync(schema, attrs, None)
        if value is None and _unlock_default_keyring(Secret):
            value = Secret.password_lookup_sync(schema, attrs, None)
        return value
    except Exception:
        return None


def keyring_available() -> bool:
    return _keyring() is not None


# ==========================================================================
# results
# ==========================================================================
@dataclass
class SyncProgress:
    phase: str = "idle"
    total_files: int = 0
    done_files: int = 0
    total_bytes: int = 0
    done_bytes: int = 0
    current: str = ""
    uploaded: int = 0
    skipped: int = 0
    errors: int = 0
    restored: int = 0
    present: int = 0
    message: str = ""
    unreachable: bool = False       # the destination could not be reached

    @property
    def fraction(self) -> float:
        if self.total_bytes:
            return min(1.0, self.done_bytes / self.total_bytes)
        return (self.done_files / self.total_files) if self.total_files else 0.0


@dataclass
class TestResult:
    ok: bool
    message: str
    detail: str = ""
    free_bytes: int | None = None
    # SHA-256 of a certificate the user may choose to trust
    fingerprint: str = ""
    # What went wrong, for code to act on without reading the message,
    # which is translated.
    unreachable: bool = False       # offline, or the drive is unplugged
    not_found: bool = False         # the folder does not exist (yet)
    cert_changed: bool = False      # the trusted certificate was replaced


def _pairs(root: Path, files) -> Iterable[tuple[Path, str]]:
    """(local file, path at the destination). A plain path keeps its place
    in the library; a (file, path) pair sends a file under another name,
    such as the catalog snapshot."""
    for f in files:
        if isinstance(f, tuple):
            yield Path(f[0]), f[1]
        else:
            yield f, f.relative_to(root).as_posix()


# Never copied back into an open library: Piklin is using them.
NEVER_RESTORE = {"catalog.db", "settings.json"}
# Replaced from the backup only when the library is empty.
STATE_FILES = {"photo-state.json", "removed-photos.json", "watched-folders.json"}
# Where a destination keeps the copies a backup replaced, by day.
VERSIONS_DIR = ".piklin-versions"
# Rebuilt from the rest on every backup: no point keeping old copies.
NO_VERSIONS = {"catalog.db"}
# The folders a backup writes at the destination (see library_files). A
# backup folder may be shared with other things - a NAS photo share with a
# Lightroom catalog beside it - so a listing reads only these and the
# library's own files next to them, and never walks anything else: walking
# a catalog of previews there took many minutes on every backup, and a
# restore must never bring those files into the library.
BACKUP_FOLDERS = ("Edits", "Albums", "Originals")


# Backups go in a folder of their own inside the folder chosen for them,
# made when it is missing, so they never mix with what else is kept there.
BACKUP_SUBFOLDER = "Piklin"


def _in_piklin(path: str) -> str:
    """Where a backup lives for a chosen folder: its Piklin subfolder, or
    the folder itself when that is already called Piklin."""
    p = (path or "").strip().strip("/")
    if p.rsplit("/", 1)[-1].lower() == BACKUP_SUBFOLDER.lower():
        return p
    return f"{p}/{BACKUP_SUBFOLDER}" if p else BACKUP_SUBFOLDER


def _old_backup(names: dict) -> list[str]:
    """Top-level names of a backup made before the Piklin folder existed -
    straight in the chosen folder - or [] when it doesn't look like one.
    ``names``: name -> is folder. Only Piklin's own items are listed."""
    if names.get(BACKUP_SUBFOLDER) is True:
        return []                           # already has its folder
    if names.get("catalog.db") is not False or not any(
            names.get(f) is True for f in BACKUP_FOLDERS):
        return []                           # not a Piklin backup
    return sorted(n for n, is_dir in names.items()
                  if (is_dir and (n in BACKUP_FOLDERS or n == VERSIONS_DIR))
                  or (not is_dir and _ours(n)))


def _ours(rel: str) -> bool:
    """Whether a path at the destination is part of Piklin's backup."""
    first, sep, rest = rel.partition("/")
    if not sep:
        return first in ("catalog.db", "README.txt") or first.endswith(".json")
    return first in BACKUP_FOLDERS and bool(rest)


# ==========================================================================
# base
# ==========================================================================
@dataclass
class Remote:
    """One configured destination."""
    id: str
    name: str
    kind: str                       # local | webdav | rclone
    # local:  {"path": "/mnt/nas/photos"}
    # webdav: {"url": "...", "username": "...", "base": "/Photos"}
    # rclone: {"remote": "pcloud:", "path": "Pikalicious"}
    config: dict = field(default_factory=dict)
    enabled: bool = True
    last_sync: float | None = None

    def backend(self) -> "Backend":
        return make_backend(self)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "kind": self.kind,
                "config": self.config, "enabled": self.enabled,
                "last_sync": self.last_sync}

    @classmethod
    def from_dict(cls, d: dict) -> "Remote":
        return cls(id=d.get("id") or hashlib.blake2b(
                       str(d).encode(), digest_size=8).hexdigest(),
                   name=d.get("name", "Remote"), kind=d.get("kind", "local"),
                   config=dict(d.get("config") or {}),
                   enabled=bool(d.get("enabled", True)),
                   last_sync=d.get("last_sync"))


class _CountingReader:
    """A file being uploaded, telling how much of it has been read so far."""

    def __init__(self, fh, report: Callable[[int], None]):
        self.fh = fh
        self.report = report
        self.sent = 0

    def read(self, n: int = -1) -> bytes:
        chunk = self.fh.read(n if n and n > 0 else 1 << 20)
        self.sent += len(chunk)
        self.report(self.sent)
        return chunk


class Backend:
    """What every destination must be able to do."""

    def __init__(self, remote: Remote):
        self.remote = remote
        self.cancel = threading.Event()
        self._prepared = False
        # set by push while a file uploads: bytes of it sent so far
        self._on_bytes: Callable[[int], None] | None = None

    def prepare(self) -> None:
        """Make sure the Piklin folder is there before using it. A backup
        made before there was one is first moved into it, on the destination
        itself, so nothing is sent again; only Piklin's own files move.
        Problems are left for the test or the backup to report."""
        if self._prepared:
            return
        try:
            self._prepare()
            self._prepared = True
        except Exception:
            pass

    def _prepare(self) -> None:
        pass

    def test(self) -> TestResult:
        raise NotImplementedError

    def put(self, local: Path, rel: str) -> bool:
        raise NotImplementedError

    def get(self, rel: str, local: Path) -> bool:
        raise NotImplementedError

    def listing(self) -> dict[str, tuple[int, float]]:
        """Map of relative path -> (size, mtime) already on the remote."""
        raise NotImplementedError

    def list_folders(self, path: str) -> list[str]:
        """Subfolders of ``path`` on the destination, for choosing where
        backups go. Raises on failure; ``problem`` explains the error."""
        raise NotImplementedError

    def problem(self, exc: Exception) -> TestResult:
        # rclone reports a missing folder in English, whatever the language
        return TestResult(False, _("Couldn't show the folders"), str(exc),
                          not_found="not found" in str(exc).lower())

    # -- previous versions ------------------------------------------------
    # A changed file is not simply overwritten at the destination: the copy
    # there first moves to .piklin-versions/<date>/, and is kept for some
    # days, so an earlier edit can still be brought back from the NAS or
    # the cloud. Old days are cleared while a backup runs, never on their own.
    def _archive(self, rel: str, stamp: str) -> None:
        pass

    def _version_days(self) -> list[str]:
        return []

    def _drop_version(self, stamp: str) -> None:
        pass

    def _prune_versions(self, keep_days: int) -> None:
        cutoff = (datetime.date.today() - datetime.timedelta(days=keep_days)).isoformat()
        for stamp in self._version_days():
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", stamp) and stamp < cutoff:
                self._drop_version(stamp)

    def has_local_changes(self, root: Path, files) -> bool:
        """Whether anything differs from what was last sent here.

        Read from the local record alone - the destination is not contacted,
        so checking costs no network and next to no power."""
        root = Path(root)
        manifest = self._load_manifest(root)
        for f, rel in _pairs(root, files):
            try:
                st = f.stat()
            except OSError:
                continue
            sent = manifest.get(rel)
            if not sent or sent[0] != st.st_size or abs(sent[1] - st.st_mtime) >= 1:
                return True
        return False

    # -- shared sync logic ----------------------------------------------
    # What was sent to this destination: relative path -> [size, mtime] of
    # the local file when it was uploaded. Many servers stamp a file with
    # the time it arrived rather than its own modification time; with this
    # record an unchanged photo is still recognised and never sent twice.
    def _manifest_path(self, root: Path) -> Path:
        return Path(root) / ".cache" / "backups" / f"{self.remote.id}.json"

    def _load_manifest(self, root: Path) -> dict:
        try:
            data = json.loads(self._manifest_path(root).read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_manifest(self, root: Path, manifest: dict) -> None:
        path = self._manifest_path(root)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(manifest))
            tmp.replace(path)
        except OSError:
            pass

    def _plan(self, root: Path, files: Iterable[Path],
              remote_index: dict, manifest: dict):
        """Files to send: missing on the destination, or modified since
        they were sent. Returns (todo, skipped)."""
        todo, skipped = [], 0
        for f, rel in _pairs(root, files):
            try:
                st = f.stat()
            except OSError:
                continue
            have = remote_index.get(rel)
            if have and have[0] == st.st_size:
                sent = manifest.get(rel)
                same_time = abs(have[1] - st.st_mtime) < 2
                sent_unchanged = (bool(sent) and sent[0] == st.st_size
                                  and abs(sent[1] - st.st_mtime) < 1)
                if same_time or sent_unchanged:
                    manifest[rel] = [st.st_size, st.st_mtime]
                    skipped += 1
                    continue
            todo.append((f, rel, st.st_size, st.st_mtime))
        return todo, skipped

    def push(self, root: Path, files: Iterable[Path],
             on_progress: Callable[[SyncProgress], None] | None = None,
             keep_versions_days: int = 0,
             ) -> SyncProgress:
        """Upload what is missing on the destination or changed since.

        A file already on the destination is never sent again unless it
        was modified. Size and modification time decide, as in every
        practical sync tool; hashing a 200 GB library on every run to find
        the three files that changed would cost far more than it saves.
        """
        root = Path(root)
        p = SyncProgress(phase="listing")
        if on_progress:
            on_progress(p)
        self.prepare()
        try:
            remote_index = self.listing()
        except Exception as exc:
            p.phase = "error"
            p.message = _("Couldn't read the backup destination: {error}").format(error=exc)
            p.unreachable = True
            if on_progress:
                on_progress(p)
            return p

        manifest = self._load_manifest(root)
        todo, p.skipped = self._plan(root, files, remote_index, manifest)
        stamp = datetime.date.today().isoformat()
        p.phase = "uploading"
        p.total_files = len(todo)
        p.total_bytes = sum(t[2] for t in todo)
        if on_progress:
            on_progress(p)

        for n, (f, rel, size, mtime) in enumerate(todo, 1):
            if self.cancel.is_set():
                p.phase = "cancelled"
                break
            p.current = rel
            if keep_versions_days and rel in remote_index and rel not in NO_VERSIONS:
                try:
                    self._archive(rel, stamp)
                except Exception:
                    pass        # the backup goes on; only this old copy is lost
            # A big video takes minutes to go up: report its bytes as they
            # go, a few times a second, so the progress never looks stuck.
            before, last = p.done_bytes, [0.0]

            def sent(n, before=before, last=last):
                now = time.monotonic()
                if on_progress and now - last[0] >= 0.5:
                    last[0] = now
                    p.done_bytes = before + n
                    on_progress(p)
            self._on_bytes = sent
            try:
                ok = self.put(f, rel)
            except Exception:
                ok = False
            finally:
                self._on_bytes = None
                p.done_bytes = before
            if ok:
                p.uploaded += 1
                manifest[rel] = [size, mtime]
            else:
                p.errors += 1
            p.done_files += 1
            p.done_bytes += size
            if n % 50 == 0:
                self._save_manifest(root, manifest)
            if on_progress:
                on_progress(p)
        self._save_manifest(root, manifest)
        if keep_versions_days and p.phase != "cancelled" and not p.errors:
            try:
                self._prune_versions(keep_versions_days)
            except Exception:
                pass

        if p.phase != "cancelled":
            if p.errors and not p.uploaded:
                p.phase = "error"
                p.message = ngettext("The file couldn't be uploaded",
                                     "None of the {count} files could be uploaded",
                                     p.errors).format(count=p.errors)
            else:
                p.phase = "done"
        if on_progress:
            on_progress(p)
        return p


    def restore(self, root: Path, skip_paths: Iterable[str] = (),
                overwrite_state: bool = False,
                on_progress: Callable[[SyncProgress], None] | None = None,
                ) -> SyncProgress:
        """Copy back what is missing from the library.

        Only files that are gone come back: nothing in the library is ever
        replaced. ``skip_paths`` are photos the user removed on purpose;
        they stay out. ``overwrite_state`` is for an empty library, where
        the favourites and album state from the backup should win.
        """
        root = Path(root)
        p = SyncProgress(phase="listing")
        if on_progress:
            on_progress(p)
        self.prepare()
        try:
            index = self.listing()
        except Exception as exc:
            p.phase = "error"
            p.message = _("Couldn't read the backup: {error}").format(error=exc)
            p.unreachable = True
            if on_progress:
                on_progress(p)
            return p

        manifest = self._load_manifest(root)
        skip = {os.path.normpath(s) for s in skip_paths}

        def order(rel: str):
            # edits, albums and state first: small and irreplaceable
            return (0 if "/" not in rel or rel.startswith(("Edits/", "Albums/"))
                    else 1, rel)

        todo = []
        for rel in sorted(index, key=order):
            parts = rel.split("/")
            if (not rel or ".." in parts or parts[0].startswith(".")
                    or rel.endswith(".part") or rel in NEVER_RESTORE):
                continue
            local = root / rel
            if local.exists() and not (overwrite_state and rel in STATE_FILES):
                p.present += 1
                continue
            if os.path.normpath(str(local)) in skip:
                p.skipped += 1
                continue
            todo.append((rel, index[rel][0]))

        p.phase = "downloading"
        p.total_files = len(todo)
        p.total_bytes = sum(max(size, 0) for _rel, size in todo)
        if on_progress:
            on_progress(p)
        if todo:
            self._fetch_many(root, todo, p, manifest, on_progress)
        self._save_manifest(root, manifest)

        if p.phase != "cancelled":
            if p.errors and not p.restored:
                p.phase = "error"
                p.message = p.message or ngettext(
                    "The file couldn't be restored",
                    "None of the {count} files could be restored", p.errors).format(count=p.errors)
            else:
                p.phase = "done"
        if on_progress:
            on_progress(p)
        return p

    def _fetch_many(self, root: Path, todo, p: SyncProgress, manifest: dict,
                    on_progress) -> None:
        for rel, size in todo:
            if self.cancel.is_set():
                p.phase = "cancelled"
                break
            local = root / rel
            tmp = local.with_name(local.name + ".part")
            p.current = rel
            try:
                local.parent.mkdir(parents=True, exist_ok=True)
                ok = (self.get(rel, tmp) and tmp.is_file()
                      and (size <= 0 or tmp.stat().st_size == size))
            except Exception:
                ok = False
            if ok:
                tmp.replace(local)
                sent = manifest.get(rel)
                if sent and sent[0] == size:
                    # the photo's own time, not the moment it came back
                    os.utime(local, (sent[1], sent[1]))
                st = local.stat()
                # recorded as backed up: the next backup won't send it again
                manifest[rel] = [st.st_size, st.st_mtime]
                p.restored += 1
            else:
                p.errors += 1
                try:
                    tmp.unlink()
                except OSError:
                    pass
            p.done_files += 1
            p.done_bytes += max(size, 0)
            if on_progress:
                on_progress(p)


# ==========================================================================
# local / mounted
# ==========================================================================
class LocalBackend(Backend):
    """A mounted directory: NAS, external disk, or a cloud drive mount."""

    @property
    def chosen(self) -> Path:
        """The folder picked for backups."""
        return Path(self.remote.config.get("path", "")).expanduser()

    @property
    def base(self) -> Path:
        """Where the backup is: the Piklin folder inside the chosen one."""
        c = self.chosen
        return c if c.name.lower() == BACKUP_SUBFOLDER.lower() else c / BACKUP_SUBFOLDER

    def _prepare(self) -> None:
        chosen, base = self.chosen, self.base
        if not chosen.is_dir() or base == chosen:
            return
        if not base.exists():
            names = {p.name: p.is_dir() for p in chosen.iterdir()}
            old = _old_backup(names)
            base.mkdir()
            for name in old:
                (chosen / name).rename(base / name)

    def test(self) -> TestResult:
        if not self.remote.config.get("path", "").strip():
            return TestResult(False, _("No folder chosen"))
        b = self.chosen
        if not b.exists():
            return TestResult(False, _("This folder can't be found"), str(b), unreachable=True)
        if not b.is_dir():
            return TestResult(False, _("Not a folder"), str(b))
        if not os.access(b, os.W_OK):
            return TestResult(False, _("Piklin can't save files in this folder"), str(b))
        try:
            st = os.statvfs(b)
            free = st.f_bavail * st.f_frsize
        except OSError:
            free = None
        # A network mount that has dropped often still looks like a
        # directory but blocks on the first real operation, so touch it.
        probe = b / ".pikalicious-write-test"
        try:
            probe.write_bytes(b"ok")
            probe.unlink()
        except OSError as exc:
            return TestResult(False, _("Piklin can't save files in this folder"), str(exc),
                              unreachable=True)
        self.prepare()
        return TestResult(True, _("Connected"), str(self.base), free)

    def listing(self) -> dict[str, tuple[int, float]]:
        out: dict[str, tuple[int, float]] = {}
        base = self.base
        if not base.is_dir():
            return out
        places = [(base, False)] + [(base / f, True) for f in BACKUP_FOLDERS]
        for top, deep in places:
            if not top.is_dir():
                continue
            for dirpath, dirnames, names in os.walk(top):
                if not deep:
                    dirnames.clear()        # only the files beside the folders
                for n in names:
                    fp = Path(dirpath) / n
                    rel = fp.relative_to(base).as_posix()
                    if not _ours(rel):
                        continue
                    try:
                        st = fp.stat()
                    except OSError:
                        continue
                    out[rel] = (st.st_size, st.st_mtime)
        return out

    def put(self, local: Path, rel: str) -> bool:
        dest = self.base / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        # copy2 preserves mtime, which is what the next sync compares on
        shutil.copy2(local, tmp)
        tmp.replace(dest)
        return True

    def _archive(self, rel: str, stamp: str) -> None:
        src = self.base / rel
        if src.is_file():
            dest = self.base / VERSIONS_DIR / stamp / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, dest)

    def _version_days(self) -> list[str]:
        d = self.base / VERSIONS_DIR
        return [x.name for x in d.iterdir() if x.is_dir()] if d.is_dir() else []

    def _drop_version(self, stamp: str) -> None:
        shutil.rmtree(self.base / VERSIONS_DIR / stamp, ignore_errors=True)

    def get(self, rel: str, local: Path) -> bool:
        src = self.base / rel
        if not src.is_file():
            return False
        local.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, local)
        return True


# ==========================================================================
# WebDAV
# ==========================================================================
def _hidden_folder(name: str) -> bool:
    # dot folders, and NAS system folders such as @Recycle or #recycle
    return name.startswith((".", "@", "#"))


def _parse_multistatus(body: str, prefix: str) -> list[tuple[str, bool, int, float]]:
    """Entries of a WebDAV PROPFIND reply, with paths relative to ``prefix``."""
    import re
    from email.utils import parsedate_to_datetime
    out = []
    for block in re.findall(r"<[^>]*response[^>]*>(.*?)</[^>]*response>",
                            body, re.S | re.I):
        href = re.search(r"<[^>]*href[^>]*>(.*?)</[^>]*href>", block, re.S | re.I)
        if not href:
            continue
        path = urllib.parse.unquote(urllib.parse.urlparse(href.group(1).strip()).path)
        if prefix and path.rstrip("/") == prefix:
            path = ""
        elif prefix and path.startswith(prefix + "/"):
            path = path[len(prefix):]
        path = path.strip("/")
        is_folder = bool(re.search(r"<[^>]*collection\s*/?>", block, re.I))
        size = re.search(r"getcontentlength[^>]*>(\d+)<", block, re.I)
        mod = re.search(r"getlastmodified[^>]*>(.*?)<", block, re.I)
        ts = 0.0
        if mod:
            try:
                ts = parsedate_to_datetime(mod.group(1).strip()).timestamp()
            except Exception:
                ts = 0.0
        out.append((path, is_folder, int(size.group(1)) if size else 0, ts))
    return out


class CertificateChanged(OSError):
    """The server shows another certificate than the one the user trusted.
    The argument is the new certificate's SHA-256."""


def _is_local_host(host: str | None) -> bool:
    """A server on the home network, where plain http:// is acceptable."""
    if not host:
        return False
    h = host.lower().strip("[]")
    if h == "localhost" or "." not in h or h.endswith((".local", ".lan", ".home.arpa")):
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def certificate_fingerprint(url: str) -> str:
    u = urllib.parse.urlparse(url)
    pem = ssl.get_server_certificate((u.hostname, u.port or 443), timeout=15)
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()


def format_fingerprint(fp: str) -> str:
    pairs = [fp[i:i + 2].upper() for i in range(0, len(fp), 2)]
    return "\n".join(":".join(pairs[i:i + 16]) for i in range(0, len(pairs), 16))


class _PinnedConnection(http.client.HTTPSConnection):
    """HTTPS to a server with its own certificate: instead of a certificate
    authority, the exact certificate the user trusted is required."""

    def __init__(self, *args, pin: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self._pin = pin

    def connect(self):
        super().connect()
        seen = hashlib.sha256(self.sock.getpeercert(binary_form=True) or b"").hexdigest()
        if seen != self._pin:
            self.sock.close()
            raise CertificateChanged(seen)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, pin: str):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE      # checked against the pin instead
        super().__init__(context=ctx)
        self._pin = pin
        self._pinned_context = ctx

    def https_open(self, req):
        return self.do_open(functools.partial(_PinnedConnection, pin=self._pin),
                            req, context=self._pinned_context)


class WebDavBackend(Backend):
    """Native WebDAV: QNAP, Synology, Nextcloud, ownCloud, pCloud, Box."""

    TIMEOUT = 60

    def __init__(self, remote: Remote):
        super().__init__(remote)
        self._folders: set[str] = set()
        self._opener = None
        self._pw: str | None = None

    @property
    def url(self) -> str:
        return self.remote.config.get("url", "").strip().rstrip("/")

    @property
    def chosen_path(self) -> str:
        """The folder picked on the server, relative to its address."""
        return self.remote.config.get("base", "").strip().strip("/")

    @property
    def base_path(self) -> str:
        """Where the backup is: the Piklin folder inside the chosen one."""
        return _in_piklin(self.chosen_path)

    def _prepare(self) -> None:
        chosen, base = self.chosen_path, self.base_path
        if base == chosen:
            return
        body = self._request_path("PROPFIND", chosen, extra={"Depth": "1"}).read()
        prefix = urllib.parse.urlparse(self.url).path.rstrip("/")
        if chosen:
            prefix += "/" + chosen
        names = {rel: is_dir for rel, is_dir, _s, _t
                 in _parse_multistatus(body.decode("utf-8", "replace"), prefix)
                 if rel and "/" not in rel}
        if names.get(BACKUP_SUBFOLDER) is True:
            return
        old = _old_backup(names)
        self._ensure_folder(base)
        for name in old:
            src = f"{chosen}/{name}" if chosen else name
            self._request_path("MOVE", src + ("/" if names[name] else ""), extra={
                "Destination": f"{self.url}/{urllib.parse.quote(base + '/' + name)}"
                               + ("/" if names[name] else ""),
                "Overwrite": "F"})

    @property
    def pin(self) -> str:
        return self.remote.config.get("cert_sha256", "")

    def _secure(self) -> bool:
        u = urllib.parse.urlparse(self.url)
        return u.scheme == "https" or (u.scheme == "http" and _is_local_host(u.hostname))

    def _password(self) -> str:
        if self._pw is None:
            self._pw = (self.remote.config.get("password")
                        or load_secret(self.remote.id) or "")
        return self._pw

    def _auth_header(self) -> dict:
        user = self.remote.config.get("username", "")
        if not user or not self._secure():
            return {}
        token = b64encode(f"{user}:{self._password()}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def _full(self, rel: str = "") -> str:
        return "/".join(p for p in (self.base_path, rel.strip("/")) if p)

    def _open(self, req):
        if self._opener is None:
            handlers = ([_PinnedHTTPSHandler(self.pin)]
                        if self.pin and self.url.startswith("https://") else [])
            self._opener = urllib.request.build_opener(*handlers)
        return self._opener.open(req, timeout=self.TIMEOUT)

    def _request_path(self, method: str, path: str, data=None,
                      extra: dict | None = None):
        url = f"{self.url}/{urllib.parse.quote(path)}" if path else self.url + "/"
        req = urllib.request.Request(url, data=data, method=method)
        for k, v in {**self._auth_header(), **(extra or {})}.items():
            req.add_header(k, v)
        return self._open(req)

    def _request(self, method: str, rel: str = "", data=None,
                 extra: dict | None = None):
        return self._request_path(method, self._full(rel), data, extra)

    def _ensure_folder(self, path: str) -> None:
        """Create every missing folder of ``path`` (relative to the URL)."""
        parts = [p for p in path.split("/") if p]
        for i in range(1, len(parts) + 1):
            sub = "/".join(parts[:i])
            if sub in self._folders:
                continue
            try:
                self._request_path("MKCOL", sub)
            except urllib.error.HTTPError as exc:
                if exc.code == 401:
                    raise
                if exc.code not in (301, 405) and i == len(parts):
                    raise           # the folder itself could not be made
                # 405 or 301: it already exists (or is a share we can't make)
            self._folders.add(sub)

    def test(self) -> TestResult:
        if not self.url:
            return TestResult(False, _("Enter the server address"))
        u = urllib.parse.urlparse(self.url)
        if u.scheme not in ("http", "https") or not u.hostname:
            return TestResult(False, _("The address must start with https:// or http://"))
        if u.scheme == "http" and not _is_local_host(u.hostname):
            return TestResult(False, _("http:// only works at home"),
                              _("Use https:// for a server on the internet, so "
                              "your password stays private"))
        if self.remote.config.get("username") and not self._password():
            return TestResult(False, _("No password saved"),
                              _("Edit this backup and enter the password again"))
        self.prepare()
        try:
            try:
                self._request("PROPFIND", extra={"Depth": "0"})
                return TestResult(True, _("Connected"), self.url)
            except urllib.error.HTTPError as exc:
                if exc.code != 404 or not self.base_path:
                    raise
            # The backup folder does not exist yet: create it.
            try:
                self._ensure_folder(self.base_path)
                self._request("PROPFIND", extra={"Depth": "0"})
            except urllib.error.HTTPError as exc:
                if exc.code == 401:
                    raise
                return TestResult(
                    False, _("Couldn't create the folder /{folder}").format(folder=self.base_path),
                    _("The server answered HTTP {code}. The folder must be inside a shared "
                      "folder you can write to").format(code=exc.code))
            return TestResult(True, _("Connected"),
                              _("created the folder /{folder}").format(folder=self.base_path))
        except urllib.error.HTTPError as exc:
            return self._http_problem(exc)
        except urllib.error.URLError as exc:
            return self._connection_problem(exc.reason)
        except Exception as exc:
            return self._connection_problem(exc)

    def _http_problem(self, exc: urllib.error.HTTPError) -> TestResult:
        code = exc.code
        if code == 401:
            return TestResult(False, _("Wrong username or password"), _("Check the username and password"))
        if code == 403:
            return TestResult(False, _("Not allowed"),
                              _("This account isn't allowed to use that folder"))
        if code in (405, 501):
            return TestResult(False, _("This address doesn't accept backups"),
                              _("Turn on WebDAV on your server and use the address it shows for it. "
                                "On a QNAP: Control Panel › Network & File Services › "
                                "Win/Mac/NFS/WebDAV"))
        if code in (301, 302, 303, 307, 308):
            loc = exc.headers.get("Location", "") if exc.headers else ""
            return TestResult(False, _("The server says to use another address"),
                              _("Try {address}").format(address=loc) if loc else f"HTTP {code}")
        if code == 404:
            return TestResult(False, _("This folder isn't on the server"),
                              _("Check the address and the folder"), not_found=True)
        return TestResult(False, _("The server answered HTTP {code}").format(code=code),
                          str(exc.reason))

    def _connection_problem(self, reason) -> TestResult:
        if isinstance(reason, CertificateChanged):
            return TestResult(False, _("The server's security certificate has changed"),
                              _("Trust the new one only if you replaced it yourself"),
                              fingerprint=str(reason), cert_changed=True)
        if isinstance(reason, ssl.SSLCertVerificationError):
            try:
                fp = certificate_fingerprint(self.url)
            except Exception:
                fp = ""
            return TestResult(False, _("Confirm this server is yours"),
                              _("It uses its own security certificate, which is normal for a NAS at home"), fingerprint=fp)
        if isinstance(reason, ssl.SSLError):
            return TestResult(False, _("A secure connection couldn't be made"), str(reason))
        return TestResult(False, _("Can't reach the server"),
                          str(getattr(reason, "strerror", None) or reason), unreachable=True)

    def _entries(self, rel: str, depth: str) -> list[tuple[str, bool, int, float]]:
        """(path relative to the backup folder, is folder, size, mtime)."""
        body = self._request("PROPFIND", rel, extra={"Depth": depth}).read()
        prefix = urllib.parse.urlparse(self.url).path.rstrip("/")
        if self.base_path:
            prefix += "/" + self.base_path
        return _parse_multistatus(body.decode("utf-8", "replace"), prefix)

    def list_folders(self, path: str) -> list[str]:
        path = path.strip().strip("/")
        body = self._request_path("PROPFIND", path, extra={"Depth": "1"}).read()
        prefix = urllib.parse.urlparse(self.url).path.rstrip("/")
        if path:
            prefix += "/" + path
        names = {rel for rel, is_folder, _s, _t
                 in _parse_multistatus(body.decode("utf-8", "replace"), prefix)
                 if is_folder and rel and "/" not in rel}
        return sorted((n for n in names if not _hidden_folder(n)), key=str.lower)

    def problem(self, exc: Exception) -> TestResult:
        if isinstance(exc, urllib.error.HTTPError):
            return self._http_problem(exc)
        if isinstance(exc, urllib.error.URLError):
            return self._connection_problem(exc.reason)
        return self._connection_problem(exc)

    def listing(self) -> dict[str, tuple[int, float]]:
        """Sizes and modification times of Piklin's files already on the
        server. Only the backup's own folders are read (see BACKUP_FOLDERS)."""
        try:
            top = self._entries("", "1")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {}                       # nothing backed up yet
            raise
        found: dict[str, tuple[int, float]] = {}
        folders = []
        for r, is_folder, size, ts in top:
            if not r or "/" in r:
                continue
            if is_folder:
                if r in BACKUP_FOLDERS:
                    folders.append(r)
            elif _ours(r):
                found[r] = (size, ts)
        for folder in folders:
            found.update(self._folder_listing(folder))
        return found

    def _folder_listing(self, folder: str) -> dict[str, tuple[int, float]]:
        try:
            entries = self._entries(folder, "infinity")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {}
            if exc.code in (401, 501):
                raise
            # Depth: infinity refused - Apache, and so QNAP, answers 403
            # by default. A real lack of access fails in the walk below.
            entries = None
        if entries is not None:
            # A server that quietly ignores Depth: infinity (Nextcloud by
            # default) returns one level only: a folder shows up without
            # anything inside it. Walk then, one level at a time.
            folders = {r for r, d, _s, _t in entries if d and r and r != folder}
            parents = {r.rsplit("/", 1)[0] for r, _d, _s, _t in entries if "/" in r}
            if any(f not in parents for f in folders):
                entries = None
        if entries is None:
            entries, pending, seen = [], [folder], {folder}
            while pending:
                current = pending.pop()
                for r, is_folder, size, ts in self._entries(current, "1"):
                    if r in seen:
                        continue
                    seen.add(r)
                    if is_folder:
                        pending.append(r)
                    else:
                        entries.append((r, False, size, ts))
        return {r: (size, ts) for r, is_folder, size, ts in entries
                if r and not is_folder and _ours(r)}

    def _archive(self, rel: str, stamp: str) -> None:
        dest = f"{VERSIONS_DIR}/{stamp}/{rel}"
        self._ensure_folder(self._full("/".join(dest.split("/")[:-1])))
        self._request("MOVE", rel, extra={
            "Destination": f"{self.url}/{urllib.parse.quote(self._full(dest))}",
            "Overwrite": "T"})

    def _version_days(self) -> list[str]:
        try:
            return self.list_folders(self._full(VERSIONS_DIR))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return []
            raise

    def _drop_version(self, stamp: str) -> None:
        self._request("DELETE", f"{VERSIONS_DIR}/{stamp}/")

    def put(self, local: Path, rel: str) -> bool:
        parent = "/".join(rel.split("/")[:-1])
        try:
            self._ensure_folder(self._full(parent))
        except Exception:
            return False
        st = local.stat()
        # Streamed from the file: a 4 GB video is not read into memory.
        with open(local, "rb") as fh:
            body = _CountingReader(fh, self._on_bytes) if self._on_bytes else fh
            try:
                self._request("PUT", rel, data=body, extra={
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(st.st_size),
                    # Nextcloud and ownCloud keep the file's own time
                    "X-OC-Mtime": str(int(st.st_mtime)),
                })
                return True
            except Exception:
                return False

    def get(self, rel: str, local: Path) -> bool:
        try:
            resp = self._request("GET", rel)
            local.parent.mkdir(parents=True, exist_ok=True)
            with open(local, "wb") as out:
                shutil.copyfileobj(resp, out)
            return True
        except Exception:
            return False


# ==========================================================================
# rclone
# ==========================================================================
def rclone_path() -> str | None:
    return shutil.which("rclone")


def rclone_remotes() -> list[str]:
    """Remotes the user already configured in rclone."""
    exe = rclone_path()
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "listremotes"], capture_output=True,
                             text=True, timeout=15)
        return [l.strip() for l in out.stdout.splitlines() if l.strip()]
    except Exception:
        return []


class RcloneBackend(Backend):
    """Delegate to rclone, for the seventy-odd providers it speaks."""

    @property
    def target(self) -> str:
        """rclone's name for where the backup is: the Piklin folder inside
        the chosen one."""
        remote = self.remote.config.get("remote", "").rstrip(":")
        return f"{remote}:{self._base()}"

    def _base(self) -> str:
        return _in_piklin(self.remote.config.get("path", ""))

    def _prepare(self) -> None:
        exe = rclone_path()
        remote = self.remote.config.get("remote", "").strip().rstrip(":")
        if not exe or not remote:
            return
        chosen = self.remote.config.get("path", "").strip().strip("/")
        if self._base() == chosen:
            return
        out = subprocess.run([exe, "lsjson", "--max-depth", "1", f"{remote}:{chosen}"],
                             capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            return                              # nothing there yet, or unreachable
        names = {it.get("Name", ""): bool(it.get("IsDir"))
                 for it in json.loads(out.stdout or "[]") if it.get("Name")}
        if names.get(BACKUP_SUBFOLDER) is True:
            return
        subprocess.run([exe, "mkdir", self.target], capture_output=True, text=True, timeout=60)
        for name in _old_backup(names):
            src = f"{remote}:{chosen}/{name}" if chosen else f"{remote}:{name}"
            subprocess.run([exe, "moveto", src, f"{self.target}/{name}"],
                           capture_output=True, text=True, timeout=1800)

    def test(self) -> TestResult:
        exe = rclone_path()
        if not exe:
            return TestResult(
                False, _("rclone is not installed"),
                _("Install rclone to use Google Drive, OneDrive, Dropbox, pCloud and "
                  "more: sudo apt install rclone (then run: rclone config)"))
        remote = self.remote.config.get("remote", "").rstrip(":")
        if not remote:
            return TestResult(False, _("Enter the name of the cloud service"),
                              _("Set up in rclone: {names}").format(
                                  names=", ".join(rclone_remotes()) or _("none")))
        self.prepare()
        try:
            # The backup folder may not exist yet; mkdir is harmless if it does.
            made = subprocess.run([exe, "mkdir", self.target],
                                  capture_output=True, text=True, timeout=60)
            out = subprocess.run([exe, "lsd", self.target, "--max-depth", "1"],
                                 capture_output=True, text=True, timeout=45)
            if out.returncode == 0:
                return TestResult(True, _("Connected"), self.target)
            return TestResult(False, _("rclone can't reach the cloud service"),
                              (out.stderr or made.stderr or out.stdout).strip()[:400],
                              unreachable=True)
        except subprocess.TimeoutExpired:
            return TestResult(False, _("The cloud service took too long to answer"),
                              unreachable=True)
        except Exception as exc:
            return TestResult(False, _("rclone failed"), str(exc))

    def list_folders(self, path: str) -> list[str]:
        exe = rclone_path()
        if not exe:
            raise RuntimeError(_("rclone is not installed"))
        remote = self.remote.config.get("remote", "").strip().rstrip(":")
        if not remote:
            raise RuntimeError(_("Enter the name of the cloud service first"))
        out = subprocess.run(
            [exe, "lsjson", "--dirs-only", f"{remote}:{path.strip().strip('/')}"],
            capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            err = (out.stderr or "rclone failed").strip().splitlines()[-1]
            raise RuntimeError(err.split(" : ", 1)[-1][:300])
        names = [it.get("Name", "") for it in json.loads(out.stdout or "[]")]
        return sorted((n for n in names if n and not _hidden_folder(n)), key=str.lower)

    def listing(self) -> dict[str, tuple[int, float]]:
        exe = rclone_path()
        if not exe:
            return {}
        # Only the backup's own folders and files: rclone then never descends
        # into anything else kept in the same place.
        only = [a for f in BACKUP_FOLDERS for a in ("--include", f"/{f}/**")]
        only += ["--include", "/*.json", "--include", "/README.txt",
                 "--include", "/catalog.db"]
        out = subprocess.run(
            [exe, "lsjson", "-R", "--files-only", *only, self.target],
            capture_output=True, text=True, timeout=300)
        if out.returncode != 0:
            if out.returncode == 3 or "not found" in out.stderr.lower():
                return {}                       # nothing backed up yet
            # Unreachable or signed out: never mistake that for "empty",
            # which would send the whole library again.
            err = (out.stderr or "rclone lsjson failed").strip().splitlines()[-1]
            # drop rclone's "2026/09/13 12:35:22 ERROR : " prefix
            raise RuntimeError(err.split(" : ", 1)[-1][:300])
        items = json.loads(out.stdout or "[]")
        res = {}
        for it in items:
            ts = 0.0
            mod = it.get("ModTime")
            if mod:
                try:
                    from datetime import datetime
                    ts = datetime.fromisoformat(
                        mod.replace("Z", "+00:00")).timestamp()
                except Exception:
                    ts = 0.0
            res[it.get("Path", "")] = (int(it.get("Size", 0)), ts)
        return res

    def put(self, local: Path, rel: str) -> bool:
        exe = rclone_path()
        if not exe:
            return False
        dest = f"{self.target}/{rel}".replace("//", "/")
        # rclone copyto takes a full destination path including filename
        try:
            out = subprocess.run([exe, "copyto", str(local), dest],
                                 capture_output=True, text=True, timeout=1800)
            return out.returncode == 0
        except Exception:
            return False

    def get(self, rel: str, local: Path) -> bool:
        exe = rclone_path()
        if not exe:
            return False
        local.parent.mkdir(parents=True, exist_ok=True)
        try:
            out = subprocess.run(
                [exe, "copyto", f"{self.target}/{rel}".replace("//", "/"),
                 str(local)], capture_output=True, text=True, timeout=1800)
            return out.returncode == 0
        except Exception:
            return False

    @staticmethod
    def _file_list(rels: Iterable[str]) -> str:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as lst:
            lst.write("\n".join(rels) + "\n")
            return lst.name

    def _run_rclone(self, cmd: list[str], p: SyncProgress, on_progress) -> int | None:
        """Run an rclone transfer, feeding its stats into ``p``.
        The exit code, or None when cancelled."""
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        last = ""
        for line in proc.stdout or []:
            if self.cancel.is_set():
                proc.terminate()
                proc.wait(timeout=30)
                return None
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                last = line[:200]
                continue
            stats = entry.get("stats") or {}
            if stats:
                p.done_bytes = int(stats.get("bytes", 0))
                p.done_files = int(stats.get("transfers", 0))
                p.errors = int(stats.get("errors", 0))
            elif entry.get("level") == "error":
                last = str(entry.get("msg", ""))[:200]
            if on_progress:
                on_progress(p)
        proc.wait(timeout=60)
        if proc.returncode:
            p.message = last or _("rclone stopped with error {code}").format(code=proc.returncode)
        return proc.returncode

    def _sub(self, rel: str) -> str:
        remote = self.remote.config.get("remote", "").strip().rstrip(":")
        return f"{remote}:{self._base()}/{rel}"

    def _version_days(self) -> list[str]:
        try:
            return self.list_folders(f"{self._base()}/{VERSIONS_DIR}")
        except RuntimeError as exc:
            if "not found" in str(exc).lower():
                return []
            raise

    def _drop_version(self, stamp: str) -> None:
        exe = rclone_path()
        if exe:
            subprocess.run([exe, "purge", self._sub(f"{VERSIONS_DIR}/{stamp}")],
                           capture_output=True, text=True, timeout=600)

    def push(self, root: Path, files, on_progress=None,
             keep_versions_days: int = 0) -> SyncProgress:
        """Send what is missing or modified, in one rclone run.

        The same rule as every destination - a file already backed up is
        never sent again unless it changed - decides the list; rclone's
        own engine then does the parallel uploads and retries.
        """
        exe = rclone_path()
        if not exe:
            return SyncProgress(phase="error", message=_("rclone not installed"))
        root = Path(root)
        p = SyncProgress(phase="listing")
        if on_progress:
            on_progress(p)
        self.prepare()
        try:
            remote_index = self.listing()
        except Exception as exc:
            p.phase = "error"
            p.message = _("Couldn't read the backup destination: {error}").format(error=exc)
            p.unreachable = True
            if on_progress:
                on_progress(p)
            return p
        manifest = self._load_manifest(root)
        todo, p.skipped = self._plan(root, list(files), remote_index, manifest)
        stamp = datetime.date.today().isoformat()
        p.total_files = len(todo)
        p.total_bytes = sum(t[2] for t in todo)
        p.phase = "uploading"
        if on_progress:
            on_progress(p)

        plain = [t for t in todo if t[0] == root / t[1]]
        renamed = [t for t in todo if t[0] != root / t[1]]
        if plain:
            lst = self._file_list(t[1] for t in plain)
            try:
                cmd = [exe, "copy", str(root), self.target,
                       "--files-from-raw", lst, "--no-traverse",
                       "--transfers", "4", "--stats", "1s", "--use-json-log",
                       "--stats-log-level", "NOTICE"]
                if keep_versions_days:
                    # replaced files move here instead of being overwritten
                    cmd += ["--backup-dir", self._sub(f"{VERSIONS_DIR}/{stamp}")]
                else:
                    cmd += ["--no-check-dest"]
                code = self._run_rclone(cmd, p, on_progress)
            except Exception as exc:
                code, p.message = 1, str(exc)
            finally:
                try:
                    os.unlink(lst)
                except OSError:
                    pass
            if code is None:
                p.phase = "cancelled"
            elif code == 0:
                p.errors = 0
                p.uploaded += len(plain)
                for _f, rel, size, mtime in plain:
                    manifest[rel] = [size, mtime]
            else:
                p.errors = max(p.errors, 1)
        for f, rel, size, mtime in renamed:
            if p.phase == "cancelled":
                break
            if self.put(f, rel):
                p.uploaded += 1
                manifest[rel] = [size, mtime]
            else:
                p.errors += 1
        self._save_manifest(root, manifest)
        if keep_versions_days and p.phase != "cancelled" and not p.errors:
            try:
                self._prune_versions(keep_versions_days)
            except Exception:
                pass

        if p.phase != "cancelled":
            p.done_files = len(todo)
            if p.errors:
                p.phase = "error"
                p.message = p.message or ngettext(
                    "{count} file couldn't be uploaded",
                    "{count} files couldn't be uploaded", p.errors).format(count=p.errors)
            else:
                p.phase = "done"
                p.done_bytes = p.total_bytes
        if on_progress:
            on_progress(p)
        return p

    def _fetch_many(self, root: Path, todo, p: SyncProgress, manifest: dict,
                    on_progress) -> None:
        exe = rclone_path()
        batch = [t for t in todo if not (root / t[0]).exists()]
        replace = [t for t in todo if (root / t[0]).exists()]
        if replace or not exe:
            super()._fetch_many(root, replace if exe else todo, p, manifest, on_progress)
        if not batch or not exe or p.phase == "cancelled":
            return
        base_files, base_bytes = p.done_files, p.done_bytes
        q = SyncProgress()

        def relay(q_):
            p.done_files = base_files + q_.done_files
            p.done_bytes = base_bytes + q_.done_bytes
            if on_progress:
                on_progress(p)

        lst = self._file_list(rel for rel, _size in batch)
        try:
            code = self._run_rclone(
                [exe, "copy", self.target, str(root), "--files-from-raw", lst,
                 "--no-traverse", "--ignore-existing", "--transfers", "4",
                 "--stats", "1s", "--use-json-log", "--stats-log-level", "NOTICE"],
                q, relay)
        except Exception as exc:
            code, q.message = 1, str(exc)
        finally:
            try:
                os.unlink(lst)
            except OSError:
                pass
        if code is None:
            p.phase = "cancelled"
        for rel, size in batch:
            local = root / rel
            try:
                st = local.stat()
            except OSError:
                p.errors += 1
                continue
            if size and st.st_size != size:        # stopped halfway through
                local.unlink(missing_ok=True)
                p.errors += 1
                continue
            p.restored += 1
            manifest[rel] = [st.st_size, st.st_mtime]
        p.done_files = base_files + len(batch)
        if code and not p.message:
            p.message = q.message


BACKENDS = {"local": LocalBackend, "webdav": WebDavBackend,
            "rclone": RcloneBackend}


def make_backend(remote: Remote) -> Backend:
    cls = BACKENDS.get(remote.kind, LocalBackend)
    return cls(remote)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot_catalog(root: Path) -> Path | None:
    """A consistent copy of catalog.db to back up.

    The live file is open while Piklin runs, with recent changes still in
    its write-ahead log; a byte copy of it can be a database that does not
    open. SQLite's backup API takes a clean snapshot instead. When nothing
    changed, the previous snapshot is kept as it is, so the next backup
    does not send it again.
    """
    import sqlite3
    src = Path(root) / "catalog.db"
    if not src.is_file():
        return None
    dest = Path(root) / ".cache" / "backup" / "catalog.db"
    tmp = dest.with_name("catalog.db.tmp")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp.unlink(missing_ok=True)
        source = sqlite3.connect(
            f"file:{urllib.request.pathname2url(str(src))}?mode=ro",
            uri=True, timeout=30)
        try:
            target = sqlite3.connect(str(tmp))
            try:
                source.backup(target)
            finally:
                target.close()
        finally:
            source.close()
        if dest.is_file() and _sha256(dest) == _sha256(tmp):
            tmp.unlink()
        else:
            tmp.replace(dest)
        return dest
    except (OSError, sqlite3.Error):
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return dest if dest.is_file() else None


def library_files(root: Path, include_originals: bool = True,
                  include_cache: bool = False, include_catalog: bool = True) -> list:
    """Everything worth backing up, in a sensible order.

    Sidecars and the album definitions go first: they are tiny, and they
    are the part that cannot be regenerated from anywhere else.  If a
    backup is interrupted halfway, the irreplaceable part is already
    across.
    """
    root = Path(root)
    ordered: list[Path] = []
    for sub in ("Edits", "Albums"):
        d = root / sub
        if d.is_dir():
            ordered += sorted(p for p in d.rglob("*") if p.is_file())
    # photo-state.json (favourites, hidden), removed-photos.json,
    # watched-folders.json and settings.json
    for f in sorted(root.glob("*.json")) + [root / "README.txt"]:
        if f.is_file():
            ordered.append(f)
    snapshot = snapshot_catalog(root) if include_catalog else None
    if snapshot:
        ordered.append((snapshot, "catalog.db"))
    if include_originals:
        d = root / "Originals"
        if d.is_dir():
            ordered += sorted(p for p in d.rglob("*") if p.is_file())
    if include_cache:
        d = root / ".cache"
        if d.is_dir():
            ordered += sorted(p for p in d.rglob("*") if p.is_file())
    return ordered


def describe_providers() -> list[tuple[str, str, str]]:
    """(kind, label, help) for the settings UI."""
    rc = rclone_path()
    configured = rclone_remotes()
    return [
        ("local", _("Folder or Drive"),
         _("A USB drive, a network folder from your NAS, or a folder that "
           "Dropbox or pCloud Drive keeps in sync. No password needed.")),
        ("webdav", _("NAS or Server (WebDAV)"),
         _("For QNAP, Synology, Nextcloud and similar. You need the server "
           "address, your username and your password.")),
        ("rclone", _("Cloud Service (rclone)") +
         (" " + _("({count} set up)").format(count=len(configured)) if configured else ""),
         (_("Google Drive, OneDrive, Dropbox, Backblaze and about seventy more, "
            "through the free rclone app. For advanced users.")
          if rc else
          _("Not installed yet. Install the free rclone app to use Google Drive, "
            "OneDrive, Dropbox and many more."))),
    ]
