"""Photos on a drive that is not plugged in (see drives.py).

Piklin looks now and then at the folders it was given. When a drive is
unplugged, the footer says which, and the photos on it stay in the library to
be browsed and searched from their previews; when it is plugged in again Piklin
says so and looks at the folder by itself - nothing to press.
"""
from __future__ import annotations

import threading

from gi.repository import GLib, Gtk

from .. import drives
from ..i18n import _, ngettext

EVERY = 6          # seconds between looks


class DriveActions:
    def _start_drive_watch(self):
        self._drives_missing: dict[str, str] = {}      # folder -> its drive's name
        self._drives_seen = False
        self.footer_drive = Gtk.Label(xalign=0.0, ellipsize=3, visible=False)
        self.footer_drive.add_css_class("pika-footer-space")
        self.footer.append(self.footer_drive)
        GLib.timeout_add_seconds(EVERY, self._drive_tick)

    def _drive_tick(self):
        def work():
            try:
                status = drives.status(self.catalog.roots())
            except Exception:
                return
            GLib.idle_add(self._drive_apply, status)
        threading.Thread(target=work, daemon=True, name="drives").start()
        return GLib.SOURCE_CONTINUE

    def _drive_apply(self, status):
        gone = {s["path"]: s["name"] for s in status if s["external"] and not s["connected"]}
        if self._drives_seen:
            for path, name in self._drives_missing.items():
                if path not in gone:
                    # plugged in again: said once, and its photos looked at again
                    self._show_toast(_("“{name}” is connected again. Looking for new photos.")
                                     .format(name=name))
                    self._start_scan([path])
        self._drives_missing = gone
        self._drives_seen = True
        names = sorted(set(gone.values()))
        if not names:
            self.footer_drive.set_visible(False)
        else:
            self.footer_drive.set_text(
                _("“{name}” is not connected").format(name=names[0]) if len(names) == 1 else
                ngettext("{count} drive is not connected", "{count} drives are not connected",
                         len(names)).format(count=len(names)))
            self.footer_drive.set_tooltip_text(
                _("The photos on it can still be browsed from their previews. The originals "
                  "open when the drive is plugged in."))
            self.footer_drive.set_visible(True)
        return False

    def _say_file_missing(self, path) -> bool:
        """A photo that cannot be opened because its file is not there: which drive
        to plug in, or that it is gone. True when it said something."""
        from pathlib import Path
        if Path(path).exists():
            return False
        if drives.is_external(path):
            self._show_toast(_("This photo is on “{name}”, which is not connected.")
                             .format(name=drives.name(path)))
        else:
            self._show_toast(_("This photo's file is not there any more."))
        return True
