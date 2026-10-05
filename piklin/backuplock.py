"""One backup at a time to a destination, whichever computer makes it.

Two computers sharing a NAS must not both be sending their libraries to it at
once: they would copy the same files over each other, and each would show a
backup of thousands of files that the other has just made. A computer that is
about to back up looks for a turn kept at the destination; if another computer
holds it, this one waits and asks again later.

The turn is one small file, renewed every half minute while the backup runs and
deleted when it ends. One that has not been renewed for ten minutes belongs to
a computer that went to sleep or was switched off, and is taken over. Receiving
what another computer sent is not held up by it.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path

# No ".json": Piklin counts the top-level .json files of a backup as its own,
# and this one comes and goes.
LOCK = ".piklin-backup-lock"
FRESH = 600.0               # seconds a turn lasts without being renewed
BEAT = 30.0
SETTLE = 2.0                # after taking the turn, wait and look once more
SKEW = 60.0                 # clocks that differ by less than this are alike


class Busy(Exception):
    """Another computer has the turn; ``who`` is its name."""

    def __init__(self, who: str):
        super().__init__(who)
        self.who = who


def me() -> tuple[str, str]:
    """An identity for this computer (kept outside any library, so a library
    copied to another computer does not carry it along) and its name."""
    import socket
    from .paths import config_dir
    path = Path(config_dir()) / "piklin-computer-id"
    try:
        ident = path.read_text(encoding="utf-8").strip()
    except OSError:
        ident = ""
    if not ident:
        ident = uuid.uuid4().hex
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(ident, encoding="utf-8")
        except OSError:
            pass
    return ident, socket.gethostname().split(".")[0] or "another computer"


def _read(backend) -> dict | None:
    folder = Path(tempfile.mkdtemp(prefix="piklin-turn-"))
    try:
        target = folder / "turn"
        if not backend.get(LOCK, target) or not target.is_file():
            return None
        data = json.loads(target.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _write(backend, ident: str, name: str) -> bool:
    folder = Path(tempfile.mkdtemp(prefix="piklin-turn-"))
    try:
        source = folder / "turn"
        source.write_text(json.dumps({"id": ident, "name": name, "at": time.time()}),
                          encoding="utf-8")
        return bool(backend.put(source, LOCK))
    except OSError:
        return False
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _held_by_other(turn: dict | None, ident: str) -> bool:
    if not turn or turn.get("id") == ident:
        return False
    try:
        age = time.time() - float(turn.get("at") or 0)
    except (TypeError, ValueError):
        return False
    return -SKEW < age < FRESH


class Turn:
    """A turn taken at a destination. ``release()`` ends it."""

    def __init__(self, backend, ident: str, name: str):
        self.backend, self.ident, self.name = backend, ident, name
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._renew, daemon=True,
                                        name="piklin-backup-turn")
        self._thread.start()

    def _renew(self) -> None:
        while not self._stop.wait(BEAT):
            try:
                _write(self.backend, self.ident, self.name)
            except Exception:
                pass

    def release(self) -> None:
        self._stop.set()
        try:
            turn = _read(self.backend)
            if turn and turn.get("id") == self.ident:
                self.backend.delete(LOCK)
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.release()


def take(backend) -> Turn | None:
    """Take the turn at this destination. Raises Busy when another computer
    has it. Returns None when the destination cannot keep a turn at all: a
    backup is not refused for that."""
    ident, name = me()
    try:
        turn = _read(backend)
        if _held_by_other(turn, ident):
            raise Busy(str(turn.get("name") or "another computer"))
        if not _write(backend, ident, name):
            return None
        # Two computers can write within the same second: whoever's file is
        # still there after a moment has the turn, and the other steps back.
        time.sleep(SETTLE)
        turn = _read(backend)
        if _held_by_other(turn, ident):
            raise Busy(str(turn.get("name") or "another computer"))
    except Busy:
        raise
    except Exception:
        return None
    return Turn(backend, ident, name)
