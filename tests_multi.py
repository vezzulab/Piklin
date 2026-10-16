#!/usr/bin/env python3
"""Many computers sharing one backup, each running at the same time.

Every computer is a library of its own with a thread that does what Piklin
does in the background: read what the others left at the backup, decide from
that, make a video smaller, say so, back up. The backup is a WebDAV server
(a small one, here), the way a NAS is.

What has to hold, for any number of computers:
  - each video is made smaller once, by one computer, never twice;
  - in the end every computer and the backup have the smaller video, and no
    computer's backup has put a large original over a smaller video.

    python3 tests_multi.py [computers] [videos]
"""
import collections
import http.server
import json
import os
import random
import shutil
import sys
import tempfile
import threading
import time
import urllib.parse
import xml.sax.saxutils as X
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from piklin import i18n as _i18n
_i18n.setup("en")

COMPUTERS = int(sys.argv[1]) if len(sys.argv) > 1 else 6
VIDEOS = int(sys.argv[2]) if len(sys.argv) > 2 else 12
# "late": half the computers join only after the others have been working a
# while, still holding every large original. "damaged": the backup holds the
# large originals while the first computer has the smaller videos and believes
# it sent them (what a computer sending its large original over them leaves).
LATE = "late" in sys.argv[3:]
DAMAGED = "damaged" in sys.argv[3:]
# "stuck": the last computer cannot check a smaller video (as a Linux laptop
# whose OpenCV could not read AV1), so it keeps every large original, and its
# backups must still not put them over the smaller videos.
STUCK = "stuck" in sys.argv[3:]
TIMEOUT = 240
TMP = Path(tempfile.mkdtemp(prefix="piklin-multi-"))
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


# -- the backup: a WebDAV server -------------------------------------------------
NAS = TMP / "nas"
NAS.mkdir()


class Dav(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _p(self):
        return NAS / urllib.parse.unquote(self.path.split("?")[0]).lstrip("/")

    def _send(self, code, body=b"", extra=None):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_PUT(self):
        n = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(n)
        p = self._p()
        if not p.parent.is_dir():
            return self._send(409)
        tmp = p.with_name(f".{p.name}.{threading.get_ident()}.put")
        tmp.write_bytes(data)
        os.replace(tmp, p)
        self._send(201)

    def do_GET(self):
        p = self._p()
        if p.is_file():
            try:
                self._send(200, p.read_bytes())
            except OSError:
                self._send(404)
        else:
            self._send(404)

    def do_MKCOL(self):
        p = self._p()
        if p.exists():
            return self._send(405)
        try:
            p.mkdir()
        except FileExistsError:
            return self._send(405)
        self._send(201)

    def do_DELETE(self):
        p = self._p()
        if not p.exists():
            return self._send(404)
        shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
        self._send(204)

    def do_MOVE(self):
        dest = urllib.parse.unquote(urllib.parse.urlparse(self.headers["Destination"]).path).lstrip("/")
        d = NAS / dest
        d.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(self._p()), str(d))
        except OSError:
            return self._send(404)
        self._send(201)

    def do_PROPFIND(self):
        n = int(self.headers.get("Content-Length", 0))
        if n:
            self.rfile.read(n)
        p = self._p()
        if not p.exists():
            return self._send(404)
        depth = self.headers.get("Depth", "1")

        def entry(q):
            try:
                st = q.stat()
            except OSError:
                return ""
            href = "/" + str(q.relative_to(NAS)) + ("/" if q.is_dir() else "")
            if q.is_dir():
                props = "<d:resourcetype><d:collection/></d:resourcetype>"
            else:
                props = (f"<d:resourcetype/><d:getcontentlength>{st.st_size}</d:getcontentlength>"
                         "<d:getlastmodified>Wed, 01 Jan 2025 00:00:00 GMT</d:getlastmodified>")
            return (f"<d:response><d:href>{X.escape(urllib.parse.quote(href))}</d:href><d:propstat>"
                    f"<d:prop>{props}</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>")
        if depth == "infinity":
            items = [p] + [q for q in p.rglob("*") if not q.name.endswith(".put")]
        elif depth == "1" and p.is_dir():
            items = [p] + [q for q in p.iterdir() if not q.name.endswith(".put")]
        else:
            items = [p]
        body = ('<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">'
                + "".join(entry(q) for q in items) + "</d:multistatus>").encode()
        self._send(207, body, {"Content-Type": "application/xml"})


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True


server = Server(("127.0.0.1", 0), Dav)
threading.Thread(target=server.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{server.server_address[1]}"

# -- the code under test -----------------------------------------------------------
import av
import numpy as np
from piklin import autobackup, backuplock, devices, remote as rem, sync, videospace
from piklin.catalog import Catalog
from piklin.indexer import Indexer
from piklin.paths import Library
from piklin.photo_rename import repoint_photo

backuplock.SETTLE = 0.5
backuplock.BEAT = 5.0

# Each thread is its own computer.
_who = threading.local()
backuplock.me = lambda: (_who.ident, _who.name)

ENCODES = collections.Counter()
SHRUNK_TO = 3                              # the smaller video is a third of the size
_enc_lock = threading.Lock()


def fake_shrink(library, catalog, photo_id, **kw):
    """What shrink_video does to a library, without an encoder: the file under
    the same name is swapped for a smaller one, and the catalog follows."""
    row = catalog.photo(photo_id)
    src = Path(row["path"])
    if not src.is_file():
        return "gone"
    with _enc_lock:
        ENCODES[(src.relative_to(library.root).as_posix(), _who.name)] += 1
    time.sleep(0.25)                       # an encode takes a while
    data = src.read_bytes()
    out = src.with_name(f".{src.name}.small")
    out.write_bytes(data[:len(data) // SHRUNK_TO])
    st = src.stat()
    os.utime(out, (st.st_atime, st.st_mtime))
    os.replace(out, src)
    repoint_photo(library, catalog, photo_id, src, src, bytes=src.stat().st_size)
    return "smaller"


devices.shrink_video = fake_shrink
videospace.verify = lambda *a, **k: not getattr(_who, "cannot_verify", False)   # the check itself is tested elsewhere


# -- the computers --------------------------------------------------------------------
def make_clip(path: Path, seed: int):
    with av.open(str(path), "w") as oc:
        st = oc.add_stream("mpeg4", rate=25)
        st.width, st.height, st.pix_fmt = 64, 48, "yuv420p"
        rng = np.random.default_rng(seed)
        for i in range(50):
            frame = av.VideoFrame.from_ndarray(
                rng.integers(0, 255, (48, 64, 3), dtype=np.uint8), format="rgb24")
            for pk in st.encode(frame):
                oc.mux(pk)
        for pk in st.encode(None):
            oc.mux(pk)


SOURCE = TMP / "source"
SOURCE.mkdir()
CLIPS = []
for v in range(VIDEOS):
    p = SOURCE / f"clip{v:02d}.mp4"
    make_clip(p, v)
    CLIPS.append(p)
BIG = {p.name: p.stat().st_size for p in CLIPS}

REMOTE = rem.Remote(id="nas", name="NAS", kind="webdav", config={"url": URL, "base": "/Piklin"})


class Computer:
    def __init__(self, n: int):
        self.n = n
        self.name = f"computer{n:02d}"
        self.ident = f"id-{n:04d}-{random.random():.6f}"
        self.library = Library(TMP / f"lib{n:02d}.piklin").ensure()
        self.catalog = Catalog(self.library.db)
        folder = self.library.originals / "2024" / "2024-01-01"
        folder.mkdir(parents=True)
        for p in CLIPS:
            shutil.copy2(p, folder / p.name)
        Indexer(self.catalog, None, library_root=self.library.root).add_files(
            [str(folder / p.name) for p in CLIPS])
        self.queue: list[dict] = []
        self.error = ""
        self.cycles = 0

    def files(self):
        return sorted((self.library.originals / "2024" / "2024-01-01").glob("*.mp4"))

    def all_small(self) -> bool:
        return all(p.stat().st_size < BIG[p.name] for p in self.files())

    def _pull(self, backend, progress=None):
        return sync.pull(self.library, self.catalog, backend, progress)

    def cycle(self):
        """One round of what Piklin does by itself."""
        self.cycles += 1
        backend = REMOTE.backend()
        # 1. read everything the others left
        self._pull(backend)
        # 2. decide from that, and make one video smaller
        if not self.queue:
            self.queue = [{"id": r["id"], "bit_rate": 0, "profile": "compressed"}
                          for r in self.catalog.q("SELECT id FROM photos WHERE duration > 0")]
        keep = []
        for item in self.queue:
            row = self.catalog.photo(item["id"])
            if row is not None and not videospace.settled(self.library.root, row["path"], int(row["bytes"] or 0)):
                keep.append(item)
        self.queue = keep
        if self.queue:
            item = random.choice(self.queue)
            result, _freed = videospace.make_smaller(self.library, self.catalog, [REMOTE], item)
            if result in ("gone", "smaller", "kept"):
                self.queue = [q for q in self.queue if q["id"] != item["id"]]
        # 3. back up
        autobackup.run_backup(self.library.root, [REMOTE], 7,
                              before_push=lambda r, b: self._pull(b))

    def run(self, stop: threading.Event, delay: float = 0.0):
        _who.ident, _who.name = self.ident, self.name
        _who.cannot_verify = STUCK and self.n == COMPUTERS - 1
        try:
            if delay:
                time.sleep(delay)
            while not stop.is_set():
                self.cycle()
                time.sleep(random.uniform(0.05, 0.3))
        except Exception as exc:                     # a real failure: say what and where
            import traceback
            self.error = traceback.format_exc()
            print(f"  {self.name} stopped: {exc}\n{self.error}")


def nas_state():
    base = NAS / "Piklin" / "Originals" / "2024" / "2024-01-01"
    return {p.name: (p.stat().st_size if p.exists() else None)
            for p in (base / c.name for c in CLIPS)}


def converged(computers) -> bool:
    if not all(c.all_small() for c in computers if not (STUCK and c.n == COMPUTERS - 1)):
        return False
    return all(s is not None and s < BIG[n] for n, s in nas_state().items())


# -- run ---------------------------------------------------------------------------
print(f"\n{COMPUTERS} computers, {VIDEOS} videos, one backup\n")
computers = [Computer(n) for n in range(COMPUTERS)]
# the first computer's library is what is in the backup to begin with
_who.ident, _who.name = computers[0].ident, computers[0].name
autobackup.run_backup(computers[0].library.root, [REMOTE], 7)
check("the backup begins with every video at full size",
      all(s == BIG[n] for n, s in nas_state().items()), nas_state())

if DAMAGED:
    # computer 0 made every video smaller on its own and recorded it, and its
    # record of what it sent says the smaller ones are at the backup
    c0 = computers[0]
    _who.ident, _who.name = c0.ident, c0.name
    for row in c0.catalog.q("SELECT id FROM photos WHERE duration > 0"):
        videospace.make_smaller.__globals__["convert_one"]  # the real path, below
        cand = videospace.Candidate(row["id"], c0.catalog.photo(row["id"])["path"],
                                    int(c0.catalog.photo(row["id"])["bytes"]), 1.0, 64, 48, 0, 0, "compressed")
        videospace.convert_one(c0.library, c0.catalog, cand, [REMOTE])
    man = {}
    for p in c0.files():
        st = p.stat()
        man[p.relative_to(c0.library.root).as_posix()] = [st.st_size, st.st_mtime]
    REMOTE.backend()._save_manifest(c0.library.root, man)
    ENCODES.clear()                        # that work was done before, and counts once
    for p in CLIPS:
        ENCODES[(f"Originals/2024/2024-01-01/{p.name}", "before")] += 1

stop = threading.Event()
threads = [threading.Thread(target=c.run, args=(stop, (6.0 if LATE and i >= COMPUTERS // 2 else 0.0)), daemon=True)
           for i, c in enumerate(computers)]
started = time.time()
for t in threads:
    t.start()
settled_at = None
while time.time() - started < TIMEOUT:
    time.sleep(1.0)
    if any(c.error for c in computers):
        break
    if converged(computers):
        # a moment more, to see that nothing is done again
        settled_at = settled_at or time.time()
        if time.time() - settled_at > 6:
            break
stop.set()
for t in threads:
    t.join(timeout=30)

per_video = collections.Counter()
for (rel, _who_name), n in ENCODES.items():
    per_video[rel] += n
print(f"\n  {sum(per_video.values())} encodes for {VIDEOS} videos, "
      f"{sum(c.cycles for c in computers)} cycles, {time.time() - started:.0f} s\n")
check("no computer stopped with an error", not any(c.error for c in computers))
check("every video was made smaller", len(per_video) == VIDEOS, len(per_video))
if DAMAGED:
    # A computer that made videos smaller while it was away knows what no other
    # computer does until it says so: one that begins in that very moment can do
    # one of them again. Nothing can prevent that; what matters is that it
    # comes out right in the end (below) and happens at most once or twice.
    extra = sum(n - 1 for n in per_video.values())
    check("a video made smaller while away is at worst done once more, by one computer",
          all(n <= 2 for n in per_video.values()) and extra <= 2, {k: v for k, v in per_video.items() if v != 1})
else:
    check("no video was made smaller twice, by any computer",
          all(n == 1 for n in per_video.values()), {k: v for k, v in per_video.items() if v != 1})
check("in the end every computer has every smaller video",
      all(c.all_small() for c in computers if not (STUCK and c.n == COMPUTERS - 1)),
      [(c.name, sum(1 for p in c.files() if p.stat().st_size >= BIG[p.name])) for c in computers if not c.all_small()])
check("and the backup has them too, not the large originals",
      all(s is not None and s < BIG[n] for n, s in nas_state().items()),
      {n: s for n, s in nas_state().items() if s is None or s >= BIG[n]})
check("every computer's catalog follows its files",
      all(int(c.catalog.photo_by_path(str(p.resolve()))["bytes"]) == p.stat().st_size
          for c in computers for p in c.files() if c.catalog.photo_by_path(str(p.resolve())) is not None))

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
