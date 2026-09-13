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
        super().__init__(title=_("Preferences"), content_width=640,
                         content_height=720)
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
            subtitle=_("Once a day, Piklin asks GitHub whether a new version is "
                       "out. Nothing about you or your photos is sent."),
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

        export = Adw.PreferencesGroup(
            title=_("Photo Size When Exporting"),
            description=_("Piklin tries each option on your photos and shows how much smaller "
                          "the files get."))
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
            subtitle=_("Only photos from a camera or memory card. Your folders are never "
                       "changed."),
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
            subtitle=_("Smaller files save space. Keep the originals if you want the best "
                       "quality for editing later."),
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
        self._measure_usage()
        return page

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
        self.usage_row.set_subtitle(
            ngettext("{count} photo", "{count} photos", total).format(count=f"{total:,}")
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
            for label, handler in (
                    (_("Check Connection"), self._on_test_remote),
                    (_("Restore Missing Files…"), self._on_restore_remote),
                    (_("Edit…"), self._on_edit_remote),
                    (_("Remove"), self._on_remove_remote)):
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
            return r.config.get("path", "")
        if r.kind == "webdav":
            return f"{r.config.get('url','')} · {r.config.get('username','')}"
        return f"rclone · {r.config.get('remote','')}:{r.config.get('path','')}"

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
                     else _("NAS or Server (WebDAV)") if webdav else _("Cloud Service (rclone)")),
            body=(_("Enter your server's address (it starts with https://, or http:// at "
                    "home), your username and password. Then choose a folder with the "
                    "folder button.")
                  if webdav else
                  _("Enter the name of a cloud service you set up in rclone, with the "
                    "command rclone config.")))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        entries = {}
        if webdav:
            fields = [("name", _("Name")), ("url", _("Server address")),
                      ("base", _("Folder on the server")),
                      ("username", _("Username")),
                      ("password", _("Password (leave empty to keep it)")
                       if existing else _("Password"))]
        else:
            fields = [("name", _("Name")),
                      ("remote", _("Name in rclone")),
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
            if p is not None and p.restored:
                if empty:
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
