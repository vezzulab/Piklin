"""Events: a card for each day or trip Piklin found in the photos (see events.py).

A card is a cover photo, a name, the dates, and how many photos and videos it
holds. A click opens it; a right-click renames it, joins it to the one before
or after, or makes an album of it. Piklin picks the cover and the name: the
person only changes what is wrong.
"""
from __future__ import annotations

import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gdk, Gtk  # noqa: E402

from .. import events
from ..i18n import _, ngettext

CARD_W, CARD_H = 300, 200
BUILD_SLICE = 0.012


def _counts(ev) -> str:
    parts = []
    if ev["photos"]:
        parts.append(ngettext("{n} photo", "{n} photos", ev["photos"]).format(n=f"{ev['photos']:,}"))
    if ev["videos"]:
        parts.append(ngettext("{n} video", "{n} videos", ev["videos"]).format(n=f"{ev['videos']:,}"))
    return " · ".join(parts)


class EventsView(Gtk.ScrolledWindow):
    __gsignals__ = {
        "open-event": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        "rename": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        "join": (GObject.SignalFlags.RUN_FIRST, None, (int, bool)),     # event, with the next one (else the one before)
        "album": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }

    def __init__(self, catalog, thumbs, settings):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, hexpand=True)
        self.add_css_class("pika-summary")
        self.catalog, self.thumbs, self.settings = catalog, thumbs, settings
        self._generation = 0
        self._sig = None
        self._year = None
        self._building = 0

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, margin_top=22, margin_start=24, margin_end=24)
        title = Gtk.Label(label=_("Events"), xalign=0.0, hexpand=True)
        title.add_css_class("pika-section-title")
        title.add_css_class("pika-folder-heading")
        head.append(title)
        self.years = Gtk.DropDown.new_from_strings([_("All Years")])
        self.years.set_valign(Gtk.Align.CENTER)
        self.years.connect("notify::selected", self._on_year)
        head.append(self.years)
        box.append(head)
        self.sub = Gtk.Label(xalign=0.0, margin_start=24, margin_top=2, margin_bottom=10)
        self.sub.add_css_class("pika-dim")
        box.append(self.sub)
        self.holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_bottom=40)
        box.append(self.holder)
        self.empty = Adw.StatusPage(icon_name="x-office-calendar-symbolic", vexpand=True,
                                    title=_("No Events Yet"),
                                    description=_("Piklin finds them by itself from when and where your photos were taken."))
        box.append(self.empty)
        self.set_child(box)
        self._year_labels = [None]

    # -- what is known ----------------------------------------------------------
    def _signature(self):
        return (self.catalog.scalar("SELECT COUNT(*) FROM photos WHERE taken_at IS NOT NULL AND trashed_at IS NULL", (), 0),
                self.catalog.scalar("SELECT COALESCE(MAX(taken_at),0) FROM photos", (), 0),
                self.catalog.scalar("SELECT COUNT(*) FROM photo_places", (), 0),
                self.catalog.scalar("SELECT COUNT(*) FROM events WHERE custom=1", (), 0))

    def load(self, force: bool = False):
        """Find the events again if the photos changed, then draw them."""
        sig = self._signature()
        if not force and sig == self._sig and self._building == 0:
            return
        self._generation += 1
        gen = self._generation

        def work():
            try:
                if force or self._sig is None or sig[:3] != (self._sig or (0, 0, 0, 0))[:3]:
                    events.refresh(self.catalog)
                rows = events.all_events(self.catalog, self._year)
                years = events.years(self.catalog)
            except Exception:
                rows, years = [], []
            GLib.idle_add(self._render, gen, sig, rows, years)
        threading.Thread(target=work, daemon=True, name="pika-events").start()

    refresh = load

    def _on_year(self, dd, _p):
        i = dd.get_selected()
        year = self._year_labels[i] if 0 <= i < len(self._year_labels) else None
        if year != self._year:
            self._year = year
            self._sig = None
            self.load()

    # -- drawing ------------------------------------------------------------------
    def _render(self, gen, sig, rows, years):
        if gen != self._generation:
            return False
        self._sig = sig
        labels = [None] + years
        if labels != self._year_labels:
            self._year_labels = labels
            self.years.handler_block_by_func(self._on_year)
            self.years.set_model(Gtk.StringList.new([_("All Years")] + [str(y) for y in years]))
            self.years.set_selected(labels.index(self._year) if self._year in labels else 0)
            self.years.handler_unblock_by_func(self._on_year)
        while (c := self.holder.get_first_child()) is not None:
            self.holder.remove(c)
        self.empty.set_visible(not rows)
        self.years.set_visible(bool(years))
        total = sum(r["photos"] + r["videos"] for r in rows)
        self.sub.set_text(ngettext("{n} event · {m} photos and videos", "{n} events · {m} photos and videos",
                                   len(rows)).format(n=f"{len(rows):,}", m=f"{total:,}") if rows else "")
        self._building += 1
        self._queue = list(rows)
        self._section = None
        self._flow = None
        GLib.timeout_add(1, self._build, gen)
        return False

    def _build(self, gen):
        if gen != self._generation:
            self._building = max(0, self._building - 1)
            return GLib.SOURCE_REMOVE
        t0 = time.monotonic()
        while self._queue and time.monotonic() - t0 < BUILD_SLICE:
            ev = self._queue.pop(0)
            from datetime import datetime
            year = datetime.fromtimestamp(ev["start_at"]).year
            if self._section != year:
                self._section = year
                h = Gtk.Label(label=str(year), xalign=0.0, margin_start=24, margin_top=22, margin_bottom=8)
                h.add_css_class("heading")
                self.holder.append(h)
                self._flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                         min_children_per_line=1, max_children_per_line=6,
                                         column_spacing=16, row_spacing=18, margin_start=24, margin_end=24,
                                         valign=Gtk.Align.START, halign=Gtk.Align.FILL, hexpand=True)
                self.holder.append(self._flow)
            self._flow.append(self._card(ev))
        if self._queue:
            return GLib.SOURCE_CONTINUE
        self._building = max(0, self._building - 1)
        return GLib.SOURCE_REMOVE

    def _card(self, ev):
        eid = ev["id"]
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.set_size_request(CARD_W, -1)
        card.set_hexpand(True)
        pic = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
        pic.set_size_request(CARD_W, CARD_H)
        pic.set_hexpand(True)
        pic.add_css_class("pika-tile")
        frame = Gtk.Overlay(child=pic)
        frame.add_css_class("pika-event-cover")
        card.append(frame)
        if ev["cover_path"]:
            pic._requested = False
            pic.connect("map", lambda w, path=ev["cover_path"]: self._cover(w, path))
        name = Gtk.Label(label=events.name_of(ev), xalign=0.0, ellipsize=3)
        name.add_css_class("heading")
        card.append(name)
        meta = Gtk.Label(label=events.dates_of(ev), xalign=0.0, ellipsize=3)
        meta.add_css_class("pika-dim")
        card.append(meta)
        counts = Gtk.Label(label=_counts(ev), xalign=0.0)
        counts.add_css_class("pika-dim")
        card.append(counts)
        click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        click.connect("released", lambda *_a, i=eid: self.emit("open-event", i))
        card.add_controller(click)
        menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        menu.connect("pressed", lambda g, n, x, y, w=card, i=eid: self._menu(w, x, y, i))
        card.add_controller(menu)
        card.set_cursor_from_name("pointer")
        return card

    def _cover(self, pic, path):
        if getattr(pic, "_requested", True):
            return
        pic._requested = True
        from .grid import GRID_SIZE, thumb_texture

        def done(thumb):
            if thumb is None:
                return
            try:
                tex = thumb_texture(thumb, CARD_W * max(1, self.get_scale_factor()))
            except Exception:
                return
            GLib.idle_add(lambda: (pic.set_paintable(tex), False)[1])
        self.thumbs.request(path, GRID_SIZE, done, wanted=lambda: pic.get_mapped())

    def _menu(self, widget, x, y, eid):
        pop = Gtk.Popover(has_arrow=False)
        pop.set_parent(widget)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        pop.set_pointing_to(rect)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=4, margin_bottom=4, margin_start=4, margin_end=4)
        has_prev = events.neighbour(self.catalog, eid, False) is not None
        has_next = events.neighbour(self.catalog, eid, True) is not None
        for label, fn, ok in ((_("Rename…"), lambda: self.emit("rename", eid), True),
                              (_("Make an Album"), lambda: self.emit("album", eid), True),
                              (_("Join With the Earlier Event"), lambda: self.emit("join", eid, False), has_prev),
                              (_("Join With the Later Event"), lambda: self.emit("join", eid, True), has_next)):
            b = Gtk.Button(label=label, sensitive=ok, halign=Gtk.Align.FILL)
            b.add_css_class("flat")
            b.get_child().set_halign(Gtk.Align.START)
            b.connect("clicked", lambda _b, f=fn, p=pop: (p.popdown(), f()))
            box.append(b)
        pop.set_child(box)
        pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
        pop.popup()
