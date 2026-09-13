"""Export, with the compression choice made concrete.

Every profile is measured on the actual photos being exported before the
user commits, so the dialog can say "this saves 63%, and here is the
quality score" instead of asking them to guess at a number.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import gi
from ..i18n import _, ngettext

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gio, Gtk  # noqa: E402

from .. import compress as cz


def _fmt(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


class ExportDialog(Adw.Dialog):
    def __init__(self, parent, library, settings, paths, stack=None,
                 catalog=None):
        super().__init__(title=_("Export"), content_width=520,
                         content_height=620)
        self.library = library
        self.settings = settings
        self.paths = [Path(p) for p in paths]
        self.stack = stack
        self.catalog = catalog
        self.destination = library.exports / time.strftime("%Y-%m-%d")
        self._estimating = False

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        # Cancel and Escape already close this sheet; a lone close dot in
        # its corner was a second, inconsistent way to do the same thing.
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)
        toolbar.add_top_bar(header)

        page = Adw.PreferencesPage()

        from ..video import is_video
        n_videos = sum(1 for q in self.paths if is_video(q))
        n_photos = len(self.paths) - n_videos
        counted = []
        if n_photos:
            counted.append(ngettext("{count} photo", "{count} photos",
                                    n_photos).format(count=n_photos))
        if n_videos:
            counted.append(ngettext("{count} video", "{count} videos",
                                    n_videos).format(count=n_videos))
        if len(counted) == 2:
            heading = _("{photos} and {videos}").format(photos=counted[0], videos=counted[1])
        else:
            heading = counted[0] if counted else _("Nothing")
        target = Adw.PreferencesGroup(title=heading)
        self.dest_row = Adw.ActionRow(title=_("Save to"),
                                      subtitle=str(self.destination))
        choose = Gtk.Button(label=_("Change…"), valign=Gtk.Align.CENTER)
        choose.connect("clicked", self._on_choose_dest)
        self.dest_row.add_suffix(choose)
        target.add(self.dest_row)
        page.add(target)

        self.group = Adw.PreferencesGroup(
            title=_("File Size"),
            description=_("Piklin tries each option on your photos to show how much smaller "
                          "they get."))
        self._rows = {}
        first = None
        current = settings.get("export_profile", cz.DEFAULT_PROFILE)
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
            badge = Gtk.Label(label="", valign=Gtk.Align.CENTER)
            badge.add_css_class("pika-savings")
            row.add_suffix(badge)
            row._badge = badge
            self._rows[pid] = row
            self.group.add(row)
        page.add(self.group)

        fmt_group = Adw.PreferencesGroup(title=_("Format"))
        self.fmt_row = Adw.ComboRow(
            title=_("File type"),
            model=Gtk.StringList.new(
                [_("Same as original"), "JPEG", "WebP", "AVIF", "PNG"]))
        fmt_group.add(self.fmt_row)
        # Size: the long edge, never enlarged.
        self._sizes = [None, 2048, 1280, 640]
        self.size_row = Adw.ComboRow(
            title=_("Size"),
            model=Gtk.StringList.new([_("Full Size"), _("Large (2048 px)"),
                                      _("Medium (1280 px)"), _("Small (640 px)")]))
        self.size_row.set_selected(int(settings.get("export_size", 0)))
        fmt_group.add(self.size_row)
        page.add(fmt_group)
        if not n_photos:
            # photo compression and file types mean nothing for videos alone
            self.group.set_visible(False)
            fmt_group.set_visible(False)

        # Videos: rendered with their edits, in a chosen
        # format and size; an unedited video in its own format is copied.
        from .. import video_edit as ve
        self._video_formats = ["original"] + ve.available_formats()
        self._video_sizes = [None, 3840, 1920, 1280, 854]
        video_group = Adw.PreferencesGroup(
            title=_("Video"),
            description=_("Edited videos are saved with your trims, cuts and changes. The "
                          "original files are never changed."))
        labels = {"original": _("Same as Original")}
        labels.update({k: _(v["label"]) for k, v in ve.EXPORT_FORMATS.items()})
        self.vformat_row = Adw.ComboRow(
            title=_("Format"), model=Gtk.StringList.new(
                [labels[f] for f in self._video_formats]))
        saved_fmt = settings.get("export_video_format", "original")
        if saved_fmt in self._video_formats:
            self.vformat_row.set_selected(self._video_formats.index(saved_fmt))
        video_group.add(self.vformat_row)
        self.vsize_row = Adw.ComboRow(
            title=_("Video Quality"), model=Gtk.StringList.new(
                [_("Original"), "4K", "1080p", "720p", "480p"]))
        self.vsize_row.set_selected(int(settings.get("export_video_size", 0)))
        video_group.add(self.vsize_row)
        video_group.set_visible(bool(n_videos))
        page.add(video_group)

        # What travels with the copy. Location is off by default: a shared
        # photo should not say where you live unless you choose that.
        info_group = Adw.PreferencesGroup(title=_("Include"))
        self.location_row = Adw.SwitchRow(
            title=_("Location"),
            subtitle=_("Where it was taken (GPS)"),
            active=bool(settings.get("export_include_location", False)))
        info_group.add(self.location_row)
        self.strip_row = Adw.SwitchRow(
            title=_("Remove all hidden details"),
            subtitle=_("Also removes the camera and the date it was taken"),
            active=bool(settings.get("export_strip_metadata", False)))
        info_group.add(self.strip_row)
        page.add(info_group)

        naming_group = Adw.PreferencesGroup(title=_("File Names"))
        self._namings = ["filename", "title", "sequential"]
        self.name_row = Adw.ComboRow(
            title=_("File Name"),
            model=Gtk.StringList.new([_("Use File Name"), _("Use Title"), _("Numbered")]))
        self.name_row.set_selected(self._namings.index(
            settings.get("export_naming", "filename"))
            if settings.get("export_naming", "filename") in self._namings else 0)
        naming_group.add(self.name_row)
        self._subfolders = ["none", "day"]
        self.subfolder_row = Adw.ComboRow(
            title=_("Put in Folders by"),
            model=Gtk.StringList.new([_("No Folders"), _("Date")]))
        self.subfolder_row.set_selected(1 if settings.get("export_subfolder") == "day" else 0)
        naming_group.add(self.subfolder_row)
        page.add(naming_group)

        self.status = Adw.PreferencesGroup()
        self.status_row = Adw.ActionRow(
            title=_("Measuring…"),
            subtitle=_("Trying each option on a few of your photos"))
        self.progress = Gtk.ProgressBar(valign=Gtk.Align.CENTER,
                                        show_text=False)
        self.status_row.add_suffix(self.progress)
        self.status.add(self.status_row)
        page.add(self.status)

        toolbar.set_content(page)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                          margin_top=8, margin_bottom=10, margin_start=12,
                          margin_end=12, halign=Gtk.Align.END)
        # Export Unmodified Original: the file as imported, byte for byte -
        # no edits, no re-encoding, nothing removed.
        unmodified = Gtk.Button(label=_("Export Original Without Edits"))
        unmodified.add_css_class("flat")
        unmodified.connect("clicked", self._on_export_unmodified)
        actions.append(unmodified)
        spacer = Gtk.Box(hexpand=True)
        actions.append(spacer)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda *_: self.close())
        actions.append(cancel)
        self.export_btn = Gtk.Button(label=_("Export"))
        self.export_btn.add_css_class("suggested-action")
        self.export_btn.connect("clicked", self._on_export)
        actions.append(self.export_btn)
        toolbar.add_bottom_bar(actions)

        self.set_child(toolbar)
        self._start_estimate()

    # -- estimation ------------------------------------------------------
    def _start_estimate(self):
        if self._estimating:
            return
        self._estimating = True
        sample = self.paths[:6]

        def work():
            for pid in cz.PROFILES:
                if pid == "original":
                    GLib.idle_add(self._set_badge, pid, "unchanged")
                    continue
                total_in = total_out = 0
                ssim = []
                for p in sample:
                    try:
                        r = cz.plan(p, pid)
                    except Exception:
                        continue
                    if r.ok and r.original_bytes:
                        total_in += r.original_bytes
                        total_out += r.output_bytes
                        if r.metrics.get("ssim"):
                            ssim.append(r.metrics["ssim"])
                if total_in:
                    pct = (1 - total_out / total_in) * 100
                    q = ("  ·  " + _("{percent}% match").format(
                        percent=f"{min(ssim)*100:.1f}")) if ssim else ""
                    GLib.idle_add(self._set_badge, pid,
                                  (_("no saving") if pct < 0.5
                                   else f"−{pct:.0f}%{q}"))
            GLib.idle_add(self._estimate_done)
        threading.Thread(target=work, daemon=True).start()

    def _set_badge(self, pid, text):
        row = self._rows.get(pid)
        if row:
            row._badge.set_text(text)
        return False

    def _estimate_done(self):
        self._estimating = False
        self.status_row.set_title(_("Ready"))
        self.status_row.set_subtitle(
            _("Sizes measured on a few of the selected photos"))
        self.progress.set_fraction(0.0)
        return False

    def _on_profile(self, check, pid):
        if check.get_active():
            self.settings.set("export_profile", pid)

    def _on_choose_dest(self, _btn):
        """Pick a destination without leaving the window.

        The system folder chooser is a separate toplevel; exporting is
        part of the editing flow, which stays inside the app.  The common
        destinations are offered directly and anything else can be typed.
        """
        import os
        home = Path.home()
        choices = [
            (str(self.library.exports / time.strftime("%Y-%m-%d")),
             _("Library exports, filed by date")),
            (str(home / "Pictures"), _("Your pictures folder")),
            (str(home / "Desktop"), _("Desktop")),
            (str(home / "Downloads"), _("Downloads")),
            (str(home), _("Home folder")),
        ]
        choices = [(p, d) for p, d in choices
                   if Path(p).parent.is_dir() or Path(p).is_dir()]

        dialog = Adw.AlertDialog(
            heading=_("Export to"),
            body=_("Choose where the exported photos should go."))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        group = Adw.PreferencesGroup()
        first = None
        selected = {"path": str(self.destination)}
        for path, desc in choices:
            row = Adw.ActionRow(title=Path(path).name or path, subtitle=desc)
            check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            if first is None:
                first = check
                check.set_active(True)
                selected["path"] = path
            else:
                check.set_group(first)
            check.connect(
                "toggled",
                lambda c, p=path: selected.__setitem__("path", p)
                if c.get_active() else None)
            row.add_prefix(check)
            group.add(row)
        box.append(group)

        custom = Gtk.Entry(placeholder_text=_("…or type another folder path"))
        box.append(custom)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("use", _("Use Folder"))
        dialog.set_close_response("cancel")
        dialog.set_response_appearance("use", Adw.ResponseAppearance.SUGGESTED)
        # See the same fix in window.py's New Album dialog: without an
        # explicit grab_focus after the dialog is mapped, typing into
        # this field does nothing because focus stays on the response
        # button. This dialog also has several radio rows above the
        # entry, which makes it worse - Up/Down would move between them
        # instead of the field ever seeing a keystroke.
        def _focus_path_field_once():
            custom.grab_focus()
            return GLib.SOURCE_REMOVE      # run once, not forever
        GLib.idle_add(_focus_path_field_once, priority=GLib.PRIORITY_HIGH)

        def done(d, response):
            if response != "use":
                return
            typed = custom.get_text().strip()
            chosen = Path(typed).expanduser() if typed else Path(selected["path"])
            self.destination = chosen
            self.dest_row.set_subtitle(str(chosen))
        dialog.connect("response", done)
        dialog.present(self.get_root())

    # -- export ----------------------------------------------------------
    def _on_export(self, _btn):
        profile = self.settings.get("export_profile", cz.DEFAULT_PROFILE)
        fmt = ["keep", "jpeg", "webp", "avif", "png"][
            self.fmt_row.get_selected()]
        strip = self.strip_row.get_active()
        self.settings.set("export_strip_metadata", strip)
        keep_location = self.location_row.get_active()
        self.settings.set("export_include_location", keep_location)
        max_side = self._sizes[self.size_row.get_selected()]
        self.settings.set("export_size", self.size_row.get_selected())
        naming = self._namings[self.name_row.get_selected()]
        subfolder = self._subfolders[self.subfolder_row.get_selected()]
        self.settings.set("export_naming", naming)
        self.settings.set("export_subfolder", subfolder)
        video_fmt = self._video_formats[self.vformat_row.get_selected()]
        video_side = self._video_sizes[self.vsize_row.get_selected()]
        self.settings.set("export_video_format", video_fmt)
        self.settings.set("export_video_size", self.vsize_row.get_selected())
        video_meta = self._video_meta()
        meta = self._photo_meta()
        dest = self.destination
        paths = list(self.paths)
        stack = self.stack
        library = self.library
        self.export_btn.set_sensitive(False)
        self.status_row.set_title(_("Exporting…"))

        def work():
            done = saved_in = saved_out = 0
            errors = 0
            for i, p in enumerate(paths):
                try:
                    from ..video import is_video
                    if is_video(p):
                        # Videos never go through the photo encoder: an
                        # unedited one in its own format is copied, anything
                        # else is rendered with its edit.
                        import shutil
                        from .. import video_edit as ve
                        title, taken = meta.get(str(p), (None, None))
                        out = cz.export_target(dest, p, i, naming=naming,
                                               subfolder=subfolder,
                                               title=title, taken_at=taken)
                        out.parent.mkdir(parents=True, exist_ok=True)
                        vm = video_meta.get(str(p), {})
                        edit = ve.load(library, p, vm.get("duration") or 0.0)
                        if video_fmt == "original" and edit.is_identity() and not video_side:
                            shutil.copy2(p, out)
                        else:
                            fmt_here = "mp4" if video_fmt == "original" else video_fmt
                            if not edit.duration:
                                from ..video import stream_info
                                edit.duration = (stream_info(p) or {}).get("duration", 0.0)
                            base = (i, len(paths))

                            def step(frac, base=base):
                                GLib.idle_add(self.progress.set_fraction,
                                              (base[0] + frac) / base[1])
                            out = ve.export(p, out.with_suffix(""), edit, fmt=fmt_here,
                                            max_side=video_side,
                                            keep_location=keep_location,
                                            strip_metadata=strip,
                                            meta={"taken_at": taken, "title": title,
                                                  "location": vm.get("location")},
                                            on_progress=step)
                        size = out.stat().st_size
                        done += 1
                        saved_in += size
                        saved_out += size
                        GLib.idle_add(self.progress.set_fraction,
                                      (i + 1) / len(paths))
                        continue
                    image = None
                    sidecar = library.edit_sidecar(p)
                    use_stack = stack
                    if use_stack is None and sidecar.exists():
                        from ..engine.stack import EditStack
                        use_stack = EditStack.load(sidecar)
                    if use_stack is not None and len(use_stack):
                        from ..engine.stack import render_full
                        image = render_full(p, use_stack)
                    title, taken = meta.get(str(p), (None, None))
                    out = cz.export_target(dest, p, i, naming=naming,
                                           subfolder=subfolder, title=title,
                                           taken_at=taken)
                    r = cz.compress_to(p, out, profile, fmt, image=image,
                                       strip_metadata=strip,
                                       strip_location=not keep_location,
                                       max_side=max_side)
                    if r.ok:
                        done += 1
                        saved_in += r.original_bytes
                        saved_out += r.output_bytes
                    else:
                        errors += 1
                except Exception:
                    errors += 1
                GLib.idle_add(self.progress.set_fraction, (i + 1) / len(paths))
            GLib.idle_add(self._export_done, done, errors, saved_in, saved_out)
        threading.Thread(target=work, daemon=True).start()

    def _video_meta(self):
        """Length and location per video, for rendering its export."""
        out = {}
        if self.catalog is None:
            return out
        for p in self.paths:
            row = self.catalog.photo_by_path(str(p))
            if row is not None:
                keys = row.keys()
                loc = ((row["gps_lat"], row["gps_lon"])
                       if row["gps_lat"] is not None else None)
                out[str(p)] = {"duration": row["duration"] if "duration" in keys else None,
                               "location": loc}
        return out

    def _photo_meta(self):
        """Title and capture date per path, for naming and subfolders."""
        out = {}
        if self.catalog is None:
            return out
        for p in self.paths:
            row = self.catalog.photo_by_path(str(p))
            if row is not None:
                keys = row.keys()
                out[str(p)] = (row["title"] if "title" in keys else None,
                               row["taken_at"])
        return out

    def _on_export_unmodified(self, _btn):
        import shutil
        dest = self.destination
        paths = list(self.paths)
        naming = self._namings[self.name_row.get_selected()]
        subfolder = self._subfolders[self.subfolder_row.get_selected()]
        meta = self._photo_meta()
        self.export_btn.set_sensitive(False)
        self.status_row.set_title(_("Exporting originals…"))

        def work():
            done = errors = total = 0
            for i, p in enumerate(paths):
                try:
                    title, taken = meta.get(str(p), (None, None))
                    out = cz.export_target(dest, p, i, naming=naming,
                                           subfolder=subfolder, title=title,
                                           taken_at=taken)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, out)
                    done += 1
                    total += out.stat().st_size
                except Exception:
                    errors += 1
                GLib.idle_add(self.progress.set_fraction, (i + 1) / len(paths))
            GLib.idle_add(self._export_done, done, errors, total, total)
        threading.Thread(target=work, daemon=True).start()

    def _export_done(self, done, errors, size_in, size_out):
        self.export_btn.set_sensitive(True)
        pct = (1 - size_out / size_in) * 100 if size_in else 0
        self.status_row.set_title(
            ngettext("Exported {count} item", "Exported {count} items",
                     done).format(count=done))
        detail = _("to {folder}").format(folder=self.destination)
        if size_in:
            detail += f"  ·  {_fmt(size_in)} → {_fmt(size_out)} ({pct:+.0f}%)"
        if errors:
            detail += "  ·  " + ngettext("{count} failed", "{count} failed",
                                         errors).format(count=errors)
        self.status_row.set_subtitle(detail)
        self.progress.set_fraction(1.0)
        return False
