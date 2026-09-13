"""Editing a video: the same promise as a photo - the file is never touched.

Layout follows the photo editor: the picture on the dark stage with a bar
above it, the tools in a panel on the right. Under the picture sits the
timeline, which is where most of the editing happens:

* drag the white handles at either end to trim
* Shift-drag (or Mark In / Mark Out) to select a piece, then Cut Out
* click anywhere to move the playhead

Every change is saved at once beside the video (see video_edit.py) and can
be undone; the player previews exactly what an export will produce.
"""
from __future__ import annotations

import copy
import threading
from pathlib import Path

import cairo
import gi
from ..i18n import _, N_

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, GObject, Gtk  # noqa: E402

from .. import video_edit as ve
from ..video import format_duration, frame_at, stream_info
from .player import VideoPlayer

SPEEDS = (0.25, 0.5, 1.0, 1.5, 2.0)
CROPS = ((N_("Original"), None), (N_("Square"), 1.0), ("16:9", 16 / 9), ("9:16", 9 / 16),
         ("4:3", 4 / 3), ("4:5", 4 / 5))


class VideoTimeline(Gtk.DrawingArea):
    """Filmstrip with trim handles, cut pieces, a selection and the playhead."""

    __gsignals__ = {
        "seek": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        "trim": (GObject.SignalFlags.RUN_FIRST, None, (float, float)),
        "trim-done": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "select": (GObject.SignalFlags.RUN_FIRST, None, (float, float)),
    }
    PAD = 14            # room for the handles at both ends
    STRIP_TOP = 6
    STRIP_H = 52

    def __init__(self):
        super().__init__(hexpand=True, content_height=self.STRIP_TOP * 2 + self.STRIP_H)
        self.add_css_class("pika-timeline")
        self.duration = 0.0
        self.edit: ve.VideoEdit | None = None
        self.position = 0.0
        self.selection: tuple[float, float] | None = None
        self._surfaces: list[cairo.ImageSurface | None] = []
        self._aspect = 16 / 9
        self._job = 0
        self._drag = None
        self.set_draw_func(self._draw)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_begin)
        drag.connect("drag-update", self._on_update)
        drag.connect("drag-end", self._on_end)
        self.add_controller(drag)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self.add_controller(motion)

    # -- content -----------------------------------------------------------
    def set_video(self, path, duration: float, aspect: float):
        self.duration = max(0.0, duration)
        self._aspect = aspect or 16 / 9
        self._job += 1
        job = self._job
        count = 14
        self._surfaces = [None] * count
        self.queue_draw()

        def work():
            for i in range(count):
                if job != self._job:
                    return
                t = (i + 0.5) * self.duration / count if self.duration else 0.0
                try:
                    im = frame_at(path, t, max_side=180).convert("RGBA")
                except Exception:
                    continue
                w, h = im.size
                data = bytearray(im.tobytes("raw", "BGRa"))
                GLib.idle_add(self._set_surface, job, i, data, w, h)
        threading.Thread(target=work, daemon=True, name="filmstrip").start()

    def _set_surface(self, job, i, data, w, h):
        if job == self._job and i < len(self._surfaces):
            stride = cairo.ImageSurface.format_stride_for_width(cairo.FORMAT_ARGB32, w)
            if stride != w * 4:
                return GLib.SOURCE_REMOVE
            self._surfaces[i] = cairo.ImageSurface.create_for_data(
                data, cairo.FORMAT_ARGB32, w, h, stride)
            self.queue_draw()
        return GLib.SOURCE_REMOVE

    def set_edit(self, edit):
        self.edit = edit
        self.queue_draw()

    def set_position(self, t: float):
        if abs(t - self.position) > 1e-3:
            self.position = t
            self.queue_draw()

    def set_selection(self, sel):
        self.selection = sel
        self.queue_draw()

    # -- geometry ----------------------------------------------------------
    def _x(self, t: float) -> float:
        w = self.get_width() - 2 * self.PAD
        return self.PAD + (w * t / self.duration if self.duration else 0.0)

    def _t(self, x: float) -> float:
        w = max(1.0, self.get_width() - 2 * self.PAD)
        return min(self.duration, max(0.0, (x - self.PAD) / w * self.duration))

    def _hit(self, x: float) -> str | None:
        if self.edit is None:
            return None
        if abs(x - self._x(self.edit.start)) <= 10:
            return "start"
        if abs(x - self._x(self.edit.stop)) <= 10:
            return "end"
        return None

    # -- drawing -----------------------------------------------------------
    def _draw(self, _area, cr, width, height):
        top, sh = self.STRIP_TOP, self.STRIP_H
        left, right = self.PAD, width - self.PAD
        span = max(1.0, right - left)

        # filmstrip
        cr.save()
        _round(cr, left, top, span, sh, 6)
        cr.clip()
        cr.set_source_rgb(0.08, 0.08, 0.09)
        cr.paint()
        n = len(self._surfaces)
        if n:
            cell = span / n
            for i, surf in enumerate(self._surfaces):
                if surf is None:
                    continue
                sw, shh = surf.get_width(), surf.get_height()
                scale = max(cell / sw, sh / shh)
                cr.save()
                cr.rectangle(left + i * cell, top, cell + 0.5, sh)
                cr.clip()
                cr.translate(left + i * cell + (cell - sw * scale) / 2,
                             top + (sh - shh * scale) / 2)
                cr.scale(scale, scale)
                cr.set_source_surface(surf, 0, 0)
                cr.paint()
                cr.restore()

        edit = self.edit
        if edit is not None and self.duration:
            # everything the edit leaves out is dimmed; cuts get a hatch too
            kept = edit.segments()
            gaps, cursor = [], 0.0
            for s, e in kept:
                if s > cursor:
                    gaps.append((cursor, s))
                cursor = e
            if cursor < self.duration:
                gaps.append((cursor, self.duration))
            for a, b in gaps:
                x0, x1 = self._x(a), self._x(b)
                cr.set_source_rgba(0, 0, 0, 0.66)
                cr.rectangle(x0, top, x1 - x0, sh)
                cr.fill()
            for a, b in edit.cuts:
                x0, x1 = self._x(min(a, b)), self._x(max(a, b))
                cr.save()
                cr.rectangle(x0, top, x1 - x0, sh)
                cr.clip()
                cr.set_source_rgba(1, 1, 1, 0.22)
                cr.set_line_width(1.2)
                for k in range(int(x0) - sh, int(x1) + sh, 7):
                    cr.move_to(k, top + sh)
                    cr.line_to(k + sh, top)
                cr.stroke()
                cr.restore()
        cr.restore()

        if self.selection and self.duration:
            a, b = self.selection
            x0, x1 = self._x(a), self._x(b)
            cr.set_source_rgba(1, 1, 1, 0.16)
            cr.rectangle(x0, top, x1 - x0, sh)
            cr.fill()
            cr.set_source_rgba(1, 1, 1, 0.9)
            cr.set_line_width(1.5)
            cr.set_dash([4, 3])
            cr.rectangle(x0 + 0.75, top + 0.75, x1 - x0 - 1.5, sh - 1.5)
            cr.stroke()
            cr.set_dash([])

        if edit is not None and self.duration:
            # the kept range in a white frame with a grip at each end
            xs, xe = self._x(edit.start), self._x(edit.stop)
            cr.set_source_rgb(1, 1, 1)
            cr.set_line_width(2.5)
            cr.rectangle(xs, top + 1.25, xe - xs, sh - 2.5)
            cr.stroke()
            for x, side in ((xs, -1), (xe, 1)):
                hx = x - 10 if side < 0 else x
                _round(cr, hx, top, 10, sh, 3)
                cr.set_source_rgb(1, 1, 1)
                cr.fill()
                cr.set_source_rgb(0.11, 0.11, 0.12)
                cr.set_line_width(1.5)
                for dy in (-6, 0, 6):
                    cr.move_to(hx + 3, top + sh / 2 + dy)
                    cr.line_to(hx + 7, top + sh / 2 + dy)
                cr.stroke()

        if self.duration:
            px = self._x(self.position)
            cr.set_source_rgba(0, 0, 0, 0.5)
            cr.rectangle(px - 2, top - 4, 4, sh + 8)
            cr.fill()
            cr.set_source_rgb(1, 1, 1)
            cr.rectangle(px - 1, top - 4, 2, sh + 8)
            cr.fill()

    # -- interaction -------------------------------------------------------
    def _on_motion(self, _c, x, _y):
        self.set_cursor_from_name("ew-resize" if self._hit(x) else "pointer")

    def _on_begin(self, gesture, x, _y):
        state = gesture.get_current_event_state()
        hit = self._hit(x)
        if hit:
            self._drag = (hit, x)
        elif state & Gdk.ModifierType.SHIFT_MASK:
            t = self._t(x)
            self._drag = ("select", x, t)
            self.set_selection((t, t))
        else:
            self._drag = ("scrub", x)
            self.emit("seek", self._t(x))

    def _on_update(self, _gesture, dx, _dy):
        if not self._drag or self.edit is None:
            return
        kind, x0 = self._drag[0], self._drag[1]
        t = self._t(x0 + dx)
        if kind == "start":
            self.emit("trim", min(t, self.edit.stop - 0.1), self.edit.stop)
            self.emit("seek", min(t, self.edit.stop - 0.1))
        elif kind == "end":
            self.emit("trim", self.edit.start, max(t, self.edit.start + 0.1))
            self.emit("seek", max(t, self.edit.start + 0.1))
        elif kind == "select":
            a = self._drag[2]
            self.set_selection((min(a, t), max(a, t)))
            self.emit("select", min(a, t), max(a, t))
        else:
            self.emit("seek", t)

    def _on_end(self, *_args):
        if self._drag and self._drag[0] in ("start", "end"):
            self.emit("trim-done")
        self._drag = None


def _round(cr, x, y, w, h, r):
    r = max(0.0, min(r, w / 2, h / 2))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -1.5708, 0)
    cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
    cr.arc(x + r, y + h - r, r, 1.5708, 3.14159)
    cr.arc(x + r, y + r, r, 3.14159, 4.71239)
    cr.close_path()


class VideoEditorView(Gtk.Box):
    """Stage, timeline and the video tools."""

    __gsignals__ = {
        "closed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "photo-saved": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, library, catalog, settings, thumbs=None):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.library = library
        self.catalog = catalog
        self.settings = settings
        self.thumbs = thumbs
        self.path: Path | None = None
        self.photo_id = None
        self.item = None
        self.edit = ve.VideoEdit()
        self._history: list[ve.VideoEdit] = []
        self._future: list[ve.VideoEdit] = []
        self._size = (0, 0)
        self._mark_in = None
        self._mark_out = None
        self._tick = 0
        self._trim_base = None

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        left.add_css_class("pika-editor-canvas")
        left.append(self._build_bar())
        self.player = VideoPlayer()
        self.player.add_css_class("pika-stage")
        self.player.set_vexpand(True)
        left.append(self.player)
        left.append(self._build_timeline())
        self.append(left)
        self.append(self._build_panel())

    # -- layout ------------------------------------------------------------
    def _build_bar(self):
        bar = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6,
                      margin_start=10, margin_end=10)
        back = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text=_("Back"))
        back.connect("clicked", lambda *_: self.close())
        bar.append(back)
        self.title_label = Gtk.Label(xalign=0.0, hexpand=True)
        self.title_label.add_css_class("pika-dim")
        bar.append(self.title_label)
        self.undo_btn = Gtk.Button(icon_name="edit-undo-symbolic",
                                   tooltip_text=_("Undo (Ctrl+Z)"), sensitive=False)
        self.undo_btn.connect("clicked", lambda *_: self.undo())
        self.redo_btn = Gtk.Button(icon_name="edit-redo-symbolic",
                                   tooltip_text=_("Redo (Ctrl+Shift+Z)"), sensitive=False)
        self.redo_btn.connect("clicked", lambda *_: self.redo())
        bar.append(self.undo_btn)
        bar.append(self.redo_btn)
        revert = Gtk.Button(icon_name="edit-clear-all-symbolic",
                            tooltip_text=_("Revert to Original"))
        revert.connect("clicked", self._on_revert)
        bar.append(revert)
        export = Gtk.Button(label=_("Export…"))
        export.add_css_class("suggested-action")
        export.connect("clicked", self._on_export)
        bar.append(export)
        controls = Gtk.WindowControls(side=Gtk.PackType.END)
        controls.set_decoration_layout(":minimize,maximize,close")
        bar.append(controls)
        handle = Gtk.WindowHandle()
        handle.set_child(bar)
        return handle

    def _build_timeline(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.add_css_class("pika-timeline-bar")
        self.timeline = VideoTimeline()
        self.timeline.connect("seek", lambda _t, t: self.player.seek(t))
        self.timeline.connect("trim", self._on_trim_drag)
        self.timeline.connect("trim-done", self._on_trim_done)
        self.timeline.connect("select", self._on_select)
        box.append(self.timeline)
        labels = Gtk.Box(spacing=8, margin_start=14, margin_end=14)
        self.pos_label = Gtk.Label(label="0:00", xalign=0)
        self.pos_label.add_css_class("pika-timeline-label")
        self.len_label = Gtk.Label(label="", xalign=1, hexpand=True)
        self.len_label.add_css_class("pika-timeline-label")
        labels.append(self.pos_label)
        labels.append(self.len_label)
        box.append(labels)
        return box

    def _build_panel(self):
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.add_css_class("pika-tool-panel")
        panel.set_size_request(320, -1)
        page = Adw.PreferencesPage()

        trim = Adw.PreferencesGroup(title=_("Trim"),
                                    description=_("Drag the white handles on the "
                                                "timeline, or set them here."))
        self.start_row = Adw.ActionRow(title=_("Start"))
        btn = Gtk.Button(label=_("Set Here"), valign=Gtk.Align.CENTER,
                         tooltip_text=_("Start here (I)"))
        btn.connect("clicked", lambda *_: self.set_start_here())
        self.start_row.add_suffix(btn)
        self.end_row = Adw.ActionRow(title=_("End"))
        btn = Gtk.Button(label=_("Set Here"), valign=Gtk.Align.CENTER,
                         tooltip_text=_("End here (O)"))
        btn.connect("clicked", lambda *_: self.set_end_here())
        self.end_row.add_suffix(btn)
        trim.add(self.start_row)
        trim.add(self.end_row)
        page.add(trim)

        cut = Adw.PreferencesGroup(
            title=_("Cut Out"),
            description=_("Hold Shift and drag on the timeline, or mark the start and end, "
                          "then cut it out."))
        marks = Adw.ActionRow(title=_("Selection"))
        self.sel_label = marks
        mark_box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        b_in = Gtk.Button(label=_("Mark Start"), tooltip_text=_("Mark the start ([)"))
        b_in.connect("clicked", lambda *_: self.mark_in())
        b_out = Gtk.Button(label=_("Mark End"), tooltip_text=_("Mark the end (])"))
        b_out.connect("clicked", lambda *_: self.mark_out())
        mark_box.append(b_in)
        mark_box.append(b_out)
        marks.add_suffix(mark_box)
        cut.add(marks)
        self.cut_btn = Gtk.Button(label=_("Cut Out Selection"), sensitive=False,
                                  margin_top=8, tooltip_text=_("Cut out (X)"))
        self.cut_btn.connect("clicked", lambda *_: self.cut_selection())
        cut.add(self.cut_btn)
        self.cuts_group = Adw.PreferencesGroup()
        self._cut_rows = []
        page.add(cut)
        page.add(self.cuts_group)

        motion = Adw.PreferencesGroup(title=_("Playback"))
        self.speed_row = Adw.ComboRow(
            title=_("Speed"), subtitle=_("Voices sound normal at any speed"),
            model=Gtk.StringList.new([f"{s:g}×" for s in SPEEDS]))
        self.speed_row.connect("notify::selected", self._on_speed)
        motion.add(self.speed_row)
        self.mute_row = Adw.SwitchRow(title=_("Mute"), subtitle=_("Export without sound"))
        self.mute_row.connect("notify::active", self._on_mute)
        motion.add(self.mute_row)
        page.add(motion)

        picture = Adw.PreferencesGroup(title=_("Picture"))
        turn = Adw.ActionRow(title=_("Rotate and Flip"))
        tbox = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        for icon, tip, cb in (("object-rotate-left-symbolic", _("Rotate Left"), lambda: self._rotate(-90)),
                              ("object-rotate-right-symbolic", _("Rotate Right"), lambda: self._rotate(90)),
                              ("object-flip-horizontal-symbolic", _("Flip"), self._flip)):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip)
            b.connect("clicked", lambda *_a, c=cb: c())
            tbox.append(b)
        turn.add_suffix(tbox)
        picture.add(turn)
        self.crop_row = Adw.ComboRow(
            title=_("Crop"), model=Gtk.StringList.new([_(name) for name, _ratio in CROPS]))
        self.crop_row.connect("notify::selected", self._on_crop)
        picture.add(self.crop_row)
        page.add(picture)

        frames = Adw.PreferencesGroup(title=_("Frames"))
        poster = Adw.ActionRow(title=_("Cover Frame"), subtitle=_("The frame shown in the library"))
        pb = Gtk.Button(label=_("Use This Frame"), valign=Gtk.Align.CENTER)
        pb.connect("clicked", lambda *_: self._set_poster())
        poster.add_suffix(pb)
        frames.add(poster)
        still = Adw.ActionRow(title=_("Save Frame as Photo"),
                              subtitle=_("Full resolution, into your library"))
        sb = Gtk.Button(icon_name="camera-photo-symbolic", valign=Gtk.Align.CENTER,
                        tooltip_text=_("Save the frame you are seeing"))
        sb.connect("clicked", lambda *_: self.save_frame())
        still.add_suffix(sb)
        frames.add(still)
        page.add(frames)

        panel.append(page)
        page.set_vexpand(True)
        return panel

    # -- opening and closing -------------------------------------------------
    def open(self, path, photo_id=None, item=None):
        self.path = Path(path)
        self.photo_id = photo_id
        self.item = item
        info = stream_info(self.path) or {}
        duration = (getattr(item, "duration", None) or info.get("duration") or 0.0)
        self._size = (info.get("width") or getattr(item, "width", 0) or 0,
                      info.get("height") or getattr(item, "height", 0) or 0)
        self.edit = ve.load(self.library, self.path, duration)
        self.edit.duration = duration
        self._history.clear()
        self._future.clear()
        self._mark_in = self._mark_out = None
        self.title_label.set_text(self.path.name)
        poster = None
        if self.thumbs is not None:
            tp = self.thumbs.get_path(self.path)
            if tp:
                try:
                    poster = Gdk.Texture.new_from_filename(str(tp))
                except Exception:
                    poster = None
        w, h = self._size
        self.player.load(self.path, w, h, info.get("fps", 0.0), duration, poster)
        self.timeline.set_video(self.path, duration, (w / h) if w and h else 16 / 9)
        self.timeline.set_selection(None)
        self._apply(save=False)
        if not self._tick:
            self._tick = GLib.timeout_add(60, self._on_tick)

    def close(self):
        self.player.unload()
        if self._tick:
            GLib.source_remove(self._tick)
            self._tick = 0
        self.emit("closed")

    def shutdown(self):
        self.player.unload()

    def _on_tick(self):
        if self.path is None:
            self._tick = 0
            return GLib.SOURCE_REMOVE
        pos = self.player.position()
        self.timeline.set_position(pos)
        self.pos_label.set_text(format_duration(pos))
        return GLib.SOURCE_CONTINUE

    # -- changes -------------------------------------------------------------
    def _change(self, mutate):
        self._history.append(copy.deepcopy(self.edit))
        self._future.clear()
        mutate(self.edit)
        self._apply()

    def _apply(self, save=True):
        edit = self.edit
        if save and self.path is not None:
            try:
                ve.save(self.library, self.path, edit, self.catalog, self.photo_id)
            except OSError:
                pass
        self.player.set_edit(None if edit.is_identity() else copy.deepcopy(edit))
        self.player.set_rate(edit.speed)
        if self.player.mute_btn.get_active() != edit.mute:
            self.player.mute_btn.set_active(edit.mute)
        self.timeline.set_edit(edit)
        self._refresh_panel()

    def _refresh_panel(self):
        e = self.edit
        self.start_row.set_subtitle(format_duration(e.start) if e.start else _("Beginning"))
        self.end_row.set_subtitle(format_duration(e.stop)
                                  if e.end is not None and e.stop < e.duration - 0.01 else _("End"))
        self.len_label.set_text(_("{length} after editing").format(
                                    length=format_duration(e.output_duration()))
                                if not e.is_identity() else format_duration(e.duration))
        self._syncing = True
        try:
            self.speed_row.set_selected(min(range(len(SPEEDS)),
                                            key=lambda i: abs(SPEEDS[i] - e.speed)))
            self.mute_row.set_active(e.mute)
            aspect_index = 0
            if e.crop:
                w, h = self._display_size()
                ratio = (e.crop[2] * w) / max(1.0, e.crop[3] * h)
                aspect_index = min(range(1, len(CROPS)),
                                   key=lambda i: abs(CROPS[i][1] - ratio))
            self.crop_row.set_selected(aspect_index)
        finally:
            self._syncing = False
        sel = self._selection()
        self.sel_label.set_subtitle(
            f"{format_duration(sel[0])} – {format_duration(sel[1])}" if sel
            else _("Nothing selected"))
        self.cut_btn.set_sensitive(bool(sel))
        for row in self._cut_rows:
            self.cuts_group.remove(row)
        self._cut_rows = []
        self.cuts_group.set_title(_("Pieces Cut Out") if e.cuts else "")
        for index, (a, b) in enumerate(e.cuts):
            row = Adw.ActionRow(title=f"{format_duration(a)} – {format_duration(b)}")
            restore = Gtk.Button(label=_("Restore"), valign=Gtk.Align.CENTER)
            restore.connect("clicked", lambda *_a, i=index: self._restore_cut(i))
            row.add_suffix(restore)
            self.cuts_group.add(row)
            self._cut_rows.append(row)
        self.undo_btn.set_sensitive(bool(self._history))
        self.redo_btn.set_sensitive(bool(self._future))

    def undo(self):
        if self._history:
            self._future.append(copy.deepcopy(self.edit))
            self.edit = self._history.pop()
            self._apply()

    def redo(self):
        if self._future:
            self._history.append(copy.deepcopy(self.edit))
            self.edit = self._future.pop()
            self._apply()

    # -- trim and cuts -------------------------------------------------------
    def _on_trim_drag(self, _tl, start, end):
        if self._trim_base is None:
            self._trim_base = copy.deepcopy(self.edit)
        self.edit.start = max(0.0, start)
        self.edit.end = None if end >= self.edit.duration - 0.01 else end
        self.timeline.set_edit(self.edit)
        self.start_row.set_subtitle(format_duration(self.edit.start))
        self.end_row.set_subtitle(format_duration(self.edit.stop))

    def _on_trim_done(self, _tl):
        if self._trim_base is not None:
            self._history.append(self._trim_base)
            self._future.clear()
            self._trim_base = None
        self._apply()

    def set_start_here(self):
        t = self.player.position()
        if t < self.edit.stop - 0.1:
            self._change(lambda e: setattr(e, "start", t))

    def set_end_here(self):
        t = self.player.position()
        if t > self.edit.start + 0.1:
            self._change(lambda e: setattr(e, "end", t))

    def mark_in(self):
        self._mark_in = self.player.position()
        self._sync_selection()

    def mark_out(self):
        self._mark_out = self.player.position()
        self._sync_selection()

    def _on_select(self, _tl, a, b):
        self._mark_in, self._mark_out = a, b
        self._sync_selection(draw=False)

    def _selection(self):
        if self._mark_in is None or self._mark_out is None:
            return None
        a, b = sorted((self._mark_in, self._mark_out))
        return (a, b) if b - a >= 0.05 else None

    def _sync_selection(self, draw=True):
        sel = self._selection()
        if draw:
            self.timeline.set_selection(sel)
        self._refresh_panel()

    def cut_selection(self):
        sel = self._selection()
        if not sel:
            return
        self._mark_in = self._mark_out = None
        self.timeline.set_selection(None)
        self._change(lambda e: e.cuts.append(sel))

    def _restore_cut(self, index):
        if 0 <= index < len(self.edit.cuts):
            self._change(lambda e: e.cuts.pop(index))

    # -- playback, picture ---------------------------------------------------
    def _on_speed(self, row, _pspec):
        if getattr(self, "_syncing", False):
            return
        speed = SPEEDS[row.get_selected()]
        if abs(speed - self.edit.speed) > 1e-6:
            self._change(lambda e: setattr(e, "speed", speed))

    def _on_mute(self, row, _pspec):
        if getattr(self, "_syncing", False):
            return
        if row.get_active() != self.edit.mute:
            self._change(lambda e: setattr(e, "mute", row.get_active()))

    def _rotate(self, degrees):
        def turn(e):
            e.rotate = (e.rotate + degrees) % 360
            e.crop = None           # a crop drawn for one orientation fits no other
        self._change(turn)

    def _flip(self):
        self._change(lambda e: setattr(e, "flip", not e.flip))

    def _display_size(self):
        w, h = self._size
        if self.edit.rotate % 180:
            w, h = h, w
        return (w or 16), (h or 9)

    def _on_crop(self, row, _pspec):
        if getattr(self, "_syncing", False):
            return
        _name, target = CROPS[row.get_selected()]
        if target is None:
            if self.edit.crop is not None:
                self._change(lambda e: setattr(e, "crop", None))
            return
        w, h = self._display_size()
        picture = w / h
        if target > picture:
            cw, ch = 1.0, picture / target
        else:
            cw, ch = target / picture, 1.0
        rect = ((1 - cw) / 2, (1 - ch) / 2, cw, ch)
        self._change(lambda e: setattr(e, "crop", rect))

    # -- frames --------------------------------------------------------------
    def _set_poster(self):
        t = self.player.position()
        self._change(lambda e: setattr(e, "poster", t))
        self._toast(_("Poster frame set"))

    def save_frame(self):
        if self.path is None:
            return
        t = self.player.position()
        path, edit = self.path, copy.deepcopy(self.edit)
        taken = None
        if self.photo_id is not None:
            row = self.catalog.photo(self.photo_id)
            taken = row["taken_at"] if row is not None else None

        def work():
            try:
                saved = ve.save_frame_as_photo(self.library, path, t, edit, taken)
            except Exception as exc:
                GLib.idle_add(self._toast,
                              _("The frame couldn’t be saved: {error}").format(error=exc))
                return
            GLib.idle_add(self.emit, "photo-saved", str(saved))
        threading.Thread(target=work, daemon=True).start()

    # -- bar actions ---------------------------------------------------------
    def _on_revert(self, _btn):
        if self.edit.is_identity() and self.edit.poster is None:
            return
        dialog = Adw.AlertDialog(
            heading=_("Revert to Original?"),
            body=_("Your video file is untouched either way — this clears the "
                 "trims, cuts and other changes made to it."))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("revert", _("Revert"))
        dialog.set_response_appearance("revert", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")

        def done(_d, response):
            if response == "revert":
                duration = self.edit.duration
                self._change(lambda e: e.__init__(duration=duration))
        dialog.connect("response", done)
        dialog.present(self.get_root())

    def _on_export(self, _btn):
        if self.path is None:
            return
        from .export_dialog import ExportDialog
        ExportDialog(self.get_root(), self.library, self.settings, [self.path],
                     catalog=self.catalog).present(self.get_root())

    def _toast(self, text):
        root = self.get_root()
        toasts = getattr(root, "toasts", None)
        if toasts is not None:
            toasts.add_toast(Adw.Toast(title=text, timeout=3))
        return GLib.SOURCE_REMOVE
