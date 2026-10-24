"""The video encoder, in a process of its own.

Making a video smaller takes minutes of every core. In Piklin's own process
it held the interpreter's lock in bursts, and the window stalled for up to a
tenth of a second at a time (measured: the longest wait of a 5 ms timer went
from 3 ms to 100 ms while a video was being made smaller); and it shared the
disk and the memory with everything else the person was doing, so a copy of
photos made at the same time crawled.

In a process of its own the encoder cannot stall the window (the same
measurement: 10 ms), it can be told to give way to the disk, it can be stopped
for as long as photos are being copied and go on afterwards, and its memory
goes with it when it ends.

The parent writes the job in a file, starts the process and listens to what it
prints, one word per line: STARTED, P <fraction>, DONE <file>, ERR <why>,
CANCELLED. Anything else the process prints (the encoder's own messages) is
ignored. Where a process cannot be started this way (Windows), or fails to
start, the video is made in Piklin's own process, as it always was.
"""
from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ENV = "PIKLIN_ENCODE_JOB"          # set: this start of Piklin is an encoder, not a window

# What a process started by Python itself runs: the parent's search path (the
# app may keep its code anywhere), then the job.
_CODE = ("import sys, json\n"
         "sys.path[:0] = json.loads(sys.argv[1])\n"
         "from piklin import encode_worker\n"
         "raise SystemExit(encode_worker.run(sys.argv[2]))\n")


def available() -> bool:
    return sys.platform != "win32" and not os.environ.get("PIKLIN_NO_WORKER")


def _command(job: Path) -> tuple[list[str], dict]:
    env = dict(os.environ)
    if os.environ.get("PIKLIN_BUNDLE_CONTENTS"):
        # A Mac app: its launcher starts Piklin whatever it is given, and
        # Piklin's main() sees the job in the environment (see app.main).
        env[ENV] = str(job)
        return [sys.executable], env
    return [sys.executable, "-I", "-c", _CODE, json.dumps(sys.path), str(job)], env


# -- the encoder's side ---------------------------------------------------------
def run(job_path) -> int:
    """Make the video the job asks for and say how it went. Returns the exit code."""
    from . import system, video_edit as ve
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    system.hide_from_dock()
    system.lower_encoder_priority()

    def stop(*_args):
        raise ve.Cancelled()
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop)

    def say(text: str) -> None:
        print(text, flush=True)

    last = [0.0]

    def progress(fraction: float) -> None:
        now = time.monotonic()
        if now - last[0] >= 0.5 or fraction >= 1.0:
            last[0] = now
            say(f"P {fraction:.4f}")

    say("STARTED")
    meta = dict(job.get("meta") or {})
    if meta.get("location"):
        meta["location"] = tuple(meta["location"])
    try:
        done = ve.export(job["src"], job["dst"], ve.VideoEdit(duration=job["duration"]),
                         fmt=job["fmt"], max_side=job.get("max_side"),
                         keep_location=bool(job.get("keep_location")), meta=meta,
                         on_progress=progress, bit_rate=job.get("bit_rate"),
                         crf=job.get("crf"), preset=job.get("preset"))
    except ve.Cancelled:
        say("CANCELLED")
        return 3
    except BaseException as exc:                    # the parent is told, and decides
        say("ERR " + repr(exc)[:300])
        return 1
    say(f"DONE {done}")
    return 0


# -- the parent's side ----------------------------------------------------------
def export(path, dst, duration: float, *, fmt: str = "mp4", max_side: int | None = None,
           keep_location: bool = False, meta: dict | None = None, on_progress=None,
           cancel: threading.Event | None = None, bit_rate: int | None = None,
           crf: int | None = None, preset: int | None = None, pause=None) -> Path:
    """video_edit.export for a whole video, made in a process of its own.

    ``pause()`` is asked every moment: while it says True the encoder is stopped
    (photos are being copied) and goes on when it says False. ``cancel`` ends it.
    Raises video_edit.Cancelled when cancelled, RuntimeError when it failed."""
    from . import video_edit as ve

    def in_process() -> Path:
        return ve.export(path, dst, ve.VideoEdit(duration=duration), fmt=fmt, max_side=max_side,
                         keep_location=keep_location, meta=meta, on_progress=on_progress,
                         cancel=cancel, bit_rate=bit_rate, crf=crf, preset=preset)

    if not available():
        return in_process()
    final = Path(dst).with_suffix(ve.EXPORT_FORMATS[fmt]["suffix"])
    leftover = final.with_name(f".{final.name}.part")
    folder = Path(tempfile.mkdtemp(prefix="piklin-encode-"))
    job = folder / "job.json"
    job.write_text(json.dumps({
        "src": str(path), "dst": str(dst), "duration": duration, "fmt": fmt,
        "max_side": max_side, "keep_location": keep_location, "meta": meta or {},
        "bit_rate": bit_rate, "crf": crf, "preset": preset}), encoding="utf-8")
    command, env = _command(job)
    errors = open(folder / "errors.txt", "wb")
    try:
        proc = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=errors, text=True)
    except OSError:
        errors.close()
        _remove(folder)
        return in_process()

    lines: queue.Queue = queue.Queue()

    def read():
        for line in proc.stdout:
            lines.put(line.rstrip("\n"))
        lines.put(None)
    threading.Thread(target=read, daemon=True, name="encoder-output").start()

    started = stopped = cancelled = False
    done = None
    why = ""
    try:
        while True:
            try:
                line = lines.get(timeout=0.25)
            except queue.Empty:
                line = ""
            if line is None:
                break
            if line == "STARTED":
                started = True
            elif line.startswith("P ") and on_progress is not None:
                try:
                    on_progress(float(line[2:]))
                except ValueError:
                    pass
            elif line.startswith("DONE "):
                done = line[5:]
            elif line.startswith("ERR "):
                why = line[4:]
            if cancel is not None and cancel.is_set():
                cancelled = True
                break
            if started:
                want = bool(pause is not None and pause())
                if want != stopped:
                    _signal(proc, signal.SIGSTOP if want else signal.SIGCONT)
                    stopped = want
    except BaseException:
        _end(proc)                  # whatever went wrong here, the encoder does not go on alone
        raise
    finally:
        errors.close()
    if cancelled:
        _end(proc)
    code = proc.wait()
    if stopped:
        _signal(proc, signal.SIGCONT)
    detail = ""
    try:
        detail = (folder / "errors.txt").read_text(errors="replace")[-300:].strip()
    except OSError:
        pass
    _remove(folder)
    if cancelled:
        leftover.unlink(missing_ok=True)
        raise ve.Cancelled()
    if not started and done is None:
        # It never began (the process could not be started this way here):
        # the video is made in Piklin's own process, as it always was.
        leftover.unlink(missing_ok=True)
        return in_process()
    if done is None:
        leftover.unlink(missing_ok=True)
        if code == 3:
            raise ve.Cancelled()
        raise RuntimeError(why or detail or f"the encoder ended with code {code}")
    return final


def _signal(proc, sig) -> None:
    try:
        os.kill(proc.pid, sig)
    except (OSError, ProcessLookupError):
        pass


def _end(proc) -> None:
    """Stop the encoder: asked first (it removes its half-made file), then made to."""
    _signal(proc, signal.SIGCONT)               # a stopped process cannot hear the request
    _signal(proc, signal.SIGTERM)
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        _signal(proc, signal.SIGKILL)


def _remove(folder: Path) -> None:
    import shutil
    shutil.rmtree(folder, ignore_errors=True)
