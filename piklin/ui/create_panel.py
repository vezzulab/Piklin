"""Create: the panel at the right that says what can be made out of the
photos you have chosen.

It is a way in, not a workshop. What it knows is the selection - how many
photos, from when - and what each kind of creation would do with them;
choosing one opens the whole window to work in. Keeping it this side of
the window means the photos stay where they are while you decide, which
is the moment when people change their minds about which ones to use.
"""
from __future__ import annotations

from datetime import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk  # noqa: E402

from ..i18n import N_, _, ngettext

PANEL_WIDTH = 320

# What can be made, in the order it is offered. The smallest and largest
# number of photos each one is worth doing with.
KINDS = (
    ("collage", N_("Collage"),
     N_("Your photos side by side on one picture, each at its own shape."),
     2, 60),
    ("poster", N_("Poster"),
     N_("One picture to print and frame, with room for a few words."),
     1, 12),
)


def span_of(items) -> str:
    """"2019–2024", or one date when they are all from the same day."""
    times = sorted(i.taken_at for i in items if getattr(i, "taken_at", None))
    if not times:
        return ""
    first = datetime.fromtimestamp(times[0])
    last = datetime.fromtimestamp(times[-1])
    if first.year != last.year:
        return f"{first.year}–{last.year}"
    if (first.month, first.day) == (last.month, last.day):
        return first.strftime("%d/%m/%Y")
    return first.strftime("%Y")


class CreatePanel(Gtk.Box):
    """The right-hand panel. ``window`` opens what is chosen here."""

    __gtype_name__ = "PikaCreatePanel"

    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.window = window
        self.add_css_class("pika-create-panel")
        self.set_size_request(PANEL_WIDTH, -1)
        self._rows = []

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                       margin_top=14, margin_bottom=6,
                       margin_start=16, margin_end=10)
        title = Gtk.Label(label=_("Create"), xalign=0.0, hexpand=True)
        title.add_css_class("heading")
        head.append(title)
        close = Gtk.Button(icon_name="window-close-symbolic",
                           tooltip_text=_("Hide Create"))
        close.add_css_class("flat")
        close.connect("clicked", lambda *_a: window.show_create_panel(False))
        head.append(close)
        self.append(head)

        self.summary = Gtk.Label(xalign=0.0, wrap=True, margin_start=16,
                                 margin_end=16, margin_bottom=10)
        self.summary.add_css_class("pika-dim")
        self.append(self.summary)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      margin_start=12, margin_end=12, margin_bottom=12)
        for kind, label, blurb, least, most in KINDS:
            row = self._kind_row(kind, label, blurb, least, most)
            box.append(row)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                    vexpand=True, child=box)
        self.append(scroll)

        self.mine = Gtk.Button()
        self.mine.add_css_class("flat")
        self.mine.connect("clicked", lambda *_a: window.open_scope("creations"))
        self.mine.set_margin_start(12)
        self.mine.set_margin_end(12)
        self.mine.set_margin_bottom(12)
        self.append(self.mine)
        self.refresh()

    def _kind_row(self, kind, label, blurb, least, most):
        button = Gtk.Button()
        button.add_css_class("card")
        button.add_css_class("pika-create-kind")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3,
                        margin_top=10, margin_bottom=10,
                        margin_start=12, margin_end=12)
        name = Gtk.Label(label=_(label), xalign=0.0)
        name.add_css_class("heading")
        inner.append(name)
        why = Gtk.Label(label=_(blurb), xalign=0.0, wrap=True)
        why.add_css_class("pika-dim")
        why.add_css_class("caption")
        inner.append(why)
        note = Gtk.Label(xalign=0.0, wrap=True, visible=False)
        note.add_css_class("caption")
        note.add_css_class("pika-dim")
        inner.append(note)
        button.set_child(inner)
        button.connect("clicked", lambda *_a, k=kind: self._open(k))
        self._rows.append((button, note, kind, least, most))
        return button

    def _open(self, kind):
        items = self.window.grid.selected_items()
        self.window.open_creation(kind, items)

    def refresh(self, *_a):
        """Say what is selected, and which creations that is enough for."""
        items = self.window.grid.selected_items() if self.window.grid else []
        n = len(items)
        if n:
            span = span_of(items)
            text = ngettext("{count} photo chosen", "{count} photos chosen",
                            n).format(count=n)
            self.summary.set_text(text + (f"  ·  {span}" if span else ""))
        else:
            self.summary.set_text(
                _("Choose the photos you want to use, then pick what to make."))
        for button, note, _kind, least, most in self._rows:
            if n == 0:
                button.set_sensitive(False)
                note.set_visible(False)
            elif n < least:
                button.set_sensitive(False)
                note.set_text(ngettext("Needs at least {count} photo",
                                       "Needs at least {count} photos",
                                       least).format(count=least))
                note.set_visible(True)
            elif n > most:
                # Still allowed: it is their photo set, and a poster of
                # twenty is a choice, not a mistake. It just says so.
                button.set_sensitive(True)
                note.set_text(_("Works best with {count} or fewer").format(count=most))
                note.set_visible(True)
            else:
                button.set_sensitive(True)
                note.set_visible(False)
        made = 0
        try:
            from .. import creations
            made = creations.count(self.window.catalog)
        except Exception:
            made = 0
        self.mine.set_label(ngettext("{count} creation you made",
                                     "{count} creations you made", made
                                     ).format(count=made) if made
                            else _("Nothing made yet"))
        self.mine.set_sensitive(bool(made))
