"""The library as the sidebar shows it: All Photos, Favourites, and the folders
with the albums inside them, as a tree to choose from.

It is used wherever someone has to say *where* from: the photo picker of Create,
which takes photos from several albums and folders at once, and the search box.
It reads the catalog once, cheaply (names only, no counts of photos), and a box
at the top narrows it to what matches, opening the folders that hold a match.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, GObject, Gtk  # noqa: E402

from ..i18n import _
from ..smartsearch import fold

INDENT = 16


class LibraryTree(Gtk.Box):
    __gtype_name__ = "PikaLibraryTree"
    # key, kind (library | favorites | folder | album | smart | extra), id, name
    __gsignals__ = {"choose": (GObject.SignalFlags.RUN_FIRST, None, (str, str, int, str))}

    def __init__(self, catalog, extras=(), keep=True, own_search=True):
        """``extras``: (key, label, icon) rows kept above the library's own.
        ``keep``: whether the row chosen stays marked (a place being looked
        through), or is let go so the same one can be chosen again (a list
        to pick several from)."""
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.catalog = catalog
        self._keep = keep
        self._nodes: dict[str, dict] = {}
        self._open: set[str] = set()
        self._selected = ""
        self._busy = False

        self.filter = Gtk.SearchEntry(placeholder_text=_("Find a folder or album"),
                                      margin_start=8, margin_end=8, margin_top=8)
        self.filter.connect("search-changed", lambda *_a: self._apply())
        if own_search:
            self.append(self.filter)         # (without it the list is narrowed by the search box it sits beside: one search, not two)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.add_css_class("navigation-sidebar")
        self.list.connect("row-selected", self._on_row)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                                    child=self.list)
        self.append(scroll)
        self._build(extras)

    def narrow(self, text: str) -> None:
        """Show only what matches ``text`` (what is typed in the search box of the window this list belongs to)."""
        if (self.filter.get_text() or "") != (text or ""):
            self.filter.set_text(text or "")
        self._apply()

    # -- reading the library ------------------------------------------------------
    def _build(self, extras):
        cat = self.catalog
        folders = cat.q("SELECT id, name, parent_id FROM folders "
                        "ORDER BY parent_id, position, name COLLATE NOCASE")
        albums = cat.q("SELECT id, name, folder_id FROM albums "
                       "ORDER BY folder_id, position, name COLLATE NOCASE")
        smarts = cat.q("SELECT id, name, folder_id FROM smart_albums "
                       "ORDER BY folder_id, position, name COLLATE NOCASE")
        kids_f: dict = {}
        for r in folders:
            kids_f.setdefault(r["parent_id"], []).append(r)
        kids_a: dict = {}
        for r in albums:
            kids_a.setdefault(r["folder_id"], []).append(("album", r))
        for r in smarts:
            kids_a.setdefault(r["folder_id"], []).append(("smart", r))

        for key, label, icon in extras:
            self._add(key, "extra", 0, label, icon, 0, None)
        self._add("library", "library", 0, _("All Photos"), "image-x-generic-symbolic", 0, None)
        self._add("favorites", "favorites", 0, _("Favourites"), "starred-symbolic", 0, None)

        def walk(parent_id, depth, parent_key):
            for f in kids_f.get(parent_id, ()):
                key = f"folder:{f['id']}"
                self._add(key, "folder", f["id"], f["name"], "folder-symbolic", depth, parent_key)
                walk(f["id"], depth + 1, key)
                for kind, a in kids_a.get(f["id"], ()):
                    self._add(f"{kind}:{a['id']}", kind, a["id"], a["name"],
                              "view-list-symbolic" if kind == "smart" else "image-x-generic-symbolic",
                              depth + 1, key)
        walk(None, 0, None)
        for kind, a in kids_a.get(None, ()):
            self._add(f"{kind}:{a['id']}", kind, a["id"], a["name"],
                      "view-list-symbolic" if kind == "smart" else "image-x-generic-symbolic", 0, None)
        self._apply()

    def _add(self, key, kind, ident, name, icon, depth, parent):
        row = Gtk.ListBoxRow()
        row._key = key
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                      margin_start=8 + depth * INDENT, margin_end=8, margin_top=4, margin_bottom=4)
        arrow = None
        if kind == "folder":
            arrow = Gtk.Image(icon_name="pan-end-symbolic", pixel_size=12)
            click = Gtk.GestureClick()
            click.connect("pressed", lambda g, _n, _x, _y, k=key: (g.set_state(Gtk.EventSequenceState.CLAIMED),
                                                                  self._toggle(k)))
            arrow.add_controller(click)
            box.append(arrow)
        elif depth:
            box.append(Gtk.Box(width_request=12))
        box.append(Gtk.Image(icon_name=icon, pixel_size=16))
        label = Gtk.Label(label=name, xalign=0.0, hexpand=True, ellipsize=3)
        box.append(label)
        row.set_child(box)
        self.list.append(row)
        self._nodes[key] = {"row": row, "kind": kind, "id": ident, "name": name, "folded": fold(name),
                            "parent": parent, "arrow": arrow}

    # -- opening and narrowing -----------------------------------------------------
    def _toggle(self, key):
        if key in self._open:
            self._open.discard(key)
        else:
            self._open.add(key)
        self._apply()

    def _apply(self):
        """Show the rows whose folders are open, or, with something typed, those
        that match and the folders that hold them."""
        word = fold(self.filter.get_text() or "").strip()
        shown: set[str] = set()
        if word:
            for key, n in self._nodes.items():
                if word in n["folded"]:
                    shown.add(key)
                    p = n["parent"]
                    while p:
                        shown.add(p)
                        p = self._nodes[p]["parent"]
        for key, n in self._nodes.items():
            if word:
                visible = key in shown
                opened = True
            else:
                visible, p = True, n["parent"]
                while p:
                    if p not in self._open:
                        visible = False
                        break
                    p = self._nodes[p]["parent"]
                opened = key in self._open
            n["row"].set_visible(visible)
            if n["arrow"] is not None:
                n["arrow"].set_from_icon_name("pan-down-symbolic" if opened else "pan-end-symbolic")

    # -- choosing -------------------------------------------------------------------
    def _on_row(self, _list, row):
        if row is None or self._busy:
            return
        n = self._nodes.get(getattr(row, "_key", ""))
        if n is None:
            return
        self._selected = row._key
        self.emit("choose", row._key, n["kind"], n["id"], n["name"])
        if not self._keep:
            GLib.idle_add(lambda: (self.list.unselect_all(), False)[1])

    def select(self, key: str, emit: bool = False) -> None:
        n = self._nodes.get(key)
        if n is None:
            return
        self._busy = not emit
        self.list.select_row(n["row"])
        self._busy = False
        self._selected = key

    def folders_inside(self, key: str) -> list[int]:
        """The folder and every folder inside it, for a photo query."""
        n = self._nodes.get(key)
        if not n or n["kind"] != "folder":
            return []
        return sorted(self.catalog.folder_and_inside(n["id"]))
