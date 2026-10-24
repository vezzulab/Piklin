"""In-app photo picker.

Editing never leaves the window.  Choosing a second image for Double
Exposure used to open the system file chooser, which is a separate
toplevel: the edit surface disappeared behind it.  This picker is an
``Adw.Dialog``, so libadwaita presents it inside the window over the
photo being edited, and the library is already indexed - browsing it is
a database query, not a filesystem walk.

With ``multiple`` it takes photos from anywhere in the library at once: the
library as the sidebar shows it is on the left, and what is marked stays
marked while moving between albums and folders.
"""
from __future__ import annotations

import threading

import gi
from ..i18n import _, ngettext

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gdk, Gtk  # noqa: E402

from ..thumbs import GRID_SIZE
from .tile import PhotoTile

PAGE = 240            # photos shown at a time: a tile is a widget, so not thousands at once


class PhotoPicker(Adw.Dialog):
    """Pick one photo from the library, or several from anywhere in it."""

    __gsignals__ = {
        "picked": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # Several photos, in the order they were marked: their paths, one per line.
        "picked-many": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, catalog, thumbs, title=_("Choose a photo"),
                 exclude_path=None, multiple=False, limit: int = 0, videos: bool = False, action: str = ""):
        from .chrome import fit
        width, height = fit(1020 if multiple else 760, 660 if multiple else 620)
        super().__init__(title=title, content_width=width, content_height=height)
        self.catalog = catalog
        self.thumbs = thumbs
        self.exclude_path = exclude_path
        self.multiple = multiple
        self.videos = videos                           # videos can be marked too (a film's clips)
        self.limit = limit if multiple else 0          # most photos that can be marked; 0: no limit
        self._marked: list[str] = []
        self._reverting = False
        self._where = ("library", 0)          # what is being looked through
        self._offset = 0
        self._generation = 0
        self._more = None
        self._toggles: dict[str, Gtk.ToggleButton] = {}
        self._made = set()
        self._exported = set()

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.add_css_class("pika-header")
        self.search = Gtk.SearchEntry(placeholder_text=_("Search your photos"),
                                      width_chars=26)
        self.search.add_css_class("pika-search")
        self.search.connect("search-changed", lambda *_: (self._load(), getattr(self, "tree", None) and self.tree.narrow(self.search.get_text())))
        header.set_title_widget(self.search)
        toolbar.add_top_bar(header)

        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                                max_children_per_line=64,
                                min_children_per_line=1,
                                row_spacing=0, column_spacing=0,
                                valign=Gtk.Align.START)
        self.more_btn = Gtk.Button(label=_("Show more"), halign=Gtk.Align.CENTER,
                                   margin_top=10, margin_bottom=14, visible=False)
        self.more_btn.add_css_class("pill")
        self.more_btn.connect("clicked", lambda *_a: self._load(more=True))
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        column.append(self.flow)
        column.append(self.more_btn)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      vexpand=True, hexpand=True)
        scroller.set_child(column)

        if multiple:
            from .library_tree import LibraryTree
            self.tree = LibraryTree(catalog, extras=[
                ("marked", _("Marked"), "object-select-symbolic")], own_search=False)   # the one search above narrows it too
            self.tree.set_size_request(250, -1)
            self.tree.connect("choose", self._on_source)
            self.tree.select("library")
            self.tree.set_size_request(170, -1)
            # The library on the left can be dragged wider, to read long names of folders and albums.
            body = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, wide_handle=True,
                             start_child=self.tree, end_child=scroller,
                             resize_start_child=False, shrink_start_child=False,
                             resize_end_child=True, shrink_end_child=False)
            body.set_position(300)
            toolbar.set_content(body)
        else:
            toolbar.set_content(scroller)

        self.status = Gtk.Label(label=_("No photos found"))
        self.status.add_css_class("pika-dim")
        self.status.set_visible(False)
        if multiple:
            bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
                             margin_top=8, margin_bottom=8, margin_start=12, margin_end=12)
            self.status.set_hexpand(True)
            self.status.set_xalign(0.0)
            self.all_btn = Gtk.Button(label=_("Select all"),
                                      tooltip_text=_("Mark every photo of what is open: the whole album or folder"))
            self.all_btn.add_css_class("pill")
            self.all_btn.connect("clicked", lambda *_a: self._select_all())
            self.all_btn.set_visible(self.limit != 1)          # one photo only: there is nothing to select all of
            bottom.append(self.all_btn)
            bottom.append(self.status)
            self.add_btn = Gtk.Button(label=action or _("Add photos"))
            self.add_btn.add_css_class("suggested-action")
            self.add_btn.add_css_class("pill")
            self.add_btn.set_sensitive(False)
            self.add_btn.connect("clicked", self._on_add)
            bottom.append(self.add_btn)
            toolbar.add_bottom_bar(bottom)
        else:
            toolbar.add_bottom_bar(self.status)

        self.set_child(toolbar)
        self._load()

    # -- where from --------------------------------------------------------------
    def _on_source(self, _tree, key, kind, ident, _name):
        self._where = (key, ident)
        self._load()

    def _query(self, term, offset):
        key, ident = self._where
        kind = key.split(":")[0]
        args = {"search": term, "limit": PAGE, "offset": offset}
        if key == "favorites":
            return self.catalog.browse(scope="favorites", **args)
        if kind == "album":
            return self.catalog.browse(scope="album", album_id=ident, **args)
        if kind == "smart":
            return self.catalog.browse(scope="smart", smart_id=ident, **args)
        if kind == "folder":
            return self.catalog.browse(scope="folders",
                                       folder_ids=self.tree.folders_inside(key), **args)
        if key == "marked":
            ids = []
            for path in self._marked:
                row = self.catalog.photo_by_path(path)
                if row is not None:
                    ids.append(row["id"])
            return self.catalog.browse(scope="ids", photo_ids=ids, search=term)
        return self.catalog.browse(scope="library", **args)

    def _load(self, more: bool = False):
        term = self.search.get_text().strip() or None
        if not more:
            self._generation += 1
            self._offset = 0
            self._toggles = {}
            while (child := self.flow.get_first_child()) is not None:
                self.flow.remove(child)
        gen = self._generation
        offset = self._offset
        self.more_btn.set_visible(False)

        def work():
            rows = self._query(term, offset)
            GLib.idle_add(self._fill, rows, gen)
        threading.Thread(target=work, daemon=True).start()

    def _fill(self, rows, gen):
        if gen != self._generation:
            return False
        self._offset += len(rows)
        shown = 0
        if self.multiple:
            from ..video import is_video
        for row in rows:
            if self.exclude_path and row["path"] == str(self.exclude_path):
                continue
            if self.multiple and (row["id"] in self._made or row["path"] in self._exported):
                continue
            if self.multiple and is_video(row["path"]) and not self.videos:
                continue
            shown += 1
            button = Gtk.ToggleButton() if self.multiple else Gtk.Button()
            button.add_css_class("flat")
            button.add_css_class("pika-tile-button")
            tile = PhotoTile(140)
            tile.add_css_class("pika-tile")
            if self.multiple:
                # A photo that is marked says so on itself, and which one it is: its
                # number among the ones chosen, in the order they were chosen.
                veil = Gtk.Box(hexpand=True, vexpand=True, visible=False)
                veil.add_css_class("pika-pick-mark")
                number = Gtk.Label(halign=Gtk.Align.START, valign=Gtk.Align.START,
                                   margin_start=8, margin_top=8, visible=False)
                number.add_css_class("pika-pick-number")
                over = Gtk.Overlay(child=tile)
                over.add_overlay(veil)
                over.add_overlay(number)
                button.set_child(over)
                button._veil = veil
                button._number = number
            else:
                button.set_child(tile)
            button.set_tooltip_text(row["filename"])
            path = row["path"]
            if self.multiple:
                button.set_active(path in self._marked)
                self._show_mark(button, path)
                button.connect("toggled", self._on_mark, path)
                self._toggles[path] = button
            else:
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
        self.more_btn.set_visible(len(rows) >= PAGE and self._where[0] != "marked")
        empty = self.flow.get_first_child() is None
        self.status.set_visible(empty or self.multiple)
        if empty:
            self.status.set_text(_("No photos found") if self._where[0] != "marked"
                                 else _("Nothing is marked yet"))
        self._count()
        return False

    # -- marking -------------------------------------------------------------------
    def _show_mark(self, button, path):
        on = path in self._marked
        button._veil.set_visible(on)
        button._number.set_visible(on)
        if on:
            button._number.set_text(str(self._marked.index(path) + 1))

    def _on_mark(self, button, path):
        if self._reverting:
            return
        if button.get_active():
            if self.limit == 1 and self._marked and path not in self._marked:
                self._marked.clear()                # one photo only: the new one takes the place of the old
                self._reverting = True
                for other, btn in self._toggles.items():
                    if other != path:
                        btn.set_active(False)
                self._reverting = False
            if self.limit and len(self._marked) >= self.limit and path not in self._marked:
                self._reverting = True              # no room: the photo is not marked
                button.set_active(False)
                self._reverting = False
                self.status.set_text(ngettext("At most {count} photo can be chosen",
                                              "At most {count} photos can be chosen",
                                              self.limit).format(count=self.limit))
                return
            if path not in self._marked:
                self._marked.append(path)
        elif path in self._marked:
            self._marked.remove(path)
        for other, btn in self._toggles.items():     # the numbers follow the order, whatever left
            self._show_mark(btn, other)
        self._count()

    def _count(self):
        if not self.multiple:
            return
        n = len(self._marked)
        self.add_btn.set_sensitive(n > 0)
        if n and self.limit:
            self.status.set_text(_("{count} of {limit} photos marked").format(count=n, limit=self.limit))
        elif n:
            self.status.set_text(ngettext("{count} photo marked", "{count} photos marked", n
                                          ).format(count=n))
        elif self.flow.get_first_child() is not None:
            self.status.set_text(_("Mark the photos you want"))

    def _select_all(self):
        """Mark every photo of what is open (not only the ones shown yet), up to what the page holds;
        pressed again with all of them marked, it lets them go."""
        if self._where[0] == "marked":
            return
        term = self.search.get_text().strip() or None
        gen = self._generation
        self.all_btn.set_sensitive(False)
        self.status.set_text(_("Marking…"))

        def work():
            from ..video import is_video
            found, offset = [], 0
            while True:
                rows = self._query(term, offset)
                if not rows:
                    break
                offset += len(rows)
                found += [r["path"] for r in rows
                          if (self.videos or not is_video(r["path"])) and r["path"] != str(self.exclude_path or "")
                          and r["id"] not in self._made and r["path"] not in self._exported]
                if len(rows) < PAGE or (self.limit and len(found) >= self.limit + len(self._marked)):
                    break
            GLib.idle_add(apply, found)

        def apply(found):
            self.all_btn.set_sensitive(True)
            if gen != self._generation:
                return False
            take = found[:self.limit] if self.limit else found
            if all(p in self._marked for p in take):           # all were marked: let them go
                self._marked = [p for p in self._marked if p not in set(take)]
            else:
                for p in found:
                    if p in self._marked:
                        continue
                    if self.limit and len(self._marked) >= self.limit:
                        break
                    self._marked.append(p)
            self._reverting = True
            for path, btn in self._toggles.items():
                btn.set_active(path in self._marked)
            self._reverting = False
            for path, btn in self._toggles.items():
                self._show_mark(btn, path)
            self._count()
            return False
        threading.Thread(target=work, daemon=True).start()

    def _on_add(self, _btn):
        self.emit("picked-many", "\n".join(self._marked))
        self.close()

    def _on_pick(self, _btn, path):
        self.emit("picked", path)
        self.close()
