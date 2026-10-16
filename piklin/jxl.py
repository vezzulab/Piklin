"""JPEG XL, for photos that are kept smaller without losing a single bit.

A JPEG becomes a .jxl about a fifth smaller, and the .jxl turns back into the
very same JPEG, byte for byte. That is what makes it safe to keep only the
smaller file: nothing is thrown away, it is packed differently.

The work is done by libjxl's own cjxl and djxl (BSD-3-Clause), found next to
Piklin or on the PATH. Without them Piklin simply does not offer JPEG XL, and
a library that has .jxl files in it shows them as it shows any file it cannot
open.
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .logs import log

EXT = ".jxl"
JPEG_EXT = {".jpg", ".jpeg", ".jpe", ".jfif"}
_TIMEOUT = 300
_cache: dict[str, str | None] = {}


def _tool(name: str) -> str | None:
    """cjxl or djxl: the one Piklin carries - in Piklin.app's runtime, or beside
    the .deb's and the AppImage's libraries - else one installed on the system."""
    if name in _cache:
        return _cache[name]
    exe = name + (".exe" if sys.platform == "win32" else "")
    here = Path(__file__).resolve()
    candidates = []
    runtime = os.environ.get("PIKLIN_RUNTIME")
    if runtime:
        candidates.append(Path(runtime) / "bin" / exe)
    # usr/share/piklin/piklin/jxl.py -> usr/lib/piklin/cjxl
    if len(here.parents) > 3:
        candidates.append(here.parents[3] / "lib" / "piklin" / exe)
    candidates.append(here.parents[1] / "bin" / exe)
    found = next((str(c) for c in candidates
                  if c.is_file() and os.access(c, os.X_OK)), None)
    _cache[name] = found or shutil.which(name)
    return _cache[name]


def available() -> bool:
    return bool(_tool("cjxl") and _tool("djxl"))


def _run(args: list[str], want: str | None = None) -> bool:
    """Run a tool; with ``want``, also require that text in what it says."""
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = 0x08000000
    try:
        r = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=_TIMEOUT, **kw)
        if r.returncode != 0:
            return False
        return want is None or want in r.stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("jpeg xl tool failed: %s", exc)
        return False


def _same_bytes(a: Path, b: Path) -> bool:
    if a.stat().st_size != b.stat().st_size:
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            x, y = fa.read(1 << 20), fb.read(1 << 20)
            if x != y:
                return False
            if not x:
                return True


def restore_jpeg(src: Path | str, dst: Path | str) -> bool:
    """Write the JPEG a .jxl was made from to dst. False if the .jxl was not
    made from a JPEG (or cannot be read); dst is then not touched."""
    djxl = _tool("djxl")
    if not djxl:
        return False
    src, dst = Path(src), Path(dst)
    tmp = dst.with_name(dst.name + ".part.jpg")
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        # djxl, asked for a .jpg of a .jxl that did not come from a JPEG, makes
        # a new, lossy one without a word. It only says "Reconstructed to JPEG"
        # when it gave back the original.
        ok = _run([djxl, str(src), str(tmp), "-v"], "Reconstructed to JPEG") \
            and tmp.stat().st_size > 0
        if ok and not _is_jpeg(tmp):
            ok = False
        if ok:
            os.replace(tmp, dst)
        return ok
    except OSError:
        return False
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _is_jpeg(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(3) == b"\xff\xd8\xff"
    except OSError:
        return False


def compress_jpeg(src: Path | str, dst: Path | str, effort: int = 7) -> bool:
    """Make dst, a .jxl, from the JPEG src, and only keep it if turning it back
    gives exactly src. False (and no dst) otherwise, or if it is not smaller."""
    cjxl, djxl = _tool("cjxl"), _tool("djxl")
    src, dst = Path(src), Path(dst)
    if not (cjxl and djxl) or src.suffix.lower() not in JPEG_EXT:
        return False
    # cjxl picks its output format from the extension: .jxl goes last.
    tmp = dst.with_name(dst.stem + ".part.jxl")
    back = None
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not _run([cjxl, str(src), str(tmp), "-e", str(effort), "--quiet"]):
            return False
        if tmp.stat().st_size >= src.stat().st_size:
            return False
        fd, back_name = tempfile.mkstemp(suffix=".jpg", dir=str(dst.parent))
        os.close(fd)
        back = Path(back_name)
        if not (_run([djxl, str(tmp), str(back), "-v"], "Reconstructed to JPEG")
                and _same_bytes(src, back)):
            log.warning("jpeg xl of %s does not give the same jpeg back; kept as it is", src.name)
            return False
        # A photo with no date inside it is dated by its file: the smaller
        # one keeps the day the original had.
        st = src.stat()
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(tmp, dst)
        return True
    except OSError:
        return False
    finally:
        for f in (tmp, back):
            if f is not None:
                try:
                    f.unlink()
                except OSError:
                    pass


def open_image(path: Path | str):
    """A PIL image of a .jxl: the JPEG it was made from, or else its pixels."""
    from PIL import Image
    path = Path(path)
    djxl = _tool("djxl")
    if not djxl:
        raise OSError("JPEG XL is not available")
    with tempfile.TemporaryDirectory(prefix="piklin-jxl-") as d:
        # The JPEG it was made from, if there is one: nothing is lost, and the
        # metadata comes with it. Otherwise its pixels.
        out = Path(d) / "out.jpg"
        if _run([djxl, str(path), str(out), "-v"], "Reconstructed to JPEG") and out.exists():
            return Image.open(io.BytesIO(out.read_bytes()))
        # PPM, which the tools Piklin builds write without any library.
        out = Path(d) / "out.ppm"
        if _run([djxl, str(path), str(out), "--quiet"]) and out.exists():
            im = Image.open(io.BytesIO(out.read_bytes()))
            im.load()
            return im
    raise OSError(f"cannot read {path.name}")
