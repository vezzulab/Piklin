"""The update window the bell opens: the version you have, the one on
offer, and what it improves and fixes, in your language."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .. import updates
from ..i18n import _, current_language


class UpdateDialog(Adw.Dialog):
    def __init__(self, release, current: str, on_install, installing: bool = False,
                 on_cancel=None):
        super().__init__(title=_("Update Piklin"), content_width=560,
                         content_height=640)
        self._release = release
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                       margin_top=6, margin_bottom=18, margin_start=24, margin_end=24)
        title = Gtk.Label(label=_("Piklin {version} is available").format(
            version=release.version), xalign=0.0, wrap=True)
        title.add_css_class("title-2")
        page.append(title)
        versions = Gtk.Label(
            label=_("You have version {current}. This update takes you to version "
                    "{version}. Your library, albums, edits and backups stay as they "
                    "are.").format(current=current, version=release.version),
            xalign=0.0, wrap=True)
        versions.add_css_class("pika-dim")
        page.append(versions)

        changes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                          margin_top=12)
        lines = updates.notes_for(release.notes, current_language())
        if not lines:
            changes.append(Gtk.Label(
                label=_("This version brings improvements and fixes."),
                xalign=0.0, wrap=True))
        for kind, text in lines:
            if kind == "heading":
                head = Gtk.Label(label=text, xalign=0.0, wrap=True, margin_top=10)
                head.add_css_class("heading")
                changes.append(head)
            else:
                row = Gtk.Box(spacing=8)
                row.append(Gtk.Label(label="•", valign=Gtk.Align.START))
                row.append(Gtk.Label(label=text, xalign=0.0, wrap=True, hexpand=True,
                                     natural_wrap_mode=Gtk.NaturalWrapMode.WORD))
                changes.append(row)
        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      child=changes)
        page.append(scroller)

        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=12)
        def cancel(*_a):
            self.close()
            if on_cancel is not None:
                on_cancel()
        if installing:
            close = Gtk.Button(label=_("Close"))
            close.connect("clicked", lambda *_: self.close())
            buttons.append(close)
            busy = Gtk.Box(spacing=8)
            busy.append(Gtk.Spinner(spinning=True))
            busy.append(Gtk.Label(label=_("Installing…")))
            buttons.append(busy)
        else:
            cancel_btn = Gtk.Button(label=_("Cancel"))
            cancel_btn.connect("clicked", cancel)
            buttons.append(cancel_btn)
            accept = Gtk.Button(label=_("Accept"))
            accept.add_css_class("suggested-action")
            if updates.can_install_itself():
                accept.set_tooltip_text(_("Installs the update and reopens Piklin"))
                accept.connect("clicked", lambda *_: (self.close(), on_install(release)))
            else:
                accept.set_tooltip_text(_("Opens the download page"))
                accept.connect("clicked", lambda *_: (Gtk.UriLauncher.new(
                    release.url).launch(self.get_root(), None, None, None), self.close()))
            buttons.append(accept)
        page.append(buttons)

        view.set_content(page)
        self.set_child(view)
