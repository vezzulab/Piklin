"""Preferences: appearance, storage and compression, backup destinations."""
from __future__ import annotations

import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gio, Gtk  # noqa: E402

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
        super().__init__(title="Preferences", content_width=640,
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
        page = Adw.PreferencesPage(title="General", name="general",
                                   icon_name="preferences-system-symbolic")

        look = Adw.PreferencesGroup(title="Appearance")
        theme = Adw.ComboRow(title="Theme",
                             model=Gtk.StringList.new(
                                 ["Match the system", "Light", "Dark"]))
        theme.set_selected({"auto": 0, "light": 1,
                            "dark": 2}.get(self.settings.get("theme"), 0))
        theme.connect("notify::selected", self._on_theme)
        look.add(theme)

        group_by = Adw.ComboRow(
            title="Group photos by",
            model=Gtk.StringList.new(["Nothing", "Day", "Month", "Year"]))
        group_by.set_selected({"none": 0, "day": 1, "month": 2,
                               "year": 3}.get(self.settings.get("group_by"), 1))
        group_by.connect(
            "notify::selected",
            lambda r, _p: self.settings.set(
                "group_by", ["none", "day", "month", "year"][r.get_selected()]))
        look.add(group_by)

        names = Adw.SwitchRow(title="Show filenames in the grid",
                              active=bool(self.settings.get("show_filenames")))
        names.connect("notify::active",
                      lambda r, _p: self.settings.set("show_filenames",
                                                      r.get_active()))
        look.add(names)
        page.add(look)

        perf = Adw.PreferencesGroup(
            title="Performance",
            description="Higher preview resolution looks better while "
                        "editing and costs more time per adjustment.")
        preview = Adw.SpinRow.new_with_range(800, 4000, 100)
        preview.set_title("Editing preview size")
        preview.set_subtitle("Longest edge, in pixels")
        preview.set_value(self.settings.get("preview_max_side", 1600))
        preview.connect("notify::value",
                        lambda r, _p: self.settings.set(
                            "preview_max_side", int(r.get_value())))
        perf.add(preview)

        drag = Adw.SpinRow.new_with_range(400, 2000, 100)
        drag.set_title("Size while dragging a slider")
        drag.set_subtitle("Lower is more responsive")
        drag.set_value(self.settings.get("drag_max_side", 900))
        drag.connect("notify::value",
                     lambda r, _p: self.settings.set("drag_max_side",
                                                     int(r.get_value())))
        perf.add(drag)
        page.add(perf)

        privacy = Adw.PreferencesGroup(
            title="Privacy",
            description="Piklin has no accounts and no telemetry. "
                        "Nothing leaves this machine unless you set up a "
                        "backup destination yourself.")
        faces = Adw.SwitchRow(
            title="Detect faces for the portrait tools",
            subtitle="Runs locally. No image or face data is ever uploaded.",
            active=bool(self.settings.get("face_detection", True)))
        faces.connect("notify::active",
                      lambda r, _p: self.settings.set("face_detection",
                                                      r.get_active()))
        privacy.add(faces)
        page.add(privacy)
        return page

    def _on_theme(self, row, _pspec):
        value = ["auto", "light", "dark"][row.get_selected()]
        self.settings.set("theme", value)
        mgr = Adw.StyleManager.get_default()
        mgr.set_color_scheme({"light": Adw.ColorScheme.FORCE_LIGHT,
                              "dark": Adw.ColorScheme.FORCE_DARK}
                             .get(value, Adw.ColorScheme.DEFAULT))

    # ==================================================================
    def _storage_page(self):
        page = Adw.PreferencesPage(title="Storage", name="storage",
                                   icon_name="drive-harddisk-symbolic")

        export = Adw.PreferencesGroup(
            title="Compression",
            description="Applied when you export. Piklin encodes a "
                        "sample and measures the result, so these are "
                        "outcomes rather than quality numbers to guess at.")
        first = None
        current = self.settings.get("export_profile", cz.DEFAULT_PROFILE)
        for pid, prof in cz.PROFILES.items():
            row = Adw.ActionRow(title=prof.name, subtitle=prof.summary)
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

        fmt = Adw.PreferencesGroup(title="Export format")
        combo = Adw.ComboRow(
            title="File type",
            subtitle="WebP and AVIF are much smaller; JPEG opens everywhere",
            model=Gtk.StringList.new(
                ["Same as original", "JPEG", "WebP", "AVIF", "PNG"]))
        combo.set_selected(
            ["keep", "jpeg", "webp", "avif", "png"].index(
                self.settings.get("export_format", "keep")))
        combo.connect("notify::selected",
                      lambda r, _p: self.settings.set(
                          "export_format",
                          ["keep", "jpeg", "webp", "avif", "png"][r.get_selected()]))
        fmt.add(combo)

        strip = Adw.SwitchRow(
            title="Remove metadata from exports",
            subtitle="Camera, date and location are stripped from the copy",
            active=bool(self.settings.get("export_strip_metadata")))
        strip.connect("notify::active",
                      lambda r, _p: self.settings.set("export_strip_metadata",
                                                      r.get_active()))
        fmt.add(strip)
        page.add(fmt)

        imp = Adw.PreferencesGroup(
            title="Importing",
            description="How photos are brought into the library.")
        policy = Adw.ComboRow(
            title="When adding a folder",
            model=Gtk.StringList.new([
                "Leave photos where they are",
                "Copy them into the library",
                "Move them into the library"]))
        policy.set_selected({"reference": 0, "copy": 1,
                             "move": 2}.get(self.settings.get("import_policy"), 0))
        policy.set_subtitle(
            "Leaving them in place never touches your existing folders")
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
            label = cz.PROFILES[pid].name
            if pid == "visually_lossless":
                label += " (recommended)"
            names.append(label)
        storage = Adw.ComboRow(
            title="Compress photos when importing",
            subtitle="From a camera or card. Your folders are never changed.",
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
            title="Videos when importing",
            subtitle="Smaller files are re-encoded; keep the originals to "
                     "edit later at full quality",
            model=Gtk.StringList.new(["Keep Original Files",
                                      "Smaller Files (H.264)"]))
        current_video = self.settings.get("storage_video_profile", "original")
        video_row.set_selected(video_ids.index(current_video)
                               if current_video in video_ids else 0)
        video_row.connect(
            "notify::selected",
            lambda r, _p: self.settings.set(
                "storage_video_profile", video_ids[r.get_selected()]))
        imp.add(video_row)
        page.add(imp)

        disk = Adw.PreferencesGroup(title="Disk usage")
        self.usage_row = Adw.ActionRow(title="Library", subtitle="Measuring…")
        disk.add(self.usage_row)
        self.thumb_row = Adw.ActionRow(title="Thumbnail cache",
                                       subtitle="Measuring…")
        clear = Gtk.Button(label="Clear", valign=Gtk.Align.CENTER)
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
        self.usage_row.set_subtitle(
            f"{counts.get('total', 0):,} photos  ·  "
            f"{_fmt(counts.get('bytes', 0))} of originals")
        self.thumb_row.set_subtitle(
            f"{_fmt(thumb_bytes)}  ·  rebuilt automatically when needed")
        return False

    def _on_clear_thumbs(self, _btn):
        freed = self.thumbs.clear()
        self.thumb_row.set_subtitle(f"Cleared {_fmt(freed)}")

    # ==================================================================
    def _remotes_page(self):
        page = Adw.PreferencesPage(title="Backup", name="remotes",
                                   icon_name="network-server-symbolic")

        intro = Adw.PreferencesGroup(
            title="Backup destinations",
            description="Your library folder is designed so that copying it "
                        "is a complete backup. These send it somewhere else "
                        "on a schedule or on demand.")
        page.add(intro)

        self.remote_group = Adw.PreferencesGroup(title="Configured")
        page.add(self.remote_group)
        self._refresh_remotes()

        add_group = Adw.PreferencesGroup(title="Add a destination")
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
            title="Passwords",
            subtitle=("Stored in your system keyring, never in the library "
                      "folder." if remote_mod.keyring_available() else
                      "No system keyring found — password-based destinations "
                      "are unavailable. Use a mounted folder instead."))
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
        remotes = self.settings.get("remotes", []) or []
        if not remotes:
            empty = Adw.ActionRow(
                title="No destinations yet",
                subtitle="Add one below to back up your library.")
            self.remote_group.add(empty)
            self._remote_rows.append(empty)
            return
        for cfg in remotes:
            r = remote_mod.Remote.from_dict(cfg)
            row = Adw.ActionRow(title=r.name,
                                subtitle=self._describe(r))
            test = Gtk.Button(label="Test", valign=Gtk.Align.CENTER)
            test.connect("clicked", self._on_test_remote, r, row)
            row.add_suffix(test)
            push = Gtk.Button(label="Back Up Now", valign=Gtk.Align.CENTER)
            push.add_css_class("suggested-action")
            push.connect("clicked", self._on_push_remote, r, row)
            row.add_suffix(push)
            drop = Gtk.Button(icon_name="user-trash-symbolic",
                              valign=Gtk.Align.CENTER)
            drop.add_css_class("flat")
            drop.connect("clicked", self._on_remove_remote, r.id)
            row.add_suffix(drop)
            self.remote_group.add(row)
            self._remote_rows.append(row)

    @staticmethod
    def _describe(r):
        if r.kind == "local":
            return r.config.get("path", "")
        if r.kind == "webdav":
            return f"{r.config.get('url','')} · {r.config.get('username','')}"
        return f"rclone · {r.config.get('remote','')}:{r.config.get('path','')}"

    def _on_add_remote(self, _btn, kind):
        if kind == "local":
            dialog = Gtk.FileDialog(title="Choose a backup folder")

            def done(dlg, res):
                try:
                    folder = dlg.select_folder_finish(res)
                except GLib.Error:
                    return
                if folder and folder.get_path():
                    self._save_remote({
                        "id": f"local-{abs(hash(folder.get_path()))%10**8}",
                        "name": Path(folder.get_path()).name or "Backup",
                        "kind": "local",
                        "config": {"path": folder.get_path()}})
            dialog.select_folder(self.get_root(), None, done)
            return
        self._prompt_remote(kind)

    def _prompt_remote(self, kind):
        dialog = Adw.AlertDialog(
            heading="WebDAV server" if kind == "webdav" else "rclone remote",
            body=("QNAP, Synology, Nextcloud, pCloud and Box all speak "
                  "WebDAV. Use the https:// address from your server."
                  if kind == "webdav" else
                  "Pick a remote you have already set up with "
                  "'rclone config'."))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        entries = {}
        if kind == "webdav":
            fields = [("name", "Name"), ("url", "https://server/dav"),
                      ("base", "Folder on the server (optional)"),
                      ("username", "Username"), ("password", "Password")]
        else:
            configured = remote_mod.rclone_remotes()
            fields = [("name", "Name"),
                      ("remote", "Remote name, e.g. "
                       + (configured[0] if configured else "pcloud:")),
                      ("path", "Folder on the remote (optional)")]
        for key, placeholder in fields:
            e = Gtk.Entry(placeholder_text=placeholder)
            if key == "password":
                e.set_visibility(False)
            entries[key] = e
            box.append(e)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("add", "Add")
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
            rid = f"{kind}-{abs(hash(str(values)))%10**8}"
            password = values.pop("password", "")
            cfg = {"id": rid, "name": values.get("name") or kind.title(),
                   "kind": kind,
                   "config": {k: v for k, v in values.items() if k != "name"}}
            if password:
                if not remote_mod.store_secret(rid, password):
                    # Refuse to silently write a password into the library
                    # folder, which the user has been told to back up.
                    self._toast("No keyring available — password not saved. "
                                "Use a mounted folder instead.")
            self._save_remote(cfg)
        dialog.connect("response", done)
        dialog.present(self.get_root())

    def _save_remote(self, cfg):
        remotes = list(self.settings.get("remotes", []) or [])
        remotes = [r for r in remotes if r.get("id") != cfg["id"]]
        remotes.append(cfg)
        self.settings.set("remotes", remotes)
        self._refresh_remotes()

    def _on_remove_remote(self, _btn, rid):
        remotes = [r for r in (self.settings.get("remotes") or [])
                   if r.get("id") != rid]
        self.settings.set("remotes", remotes)
        self._refresh_remotes()

    def _on_test_remote(self, btn, r, row):
        btn.set_sensitive(False)
        row.set_subtitle("Testing…")

        def work():
            result = r.backend().test()
            GLib.idle_add(finish, result)

        def finish(result):
            btn.set_sensitive(True)
            text = result.message
            if result.detail:
                text += f" — {result.detail}"
            if result.free_bytes:
                text += f" · {_fmt(result.free_bytes)} free"
            row.set_subtitle(text)
            return False
        threading.Thread(target=work, daemon=True).start()

    def _on_push_remote(self, btn, r, row):
        btn.set_sensitive(False)
        library = self.library

        def work():
            backend = r.backend()
            test = backend.test()
            if not test.ok:
                GLib.idle_add(finish, f"{test.message} — {test.detail}")
                return
            files = remote_mod.library_files(library.root)
            progress = backend.push(
                library.root, files,
                on_progress=lambda p: GLib.idle_add(
                    row.set_subtitle,
                    f"{p.phase} — {p.done_files}/{p.total_files}"
                    f" ({_fmt(p.done_bytes)})"))
            if progress.phase == "done":
                GLib.idle_add(
                    finish,
                    f"Backed up {progress.uploaded} file"
                    + ("s" if progress.uploaded != 1 else "")
                    + (f", {progress.skipped} already current"
                       if progress.skipped else "")
                    + (f", {progress.errors} failed" if progress.errors else ""))
            else:
                GLib.idle_add(finish,
                              progress.message or f"Stopped: {progress.phase}")

        def finish(text):
            btn.set_sensitive(True)
            row.set_subtitle(text)
            return False
        threading.Thread(target=work, daemon=True).start()

    def _toast(self, text):
        try:
            self.add_toast(Adw.Toast(title=text, timeout=6))
        except Exception:
            pass

    # ==================================================================
    def _library_page(self):
        page = Adw.PreferencesPage(title="Library", name="library",
                                   icon_name="folder-pictures-symbolic")
        group = Adw.PreferencesGroup(
            title="Location",
            description="Everything Piklin owns lives in this one "
                        "package, so copying it is a complete backup. Move it "
                        "anywhere, even another disk, and open it with "
                        "Open Library… in the main menu.")
        row = Adw.ActionRow(title="Library",
                            subtitle=str(self.library.root))
        row.set_subtitle_selectable(True)
        open_btn = Gtk.Button(icon_name="folder-open-symbolic",
                              valign=Gtk.Align.CENTER)
        open_btn.connect("clicked", self._on_open_library)
        row.add_suffix(open_btn)
        group.add(row)
        page.add(group)

        folders = Adw.PreferencesGroup(
            title="Watched folders",
            description="Photos in these folders are indexed where they "
                        "are. They are never moved or modified.")
        roots = self.catalog.roots(enabled_only=False)
        if not roots:
            folders.add(Adw.ActionRow(title="None yet"))
        for r in roots:
            n = self.catalog.scalar(
                "SELECT COUNT(*) FROM photos WHERE root_id=?", (r["id"],), 0)
            rr = Adw.ActionRow(title=r["path"], subtitle=f"{n:,} photos")
            drop = Gtk.Button(icon_name="list-remove-symbolic",
                              valign=Gtk.Align.CENTER,
                              tooltip_text="Stop watching (files are kept)")
            drop.connect("clicked", self._on_forget_root, r["id"])
            rr.add_suffix(drop)
            folders.add(rr)
        page.add(folders)

        maint = Adw.PreferencesGroup(
            title="Maintenance",
            description="catalog.db is an index, not your data. It can "
                        "always be rebuilt from your photos and the edit "
                        "files beside them.")
        dupes = Adw.ActionRow(
            title="Find duplicates",
            subtitle="Groups photos with identical content")
        dbtn = Gtk.Button(label="Scan", valign=Gtk.Align.CENTER)
        dbtn.connect("clicked", self._on_find_dupes, dupes)
        dupes.add_suffix(dbtn)
        maint.add(dupes)

        n_removed = len(self.catalog.removed_paths())
        removed = Adw.ActionRow(
            title="Removed from library",
            subtitle=(f"{n_removed:,} photo{'s' if n_removed != 1 else ''} you "
                      f"removed stay out, although the files are still in "
                      f"watched folders" if n_removed else
                      "Photos you remove stay out, even if their files are "
                      "still in a watched folder"))
        again = Gtk.Button(label="Show Again", valign=Gtk.Align.CENTER,
                           sensitive=bool(n_removed))
        again.connect("clicked", self._on_show_removed, removed)
        removed.add_suffix(again)
        maint.add(removed)

        rebuild = Adw.ActionRow(
            title="Rebuild index",
            subtitle="Run: piklin --rebuild-index")
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
        row.set_subtitle("Coming back with the scan that is running now")
        try:
            from .. import sidecars
            sidecars.write_removed(self.library, self.catalog)
            self._window._start_scan(None)
        except Exception:
            pass
        self._toast(f"{n:,} photo{'s' if n != 1 else ''} will be back in a moment.")

    def _on_forget_root(self, _btn, root_id):
        self.catalog.forget_root(root_id, drop_photos=True)
        self._toast("Folder removed from the library. No files were deleted.")

    def _on_find_dupes(self, btn, row):
        btn.set_sensitive(False)
        row.set_subtitle("Scanning…")

        def work():
            groups = self.catalog.duplicate_groups()
            extra = sum(len(g) - 1 for g in groups)
            waste = sum(sum(r["bytes"] for r in g[1:]) for g in groups)
            GLib.idle_add(finish, len(groups), extra, waste)

        def finish(n, extra, waste):
            btn.set_sensitive(True)
            row.set_subtitle(
                f"{n} group" + ("s" if n != 1 else "")
                + f", {extra} duplicate file" + ("s" if extra != 1 else "")
                + f" using {_fmt(waste)}" if n else "No duplicates found")
            return False
        threading.Thread(target=work, daemon=True).start()
