"""Saying where an album's photos were taken, by naming the place.

Most photographs carry no coordinates: a camera without a receiver, a
phone with location turned off, or a picture that has been through a
messaging app, which strips the metadata. Their owner still knows where
they were - a birthday at home, a week at the coast - and this is how
that is told to Piklin: open the album's menu, type the country, the
city and, if it is somewhere particular, its name, and pick the answer.
Piklin finds the exact spot itself (see geocode.py); clicking the map is
only there to nudge it.

What is chosen is stored as the photo's own location, marked as set by
hand so that a later scan of the file never overwrites it, and written
to photo-state.json so it survives a backup and restore.
"""
from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Shumate", "1.0")
from gi.repository import Adw, GLib, GObject, Gtk, Shumate  # noqa: E402

from .. import geocode
from ..i18n import _, current_language, ngettext
from ..places import Places
from .map_view import TILE_LICENSE, TILE_LICENSE_URI, TILE_URL, MapPin, PIN, PIN_TAIL


class PlaceDialog(Adw.Dialog):
    """A map to click, and the name of whatever was clicked."""

    __gsignals__ = {
        # the place somebody settled on: latitude, longitude
        "chosen": (GObject.SignalFlags.RUN_FIRST, None, (float, float)),
    }

    def __init__(self, title: str, subtitle: str,
                 start: tuple[float, float] | None = None):
        super().__init__(title=title, content_width=760, content_height=720)
        self.places = Places()
        self._picked: tuple[float, float] | None = None
        self._search_id = 0

        self.map = Shumate.SimpleMap()
        source = Shumate.RasterRenderer.new_from_url(TILE_URL)
        source.set_license(TILE_LICENSE)
        source.set_license_uri(TILE_LICENSE_URI)
        self.map.set_map_source(source)
        self.map.set_show_zoom_buttons(True)
        self.map.set_vexpand(True)

        viewport = self.map.get_viewport()
        viewport.set_max_zoom_level(18)
        if start is not None:
            viewport.set_location(*start)
            viewport.set_zoom_level(12)
        else:
            viewport.set_location(20.0, -20.0)
            viewport.set_zoom_level(2)

        self.markers = Shumate.MarkerLayer.new(viewport)
        self.map.add_overlay_layer(self.markers)

        click = Gtk.GestureClick()
        click.connect("released", self._on_map_clicked)
        self.map.get_map().add_controller(click)

        self.where = Gtk.Label(xalign=0.0, ellipsize=3,
                               label=_("Search for the place, or click the map"))
        self.where.add_css_class("pika-place-where")
        note = Gtk.Label(xalign=0.0, ellipsize=3, label=subtitle)
        note.add_css_class("pika-dim")

        self.save = Gtk.Button(label=_("Place Photos"), sensitive=False)
        self.save.add_css_class("suggested-action")
        self.save.connect("clicked", self._on_save)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda *_a: self.close())

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                          halign=Gtk.Align.END)
        buttons.append(cancel)
        buttons.append(self.save)

        heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                          hexpand=True)
        heading.append(self.where)
        heading.append(note)
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
                      margin_top=12, margin_bottom=12,
                      margin_start=16, margin_end=16)
        bar.append(heading)
        bar.append(buttons)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self._build_search())
        box.append(self.map)
        box.append(bar)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.set_content(box)
        self.set_child(toolbar)

    # ------------------------------------------------------------------
    def _build_search(self) -> Gtk.Widget:
        """Country, city and an optional name, and the answers under them."""
        self.country = Gtk.Entry(placeholder_text=_("Country"), hexpand=True)
        self.city = Gtk.Entry(placeholder_text=_("City"), hexpand=True)
        self.spot = Gtk.Entry(placeholder_text=_("Place (optional)"), hexpand=True)
        for entry in (self.country, self.city, self.spot):
            entry.connect("activate", self._on_search)
        self.find = Gtk.Button(label=_("Search"))
        self.find.add_css_class("suggested-action")
        self.find.connect("clicked", self._on_search)

        fields = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for w in (self.country, self.city, self.spot, self.find):
            fields.append(w)

        self.status = Gtk.Label(xalign=0.0, ellipsize=3)
        self.status.add_css_class("pika-dim")
        self.status.set_visible(False)

        self.results = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.results.add_css_class("boxed-list")
        self.results.connect("row-selected", self._on_result_selected)
        self.results_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, min_content_height=0,
            max_content_height=150, propagate_natural_height=True)
        self.results_scroll.set_child(self.results)
        self.results_scroll.set_visible(False)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      margin_top=12, margin_bottom=12,
                      margin_start=16, margin_end=16)
        box.append(fields)
        box.append(self.status)
        box.append(self.results_scroll)
        return box

    def _on_search(self, *_a) -> None:
        country = self.country.get_text().strip()
        city = self.city.get_text().strip()
        spot = self.spot.get_text().strip()
        if not (country or city or spot):
            return
        self._search_id += 1
        mine = self._search_id
        self.find.set_sensitive(False)
        self.status.set_text(_("Searching…"))
        self.status.set_visible(True)

        def work():
            try:
                found, online = geocode.search(
                    self.places, country, city, spot, current_language() or "en")
            except Exception:
                found, online = [], False
            GLib.idle_add(self._show_results, mine, found, online)
        threading.Thread(target=work, daemon=True, name="pika-geocode").start()

    def _show_results(self, mine: int, found, online: bool) -> bool:
        if mine != self._search_id:
            return False
        self.find.set_sensitive(True)
        while (row := self.results.get_first_child()) is not None:
            self.results.remove(row)
        self._found = found
        if not found:
            self.status.set_text(_("Nothing found. Check the spelling, or click the map."))
            self.results_scroll.set_visible(False)
            return False
        self.status.set_text(
            _("Pick the right one") if online else
            _("No connection - showing towns Piklin knows. Click the map to be exact."))
        for item in found:
            row = Adw.ActionRow(title=GLib.markup_escape_text(item.title),
                                subtitle=GLib.markup_escape_text(item.detail))
            row.set_subtitle_lines(1)
            self.results.append(row)
        self.results_scroll.set_visible(True)
        # The likeliest answer is already on the map; one click confirms
        # it, and a wrong one is a click away from another.
        self.results.select_row(self.results.get_row_at_index(0))
        return False

    def _on_result_selected(self, _list, row) -> None:
        if row is None:
            return
        item = self._found[row.get_index()]
        self._pick(item.lat, item.lon, item.title, 15 if item.online else 12)

    def _pick(self, lat: float, lon: float, name: str | None = None,
              zoom: int | None = None) -> None:
        self._picked = (lat, lon)
        self.markers.remove_all()
        marker = Shumate.Marker()
        marker.set_location(lat, lon)
        pin = MapPin()
        pin.set_margin_bottom(PIN + PIN_TAIL)
        marker.set_child(pin)
        self.markers.add_marker(marker)
        viewport = self.map.get_viewport()
        if zoom is not None:
            viewport.set_location(lat, lon)
            viewport.set_zoom_level(zoom)
        label = self.places.describe([(lat, lon)])
        self.where.set_text(name or label or f"{lat:.5f}, {lon:.5f}")
        self.save.set_sensitive(True)

    def _on_map_clicked(self, gesture, _n, x, y):
        viewport = self.map.get_viewport()
        lat, lon = viewport.widget_coords_to_location(self.map.get_map(), x, y)
        self._pick(lat, lon)

    def _on_save(self, _button):
        if self._picked is None:
            return
        self.emit("chosen", self._picked[0], self._picked[1])
        self.close()


def ask_for_place(parent, title: str, photos: int, on_chosen,
                  start: tuple[float, float] | None = None,
                  replacing: int = 0) -> None:
    """Open the picker for ``photos`` photos and call back with the
    coordinates if a place is settled on. ``replacing`` is how many of
    them already have a place, which this one will take over."""
    subtitle = ngettext(
        "{count} photo will be placed here",
        "{count} photos will be placed here",
        photos).format(count=f"{photos:,}")
    if replacing:
        subtitle += " · " + ngettext(
            "{count} already on the map will move",
            "{count} already on the map will move",
            replacing).format(count=f"{replacing:,}")
    dialog = PlaceDialog(title, subtitle, start)
    dialog.connect("chosen", lambda _d, lat, lon: on_chosen(lat, lon))
    dialog.present(parent)
