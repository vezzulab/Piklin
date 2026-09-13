"""The edit stack and its renderer.

An edit is a list of layers.  Each layer names a tool, carries that
tool's parameters, and may carry its own mask.  Rendering walks the list.
The original file is never written.

The renderer's job is to make dragging a slider feel instant on a 60
megapixel photo, which it does two ways:

**Prefix caching.**  Changing layer *k* cannot affect layers before it,
so the image as it stood after layer k-1 is cached and reused.  Adjusting
the layer you are working on - overwhelmingly the common case - costs one
tool, not the whole stack.

**Draft renders.**  While a slider is moving, the frame is rendered at a
smaller size with expensive approximations enabled, and the full-quality
render is issued once the value settles.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from . import looks as looks_mod
from . import ops, tools
from .tools import Ctx, REGISTRY

FORMAT_VERSION = 1


@dataclass
class Layer:
    tool: str
    params: dict = field(default_factory=dict)
    enabled: bool = True
    opacity: float = 1.0
    # An optional stack brush: strokes limiting where this layer applies.
    # Empty means the layer applies everywhere.
    mask_strokes: list = field(default_factory=list)
    mask_invert: bool = False
    created_at: float = field(default_factory=time.time)

    @property
    def spec(self) -> tools.Tool | None:
        return REGISTRY.get(self.tool)

    @property
    def name(self) -> str:
        s = self.spec
        return s.name if s else self.tool

    def signature(self) -> str:
        """Stable hash of everything that affects this layer's output."""
        payload = json.dumps({
            "t": self.tool, "p": self.params, "e": self.enabled,
            "o": round(float(self.opacity), 4), "m": self.mask_strokes,
            "i": self.mask_invert,
        }, sort_keys=True, default=str)
        return hashlib.blake2b(payload.encode(), digest_size=12).hexdigest()

    def to_dict(self) -> dict:
        return {"tool": self.tool, "params": self.params,
                "enabled": self.enabled, "opacity": self.opacity,
                "mask_strokes": self.mask_strokes,
                "mask_invert": self.mask_invert,
                "created_at": self.created_at}

    @classmethod
    def from_dict(cls, d: dict) -> "Layer":
        return cls(tool=d.get("tool", ""), params=dict(d.get("params") or {}),
                   enabled=bool(d.get("enabled", True)),
                   opacity=float(d.get("opacity", 1.0)),
                   mask_strokes=list(d.get("mask_strokes") or []),
                   mask_invert=bool(d.get("mask_invert", False)),
                   created_at=float(d.get("created_at", time.time())))


class EditStack:
    """An ordered list of layers, with undo history."""

    MAX_UNDO = 60

    def __init__(self, layers: list[Layer] | None = None):
        self.layers: list[Layer] = layers or []
        self._undo: list[list[dict]] = []
        self._redo: list[list[dict]] = []
        self.version = 0

    # -- history ---------------------------------------------------------
    def snapshot(self) -> None:
        """Record the current state so the next change can be undone."""
        self._undo.append([l.to_dict() for l in self.layers])
        if len(self._undo) > self.MAX_UNDO:
            self._undo.pop(0)
        self._redo.clear()

    def _restore(self, state: list[dict]) -> None:
        self.layers = [Layer.from_dict(d) for d in state]
        self.version += 1

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append([l.to_dict() for l in self.layers])
        self._restore(self._undo.pop())
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append([l.to_dict() for l in self.layers])
        self._restore(self._redo.pop())
        return True

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    # -- editing ---------------------------------------------------------
    def add(self, tool_id: str, params: dict | None = None,
            record: bool = True) -> Layer | None:
        spec = REGISTRY.get(tool_id)
        if spec is None:
            return None
        if record:
            self.snapshot()
        p = spec.defaults()
        if params:
            p.update(params)
        layer = Layer(tool=tool_id, params=p)
        self.layers.append(layer)
        self.version += 1
        return layer

    def remove(self, index: int) -> None:
        if 0 <= index < len(self.layers):
            self.snapshot()
            self.layers.pop(index)
            self.version += 1

    def move(self, index: int, to: int) -> None:
        if not (0 <= index < len(self.layers)) or not (0 <= to < len(self.layers)):
            return
        self.snapshot()
        self.layers.insert(to, self.layers.pop(index))
        self.version += 1

    def set_params(self, index: int, params: dict, record: bool = False) -> None:
        if not (0 <= index < len(self.layers)):
            return
        if record:
            self.snapshot()
        self.layers[index].params.update(params)
        self.version += 1

    def apply_look(self, name: str) -> None:
        layers = looks_mod.apply_look(name)
        if not layers:
            return
        self.snapshot()
        for tid, params in layers:
            self.add(tid, params, record=False)

    def clear(self) -> None:
        if self.layers:
            self.snapshot()
            self.layers = []
            self.version += 1

    def __len__(self) -> int:
        return len(self.layers)

    def __iter__(self):
        return iter(self.layers)

    # -- serialisation ---------------------------------------------------
    def to_dict(self, source: str | None = None) -> dict:
        return {
            "format": "pikalicious-edit",
            "version": FORMAT_VERSION,
            "source": source,
            "saved_at": time.time(),
            "layers": [l.to_dict() for l in self.layers],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EditStack":
        return cls([Layer.from_dict(x) for x in (d.get("layers") or [])])

    def save(self, path: Path | str, source: str | None = None) -> None:
        """Write the sidecar.

        Written to a temporary file and renamed, so an interrupted save
        cannot leave a truncated JSON file where the edit used to be.
        """
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(source), indent=2,
                                  sort_keys=False))
        tmp.replace(p)

    @classmethod
    def load(cls, path: Path | str) -> "EditStack":
        p = Path(path)
        if not p.is_file():
            return cls()
        try:
            return cls.from_dict(json.loads(p.read_text()))
        except (OSError, ValueError):
            return cls()

    def signature(self) -> str:
        h = hashlib.blake2b(digest_size=12)
        for l in self.layers:
            h.update(l.signature().encode())
        return h.hexdigest()


class Renderer:
    """Renders a stack onto a source image, caching intermediate results."""

    def __init__(self, max_cached: int = 5):
        self.max_cached = max_cached
        # index -> (prefix_signature, image).  index i holds the image as
        # it stood *before* layer i was applied.
        self._cache: dict[int, tuple[str, np.ndarray]] = {}
        self._source_key: tuple | None = None

    def invalidate(self) -> None:
        self._cache.clear()
        self._source_key = None

    @staticmethod
    def _prefix_sigs(stack: EditStack) -> list[str]:
        """Cumulative signature before each layer."""
        sigs, h = [], hashlib.blake2b(digest_size=12)
        for layer in stack.layers:
            sigs.append(h.hexdigest())
            h.update(layer.signature().encode())
        sigs.append(h.hexdigest())
        return sigs

    def render(self, source: np.ndarray, stack: EditStack, *,
               scale: float = 1.0, full_size: tuple[int, int] | None = None,
               draft: bool = False, upto: int | None = None,
               seed: int = 7, face_detection: bool = True) -> np.ndarray:
        """Apply the stack to ``source``.

        ``upto`` renders only the first N layers, which is how the editor
        shows a layer's state while you are adjusting it.
        """
        layers = stack.layers if upto is None else stack.layers[:upto]
        h, w = source.shape[:2]
        ctx = Ctx(scale=scale, full_size=full_size or (w, h), seed=seed,
                  preview=scale < 0.999, draft=draft,
                  face_detection=face_detection)

        # A different source image (or a different preview size) makes
        # every cached intermediate meaningless.
        key = (id(source), source.shape, round(scale, 5), draft)
        if key != self._source_key:
            self._cache.clear()
            self._source_key = key

        sigs = self._prefix_sigs(stack)
        start, img = 0, source
        # Reuse the deepest cached prefix that still matches.
        for i in range(min(len(layers), len(sigs) - 1), 0, -1):
            hit = self._cache.get(i)
            if hit and hit[0] == sigs[i]:
                start, img = i, hit[1]
                break

        for i in range(start, len(layers)):
            layer = layers[i]
            spec = layer.spec
            if spec is None:
                continue
            if not layer.enabled:
                self._store(i + 1, sigs[i + 1], img)
                continue
            before = img
            try:
                out = spec.apply(img, layer.params, ctx)
            except Exception:
                # One broken layer must not cost the user the rest of the
                # edit; skip it and keep rendering.
                out = img

            # A geometry tool changes dimensions, so a mask or opacity
            # from before the change cannot be composited over it.
            same_shape = out.shape == before.shape
            if same_shape and layer.mask_strokes:
                mask = tools.rasterize_strokes(before.shape[:2],
                                               layer.mask_strokes)
                if layer.mask_invert:
                    mask = 1.0 - mask
                out = ops.composite(before, out, mask)
            if same_shape and layer.opacity < 0.999:
                out = (before * np.float32(1.0 - layer.opacity)
                       + out * np.float32(layer.opacity)).astype(np.float32)
            img = out
            self._store(i + 1, sigs[i + 1], img)
        return img

    def _store(self, index: int, sig: str, img: np.ndarray) -> None:
        self._cache[index] = (sig, img)
        if len(self._cache) > self.max_cached:
            # Drop the shallowest entries: the deep ones are what a slider
            # drag on the top layer reuses.
            for k in sorted(self._cache)[:len(self._cache) - self.max_cached]:
                self._cache.pop(k, None)


def render_full(source_path: Path | str, stack: EditStack,
                max_side: int | None = None) -> np.ndarray:
    """Render at full (or capped) resolution, for export.

    Deliberately does not share the interactive renderer's cache: an
    export must be computed from the original pixels at export scale, not
    upscaled from whatever the preview happened to hold.
    """
    from .. import imageio as iio
    img = iio.load_rgb(source_path, max_side=max_side)
    h, w = img.shape[:2]
    r = Renderer(max_cached=1)
    return r.render(img, stack, scale=1.0, full_size=(w, h), draft=False)
