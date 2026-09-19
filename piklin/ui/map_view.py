"""Every geotagged photo on one map.

The library's photos carry the coordinates their camera recorded, and
this view is the way in through them: a map first, the photos on it,
rather than a photo first and its place afterwards.

Photos that were taken close together are drawn as one pin, so a city
holiday is a single pin rather than four hundred overlapping ones. The
pins regroup as the map is zoomed: what is one pin over a country
becomes a pin per town, then a pin per street. A pin that stands for
several places says how many in a number beside it.

Resting the pointer on a pin slides its albums in at the side, over the
map rather than instead of it: the map stays where it was, so the next
pin is one movement away. Clicking the pin - or an album in the panel -
opens the album, and the window offers a way back to this map, which is
left exactly as it was.

The button at the top left counts the countries the pins are in, and
opens the list of them with the cities in each.

Nothing about a photo leaves the computer. The map's tiles come from
whichever source ``TILE_URL`` names, and that source sees the part of
the world being looked at - never the photos, and never their
coordinates. A source serving from disk makes the view work with no
network at all.
"""
from __future__ import annotations

import math
import threading
from concurrent.futures import ThreadPoolExecutor

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Shumate", "1.0")
from gi.repository import (Adw, GLib, GObject, Gdk, Graphene,  # noqa: E402
                           Gsk, Gtk, Shumate)

from ..i18n import _, ngettext
from ..places import Places
from ..thumbs import GRID_SIZE
from .tile import PhotoTile

# The tiles the map is drawn with. OpenStreetMap's own servers stand in
# while this is being tried out; their usage policy does not allow a
# released app to point at them, so shipping this means a source of our
# own - which is also what lets the map work with no network at all.
TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
TILE_LICENSE = "© OpenStreetMap contributors"
TILE_LICENSE_URI = "https://www.openstreetmap.org/copyright"

# The pin itself: a round head over a point, the shape a map pin has.
# Small on purpose - the pleasure of this view is seeing everywhere you
# have been at once, and big pins would swallow the map showing it.
PIN = 22
# The point below the head, which touches the exact spot on the map.
PIN_TAIL = 10
# How far apart two photos must be, in pixels on screen, to be drawn as
# two pins rather than one. Roughly a pin's width, so pins do not touch.
CLUSTER_PX = 34
# Photos taken within this many metres of each other are folded into one
# spot as the library is read, and a spot is what a pin means: somewhere a
# person went, not every step taken there. It is also what makes "two
# pins in this city" a fact about places rather than about how many
# photos were taken, and a large library becomes far less to sift through
# on every move.
SNAP_M = 250.0
# The albums of the pin under the pointer, at the side of the map.
PANEL_W = 288
ALBUM_THUMB = 52
# Width of the number beside a pin that stands for several places.
BADGE_W = 28


def _rgba(spec: str) -> Gdk.RGBA:
    """A colour from "#rrggbb" or "rgba(...)".

    ``Gdk.RGBA(red=..., green=...)`` silently ignores its arguments on
    current PyGObject and hands back a transparent colour, so colours
    are parsed rather than constructed.
    """
    colour = Gdk.RGBA()
    colour.parse(spec)
    return colour


class MapPin(Gtk.Widget):
    """The red pin that marks a place on the map.

    Drawn rather than built out of widgets because the shape is one
    piece: the point has to meet the head without a seam, and it is the
    point, not the middle, that marks the spot.
    """
    __gtype_name__ = "PikaMapPin"

    RED = _rgba("#e53935")
    EDGE = _rgba("#ffffff")
    SHADOW = _rgba("rgba(0,0,0,0.34)")

    def do_measure(self, orientation, _for_size):
        size = PIN if orientation == Gtk.Orientation.HORIZONTAL else PIN + PIN_TAIL
        return (size, size, -1, -1)

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        if width <= 0 or height <= 0:
            return
        head = min(width, height - PIN_TAIL)

        # The point first, so the head is drawn over its blunt end and
        # the two read as one shape. A square turned 45° is its own
        # arrowhead, and needs no path.
        side = PIN_TAIL * 1.45
        for colour, grow in ((self.EDGE, 1.5), (self.RED, 0.0)):
            snapshot.save()
            snapshot.translate(Graphene.Point().init(width / 2.0, head - side / 2.0))
            snapshot.rotate(45.0)
            half = side / 2.0 + grow
            snapshot.append_color(
                colour, Graphene.Rect().init(-half, -half, half * 2, half * 2))
            snapshot.restore()

        circle = Graphene.Rect().init(0, 0, head, head)
        ring = Gsk.RoundedRect()
        ring.init_from_rect(circle, head / 2.0)
        snapshot.append_outset_shadow(ring, self.SHADOW, 0.0, 1.0, 0.0, 4.0)
        # a white rim, so the pin holds its shape over a dark photo or a
        # pale sea alike
        snapshot.push_rounded_clip(ring)
        snapshot.append_color(self.EDGE, circle)
        snapshot.pop()

        inset = 1.5
        body = Graphene.Rect().init(inset, inset, head - inset * 2, head - inset * 2)
        body_clip = Gsk.RoundedRect()
        body_clip.init_from_rect(body, (head - inset * 2) / 2.0)
        snapshot.push_rounded_clip(body_clip)
        snapshot.append_color(self.RED, body)
        snapshot.pop()


class MapView(Gtk.Box):
    """The map, its pins, and the photos behind them."""

    __gsignals__ = {
        # an album was chosen, from a pin or from the side panel
        "open-album": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        # photos that belong to no album were chosen: their ids
        "open-place": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self, catalog, thumbs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.catalog = catalog
        self.thumbs = thumbs
        # The world's towns, read from disk the first time a pin is asked
        # what it is called.
        self.places = Places()
        # Two threads of its own for reading thumbnails that already
        # exist, so the map never queues behind a scan making new ones.
        self._readers = ThreadPoolExecutor(max_workers=2,
                                           thread_name_prefix="pika-map")
        # each spot: (latitude, longitude, [(photo id, path), ...])
        self._spots: list[tuple[float, float, list[tuple[int, str]]]] = []
        self._generation = 0
        self._recluster_id = 0
        # The panel's photos outlive a recluster, so they count their own
        # generation rather than the pins'.
        self._panel_members = None
        self._panel_gen = 0
        self._close_id = 0
        # A pin that was clicked keeps its panel open until it is closed,
        # so an album can be chosen without keeping the pointer on the pin.
        # Whether the map has been framed round the photos yet. After that
        # it stays where the person left it, whatever reloads it.
        self._framed = False
        self._stats_gen = 0

        # A tile server refuses, or throttles, a client that does not say
        # who it is.
        from ..app import VERSION
        Shumate.set_user_agent(f"Piklin/{VERSION} (+https://vezzu.studio)")

        self.map = Shumate.SimpleMap()
        source = Shumate.RasterRenderer.new_from_url(TILE_URL)
        source.set_license(TILE_LICENSE)
        source.set_license_uri(TILE_LICENSE_URI)
        self.map.set_map_source(source)
        # Shumate's own zoom buttons sit at the top right, square and
        # welded there; Piklin draws its own, round, out of the way of
        # the panel that slides in over that corner.
        self.map.set_show_zoom_buttons(False)
        self.map.set_vexpand(True)
        self.map.set_hexpand(True)

        self.markers = Shumate.MarkerLayer.new(self.map.get_viewport())
        self.map.add_overlay_layer(self.markers)

        # OpenStreetMap's tiles stop at 18, and the viewport would stop
        # there with them - leaving photos a few doors apart stuck under
        # one pin. Past 18 the tiles are stretched rather than sharper,
        # but the pins go on separating, which is what the last step of
        # zooming is for.
        self.map.get_viewport().set_max_zoom_level(20)
        # Pulling back further than the whole world leaves it a small
        # square adrift in grey, with the same countries repeated either
        # side of it, so the world filling the view is as far out as the
        # map goes.
        self.map.connect("notify::width", lambda *_a: self._limit_zoom_out())
        self.map.connect("notify::height", lambda *_a: self._limit_zoom_out())
        self._limit_zoom_out()

        # Panning and zooming both change which photos fall together.
        vp = self.map.get_viewport()
        for prop in ("zoom-level", "latitude", "longitude"):
            vp.connect(f"notify::{prop}", lambda *_a: self._schedule_recluster())

        # Shown instead of the map when no photo has a place.
        self.empty = Adw.StatusPage(
            icon_name="mark-location-symbolic",
            title=_("No photos with a place"),
            description=_("Photos that recorded where they were taken appear "
                          "on the map. Most cameras and phones record it."),
            vexpand=True)

        # The panel lies over the map, so opening it never moves the map
        # under the pointer.
        self.overlay = Gtk.Overlay()
        self.overlay.set_child(self.map)
        self.overlay.add_overlay(self._build_zoom())
        self.overlay.add_overlay(self._build_panel())
        self.overlay.add_overlay(self._build_stats())

        # A click on the bare map puts a clicked pin's panel away.
        away = Gtk.GestureClick()
        away.connect("released", lambda *_a: self._release_panel())
        self.map.get_map().add_controller(away)

        self.stack = Gtk.Stack()
        self.stack.add_named(self.overlay, "map")
        self.stack.add_named(self.empty, "empty")
        self.append(self.stack)

    # ------------------------------------------------------------------
    def _build_zoom(self) -> Gtk.Widget:
        """Closer and further, at the corner furthest from the photos."""
        inner = self.map.get_map()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      halign=Gtk.Align.END, valign=Gtk.Align.END,
                      margin_end=16, margin_bottom=16)
        for icon, tip, act in (
                ("list-add-symbolic", _("Zoom in"), inner.zoom_in),
                ("list-remove-symbolic", _("Zoom out"), inner.zoom_out)):
            button = Gtk.Button(icon_name=icon, tooltip_text=tip)
            button.add_css_class("pika-map-zoom")
            button.connect("clicked", lambda _b, a=act: a())
            box.append(button)
        return box

    def _build_panel(self) -> Gtk.Widget:
        """The albums of the pin under the pointer, at the right edge."""
        self.panel_title = Gtk.Label(xalign=0.0, ellipsize=3, hexpand=True)
        self.panel_title.add_css_class("pika-map-panel-title")
        # The place on one line, how much is there on the next: the name
        # is what you came for, the count is only context.
        self.panel_count = Gtk.Label(xalign=0.0, ellipsize=3)
        self.panel_count.add_css_class("pika-map-panel-count")
        heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1,
                          hexpand=True)
        heading.append(self.panel_title)
        heading.append(self.panel_count)

        close = Gtk.Button(icon_name="window-close-symbolic",
                           valign=Gtk.Align.START,
                           tooltip_text=_("Close"))
        close.add_css_class("flat")
        close.add_css_class("circular")
        close.connect("clicked", lambda _b: self._release_panel(force=True))
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        top.append(heading)
        top.append(close)

        self.panel_albums = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.panel_albums.add_css_class("pika-map-albums")
        self.panel_albums.connect("row-activated", self._on_album_row)
        self.panel_albums.set_valign(Gtk.Align.START)

        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        # Without this the panel grows to whatever its albums need and
        # runs off the bottom of the map instead of scrolling.
        scroller.set_propagate_natural_height(False)
        scroller.set_min_content_height(80)
        scroller.set_child(self.panel_albums)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      margin_top=12, margin_bottom=12,
                      margin_start=12, margin_end=12)
        box.append(top)
        box.append(scroller)

        self.panel = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT,
            transition_duration=140,
            halign=Gtk.Align.END, valign=Gtk.Align.FILL,
            margin_top=12, margin_bottom=12, margin_end=12)
        frame = Gtk.Frame()
        frame.add_css_class("pika-map-panel")
        frame.set_size_request(PANEL_W, -1)
        frame.set_child(box)
        self.panel.set_child(frame)

        # The pointer resting anywhere on the panel keeps it open.
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_a: self._hold_panel())
        self.panel.add_controller(motion)
        return self.panel

    def _build_stats(self) -> Gtk.Widget:
        """How many countries the pins are in, and a list of them."""
        self.stats_button = Gtk.MenuButton(
            halign=Gtk.Align.START, valign=Gtk.Align.START,
            margin_start=16, margin_top=16, visible=False)
        self.stats_button.add_css_class("pika-map-stats")
        self.stats_label = Gtk.Label()
        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        content.append(Gtk.Image(icon_name="mark-location-symbolic"))
        content.append(self.stats_label)
        self.stats_button.set_child(content)

        self.stats_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.stats_list.add_css_class("boxed-list")
        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, min_content_width=320,
            max_content_height=420, propagate_natural_height=True)
        scroller.set_child(self.stats_list)
        self.stats_popover = Gtk.Popover()
        self.stats_popover.set_child(scroller)
        self.stats_button.set_popover(self.stats_popover)
        return self.stats_button

    # ------------------------------------------------------------------
    def load(self, keep_view: bool = True) -> None:
        """Read every geotagged photo and draw the map around them.

        The map is framed round the photos the first time it has any, and
        after that stays wherever it was left: a reload for a scan, a
        backup, an album opened and closed again or the sidebar being
        rebuilt should not drag it off somewhere else. ``keep_view=False``
        frames it again, for when that is what is wanted.
        """
        self._generation += 1
        rows = self.catalog.q(
            "SELECT p.id, p.path, p.gps_lat, p.gps_lon, "
            # Which albums each photo belongs to, gathered here so a pin
            # can say how many albums it holds without asking again for
            # every pin on every move of the map.
            " (SELECT GROUP_CONCAT(ai.album_id) FROM album_items ai "
            "   WHERE ai.photo_id = p.id) AS albums "
            "FROM photos p "
            "WHERE p.gps_lat IS NOT NULL AND p.gps_lon IS NOT NULL "
            # A camera that found no satellites writes exactly 0, 0 - a
            # spot in the Atlantic. Those are not places anyone visited.
            "AND NOT (p.gps_lat = 0 AND p.gps_lon = 0) "
            "AND p.trashed_at IS NULL AND p.hidden=0 AND p.paired_to IS NULL")
        self._spots = self._to_spots(rows)
        if not self._spots:
            self._framed = False
            self.stats_button.set_visible(False)
            self.stack.set_visible_child_name("empty")
            return
        self.stack.set_visible_child_name("map")
        if not keep_view or not self._framed:
            self._frame_all()
            self._framed = True
        self._recluster()
        self._count_places()

    @staticmethod
    def _to_spots(rows) -> list[tuple[float, float, list[tuple[int, str]]]]:
        """Fold photos taken within a few metres of each other into one
        spot, once, when the library is read.

        No zoom can tell two photos eight metres apart from one another,
        so from here on the map works in spots rather than photos. On a
        large library that is an order of magnitude less to sift through
        every time the map moves, and a spot is closer to what a pin
        means anyway: somewhere a person stood.
        """
        step = SNAP_M / 111320.0
        spots: dict[tuple[int, int], list] = {}
        for r in rows:
            lat, lon = r["gps_lat"], r["gps_lon"]
            key = (round(lat / step), round(lon / step))
            albums = {int(a) for a in (r["albums"] or "").split(",") if a}
            spot = spots.get(key)
            if spot is None:
                spots[key] = [lat, lon, [(r["id"], r["path"])], albums]
            else:
                spot[2].append((r["id"], r["path"]))
                spot[3] |= albums
        return [(s[0], s[1], s[2], s[3]) for s in spots.values()]

    def _frame_all(self) -> None:
        """Open on the whole library: centred on the photos, zoomed out
        far enough that the furthest apart are both on screen."""
        lats = [s[0] for s in self._spots]
        lons = [s[1] for s in self._spots]
        vp = self.map.get_viewport()
        vp.set_location((min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2)
        span = max(max(lats) - min(lats), max(lons) - min(lons))
        # 360° of longitude is zoom 0 and each zoom level halves it.
        for level, degrees in ((2, 90), (4, 22), (6, 5), (8, 1.4), (10, 0.35), (12, 0.08)):
            if span > degrees:
                vp.set_zoom_level(level)
                return
        vp.set_zoom_level(14)

    # ------------------------------------------------------------------
    def _schedule_recluster(self) -> None:
        """Regroup once the map settles, not on every frame of a drag."""
        if self._recluster_id:
            GLib.source_remove(self._recluster_id)

        def run():
            self._recluster_id = 0
            self._recluster()
            return False
        self._recluster_id = GLib.timeout_add(150, run)

    def _limit_zoom_out(self) -> None:
        """Stop zooming out once the whole world is on screen.

        The world is a 256 * 2^zoom pixel square, so the height of the
        view is what decides whether all of it fits - and fitting all of
        it is the point, since a pin can be anywhere, Antarctica
        included. Pulling back further only shrinks the world into the
        middle of an empty grey field.

        A window wider than it is tall then has room to spare at the
        sides, which the map fills by repeating itself, the way every
        map on this projection does. That is the accepted cost of seeing
        everywhere at once.
        """
        width, height = self.map.get_width(), self.map.get_height()
        if width <= 0 or height <= 0:
            return
        # Far enough out that the world is narrower than the view, the map
        # repeats itself sideways - and libshumate stops drawing markers
        # on the repeats, so the pins vanish and the map is useless. The
        # world must stay at least as wide as the view, which also settles
        # the repetition. The cost is that the poles fall outside; nobody
        # keeps photographs there.
        floor = max(0, math.ceil(math.log2(width / 256.0)))
        self.map.get_viewport().set_min_zoom_level(floor)

    def _visible(self, viewport) -> list[tuple[float, float, list[tuple[int, str]]]]:
        """The photos on screen, plus a margin either side.

        A library of a hundred thousand photos would otherwise become a
        hundred thousand pins the moment the map was zoomed in far enough
        to tell them apart - every one of them a widget, nearly all of
        them off screen. Pins should cost what the screen shows, not what
        the library holds.
        """
        width, height = self.map.get_width(), self.map.get_height()
        if width <= 0 or height <= 0:
            return self._spots
        north, west = viewport.widget_coords_to_location(self.map, 0, 0)
        south, east = viewport.widget_coords_to_location(self.map, width, height)
        if north < south:
            north, south = south, north
        # Half a screen of margin, so panning has pins ready before they
        # are needed.
        pad_lat = (north - south) * 0.5
        pad_lon = (east - west) * 0.5
        north, south = north + pad_lat, south - pad_lat
        west, east = west - pad_lon, east + pad_lon
        # Zoomed out far enough to see round the back of the world, the
        # longitudes stop being a range at all; then nothing is off screen.
        whole_world = east - west >= 360.0 or east < west
        return [s for s in self._spots
                if south <= s[0] <= north
                and (whole_world or west <= s[1] <= east)]

    def _recluster(self) -> None:
        """Draw one pin per place, for the places this zoom can tell apart.

        Photos are gathered by how close together they actually are, not
        by which square of a grid they fall in: a grid would cut a single
        visit in two whenever it happened to straddle a line, and the
        point of a pin is that it lands on the place someone went.
        """
        if not self._spots:
            return
        vp = self.map.get_viewport()
        # The map is square in Mercator: at zoom z the world is
        # 256 * 2^z pixels across, so this is degrees of longitude per
        # pixel, which is what the gathering radius is expressed in.
        per_px = 360.0 / (256.0 * (2.0 ** vp.get_zoom_level()))
        radius = CLUSTER_PX * per_px

        spots = self._visible(vp)
        # Only spots in the neighbouring squares can be within the
        # radius, so each spot is compared with a handful rather than
        # with all of them.
        buckets: dict[tuple[int, int], list[int]] = {}
        for i, s in enumerate(spots):
            buckets.setdefault((int(s[0] / radius), int(s[1] / radius)), []).append(i)

        taken = [False] * len(spots)
        groups = []
        for i, seed in enumerate(spots):
            if taken[i]:
                continue
            taken[i] = True
            group = [seed]
            # A degree of longitude is shorter the further from the
            # equator; without this a "100 m" radius would be far wider
            # than 100 m in Scandinavia.
            squeeze = math.cos(math.radians(seed[0])) or 1.0
            ci, cj = int(seed[0] / radius), int(seed[1] / radius)
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    for j in buckets.get((ci + di, cj + dj), ()):
                        if taken[j]:
                            continue
                        other = spots[j]
                        dy = other[0] - seed[0]
                        dx = (other[1] - seed[1]) * squeeze
                        if dx * dx + dy * dy <= radius * radius:
                            taken[j] = True
                            group.append(other)
            groups.append(group)

        self.markers.remove_all()
        self._generation += 1
        for group in groups:
            self.markers.add_marker(self._pin(group, self._generation))

    def _pin(self, group, gen: int) -> Shumate.Marker:
        """One pin for one place: where it sits, and how much is there.

        The pin is drawn where the photographs were, weighted by how many
        were taken at each spot, so a pin over a beach sits on the part of
        it somebody actually stood on. When it stands for several places
        - zoomed out far enough that they run together - a number beside
        it says how many.
        """
        photos = sum((s[2] for s in group), [])
        total = len(photos)
        lat = sum(s[0] * len(s[2]) for s in group) / total
        lon = sum(s[1] * len(s[2]) for s in group) / total
        marker = Shumate.Marker()
        marker.set_location(lat, lon)

        coords = [(s[0], s[1]) for s in group]
        # What the badge counts is albums, because albums are what opens
        # when the pin is used - and it is shown even when there is only
        # one, so every pin says how much is behind it.
        albums = set()
        for s in group:
            albums |= s[3]
        count = len(albums) or len(group)
        # The pin sits in the middle whatever is beside it, so the spot it
        # points at is the spot on the map: an empty space the width of
        # the badge on the other side keeps it centred on the marker.
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        gap = Gtk.Box()
        gap.set_size_request(BADGE_W, 1)
        gap.set_can_target(False)
        row.append(gap)
        row.append(MapPin())
        badge = Gtk.Label(label=str(count) if count < 100 else "99+")
        badge.add_css_class("pika-map-badge")
        badge.set_size_request(BADGE_W, -1)
        badge.set_halign(Gtk.Align.START)
        badge.set_valign(Gtk.Align.START)
        badge.set_margin_top(2)
        row.append(badge)
        row.set_tooltip_text(
            ngettext("{count} album", "{count} albums", count).format(count=count)
            if albums else
            ngettext("{count} place", "{count} places", count).format(count=count))
        # A marker sits centred on its coordinate; lifting the pin by its
        # own height puts the point, not the middle, on the spot.
        row.set_margin_bottom(PIN + PIN_TAIL)
        marker.set_child(row)

        # Resting on a pin shows its albums; clicking it opens them. The
        # controllers go on the widget that is actually drawn: a marker
        # only places its child, and the pointer never reaches the marker
        # itself.
        row.set_can_target(True)
        row.set_cursor_from_name("pointer")
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_a: self._show_place(photos, coords))
        row.add_controller(motion)
        click = Gtk.GestureClick()

        def clicked(gesture, *_a):
            # The bare map's own click would put the panel away again.
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self._click_place(photos, coords)
        click.connect("released", clicked)
        row.add_controller(click)
        return marker

    # ------------------------------------------------------------------
    def _albums_at(self, photos) -> tuple[list[dict], list[int]]:
        """The albums holding photos taken at a pin, and the photos of it
        that are in none.

        Each album is shown by a photo of it taken *here* rather than by
        its own cover, since what identifies the album on this pin is
        what was taken at this pin.
        """
        ids = [p[0] for p in photos]
        path_of = dict(photos)
        found: dict[int, dict] = {}
        filed: set[int] = set()
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            rows = self.catalog.q(
                "SELECT ai.album_id, ai.photo_id, a.name FROM album_items ai "
                "JOIN albums a ON a.id=ai.album_id "
                f"WHERE ai.photo_id IN ({','.join('?' * len(part))}) "
                "ORDER BY ai.position", part)
            for r in rows:
                entry = found.setdefault(
                    r["album_id"], {"id": r["album_id"], "name": r["name"],
                                    "here": 0, "cover": path_of[r["photo_id"]]})
                entry["here"] += 1
                filed.add(r["photo_id"])
        albums = sorted(found.values(),
                        key=lambda e: (-e["here"], e["name"].casefold()))
        return albums, [i for i in ids if i not in filed]

    def _click_place(self, photos, coords) -> None:
        """Clicking a pin shows what is there; it never opens it.

        Going straight into the album when a pin held only one took the
        map away without being asked, and there was no telling beforehand
        which pins would do it. A pin always answers the same way - here
        is what is here - and the choosing is done in the panel.
        """
        self._show_place(photos, coords)

    def _on_album_row(self, _list, row) -> None:
        target = getattr(row, "_target", None)
        if target is None:
            return
        self._release_panel(force=True)
        kind, value = target
        self.emit("open-album" if kind == "album" else "open-place", value)

    def _show_place(self, photos, coords) -> None:
        """Fill the side panel with one pin's albums and slide it in.

        The panel stays until it is dismissed: resting on a pin is how
        it is asked for, but reaching the albums in it means crossing the
        map, and a panel that fled on the way was no use for choosing
        anything. Another pin replaces what is in it; the close button
        and a click on the map put it away.
        """
        self._hold_panel()
        if photos is self._panel_members:
            return
        self._panel_members = photos
        self._panel_gen += 1
        gen = self._panel_gen

        # Named only when a pin is actually asked about, so a map full of
        # them costs nothing to draw.
        where = self.places.describe(coords)
        self.panel_title.set_text(where or _("On the map"))
        self.panel_count.set_text(
            ngettext("{count} photo", "{count} photos", len(photos))
            .format(count=f"{len(photos):,}"))

        while (row := self.panel_albums.get_first_child()) is not None:
            self.panel_albums.remove(row)
        albums, loose = self._albums_at(photos)
        for album in albums:
            self._album_row(
                gen, album["cover"], album["name"],
                ngettext("{count} photo here", "{count} photos here", album["here"])
                .format(count=f"{album['here']:,}"), ("album", album["id"]))
        if loose:
            path_of = dict(photos)
            self._album_row(
                gen, path_of[loose[0]], _("Not in an album"),
                ngettext("{count} photo", "{count} photos", len(loose))
                .format(count=f"{len(loose):,}"), ("photos", loose))
        self.panel.set_reveal_child(True)

    def _album_row(self, gen: int, cover_path: str, name: str, detail: str,
                   target) -> None:
        tile = PhotoTile(size=ALBUM_THUMB, radius=8.0)
        title = Gtk.Label(label=name, xalign=0.0, ellipsize=3)
        title.add_css_class("pika-map-album-name")
        sub = Gtk.Label(label=detail, xalign=0.0, ellipsize=3)
        sub.add_css_class("pika-map-panel-count")
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1,
                       valign=Gtk.Align.CENTER, hexpand=True)
        text.append(title)
        text.append(sub)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10,
                      margin_top=6, margin_bottom=6,
                      margin_start=6, margin_end=8)
        box.append(tile)
        box.append(text)
        row = Gtk.ListBoxRow(activatable=True)
        row.set_child(box)
        row._target = target
        self.panel_albums.append(row)
        self._load_thumb(tile, cover_path, gen, size=ALBUM_THUMB, panel=True)

    def _hold_panel(self) -> None:
        if self._close_id:
            GLib.source_remove(self._close_id)
            self._close_id = 0

    def _release_panel(self, force: bool = False) -> None:
        """Put the panel away: its close button, or a click on the map."""
        self._hold_panel()
        self.panel.set_reveal_child(False)
        self._panel_members = None

    # ------------------------------------------------------------------
    def _count_places(self) -> None:
        """Work out which countries and cities the pins are in.

        Off the UI thread: naming thousands of spots against a list of
        thirty-four thousand towns is not something to do while the map
        is being drawn.
        """
        self._stats_gen += 1
        gen = self._stats_gen
        spots = self._spots

        def work():
            names: dict[tuple[float, float], tuple[str, str]] = {}
            tally: dict[str, dict[str, list]] = {}
            for lat, lon, photos, _albums in spots:
                # A couple of kilometres: spots that near are one place to
                # the list, and the answer is worth keeping.
                key = (round(lat, 2), round(lon, 2))
                if key not in names:
                    names[key] = self.places.locate(lat, lon)
                country, city = names[key]
                if not country:
                    continue
                entry = tally.setdefault(country, {}).setdefault(
                    city or "", [0, 0.0, 0.0])
                n = len(photos)
                entry[0] += n
                entry[1] += lat * n
                entry[2] += lon * n
            GLib.idle_add(self._show_stats, gen, tally)
        threading.Thread(target=work, daemon=True, name="pika-map-count").start()

    def _show_stats(self, gen: int, tally) -> bool:
        if gen != self._stats_gen:
            return False
        countries = len(tally)
        # Every place counts, named or not. Counting only the ones the
        # town list could name gave fewer cities than countries, which
        # cannot be true and read as a mistake.
        places = sum(len(c) for c in tally.values())
        if not countries:
            self.stats_button.set_visible(False)
            return False
        self.stats_label.set_text(
            ngettext("{count} country", "{count} countries", countries)
            .format(count=countries) + " · " +
            ngettext("{count} place", "{count} places", places)
            .format(count=places))
        self.stats_button.set_visible(True)

        while (row := self.stats_list.get_first_child()) is not None:
            self.stats_list.remove(row)
        ranked = sorted(tally.items(),
                        key=lambda kv: (-sum(v[0] for v in kv[1].values()), kv[0]))
        for country, towns in ranked:
            photos = sum(v[0] for v in towns.values())
            named = {k: v for k, v in towns.items() if k}
            expander = Adw.ExpanderRow(
                title=GLib.markup_escape_text(country),
                subtitle=GLib.markup_escape_text(" · ".join((
                    ngettext("{count} city", "{count} cities", len(named))
                    .format(count=len(named)),
                    ngettext("{count} photo", "{count} photos", photos)
                    .format(count=f"{photos:,}")))
                    if named else
                    ngettext("{count} photo", "{count} photos", photos)
                    .format(count=f"{photos:,}")))
            for city, (n, sum_lat, sum_lon) in sorted(
                    named.items(), key=lambda kv: (-kv[1][0], kv[0])):
                row = Adw.ActionRow(
                    title=GLib.markup_escape_text(city),
                    subtitle=ngettext("{count} photo", "{count} photos", n)
                    .format(count=f"{n:,}"),
                    activatable=True)
                row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
                row.connect("activated", lambda _r, la=sum_lat / n, lo=sum_lon / n:
                            self._fly_to(la, lo))
                expander.add_row(row)
            self.stats_list.append(expander)
        return False

    def _fly_to(self, lat: float, lon: float) -> None:
        """Bring the map to a city, from the list of where the pins are."""
        self.stats_popover.popdown()
        vp = self.map.get_viewport()
        vp.set_location(lat, lon)
        vp.set_zoom_level(11)

    def _load_thumb(self, tile: PhotoTile, path: str, gen: int,
                    size: int = PIN, panel: bool = False) -> None:
        """Decode off the UI thread, paint on it, and drop the result if
        the pins regrouped - or the panel moved on - while it was read."""
        from .grid import thumb_texture

        def current() -> bool:
            return gen == (self._panel_gen if panel else self._generation)

        def paint(thumb) -> None:
            try:
                texture = thumb_texture(thumb, size)
            except Exception:
                return

            def apply():
                if current():
                    tile.set_paintable(texture)
                return False
            GLib.idle_add(apply)

        # A thumbnail that already exists is only a file to read, and
        # reading it must not wait behind the thousands of thumbnails a
        # first scan is still making: that queue is hours long on a
        # machine with little memory, and the panel would sit empty for
        # photos whose pictures were ready all along.
        ready = self.thumbs.get_path(path, GRID_SIZE)
        if ready is not None:
            self._readers.submit(lambda: current() and paint(ready))
            return

        def done(p):
            if p is not None and current():
                paint(p)
        self.thumbs.request(path, GRID_SIZE, done)
