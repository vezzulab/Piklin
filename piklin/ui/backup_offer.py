"""Once, at the start, when the first steps were skipped: where should a copy of the
photos go? The same three cards as the backup step of the first steps (same words,
same look), and a quiet way out, rather than four equal buttons under a wall of text."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GObject, Gtk, Pango  # noqa: E402

from ..i18n import N_, _

OPTIONS = (
    ("local", "drive-removable-media-symbolic", N_("Folder or Drive"),
     N_("An external drive or a folder on this computer")),
    ("webdav", "network-server-symbolic", N_("NAS or WebDAV Server"),
     N_("A NAS at home, such as QNAP or Synology")),
    ("rclone", "weather-overcast-symbolic", N_("Cloud Service"),
     N_("Google Drive, OneDrive, Dropbox, pCloud or Box")),
)


class BackupOffer(Adw.Dialog):
    __gtype_name__ = "PikaBackupOffer"
    # "local", "webdav", "rclone", or "later" (also when it is closed any other way)
    __gsignals__ = {"answer": (GObject.SignalFlags.RUN_FIRST, None, (str,))}

    def __init__(self):
        super().__init__(content_width=500, follows_content_size=True)
        self._answered = False
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0,
                      margin_top=30, margin_bottom=22, margin_start=26, margin_end=26)

        badge = Gtk.CenterBox(halign=Gtk.Align.CENTER, hexpand=False, vexpand=False)
        badge.set_size_request(60, 60)
        badge.add_css_class("pika-offer-badge")
        badge.set_center_widget(Gtk.Image(icon_name="network-server-symbolic", pixel_size=26))
        box.append(badge)
        title = Gtk.Label(label=_("Keep a Copy of Your Photos"), wrap=True, justify=Gtk.Justification.CENTER,
                          margin_top=16)
        title.add_css_class("title-2")
        box.append(title)
        body = Gtk.Label(label=_("New photos, videos and edits are copied there automatically. "
                                 "Nothing leaves your computer unless you choose where."),
                         wrap=True, justify=Gtk.Justification.CENTER, margin_top=8, margin_bottom=22,
                         max_width_chars=48)
        body.add_css_class("pika-step-body")
        box.append(body)

        cards = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for key, icon, name, detail in OPTIONS:
            cards.append(self._card(key, icon, name, detail))
        box.append(cards)

        later = Gtk.Button(label=_("Not Now"), halign=Gtk.Align.CENTER, margin_top=18)
        later.add_css_class("pill")
        later.add_css_class("pika-quiet-pill")
        later.connect("clicked", lambda *_a: self._answer("later"))
        box.append(later)
        note = Gtk.Label(label=_("Set it up any time in Preferences"), margin_top=8)
        note.add_css_class("pika-step-note")
        box.append(note)

        self.set_child(box)
        self.connect("closed", lambda *_a: self._answer("later"))

    def _card(self, key, icon, name, detail):
        button = Gtk.Button()
        button.add_css_class("pika-choice")
        row = Gtk.Box(spacing=14)
        tile = Gtk.CenterBox(halign=Gtk.Align.START, valign=Gtk.Align.CENTER, hexpand=False, vexpand=False)
        tile.set_size_request(44, 44)
        tile.add_css_class("pika-choice-icon")
        tile.set_center_widget(Gtk.Image(icon_name=icon))
        row.append(tile)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True,
                        valign=Gtk.Align.CENTER)
        label = Gtk.Label(label=_(name), xalign=0.0, wrap=True)
        label.add_css_class("pika-choice-title")
        texts.append(label)
        sub = Gtk.Label(label=_(detail), xalign=0.0, wrap=True, lines=2, ellipsize=Pango.EllipsizeMode.END,
                        wrap_mode=Pango.WrapMode.WORD_CHAR)
        sub.add_css_class("pika-choice-desc")
        texts.append(sub)
        row.append(texts)
        row.append(Gtk.Image(icon_name="go-next-symbolic", valign=Gtk.Align.CENTER, opacity=0.45))
        button.set_child(row)
        button.connect("clicked", lambda *_a: self._answer(key))
        return button

    def _answer(self, answer: str) -> None:
        if self._answered:
            return
        self._answered = True
        self.emit("answer", answer)
        self.close()
