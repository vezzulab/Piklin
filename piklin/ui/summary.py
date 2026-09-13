"""Years and Months as summary cards: one card per period of the library.

Years is one card per year with a key photo; Months is one card per month
with a small mosaic of key photos. Clicking a card goes one level deeper -
a year opens Months at that year, a month opens Days at that month -
which is how you move through years of photos without scrolling past
every one of them.
"""
from __future__ import annotations

import threading

import gi
from ..i18n import _, ngettext

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import GLib, GObject, Gdk, Gtk  # noqa: E402

from ..thumbs import GRID_SIZE
from .tile import PhotoTile

YEAR_CARD = (300, 220)
MONTH_CARD = (240, 240)


class SummaryView(Gtk.ScrolledWindow):
    __gsignals__ = {
        # mode ("year" | "month"), group key ("2024" | "2024-06"), first, last
        "open-group": (GObject.SignalFlags.RUN_FIRST, None,
                       (str, str, float, float)),
    }

    def __init__(self, catalog, thumbs):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                         hexpand=True)
        self.add_css_class("pika-summary")
        self.catalog = catalog
        self.thumbs = thumbs
        self.mode = "year"
        self._generation = 0
        self._filled_generation = 0
        self._cards: dict[str, Gtk.Widget] = {}

        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                                homogeneous=True, valign=Gtk.Align.START,
                                column_spacing=18, row_spacing=26,
                                min_children_per_line=1,
                                max_children_per_line=12,
                                margin_top=22, margin_bottom=30,
                                margin_start=24, margin_end=24)
        self.set_child(self.flow)

        self.empty = Gtk.Label(label=_("No Photos"), vexpand=True)
        self.empty.add_css_class("pika-dim")

    # -- data ------------------------------------------------------------
    def load(self, mode: str, filters=None) -> None:
        self.mode = "month" if mode == "month" else "year"
        self._generation += 1
        gen = self._generation
        mode_now, filters = self.mode, set(filters or ())

        def work():
            try:
                groups = self.catalog.summary(
                    mode_now, filters, per_group=1 if mode_now == "year" else 4)
            except Exception:
                groups = []
            GLib.idle_add(self._fill, gen, groups)
        threading.Thread(target=work, daemon=True).start()

    def _fill(self, gen, groups):
        if gen != self._generation:
            return False
        while (child := self.flow.get_first_child()) is not None:
            self.flow.remove(child)
        self._cards = {}
        self._filled_generation = gen
        self.get_vadjustment().set_value(0)
        if not groups:
            self.flow.append(self.empty)
            return False
        for g in groups:
            card = self._make_card(g)
            self.flow.append(card)
            self._cards[g["key"]] = card
        return False

    # -- cards -----------------------------------------------------------
    def _make_card(self, group):
        w, h = YEAR_CARD if self.mode == "year" else MONTH_CARD
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      halign=Gtk.Align.CENTER)
        box.add_css_class("pika-summary-card")
        box.set_cursor_from_name("pointer")
        box.update_property([Gtk.AccessibleProperty.LABEL],
                            [f"{group['label']}, {group['count']} photos"])

        frame = Gtk.Box()
        frame.add_css_class("pika-summary-art")
        frame.set_overflow(Gtk.Overflow.HIDDEN)
        photos = group["photos"]
        if self.mode == "year" or len(photos) < 4:
            tile = PhotoTile(max(w, h))
            tile.set_size_request(w, h)
            frame.append(tile)
            if photos:
                self._thumb(photos[0]["path"], tile)
        else:
            grid = Gtk.Grid(column_spacing=2, row_spacing=2)
            half = (w - 2) // 2
            for i, ph in enumerate(photos[:4]):
                tile = PhotoTile(half)
                grid.attach(tile, i % 2, i // 2, 1, 1)
                self._thumb(ph["path"], tile)
            frame.append(grid)
        box.append(frame)

        title = Gtk.Label(label=group["label"], xalign=0)
        title.add_css_class("pika-summary-title")
        n = group["count"]
        sub = Gtk.Label(label=ngettext("{count} photo", "{count} photos",
                                       n).format(count=f"{n:,}"), xalign=0)
        sub.add_css_class("pika-dim")
        box.append(title)
        box.append(sub)

        click = Gtk.GestureClick()
        click.connect("released", lambda *_a, g=group: self.emit(
            "open-group", self.mode, g["key"], float(g["first"] or 0),
            float(g["last"] or 0)))
        box.add_controller(click)
        return box

    def _thumb(self, path, tile):
        def done(p):
            def apply():
                if p is None:
                    return False
                try:
                    tile.set_paintable(Gdk.Texture.new_from_filename(str(p)))
                except Exception:
                    pass
                return False
            GLib.idle_add(apply)
        self.thumbs.request(path, GRID_SIZE, done)

    # -- navigation ------------------------------------------------------
    def scroll_to_key(self, prefix: str) -> None:
        """Bring the first card whose key starts with ``prefix`` into view
        (a year's months).

        The cards are built on a worker thread and laid out a frame or two
        after they are added, so a card can exist but still sit at y=0 with
        no height. Scrolling then went nowhere - the Months view opened at
        the newest month instead of at the year you clicked. Wait until
        the card has really been placed.
        """
        tries = {"n": 0}
        # The load that brings the months was started just before this call;
        # until it has filled the view, the cards on screen are still the
        # years - "2019" matched the year card, the view scrolled to it, and
        # then the month cards replaced it and put the view back at the top.
        wanted = self._generation

        def attempt():
            tries["n"] += 1
            if tries["n"] > 80:                      # ~4 s: give up quietly
                return GLib.SOURCE_REMOVE
            if self._filled_generation != wanted:
                return GLib.SOURCE_CONTINUE
            key = next((k for k in self._cards if k.startswith(prefix)), None)
            if key is None:
                return GLib.SOURCE_CONTINUE
            card = self._cards[key]
            ok, rect = card.compute_bounds(self.flow)
            if not ok or rect.get_height() <= 0:
                return GLib.SOURCE_CONTINUE
            first = next(iter(self._cards.values()))
            if card is not first and rect.get_y() <= 0:
                return GLib.SOURCE_CONTINUE
            adj = self.get_vadjustment()
            target = max(0.0, rect.get_y() - 12)
            limit = adj.get_upper() - adj.get_page_size()
            # The last year's months sit in the last rows, which can never
            # scroll all the way to the top of the view. Once the layout has
            # settled (a few frames), go as far as the view allows - that is
            # exactly where those months are.
            if limit < target - 1 and tries["n"] < 8:
                return GLib.SOURCE_CONTINUE
            adj.set_value(min(target, max(0.0, limit)))
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(50, attempt)
