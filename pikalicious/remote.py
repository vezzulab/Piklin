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

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from base64 import b64encode
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

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
    message: str = ""

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


class Backend:
    """What every destination must be able to do."""

    def __init__(self, remote: Remote):
        self.remote = remote
        self.cancel = threading.Event()

    def test(self) -> TestResult:
        raise NotImplementedError

    def put(self, local: Path, rel: str) -> bool:
        raise NotImplementedError

    def get(self, rel: str, local: Path) -> bool:
        raise NotImplementedError

    def listing(self) -> dict[str, tuple[int, float]]:
        """Map of relative path -> (size, mtime) already on the remote."""
        raise NotImplementedError

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
        for f in files:
            try:
                st = f.stat()
            except OSError:
                continue
            rel = f.relative_to(root).as_posix()
            have = remote_index.get(rel)
            if have and have[0] == st.st_size:
                sent = manifest.get(rel)
                same_time = abs(have[1] - st.st_mtime) < 2
                sent_unchanged = (bool(sent) and sent[0] == st.st_size
                                  and abs(sent[1] - st.st_mtime) < 1)
                if same_time or sent_unchanged:
                    skipped += 1
                    continue
            todo.append((f, rel, st.st_size, st.st_mtime))
        return todo, skipped

    def push(self, root: Path, files: Iterable[Path],
             on_progress: Callable[[SyncProgress], None] | None = None,
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
        try:
            remote_index = self.listing()
        except Exception as exc:
            p.phase = "error"
            p.message = f"could not list the destination: {exc}"
            if on_progress:
                on_progress(p)
            return p

        manifest = self._load_manifest(root)
        todo, p.skipped = self._plan(root, files, remote_index, manifest)
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
            try:
                ok = self.put(f, rel)
            except Exception:
                ok = False
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

        if p.phase != "cancelled":
            if p.errors and not p.uploaded:
                p.phase = "error"
                p.message = f"none of the {p.errors} files could be uploaded"
            else:
                p.phase = "done"
        if on_progress:
            on_progress(p)
        return p


# ==========================================================================
# local / mounted
# ==========================================================================
class LocalBackend(Backend):
    """A mounted directory: NAS, external disk, or a cloud drive mount."""

    @property
    def base(self) -> Path:
        return Path(self.remote.config.get("path", "")).expanduser()

    def test(self) -> TestResult:
        b = self.base
        if not str(b):
            return TestResult(False, "No folder chosen")
        if not b.exists():
            return TestResult(False, "Folder does not exist", str(b))
        if not b.is_dir():
            return TestResult(False, "Not a folder", str(b))
        if not os.access(b, os.W_OK):
            return TestResult(False, "Folder is not writable", str(b))
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
            return TestResult(False, "Cannot write to folder", str(exc))
        return TestResult(True, "Connected", str(b), free)

    def listing(self) -> dict[str, tuple[int, float]]:
        out: dict[str, tuple[int, float]] = {}
        base = self.base
        if not base.is_dir():
            return out
        for dirpath, _, names in os.walk(base):
            for n in names:
                fp = Path(dirpath) / n
                try:
                    st = fp.stat()
                except OSError:
                    continue
                out[fp.relative_to(base).as_posix()] = (st.st_size, st.st_mtime)
        return out

    def put(self, local: Path, rel: str) -> bool:
        dest = self.base / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        # copy2 preserves mtime, which is what the next sync compares on
        shutil.copy2(local, tmp)
        tmp.replace(dest)
        return True

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
class WebDavBackend(Backend):
    """Native WebDAV: QNAP, Synology, Nextcloud, ownCloud, pCloud, Box."""

    TIMEOUT = 60

    def __init__(self, remote: Remote):
        super().__init__(remote)
        self._folders: set[str] = set()

    @property
    def url(self) -> str:
        return self.remote.config.get("url", "").rstrip("/")

    @property
    def base_path(self) -> str:
        return self.remote.config.get("base", "").strip("/")

    def _auth_header(self) -> dict:
        user = self.remote.config.get("username", "")
        if not user:
            return {}
        pw = self.remote.config.get("password") or load_secret(self.remote.id) or ""
        token = b64encode(f"{user}:{pw}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def _full(self, rel: str = "") -> str:
        return "/".join(p for p in (self.base_path, rel.strip("/")) if p)

    def _request_path(self, method: str, path: str, data=None,
                      extra: dict | None = None):
        url = f"{self.url}/{urllib.parse.quote(path)}" if path else self.url
        req = urllib.request.Request(url, data=data, method=method)
        for k, v in {**self._auth_header(), **(extra or {})}.items():
            req.add_header(k, v)
        return urllib.request.urlopen(req, timeout=self.TIMEOUT)

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
                if exc.code in (401, 403):
                    raise
                # 405 Method Not Allowed / 301: the folder already exists
            self._folders.add(sub)

    def test(self) -> TestResult:
        if not self.url:
            return TestResult(False, "No server URL")
        if not self.url.startswith("https://"):
            # http would send the password in the clear
            if not self.url.startswith("http://"):
                return TestResult(False, "URL must start with https://")
            return TestResult(False,
                              "Refusing to send credentials over plain HTTP",
                              "Use an https:// URL")
        try:
            try:
                self._request("PROPFIND", extra={"Depth": "0"})
                return TestResult(True, "Connected", self.url)
            except urllib.error.HTTPError as exc:
                if exc.code == 404 and self.base_path:
                    # The backup folder does not exist yet: create it.
                    self._ensure_folder(self.base_path)
                    self._request("PROPFIND", extra={"Depth": "0"})
                    return TestResult(True, "Connected",
                                      f"created the folder {self.base_path}")
                raise
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return TestResult(False, "Sign-in rejected", f"HTTP {exc.code}")
            if exc.code == 405:
                return TestResult(True, "Connected", "server allows uploads")
            return TestResult(False, f"Server returned HTTP {exc.code}",
                              str(exc.reason))
        except urllib.error.URLError as exc:
            return TestResult(False, "Could not reach server", str(exc.reason))
        except Exception as exc:
            return TestResult(False, "Connection failed", str(exc))

    def _entries(self, rel: str, depth: str) -> list[tuple[str, bool, int, float]]:
        """(path relative to the backup folder, is folder, size, mtime)."""
        import re
        from email.utils import parsedate_to_datetime
        body = self._request("PROPFIND", rel, extra={"Depth": depth}).read()
        body = body.decode("utf-8", "replace")
        prefix = urllib.parse.urlparse(self.url).path.rstrip("/")
        if self.base_path:
            prefix += "/" + self.base_path
        out = []
        for block in re.findall(r"<[^>]*response[^>]*>(.*?)</[^>]*response>",
                                body, re.S | re.I):
            href = re.search(r"<[^>]*href[^>]*>(.*?)</[^>]*href>", block, re.S | re.I)
            if not href:
                continue
            path = urllib.parse.unquote(urllib.parse.urlparse(href.group(1).strip()).path)
            if prefix and path.startswith(prefix):
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

    def listing(self) -> dict[str, tuple[int, float]]:
        """Sizes and modification times of what is already on the server."""
        try:
            entries = self._entries("", "infinity")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {}                       # nothing backed up yet
            if exc.code == 401:
                raise
            entries = None                      # Depth: infinity refused
        # Servers that refuse or quietly ignore Depth: infinity (Nextcloud
        # by default) return only the top level: walk the folders instead.
        if entries is None or (any(d for r, d, _s, _t in entries if r)
                               and not any(not d for r, d, _s, _t in entries if r)):
            entries, pending, seen = [], [""], {""}
            while pending:
                folder = pending.pop()
                for r, is_folder, size, ts in self._entries(folder, "1"):
                    if r in seen:
                        continue
                    seen.add(r)
                    if is_folder:
                        pending.append(r)
                    else:
                        entries.append((r, False, size, ts))
        return {r: (size, ts) for r, is_folder, size, ts in entries
                if r and not is_folder}

    def put(self, local: Path, rel: str) -> bool:
        parent = "/".join(rel.split("/")[:-1])
        self._ensure_folder(self._full(parent))
        st = local.stat()
        # Streamed from the file: a 4 GB video is not read into memory.
        with open(local, "rb") as fh:
            try:
                self._request("PUT", rel, data=fh, extra={
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(st.st_size),
                    # Nextcloud and ownCloud keep the file's own time
                    "X-OC-Mtime": str(int(st.st_mtime)),
                })
                return True
            except urllib.error.HTTPError as exc:
                return exc.code in (200, 201, 204)
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
        remote = self.remote.config.get("remote", "").rstrip(":")
        path = self.remote.config.get("path", "").strip("/")
        return f"{remote}:{path}" if path else f"{remote}:"

    def test(self) -> TestResult:
        exe = rclone_path()
        if not exe:
            return TestResult(
                False, "rclone is not installed",
                "Install it to reach pCloud, S3, Backblaze, Google Drive, "
                "OneDrive, Dropbox and others:  sudo apt install rclone  "
                "(then run: rclone config)")
        remote = self.remote.config.get("remote", "").rstrip(":")
        if not remote:
            return TestResult(False, "No rclone remote chosen",
                              f"Configured: {', '.join(rclone_remotes()) or 'none'}")
        try:
            # The backup folder may not exist yet; mkdir is harmless if it does.
            made = subprocess.run([exe, "mkdir", self.target],
                                  capture_output=True, text=True, timeout=60)
            out = subprocess.run([exe, "lsd", self.target, "--max-depth", "1"],
                                 capture_output=True, text=True, timeout=45)
            if out.returncode == 0:
                return TestResult(True, "Connected", self.target)
            return TestResult(False, "rclone could not reach the remote",
                              (out.stderr or made.stderr or out.stdout).strip()[:400])
        except subprocess.TimeoutExpired:
            return TestResult(False, "Timed out contacting the remote")
        except Exception as exc:
            return TestResult(False, "rclone failed", str(exc))

    def listing(self) -> dict[str, tuple[int, float]]:
        exe = rclone_path()
        if not exe:
            return {}
        out = subprocess.run(
            [exe, "lsjson", "-R", "--files-only", self.target],
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
            out = subprocess.run([exe, "copyto", str(local), dest,
                                  "--ignore-times" if False else "--update"],
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

    def push(self, root: Path, files, on_progress=None) -> SyncProgress:
        """Send what is missing or modified, in one rclone run.

        The same rule as every destination - a file already backed up is
        never sent again unless it changed - decides the list; rclone's
        own engine then does the parallel uploads and retries.
        """
        import tempfile
        exe = rclone_path()
        if not exe:
            return SyncProgress(phase="error", message="rclone not installed")
        root = Path(root)
        p = SyncProgress(phase="listing")
        if on_progress:
            on_progress(p)
        manifest = self._load_manifest(root)
        try:
            remote_index = self.listing()
        except Exception as exc:
            p.phase = "error"
            p.message = f"could not list the destination: {exc}"
            if on_progress:
                on_progress(p)
            return p
        todo, p.skipped = self._plan(root, list(files), remote_index, manifest)
        p.total_files = len(todo)
        p.total_bytes = sum(t[2] for t in todo)
        if not todo:
            p.phase = "done"
            if on_progress:
                on_progress(p)
            return p

        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as lst:
            lst.write("\n".join(rel for _f, rel, _s, _m in todo) + "\n")
            list_path = lst.name
        p.phase = "uploading"
        if on_progress:
            on_progress(p)
        cmd = [exe, "copy", str(root), self.target,
               "--files-from-raw", list_path, "--no-check-dest", "--no-traverse",
               "--transfers", "4", "--stats", "1s", "--use-json-log",
               "--stats-log-level", "NOTICE"]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True)
            for line in proc.stdout or []:
                if self.cancel.is_set():
                    proc.terminate()
                    p.phase = "cancelled"
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    stats = (json.loads(line).get("stats") or {})
                    if stats:
                        p.done_bytes = int(stats.get("bytes", 0))
                        p.done_files = int(stats.get("transfers", 0))
                        p.errors = int(stats.get("errors", 0))
                except ValueError:
                    p.current = line[:120]
                if on_progress:
                    on_progress(p)
            proc.wait(timeout=60)
            if p.phase != "cancelled":
                if proc.returncode == 0:
                    p.phase = "done"
                    p.uploaded = p.done_files = len(todo)
                    p.done_bytes = p.total_bytes
                    p.errors = 0
                    for _f, rel, size, mtime in todo:
                        manifest[rel] = [size, mtime]
                    self._save_manifest(root, manifest)
                else:
                    p.phase = "error"
                    p.errors = max(p.errors, 1)
                    p.message = f"rclone exited {proc.returncode}"
        except Exception as exc:
            p.phase = "error"
            p.message = str(exc)
        finally:
            try:
                os.unlink(list_path)
            except OSError:
                pass
        if on_progress:
            on_progress(p)
        return p


BACKENDS = {"local": LocalBackend, "webdav": WebDavBackend,
            "rclone": RcloneBackend}


def make_backend(remote: Remote) -> Backend:
    cls = BACKENDS.get(remote.kind, LocalBackend)
    return cls(remote)


def library_files(root: Path, include_originals: bool = True,
                  include_cache: bool = False) -> list[Path]:
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
    for name in ("settings.json", "README.txt", "catalog.db"):
        f = root / name
        if f.is_file():
            ordered.append(f)
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
        ("local", "Folder, drive or NAS mount",
         "Any folder your system can see: a USB disk, a QNAP or Synology "
         "share mounted over SMB or NFS, an sshfs mount, or the folder "
         "that pCloud Drive or Dropbox creates. No password needed."),
        ("webdav", "WebDAV server",
         "Works with QNAP, Synology, Nextcloud, ownCloud, pCloud and Box. "
         "Needs the server URL and your sign-in. HTTPS only."),
        ("rclone", "Cloud storage via rclone" +
         (f" ({len(configured)} configured)" if configured else ""),
         ("Brings S3, Backblaze B2, pCloud, Google Drive, OneDrive, "
          "Dropbox, Azure, SFTP and about seventy others."
          if rc else
          "Not installed. Install rclone to reach S3, Backblaze, pCloud, "
          "Google Drive, OneDrive, Dropbox and many more.")),
    ]
