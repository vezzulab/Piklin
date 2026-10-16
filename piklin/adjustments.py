"""Copy the edits of one photo and paste them onto others.

A paste is an ordinary non-destructive edit: the copied layers are added to
the photo's own edit stack, saved in its own sidecar, and undone by putting
the sidecar back as it was.
"""
from __future__ import annotations

import json
from pathlib import Path

from .engine import tools
from .engine.stack import EditStack, Layer
from .i18n import N_

# What can be copied, in the order a person is asked. Tool groups are the
# editor's own; the ones marked True are copied unless the person unticks them.
CATEGORIES = (
    ("light", N_("Light and colour"), ("Adjust",), True),
    ("looks", N_("Looks and effects"), ("Effects", "Looks"), False),
    ("geometry", N_("Crop and rotate"), ("Geometry",), False),
    ("portrait", N_("Portrait"), ("Portrait",), False),
    ("overlay", N_("Text and frames"), ("Overlay",), False),
    # Strokes and spots belong to the one photo they were made on.
    ("local", N_("Local retouching"), ("Local",), False),
)


def category_of(tool_id: str) -> str | None:
    spec = tools.REGISTRY.get(tool_id)
    if spec is None:
        return None
    for cid, _label, groups, _default in CATEGORIES:
        if spec.group in groups:
            return cid
    return None


def read(library, photo_path) -> list[dict]:
    """The layers saved for a photo, as plain dictionaries."""
    stack = EditStack.load(library.edit_sidecar(Path(photo_path)))
    return [layer.to_dict() for layer in stack.layers]


def categories_in(layers: list[dict]) -> set[str]:
    return {c for l in layers if (c := category_of(l.get("tool", "")))}


def pick(layers: list[dict], categories: set[str]) -> list[dict]:
    return [l for l in layers if category_of(l.get("tool", "")) in categories]


def paste(library, catalog, photo_id, photo_path, layers: list[dict], *,
          replace: bool = False) -> str | None:
    """Add ``layers`` to a photo's edits. Returns the sidecar as it was
    (``""`` when the photo had no edits) so the paste can be undone."""
    sidecar = library.edit_sidecar(Path(photo_path))
    before = sidecar.read_text() if sidecar.is_file() else ""
    stack = EditStack.load(sidecar)
    if replace:
        copied = {l["tool"] for l in layers}
        stack.layers = [l for l in stack.layers if l.tool not in copied]
    for d in layers:
        stack.layers.append(Layer.from_dict(json.loads(json.dumps(d))))
    stack.save(sidecar, str(photo_path))
    if catalog is not None and photo_id is not None:
        catalog.note_edit(photo_id, len(stack))
    return before


def restore(library, catalog, photo_id, photo_path, before: str) -> None:
    """Put a photo's edits back as ``paste`` found them."""
    sidecar = library.edit_sidecar(Path(photo_path))
    if before:
        sidecar.write_text(before)
        count = len(EditStack.load(sidecar))
    else:
        sidecar.unlink(missing_ok=True)
        count = 0
    if catalog is not None and photo_id is not None:
        catalog.note_edit(photo_id, count)
