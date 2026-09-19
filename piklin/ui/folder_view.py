"""A folder's contents as cards: the albums and folders grouped inside it.

A folder holds albums, not photos. Selecting one shows what it groups -
each album with its cover, name and number of photos, each folder inside
it with its albums - and clicking a card goes into it.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gdk, Gtk  # noqa: E402

from ..i18n import _, ngettext
from ..thumbs import GRID_SIZE
from .tile import PhotoTile

CARD = 220


class FolderView(Gtk.ScrolledWindow):
    __gsignals__ = {
        "open-album": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        "open-smart": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        "open-folder": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        # right-click on empty space: (widget, x, y)
        "background-menu": (GObject.SignalFlags.RUN_FIRST, None, (object, float, float)),
        # right-click on cards: ([(kind, id, name), ...], x, y)
        "cards-menu": (GObject.SignalFlags.RUN_FIRST, None, (object, float, float)),
    }

    def __init__(self, catalog, thumbs):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                         hexpand=True)
        self.add_css_class("pika-summary")
        self.catalog = catalog
        self.thumbs = thumbs
        self.folder_id = None
        self._generation = 0

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.title = Gtk.Label(xalign=0.0, ellipsize=3, margin_end=24, hexpand=True,
                               valign=Gtk.Align.CENTER)
        self.title.add_css_class("pika-section-title")
        self.title.add_css_class("pika-folder-heading")
        # A folder inside another folder: a back arrow beside its name.
        self.back = Gtk.Button(icon_name="go-previous-symbolic", visible=False,
                               valign=Gtk.Align.CENTER, margin_start=16)
        self.back.add_css_class("flat")
        self.back.add_css_class("pika-heading-back")
        self.back.connect("clicked", lambda *_: self._parent is not None
                          and self.emit("open-folder", self._parent))
        self._parent = None
        # the space above sits on the row, so the arrow and the name stay centred together
        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, margin_top=22)
        title_row.append(self.back)
        title_row.append(self.title)
        self.subtitle = Gtk.Label(xalign=0.0, ellipsize=3, margin_top=2,
                                  margin_start=24, margin_end=24)
        self.subtitle.add_css_class("pika-dim")
        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                                homogeneous=True, valign=Gtk.Align.START,
                                column_spacing=18, row_spacing=26,
                                min_children_per_line=1,
                                max_children_per_line=12,
                                margin_top=18, margin_bottom=30,
                                margin_start=24, margin_end=24)
        self.empty = Adw.StatusPage(
            icon_name="folder-symbolic", vexpand=True, visible=False,
            title=_("This Folder Is Empty"),
            description=_("Drag albums onto this folder in the sidebar to group them."))
        for w in (title_row, self.subtitle, self.flow, self.empty):
            box.append(w)
        # Cards can be chosen - dragging a rectangle from empty space, or
        # Ctrl+click - and a right-click offers what to do with them.
        self._cards = []                 # (key, card box); key = (kind, id, name)
        self._selected = set()
        self._band = None
        overlay = Gtk.Overlay()
        overlay.set_child(box)
        self._band_area = Gtk.DrawingArea(can_target=False)
        self._band_area.set_draw_func(self._draw_band)
        overlay.add_overlay(self._band_area)
        self.set_child(overlay)
        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self._band_begin)
        drag.connect("drag-update", self._band_update)
        drag.connect("drag-end", self._band_end)
        overlay.add_controller(drag)
        menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        menu.connect("pressed", self._on_right_click)
        overlay.add_controller(menu)
        self._overlay = overlay

    # -- data ------------------------------------------------------------
    @staticmethod
    def _find(nodes, folder_id):
        for node in nodes:
            if node["kind"] == "folder":
                if node["row"]["id"] == folder_id:
                    return node
                hit = FolderView._find(node["children"], folder_id)
                if hit is not None:
                    return hit
        return None

    def load(self, folder_id) -> None:
        self.folder_id = folder_id
        self._generation += 1
        self._cards = []
        self._selected = set()
        while (child := self.flow.get_first_child()) is not None:
            self.flow.remove(child)
        node = self._find(self.catalog.tree(), folder_id)
        if node is None:
            self.title.set_text("")
            self.subtitle.set_text("")
            self.flow.set_visible(False)
            self.empty.set_visible(True)
            return
        children = node["children"]
        self.title.set_text(node["row"]["name"])
        parent_id = node["row"]["parent_id"]
        parent = (self.catalog.q1("SELECT id, name FROM folders WHERE id=?", (parent_id,))
                  if parent_id is not None else None)
        self._parent = int(parent["id"]) if parent is not None else None
        self.back.set_visible(parent is not None)
        self.title.set_margin_start(0 if parent is not None else 24)
        if parent is not None:
            self.back.set_tooltip_text(_("Back to {folder}").format(folder=parent["name"]))
        n_folders = sum(1 for c in children if c["kind"] == "folder")
        n_albums = len(children) - n_folders
        parts = []
        if n_albums:
            parts.append(ngettext("{count} album", "{count} albums",
                                  n_albums).format(count=n_albums))
        if n_folders:
            parts.append(ngettext("{count} folder", "{count} folders",
                                  n_folders).format(count=n_folders))
        self.subtitle.set_text("  ·  ".join(parts))
        self.subtitle.set_visible(bool(parts))
        for child in children:
            self.flow.append(self._card(child))
        self.flow.set_visible(bool(children))
        self.empty.set_visible(not children)

    def _count_albums(self, node) -> int:
        return sum(self._count_albums(c) if c["kind"] == "folder" else 1
                   for c in node["children"])

    def _cover(self, node):
        if node["kind"] == "album":
            return node["row"]["cover_path"]
        if node["kind"] == "folder":
            for child in node["children"]:
                path = self._cover(child)
                if path:
                    return path
        return None

    # -- cards -----------------------------------------------------------
    def _card(self, node):
        kind, row = node["kind"], node["row"]
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.add_css_class("pika-summary-card")
        box.set_cursor_from_name("pointer")
        # The card keeps the cover's width; a wider column must not leave a
        # strip beside the square cover.
        box.set_size_request(CARD, -1)
        box.set_halign(Gtk.Align.START)

        frame = Gtk.Box()
        frame.add_css_class("pika-summary-art")
        frame.set_overflow(Gtk.Overflow.HIDDEN)
        cover = self._cover(node)
        if cover:
            tile = PhotoTile(CARD)
            tile.set_size_request(CARD, CARD)
            frame.append(tile)
            self._thumb(cover, tile)
        else:
            icon = Gtk.Image(
                icon_name={"folder": "folder-symbolic",
                           "smart": "folder-saved-search-symbolic"}.get(
                               kind, "folder-pictures-symbolic"),
                pixel_size=64, width_request=CARD, height_request=CARD)
            icon.add_css_class("pika-folder-card-icon")
            frame.append(icon)
        box.append(frame)

        if kind == "folder":
            n = self._count_albums(node)
            detail = ngettext("{count} album", "{count} albums", n).format(count=n)
        elif kind == "smart":
            from ..video import is_video
            from .grid import count_label
            found = self.catalog.browse(scope="smart", smart_id=row["id"], limit=None, offset=0)
            videos = sum(1 for r in found if is_video(r["path"]))
            detail = count_label(len(found) - videos, videos)
        else:
            from ..catalog import VIDEO_SQL
            from .grid import count_label
            n = int(row["n"] or 0)
            videos = int(self.catalog.scalar(
                f"SELECT COUNT(*) FROM album_items ai JOIN photos p ON p.id=ai.photo_id "
                f"WHERE ai.album_id=? AND {VIDEO_SQL}", (row["id"],), 0))
            # "12 videos", not "12 photos", when they are videos
            detail = count_label(n - videos, videos)
        title = Gtk.Label(label=row["name"], xalign=0.0, ellipsize=3,
                          max_width_chars=1, hexpand=True)
        title.add_css_class("pika-summary-title")
        sub = Gtk.Label(label=detail, xalign=0.0)
        sub.add_css_class("pika-dim")
        box.append(title)
        # A dot when the album is on the map: filled once every photo has
        # a place, hollow while only some do. It follows the count rather
        # than the name, which stretches to the card's width and would
        # leave the dot stranded at the far edge.
        placed, mapped = self._placement(kind, row)
        if mapped:
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            line.append(sub)
            line.append(self._map_dot(placed, detail))
            box.append(line)
        else:
            box.append(sub)
        box.update_property([Gtk.AccessibleProperty.LABEL],
                            [f"{row['name']}, {detail}"])

        signal = {"folder": "open-folder", "smart": "open-smart"}.get(kind, "open-album")
        key = (kind, int(row["id"]), row["name"])
        box._card_key = key
        self._cards.append((key, box))
        click = Gtk.GestureClick()
        click.connect("released", self._on_card_click, key, signal)
        box.add_controller(click)
        return box

    @staticmethod
    def _placement(kind, row) -> tuple[bool, bool]:
        """(the album is settled on the map, any of it is on the map).

        Settled means every photo has a place *and* they are all at the
        same one. An album half placed, or placed but still scattered
        over a neighbourhood, is the thing that needs going back to.
        """
        if kind != "album":
            return False, False
        keys = row.keys()
        if "placed" not in keys or "n" not in keys:
            return False, False
        placed, total = int(row["placed"] or 0), int(row["n"] or 0)
        # About 20 metres: closer than that is one place by any reading,
        # and it is the same distance the map itself folds into one pin.
        spread = max(float(row["lat_spread"] or 0.0),
                     float(row["lon_spread"] or 0.0)) \
            if "lat_spread" in keys else 0.0
        return placed >= total > 0 and spread < 0.0002, placed > 0

    @staticmethod
    def _map_dot(whole: bool, detail: str) -> Gtk.Widget:
        dot = Gtk.Image(icon_name="media-record-symbolic",
                        pixel_size=10, valign=Gtk.Align.CENTER)
        dot.add_css_class("pika-map-dot" if whole else "pika-map-dot-part")
        dot.set_tooltip_text(_("On the map") if whole
                             else _("Partly on the map"))
        return dot

    # -- choosing cards --------------------------------------------------------
    def _set_selected(self, keys):
        self._selected = set(keys)
        for key, card in self._cards:
            if key in self._selected:
                card.add_css_class("selected")
            else:
                card.remove_css_class("selected")

    def _on_card_click(self, gesture, _n, _x, _y, key, signal):
        from .chrome import PRIMARY_MASK
        state = gesture.get_current_event_state()
        if state & (PRIMARY_MASK | Gdk.ModifierType.SHIFT_MASK):
            self._set_selected(self._selected ^ {key})   # Ctrl+click (Command on a Mac): choose it
            return
        self._set_selected(set())
        self.emit(signal, key[1])

    def _card_at(self, x, y):
        w = self._overlay.pick(x, y, Gtk.PickFlags.DEFAULT)
        while w is not None and w is not self._overlay:
            key = getattr(w, "_card_key", None)
            if key is not None:
                return key
            w = w.get_parent()
        return None

    def _on_right_click(self, gesture, _n, x, y):
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        key = self._card_at(x, y)
        if key is None:
            self.emit("background-menu", self._overlay, x, y)
            return
        if key not in self._selected:
            self._set_selected({key})
        order = [k for k, _card in self._cards if k in self._selected]
        self.emit("cards-menu", order, x, y)

    def _band_begin(self, gesture, x, y):
        self._band = None
        if self._card_at(x, y) is not None:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        from .chrome import PRIMARY_MASK
        state = gesture.get_current_event_state()
        keep = bool(state & (PRIMARY_MASK | Gdk.ModifierType.SHIFT_MASK))
        self._band = {"x": x, "y": y, "dx": 0.0, "dy": 0.0, "moved": False,
                      "base": set(self._selected) if keep else set()}

    def _band_update(self, gesture, dx, dy):
        b = self._band
        if b is None:
            return
        b["dx"], b["dy"] = dx, dy
        if not b["moved"] and (abs(dx) > 4 or abs(dy) > 4):
            b["moved"] = True
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        if not b["moved"]:
            return
        x0, x1 = sorted((b["x"], b["x"] + dx))
        y0, y1 = sorted((b["y"], b["y"] + dy))
        b["rect"] = (x0, y0, x1 - x0, y1 - y0)
        hits = set()
        for key, card in self._cards:
            ok, r = card.compute_bounds(self._overlay)
            if (ok and r.get_x() < x1 and r.get_x() + r.get_width() > x0
                    and r.get_y() < y1 and r.get_y() + r.get_height() > y0):
                hits.add(key)
        self._set_selected(b["base"] | hits)
        self._band_area.queue_draw()

    def _band_end(self, _gesture, _dx, _dy):
        b, self._band = self._band, None
        self._band_area.queue_draw()
        if b is not None and not b["moved"] and not b["base"]:
            self._set_selected(set())

    def _draw_band(self, _area, cr, _w, _h):
        b = self._band
        if not b or not b.get("moved") or "rect" not in b:
            return
        x, y, bw, bh = b["rect"]
        cr.set_source_rgba(0.114, 0.114, 0.122, 0.10)
        cr.rectangle(x, y, bw, bh)
        cr.fill()
        cr.set_source_rgba(0.114, 0.114, 0.122, 0.55)
        cr.set_line_width(1.0)
        cr.rectangle(x + 0.5, y + 0.5, max(0.0, bw - 1), max(0.0, bh - 1))
        cr.stroke()

    def _thumb(self, path, tile):
        from .grid import thumb_texture
        gen = self._generation
        # decoded at the size the card draws it, not as a 400-pixel picture
        need = int(getattr(tile, "_size", GRID_SIZE) * max(1, self.get_scale_factor()))

        def done(p):
            # decoded on the worker thread, only painted on the UI thread
            if p is None or gen != self._generation:
                return
            try:
                texture = thumb_texture(p, need)
            except Exception:
                return

            def apply():
                if gen == self._generation:
                    tile.set_paintable(texture)
                return False
            GLib.idle_add(apply)
        self.thumbs.request(path, GRID_SIZE, done)
