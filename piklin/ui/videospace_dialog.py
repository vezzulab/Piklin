"""Free Up Space: the videos that can be made smaller, what each saves, how
long this computer would take, and what happens to the originals."""
from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import videospace
from ..i18n import _, ngettext


def fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB", "MB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def fmt_duration(seconds: float) -> str:
    minutes = max(1, int(round(seconds / 60)))
    if minutes == 1:
        return _("about a minute")
    if minutes < 60:
        return ngettext("about {count} minute", "about {count} minutes", minutes).format(count=minutes)
    hours = max(1, int(round(minutes / 60)))
    if hours == 1:
        return _("about an hour")
    if hours < 48:
        return ngettext("about {count} hour", "about {count} hours", hours).format(count=hours)
    days = int(round(hours / 24))
    return ngettext("about {count} day", "about {count} days", days).format(count=days)


def backup_note(remotes) -> tuple[str, bool]:
    """What happens to the originals, for this library's backups. The flag
    is True when it is a warning (no backup keeps them)."""
    nas = [r for r in remotes if videospace.hold_days_for(r) > 0]
    if nas:
        return (_("Your backup on {name} keeps each original for five days, then lets it go "
                  "once the smaller video is checked there and here. This computer gets the "
                  "room back at once.").format(name=nas[0].name), False)
    if remotes:
        return (_("Your cloud backup keeps only the smaller videos, so you don't pay for both. "
                  "Each original stays on this computer for five days, then goes once the "
                  "smaller video is checked."), False)
    return (_("You have no backup. Each original stays on this computer for five days, then "
              "goes for good once the smaller video is checked. Setting up a backup first, "
              "even on a USB drive, is safer."), True)


class VideoSpaceDialog(Adw.Dialog):
    def __init__(self, window, candidates):
        super().__init__(title=_("Optimize Videos"), content_width=560, content_height=680)
        self.window = window
        self.candidates = candidates
        self.checks: list[tuple[Gtk.CheckButton, videospace.Candidate]] = []
        self._speed = 0.0

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                      margin_top=6, margin_bottom=18, margin_start=18, margin_end=18)

        total = sum(c.saving for c in candidates)
        self.summary = Gtk.Label(wrap=True, xalign=0)
        self.summary.add_css_class("title-3")
        self.summary.set_text(ngettext("{count} video can take {size} less",
                                       "{count} videos can take {size} less",
                                       len(candidates)).format(count=len(candidates),
                                                               size=fmt_size(total)))
        box.append(self.summary)

        explain = Gtk.Label(wrap=True, xalign=0, label=_(
            "Each video is made smaller at a quality where the difference doesn't show at a "
            "normal viewing distance, and is kept only once the smaller copy is checked to be "
            "the same video: its length, its sound and its picture. Albums, favourites and "
            "edits stay with it."))
        explain.add_css_class("dim-label")
        box.append(explain)

        remotes = list(getattr(window.autobackup, "remotes", []) or [])
        note, warning = backup_note(remotes)
        note_label = Gtk.Label(wrap=True, xalign=0, label=note)
        if warning:
            note_label.add_css_class("warning")
        box.append(note_label)

        self.time_label = Gtk.Label(wrap=True, xalign=0,
                                    label=_("Measuring how fast this computer is…"))
        self.time_label.add_css_class("dim-label")
        box.append(self.time_label)

        toggle = Gtk.CheckButton(label=_("All videos"), active=True)
        toggle.connect("toggled", self._on_all)
        box.append(toggle)

        rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        rows.add_css_class("boxed-list")
        for c in candidates:
            row = Adw.ActionRow(title=GLib.markup_escape_text(Path(c.path).name))
            taken = window.catalog.photo(c.photo_id)
            taken = taken["taken_at"] if taken is not None else None
            when = datetime.fromtimestamp(taken) if taken else None
            detail = f"{fmt_size(c.bytes)}  →  {fmt_size(c.expected)}"
            if when:
                detail = when.strftime("%Y-%m-%d") + "  ·  " + detail
            row.set_subtitle(detail)
            check = Gtk.CheckButton(active=True, valign=Gtk.Align.CENTER)
            check.connect("toggled", lambda *_a: self._update())
            row.add_prefix(check)
            row.set_activatable_widget(check)
            rows.append(row)
            self.checks.append((check, c))
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                                    min_content_height=220, child=rows)
        box.append(scroll)

        buttons = Gtk.Box(spacing=12, homogeneous=True, halign=Gtk.Align.CENTER)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.add_css_class("pill")
        cancel.connect("clicked", lambda *_a: self.close())
        self.go = Gtk.Button()
        self.go.add_css_class("pill")
        self.go.add_css_class("suggested-action")
        self.go.connect("clicked", self._on_go)
        buttons.append(cancel)
        buttons.append(self.go)
        box.append(buttons)

        view.set_content(box)
        self.set_child(view)
        self._update()
        threading.Thread(target=self._measure, daemon=True).start()

    def chosen(self):
        return [c for check, c in self.checks if check.get_active()]

    def _on_all(self, toggle):
        for check, _c in self.checks:
            check.set_active(toggle.get_active())

    def _update(self):
        chosen = self.chosen()
        self.go.set_label(_("Optimize (saves {size})").format(size=fmt_size(sum(c.saving for c in chosen))))
        self.go.set_sensitive(bool(chosen))
        if self._speed:
            self.time_label.set_text(_("On this computer: {time}, only while it is plugged in and "
                                       "not busy. It carries on after Piklin is closed and opened "
                                       "again.").format(time=fmt_duration(
                                           videospace.estimate_seconds(chosen, self._speed))))

    def _measure(self):
        speed = 0.0
        sample = max(self.candidates, key=lambda c: c.duration, default=None)
        if sample is not None:
            try:
                speed = videospace.measure_speed(sample)
            except Exception:
                speed = 0.0
        GLib.idle_add(self._measured, speed)

    def _measured(self, speed):
        self._speed = speed
        if not speed:
            self.time_label.set_text(_("How long it takes depends on this computer; it runs "
                                       "only while it is plugged in and not busy."))
        self._update()
        return False

    def _on_go(self, _btn):
        chosen = self.chosen()
        if chosen:
            self.window.video_watch.start(chosen)
            self.window.toasts.add_toast(Adw.Toast(
                title=_("Optimizing videos in the background"), timeout=5))
        self.close()
