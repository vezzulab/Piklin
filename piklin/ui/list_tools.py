"""A search box and a way to order a list: what a long list needs.

Countries with a hundred cities, albums at a busy pin, an album list that
has grown for years - each is easier to use when the one wanted can be
typed for and the rest sorted A to Z, Z to A or by how many photos they
hold. The lists are different widgets; this is the one strip above them.
"""
from __future__ import annotations

import unicodedata

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GObject, Gtk  # noqa: E402

from ..i18n import _

# (key, label) - the labels are looked up when the strip is built, so the
# language chosen at start-up is the one used.
MODES = ("count_desc", "count_asc", "az", "za")


def mode_labels() -> dict[str, str]:
    return {
        "count_desc": _("Most photos"),
        "count_asc": _("Fewest photos"),
        "az": _("A to Z"),
        "za": _("Z to A"),
    }


def fold(text: str) -> str:
    """Lower case and without accents, so "sao paulo" finds "São Paulo"."""
    plain = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in plain if not unicodedata.combining(c)).casefold()


def matches(query: str, *texts: str) -> bool:
    """Every word typed appears somewhere in the texts."""
    haystack = " ".join(fold(t) for t in texts if t)
    return all(word in haystack for word in fold(query).split())


def order(items, mode: str, name, count):
    """``items`` in the order ``mode`` names. ``name`` and ``count`` say how
    to read each one; ties fall back to A to Z so the order never jumps."""
    from ..catalog import natural_key
    if mode == "za":
        return sorted(items, key=lambda i: natural_key(name(i)), reverse=True)
    if mode == "count_desc":
        return sorted(items, key=lambda i: (-count(i), natural_key(name(i))))
    if mode == "count_asc":
        return sorted(items, key=lambda i: (count(i), natural_key(name(i))))
    return sorted(items, key=lambda i: natural_key(name(i)))


class ListTools(Gtk.Box):
    """The search entry and the order menu, side by side."""

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, placeholder: str, mode: str = "count_desc",
                 modes: tuple[str, ...] = MODES):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.modes = modes
        self.mode = mode if mode in modes else modes[0]
        self.entry = Gtk.SearchEntry(hexpand=True, placeholder_text=placeholder)
        self.entry.connect("search-changed", lambda _e: self.emit("changed"))
        labels = mode_labels()
        self.menu = Gtk.DropDown.new_from_strings([labels[m] for m in modes])
        self.menu.set_selected(modes.index(self.mode))
        self.menu.set_tooltip_text(_("Sort by"))
        self.menu.connect("notify::selected", self._on_mode)
        self.append(self.entry)
        self.append(self.menu)

    @property
    def query(self) -> str:
        return self.entry.get_text().strip()

    def _on_mode(self, *_a) -> None:
        self.mode = self.modes[self.menu.get_selected()]
        self.emit("changed")

    def clear(self) -> None:
        self.entry.set_text("")
