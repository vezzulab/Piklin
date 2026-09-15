"""First steps: the first time Piklin opens, one question at a time.

1 Language · 2 Library · 3 Photos · 4 Backup · 5 Ready

Nothing happens until the last step, so Back can change any choice. The
language changes the guide itself at once. A new language or another place
for the library needs Piklin to start again: what is left to do - the
folders to add, the backup to set up - is written down first and done when
Piklin reopens (see ``take_pending`` and ``apply``).
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from .. import i18n  # noqa: E402
from ..i18n import _  # noqa: E402
from ..paths import LIBRARY_EXT, LIBRARY_NAME, Library, pictures_dir, remember_library  # noqa: E402

STEPS = 5
BACKUP_KINDS = ("local", "webdav", "rclone")


def _pending_file() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "piklin" / "first-steps.json"


def save_pending(data: dict) -> None:
    path = _pending_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
    except OSError:
        pass


def take_pending(library_root: Path) -> dict | None:
    """What the first steps left to do in this library, once."""
    path = _pending_file()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    wanted = data.get("library")
    if wanted and Path(wanted).resolve() != Path(library_root).resolve():
        return None                      # meant for another library
    path.unlink(missing_ok=True)
    return data


def _tilde(path) -> str:
    text, home = str(path), str(Path.home())
    return "~" + text[len(home):] if text.startswith(home + os.sep) else text


def _remove_empty_library(old: Path, current: Path) -> None:
    """The library made before another place was chosen, if nothing was
    ever put in it."""
    try:
        old = old.resolve()
        if (old == current.resolve() or old.suffix != LIBRARY_EXT
                or not (old / "catalog.db").is_file()):
            return
        with closing(sqlite3.connect(f"file:{old / 'catalog.db'}?mode=ro", uri=True)) as db:
            for table in ("photos", "albums", "folders", "smart_albums", "roots"):
                try:
                    if db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]:
                        return
                except sqlite3.OperationalError:
                    continue
        shutil.rmtree(old)
    except Exception:
        pass


def apply(window, pending: dict) -> None:
    """Do what the first steps asked for, in the library now open."""
    window.settings.set("onboarding_done", True)
    window.settings.set("backup_onboarding_done", True)
    old = pending.get("old_library")
    if old:
        _remove_empty_library(Path(old), window.library.root)
    folders = [f for f in (pending.get("folders") or []) if Path(f).is_dir()]
    if folders:
        window._start_scan(folders)
    backup = pending.get("backup")
    if backup in BACKUP_KINDS or pending.get("restore"):
        def open_backup():
            settings = window._open_settings("remotes")
            if backup in BACKUP_KINDS:
                settings._on_add_remote(None, backup)
            return False
        GLib.timeout_add(700, open_backup)


class FirstSteps(Adw.Dialog):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.add_css_class("pika-first-steps")
        self.set_content_width(600)
        self.set_content_height(700)
        # Escape does not dismiss it by accident: Set Up Later does.
        self.set_can_close(False)
        self._original_language = i18n.chosen_language()
        self._current_library = window.library.root
        pictures = pictures_dir()
        self._pictures = pictures if pictures.is_dir() else None
        self.choices = {
            "language": self._original_language,
            "library": self._current_library.parent,
            "photos": "pictures" if self._pictures else "later",
            "folders": [],
            "backup": "none",
        }
        self._step = 0
        self._shown = None
        self._buttons = {}

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.add_css_class("pika-steps")
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        top.add_css_class("pika-steps-top")
        self._dots = Gtk.Box(halign=Gtk.Align.CENTER)
        self._caption = Gtk.Label(halign=Gtk.Align.CENTER)
        self._caption.add_css_class("pika-steps-caption")
        top.append(self._dots)
        top.append(self._caption)
        root.append(top)

        self._stack = Gtk.Stack(transition_duration=260, vhomogeneous=False)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroller.set_child(self._stack)
        root.append(scroller)

        footer = Gtk.Box(spacing=8)
        footer.add_css_class("pika-steps-footer")
        # Back and Continue are the same width, one at each side
        self._back = Gtk.Button()
        self._back.set_size_request(180, -1)
        self._back.add_css_class("pika-step-back")
        self._back.connect("clicked", self._on_back)
        footer.append(self._back)
        footer.append(Gtk.Box(hexpand=True))
        self._next = Gtk.Button()
        self._next.set_size_request(180, -1)
        self._next.add_css_class("suggested-action")
        self._next.add_css_class("pika-primary-pill")
        self._next.connect("clicked", self._on_next)
        footer.append(self._next)
        root.append(footer)
        self.set_child(root)
        self._show(0, Gtk.StackTransitionType.NONE)

    # -- moving between steps ------------------------------------------------
    def _show(self, step, transition):
        self._step = step
        self._buttons = {}
        page = self._build(step)
        self._stack.set_transition_type(transition)
        self._stack.add_child(page)
        self._stack.set_visible_child(page)
        old, self._shown = self._shown, page

        def drop():
            if old is not None and old.get_parent() is self._stack:
                self._stack.remove(old)
            return False
        GLib.timeout_add(self._stack.get_transition_duration() + 80, drop)
        self._update_chrome()
        return False

    def _update_chrome(self):
        while (child := self._dots.get_first_child()) is not None:
            self._dots.remove(child)
        for i in range(STEPS):
            if i:
                line = Gtk.Box(valign=Gtk.Align.CENTER)
                line.add_css_class("pika-step-line")
                if i <= self._step:
                    line.add_css_class("done")
                self._dots.append(line)
            dot = Gtk.Box()
            dot.add_css_class("pika-step-dot")
            if i < self._step:
                dot.add_css_class("done")
                mark = Gtk.Image(icon_name="object-select-symbolic", hexpand=True,
                                 halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            else:
                if i == self._step:
                    dot.add_css_class("current")
                mark = Gtk.Label(label=str(i + 1), hexpand=True,
                                 halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            dot.append(mark)
            self._dots.append(dot)
        self._caption.set_text(_("Step {current} of {total}").format(
            current=self._step + 1, total=STEPS))
        self._back.set_label(_("Set Up Later") if self._step == 0 else _("Back"))
        self._next.set_label(_("Start Using Piklin") if self._step == STEPS - 1
                             else _("Continue"))
        self._next.set_sensitive(self._can_continue())

    def _can_continue(self) -> bool:
        if self._step == 2 and self.choices["photos"] == "folder":
            return bool(self.choices["folders"])
        if self._step == 3 and self.choices["photos"] == "restore":
            return self.choices["backup"] in BACKUP_KINDS
        return True

    def _on_back(self, _button):
        if self._step == 0:
            self._later()
        else:
            self._show(self._step - 1, Gtk.StackTransitionType.SLIDE_RIGHT)

    def _on_next(self, _button):
        if self._step == STEPS - 1:
            self._finish()
        else:
            self._show(self._step + 1, Gtk.StackTransitionType.SLIDE_LEFT)

    # -- pages -------------------------------------------------------------------
    def _build(self, step):
        return (self._language_page, self._library_page, self._photos_page,
                self._backup_page, self._ready_page)[step]()

    def _frame(self, icon, title, body, content):
        # centred in the height too, so the space above and below is the same
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER,
                      vexpand=True)
        box.add_css_class("pika-step-page")
        # a fixed square: an expanding icon stretched it into a tall bar
        badge = Gtk.CenterBox(halign=Gtk.Align.CENTER, valign=Gtk.Align.START,
                              hexpand=False, vexpand=False)
        badge.set_size_request(64, 64)
        badge.add_css_class("pika-step-icon")
        badge.set_center_widget(Gtk.Image(icon_name=icon))
        box.append(badge)
        heading = Gtk.Label(label=title, wrap=True, justify=Gtk.Justification.CENTER)
        heading.add_css_class("pika-step-title")
        box.append(heading)
        text = Gtk.Label(label=body, wrap=True, justify=Gtk.Justification.CENTER,
                         max_width_chars=52, halign=Gtk.Align.CENTER)
        text.add_css_class("pika-step-body")
        box.append(text)
        content.add_css_class("pika-step-content")
        box.append(content)
        return box

    def _choices(self, key, selected, options):
        """Cards to choose one from: (value, icon, title, description)."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        first = None
        for value, icon, title, desc in options:
            button = Gtk.ToggleButton(active=value == selected)
            button.add_css_class("pika-choice")
            if first is None:
                first = button
            else:
                button.set_group(first)
            row = Gtk.Box(spacing=14)
            badge = Gtk.CenterBox(halign=Gtk.Align.START, valign=Gtk.Align.CENTER,
                                  hexpand=False, vexpand=False)
            badge.set_size_request(40, 40)
            badge.add_css_class("pika-choice-icon")
            badge.set_center_widget(Gtk.Image(icon_name=icon))
            row.append(badge)
            texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                            hexpand=True, valign=Gtk.Align.CENTER)
            name = Gtk.Label(label=title, xalign=0.0, wrap=True)
            name.add_css_class("pika-choice-title")
            texts.append(name)
            # at most two lines, so every card is the same height
            detail = Gtk.Label(label=desc or "", xalign=0.0, wrap=True, lines=2,
                               ellipsize=Pango.EllipsizeMode.END,
                               wrap_mode=Pango.WrapMode.WORD_CHAR, visible=bool(desc))
            detail.add_css_class("pika-choice-desc")
            texts.append(detail)
            row.append(texts)
            check = Gtk.Image(icon_name="object-select-symbolic", valign=Gtk.Align.CENTER)
            check.add_css_class("pika-choice-check")
            row.append(check)
            button.set_child(row)
            button._detail = detail
            # "clicked", not "toggled": choosing Somewhere Else again picks
            # another folder, though the card is already chosen.
            button.connect("clicked", self._on_choice, key, value)
            self._buttons[(key, value)] = button
            box.append(button)
        return box

    def _language_page(self):
        options = []
        for code, name in i18n.LANGUAGES:
            if code:
                options.append((code, "preferences-desktop-locale-symbolic", name, None))
            else:
                options.append((code, "computer-symbolic", _(name),
                                _("Piklin follows this computer's language")))
        return self._frame(
            "preferences-desktop-locale-symbolic", _("Welcome to Piklin"),
            _("Your photos and videos, beautifully organized, on this computer. "
              "First, choose the language Piklin speaks."),
            self._choices("language", self.choices["language"], options))

    def _library_page(self):
        chosen = self.choices["library"]
        in_pictures = self._pictures is not None and chosen == self._pictures
        options = []
        if self._pictures is not None:
            options.append(("pictures", "folder-pictures-symbolic", _("In Your Pictures Folder"),
                            _tilde(self._pictures / LIBRARY_NAME)))
        options.append(("else", "folder-open-symbolic", _("Somewhere Else…"),
                        _tilde(chosen / LIBRARY_NAME) if not in_pictures
                        else _("An external drive or another folder")))
        return self._frame(
            "drive-harddisk-symbolic", _("Where Your Library Lives"),
            _("The library keeps Piklin's catalog of your photos, your albums, edits "
              "and thumbnails. Your photos stay where they are."),
            self._choices("library", "pictures" if in_pictures else "else", options))

    def _photos_page(self):
        folder = self.choices["folders"][0] if self.choices["folders"] else None
        options = []
        if self._pictures is not None:
            options.append(("pictures", "folder-pictures-symbolic", _("Pictures Folder"),
                            _tilde(self._pictures)))
        options += [
            ("folder", "folder-open-symbolic", _("Another Folder"),
             _tilde(folder) if folder else _("Such as a folder on an external drive")),
            ("restore", "document-revert-symbolic", _("Restore From a Backup"),
             _("Bring back a library from a drive, NAS or cloud service where Piklin "
               "made a backup")),
            ("later", "document-open-recent-symbolic", _("Not Now"),
             _("Start with an empty library and add folders whenever you like")),
        ]
        return self._frame(
            "image-x-generic-symbolic", _("Your Photos"),
            _("Choose where your photos are. Piklin finds every photo and video in "
              "that folder and the folders inside it. Nothing is moved or changed."),
            self._choices("photos", self.choices["photos"], options))

    def _backup_page(self):
        restore = self.choices["photos"] == "restore"
        options = [
            ("local", "drive-removable-media-symbolic", _("Folder or Drive"),
             _("An external drive or a folder on this computer")),
            ("webdav", "network-server-symbolic", _("NAS or WebDAV Server"),
             _("A NAS at home, such as QNAP or Synology")),
            ("rclone", "weather-overcast-symbolic", _("Cloud Service"),
             _("Google Drive, OneDrive, Dropbox, pCloud or Box")),
        ]
        if restore:
            if self.choices["backup"] not in BACKUP_KINDS:
                self.choices["backup"] = ""
            title = _("Where Is Your Backup?")
            body = _("Connect to the place that holds your Piklin backup. Once it is "
                     "connected, choose Restore beside it.")
        else:
            if self.choices["backup"] == "":
                self.choices["backup"] = "none"
            options.append(("none", "action-unavailable-symbolic", _("Not Now"),
                            _("Set it up any time in Preferences")))
            title = _("Keep a Copy of Your Photos")
            body = _("New photos, videos and edits are copied there automatically. "
                     "Nothing leaves your computer unless you choose where.")
        return self._frame("network-server-symbolic", title, body,
                           self._choices("backup", self.choices["backup"], options))

    def _ready_page(self):
        c = self.choices
        language = dict(i18n.LANGUAGES).get(c["language"], "")
        if not c["language"]:
            language = _(language)
        photos = {"pictures": _tilde(self._pictures) if self._pictures else "",
                  "folder": _tilde(c["folders"][0]) if c["folders"] else "",
                  "restore": _("Restore From a Backup"),
                  "later": _("Not Now")}.get(c["photos"], "")
        backup = {"local": _("Folder or Drive"), "webdav": _("NAS or WebDAV Server"),
                  "rclone": _("Cloud Service")}.get(c["backup"], _("Not Now"))
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class("pika-ready-card")
        for icon, label, value in (
                ("preferences-desktop-locale-symbolic", _("Language"), language),
                ("drive-harddisk-symbolic", _("Library"), _tilde(self._target_root())),
                ("image-x-generic-symbolic", _("Photos"), photos),
                ("network-server-symbolic", _("Backup"), backup)):
            row = Gtk.Box(spacing=12)
            row.add_css_class("pika-ready-row")
            row.append(Gtk.Image(icon_name=icon))
            name = Gtk.Label(label=label, xalign=0.0)
            name.add_css_class("pika-ready-label")
            row.append(name)
            shown = Gtk.Label(label=value, xalign=1.0, hexpand=True,
                              ellipsize=Pango.EllipsizeMode.MIDDLE)
            shown.add_css_class("pika-ready-value")
            row.append(shown)
            card.append(row)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        content.append(card)
        if self._needs_restart():
            note = Gtk.Label(label=_("Piklin opens again to apply these choices."),
                             wrap=True, justify=Gtk.Justification.CENTER)
            note.add_css_class("pika-step-note")
            content.append(note)
        return self._frame(
            "object-select-symbolic", _("You're All Set"),
            _("This is how Piklin starts. You can change any of it later in Preferences."),
            content)

    # -- choosing ------------------------------------------------------------------
    def _on_choice(self, button, key, value):
        if not button.get_active():
            return
        if key == "language":
            if value != self.choices["language"]:
                self.choices["language"] = value
                i18n.setup(value)
                # the guide speaks the new language from here on
                GLib.idle_add(self._show, self._step, Gtk.StackTransitionType.CROSSFADE)
            return
        if key == "library":
            if value == "pictures":
                self.choices["library"] = self._pictures
            else:
                self._pick_folder(_("Choose Where to Keep the Library"), key, value,
                                  self._set_library)
            self._update_chrome()
            return
        if key == "photos" and value == "folder":
            previous = self.choices["photos"]
            self.choices["photos"] = "folder"
            if not self.choices["folders"]:
                self._pick_folder(_("Choose a Folder with Photos"), key, previous,
                                  self._set_photo_folder)
            else:
                self._pick_folder(_("Choose a Folder with Photos"), key, None,
                                  self._set_photo_folder)
            self._update_chrome()
            return
        self.choices[key] = value
        self._update_chrome()

    def _pick_folder(self, title, key, fallback, chosen):
        """Choose a folder; cancelled, the card chosen before comes back."""
        before = next((v for (k, v), b in self._buttons.items()
                       if k == key and v != "else" and b is not None), None)
        dialog = Gtk.FileDialog(title=title)

        def done(dlg, res):
            try:
                folder = dlg.select_folder_finish(res)
            except GLib.Error:
                folder = None
            path = folder.get_path() if folder is not None else None
            if path and chosen(Path(path)):
                pass
            elif key == "library" and self.choices["library"] == self._pictures and before:
                self._buttons[(key, before)].set_active(True)
            elif key == "photos" and fallback and not self.choices["folders"]:
                self.choices["photos"] = fallback
                button = self._buttons.get((key, fallback))
                if button is not None:
                    button.set_active(True)
            self._update_chrome()
        dialog.select_folder(self.window, None, done)

    def _set_library(self, folder: Path) -> bool:
        button = self._buttons.get(("library", "else"))
        if not os.access(folder, os.W_OK):
            if button is not None:
                button._detail.set_text(_("Piklin can't write in this folder. Choose another."))
            return False
        self.choices["library"] = folder
        if button is not None:
            button._detail.set_text(_tilde(folder / LIBRARY_NAME))
        return True

    def _set_photo_folder(self, folder: Path) -> bool:
        self.choices["folders"] = [folder]
        self.choices["photos"] = "folder"
        button = self._buttons.get(("photos", "folder"))
        if button is not None:
            button._detail.set_text(_tilde(folder))
        return True

    # -- the end -------------------------------------------------------------------
    def _target_root(self) -> Path:
        chosen = Path(self.choices["library"])
        if chosen.resolve() == self._current_library.parent.resolve():
            return self._current_library
        return chosen / LIBRARY_NAME

    def _needs_restart(self) -> bool:
        return (self.choices["language"] != self._original_language
                or self._target_root().resolve() != self._current_library.resolve())

    def _later(self):
        """Set Up Later: nothing is asked again; a language already chosen
        is kept."""
        self.window.settings.set("onboarding_done", True)
        self.force_close()
        if self.choices["language"] != self._original_language:
            i18n.choose_language(self.choices["language"])
            app = self.window.get_application()
            app.relaunch_library = str(self._current_library)
            app.quit()

    def _finish(self):
        c, window = self.choices, self.window
        if c["photos"] == "pictures" and self._pictures is not None:
            folders = [str(self._pictures)]
        elif c["photos"] == "folder":
            folders = [str(p) for p in c["folders"]]
        else:
            folders = []
        pending = {"folders": folders,
                   "backup": c["backup"] if c["backup"] in BACKUP_KINDS else None,
                   "restore": c["photos"] == "restore"}
        window.settings.set("onboarding_done", True)
        window.settings.set("backup_onboarding_done", True)
        target = self._target_root()
        restart = self._needs_restart()
        self.force_close()
        if not restart:
            apply(window, pending)
            return
        if c["language"] != self._original_language:
            i18n.choose_language(c["language"])
        if target.resolve() != self._current_library.resolve():
            try:
                Library(target).ensure()
            except OSError:
                target = self._current_library
            else:
                remember_library(target)
                try:
                    empty = (not window.catalog.roots() and not window.catalog.scalar(
                        "SELECT COUNT(*) FROM photos", (), 0))
                except Exception:
                    empty = False
                if empty:
                    pending["old_library"] = str(self._current_library)
        pending["library"] = str(target)
        save_pending(pending)
        app = window.get_application()
        app.relaunch_library = str(target)
        app.quit()
