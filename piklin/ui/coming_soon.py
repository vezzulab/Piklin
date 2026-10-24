"""A page that says a part of Piklin is not open yet: its name, "Coming Soon", one sentence. Nothing else:
no pictures, no list of what is in it."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..i18n import _


class ComingSoonPage(Gtk.Box):
    def __init__(self, title: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                         halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, hexpand=True, vexpand=True)
        name = Gtk.Label(label=title)
        name.add_css_class("title-1")
        tag = Gtk.Label(label=_("Coming Soon"), halign=Gtk.Align.CENTER)
        tag.add_css_class("pika-soon")
        tag.add_css_class("pika-soon-large")
        note = Gtk.Label(label=_("We are making Create better. It will open here when it is ready."),
                         wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=40)
        note.add_css_class("pika-dim")
        for w in (name, tag, note):
            self.append(w)
