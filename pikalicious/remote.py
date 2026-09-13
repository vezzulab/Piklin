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


def store_secret(remote_id: str, secret: str) -> bool:
    """Put a password in the system keyring. False if unavailable."""
    Secret = _keyring()
    if Secret is None:
        return False
    try:
        schema = Secret.Schema.new(
            "com.envy.Pikalicious", Secret.SchemaFlags.NONE,
            {"application": Secret.SchemaAttributeType.STRING,
             "remote": Secret.SchemaAttributeType.STRING})
        return bool(Secret.password_store_sync(
            schema, {"application": "pikalicious", "remote": remote_id},
            Secret.COLLECTION_DEFAULT,
            f"Piklin remote: {remote_id}", secret, None))
    except Exception:
        return False


def load_secret(remote_id: str) -> str | None:
    Secret = _keyring()
    if Secret is None:
        return None
    try:
        schema = Secret.Schema.new(
            "com.envy.Pikalicious", Secret.SchemaFlags.NONE,
            {"application": Secret.SchemaAttributeType.STRING,
             "remote": Secret.SchemaAttributeType.STRING})
        return Secret.password_lookup_sync(
            schema, {"application": "pikalicious", "remote": remote_id}, None)
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
    def push(self, root: Path, files: Iterable[Path],
             on_progress: Callable[[SyncProgress], None] | None = None,
             ) -> SyncProgress:
        """Upload everything that is missing or changed.

        Comparison is by size then mtime, which is what every practical
        sync tool uses: hashing a 200 GB library on every run to find the
        three files that changed would cost far more than it saves.
        """
        p = SyncProgress(phase="listing")
        if on_progress:
            on_progress(p)
        try:
            remote_index = self.listing()
        except Exception as exc:
            p.phase = "error"
            p.message = f"could not list remote: {exc}"
            if on_progress:
                on_progress(p)
            return p

        todo: list[tuple[Path, str, int]] = []
        for f in files:
            try:
                st = f.stat()
            except OSError:
                continue
            rel = f.relative_to(root).as_posix()
            have = remote_index.get(rel)
            if have and have[0] == st.st_size and abs(have[1] - st.st_mtime) < 2:
                p.skipped += 1
                continue
            todo.append((f, rel, st.st_size))

        p.phase = "uploading"
        p.total_files = len(todo)
        p.total_bytes = sum(t[2] for t in todo)
        if on_progress:
            on_progress(p)

        for f, rel, size in todo:
            if self.cancel.is_set():
                p.phase = "cancelled"
                break
            p.current = rel
            try:
                if self.put(f, rel):
                    p.uploaded += 1
                else:
                    p.errors += 1
            except Exception:
                p.errors += 1
            p.done_files += 1
            p.done_bytes += size
            if on_progress:
                on_progress(p)

        if p.phase != "cancelled":
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

    TIMEOUT = 30

    @property
    def url(self) -> str:
        return self.remote.config.get("url", "").rstrip("/")

    @property
    def base_path(self) -> str:
        return self.remote.config.get("base", "").strip("/")

    def _auth_header(self) -> dict:
        user = self.remote.config.get("username", "")
        pw = load_secret(self.remote.id) or self.remote.config.get("password", "")
        if not user:
            return {}
        token = b64encode(f"{user}:{pw}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def _request(self, method: str, rel: str = "", data: bytes | None = None,
                 extra: dict | None = None) -> urllib.request.addinfourl:
        parts = [p for p in (self.base_path, rel) if p]
        path = "/".join(parts)
        url = f"{self.url}/{urllib.parse.quote(path)}" if path else self.url
        req = urllib.request.Request(url, data=data, method=method)
        for k, v in {**self._auth_header(), **(extra or {})}.items():
            req.add_header(k, v)
        return urllib.request.urlopen(req, timeout=self.TIMEOUT)

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
            self._request("PROPFIND", extra={"Depth": "0"})
            return TestResult(True, "Connected", self.url)
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

    def listing(self) -> dict[str, tuple[int, float]]:
        """PROPFIND the tree once and parse sizes and modification times."""
        out: dict[str, tuple[int, float]] = {}
        try:
            resp = self._request("PROPFIND", extra={"Depth": "infinity"})
            body = resp.read().decode("utf-8", "replace")
        except Exception:
            return out
        import re
        from email.utils import parsedate_to_datetime
        prefix = f"/{self.base_path}" if self.base_path else ""
        for block in re.findall(r"<[^>]*response[^>]*>(.*?)</[^>]*response>",
                                body, re.S | re.I):
            href = re.search(r"<[^>]*href[^>]*>(.*?)</[^>]*href>", block, re.S | re.I)
            size = re.search(r"getcontentlength[^>]*>(\d+)<", block, re.I)
            mod = re.search(r"getlastmodified[^>]*>(.*?)<", block, re.I)
            if not href or not size:
                continue
            path = urllib.parse.unquote(href.group(1))
            idx = path.find(prefix) if prefix else -1
            rel = path[idx + len(prefix):] if idx >= 0 else path
            rel = rel.strip("/")
            if not rel:
                continue
            ts = 0.0
            if mod:
                try:
                    ts = parsedate_to_datetime(mod.group(1).strip()).timestamp()
                except Exception:
                    ts = 0.0
            out[rel] = (int(size.group(1)), ts)
        return out

    def _mkcol(self, rel_dir: str) -> None:
        parts = rel_dir.split("/")
        for i in range(1, len(parts) + 1):
            sub = "/".join(parts[:i])
            if not sub:
                continue
            try:
                self._request("MKCOL", sub)
            except urllib.error.HTTPError as exc:
                if exc.code not in (405, 301):     # already exists
                    pass
            except Exception:
                pass

    def put(self, local: Path, rel: str) -> bool:
        parent = "/".join(rel.split("/")[:-1])
        if parent:
            self._mkcol(parent)
        data = local.read_bytes()
        try:
            self._request("PUT", rel, data=data,
                          extra={"Content-Type": "application/octet-stream"})
            return True
        except urllib.error.HTTPError as exc:
            return exc.code in (200, 201, 204)
        except Exception:
            return False

    def get(self, rel: str, local: Path) -> bool:
        try:
            resp = self._request("GET", rel)
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(resp.read())
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
            out = subprocess.run([exe, "lsd", self.target, "--max-depth", "1"],
                                 capture_output=True, text=True, timeout=45)
            if out.returncode == 0:
                return TestResult(True, "Connected", self.target)
            return TestResult(False, "rclone could not reach the remote",
                              (out.stderr or out.stdout).strip()[:400])
        except subprocess.TimeoutExpired:
            return TestResult(False, "Timed out contacting the remote")
        except Exception as exc:
            return TestResult(False, "rclone failed", str(exc))

    def listing(self) -> dict[str, tuple[int, float]]:
        exe = rclone_path()
        if not exe:
            return {}
        try:
            out = subprocess.run(
                [exe, "lsjson", "-R", "--files-only", self.target],
                capture_output=True, text=True, timeout=300)
            if out.returncode != 0:
                return {}
            items = json.loads(out.stdout or "[]")
        except Exception:
            return {}
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
        """Hand the whole directory to ``rclone sync``.

        rclone's own transfer engine does parallel uploads, resumes and
        server-side checksums far better than a per-file loop over its
        CLI would, so for a full library push we get out of its way.
        """
        exe = rclone_path()
        if not exe:
            return SyncProgress(phase="error", message="rclone not installed")
        p = SyncProgress(phase="uploading")
        if on_progress:
            on_progress(p)
        cmd = [exe, "copy", str(root), self.target, "--transfers", "4",
               "--checkers", "8", "--stats", "1s", "--stats-one-line",
               "--exclude", ".cache/**", "--use-json-log", "--stats-log-level",
               "NOTICE"]
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
                    rec = json.loads(line)
                    stats = rec.get("stats") or {}
                    if stats:
                        p.total_bytes = int(stats.get("totalBytes", 0))
                        p.done_bytes = int(stats.get("bytes", 0))
                        p.total_files = int(stats.get("totalTransfers", 0))
                        p.done_files = int(stats.get("transfers", 0))
                        p.errors = int(stats.get("errors", 0))
                except ValueError:
                    p.current = line[:120]
                if on_progress:
                    on_progress(p)
            proc.wait(timeout=30)
            if p.phase != "cancelled":
                p.phase = "done" if proc.returncode == 0 else "error"
                if proc.returncode:
                    p.message = f"rclone exited {proc.returncode}"
        except Exception as exc:
            p.phase = "error"
            p.message = str(exc)
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
