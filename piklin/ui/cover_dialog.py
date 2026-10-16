"""Framing the picture of a person: a round window over their photo, moved and
made larger or smaller with the mouse, and the photo turned if it is on its side.

Piklin finds the face for it and shows where; when it found a button, or the
face is turned or too small, the person says where it is. What is saved is the
square (people.set_cover_custom), so it does not depend on the faces found."""
from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GObject, Gdk, Gtk  # noqa: E402

from .. import imageio as iio
from ..i18n import _

VIEW = 560                # the photo is shown at most this wide and tall
HANDLE = 16


class CoverDialog(Adw.Dialog):
    __gsignals__ = {
        # photo id, cx, cy, side, rotation
        "framed": (GObject.SignalFlags.RUN_FIRST, None, (int, float, float, float, int)),
    }

    def __init__(self, path, photo_id: int, face=None):
        super().__init__(title=_("Frame the Picture"), content_width=VIEW + 48, content_height=VIEW + 190)
        self.photo_id = photo_id
        self.rot = 0
        self._base = iio.load_pil(path, max_side=1400).convert("RGB")
        self._surface = None
        self._data = b""
        # where the square starts: around the face Piklin found, else the middle
        W, H = self._base.size
        if face is not None:
            self.cx, self.cy = face["x"] + face["w"] / 2, face["y"] + face["h"] / 2
            self.s = min(1.0, max(face["w"] * W, face["h"] * H) * 1.6 / max(W, H))
        else:
            self.cx = self.cy = 0.5
            self.s = 0.4
        self._drag = None

        view = Adw.ToolbarView()
        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda *_a: self.close())
        header.pack_start(cancel)
        use = Gtk.Button(label=_("Use as Picture"))
        use.add_css_class("suggested-action")
        use.connect("clicked", self._on_use)
        header.pack_end(use)
        view.add_top_bar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=6,
                      margin_bottom=12, margin_start=24, margin_end=24)
        hint = Gtk.Label(label=_("Drag the circle onto the face. Make it larger or smaller with "
                                 "the wheel or the slider, and turn the photo if it is on its side."),
                         wrap=True, xalign=0.0)
        hint.add_css_class("pika-dim")
        box.append(hint)
        self.area = Gtk.DrawingArea(content_width=VIEW, content_height=VIEW, hexpand=True,
                                    vexpand=True, halign=Gtk.Align.CENTER)
        self.area.set_draw_func(self._draw)
        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        drag.connect("drag-end", lambda *_a: setattr(self, "_drag", None))
        self.area.add_controller(drag)
        wheel = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        wheel.connect("scroll", self._on_wheel)
        self.area.add_controller(wheel)
        box.append(self.area)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        left = Gtk.Button(icon_name="object-rotate-left-symbolic", tooltip_text=_("Turn Left"))
        right = Gtk.Button(icon_name="object-rotate-right-symbolic", tooltip_text=_("Turn Right"))
        left.connect("clicked", lambda *_a: self._turn(-1))
        right.connect("clicked", lambda *_a: self._turn(1))
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.05, 1.0, 0.01)
        self.scale.set_value(self.s)
        self.scale.set_hexpand(True)
        self.scale.set_draw_value(False)
        self.scale.connect("value-changed", self._on_scale)
        row.append(left)
        row.append(right)
        row.append(self.scale)
        box.append(row)
        view.set_content(box)
        self.set_child(view)
        self._rebuild_texture()

    # -- the photo as it is now turned --------------------------------------
    def _turned(self):
        return self._base.rotate(-self.rot, expand=True) if self.rot else self._base

    def _rebuild_texture(self):
        import cairo
        im = self._turned()
        scale = min(VIEW / im.width, VIEW / im.height, 1.0)
        shown = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))))
        self._tw, self._th = shown.size
        # cairo wants premultiplied BGRA
        self._data = bytearray(shown.convert("RGBA").tobytes("raw", "BGRa"))
        self._surface = cairo.ImageSurface.create_for_data(
            self._data, cairo.FORMAT_ARGB32, self._tw, self._th, self._tw * 4)
        self.area.set_content_width(self._tw)
        self.area.set_content_height(self._th)
        self.area.queue_draw()

    def _turn(self, quarter):
        # the square stays on what it was over: its centre turns with the photo
        if quarter > 0:
            self.cx, self.cy = 1 - self.cy, self.cx
        else:
            self.cx, self.cy = self.cy, 1 - self.cx
        self.rot = (self.rot + 90 * quarter) % 360
        self._rebuild_texture()

    # -- drawing and moving -------------------------------------------------------
    def _square(self):
        """The square in widget pixels: centre and half side."""
        side = self.s * max(self._tw, self._th)
        side = min(side, self._tw, self._th)
        return self.cx * self._tw, self.cy * self._th, side / 2

    def _draw(self, _area, cr, w, h):
        if getattr(self, "_surface", None) is None:
            return
        cr.set_source_surface(self._surface, 0, 0)
        cr.paint()
        x, y, r = self._square()
        # outside the circle, darkened
        cr.save()
        cr.rectangle(0, 0, self._tw, self._th)
        cr.arc(x, y, r, 0, 2 * math.pi)
        cr.set_fill_rule(1)       # even-odd: the circle is a hole
        cr.set_source_rgba(0, 0, 0, 0.55)
        cr.fill()
        cr.restore()
        cr.set_source_rgba(1, 1, 1, 0.95)
        cr.set_line_width(2.5)
        cr.arc(x, y, r, 0, 2 * math.pi)
        cr.stroke()
        # the handle that makes it larger, at its lower right
        hx, hy = x + r * math.cos(math.pi / 4), y + r * math.sin(math.pi / 4)
        cr.arc(hx, hy, HANDLE / 2, 0, 2 * math.pi)
        cr.set_source_rgba(1, 1, 1, 1)
        cr.fill_preserve()
        cr.set_source_rgba(0, 0, 0, 0.6)
        cr.set_line_width(1.5)
        cr.stroke()

    def _drag_begin(self, _g, x, y):
        cx, cy, r = self._square()
        hx, hy = cx + r * math.cos(math.pi / 4), cy + r * math.sin(math.pi / 4)
        if math.hypot(x - hx, y - hy) <= HANDLE:
            self._drag = ("size", cx, cy, r)
        else:
            self._drag = ("move", self.cx, self.cy, x, y)
            if math.hypot(x - cx, y - cy) > r:
                # a click outside the circle puts it there
                self.cx, self.cy = x / self._tw, y / self._th
                self._drag = ("move", self.cx, self.cy, x, y)
                self.area.queue_draw()

    def _drag_update(self, _g, dx, dy):
        if not self._drag:
            return
        if self._drag[0] == "move":
            _k, cx0, cy0, x0, y0 = self._drag
            self.cx = min(1.0, max(0.0, cx0 + dx / self._tw))
            self.cy = min(1.0, max(0.0, cy0 + dy / self._th))
        else:
            _k, cx, cy, r0 = self._drag
            hx0 = cx + r0 * math.cos(math.pi / 4) + dx
            hy0 = cy + r0 * math.sin(math.pi / 4) + dy
            r = max(8.0, math.hypot(hx0 - cx, hy0 - cy))
            self._set_size(2 * r / max(self._tw, self._th))
        self.area.queue_draw()

    def _on_wheel(self, _c, _dx, dy):
        self._set_size(self.s * (1.08 if dy > 0 else 1 / 1.08))
        return True

    def _set_size(self, s):
        self.s = min(1.0, max(0.05, s))
        if abs(self.scale.get_value() - self.s) > 1e-4:
            self.scale.set_value(self.s)

    def _on_scale(self, scale):
        self.s = scale.get_value()
        self.area.queue_draw()

    def _on_use(self, *_a):
        self.emit("framed", self.photo_id, self.cx, self.cy, self.s, self.rot)
        self.close()
