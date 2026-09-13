"""The app's mark at the top of the sidebar: a camera that now and then
takes a picture - the flash fires, the lens blinks - beside the name
"Piklin" in black, centred above the sidebar.

Drawn with cairo rather than shipped as an image, so it stays sharp at any
scale and follows the same weight as the symbolic icons around it. The
animation lasts well under a second and runs every several seconds; it is
skipped entirely when the desktop has animations turned off.

``draw_camera`` is shared with packaging/make-icons.py, so the app icon is
this same camera, caught as the flash fires.
"""
from __future__ import annotations

import math
import random

import cairo
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

FLASH_SECONDS = 0.75
_INK = (0.114, 0.114, 0.122)


def _lerp(a, b, t):
    return a + (b - a) * t


def _rounded(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def draw_camera(cr, x, y, size, burst=0.0, lit=0.0, blink=0.0):
    """The camera in a ``size`` square at (x, y).

    ``burst`` is the flash glow and rays, ``lit`` the flash window, and
    ``blink`` how far the iris has closed, each 0..1.
    """
    cr.save()
    cr.translate(x, y)
    cr.scale(size / 26.0, size / 26.0)

    # flash burst behind the camera: a soft glow and a few rays
    if burst > 0:
        fx, fy = 7.2, 8.6
        glow = 3 + 13 * burst
        grad = cairo.RadialGradient(fx, fy, 0, fx, fy, glow)
        grad.add_color_stop_rgba(0, 1, 1, 1, 0.95 * burst)
        grad.add_color_stop_rgba(0.35, 0.86, 0.93, 1.0, 0.55 * burst)
        grad.add_color_stop_rgba(1, 0.86, 0.93, 1.0, 0)
        cr.set_source(grad)
        cr.arc(fx, fy, glow, 0, 2 * math.pi)
        cr.fill()
        cr.set_line_width(1.2)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_source_rgba(0.35, 0.55, 0.95, 0.85 * burst)
        for k in range(6):
            a = -math.pi * 0.95 + k * (math.pi * 0.95 / 5)
            r0, r1 = 4.2 + 2 * burst, 4.2 + 6.5 * burst
            cr.move_to(fx + math.cos(a) * r0, fy + math.sin(a) * r0)
            cr.line_to(fx + math.cos(a) * r1, fy + math.sin(a) * r1)
        cr.stroke()

    # body, with the viewfinder hump
    cr.set_source_rgb(*_INK)
    _rounded(cr, 3, 9, 20, 13, 3.2)
    cr.fill()
    _rounded(cr, 10, 6.5, 7, 4, 1.4)
    cr.fill()

    # flash window: lit while the flash fires
    cr.set_source_rgb(_lerp(0.62, 1.0, lit), _lerp(0.62, 1.0, lit),
                      _lerp(0.66, 1.0, lit))
    _rounded(cr, 5.2, 7.2, 4, 2.4, 0.9)
    cr.fill()

    # lens: a white ring, and an iris that blinks shut and opens again
    cr.set_source_rgb(1, 1, 1)
    cr.arc(13, 15.5, 4.6, 0, 2 * math.pi)
    cr.fill()
    cr.set_source_rgb(*_INK)
    cr.arc(13, 15.5, 3.2 * (1 - 0.8 * blink), 0, 2 * math.pi)
    cr.fill()
    # a glint on the lens
    cr.set_source_rgba(1, 1, 1, 0.9 if blink < 0.5 else 0.2)
    cr.arc(11.9, 14.4, 0.9, 0, 2 * math.pi)
    cr.fill()
    cr.restore()


class CameraMark(Gtk.DrawingArea):
    """A camera glyph that fires its flash every few seconds."""

    def __init__(self, size: int = 26):
        super().__init__()
        self.set_content_width(size)
        self.set_content_height(size)
        self.set_valign(Gtk.Align.CENTER)
        self.set_draw_func(self._draw)
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Piklin"])
        self._phase = None          # None when idle, else 0..1 through a flash
        self._start = 0
        self._tick = 0
        self._timer = 0
        self.connect("map", lambda *_: self._schedule(1.6))
        self.connect("unmap", lambda *_: self._stop())

    # -- timing ------------------------------------------------------------
    def _animations_enabled(self) -> bool:
        settings = Gtk.Settings.get_default()
        return bool(settings is None or settings.props.gtk_enable_animations)

    def _schedule(self, seconds=None):
        if self._timer:
            GLib.source_remove(self._timer)
        delay = seconds if seconds is not None else random.uniform(6.0, 9.0)
        self._timer = GLib.timeout_add(int(delay * 1000), self._fire)

    def _stop(self):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        if self._tick:
            self.remove_tick_callback(self._tick)
            self._tick = 0
        self._phase = None

    def _fire(self):
        self._timer = 0
        if self._animations_enabled() and self.get_mapped():
            self._start = 0
            self._phase = 0.0
            self._tick = self.add_tick_callback(self._on_tick)
        self._schedule()
        return GLib.SOURCE_REMOVE

    def _on_tick(self, _widget, clock):
        now = clock.get_frame_time()
        if not self._start:
            self._start = now
        self._phase = (now - self._start) / (FLASH_SECONDS * 1_000_000)
        self.queue_draw()
        if self._phase >= 1.0:
            self._phase = None
            self._tick = 0
            self.queue_draw()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    # -- drawing -----------------------------------------------------------
    def _draw(self, _area, cr, w, h):
        p = self._phase
        burst = lit = blink = 0.0
        if p is not None:
            burst = math.sin(min(1.0, p / 0.55) * math.pi)       # 0 -> 1 -> 0
            lit = math.sin(min(1.0, p / 0.45) * math.pi)
            if 0.08 < p < 0.5:
                blink = math.sin((p - 0.08) / 0.42 * math.pi)
        size = min(w, h)
        draw_camera(cr, (w - size) / 2, (h - size) / 2, size, burst, lit, blink)


class BrandMark(Gtk.Box):
    """Camera mark + the name, centred at the top of the sidebar."""

    def __init__(self, size: int = 34):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=10,
                         valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        self.add_css_class("pika-brand")
        self.camera = CameraMark(size)
        self.append(self.camera)
        self.title = Gtk.Label(label="Piklin", xalign=0.5)
        self.title.add_css_class("pika-app-title")
        self.append(self.title)
