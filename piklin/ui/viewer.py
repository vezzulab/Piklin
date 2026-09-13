"""Full-frame photo view with pan and zoom."""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gdk, Gtk  # noqa: E402

import numpy as np

from .. import imageio as iio
from ..engine.stack import EditStack, Renderer
from ..thumbs import DETAIL_SIZE
from .player import VideoPlayer
from .render_thread import RenderThread, texture_from_array
from ..video import format_duration


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


class ViewerView(Gtk.Box):
    """One photo, large, with its metadata and the actions that apply."""

    __gsignals__ = {
        "closed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "edit-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "navigate": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }

    def __init__(self, library, catalog, thumbs, settings):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.library = library
        self.catalog = catalog
        self.thumbs = thumbs
        self.settings = settings
        self.item = None
        self._render = RenderThread(self._on_frame)
        self._zoom = 1.0

        self.bar = self._build_bar()
        self.append(self.bar)

        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN,
                                   can_shrink=True, hexpand=True, vexpand=True)
        self.scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        self.scroller.set_child(self.picture)
        self.scroller.add_css_class("pika-viewer")
        self.scroller.add_css_class("pika-stage")

        self.info = self._build_info()
        self.split = Adw.OverlaySplitView(
            sidebar_position=Gtk.PackType.END, collapsed=False,
            show_sidebar=False, sidebar_width_fraction=0.26)
        # Photos and videos share the stage; a video gets the player.
        self.player = VideoPlayer()
        self.player.add_css_class("pika-stage")
        self.player.connect("toggle-fullscreen",
                            lambda *_: self.toggle_fullscreen())
        self.stage = Gtk.Stack(transition_type=Gtk.StackTransitionType.NONE)
        photo_overlay = Gtk.Overlay()
        photo_overlay.set_child(self.scroller)
        # A live photo moves when LIVE is clicked, then settles on the still.
        self.live_btn = Gtk.Button(label="LIVE", halign=Gtk.Align.START,
                                   valign=Gtk.Align.START, margin_start=16,
                                   margin_top=14, visible=False,
                                   tooltip_text="Play the live photo")
        self.live_btn.add_css_class("pika-live-button")
        self.live_btn.connect("clicked", lambda *_: self._play_live())
        photo_overlay.add_overlay(self.live_btn)
        self._live_path = None
        self._live_playing = False
        self.stage.add_named(photo_overlay, "photo")
        self.stage.add_named(self.player, "video")
        self.split.set_content(self.stage)
        # Leaving the viewer stops the sound.
        self.connect("unmap", lambda *_: self.player.unload())
        self.player.connect("ended", self._on_player_ended)
        self.split.set_sidebar(self.info)
        self.split.set_vexpand(True)
        self.append(self.split)

        scroll = Gtk.EventControllerScroll(
            flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._on_scroll)
        self.scroller.add_controller(scroll)

        click = Gtk.GestureClick()
        click.connect("pressed", self._on_click)
        self.picture.add_controller(click)

    def _build_bar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4,
                      margin_top=6, margin_bottom=6, margin_start=10,
                      margin_end=10)
        back = Gtk.Button(icon_name="go-previous-symbolic",
                          tooltip_text="Back (Esc)")
        back.connect("clicked", lambda *_: self.emit("closed"))
        bar.append(back)

        prev = Gtk.Button(icon_name="pan-start-symbolic", tooltip_text="Previous")
        prev.connect("clicked", lambda *_: self.emit("navigate", -1))
        nxt = Gtk.Button(icon_name="pan-end-symbolic", tooltip_text="Next")
        nxt.connect("clicked", lambda *_: self.emit("navigate", 1))
        bar.append(prev)
        bar.append(nxt)

        self.name_label = Gtk.Label(xalign=0.5, hexpand=True)
        self.name_label.add_css_class("heading")
        bar.append(self.name_label)

        self.fav_btn = Gtk.ToggleButton(icon_name="starred-symbolic",
                                        tooltip_text="Favourite (F)")
        self.fav_btn.connect("toggled", self._on_favorite)
        bar.append(self.fav_btn)

        full = Gtk.Button(icon_name="view-fullscreen-symbolic",
                          tooltip_text="Full Screen (F11)")
        full.connect("clicked", lambda *_: self.toggle_fullscreen())
        bar.append(full)

        info_btn = Gtk.ToggleButton(icon_name="dialog-information-symbolic",
                                    tooltip_text="Info (Ctrl+I)")
        info_btn.connect("toggled",
                         lambda b: self.split.set_show_sidebar(b.get_active()))
        bar.append(info_btn)
        self.info_btn = info_btn

        self.edit_btn = edit = Gtk.Button(label="Edit")
        edit.add_css_class("suggested-action")
        edit.connect("clicked", lambda *_: self.emit("edit-requested"))
        bar.append(edit)

        trash = Gtk.Button(icon_name="user-trash-symbolic",
                           tooltip_text="Move to trash (Delete)")
        trash.connect("clicked", self._on_trash)
        bar.append(trash)
        # This bar replaces the library's header while a photo is open, so
        # it carries the window buttons too - same place, same style.
        controls = Gtk.WindowControls(side=Gtk.PackType.END)
        controls.set_decoration_layout(":minimize,maximize,close")
        bar.append(controls)
        # Adw.HeaderBar gets window-drag on its empty space for free;
        # this custom bar is a plain Box and does not, which is why the
        # window could not be moved at all while the editor or viewer was
        # open (the header/sidebar's HeaderBars only exist on the library
        # page - switching to this page replaces the whole window
        # content with this Box tree, leaving no draggable area anywhere
        # in the window). Gtk.WindowHandle wraps arbitrary content and
        # makes any part of it not already claimed by an interactive
        # child (a button, the title label) draggable, exactly like a
        # titlebar - it is the standard fix for a custom CSD top bar.
        handle = Gtk.WindowHandle()
        handle.set_child(bar)
        return handle

    def _build_info(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0,
                      margin_top=12, margin_bottom=12, margin_start=12,
                      margin_end=12)
        # What you can say about a photo comes first in the Info panel:
        # a title, a caption, keywords. Each saves when you press
        # Return or the check button - the photo file itself is never
        # written.
        self.words_group = Adw.PreferencesGroup()
        self._text_rows = {}
        for key, label in (("title", "Add a Title"),
                           ("caption", "Add a Caption"),
                           ("keywords", "Add Keywords")):
            row = Adw.EntryRow(title=label, show_apply_button=True)
            row.connect("apply", self._on_text_apply, key)
            row.connect("entry-activated", self._on_text_apply, key)
            self.words_group.add(row)
            self._text_rows[key] = row
        box.append(self.words_group)

        self.info_group = Adw.PreferencesGroup(title="Photo", margin_top=18)
        box.append(self.info_group)
        self._info_rows = {}
        for key, label in (("filename", "File"), ("dimensions", "Dimensions"),
                           ("duration", "Length"),
                           ("size", "Size"), ("taken", "Taken"),
                           ("camera", "Camera"), ("lens", "Lens"),
                           ("exposure", "Exposure"), ("location", "Location"),
                           ("albums", "Albums"),
                           ("edits", "Edits"), ("path", "Folder")):
            row = Adw.ActionRow(title=label, subtitle="—")
            row.set_subtitle_selectable(True)
            if key in ("taken", "location"):
                # Adjust Date and Time / Adjust Location.
                edit = Gtk.Button(icon_name="document-edit-symbolic",
                                  valign=Gtk.Align.CENTER,
                                  tooltip_text=("Adjust Date and Time" if key == "taken"
                                                else "Adjust Location"))
                edit.add_css_class("flat")
                edit.connect("clicked", self._on_adjust_date if key == "taken"
                             else self._on_adjust_location)
                row.add_suffix(edit)
            elif key == "filename":
                rename = Gtk.Button(icon_name="document-edit-symbolic",
                                    valign=Gtk.Align.CENTER,
                                    tooltip_text="Rename (F2)")
                rename.add_css_class("flat")
                rename.connect("clicked", self.rename_file)
                row.add_suffix(rename)
            self.info_group.add(row)
            self._info_rows[key] = row
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      vexpand=True)
        scroller.set_child(box)
        return scroller

    def is_fullscreen(self) -> bool:
        return self.has_css_class("pika-fullscreen")

    def toggle_fullscreen(self, on: bool | None = None):
        """The photo alone on black, filling the screen: no bar, no Info.
        F11, the toolbar button, or Escape to come back."""
        win = self.get_root()
        on = (not self.is_fullscreen()) if on is None else on
        if on:
            self._info_was_open = self.info_btn.get_active()
            self.info_btn.set_active(False)
            self.bar.set_visible(False)
            self.add_css_class("pika-fullscreen")
            if win is not None:
                win.fullscreen()
        else:
            self.remove_css_class("pika-fullscreen")
            self.bar.set_visible(True)
            if getattr(self, "_info_was_open", False):
                self.info_btn.set_active(True)
            if win is not None:
                win.unfullscreen()
        self._zoom = 1.0
        self._apply_zoom()

    def toggle_info(self):
        self.info_btn.set_active(not self.info_btn.get_active())

    # -- loading ---------------------------------------------------------
    def show_photo(self, item):
        self.item = item
        self.name_label.set_text(item.filename)
        self.fav_btn.set_active(item.favorite)
        if getattr(item, "is_video", False):
            self._live_playing = False
            self.player.set_chrome(True)
            self.stage.set_visible_child_name("video")
            self.edit_btn.set_visible(True)
            # The library shows a video as it was edited.
            from ..video_edit import load as load_edit
            edit = load_edit(self.library, item.path, item.duration or 0.0)
            self.player.set_edit(None if edit.is_identity() else edit)
            poster = None
            thumb = self.thumbs.get_path(item.path)
            if thumb:
                try:
                    poster = Gdk.Texture.new_from_filename(str(thumb))
                except Exception:
                    poster = None
            self.player.load(item.path, item.width or 0, item.height or 0,
                             0.0, item.duration or 0.0, poster)
            if not edit.is_identity():
                self.player.set_rate(edit.speed)
                self.player.mute_btn.set_active(edit.mute)
            self._update_info()
            return
        self.player.unload()
        self.stage.set_visible_child_name("photo")
        self.edit_btn.set_visible(True)
        self._live_playing = False
        live = (self.catalog.live_video_for(item.id)
                if getattr(item, "has_live", False) else None)
        self._live_path = live["path"] if live is not None else None
        self.live_btn.set_visible(live is not None)
        self._zoom = 1.0
        # _apply_zoom() must run here, not just resetting the _zoom
        # variable: zooming in sets an explicit pixel size_request and
        # switches content_fit to FILL on the Picture widget itself, and
        # neither of those is tied to which photo is loaded. Without
        # this, opening a photo after having zoomed into a previous one
        # left that oversized request in place, so the new photo opened
        # already blown up / stretched to the old zoom level instead of
        # fitted to the window.
        self._apply_zoom()
        self._update_info()

        # Show the cached thumbnail immediately, then replace it with the
        # real decode: opening a photo should never present a blank frame.
        thumb = self.thumbs.get_path(item.path)
        if thumb:
            try:
                self.picture.set_paintable(Gdk.Texture.new_from_filename(str(thumb)))
            except Exception:
                pass

        path = Path(item.path)
        library = self.library
        max_side = max(1600, int(self.settings.get("preview_max_side", 1600)))

        def work():
            img = iio.load_rgb(path, max_side=max_side)
            stack = EditStack.load(library.edit_sidecar(path))
            if len(stack):
                h, w = img.shape[:2]
                img = Renderer(max_cached=1).render(
                    img, stack, scale=1.0, full_size=(w, h))
            return img
        self._render.submit(work, tag=str(path))

    def _on_frame(self, result, tag, elapsed):
        if result is None or self.item is None:
            return
        if tag != str(self.item.path):
            return                      # user moved on before this finished
        self.picture.set_paintable(texture_from_array(result))

    def _update_info(self):
        if self.item is None:
            return
        row = self.catalog.photo(self.item.id)
        if row is None:
            return
        def put(key, value):
            self._info_rows[key].set_subtitle(str(value) if value else "—")
        put("filename", row["filename"])
        length = row["duration"] if "duration" in row.keys() else None
        self._info_rows["duration"].set_visible(bool(length))
        # A video has no lens or exposure to show; the group says what it is.
        video = bool(getattr(self.item, "is_video", False))
        self.info_group.set_title("Video" if video else "Photo")
        for key in ("lens", "exposure"):
            self._info_rows[key].set_visible(not video)
        put("duration", format_duration(length) if length else None)
        put("dimensions", f"{row['width']} x {row['height']}"
            if row["width"] else None)
        mp = (row["width"] * row["height"] / 1e6) if row["width"] else 0
        put("size", f"{_fmt_bytes(row['bytes'])}"
            + (f"  ·  {mp:.1f} MP" if mp else ""))
        if row["taken_at"]:
            put("taken", datetime.fromtimestamp(row["taken_at"])
                .strftime("%A %d %B %Y, %H:%M")
                + ("" if row["date_source"] == "exif" else
                   f"  (from {row['date_source']})"))
        camera = " ".join(x for x in (row["camera_make"], row["camera_model"]) if x)
        put("camera", camera)
        put("lens", row["lens"])
        bits = []
        if row["exposure"]:
            e = row["exposure"]
            bits.append(f"1/{1/e:.0f}s" if e < 1 else f"{e:.1f}s")
        if row["f_number"]:
            bits.append(f"f/{row['f_number']:.1f}")
        if row["iso"]:
            bits.append(f"ISO {row['iso']}")
        if row["focal_length"]:
            bits.append(f"{row['focal_length']:.0f}mm")
        put("exposure", "  ·  ".join(bits))
        if row["gps_lat"] is not None and row["gps_lon"] is not None:
            put("location", f"{row['gps_lat']:.5f}, {row['gps_lon']:.5f}")
        else:
            put("location", None)
        put("edits", f"{row['edit_version']} adjustment"
            + ("s" if row["edit_version"] != 1 else "")
            if row["edit_version"] else "None — original")
        put("path", str(Path(row["path"]).parent))
        albums = self.catalog.albums_for_photo(self.item.id)
        put("albums", ", ".join(a["name"] for a in albums))
        keys = row.keys()
        for key, entry in self._text_rows.items():
            value = (row[key] if key in keys else None) or ""
            if entry.get_text() != value:
                entry.set_text(value)

    # -- actions ---------------------------------------------------------
    def _ask(self, heading, body, fields, on_ok, extra=None):
        """A small form in an alert dialog: labelled entries and OK."""
        dialog = Adw.AlertDialog(heading=heading, body=body)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        entries = []
        for placeholder, value in fields:
            e = Gtk.Entry(placeholder_text=placeholder, text=value or "",
                          activates_default=True)
            box.append(e)
            entries.append(e)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        if extra:
            dialog.add_response("extra", extra[0])
        dialog.add_response("ok", "Adjust")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("ok")
        dialog.set_close_response("cancel")

        def done(_d, response):
            if response == "ok":
                on_ok([e.get_text().strip() for e in entries])
            elif response == "extra" and extra:
                extra[1]()
        dialog.connect("response", done)
        dialog.present(self)

        def focus():
            if entries:
                entries[0].grab_focus()
            return GLib.SOURCE_REMOVE
        GLib.idle_add(focus, priority=GLib.PRIORITY_HIGH)

    def _on_adjust_date(self, _btn):
        if self.item is None:
            return
        row = self.catalog.photo(self.item.id)
        current = (datetime.fromtimestamp(row["taken_at"]).strftime("%Y-%m-%d %H:%M")
                   if row and row["taken_at"] else "")

        def ok(values):
            try:
                ts = datetime.strptime(values[0], "%Y-%m-%d %H:%M").timestamp()
            except ValueError:
                try:
                    ts = datetime.strptime(values[0], "%Y-%m-%d").timestamp()
                except ValueError:
                    return
            self.catalog.set_taken_at([self.item.id], ts)
            self._update_info()
            self.emit("changed")
        self._ask("Adjust Date and Time",
                  "The photo file is not changed; the new date is kept in your "
                  "library.", [("YYYY-MM-DD HH:MM", current)], ok)

    def _on_adjust_location(self, _btn):
        if self.item is None:
            return
        row = self.catalog.photo(self.item.id)
        current = (f"{row['gps_lat']:.5f}, {row['gps_lon']:.5f}"
                   if row and row["gps_lat"] is not None else "")

        def ok(values):
            try:
                lat, lon = (float(v) for v in values[0].replace(";", ",").split(","))
            except ValueError:
                return
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                return
            self.catalog.set_location([self.item.id], lat, lon)
            self._update_info()
            self.emit("changed")

        def remove():
            self.catalog.set_location([self.item.id], None, None)
            self._update_info()
            self.emit("changed")
        self._ask("Adjust Location",
                  "Latitude and longitude, for example 40.41680, -3.70380.",
                  [("latitude, longitude", current)], ok,
                  extra=("Remove Location", remove))

    def _play_live(self):
        if not self._live_path:
            return
        self._live_playing = True
        self.player.set_edit(None)
        self.player.set_chrome(False)
        self.stage.set_visible_child_name("video")
        self.player.load(self._live_path)
        self.player.toggle()

    def _on_player_ended(self, _player):
        if self._live_playing:
            self._live_playing = False
            self.player.unload()
            self.player.set_chrome(True)
            self.stage.set_visible_child_name("photo")

    def rename_file(self, _btn=None):
        """Rename the photo's file on disk (the Info window's File row, F2)."""
        if self.item is None:
            return
        from .rename_dialog import ask_rename_photo

        def renamed(new_path):
            self.item.path = str(new_path)
            self.item.filename = new_path.name
            self.name_label.set_text(new_path.name)
            self._update_info()
            self.emit("changed")
        ask_rename_photo(self, self.library, self.catalog, self.item.id,
                         renamed)

    def _on_text_apply(self, row, key):
        if self.item is None:
            return
        self.catalog.set_text_fields([self.item.id], **{key: row.get_text()})
        self.emit("changed")
    def _on_favorite(self, btn):
        if self.item is None:
            return
        self.catalog.set_favorite([self.item.id], btn.get_active())
        self.item.favorite = btn.get_active()
        self.emit("changed")

    def _on_trash(self, _btn):
        if self.item is None:
            return
        self.catalog.trash([self.item.id])
        self.emit("changed")
        self.emit("closed")

    def _on_scroll(self, controller, dx, dy):
        state = controller.get_current_event_state()
        if not (state & Gdk.ModifierType.CONTROL_MASK):
            return False
        self._zoom = max(1.0, min(8.0, self._zoom * (0.9 if dy > 0 else 1.1)))
        self._apply_zoom()
        return True

    def _on_click(self, gesture, n_press, x, y):
        if n_press >= 2:
            self._zoom = 1.0 if self._zoom > 1.0 else 2.5
            self._apply_zoom()

    def _apply_zoom(self):
        paintable = self.picture.get_paintable()
        if paintable is None:
            return
        if self._zoom <= 1.001:
            self.picture.set_size_request(-1, -1)
            self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)
            self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
        else:
            w = self.scroller.get_width() or 800
            h = self.scroller.get_height() or 600
            self.picture.set_content_fit(Gtk.ContentFit.FILL)
            self.picture.set_size_request(int(w * self._zoom),
                                          int(h * self._zoom))
            self.scroller.set_policy(Gtk.PolicyType.AUTOMATIC,
                                     Gtk.PolicyType.AUTOMATIC)

    def shutdown(self):
        self._render.stop()
