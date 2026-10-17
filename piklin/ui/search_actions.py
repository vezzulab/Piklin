"""The search box's helpers: what Piklin understood of what was typed, shown as
chips that can be taken away, and suggestions while typing (see smartsearch.py).
Also the background naming of the places of the photos (placeindex.py)."""
from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gdk, Gtk  # noqa: E402

from .. import placeindex, smartsearch
from ..smartsearch import fold as fold_
from ..i18n import _

ICONS = {"person": "pk-user-symbolic", "date": "x-office-calendar-symbolic",
         "place": "mark-location-symbolic", "camera": "camera-photo-symbolic",
         "type": "view-list-symbolic", "rating": "starred-symbolic",
         "size": "drive-harddisk-symbolic", "not": "list-remove-symbolic",
         "album": "image-x-generic-symbolic", "folder": "folder-symbolic",
         "event": "x-office-calendar-symbolic"}


class SearchActions:
    # -- building -----------------------------------------------------------
    def _setup_search_ui(self):
        """Suggestions under the search box, and the keys that go with them."""
        self._suggest_pop = Gtk.Popover(autohide=False, has_arrow=False,
                                        position=Gtk.PositionType.BOTTOM)
        self._suggest_pop.set_parent(self.search)
        self._suggest_pop.add_css_class("menu")
        self._suggest_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self._suggest_list.connect("row-activated", lambda _l, row: self._apply_suggestion(row))
        self._suggest_pop.set_child(self._suggest_list)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_search_key)
        self.search.add_controller(keys)
        self.search.connect("activate", self._on_search_activate)
        self.search.connect("stop-search", lambda *_a: self._suggest_pop.popdown())
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *_a: GLib.timeout_add(150, self._hide_suggestions))
        self.search.add_controller(focus)
        self._suggestions = []

    def _wrap_with_chips(self, content):
        """The place the chips live: a row above the photos, shown only when
        something typed was understood."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._chips = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=False,
                                  column_spacing=6, row_spacing=6,
                                  min_children_per_line=1, max_children_per_line=20,
                                  margin_top=8, margin_bottom=2, margin_start=18, margin_end=18)
        self._chips_revealer = Gtk.Revealer(child=self._chips, reveal_child=False,
                                            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        box.append(self._chips_revealer)
        content.set_vexpand(True)
        box.append(content)
        return box

    # -- chips -----------------------------------------------------------------
    def _update_search_ui(self):
        text = self.search.get_text() or ""
        try:
            parsed = smartsearch.parse(self.catalog, text)
        except Exception:
            parsed = None
        while (child := self._chips.get_first_child()) is not None:
            self._chips.remove(child)
        terms = [t for t in (parsed.terms if parsed else [])]
        for term in terms:
            self._chips.append(self._chip(term, text))
        self._chips_revealer.set_reveal_child(bool(terms))
        self._show_suggestions(text)

    def _chip(self, term, text):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.append(Gtk.Image.new_from_icon_name(ICONS.get(term.kind, "view-list-symbolic")))
        box.append(Gtk.Label(label=term.label))
        box.append(Gtk.Image.new_from_icon_name("window-close-symbolic"))
        chip = Gtk.Button(child=box, tooltip_text=_("Take this out of the search"))
        chip.add_css_class("pill")
        chip.add_css_class("pika-chip")
        chip.connect("clicked", lambda *_a, t=term, tx=text: self.search.set_text(smartsearch.without(tx, t)))
        return chip

    # -- suggestions -------------------------------------------------------------
    def _show_suggestions(self, text):
        found = []
        if self.search.has_focus() or self.search.get_focus_child() is not None or True:
            try:
                found = smartsearch.suggest(self.catalog, text, 8)
            except Exception:
                found = []
        self._suggestions = found
        while (row := self._suggest_list.get_first_child()) is not None:
            self._suggest_list.remove(row)
        if not found or not text.strip():
            self._suggest_pop.popdown()
            return
        for kind, shown, put in found:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10,
                          margin_top=4, margin_bottom=4, margin_start=8, margin_end=12)
            box.append(Gtk.Image.new_from_icon_name(ICONS.get(kind, "view-list-symbolic")))
            box.append(Gtk.Label(label=shown, xalign=0.0))
            row.set_child(box)
            row._put = put
            row._kind = kind
            self._suggest_list.append(row)
        self._suggest_pop.set_pointing_to(None)
        if self.search.get_realized():
            self._suggest_pop.popup()

    def _hide_suggestions(self):
        self._suggest_pop.popdown()
        return GLib.SOURCE_REMOVE

    def _apply_suggestion(self, row):
        if row is None:
            return
        text = self.search.get_text() or ""
        toks = smartsearch.tokens(text)
        if not toks:
            return
        put = row._put          # just the name: what was chosen between album and folder is kept apart
        if getattr(row, "_kind", "") in ("album", "folder"):
            smartsearch.prefer(put, row._kind)
        # the words typed so far that this one is named by ("viaje dis" -> "Viaje Disney 2018")
        start = toks[-1]
        bare = fold_(put)
        parent = getattr(put, "parent", "")        # found inside a folder: that is what was typed
        for n in (4, 3, 2):
            if len(toks) >= n and smartsearch.words_match(
                    [fold_(t[0].lstrip("-")) for t in toks[-n:]], fold_(parent) if parent else bare):
                start = toks[-n]
                break
        new = text[:start[1]] + ("-" if start[0].startswith("-") else "") + put + " "
        self._suggest_pop.popdown()
        self.search.set_text(new)
        self.search.set_position(-1)

    def _on_search_key(self, _ctl, keyval, _code, _state):
        if not self._suggest_pop.is_visible():
            return False
        rows = []
        r = self._suggest_list.get_first_child()
        while r is not None:
            rows.append(r)
            r = r.get_next_sibling()
        if not rows:
            return False
        cur = self._suggest_list.get_selected_row()
        i = rows.index(cur) if cur in rows else -1
        if keyval == Gdk.KEY_Down:
            self._suggest_list.select_row(rows[(i + 1) % len(rows)])
            return True
        if keyval == Gdk.KEY_Up:
            self._suggest_list.select_row(rows[(i - 1) % len(rows)])
            return True
        if keyval == Gdk.KEY_Tab and rows:
            self._apply_suggestion(cur or rows[0])
            return True
        if keyval == Gdk.KEY_Escape:
            self._suggest_pop.popdown()
            return True
        return False

    def _on_search_activate(self, _entry):
        row = self._suggest_list.get_selected_row()
        if row is not None and self._suggest_pop.is_visible():
            self._apply_suggestion(row)
        else:
            self._suggest_pop.popdown()

    # -- naming the places ----------------------------------------------------------
    def _start_search_index(self):
        self._places_busy = False
        GLib.timeout_add_seconds(25, self._places_tick)
        GLib.timeout_add_seconds(180, self._places_tick_again)

    def _places_tick_again(self):
        self._places_tick()
        return GLib.SOURCE_CONTINUE

    def _places_tick(self):
        if self._places_busy:
            return GLib.SOURCE_REMOVE
        self._places_busy = True

        def work():
            from .. import system
            system.lower_thread_priority()
            try:
                while placeindex.index_new(self.catalog):
                    pass
            except Exception:
                pass
            GLib.idle_add(self._places_done)
        threading.Thread(target=work, name="places", daemon=True).start()
        return GLib.SOURCE_REMOVE

    def _places_done(self):
        self._places_busy = False
        try:
            smartsearch.vocabulary(self.catalog).refresh(True)
        except Exception:
            pass
        return False
