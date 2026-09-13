"""In-app photo picker.

Editing never leaves the window.  Choosing a second image for Double
Exposure used to open the system file chooser, which is a separate
toplevel: the edit surface disappeared behind it.  This picker is an
``Adw.Dialog``, so libadwaita presents it inside the window over the
photo being edited, and the library is already indexed - browsing it is
a database query, not a filesystem walk.
"""
from __future__ import annotations

import threading

import gi
from ..i18n import _

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gdk, Gtk  # noqa: E402

from ..thumbs import GRID_SIZE
from .tile import PhotoTile


class PhotoPicker(Adw.Dialog):
    """Pick one photo from the library, without leaving the editor."""

    __gsignals__ = {
        "picked": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, catalog, thumbs, title=_("Choose a photo"),
                 exclude_path=None):
        super().__init__(title=title, content_width=760, content_height=620)
        self.catalog = catalog
        self.thumbs = thumbs
        self.exclude_path = exclude_path

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.add_css_class("pika-header")
        self.search = Gtk.SearchEntry(placeholder_text=_("Search your photos"),
                                      width_chars=26)
        self.search.add_css_class("pika-search")
        self.search.connect("search-changed", lambda *_: self._load())
        header.set_title_widget(self.search)
        toolbar.add_top_bar(header)

        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                                max_children_per_line=64,
                                min_children_per_line=1,
                                row_spacing=0, column_spacing=0,
                                valign=Gtk.Align.START)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      vexpand=True)
        scroller.set_child(self.flow)
        toolbar.set_content(scroller)

        self.status = Gtk.Label(label=_("No photos found"))
        self.status.add_css_class("pika-dim")
        self.status.set_visible(False)
        toolbar.add_bottom_bar(self.status)

        self.set_child(toolbar)
        self._load()

    def _load(self):
        while (child := self.flow.get_first_child()) is not None:
            self.flow.remove(child)
        term = self.search.get_text().strip() or None

        def work():
            rows = self.catalog.browse(scope="library", search=term, limit=300)
            GLib.idle_add(self._fill, rows)
        threading.Thread(target=work, daemon=True).start()

    def _fill(self, rows):
        shown = 0
        for row in rows:
            if self.exclude_path and row["path"] == str(self.exclude_path):
                continue
            shown += 1
            button = Gtk.Button()
            button.add_css_class("flat")
            button.add_css_class("pika-tile-button")
            tile = PhotoTile(140)
            tile.add_css_class("pika-tile")
            button.set_child(tile)
            button.set_tooltip_text(row["filename"])
            path = row["path"]
            button.connect("clicked", self._on_pick, path)
            self.flow.append(button)

            def done(p, _tile=tile):
                def apply():
                    if p:
                        try:
                            _tile.set_paintable(
                                Gdk.Texture.new_from_filename(str(p)))
                        except Exception:
                            pass
                    return False
                GLib.idle_add(apply)
            self.thumbs.request(path, GRID_SIZE, done)
        self.status.set_visible(shown == 0)
        return False

    def _on_pick(self, _btn, path):
        self.emit("picked", path)
        self.close()
