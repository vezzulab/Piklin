"""Rename Photo: the name typed here becomes the file's name on disk."""
from __future__ import annotations

from pathlib import Path

import gi
from ..i18n import _, ngettext

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..photo_rename import RenameError, clean_name, rename_photo


def ask_rename_photo(parent, library, catalog, photo_id, on_renamed) -> None:
    """Ask for a new name and rename the photo's file; ``on_renamed(path)``
    runs after it succeeded."""
    row = catalog.photo(photo_id)
    if row is None:
        return
    old = Path(row["path"])
    dialog = Adw.AlertDialog(
        heading=_("Rename Photo"),
        body=_("The file is renamed on your computer too, so you'll see the new "
               "name everywhere."))

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    line = Gtk.Box(spacing=6)
    entry = Gtk.Entry(text=old.stem, activates_default=True, hexpand=True)
    entry.update_property([Gtk.AccessibleProperty.LABEL], [_("New name")])
    # The extension is shown, not edited: renaming never changes the format.
    ext = Gtk.Label(label=old.suffix)
    ext.add_css_class("dim-label")
    line.append(entry)
    line.append(ext)
    problem = Gtk.Label(xalign=0, wrap=True, visible=False)
    problem.add_css_class("pika-rename-problem")
    box.append(line)
    box.append(problem)
    dialog.set_extra_child(box)

    dialog.add_response("cancel", _("Cancel"))
    dialog.add_response("rename", _("Rename"))
    dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("rename")
    dialog.set_close_response("cancel")

    def check(*_args):
        text = entry.get_text()
        message = ""
        try:
            target = old.with_name(clean_name(text, old.suffix) + old.suffix)
            if (target != old and target.exists()
                    and target.name.lower() != old.name.lower()):
                message = _("There is already a file named “{name}”.").format(
                    name=target.name)
        except RenameError as exc:
            message = str(exc)
        problem.set_text(message)
        problem.set_visible(bool(message))
        dialog.set_response_enabled(
            "rename", not message and text.strip() != old.stem)
    entry.connect("changed", check)
    check()

    def done(_d, response):
        if response != "rename":
            return
        try:
            new = rename_photo(library, catalog, photo_id, entry.get_text())
        except RenameError as exc:
            fail = Adw.AlertDialog(heading=_("Couldn’t Rename"), body=str(exc))
            fail.add_response("ok", _("OK"))
            fail.present(parent)
            return
        on_renamed(new)
    dialog.connect("response", done)
    dialog.present(parent)

    def focus():
        entry.grab_focus()
        entry.select_region(0, -1)
        return GLib.SOURCE_REMOVE
    GLib.idle_add(focus, priority=GLib.PRIORITY_HIGH)
