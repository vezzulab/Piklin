"""A fixed-size photo tile.

``Gtk.Picture`` reports its paintable's intrinsic size as its natural
size, and ``set_size_request`` only sets a *minimum*.  Inside a FlowBox
that means a 400px thumbnail asks for 400px and the grid ends up one
enormous column wide, no matter what tile size was requested.

This widget measures at exactly the size it is told and paints the
texture cropped to fill, which is the behaviour a thumbnail grid needs.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Graphene, Gsk, Gtk  # noqa: E402


class PhotoTile(Gtk.Widget):
    __gtype_name__ = "PikaPhotoTile"

    def __init__(self, size: int = 200, radius: float = 0.0):
        super().__init__()
        self._paintable = None
        self._size = int(size)
        self._radius = radius
        # "cover" fills the square and crops; "contain" shows the whole
        # photo at its own aspect ratio (the Aspect Ratio button).
        self._fit = "cover"
        self.set_overflow(Gtk.Overflow.HIDDEN)

    def set_fit(self, fit: str) -> None:
        fit = "contain" if fit == "contain" else "cover"
        if fit != self._fit:
            self._fit = fit
            self.queue_draw()

    def set_tile_size(self, size: int) -> None:
        if int(size) != self._size:
            self._size = int(size)
            self.queue_resize()

    def set_paintable(self, paintable) -> None:
        self._paintable = paintable
        self.queue_draw()

    def get_paintable(self):
        return self._paintable

    # -- geometry --------------------------------------------------------
    def do_measure(self, orientation, for_size):
        # minimum, natural, minimum baseline, natural baseline
        return (self._size, self._size, -1, -1)

    # -- painting --------------------------------------------------------
    def do_snapshot(self, snapshot):
        width = self.get_width()
        height = self.get_height()
        if width <= 0 or height <= 0:
            return

        rect = Graphene.Rect().init(0, 0, width, height)
        rounded = Gsk.RoundedRect()
        rounded.init_from_rect(rect, self._radius)
        snapshot.push_rounded_clip(rounded)

        if self._paintable is None:
            # placeholder block, so the grid keeps its shape while
            # thumbnails are still decoding
            snapshot.append_color(
                Gdk.RGBA(red=0.914, green=0.914, blue=0.933, alpha=1.0), rect)
            snapshot.pop()
            return

        pw = self._paintable.get_intrinsic_width() or width
        ph = self._paintable.get_intrinsic_height() or height
        if self._fit == "contain":
            # the whole photo, centred on the white wall; a small inset keeps
            # neighbouring photos from touching
            inset = 2.0
            scale = min((width - inset * 2) / pw, (height - inset * 2) / ph)
        else:
            # cover: fill the square, centre-cropping the longer axis
            scale = max(width / pw, height / ph)
        sw, sh = pw * scale, ph * scale
        snapshot.save()
        snapshot.translate(Graphene.Point().init((width - sw) / 2.0,
                                                 (height - sh) / 2.0))
        self._paintable.snapshot(snapshot, sw, sh)
        snapshot.restore()
        snapshot.pop()
