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
        self.title = Gtk.Label(xalign=0.0, ellipsize=3, margin_top=22,
                               margin_start=24, margin_end=24)
        self.title.add_css_class("pika-section-title")
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
        for w in (self.title, self.subtitle, self.flow, self.empty):
            box.append(w)
        self.set_child(box)

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
            n = self.catalog.smart_album_count(row["id"])
            detail = ngettext("{count} photo", "{count} photos", n).format(count=f"{n:,}")
        else:
            n = int(row["n"] or 0)
            detail = ngettext("{count} photo", "{count} photos", n).format(count=f"{n:,}")
        title = Gtk.Label(label=row["name"], xalign=0.0, ellipsize=3,
                          max_width_chars=1, hexpand=True)
        title.add_css_class("pika-summary-title")
        sub = Gtk.Label(label=detail, xalign=0.0)
        sub.add_css_class("pika-dim")
        box.append(title)
        box.append(sub)
        box.update_property([Gtk.AccessibleProperty.LABEL],
                            [f"{row['name']}, {detail}"])

        signal = {"folder": "open-folder", "smart": "open-smart"}.get(kind, "open-album")
        click = Gtk.GestureClick()
        click.connect("released", lambda *_a, s=signal, i=row["id"]: self.emit(s, i))
        box.add_controller(click)
        return box

    def _thumb(self, path, tile):
        gen = self._generation

        def done(p):
            # decoded on the worker thread, only painted on the UI thread
            if p is None or gen != self._generation:
                return
            try:
                texture = Gdk.Texture.new_from_filename(str(p))
            except Exception:
                return

            def apply():
                if gen == self._generation:
                    tile.set_paintable(texture)
                return False
            GLib.idle_add(apply)
        self.thumbs.request(path, GRID_SIZE, done)
