"""Zooming a Shumate map the way Piklin's maps do.

The library's own wheel keeps recentring on the pointer once the zoom cannot
go further, so the map slides away across the world just when somebody has
zoomed right in to place a pin. Its buttons jump a whole level. This gives
the main map and the place picker the same, steadier behaviour: the place
under the pointer stays under it, every step is eased, nothing runs far
ahead of where the map is, and the limits are never passed.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from ..i18n import _

WHEEL_NOTCH = 0.4           # levels for one click of a mouse wheel
WHEEL_SMOOTH = 0.15         # levels per unit of a trackpad's smooth scrolling
# However hard a trackpad coasts, the map is never taken further than this
# from where it is, so there is always something left to recognise.
MAX_AHEAD = 1.5


class SmoothZoom:
    """Wheel, pinch-free and button zoom for one ``Shumate.SimpleMap``."""

    def __init__(self, simple_map):
        self.map = simple_map
        self._pointer = (0.0, 0.0)
        self._goal = None
        self._anim = None
        motion = Gtk.EventControllerMotion()
        motion.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        motion.connect("motion", lambda _c, x, y: setattr(self, "_pointer", (x, y)))
        simple_map.add_controller(motion)
        wheel = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        wheel.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        wheel.connect("scroll", self._on_wheel)
        simple_map.add_controller(wheel)

    def tile(self) -> float:
        """Pixels across one tile of the map being drawn: 256 for ordinary
        tiles, 512 for the clean vector map. Every zoom level is that many
        pixels per world wide, twice over each step in."""
        try:
            return float(self.map.get_viewport().get_reference_map_source().get_tile_size())
        except Exception:
            return 256.0

    def zoom_by(self, delta: float, point=None, duration: int = 350) -> None:
        """Zoom by ``delta`` levels, eased, and never past the limits. Asking
        again while it moves carries on from where it is going. With a
        ``point`` (x, y on the map) the place under it stays under it;
        without, the middle of the map is what is zoomed on."""
        vp = self.map.get_viewport()
        start = vp.get_zoom_level()
        if self._anim is not None and self._goal is not None:
            start_goal = self._goal
        else:
            start_goal = start
        goal = start_goal + delta
        goal = max(start - MAX_AHEAD, min(start + MAX_AHEAD, goal))
        goal = max(vp.get_min_zoom_level(), min(vp.get_max_zoom_level(), goal))
        self._goal = goal
        if abs(goal - start) < 1e-6:
            return
        if self._anim is not None:
            self._anim.pause()
        anchor = None
        if point is not None:
            anchor = (point, vp.widget_coords_to_location(self.map, *point))

        def step(value):
            vp.set_zoom_level(value)
            if anchor is None:
                return
            (x, y), (lat0, lon0) = anchor
            # Latitude is not linear on the screen, so a first correction
            # leaves a little over; a couple more settle it.
            for _ in range(3):
                lat1, lon1 = vp.widget_coords_to_location(self.map, x, y)
                vp.set_location(max(-85.0, min(85.0, vp.get_latitude() + lat0 - lat1)),
                                vp.get_longitude() + lon0 - lon1)
        target = Adw.CallbackAnimationTarget.new(step)
        anim = Adw.TimedAnimation.new(self.map, start, goal, duration, target)
        anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)

        def done(*_a):
            if self._anim is anim:
                self._anim = None
        anim.connect("done", done)
        self._anim = anim
        anim.play()

    def _on_wheel(self, ctl, _dx, dy) -> bool:
        """Zoom by the wheel, keeping the place under the pointer where it is."""
        notch = ctl.get_unit() == Gdk.ScrollUnit.WHEEL
        self.zoom_by(-dy * (WHEEL_NOTCH if notch else WHEEL_SMOOTH), self._pointer, 180)
        return True


def zoom_buttons(zoom: SmoothZoom) -> Gtk.Widget:
    """Closer and further: round buttons at the bottom right of a map."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                  halign=Gtk.Align.END, valign=Gtk.Align.END,
                  margin_end=16, margin_bottom=16)
    for icon, tip, delta in (("list-add-symbolic", _("Zoom in"), 0.5),
                             ("list-remove-symbolic", _("Zoom out"), -0.5)):
        button = Gtk.Button(icon_name=icon, tooltip_text=tip)
        button.add_css_class("pika-map-zoom")
        button.connect("clicked", lambda _b, d=delta: zoom.zoom_by(d))
        box.append(button)
    return box
