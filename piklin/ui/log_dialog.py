"""Activity Log: what Piklin noted, to find the cause of a problem.

The log stays on this computer and is never sent anywhere. To report a
problem, copy it and paste it into a new problem report on GitHub.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import logs
from ..i18n import _


class LogDialog(Adw.Dialog):
    def __init__(self):
        super().__init__(title=_("Activity Log"), content_width=760,
                         content_height=720)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=12, margin_bottom=18, margin_start=18, margin_end=18)

        intro = Gtk.Label(
            label=_("Piklin notes what it does here - backups, imports, updates and "
                    "errors - only to help find the cause of a problem. The log stays "
                    "on this computer and is never sent anywhere. It holds no "
                    "passwords, addresses or names of your photos, and it never grows "
                    "past {size}.").format(size=GLib.format_size(
                        logs.MAX_BYTES * (logs.KEEP_OLD + 1))),
            wrap=True, xalign=0.0)
        box.append(intro)
        steps = Gtk.Label(
            label=_("Something went wrong? Press Copy, then Report a Problem, describe "
                    "what happened and paste the log into the report."),
            wrap=True, xalign=0.0)
        steps.add_css_class("pika-dim")
        box.append(steps)

        self._buffer = Gtk.TextBuffer()
        text = Gtk.TextView(buffer=self._buffer, editable=False, cursor_visible=False,
                            monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                            top_margin=8, bottom_margin=8, left_margin=10, right_margin=10)
        text.add_css_class("pika-log-text")
        self._text = text
        scroller = Gtk.ScrolledWindow(vexpand=True, child=text)
        scroller.add_css_class("pika-log-frame")
        self._scroller = scroller
        box.append(scroller)

        buttons = Gtk.Box(spacing=8)
        copy = Gtk.Button(label=_("Copy"))
        copy.add_css_class("suggested-action")
        copy.connect("clicked", self._on_copy)
        report = Gtk.Button(label=_("Report a Problem…"))
        report.set_tooltip_text(_("Opens a new problem report on GitHub in your browser"))
        report.connect("clicked", self._on_report)
        folder = Gtk.Button(label=_("Open Folder"))
        folder.connect("clicked", self._on_folder)
        clear = Gtk.Button(label=_("Clear"), hexpand=True, halign=Gtk.Align.END)
        clear.add_css_class("flat")
        clear.connect("clicked", self._on_clear)
        for b in (copy, report, folder, clear):
            buttons.append(b)
        box.append(buttons)

        self._toasts = Adw.ToastOverlay(child=box)
        view.set_content(self._toasts)
        self.set_child(view)
        self._load()

    def _load(self):
        content = logs.read_all() or _("Nothing has been noted yet.")
        self._buffer.set_text(content)

        def to_end():
            adj = self._scroller.get_vadjustment()
            adj.set_value(adj.get_upper())
            return False
        GLib.idle_add(to_end)

    def _on_copy(self, _btn):
        Gdk.Display.get_default().get_clipboard().set(logs.read_all())
        self._toasts.add_toast(Adw.Toast(title=_("Log copied")))

    def _on_report(self, _btn):
        Gtk.UriLauncher.new(logs.ISSUES_URL).launch(self.get_root(), None, None, None)

    def _on_folder(self, _btn):
        try:
            logs.log_dir().mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        Gtk.FileLauncher.new(Gio.File.new_for_path(str(logs.log_file()))).open_containing_folder(
            self.get_root(), None, None, None)

    def _on_clear(self, _btn):
        logs.clear()
        self._load()
