"""Small reusable controls: parameter sliders, curve editor, histogram."""
from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import GObject, Gdk, Gtk  # noqa: E402

import numpy as np


class ParamSlider(Gtk.Box):
    """A labelled slider that reports both live and settled values.

    The distinction matters for performance: dragging emits ``changing``
    so the canvas can show a fast draft, and releasing emits ``changed``
    once so the full-quality render and the undo entry happen a single
    time rather than on every motion event.
    """

    __gsignals__ = {
        "changing": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        "changed": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
    }

    def __init__(self, param, value=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.param = param
        self.add_css_class("pika-slider-row")

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.label = Gtk.Label(label=param.label, xalign=0.0, hexpand=True)
        self.label.add_css_class("pika-slider-label")
        self.value_label = Gtk.Label(xalign=1.0)
        self.value_label.add_css_class("pika-slider-value")
        header.append(self.label)
        header.append(self.value_label)
        self.append(header)

        step = param.step or 1.0
        self.adj = Gtk.Adjustment(
            lower=param.lo, upper=param.hi,
            value=float(value if value is not None else param.default),
            step_increment=step, page_increment=step * 10)
        self.scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                               adjustment=self.adj, draw_value=False,
                               hexpand=True)
        # A tick at the neutral point tells you where "no effect" is
        # without having to read the number.
        neutral = param.neutral if param.neutral is not None else (
            0.0 if param.lo < 0 else param.lo)
        if param.lo <= neutral <= param.hi:
            self.scale.add_mark(neutral, Gtk.PositionType.BOTTOM, None)
        self.append(self.scale)

        self._dragging = False
        self.adj.connect("value-changed", self._on_value)

        gesture = Gtk.GestureClick()
        gesture.connect("pressed", lambda *_: setattr(self, "_dragging", True))
        gesture.connect("released", self._on_release)
        self.scale.add_controller(gesture)

        # double-click resets to the tool's default
        dbl = Gtk.GestureClick(button=1)
        dbl.connect("pressed", self._on_maybe_reset)
        self.scale.add_controller(dbl)

        self._update_label()

    def _on_maybe_reset(self, gesture, n_press, x, y):
        if n_press >= 2:
            self.adj.set_value(float(self.param.default))
            self.emit("changed", self.adj.get_value())

    def _on_value(self, _adj):
        self._update_label()
        if self._dragging:
            self.emit("changing", self.adj.get_value())
        else:
            self.emit("changed", self.adj.get_value())

    def _on_release(self, *_):
        if self._dragging:
            self._dragging = False
            self.emit("changed", self.adj.get_value())

    def _update_label(self):
        v = self.adj.get_value()
        if abs(self.param.step - 1.0) < 1e-6:
            self.value_label.set_text(f"{v:+.0f}" if self.param.lo < 0
                                      else f"{v:.0f}")
        else:
            self.value_label.set_text(f"{v:+.1f}" if self.param.lo < 0
                                      else f"{v:.1f}")

    def get_value(self) -> float:
        return self.adj.get_value()

    def set_value(self, v: float) -> None:
        self.adj.set_value(float(v))


class CurveEditor(Gtk.DrawingArea):
    """Draggable tone curve over a live histogram."""

    __gsignals__ = {
        "curve-changed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    HIT = 0.045

    def __init__(self):
        super().__init__()
        self.set_content_height(220)
        self.set_content_width(220)
        self.points = [(0.0, 0.0), (1.0, 1.0)]
        self.histogram = None
        self._drag = None
        self.set_draw_func(self._draw)

        click = Gtk.GestureClick()
        click.connect("pressed", self._on_press)
        click.connect("released", lambda *_: setattr(self, "_drag", None))
        self.add_controller(click)

        drag = Gtk.GestureDrag()
        drag.connect("drag-update", self._on_drag)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)

        right = Gtk.GestureClick(button=3)
        right.connect("pressed", self._on_right)
        self.add_controller(right)

    def set_points(self, pts):
        self.points = sorted((float(x), float(y)) for x, y in pts) or \
            [(0.0, 0.0), (1.0, 1.0)]
        self.queue_draw()

    def set_histogram(self, hist):
        self.histogram = hist
        self.queue_draw()

    def _xy(self, px, py):
        w, h = self.get_width() or 1, self.get_height() or 1
        return (min(1.0, max(0.0, px / w)), min(1.0, max(0.0, 1.0 - py / h)))

    def _on_press(self, gesture, n, px, py):
        x, y = self._xy(px, py)
        for i, (cx, cy) in enumerate(self.points):
            if math.hypot(cx - x, cy - y) < self.HIT:
                self._drag = i
                return
        if n >= 2:
            self.points.append((x, y))
            self.points.sort()
            self._drag = self.points.index((x, y))
            self.queue_draw()
            self.emit("curve-changed", list(self.points))

    def _on_right(self, gesture, n, px, py):
        """Right-click removes a point, except the two endpoints."""
        x, y = self._xy(px, py)
        for i, (cx, cy) in enumerate(self.points):
            if math.hypot(cx - x, cy - y) < self.HIT and 0 < i < len(self.points) - 1:
                self.points.pop(i)
                self.queue_draw()
                self.emit("curve-changed", list(self.points))
                return

    def _on_drag(self, gesture, dx, dy):
        if self._drag is None:
            return
        ok, sx, sy = gesture.get_start_point()
        if not ok:
            return
        x, y = self._xy(sx + dx, sy + dy)
        i = self._drag
        # endpoints stay pinned to their edge, interior points cannot
        # cross their neighbours (which would make the curve ambiguous)
        if i == 0:
            x = 0.0
        elif i == len(self.points) - 1:
            x = 1.0
        else:
            lo = self.points[i - 1][0] + 0.01
            hi = self.points[i + 1][0] - 0.01
            x = min(max(x, lo), hi)
        self.points[i] = (x, y)
        self.queue_draw()
        self.emit("curve-changed", list(self.points))

    def _on_drag_end(self, *_):
        self._drag = None
        self.emit("curve-changed", list(self.points))

    def reset(self):
        self.points = [(0.0, 0.0), (1.0, 1.0)]
        self.queue_draw()
        self.emit("curve-changed", list(self.points))

    def _draw(self, area, cr, w, h):
        cr.set_source_rgba(0, 0, 0, 0.20)
        cr.rectangle(0, 0, w, h)
        cr.fill()

        if self.histogram is not None:
            hist = self.histogram
            m = float(hist.max()) or 1.0
            cr.set_source_rgba(1, 1, 1, 0.16)
            cr.move_to(0, h)
            for i, v in enumerate(hist):
                cr.line_to(i / max(len(hist) - 1, 1) * w, h - (v / m) * h * 0.92)
            cr.line_to(w, h)
            cr.close_path()
            cr.fill()

        cr.set_source_rgba(1, 1, 1, 0.10)
        cr.set_line_width(1)
        for i in range(1, 4):
            cr.move_to(w * i / 4, 0); cr.line_to(w * i / 4, h)
            cr.move_to(0, h * i / 4); cr.line_to(w, h * i / 4)
        cr.stroke()

        try:
            from ..engine import ops
            lut = ops.spline_lut(self.points, 128)
        except Exception:
            lut = np.linspace(0, 1, 128, dtype=np.float32)
        # Black on the light panel, like the rest of the interface: the app
        # has no colour accent.
        cr.set_source_rgb(0.114, 0.114, 0.122)
        cr.set_line_width(2)
        for i, v in enumerate(lut):
            x = i / (len(lut) - 1) * w
            y = h - float(v) * h
            cr.line_to(x, y) if i else cr.move_to(x, y)
        cr.stroke()

        for (x, y) in self.points:
            cr.set_source_rgb(1, 1, 1)
            cr.arc(x * w, h - y * h, 5, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgb(0.114, 0.114, 0.122)
            cr.arc(x * w, h - y * h, 3, 0, 2 * math.pi)
            cr.fill()


class Histogram(Gtk.DrawingArea):
    """RGB histogram readout."""

    def __init__(self):
        super().__init__()
        self.set_content_height(72)
        self.data = None
        self.set_draw_func(self._draw)

    def set_data(self, hist: dict | None):
        self.data = hist
        self.queue_draw()

    def _draw(self, area, cr, w, h):
        cr.set_source_rgba(0, 0, 0, 0.22)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        if not self.data:
            return
        m = max(float(v.max()) for v in self.data.values()) or 1.0
        # additive blending so overlapping channels read as white, the way
        # every other histogram does
        cr.set_operator(1)
        for ch, colour in (("r", (1, .25, .25)), ("g", (.25, 1, .35)),
                           ("b", (.35, .5, 1))):
            vals = self.data.get(ch)
            if vals is None:
                continue
            cr.set_source_rgba(*colour, 0.42)
            cr.move_to(0, h)
            for i, v in enumerate(vals):
                cr.line_to(i / max(len(vals) - 1, 1) * w, h - (v / m) * h * 0.95)
            cr.line_to(w, h)
            cr.close_path()
            cr.fill()
