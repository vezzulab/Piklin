"""What a drag out of Piklin carries, and the moment the JPEG copies are made.

The drag declares its files when it starts: the photos themselves where they
open anywhere, the place a JPEG copy will be where they do not. The copies
are made when the photos are dropped, and only if they are dropped outside
Piklin - onto an album in the sidebar nothing needs converting.
"""
from __future__ import annotations

import gi

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio  # noqa: E402

from .. import dragout


def provider_for(library, paths, inside: Gdk.ContentProvider) -> Gdk.ContentProvider:
    """What a drag carries: what the album drop targets inside Piklin read,
    and the files everything else reads."""
    files = [Gio.File.new_for_path(str(dragout.plan_for_drag(library, p)[0]))
             for p in paths]
    listed = Gdk.ContentProvider.new_for_value(Gdk.FileList.new_from_list(files))
    return Gdk.ContentProvider.new_union([inside, listed])


def dropped_outside(widget) -> bool:
    """Whether the pointer is not over one of Piklin's own windows."""
    try:
        seat = Gdk.Display.get_default().get_default_seat()
        surface, _x, _y = seat.get_pointer().get_surface_at_position()
    except Exception:
        return True
    root = widget.get_root()
    return surface is None or root is None or surface is not root.get_surface()


def make_copies_on_drop(drag, widget, library, paths) -> None:
    """When the photos are dropped outside Piklin, make the JPEG copies the
    drag promised before the program they were dropped on comes to read them."""
    def performed(_drag):
        if dropped_outside(widget):
            dragout.files_for_drag(library, paths)
    drag.connect("drop-performed", performed)
