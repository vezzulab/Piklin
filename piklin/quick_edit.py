"""Quick edits made without opening the editor: turning a photo or a video.

A turn is an ordinary, non-destructive edit - the same Rotate layer the
editor uses for photos, the same rotation a video edit carries - so it can
be undone by turning back, changed in the editor, or reverted with the rest.
"""
from __future__ import annotations

from pathlib import Path

from .engine.stack import EditStack


def rotate(library, catalog, photo_id: int, path, turns: int, *,
           is_video: bool = False, duration: float = 0.0) -> int:
    """Turn by ``turns`` quarter turns clockwise (negative: counterclockwise).

    Returns the photo's rotation afterwards, in quarter turns (0-3).
    """
    path = Path(path)
    if is_video:
        from . import video_edit as ve
        edit = ve.load(library, path, duration)
        edit.rotate = (edit.rotate + 90 * turns) % 360
        ve.save(library, path, edit, catalog, photo_id)
        return edit.rotate // 90

    sidecar = library.edit_sidecar(path)
    stack = EditStack.load(sidecar)
    # The last Rotate layer is the one that decides how the photo is turned.
    index = next((i for i in range(len(stack.layers) - 1, -1, -1)
                  if stack.layers[i].tool == "rotate"), None)
    if index is None:
        layer = stack.add("rotate", {"quarter_turns": turns % 4}, record=False)
        quarter = layer.params["quarter_turns"] if layer else 0
    else:
        layer = stack.layers[index]
        quarter = (int(layer.params.get("quarter_turns", 0)) + turns) % 4
        params = dict(layer.params, quarter_turns=quarter)
        untouched = (quarter == 0 and not params.get("straighten")
                     and not params.get("flip_h") and not params.get("flip_v"))
        if untouched:
            # turned all the way back: the layer does nothing any more
            stack.layers.pop(index)
        else:
            layer.params = params
    if len(stack):
        stack.save(sidecar, str(path))
    elif sidecar.exists():
        sidecar.unlink()
    if catalog is not None and photo_id is not None:
        catalog.note_edit(photo_id, len(stack))
    return quarter
