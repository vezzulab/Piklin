"""Off-thread rendering for the editor.

The rule this enforces: numpy never runs on the GTK main loop.  A single
worker thread owns the renderer, requests coalesce so a dragged slider
cannot build a backlog, and finished frames are handed back as textures
through ``GLib.idle_add``.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

import numpy as np
from gi.repository import GLib, Gdk, GObject


def texture_from_array(arr: np.ndarray) -> Gdk.Texture:
    """numpy float 0..1 or uint8 RGB -> Gdk.Texture."""
    if arr.dtype != np.uint8:
        arr = (np.clip(arr, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
    arr = np.ascontiguousarray(arr)
    h, w = arr.shape[:2]
    data = GLib.Bytes.new(arr.tobytes())
    return Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.R8G8B8, data, w * 3)


class RenderThread:
    """Serialises render requests onto one worker."""

    def __init__(self, on_frame: Callable):
        self._on_frame = on_frame
        self._cv = threading.Condition()
        self._pending = None
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="render",
                                        daemon=True)
        self._thread.start()
        self.busy = False

    def submit(self, fn, tag=None) -> None:
        """Queue work, replacing anything not yet started.

        Replacing rather than queueing is the point: while a slider moves
        we get a request per motion event, and rendering all of them would
        put the display further behind with every frame.
        """
        with self._cv:
            self._pending = (fn, tag)
            self._cv.notify()

    def stop(self) -> None:
        with self._cv:
            self._running = False
            self._cv.notify()

    def _loop(self) -> None:
        while True:
            with self._cv:
                while self._running and self._pending is None:
                    self._cv.wait()
                if not self._running:
                    return
                fn, tag = self._pending
                self._pending = None
            self.busy = True
            started = time.perf_counter()
            try:
                result = fn()
            except Exception as exc:
                result = None
                tag = ("error", str(exc), tag)
            elapsed = time.perf_counter() - started
            self.busy = False
            GLib.idle_add(self._deliver, result, tag, elapsed,
                          priority=GLib.PRIORITY_DEFAULT)

    def _deliver(self, result, tag, elapsed) -> bool:
        try:
            self._on_frame(result, tag, elapsed)
        except Exception:
            pass
        return False
