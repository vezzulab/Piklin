"""Preferences: appearance, storage and compression, backup destinations."""
from __future__ import annotations

import threading
from pathlib import Path

import gi
from ..i18n import _, ngettext, pgettext

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gio, Gtk, Pango  # noqa: E402

from .. import compress as cz
from .. import remote as remote_mod


def _fmt(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"


class SettingsDialog(Adw.PreferencesDialog):
    def __init__(self, parent, library, catalog, settings, thumbs,
                 page="general"):
        from .chrome import fit
        width, height = fit(640, 720)
        super().__init__(title=_("Preferences"), content_width=width,
                         content_height=height)
        self.library = library
        self.catalog = catalog
        self.settings = settings
        self.thumbs = thumbs
        self._window = parent

        self.add(self._general_page())
        self.add(self._storage_page())
        self.add(self._remotes_page())
        self.add(self._library_page())
        try:
            self.set_visible_page_name({"general": "general",
                                        "storage": "storage",
                                        "remotes": "remotes",
                                        "library": "library"}.get(page,
                                                                  "general"))
        except Exception:
            pass

    # ==================================================================
    def _general_page(self):
        page = Adw.PreferencesPage(title=pgettext("preferences tab", "General"), name="general",
                                   icon_name="preferences-system-symbolic")

        from .. import i18n
        language = Adw.PreferencesGroup(title=_("Language"))
        codes = [code for code, _name in i18n.LANGUAGES]
        names = [_(name) if not code else name for code, name in i18n.LANGUAGES]
        lang_row = Adw.ComboRow(title=_("Language"),
                                model=Gtk.StringList.new(names))
        chosen = i18n.chosen_language()
        lang_row.set_selected(codes.index(chosen) if chosen in codes else 0)
        lang_row.connect("notify::selected", self._on_language, codes)
        language.add(lang_row)
        page.add(language)

        look = Adw.PreferencesGroup(title=_("Appearance"))
        theme = Adw.ComboRow(title=_("Theme"),
                             model=Gtk.StringList.new(
                                 [_("Match the computer"), _("Light"), _("Dark")]))
        theme.set_selected({"auto": 0, "light": 1,
                            "dark": 2}.get(self.settings.get("theme"), 0))
        theme.connect("notify::selected", self._on_theme)
        look.add(theme)

        group_by = Adw.ComboRow(
            title=_("Group photos by"),
            model=Gtk.StringList.new([_("Don't group"), _("Day"), _("Month"), _("Year")]))
        group_by.set_selected({"none": 0, "day": 1, "month": 2,
                               "year": 3}.get(self.settings.get("group_by"), 1))
        group_by.connect(
            "notify::selected",
            lambda r, _p: self.settings.set(
                "group_by", ["none", "day", "month", "year"][r.get_selected()]))
        look.add(group_by)

        names = Adw.SwitchRow(title=_("Show file names under photos"),
                              active=bool(self.settings.get("show_filenames")))
        names.connect("notify::active",
                      lambda r, _p: self.settings.set("show_filenames",
                                                      r.get_active()))
        look.add(names)
        page.add(look)

        perf = Adw.PreferencesGroup(
            title=_("Speed"),
            description=_("How sharp photos look while you edit. Higher looks better, but each "
                          "change can take a little longer."))
        preview = Adw.SpinRow.new_with_range(800, 4000, 100)
        preview.set_title(_("Preview quality while editing"))
        preview.set_subtitle(_("Higher looks sharper, lower feels faster"))
        preview.set_value(self.settings.get("preview_max_side", 1600))
        preview.connect("notify::value",
                        lambda r, _p: self.settings.set(
                            "preview_max_side", int(r.get_value())))
        perf.add(preview)

        drag = Adw.SpinRow.new_with_range(400, 2000, 100)
        drag.set_title(_("Preview quality while moving a slider"))
        drag.set_subtitle(_("Lower feels smoother"))
        drag.set_value(self.settings.get("drag_max_side", 900))
        drag.connect("notify::value",
                     lambda r, _p: self.settings.set("drag_max_side",
                                                     int(r.get_value())))
        perf.add(drag)
        page.add(perf)

        privacy = Adw.PreferencesGroup(
            title=_("Privacy"),
            description=_("Piklin has no accounts and collects nothing about you. Your photos "
                          "only leave this computer if you set up a backup."))
        faces = Adw.SwitchRow(
            title=_("Find faces for the portrait tools"),
            subtitle=_("Done on this computer. Nothing is ever uploaded."),
            active=bool(self.settings.get("face_detection", True)))
        faces.connect("notify::active",
                      lambda r, _p: self.settings.set("face_detection",
                                                      r.get_active()))
        privacy.add(faces)
        from .. import updates
        check = Adw.SwitchRow(
            title=_("Check for updates"),
            subtitle=_("When Piklin opens, and every hour while it is open, Piklin "
                       "asks GitHub whether a new version is out and lets you "
                       "choose whether to install it. Nothing is installed without "
                       "asking. Nothing about you or your photos is sent."),
            active=updates.enabled())
        check.connect("notify::active",
                      lambda r, _p: updates.save_state(enabled=r.get_active()))
        privacy.add(check)
        page.add(privacy)
        return page

    def _on_language(self, row, _pspec, codes):
        from .. import i18n
        code = codes[row.get_selected()]
        if code == i18n.chosen_language():
            return
        i18n.choose_language(code)
        dlg = Adw.AlertDialog(
            heading=_("Reopen Piklin?"),
            body=_("Piklin needs to reopen to show everything in the new language."))
        dlg.add_response("later", _("Later"))
        dlg.add_response("reopen", _("Reopen Now"))
        dlg.set_response_appearance("reopen", Adw.ResponseAppearance.SUGGESTED)

        def done(_d, response):
            if response == "reopen":
                app = self._window.get_application()
                app.relaunch_library = str(self.library.root)
                self.close()
                app.quit()
        dlg.connect("response", done)
        dlg.present(self.get_root())

    def _on_theme(self, row, _pspec):
        value = ["auto", "light", "dark"][row.get_selected()]
        self.settings.set("theme", value)
        mgr = Adw.StyleManager.get_default()
        mgr.set_color_scheme({"light": Adw.ColorScheme.FORCE_LIGHT,
                              "dark": Adw.ColorScheme.FORCE_DARK}
                             .get(value, Adw.ColorScheme.DEFAULT))

    # ==================================================================
    def _storage_page(self):
        page = Adw.PreferencesPage(title=pgettext("preferences tab", "Storage"), name="storage",
                                   icon_name="drive-harddisk-symbolic")

        # What making a photo smaller actually is, said once, plainly, for
        # somebody who has never met the idea. Without it the choices below
        # read as jargon and people pick nothing.
        about = Adw.PreferencesGroup(
            title=_("Making photos smaller"),
            description=_(
                "A photo from a modern camera holds far more detail than a screen, "
                "or an eye, can use. Piklin can store it using less room on the "
                "disk — the same picture, written more carefully.\n\n"
                "It does not guess. Piklin tries each choice on your own photos, "
                "compares the result against the original, and keeps the best "
                "quality that still saves room.\n\n"
                "Your own folders are never touched, and the photos already in "
                "your library are left as they are. These choices apply to copies "
                "Piklin makes from here on."))
        page.add(about)

        export = Adw.PreferencesGroup(
            title=_("Photo Size When Exporting"),
            description=_("For copies you send to someone, or save outside Piklin. "
                          "The photo in your library is not affected."))
        first = None
        current = self.settings.get("export_profile", cz.DEFAULT_PROFILE)
        for pid, prof in cz.PROFILES.items():
            row = Adw.ActionRow(title=_(prof.name), subtitle=_(prof.summary))
            check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            if first is None:
                first = check
            else:
                check.set_group(first)
            check.set_active(pid == current)
            check.connect("toggled", self._on_profile, pid)
            row.add_prefix(check)
            export.add(row)
        page.add(export)

        fmt = Adw.PreferencesGroup(title=_("Export Format"))
        combo = Adw.ComboRow(
            title=_("File type"),
            subtitle=_("JPEG opens everywhere. WebP and AVIF make smaller files."),
            model=Gtk.StringList.new(
                [_("Same as original"), "JPEG", "WebP", "AVIF", "PNG"]))
        combo.set_selected(
            ["keep", "jpeg", "webp", "avif", "png"].index(
                self.settings.get("export_format", "keep")))
        combo.connect("notify::selected",
                      lambda r, _p: self.settings.set(
                          "export_format",
                          ["keep", "jpeg", "webp", "avif", "png"][r.get_selected()]))
        fmt.add(combo)

        strip = Adw.SwitchRow(
            title=_("Remove hidden details from exported photos"),
            subtitle=_("The camera, date and place are removed from the copy"),
            active=bool(self.settings.get("export_strip_metadata")))
        strip.connect("notify::active",
                      lambda r, _p: self.settings.set("export_strip_metadata",
                                                      r.get_active()))
        fmt.add(strip)
        page.add(fmt)

        imp = Adw.PreferencesGroup(
            title=_("Importing"),
            description=_("How photos are brought into the library."))
        policy = Adw.ComboRow(
            title=_("When adding a folder"),
            model=Gtk.StringList.new([
                _("Leave photos where they are"),
                _("Copy them into the library"),
                _("Move them into the library")]))
        policy.set_selected({"reference": 0, "copy": 1,
                             "move": 2}.get(self.settings.get("import_policy"), 0))
        policy.set_subtitle(
            _("With the first option, your folders are never changed"))
        policy.connect(
            "notify::selected",
            lambda r, _p: self.settings.set(
                "import_policy", ["reference", "copy", "move"][r.get_selected()]))
        imp.add(policy)

        # How photos brought in from a camera are stored. Visually Lossless
        # by default: much smaller, no difference anyone can see, and the
        # capture date, camera and location stay in the file.
        profile_ids = list(cz.PROFILES.keys())
        names = []
        for pid in profile_ids:
            label = _(cz.PROFILES[pid].name)
            if pid == "visually_lossless":
                label += " " + _("(recommended)")
            names.append(label)
        storage = Adw.ComboRow(
            title=_("Make imported photos smaller"),
            subtitle=_("For photos Piklin copies in from a camera, a memory card, a USB "
                       "drive or a drag from the desktop. The originals on the camera or "
                       "the card are never altered."),
            model=Gtk.StringList.new(names))
        current = self.settings.get("storage_profile", "visually_lossless")
        storage.set_selected(profile_ids.index(current)
                             if current in profile_ids else
                             profile_ids.index("visually_lossless"))
        storage.connect(
            "notify::selected",
            lambda r, _p: self.settings.set(
                "storage_profile", profile_ids[r.get_selected()]))
        imp.add(storage)
        video_ids = ["original", "h264"]
        video_row = Adw.ComboRow(
            title=_("Videos when importing"),
            subtitle=_("Smaller files save space. Videos are copied first and made smaller "
                       "afterwards, in the background. Keep the originals if you want the "
                       "best quality for editing later."),
            model=Gtk.StringList.new([_("Keep Original Files"),
                                      _("Smaller Files")]))
        current_video = self.settings.get("storage_video_profile", "original")
        video_row.set_selected(video_ids.index(current_video)
                               if current_video in video_ids else 0)
        video_row.connect(
            "notify::selected",
            lambda r, _p: self.settings.set(
                "storage_video_profile", video_ids[r.get_selected()]))
        imp.add(video_row)
        page.add(imp)

        disk = Adw.PreferencesGroup(title=_("Space Used"))
        self.usage_row = Adw.ActionRow(title=_("Library"), subtitle=_("Measuring…"))
        disk.add(self.usage_row)
        self.thumb_row = Adw.ActionRow(title=_("Photo previews"),
                                       subtitle=_("Measuring…"))
        clear = Gtk.Button(label=_("Clear"), valign=Gtk.Align.CENTER)
        clear.connect("clicked", self._on_clear_thumbs)
        self.thumb_row.add_suffix(clear)
        disk.add(self.thumb_row)
        page.add(disk)
        page.add(self._free_space_group())
        self._measure_usage()
        return page

    def _free_space_group(self):
        """Videos that could take less room, and the work under way."""
        group = Adw.PreferencesGroup(title=_("Optimize Videos"))
        self.space_row = Adw.ActionRow(title=_("Videos"), subtitle=_("Measuring…"))
        group.add(self.space_row)
        watch = getattr(self._window, "video_watch", None)
        if watch is not None and watch.queue:
            left = len(watch.queue)
            self.space_row.set_subtitle(ngettext(
                "{count} video left to optimize", "{count} videos left to optimize",
                left).format(count=left) + ("  ·  " + _("Paused") if watch.paused else ""))
            pause = Gtk.Button(label=_("Resume") if watch.paused else _("Pause"),
                               valign=Gtk.Align.CENTER)

            def toggle(btn):
                watch.pause(not watch.paused)
                btn.set_label(_("Resume") if watch.paused else _("Pause"))
            pause.connect("clicked", toggle)
            self.space_row.add_suffix(pause)
            return group
        review = Gtk.Button(label=_("Review…"), valign=Gtk.Align.CENTER, sensitive=False)
        self.space_row.add_suffix(review)

        def work():
            from .. import videospace
            try:
                found = videospace.plan(self.catalog, self.library.originals)
            except Exception:
                found = []
            GLib.idle_add(show, found)

        def show(found):
            from .videospace_dialog import fmt_size
            if not found:
                self.space_row.set_subtitle(_("Your videos already take little room"))
                return False
            self.space_row.set_subtitle(ngettext(
                "{count} video could take {size} less", "{count} videos could take {size} less",
                len(found)).format(count=len(found), size=fmt_size(sum(c.saving for c in found))))
            review.set_sensitive(True)

            def open_it(_b):
                from .videospace_dialog import VideoSpaceDialog
                self.close()
                VideoSpaceDialog(self._window, found).present(self._window)
            review.connect("clicked", open_it)
            return False
        threading.Thread(target=work, daemon=True).start()
        return group

    def _on_profile(self, check, pid):
        if check.get_active():
            self.settings.set("export_profile", pid)

    def _measure_usage(self):
        def work():
            counts = self.catalog.counts()
            thumb_bytes = self.thumbs.size_on_disk()
            GLib.idle_add(self._show_usage, counts, thumb_bytes)
        threading.Thread(target=work, daemon=True).start()

    def _show_usage(self, counts, thumb_bytes):
        total = counts.get("total", 0)
        videos = int(counts.get("videos", 0))
        photos = max(0, total - videos)
        parts = [ngettext("{count} photo", "{count} photos", photos).format(count=f"{photos:,}")]
        if videos:
            parts.append(ngettext("{count} video", "{count} videos", videos).format(count=f"{videos:,}"))
        self.usage_row.set_subtitle(
            "  ·  ".join(parts)
            + "  ·  " + _("{size} of originals").format(size=_fmt(counts.get("bytes", 0))))
        self.thumb_row.set_subtitle(
            _fmt(thumb_bytes) + "  ·  " + _("rebuilt automatically when needed"))
        return False

    def _on_clear_thumbs(self, _btn):
        freed = self.thumbs.clear()
        self.thumb_row.set_subtitle(_("Cleared {size}").format(size=_fmt(freed)))

    # ==================================================================
    def _remotes_page(self):
        page = Adw.PreferencesPage(title=pgettext("preferences tab", "Backup"), name="remotes",
                                   icon_name="network-server-symbolic")

        intro = Adw.PreferencesGroup(
            title=_("Keep a Copy of Your Photos"),
            description=_("Piklin can copy your library to a drive, a NAS or a cloud service, "
                          "so your photos are safe even if something happens to this computer."))
        page.add(intro)

        self.remote_group = Adw.PreferencesGroup(title=_("Your Backups"))
        page.add(self.remote_group)
        self._refresh_remotes()

        auto = Adw.PreferencesGroup(title=_("Automatic Backup"))
        self.autosync_row = Adw.SwitchRow(
            title=_("Back Up Automatically"),
            subtitle=_("New photos, videos and edits are copied a minute after they change. "
                       "Nothing happens when nothing changed, and it waits while battery "
                       "saver is on."),
            active=bool(self.settings.get("remote_autosync")))
        self.autosync_row.connect("notify::active", self._on_autosync)
        auto.add(self.autosync_row)
        keep_days = [7, 30, 90]
        keep = Adw.ComboRow(
            title=_("Keep Older Versions"),
            subtitle=_("When a photo or edit changes, the previous copy stays in the backup "
                       "for this long."),
            model=Gtk.StringList.new([_("7 days"), _("30 days"), _("90 days")]))
        current = int(self.settings.get("backup_keep_versions_days", 30) or 30)
        keep.set_selected(keep_days.index(current) if current in keep_days else 1)
        keep.connect("notify::selected", lambda row, _p: self.settings.set(
            "backup_keep_versions_days", keep_days[row.get_selected()]))
        auto.add(keep)
        page.add(auto)

        page.add(self._health_group())

        add_group = Adw.PreferencesGroup(title=_("Add a Place to Back Up"))
        for kind, label, help_text in remote_mod.describe_providers():
            row = Adw.ActionRow(title=label, subtitle=help_text)
            btn = Gtk.Button(icon_name="list-add-symbolic",
                             valign=Gtk.Align.CENTER)
            btn.connect("clicked", self._on_add_remote, kind)
            row.add_suffix(btn)
            add_group.add(row)
        page.add(add_group)

        note = Adw.PreferencesGroup()
        krow = Adw.ActionRow(
            title=_("Passwords"),
            subtitle=(_("Saved safely in your computer's password manager.") if remote_mod.keyring_available() else
                      _("This computer has no password manager, so only folders and drives "
                        "can be used.")))
        note.add(krow)
        page.add(note)
        return page

    def _health_group(self):
        """Photo Health: whether Piklin looks for damaged photos, and what
        it has found."""
        group = Adw.PreferencesGroup(
            title=_("Photo Health"),
            description=_("Disks wear out and can damage a photo without anyone noticing. "
                          "Piklin reads your photos now and then, a few at a time, and puts "
                          "back any that were damaged from your backup."))
        switch = Adw.SwitchRow(title=_("Check Photos for Damage"),
                               active=bool(self.settings.get("health_check", True)))
        switch.connect("notify::active",
                       lambda row, _p: self.settings.set("health_check", row.get_active()))
        group.add(switch)

        watch = getattr(self._window, "health_watch", None)
        s = watch.summary() if watch is not None else {"files": 0, "last": None,
                                                       "damaged": 0, "mended": 0}
        if s["files"] and s["last"]:
            from ..autobackup import describe_last
            status = ngettext("{count} photo checked, last {when}",
                              "{count} photos checked, last {when}", s["files"]).format(
                count=f"{s['files']:,}", when=describe_last(s["last"]))
        else:
            status = _("Not checked yet: the first check starts a few minutes after Piklin opens")
        if s["mended"]:
            status += "\n" + ngettext("{count} damaged photo repaired from your backup",
                                      "{count} damaged photos repaired from your backup",
                                      s["mended"]).format(count=s["mended"])
        row = Adw.ActionRow(title=_("Your Photos"), subtitle=status)
        if s["damaged"] and watch is not None:
            row.set_subtitle(status + "\n" + ngettext(
                "{count} photo is damaged, with no good copy in your backup",
                "{count} photos are damaged, with no good copy in your backup",
                s["damaged"]).format(count=s["damaged"]))
            show = Gtk.Button(label=_("Show"), valign=Gtk.Align.CENTER)
            show.connect("clicked", lambda *_a: self._window.show_damaged_photos())
            row.add_suffix(show)
        group.add(row)
        return group

    def _refresh_remotes(self):
        # Remove exactly the rows this method added. An AdwPreferencesGroup's
        # first child is its own internal box, not a row: removing that
        # "fails" with only a logged critical - no exception - so a loop
        # over get_first_child() never ended and opening Settings froze
        # the whole app.
        for old in getattr(self, "_remote_rows", []):
            self.remote_group.remove(old)
        self._remote_rows = []
        self._remote_widgets = {}
        remotes = self.settings.get("remotes", []) or []
        if not remotes:
            empty = Adw.ActionRow(
                title=_("No backups set up yet"),
                subtitle=_("Choose a place below to keep a copy of your photos."))
            self.remote_group.add(empty)
            self._remote_rows.append(empty)
            return
        for cfg in remotes:
            r = remote_mod.Remote.from_dict(cfg)
            row = Adw.ActionRow(title=r.name, subtitle=self._describe(r))
            push = Gtk.Button(label=_("Back Up Now"), valign=Gtk.Align.CENTER)
            push.add_css_class("suggested-action")
            push.connect("clicked", self._on_push_remote, cfg, row)
            row.add_suffix(push)

            more = Gtk.MenuButton(icon_name="view-more-symbolic",
                                  valign=Gtk.Align.CENTER,
                                  tooltip_text=_("More Options"))
            more.add_css_class("flat")
            popover = Gtk.Popover()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                          margin_top=6, margin_bottom=6,
                          margin_start=6, margin_end=6)
            actions = [(_("Check Connection"), self._on_test_remote)]
            if r.kind == "webdav" and r.config.get("url", "").strip().startswith("http://"):
                # Plain http:// is allowed at home, but the password and the photos
                # travel unencrypted: offer the server's https:// address.
                actions.append((_("Use a Secure Connection…"), self._on_secure_remote))
            if r.config.get("encrypted"):
                actions.append((_("Show Recovery Key…"), self._on_show_recovery_key))
                actions.append((_("Delete Unencrypted Copy…"), self._on_delete_plain_copy))
            else:
                actions.append((_("Encrypt This Backup…"), self._on_encrypt_remote))
            actions += [(_("Restore Missing Files…"), self._on_restore_remote),
                        (_("Edit…"), self._on_edit_remote),
                        (_("Remove"), self._on_remove_remote)]
            for label, handler in actions:
                item = Gtk.Button(label=label)
                item.add_css_class("flat")
                item.get_child().set_halign(Gtk.Align.START)
                item.connect("clicked", self._on_remote_menu, popover,
                             handler, cfg, row, push)
                box.append(item)
            popover.set_child(box)
            more.set_popover(popover)
            row.add_suffix(more)

            self.remote_group.add(row)
            self._remote_rows.append(row)
            self._remote_widgets[cfg["id"]] = (row, push)

    @staticmethod
    def _describe(r):
        if r.kind == "local":
            text = r.config.get("path", "")
        elif r.kind == "webdav":
            text = f"{r.config.get('url','')} · {r.config.get('username','')}"
            if r.config.get("url", "").strip().startswith("http://"):
                text += " · " + _("not encrypted")
        else:
            text = f"rclone · {r.config.get('remote','')}:{r.config.get('path','')}"
        if r.config.get("encrypted"):
            text += " · " + _("backups encrypted")
        return text

    # -- encrypted backups ------------------------------------------------
    def _on_encrypt_remote(self, cfg, row, _push):
        r = remote_mod.Remote.from_dict(cfg)
        row.set_subtitle(_("Checking the backup…"))

        def work():
            try:
                exists = remote_mod.encrypted_backup_exists(r)
            except Exception:
                exists = False
            GLib.idle_add(ask, exists)

        def ask(exists):
            row.set_subtitle(self._describe(r))
            if exists:
                self._ask_recovery_key(cfg, row)     # encrypted on another computer
            else:
                self._confirm_encryption(cfg, row)
            return False
        threading.Thread(target=work, daemon=True).start()

    def _confirm_encryption(self, cfg, row):
        dlg = Adw.AlertDialog(
            heading=_("Encrypt This Backup?"),
            body=_("Your photos and videos are encrypted on this computer before they are "
                   "sent, so only you can open them. A new, encrypted copy is made in the "
                   "folder “{folder}”: your whole library is sent again, and the copy already "
                   "there stays until you delete it.\n\nYou get a recovery key. Without it "
                   "nobody can open the encrypted copy, not even Vezzu Studio: write it down "
                   "and keep it safe.").format(folder=remote_mod.ENCRYPTED_FOLDER))
        dlg.add_response("cancel", _("Cancel"))
        dlg.add_response("encrypt", _("Encrypt"))
        dlg.set_response_appearance("encrypt", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_close_response("cancel")
        dlg.connect("response", lambda _d, response: response == "encrypt"
                    and self._run_encryption(cfg, row, None))
        dlg.present(self.get_root())

    def _ask_recovery_key(self, cfg, row):
        dlg = Adw.AlertDialog(
            heading=_("Enter the Recovery Key"),
            body=_("“{backup}” already holds an encrypted Piklin backup. Enter the recovery "
                   "key you wrote down when it was encrypted.").format(
                       backup=cfg.get("name") or _("This backup")))
        entry = Gtk.Entry(placeholder_text="XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX",
                          activates_default=True)
        dlg.set_extra_child(entry)
        dlg.add_response("cancel", _("Cancel"))
        dlg.add_response("open", _("Open Backup"))
        dlg.set_response_appearance("open", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("open")
        dlg.set_close_response("cancel")
        GLib.idle_add(lambda: (entry.grab_focus(), GLib.SOURCE_REMOVE)[1],
                      priority=GLib.PRIORITY_HIGH)
        dlg.connect("response", lambda _d, response: response == "open"
                    and self._run_encryption(cfg, row, entry.get_text()))
        dlg.present(self.get_root())

    def _run_encryption(self, cfg, row, key_text):
        row.set_subtitle(_("Setting up encryption…"))
        r = remote_mod.Remote.from_dict(cfg)

        def work():
            try:
                new_cfg, key, problem = remote_mod.enable_encryption(r, key_text)
            except Exception as exc:
                new_cfg, key, problem = None, None, str(exc)
            GLib.idle_add(finish, new_cfg, key, problem)

        def finish(new_cfg, key, problem):
            if new_cfg is None:
                row.set_subtitle(problem)
                return False
            self._save_remote(new_cfg)
            if key:
                self._show_recovery_key(new_cfg, key, first=True)
            else:
                self._toast(_("The encrypted backup is open on this computer."))
            return False
        threading.Thread(target=work, daemon=True).start()

    def _on_delete_plain_copy(self, cfg, row, _push):
        """Once the encrypted backup is complete, the unencrypted one made
        before it can go, so the destination doesn't keep both."""
        r = remote_mod.Remote.from_dict(cfg)
        row.set_subtitle(_("Checking the backup…"))

        def work():
            GLib.idle_add(ask, remote_mod.plain_backup_exists(r))

        def ask(exists):
            row.set_subtitle(self._describe(r))
            if not exists:
                self._toast(_("There is no unencrypted copy left at this destination."))
                return False
            dlg = Adw.AlertDialog(
                heading=_("Delete the Unencrypted Copy?"),
                body=_("Your encrypted backup stays. The copy made before encryption, in the "
                       "folder “{folder}”, is deleted from “{backup}”, which frees its space.\n\n"
                       "Do this only once Back Up Now has finished without errors.").format(
                           folder=remote_mod.BACKUP_SUBFOLDER, backup=cfg.get("name") or ""))
            dlg.add_response("cancel", _("Cancel"))
            dlg.add_response("delete", _("Delete Unencrypted Copy"))
            dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
            dlg.set_close_response("cancel")
            dlg.connect("response", lambda _d, response: response == "delete" and run())
            dlg.present(self.get_root())
            return False

        def run():
            row.set_subtitle(_("Deleting the unencrypted copy…"))

            def delete():
                ok, problem = remote_mod.delete_plain_backup(r)
                GLib.idle_add(done, ok, problem)
            threading.Thread(target=delete, daemon=True).start()

        def done(ok, problem):
            row.set_subtitle(self._describe(r) if ok else problem)
            if ok:
                self._toast(_("The unencrypted copy was deleted. Your encrypted backup stays."))
            return False
        threading.Thread(target=work, daemon=True).start()

    def _on_show_recovery_key(self, cfg, row, _push):
        key = remote_mod.load_secret(remote_mod.encryption_secret_id(cfg["id"]))
        if not key:
            row.set_subtitle(_("The recovery key isn't on this computer"))
            return
        self._show_recovery_key(cfg, key, first=False)

    def _show_recovery_key(self, cfg, key, first):
        from gi.repository import Gdk
        dlg = Adw.AlertDialog(
            heading=_("Your Recovery Key"),
            body=_("Write this key down and keep it somewhere safe, away from this computer. "
                   "You need it to open your encrypted backup on another computer, or if "
                   "this one is lost."))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        label = Gtk.Label(label=key, selectable=True, wrap=True)
        label.add_css_class("title-3")
        label.add_css_class("monospace")
        box.append(label)
        copy = Gtk.Button(label=_("Copy"), halign=Gtk.Align.CENTER)
        copy.connect("clicked", lambda *_a: (
            self.get_clipboard().set_content(Gdk.ContentProvider.new_for_value(key)),
            self._toast(_("Recovery key copied"))))
        box.append(copy)
        dlg.add_response("done", _("Done"))
        dlg.set_response_appearance("done", Adw.ResponseAppearance.SUGGESTED)
        if first:
            # Not closed until it is kept: this is the only time it is shown unasked.
            wrote = Gtk.CheckButton(label=_("I wrote it down"), halign=Gtk.Align.CENTER)
            box.append(wrote)
            dlg.set_response_enabled("done", False)
            wrote.connect("toggled", lambda b: dlg.set_response_enabled("done", b.get_active()))
            dlg.set_can_close(False)
            dlg.connect("response", lambda *_a: dlg.set_can_close(True))
        dlg.set_extra_child(box)
        dlg.present(self.get_root())

    @staticmethod
    def _result_text(result):
        text = result.message
        if result.detail:
            text += f" — {result.detail}"
        if result.free_bytes:
            text += " · " + _("{size} free").format(size=_fmt(result.free_bytes))
        return text

    def _on_remote_menu(self, _item, popover, handler, cfg, row, push):
        popover.popdown()
        handler(cfg, row, push)

    def _on_add_remote(self, _btn, kind):
        if kind == "local":
            self._choose_local_folder()
            return
        self._prompt_remote(kind)

    def _choose_local_folder(self, existing=None):
        dialog = Gtk.FileDialog(title=_("Choose a backup folder"))

        def done(dlg, res):
            try:
                folder = dlg.select_folder_finish(res)
            except GLib.Error:
                return
            if folder and folder.get_path():
                path = folder.get_path()
                self._save_remote({
                    "id": (existing["id"] if existing
                           else f"local-{abs(hash(path))%10**8}"),
                    "name": ((existing or {}).get("name")
                             or Path(path).name or _("Backup")),
                    "kind": "local",
                    "config": {"path": path}})
        dialog.select_folder(self.get_root(), None, done)

    def _on_edit_remote(self, cfg, _row, _push):
        if cfg.get("kind") == "local":
            self._choose_local_folder(existing=cfg)
        else:
            self._prompt_remote(cfg.get("kind"), existing=cfg)

    def _prompt_remote(self, kind, existing=None):
        conf = (existing or {}).get("config", {})
        webdav = kind == "webdav"
        dialog = Adw.AlertDialog(
            heading=(_("Edit Backup") if existing
                     else _("NAS or Server (WebDAV)") if webdav else _("Cloud Service")),
            body=(_("Enter your server's address (it starts with https://, or http:// at "
                    "home), your username and password. Then choose a folder with the "
                    "folder button.")
                  if webdav else
                  _("Choose your cloud service and sign in: your browser opens so you can "
                    "let Piklin use it. Then choose a folder for your backups.")))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        entries = {}
        cancel_signin = None
        if not webdav:
            # Signing in happens here, in the browser: no rclone config in a terminal.
            services = remote_mod.CLOUD_SERVICES
            picker = Gtk.DropDown.new_from_strings([label for _key, label in services])
            picker.set_hexpand(True)
            sign_in = Gtk.Button(label=_("Sign In…"))
            sign_in.add_css_class("suggested-action")
            line = Gtk.Box(spacing=6)
            line.append(picker)
            line.append(sign_in)
            box.append(line)
            signin_status = Gtk.Label(xalign=0.0, wrap=True)
            signin_status.add_css_class("pika-dim")
            box.append(signin_status)
            cancel_signin = threading.Event()

            def signed_in(name, problem, label):
                sign_in.set_sensitive(True)
                picker.set_sensitive(True)
                if name:
                    entries["remote"].set_text(name)
                    if not entries["name"].get_text().strip():
                        entries["name"].set_text(label)
                    signin_status.set_text(
                        _("Connected to {service}. Now choose a folder for your backups.")
                        .format(service=label))
                else:
                    signin_status.set_text(problem)
                return False

            def on_sign_in(_button):
                key, label = services[picker.get_selected()]
                sign_in.set_sensitive(False)
                picker.set_sensitive(False)
                signin_status.set_text(
                    _("Finish signing in to {service} in your browser…").format(service=label))
                cancel_signin.clear()

                def work():
                    name, problem = remote_mod.connect_cloud(key, cancel=cancel_signin)
                    GLib.idle_add(signed_in, name, problem, label)
                threading.Thread(target=work, daemon=True).start()
            sign_in.connect("clicked", on_sign_in)
        if webdav:
            fields = [("name", _("Name")), ("url", _("Server address")),
                      ("base", _("Folder on the server")),
                      ("username", _("Username")),
                      ("password", _("Password (leave empty to keep it)")
                       if existing else _("Password"))]
        else:
            fields = [("name", _("Name")),
                      ("remote", _("Account (filled in when you sign in)")),
                      ("path", _("Folder in the cloud"))]
        folder_key = "base" if webdav else "path"
        # a server certificate trusted while browsing, saved with the destination
        pending = {"pin": conf.get("cert_sha256", ""), "pin_url": conf.get("url", "")}
        for key, placeholder in fields:
            e = Gtk.Entry(placeholder_text=placeholder)
            if key == "password":
                e.set_visibility(False)
            elif existing:
                e.set_text(existing.get("name", "") if key == "name"
                           else conf.get(key, ""))
            entries[key] = e
            if key == folder_key:
                line = Gtk.Box(spacing=6)
                e.set_hexpand(True)
                line.append(e)
                line.append(self._folder_browser(kind, existing, entries,
                                                 folder_key, pending))
                box.append(line)
            else:
                box.append(e)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("add", _("Save") if existing else _("Add"))
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        # First field (the destination's name) gets focus once the
        # dialog is mapped - same fix as the other entry dialogs, and
        # doubly necessary here since there are up to five fields and no
        # keystroke would reach any of them otherwise.
        def _focus_name_field_once():
            entries["name"].grab_focus()
            return GLib.SOURCE_REMOVE      # run once, not forever
        GLib.idle_add(_focus_name_field_once, priority=GLib.PRIORITY_HIGH)

        def done(d, response):
            if cancel_signin is not None and response != "add":
                cancel_signin.set()             # stops a sign-in still waiting in the browser
            if response != "add":
                return
            values = {k: e.get_text().strip() for k, e in entries.items()}
            password = values.pop("password", "")
            rid = (existing["id"] if existing
                   else f"{kind}-{abs(hash(str(values)))%10**8}")
            config = {k: v for k, v in values.items() if k != "name"}
            # a trusted certificate stays trusted for the same server
            if pending["pin"] and pending["pin_url"] == config.get("url"):
                config["cert_sha256"] = pending["pin"]
            cfg = {"id": rid, "name": values.get("name") or kind.title(),
                   "kind": kind, "config": config}
            if password and not remote_mod.store_secret(rid, password):
                # Refuse to silently write a password into the library
                # folder, which the user has been told to back up.
                self._toast(_("The password couldn't be saved because your password manager is "
                              "locked. Unlock it and edit this backup again."))
            self._save_remote(cfg)
        dialog.connect("response", done)
        dialog.present(self.get_root())

    def _folder_browser(self, kind, existing, entries, folder_key, pending):
        """A button that lists the folders on the server, to pick one."""
        folder_entry = entries[folder_key]
        button = Gtk.MenuButton(icon_name="folder-open-symbolic",
                                tooltip_text=_("Choose a Folder"),
                                valign=Gtk.Align.CENTER)
        popover = Gtk.Popover()
        popover.add_css_class("pika-folder-browser")
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                        margin_top=10, margin_bottom=10,
                        margin_start=10, margin_end=10, width_request=320)
        where = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.START)
        where.add_css_class("heading")
        status = Gtk.Label(xalign=0, wrap=True, max_width_chars=38)
        status.add_css_class("dim-label")
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")
        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, min_content_height=40,
            max_content_height=320, propagate_natural_height=True,
            child=listbox)
        use = Gtk.Button(label=_("Use This Folder"))
        use.add_css_class("suggested-action")
        for w in (where, status, scroller, use):
            outer.append(w)
        popover.set_child(outer)
        button.set_popover(popover)
        state = {"token": 0}

        def current_remote():
            values = {k: e.get_text().strip() for k, e in entries.items()}
            config = {k: v for k, v in values.items()
                      if k not in ("name", "password", folder_key)}
            if values.get("password"):
                config["password"] = values["password"]   # only in memory
            if pending["pin"] and pending["pin_url"] == config.get("url"):
                config["cert_sha256"] = pending["pin"]
            return remote_mod.Remote(id=existing["id"] if existing else "browse",
                                     name="browse", kind=kind, config=config)

        def add_row(label, icon, target):
            row = Gtk.ListBoxRow()
            row.set_name(target)
            inner = Gtk.Box(spacing=10, margin_top=8, margin_bottom=8,
                            margin_start=10, margin_end=10)
            inner.append(Gtk.Image(icon_name=icon))
            inner.append(Gtk.Label(label=label, xalign=0, hexpand=True,
                                   ellipsize=Pango.EllipsizeMode.END))
            row.set_child(inner)
            listbox.append(row)

        def load(path, fallback=True):
            path = path.strip().strip("/")
            state["token"] += 1
            token = state["token"]
            folder_entry.set_text("/" + path if path else "")
            where.set_text("/" + path if path else _("Top Level"))
            status.set_text(_("Connecting…"))
            status.set_visible(True)
            listbox.remove_all()
            scroller.set_visible(False)
            use.set_sensitive(False)
            r = current_remote()

            def work():
                backend = r.backend()
                try:
                    names, problem = backend.list_folders(path), None
                except Exception as exc:
                    names, problem = None, backend.problem(exc)
                GLib.idle_add(show, token, path, names, problem, r, fallback)
            threading.Thread(target=work, daemon=True).start()

        def show(token, path, names, problem, r, fallback):
            if token != state["token"]:
                return False
            if problem is not None:
                if (fallback and path and not problem.fingerprint
                        and problem.not_found):
                    # a folder that doesn't exist yet: show its parent
                    load("/".join(path.split("/")[:-1]))
                    return False
                status.set_text(self._result_text(problem))
                if problem.fingerprint:
                    popover.popdown()

                    def trusted(fp):
                        pending["pin"] = fp
                        pending["pin_url"] = r.config.get("url", "")
                        button.popup()
                    self._ask_trust({"config": r.config}, problem, on_trust=trusted)
                return False
            use.set_sensitive(True)
            if path:
                add_row(_("Back"), "go-up-symbolic",
                        "/".join(path.split("/")[:-1]))
            for n in names:
                add_row(n, "folder-symbolic", f"{path}/{n}" if path else n)
            scroller.set_visible(bool(path or names))
            status.set_text("" if names else
                            _("There are no folders here. You can use this one, or type a new "
                              "folder name at the end and Piklin will create it."))
            status.set_visible(not names)
            return False

        listbox.connect("row-activated", lambda _lb, row: load(row.get_name()))
        popover.connect("show", lambda _p: load(folder_entry.get_text()))
        use.connect("clicked", lambda _b: popover.popdown())
        return button

    def _autobackup(self):
        return getattr(self._window, "autobackup", None)

    def _on_autosync(self, row, _pspec):
        self.settings.set("remote_autosync", row.get_active())
        if self._autobackup():
            self._autobackup().settings_changed()

    def _save_remote(self, cfg):
        remotes = list(self.settings.get("remotes", []) or [])
        is_new = not any(r.get("id") == cfg["id"] for r in remotes)
        if is_new:
            remotes.append(cfg)
        else:
            remotes = [cfg if r.get("id") == cfg["id"] else r for r in remotes]
        first = is_new and len(remotes) == 1
        self.settings.set("remotes", remotes)
        self._refresh_remotes()
        if first:
            # connecting a destination is asking for backups
            self.settings.set("remote_autosync", True)
            if getattr(self, "autosync_row", None) is not None:
                self.autosync_row.set_active(True)
        aut = self._autobackup()
        if aut is not None:
            aut.settings_changed()
            if is_new:
                aut.mark_changed(delay=5)       # the first backup starts now

    def _on_remove_remote(self, cfg, _row, _push):
        remotes = [r for r in (self.settings.get("remotes") or [])
                   if r.get("id") != cfg["id"]]
        self.settings.set("remotes", remotes)
        self._refresh_remotes()
        if self._autobackup() is not None:
            self._autobackup().settings_changed()

    def _ask_trust(self, cfg, result, retry=None, on_trust=None):
        """Offer to trust a server's own certificate, by its fingerprint."""
        url = GLib.markup_escape_text(cfg.get("config", {}).get("url", ""))
        changed = result.cert_changed
        dlg = Adw.AlertDialog(
            heading=_("Security Certificate Changed") if changed else _("Trust This Server?"))
        dlg.set_body_use_markup(True)
        intro = (_("The security certificate of <b>{server}</b> is not the one you "
                   "trusted before. If you didn't replace it yourself, don't continue.")
                 if changed else
                 _("<b>{server}</b> uses its own security certificate, which is normal "
                   "for a server at home. Check that this fingerprint matches the one "
                   "your server shows, then trust it.")).format(server=url)
        fp = GLib.markup_escape_text(
            remote_mod.format_fingerprint(result.fingerprint))
        dlg.set_body(f"{intro}\n\nSHA-256\n<tt>{fp}</tt>")
        dlg.add_response("cancel", _("Cancel"))
        dlg.add_response("trust", _("Trust This Server"))
        dlg.set_response_appearance(
            "trust", Adw.ResponseAppearance.DESTRUCTIVE if changed
            else Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("cancel")

        def done(_d, response):
            if response != "trust":
                return
            if on_trust is not None:
                on_trust(result.fingerprint)
                return
            new = dict(cfg)
            new["config"] = dict(cfg.get("config", {}),
                                 cert_sha256=result.fingerprint)
            self._save_remote(new)
            widgets = self._remote_widgets.get(new["id"])
            if widgets:
                (retry or self._on_test_remote)(new, *widgets)
        dlg.connect("response", done)
        dlg.present(self.get_root())

    def _on_secure_remote(self, cfg, row, _push):
        """Move an http:// destination to the https:// address its server also
        offers, once its certificate is confirmed."""
        row.set_subtitle(_("Looking for a secure connection…"))
        url = cfg.get("config", {}).get("url", "")

        def work():
            try:
                found = remote_mod.secure_address(url)
            except Exception:
                found = None
            GLib.idle_add(finish, found)

        def finish(found):
            if found is None:
                row.set_subtitle(_("This server doesn't offer a secure connection. Turn on "
                                   "HTTPS for WebDAV in its settings, then try again."))
                return False
            https, fingerprint = found
            shown = dict(cfg, config=dict(cfg.get("config", {}), url=https))

            def trusted(fp):
                new = dict(cfg, config=dict(cfg.get("config", {}), url=https, cert_sha256=fp))
                self._save_remote(new)
                widgets = self._remote_widgets.get(new["id"])
                if widgets:
                    self._on_test_remote(new, *widgets)
            self._ask_trust(shown, remote_mod.TestResult(False, "", fingerprint=fingerprint),
                            on_trust=trusted)
            row.set_subtitle(self._describe(remote_mod.Remote.from_dict(cfg)))
            return False
        threading.Thread(target=work, daemon=True).start()

    def _on_test_remote(self, cfg, row, _push):
        r = remote_mod.Remote.from_dict(cfg)
        row.set_subtitle(_("Testing…"))

        def work():
            result = r.backend().test()
            GLib.idle_add(finish, result)

        def finish(result):
            row.set_subtitle(self._result_text(result))
            if result.fingerprint:
                self._ask_trust(cfg, result)
            return False
        threading.Thread(target=work, daemon=True).start()

    def _on_push_remote(self, btn, cfg, row):
        btn.set_sensitive(False)
        library = self.library
        r = remote_mod.Remote.from_dict(cfg)
        labels = {"listing": _("Checking what is already backed up"),
                  "uploading": _("Backing up")}

        def work():
            backend = r.backend()
            test = backend.test()
            if not test.ok:
                GLib.idle_add(finish, self._result_text(test), test)
                return
            files = remote_mod.library_files(library.root)
            progress = backend.push(
                library.root, files,
                keep_versions_days=int(
                    self.settings.get("backup_keep_versions_days", 30) or 0),
                on_progress=lambda p: GLib.idle_add(
                    row.set_subtitle,
                    f"{labels.get(p.phase, p.phase.title())} — "
                    f"{p.done_files:,} of {p.total_files:,} ({_fmt(p.done_bytes)})"))
            if (progress.phase == "done" and not progress.errors
                    and len(self.settings.get("remotes") or []) == 1
                    and self._autobackup() is not None):
                GLib.idle_add(self._autobackup().mark_clean)
            if progress.phase == "done":
                text = ngettext("Backed up {count} file", "Backed up {count} files",
                                progress.uploaded).format(count=f"{progress.uploaded:,}")
                if progress.skipped:
                    text += ", " + ngettext("{count} already backed up",
                                            "{count} already backed up",
                                            progress.skipped).format(
                                                count=f"{progress.skipped:,}")
                if progress.errors:
                    text += ", " + ngettext("{count} failed", "{count} failed",
                                            progress.errors).format(
                                                count=f"{progress.errors:,}")
            else:
                text = progress.message or _("Stopped")
            GLib.idle_add(finish, text, None)

        def finish(text, test):
            btn.set_sensitive(True)
            row.set_subtitle(text)
            if test is not None and test.fingerprint:
                self._ask_trust(cfg, test, retry=lambda new, row_, push_:
                                self._on_push_remote(push_, new, row_))
            return False
        threading.Thread(target=work, daemon=True).start()

    def _on_restore_remote(self, cfg, row, push):
        dlg = Adw.AlertDialog(
            heading=_("Restore Missing Files?"),
            body=_("Piklin copies back from “{backup}” the photos, videos, edits and "
                   "albums that are missing from your library.\n\nNothing in your "
                   "library is replaced, and photos you deleted yourself stay "
                   "deleted.").format(backup=cfg.get("name") or _("the backup")))
        dlg.add_response("cancel", _("Cancel"))
        dlg.add_response("restore", _("Restore"))
        dlg.set_response_appearance("restore", Adw.ResponseAppearance.SUGGESTED)

        def done(_d, response):
            if response == "restore":
                self._run_restore(cfg, row, push)
        dlg.connect("response", done)
        dlg.present(self.get_root())

    def _run_restore(self, cfg, row, push):
        push.set_sensitive(False)
        row.set_subtitle(_("Connecting…"))
        library = self.library
        r = remote_mod.Remote.from_dict(cfg)
        try:
            removed = self.catalog.removed_paths()
            empty = not self.catalog.scalar("SELECT COUNT(*) FROM photos", (), 0)
        except Exception:
            removed, empty = [], False

        def progress_text(p):
            if p.phase != "downloading":
                return _("Reading the backup…")
            return _("Restoring — {done} of {total} ({size})").format(
                done=f"{p.done_files:,}", total=f"{p.total_files:,}",
                size=_fmt(p.done_bytes))

        def work():
            backend = r.backend()
            test = backend.test()
            if not test.ok:
                GLib.idle_add(finish, self._result_text(test), test, None)
                return
            p = backend.restore(
                library.root, skip_paths=removed, overwrite_state=empty,
                on_progress=lambda p: GLib.idle_add(row.set_subtitle,
                                                    progress_text(p)))
            if p.phase == "done":
                text = ngettext("Restored {count} file", "Restored {count} files",
                                p.restored).format(count=f"{p.restored:,}")
                if p.present:
                    text += ", " + ngettext("{count} already in your library",
                                            "{count} already in your library",
                                            p.present).format(count=f"{p.present:,}")
                if p.skipped:
                    text += ", " + ngettext("{count} you deleted left out",
                                            "{count} you deleted left out",
                                            p.skipped).format(count=f"{p.skipped:,}")
                if p.errors:
                    text += ", " + ngettext("{count} failed", "{count} failed",
                                            p.errors).format(count=f"{p.errors:,}")
            else:
                text = p.message or _("Stopped")
            GLib.idle_add(finish, text, None, p)

        def finish(text, test, p):
            push.set_sensitive(True)
            row.set_subtitle(text)
            if test is not None and test.fingerprint:
                self._ask_trust(cfg, test, retry=self._run_restore)
            if p is not None and p.phase == "no_space":
                # Said out loud, not left in a subtitle: a restore that cannot
                # fit is the one thing the person has to act on before trying
                # again, and a quiet line is what let a disk fill up unnoticed.
                full = Adw.AlertDialog(heading=_("Not Enough Room"), body=p.message)
                full.add_response("ok", _("OK"))
                full.present(self.get_root())
            if p is not None and p.restored:
                # Whatever came back, the catalog is now behind the files on
                # disk: mark the rebuild at once, even for a restore that
                # stopped halfway (a full disk, a lost connection). Until it
                # runs, the library stops mirroring the catalog back over the
                # restored files (see sidecars.write_all).
                flag = self.library.rebuild_flag
                flag.parent.mkdir(parents=True, exist_ok=True)
                flag.touch()
                # Albums, edits or marks that came back only take effect once
                # the catalog is rebuilt from them - into an empty library or not.
                if empty or p.restored_state:
                    self._offer_rebuild(p)
                else:
                    self._toast(text)
                    self._window._start_scan(None)
            return False
        threading.Thread(target=work, daemon=True).start()

    def _offer_rebuild(self, p):
        dlg = Adw.AlertDialog(
            heading=_("Reopen Piklin to Finish"),
            body=ngettext("{count} file is back. Piklin reopens and rebuilds your "
                          "library, with your albums, favourites and edits.",
                          "{count} files are back. Piklin reopens and rebuilds your "
                          "library, with your albums, favourites and edits.",
                          p.restored).format(count=f"{p.restored:,}"))
        dlg.add_response("later", _("Later"))
        dlg.add_response("reopen", _("Reopen Now"))
        dlg.set_response_appearance("reopen", Adw.ResponseAppearance.SUGGESTED)

        def done(_d, response):
            # the next start rebuilds, whether now or later
            flag = self.library.root / ".cache" / "rebuild-after-restore"
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.touch()
            if response == "reopen":
                app = self._window.get_application()
                app.relaunch_library = str(self.library.root)
                self.close()
                app.quit()
        dlg.connect("response", done)
        dlg.present(self.get_root())

    def _toast(self, text):
        try:
            self.add_toast(Adw.Toast(title=text, timeout=6))
        except Exception:
            pass

    # ==================================================================
    def _library_page(self):
        page = Adw.PreferencesPage(title=pgettext("preferences tab", "Library"), name="library",
                                   icon_name="folder-pictures-symbolic")
        group = Adw.PreferencesGroup(
            title=_("Where Your Library Is"),
            description=_("Everything Piklin keeps is in this one package, so copying it "
                          "copies everything. You can move it anywhere, even to another drive, "
                          "and open it with Open Library… in the menu."))
        row = Adw.ActionRow(title=_("Library"),
                            subtitle=str(self.library.root))
        row.set_subtitle_selectable(True)
        open_btn = Gtk.Button(icon_name="folder-open-symbolic",
                              valign=Gtk.Align.CENTER)
        open_btn.connect("clicked", self._on_open_library)
        row.add_suffix(open_btn)
        group.add(row)
        page.add(group)

        folders = Adw.PreferencesGroup(
            title=_("Your Photo Folders"),
            description=_("Photos in these folders stay where they are. Piklin never moves or "
                          "changes them."))
        roots = self.catalog.roots(enabled_only=False)
        if not roots:
            folders.add(Adw.ActionRow(title=_("None yet")))
        for r in roots:
            n = self.catalog.scalar(
                "SELECT COUNT(*) FROM photos WHERE root_id=?", (r["id"],), 0)
            rr = Adw.ActionRow(title=r["path"], subtitle=ngettext("{count} photo", "{count} photos", n).format(count=f"{n:,}"))
            drop = Gtk.Button(icon_name="list-remove-symbolic",
                              valign=Gtk.Align.CENTER,
                              tooltip_text=_("Stop using this folder (the files stay)"))
            drop.connect("clicked", self._on_forget_root, r["id"])
            rr.add_suffix(drop)
            folders.add(rr)
        page.add(folders)

        maint = Adw.PreferencesGroup(
            title=_("Maintenance"),
            description=_("Piklin keeps a list of your photos to find them quickly. If "
                          "something looks wrong, the list can be rebuilt without losing "
                          "anything."))
        dupes = Adw.ActionRow(
            title=_("Find Duplicate Photos"),
            subtitle=_("Finds photos that are exact copies"))
        dbtn = Gtk.Button(label=_("Find"), valign=Gtk.Align.CENTER)
        dbtn.connect("clicked", self._on_find_dupes, dupes)
        dupes.add_suffix(dbtn)
        maint.add(dupes)

        n_removed = len(self.catalog.removed_paths())
        removed = Adw.ActionRow(
            title=_("Photos Removed from Piklin"),
            subtitle=(ngettext("{count} photo you removed doesn't come back, even "
                               "though its file is still in your folders",
                               "{count} photos you removed don't come back, even "
                               "though their files are still in your folders",
                               n_removed).format(count=f"{n_removed:,}") if n_removed else
                      _("Photos you remove don't come back, even though their files are "
                        "still in your folders")))
        again = Gtk.Button(label=_("Show Again"), valign=Gtk.Align.CENTER,
                           sensitive=bool(n_removed))
        again.connect("clicked", self._on_show_removed, removed)
        removed.add_suffix(again)
        maint.add(removed)

        rebuild = Adw.ActionRow(
            title=_("Rebuild the Photo List"),
            subtitle=_("Close Piklin and run: piklin --rebuild-index"))
        maint.add(rebuild)
        page.add(maint)
        return page

    def _on_open_library(self, _btn):
        try:
            Gio.AppInfo.launch_default_for_uri(
                Gio.File.new_for_path(str(self.library.root)).get_uri(), None)
        except Exception:
            pass

    def _on_show_removed(self, btn, row):
        """Let removed photos back in: they return with the next scan."""
        n = self.catalog.restore_removed()
        btn.set_sensitive(False)
        row.set_subtitle(_("They'll be back in a moment"))
        try:
            from .. import sidecars
            sidecars.write_removed(self.library, self.catalog)
            self._window._start_scan(None)
        except Exception:
            pass
        self._toast(ngettext("{count} photo will be back in a moment.",
                             "{count} photos will be back in a moment.",
                             n).format(count=f"{n:,}"))

    def _on_forget_root(self, _btn, root_id):
        self.catalog.forget_root(root_id, drop_photos=True)
        self._toast(_("Folder removed from the library. No files were deleted."))

    def _on_find_dupes(self, btn, row):
        btn.set_sensitive(False)
        row.set_subtitle(_("Scanning…"))

        def work():
            groups = self.catalog.duplicate_groups()
            extra = sum(len(g) - 1 for g in groups)
            waste = sum(sum(r["bytes"] for r in g[1:]) for g in groups)
            GLib.idle_add(finish, len(groups), extra, waste)

        def finish(n, extra, waste):
            btn.set_sensitive(True)
            row.set_subtitle(
                (ngettext("{count} group", "{count} groups", n).format(count=n)
                 + ", " + ngettext("{count} duplicate file", "{count} duplicate files",
                                   extra).format(count=extra)
                 + " " + _("using {size}").format(size=_fmt(waste)))
                if n else _("No duplicates found"))
            return False
        threading.Thread(target=work, daemon=True).start()
