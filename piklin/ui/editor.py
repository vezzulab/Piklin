"""The edit surface.

The tool panel is generated from the ``Param`` declarations in
engine/tools.py, so every tool gets a correct UI without a line of
per-tool widget code, and adding a tool needs no change here.

Interaction is split across two rendering paths.  Dragging a slider
renders a reduced-size draft with expensive approximations enabled;
letting go renders the real preview.  Both run on the render thread.
"""
from __future__ import annotations

import math
import threading
import time
from pathlib import Path

import gi
from ..i18n import _

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gdk, Gio, Gtk  # noqa: E402

import numpy as np

from .. import imageio as iio
from ..engine import looks as looks_mod
from ..engine import ops, tools
from ..engine.stack import EditStack, Renderer
from ..engine.tools import (CHOICE, COLOR, CURVE, IMAGE, POINT, SLIDER,
                            STROKES, TEXT, TOGGLE)
from .render_thread import RenderThread, texture_from_array
from .widgets import CurveEditor, Histogram, ParamSlider

# Tools whose parameters are set by interacting with the image itself.
CANVAS_TOOLS = {"crop", "selective", "brush", "healing", "vignette",
                "lens_blur", "white_balance", "text"}


class EditorView(Gtk.Box):
    """Canvas plus tool panel."""

    __gsignals__ = {
        "closed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "saved": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, library, catalog, settings, thumbs=None):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.library = library
        self.catalog = catalog
        self.settings = settings
        if thumbs is None:
            from ..thumbs import ThumbCache
            thumbs = ThumbCache(library.thumbs)
        self._thumbs = thumbs

        self.photo_path: Path | None = None
        self.photo_id: int | None = None
        self.source: np.ndarray | None = None       # preview-resolution source
        self.full_size = (0, 0)
        self.stack = EditStack()
        self.renderer = Renderer()
        self.active_index: int | None = None
        self.rendered: np.ndarray | None = None
        self._compare = False
        self._render = RenderThread(self._on_frame)
        self._pending_full = None
        self._autosave_source = 0

        self._build_canvas()
        self._build_panel()

    # ==================================================================
    # canvas
    # ==================================================================
    def _build_canvas(self):
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN,
                                   can_shrink=True, hexpand=True, vexpand=True)
        self.overlay_area = Gtk.DrawingArea()
        self.overlay_area.set_draw_func(self._draw_overlay)
        self.overlay_area.set_can_target(True)

        self.canvas_overlay = Gtk.Overlay(hexpand=True, vexpand=True)
        # The photo sits on a dark stage while editing: judging exposure and
        # colour against white makes every photo look darker than it is.
        self.canvas_overlay.add_css_class("pika-stage")
        self.canvas_overlay.set_child(self.picture)
        self.canvas_overlay.add_overlay(self.overlay_area)

        self.spinner = Gtk.Spinner(halign=Gtk.Align.END, valign=Gtk.Align.START,
                                   margin_top=10, margin_end=10)
        self.canvas_overlay.add_overlay(self.spinner)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.add_css_class("pika-editor-canvas")
        box.append(self._build_canvas_bar())
        box.append(self.canvas_overlay)
        box.set_hexpand(True)
        self.append(box)

        # pointer interaction for canvas tools
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_canvas_press)
        drag.connect("drag-update", self._on_canvas_drag)
        drag.connect("drag-end", self._on_canvas_release)
        self.overlay_area.add_controller(drag)

        self._crop_rect = None
        self._crop_handle = None
        self._stroke = None
        self._canvas_mode = None

    def _build_canvas_bar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                      margin_top=6, margin_bottom=6, margin_start=10,
                      margin_end=10)
        back = Gtk.Button(icon_name="go-previous-symbolic",
                          tooltip_text=_("Back to library"))
        back.connect("clicked", lambda *_: self.emit("closed"))
        bar.append(back)

        self.title_label = Gtk.Label(xalign=0.0, hexpand=True)
        self.title_label.add_css_class("pika-dim")
        bar.append(self.title_label)

        self.undo_btn = Gtk.Button(icon_name="edit-undo-symbolic",
                                   tooltip_text=_("Undo (Ctrl+Z)"), sensitive=False)
        self.undo_btn.connect("clicked", lambda *_: self.undo())
        self.redo_btn = Gtk.Button(icon_name="edit-redo-symbolic",
                                   tooltip_text=_("Redo (Ctrl+Shift+Z)"),
                                   sensitive=False)
        self.redo_btn.connect("clicked", lambda *_: self.redo())
        bar.append(self.undo_btn)
        bar.append(self.redo_btn)

        compare = Gtk.ToggleButton(icon_name="view-reveal-symbolic",
                                   tooltip_text=_("Hold to compare with original (M)"))
        compare.connect("toggled", self._on_compare)
        bar.append(compare)
        self.compare_btn = compare

        revert = Gtk.Button(icon_name="edit-clear-all-symbolic",
                            tooltip_text=_("Remove all edits"))
        revert.connect("clicked", self._on_revert)
        bar.append(revert)

        export = Gtk.Button(label=_("Export…"))
        export.add_css_class("suggested-action")
        export.connect("clicked", self._on_export)
        bar.append(export)
        # The editor replaces the library's header, so it carries the
        # window buttons too - same place, same style as everywhere else.
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

    # ==================================================================
    # tool panel
    # ==================================================================
    def _build_panel(self):
        self.panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.panel.add_css_class("pika-tool-panel")
        self.panel.set_size_request(320, -1)

        self.switcher = Adw.ViewStack()
        self.switcher.set_vexpand(True)

        self.switcher.add_titled_with_icon(
            self._build_tools_page(), "tools", _("Tools"),
            "applications-graphics-symbolic")
        self.switcher.add_titled_with_icon(
            self._build_looks_page(), "looks", _("Looks"),
            "image-filter-symbolic")
        self.switcher.add_titled_with_icon(
            self._build_layers_page(), "layers", _("Edits"),
            "view-list-symbolic")

        bar = Adw.ViewSwitcherBar(stack=self.switcher)
        bar.set_reveal(True)

        self.param_page = self._build_param_page()
        self.panel_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT,
            transition_duration=140)
        browse = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        browse.append(self.switcher)
        browse.append(bar)
        self.panel_stack.add_named(browse, "browse")
        self.panel_stack.add_named(self.param_page, "params")
        self.panel.append(self.panel_stack)
        self.append(self.panel)

    def _build_tools_page(self):
        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                      margin_top=8, margin_bottom=12, margin_start=10,
                      margin_end=10)
        icons = {
            "tune": "preferences-color-symbolic",
            "details": "zoom-in-symbolic",
            "curves": "view-continuous-symbolic",
            "white_balance": "weather-clear-symbolic",
            "crop": "view-fullscreen-symbolic",
            "rotate": "object-rotate-right-symbolic",
            "perspective": "view-paged-symbolic",
            "expand": "zoom-fit-best-symbolic",
            "selective": "find-location-symbolic",
            "brush": "document-edit-symbolic",
            "healing": "edit-paste-symbolic",
            "portrait": "avatar-default-symbolic",
            "head_pose": "face-smile-symbolic",
        }
        for group, tool_list in tools.groups().items():
            header = Gtk.Label(label=_(group), xalign=0.0)
            header.add_css_class("pika-tool-group")
            box.append(header)
            flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                               max_children_per_line=3,
                               min_children_per_line=3,
                               homogeneous=True, row_spacing=2,
                               column_spacing=2)
            for tool in tool_list:
                btn = Gtk.Button()
                btn.add_css_class("flat")
                btn.add_css_class("pika-tool-tile")
                inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
                img = Gtk.Image(icon_name=icons.get(tool.id,
                                                    "image-filter-symbolic"),
                                pixel_size=22)
                name = Gtk.Label(label=_(tool.name), wrap=True, justify=2,
                                 max_width_chars=11)
                name.add_css_class("pika-tool-name")
                inner.append(img)
                inner.append(name)
                btn.set_child(inner)
                btn.set_tooltip_text(_(tool.description or tool.name))
                btn.connect("clicked", self._on_pick_tool, tool.id)
                flow.append(btn)
            box.append(flow)
        scroller.set_child(box)
        return scroller

    def _build_looks_page(self):
        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                      margin_top=10, margin_bottom=12, margin_start=10,
                      margin_end=10)
        hint = Gtk.Label(
            label=_("A Look adds edits you can still change or remove."),
            wrap=True, xalign=0.0)
        hint.add_css_class("pika-dim")
        box.append(hint)
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                           max_children_per_line=2, min_children_per_line=2,
                           homogeneous=True, row_spacing=4, column_spacing=4)
        for name in looks_mod.names():
            btn = Gtk.Button(label=_(name))
            btn.add_css_class("flat")
            btn.add_css_class("pika-look-tile")
            btn.connect("clicked", self._on_apply_look, name)
            flow.append(btn)
        box.append(flow)
        scroller.set_child(box)
        return scroller

    def _build_layers_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      margin_top=10, margin_bottom=10, margin_start=10,
                      margin_end=10)
        self.histogram = Histogram()
        box.append(self.histogram)

        self.layer_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.layer_list.add_css_class("boxed-list")
        self.layer_list.connect("row-activated", self._on_layer_activated)
        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroller.set_child(self.layer_list)
        box.append(scroller)

        self.layers_empty = Gtk.Label(
            label=_("No edits yet.\nPick a tool to begin."),
            justify=2, wrap=True)
        self.layers_empty.add_css_class("pika-dim")
        box.append(self.layers_empty)
        return box

    def _build_param_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                       margin_top=8, margin_bottom=4, margin_start=8,
                       margin_end=8)
        back = Gtk.Button(icon_name="go-previous-symbolic")
        back.add_css_class("flat")
        back.connect("clicked", lambda *_: self._close_params())
        head.append(back)
        self.param_title = Gtk.Label(xalign=0.0, hexpand=True)
        self.param_title.add_css_class("heading")
        head.append(self.param_title)
        drop = Gtk.Button(icon_name="user-trash-symbolic",
                          tooltip_text=_("Remove this edit"))
        drop.add_css_class("flat")
        drop.connect("clicked", lambda *_: self._delete_active())
        head.append(drop)
        box.append(head)

        self.param_hint = Gtk.Label(xalign=0.0, wrap=True, margin_start=12,
                                    margin_end=12, margin_bottom=6)
        self.param_hint.add_css_class("pika-dim")
        box.append(self.param_hint)

        self.param_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                 spacing=2, margin_start=8, margin_end=8,
                                 margin_bottom=10)
        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroller.set_child(self.param_box)
        box.append(scroller)
        return box

    # ==================================================================
    # opening
    # ==================================================================
    def open(self, path, photo_id=None):
        self.photo_path = Path(path)
        self.photo_id = photo_id
        self.title_label.set_text(self.photo_path.name)
        self.renderer.invalidate()
        self.active_index = None
        self._close_params()

        sidecar = self.library.edit_sidecar(self.photo_path)
        self.stack = EditStack.load(sidecar)
        self._refresh_layers()

        max_side = int(self.settings.get("preview_max_side", 1600))
        self.spinner.start()

        def load():
            img = iio.load_rgb(self.photo_path, max_side=max_side)
            try:
                rec = iio.probe(self.photo_path)
                full = (rec["width"], rec["height"]) if rec else img.shape[1::-1]
            except Exception:
                full = (img.shape[1], img.shape[0])
            return ("loaded", img, full)

        self._render.submit(load, tag="load")

    def _on_frame(self, result, tag, elapsed):
        if isinstance(tag, tuple) and tag and tag[0] == "error":
            self.spinner.stop()
            return
        if result is None:
            self.spinner.stop()
            return
        if isinstance(result, tuple) and result and result[0] == "loaded":
            _, img, full = result
            self.source = img
            self.full_size = full
            self.renderer.invalidate()
            self._request_render(draft=False)
            return

        self.rendered = result
        self.spinner.stop()
        if not self._compare:
            self.picture.set_paintable(texture_from_array(result))
        try:
            self.histogram.set_data(ops.histogram(result, 128))
        except Exception:
            pass
        self.overlay_area.queue_draw()

        # A pending full-quality render is issued once the draft that was
        # in flight has landed, so the two never queue up behind a drag.
        if self._pending_full is not None and not self._render.busy:
            self._pending_full = None
            self._request_render(draft=False)

    # ==================================================================
    # rendering
    # ==================================================================
    def _request_render(self, draft=False):
        if self.source is None:
            return
        if draft and self._render.busy:
            self._pending_full = True
        stack = self.stack
        source = self.source
        full = self.full_size
        drag_side = int(self.settings.get("drag_max_side", 900))
        preview_side = int(self.settings.get("preview_max_side", 1600))

        def work():
            img = source
            if draft and max(img.shape[:2]) > drag_side:
                img = ops.fit_within(img, drag_side)
            scale = (max(img.shape[:2]) / max(full)) if max(full) else 1.0
            return self.renderer.render(
                img, stack, scale=scale, full_size=full, draft=draft,
                face_detection=bool(self.settings.get("face_detection", True)))

        if not draft:
            self.spinner.start()
            self._pending_full = None
        self._render.submit(work, tag="draft" if draft else "full")

    def _autosave(self):
        if not self.settings.get("edit_autosave", True) or not self.photo_path:
            return
        sidecar = self.library.edit_sidecar(self.photo_path)
        try:
            if len(self.stack):
                self.stack.save(sidecar, str(self.photo_path))
            elif sidecar.exists():
                sidecar.unlink()
            if self.photo_id is not None:
                self.catalog.note_edit(self.photo_id, len(self.stack))
        except Exception:
            pass

    # ==================================================================
    # tools
    # ==================================================================
    def _on_pick_tool(self, _btn, tool_id):
        if self.source is None:
            return
        spec = tools.REGISTRY.get(tool_id)
        if spec is None:
            return
        # Reuse the topmost layer when the same tool is picked twice in a
        # row, rather than stacking two Tune Image layers that fight.
        if (self.stack.layers and self.stack.layers[-1].tool == tool_id
                and self.active_index == len(self.stack) - 1):
            self._show_params(len(self.stack) - 1)
            return
        params = None
        if tool_id == "crop":
            params = {"rect": [0.0, 0.0, 1.0, 1.0]}
        self.stack.add(tool_id, params)
        self._refresh_layers()
        self._show_params(len(self.stack) - 1)
        self._request_render(draft=False)
        self._autosave()

    def _on_apply_look(self, _btn, name):
        if self.source is None:
            return
        self.stack.apply_look(name)
        self._refresh_layers()
        self._request_render(draft=False)
        self._autosave()
        self._update_history_buttons()

    def _show_params(self, index):
        if not (0 <= index < len(self.stack)):
            return
        self.active_index = index
        layer = self.stack.layers[index]
        spec = layer.spec
        if spec is None:
            return
        self.param_title.set_text(_(spec.name))
        self.param_hint.set_text(_(spec.description) if spec.description else "")
        self.param_hint.set_visible(bool(spec.description))

        child = self.param_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.param_box.remove(child)
            child = nxt

        self._canvas_mode = spec.id if spec.id in CANVAS_TOOLS else None
        if spec.id == "crop":
            r = layer.params.get("rect") or [0, 0, 1, 1]
            self._crop_rect = list(r)

        for param in spec.params:
            widget = self._widget_for(param, layer, index)
            if widget is not None:
                self.param_box.append(widget)

        if spec.maskable or spec.id not in ("crop", "rotate"):
            self.param_box.append(self._opacity_row(layer, index))

        self.panel_stack.set_visible_child_name("params")
        self.overlay_area.queue_draw()

    def _widget_for(self, param, layer, index):
        value = layer.params.get(param.key, param.default)

        if param.kind == SLIDER:
            slider = ParamSlider(param, value)
            slider.connect("changing", self._on_param_changing, index, param.key)
            slider.connect("changed", self._on_param_changed, index, param.key)
            return slider

        if param.kind == CHOICE:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                          margin_top=6, margin_bottom=2)
            row.append(Gtk.Label(label=_(param.label), xalign=0.0, hexpand=True))
            choices = [str(c) for c in param.choices]
            drop = Gtk.DropDown.new_from_strings([_(c) for c in choices])
            try:
                drop.set_selected(choices.index(str(value)))
            except ValueError:
                pass
            drop.connect("notify::selected", self._on_choice, index, param)
            row.append(drop)
            return row

        if param.kind == TOGGLE:
            row = Adw.SwitchRow(title=_(param.label), active=bool(value))
            row.connect("notify::active", self._on_toggle, index, param.key)
            return row

        if param.kind == TEXT:
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                          margin_top=6)
            row.append(Gtk.Label(label=_(param.label), xalign=0.0))
            buf = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD)
            buf.get_buffer().set_text(str(value or ""))
            buf.set_size_request(-1, 70)
            buf.get_buffer().connect("changed", self._on_text, index, param.key)
            frame = Gtk.Frame()
            frame.set_child(buf)
            row.append(frame)
            return row

        if param.kind == COLOR:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                          margin_top=6)
            row.append(Gtk.Label(label=_(param.label), xalign=0.0, hexpand=True))
            btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog())
            c = value or [1, 1, 1]
            rgba = Gdk.RGBA()
            rgba.red, rgba.green, rgba.blue, rgba.alpha = (
                float(c[0]), float(c[1]), float(c[2]), 1.0)
            btn.set_rgba(rgba)
            btn.connect("notify::rgba", self._on_color, index, param.key)
            row.append(btn)
            return row

        if param.kind == CURVE:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                          margin_top=6)
            editor = CurveEditor()
            curves = layer.params.get("curves") or {}
            editor.set_points(curves.get("rgb") or [(0, 0), (1, 1)])
            if self.rendered is not None:
                try:
                    editor.set_histogram(ops.histogram(self.rendered, 128)["l"])
                except Exception:
                    pass
            editor.connect("curve-changed", self._on_curve, index)
            box.append(editor)
            hint = Gtk.Label(
                label=_("Double-click to add a point, right-click to remove."),
                wrap=True, xalign=0.0)
            hint.add_css_class("pika-dim")
            box.append(hint)
            reset = Gtk.Button(label=_("Reset curve"))
            reset.add_css_class("flat")
            reset.connect("clicked", lambda *_: editor.reset())
            box.append(reset)
            return box

        if param.kind == IMAGE:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                          margin_top=6)
            btn = Gtk.Button(label=_("Choose image…"), hexpand=True)
            btn.connect("clicked", self._on_pick_image, index, param.key)
            row.append(btn)
            return row

        if param.kind in (POINT, STROKES):
            label = {"crop": _("Drag on the photo to set the crop."),
                     "selective": _("Click the photo to drop a control point."),
                     "brush": _("Drag on the photo to paint."),
                     "healing": _("Drag over what you want removed."),
                     }.get(layer.tool, _("Click the photo to position this."))
            hint = Gtk.Label(label=label, wrap=True, xalign=0.0,
                             margin_top=6, margin_bottom=2)
            hint.add_css_class("pika-dim")
            wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            wrapper.append(hint)
            if layer.tool in ("selective", "brush", "healing"):
                clear = Gtk.Button(label=_("Clear"))
                clear.add_css_class("flat")
                clear.connect("clicked", self._on_clear_marks, index, param.key)
                wrapper.append(clear)
            return wrapper
        return None

    def _opacity_row(self, layer, index):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=10)
        sep = Gtk.Separator()
        box.append(sep)
        p = tools.Param("__opacity", _("Strength"), lo=0, hi=100,
                        default=layer.opacity * 100)
        slider = ParamSlider(p, layer.opacity * 100)

        def changing(_w, v):
            layer.opacity = max(0.0, min(1.0, v / 100.0))
            self._request_render(draft=True)

        def changed(_w, v):
            layer.opacity = max(0.0, min(1.0, v / 100.0))
            self._request_render(draft=False)
            self._autosave()
        slider.connect("changing", changing)
        slider.connect("changed", changed)
        box.append(slider)
        return box

    # -- param callbacks -------------------------------------------------
    def _on_param_changing(self, _w, value, index, key):
        self.stack.set_params(index, {key: value})
        self._request_render(draft=True)

    def _on_param_changed(self, _w, value, index, key):
        self.stack.set_params(index, {key: value})
        self._request_render(draft=False)
        self._autosave()
        self._refresh_layers()

    def _on_choice(self, drop, _pspec, index, param):
        sel = drop.get_selected()
        if sel < 0 or sel >= len(param.choices):
            return
        value = param.choices[sel]
        layer = self.stack.layers[index]
        self.stack.set_params(index, {param.key: value})

        # Style presets carry slider values with them; adopt those and
        # rebuild the panel so the sliders show what is actually applied.
        presets = {
            "tonal_contrast": tools.TONAL_STYLES, "hdr": tools.HDR_STYLES,
            "glamour": tools.GLAMOUR_STYLES,
        }.get(layer.tool)
        if presets and param.key == "style" and value in presets:
            mapped = {"high": "high_tones", "mid": "mid_tones",
                      "low": "low_tones", "strength": "strength",
                      "brightness": "brightness", "saturation": "saturation",
                      "smoothing": "smoothing", "glow": "glow",
                      "warmth": "warmth"}
            update = {mapped.get(k, k): v for k, v in presets[value].items()
                      if mapped.get(k, k) in {p.key for p in layer.spec.params}}
            self.stack.set_params(index, update)
            self._show_params(index)
        if param.key == "preset" and layer.tool == "curves":
            pts = tools.CURVE_PRESETS.get(str(value))
            if pts:
                self.stack.set_params(index, {"curves": {"rgb": list(pts)}})
                self._show_params(index)
        self._request_render(draft=False)
        self._autosave()

    def _on_toggle(self, row, _pspec, index, key):
        self.stack.set_params(index, {key: row.get_active()})
        self._request_render(draft=False)
        self._autosave()

    def _on_text(self, buf, index, key):
        start, end = buf.get_bounds()
        self.stack.set_params(index, {key: buf.get_text(start, end, False)})
        self._request_render(draft=False)
        self._autosave()

    def _on_color(self, btn, _pspec, index, key):
        c = btn.get_rgba()
        self.stack.set_params(index, {key: [c.red, c.green, c.blue]})
        self._request_render(draft=False)
        self._autosave()

    def _on_curve(self, _editor, points, index):
        self.stack.set_params(index, {"curves": {"rgb": [list(p) for p in points]}})
        self._request_render(draft=True)
        self._autosave()

    def _on_clear_marks(self, _btn, index, key):
        self.stack.set_params(index, {key: []})
        self._request_render(draft=False)
        self.overlay_area.queue_draw()
        self._autosave()

    def _on_pick_image(self, btn, index, key):
        """Choose the second exposure from the library, in-window.

        A system file chooser is a separate toplevel and would cover the
        photo being edited; everything in the editor stays inside the
        app.
        """
        from .picker import PhotoPicker
        picker = PhotoPicker(self.catalog, self._thumbs,
                             title=_("Choose a photo to blend"),
                             exclude_path=self.photo_path)

        def picked(_p, path):
            self.stack.set_params(index, {key: path})
            self._request_render(draft=False)
            self._autosave()
            btn.set_label(Path(path).name)
        picker.connect("picked", picked)
        picker.present(self.get_root())

    # ==================================================================
    # layers
    # ==================================================================
    def _refresh_layers(self):
        while (row := self.layer_list.get_first_child()) is not None:
            self.layer_list.remove(row)
        for i, layer in enumerate(self.stack.layers):
            row = Adw.ActionRow(title=_(layer.name), activatable=True)
            row.add_css_class("pika-layer-row")
            if layer.opacity < 0.999:
                row.set_subtitle(_("{percent}% strength").format(
                    percent=f"{layer.opacity*100:.0f}"))
            toggle = Gtk.Switch(active=layer.enabled, valign=Gtk.Align.CENTER)
            toggle.connect("notify::active", self._on_layer_toggle, i)
            row.add_suffix(toggle)
            delete = Gtk.Button(icon_name="user-trash-symbolic",
                                valign=Gtk.Align.CENTER)
            delete.add_css_class("flat")
            delete.connect("clicked", self._on_layer_delete, i)
            row.add_suffix(delete)
            row._index = i
            self.layer_list.append(row)
        empty = len(self.stack) == 0
        self.layers_empty.set_visible(empty)
        self.layer_list.set_visible(not empty)
        self._update_history_buttons()

    def _on_layer_activated(self, _list, row):
        self._show_params(getattr(row, "_index", 0))

    def _on_layer_toggle(self, sw, _pspec, index):
        if 0 <= index < len(self.stack):
            self.stack.layers[index].enabled = sw.get_active()
            self.stack.version += 1
            self._request_render(draft=False)
            self._autosave()

    def _on_layer_delete(self, _btn, index):
        self.stack.remove(index)
        if self.active_index == index:
            self._close_params()
        self._refresh_layers()
        self._request_render(draft=False)
        self._autosave()

    def _delete_active(self):
        if self.active_index is not None:
            self._on_layer_delete(None, self.active_index)

    def _close_params(self):
        self.active_index = None
        self._canvas_mode = None
        self.panel_stack.set_visible_child_name("browse")
        self.overlay_area.queue_draw()

    # ==================================================================
    # history / compare / revert
    # ==================================================================
    def _update_history_buttons(self):
        self.undo_btn.set_sensitive(self.stack.can_undo)
        self.redo_btn.set_sensitive(self.stack.can_redo)

    def undo(self):
        if self.stack.undo():
            self._close_params()
            self._refresh_layers()
            self.renderer.invalidate()
            self._request_render(draft=False)
            self._autosave()

    def redo(self):
        if self.stack.redo():
            self._close_params()
            self._refresh_layers()
            self.renderer.invalidate()
            self._request_render(draft=False)
            self._autosave()

    def hold_compare(self, active: bool) -> None:
        """Show the original while M is held down."""
        if self.compare_btn.get_active() != active:
            self.compare_btn.set_active(active)

    def _on_compare(self, btn):
        self._compare = btn.get_active()
        if self._compare and self.source is not None:
            self.picture.set_paintable(texture_from_array(self.source))
        elif self.rendered is not None:
            self.picture.set_paintable(texture_from_array(self.rendered))

    def _on_revert(self, _btn):
        if not len(self.stack):
            return
        dialog = Adw.AlertDialog(
            heading=_("Remove all edits?"),
            body=_("Your original photo is untouched either way — this just "
                 "clears the adjustments you have made to it."))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("revert", _("Remove Edits"))
        dialog.set_response_appearance("revert",
                                       Adw.ResponseAppearance.DESTRUCTIVE)

        def done(d, response):
            if response == "revert":
                self.stack.clear()
                self._close_params()
                self._refresh_layers()
                self.renderer.invalidate()
                self._request_render(draft=False)
                self._autosave()
        dialog.connect("response", done)
        dialog.present(self.get_root())

    def _on_export(self, _btn):
        from .export_dialog import ExportDialog
        if self.photo_path is None:
            return
        ExportDialog(self.get_root(), self.library, self.settings,
                     [self.photo_path], stack=self.stack, catalog=self.catalog).present(self.get_root())

    # ==================================================================
    # canvas interaction
    # ==================================================================
    def _image_rect(self):
        """Where the photo actually sits inside the canvas widget."""
        if self.rendered is None:
            return None
        w = self.overlay_area.get_width()
        h = self.overlay_area.get_height()
        ih, iw = self.rendered.shape[:2]
        if not (w and h and iw and ih):
            return None
        scale = min(w / iw, h / ih)
        dw, dh = iw * scale, ih * scale
        return ((w - dw) / 2, (h - dh) / 2, dw, dh)

    def _to_image(self, px, py):
        rect = self._image_rect()
        if rect is None:
            return None
        x0, y0, dw, dh = rect
        return (min(1.0, max(0.0, (px - x0) / dw)),
                min(1.0, max(0.0, (py - y0) / dh)))

    def _on_canvas_press(self, gesture, px, py):
        if self._canvas_mode is None or self.active_index is None:
            return
        pt = self._to_image(px, py)
        if pt is None:
            return
        mode = self._canvas_mode
        layer = self.stack.layers[self.active_index]

        if mode == "crop":
            self._crop_handle = self._crop_hit(pt)
            if self._crop_handle is None:
                self._crop_rect = [pt[0], pt[1], 0.0, 0.0]
                self._crop_handle = "new"
        elif mode in ("brush", "healing"):
            size = float(layer.params.get("size", 20)) / 100.0 * 0.18 + 0.01
            stroke = {"points": [list(pt)], "radius": size}
            if mode == "brush":
                stroke["mode"] = layer.params.get("mode", "Dodge & Burn")
                stroke["value"] = float(layer.params.get("value", 50)) / 100.0
            self._stroke = stroke
            layer.params.setdefault("strokes", []).append(stroke)
        elif mode == "selective":
            pts = layer.params.setdefault("points", [])
            pts.append({"x": pt[0], "y": pt[1], "size": 0.25,
                        "brightness": 0, "contrast": 0, "saturation": 0,
                        "structure": 0})
            self._show_params(self.active_index)
            self._request_render(draft=False)
        elif mode in ("vignette", "lens_blur"):
            layer.params["center"] = [pt[0], pt[1]]
            self._request_render(draft=True)
        elif mode == "white_balance":
            layer.params["picker"] = [pt[0], pt[1]]
            self._request_render(draft=False)
        elif mode == "text":
            layer.params["position"] = [pt[0], pt[1]]
            self._request_render(draft=True)
        self.overlay_area.queue_draw()

    def _crop_hit(self, pt):
        if not self._crop_rect:
            return None
        x, y, w, h = self._crop_rect
        tol = 0.035
        corners = {"nw": (x, y), "ne": (x + w, y),
                   "sw": (x, y + h), "se": (x + w, y + h)}
        for name, (cx, cy) in corners.items():
            if abs(pt[0] - cx) < tol and abs(pt[1] - cy) < tol:
                return name
        if x < pt[0] < x + w and y < pt[1] < y + h:
            return "move"
        return None

    def _on_canvas_drag(self, gesture, dx, dy):
        if self._canvas_mode is None or self.active_index is None:
            return
        ok, sx, sy = gesture.get_start_point()
        if not ok:
            return
        pt = self._to_image(sx + dx, sy + dy)
        start = self._to_image(sx, sy)
        if pt is None or start is None:
            return
        layer = self.stack.layers[self.active_index]
        mode = self._canvas_mode

        if mode == "crop" and self._crop_handle:
            self._update_crop(start, pt)
        elif mode in ("brush", "healing") and self._stroke is not None:
            last = self._stroke["points"][-1]
            if math.hypot(pt[0] - last[0], pt[1] - last[1]) > 0.004:
                self._stroke["points"].append(list(pt))
                self.overlay_area.queue_draw()
        elif mode in ("vignette", "lens_blur"):
            layer.params["center"] = [pt[0], pt[1]]
            self._request_render(draft=True)
        elif mode == "text":
            layer.params["position"] = [pt[0], pt[1]]
            self._request_render(draft=True)
        self.overlay_area.queue_draw()

    def _update_crop(self, start, pt):
        x, y, w, h = self._crop_rect
        handle = self._crop_handle
        if handle == "new":
            self._crop_rect = [min(start[0], pt[0]), min(start[1], pt[1]),
                               abs(pt[0] - start[0]), abs(pt[1] - start[1])]
        elif handle == "move":
            nx = min(max(0.0, x + (pt[0] - start[0])), 1.0 - w)
            ny = min(max(0.0, y + (pt[1] - start[1])), 1.0 - h)
            self._crop_rect = [nx, ny, w, h]
        else:
            x0, y0, x1, y1 = x, y, x + w, y + h
            if "n" in handle:
                y0 = min(pt[1], y1 - 0.02)
            if "s" in handle:
                y1 = max(pt[1], y0 + 0.02)
            if "w" in handle:
                x0 = min(pt[0], x1 - 0.02)
            if "e" in handle:
                x1 = max(pt[0], x0 + 0.02)
            self._crop_rect = [x0, y0, x1 - x0, y1 - y0]

    def _on_canvas_release(self, gesture, dx, dy):
        if self.active_index is None:
            return
        mode = self._canvas_mode
        layer = self.stack.layers[self.active_index]
        if mode == "crop" and self._crop_rect:
            r = [max(0.0, min(1.0, v)) for v in self._crop_rect]
            if r[2] > 0.02 and r[3] > 0.02:
                aspect = layer.params.get("aspect", "Free")
                ratio = tools.CROP_ASPECTS.get(aspect)
                if ratio:
                    # Lock the aspect by shrinking the longer dimension, so
                    # the rectangle always stays inside what the user drew.
                    cur = (r[2] * self.full_size[0]) / max(
                        r[3] * self.full_size[1], 1e-6)
                    if cur > ratio:
                        r[2] = r[3] * ratio * self.full_size[1] / max(
                            self.full_size[0], 1)
                    else:
                        r[3] = r[2] / ratio * self.full_size[0] / max(
                            self.full_size[1], 1)
                layer.params["rect"] = r
                self._crop_rect = r
                self._request_render(draft=False)
        self._crop_handle = None
        if self._stroke is not None:
            self._stroke = None
            self._request_render(draft=False)
        elif mode in ("vignette", "lens_blur", "text"):
            self._request_render(draft=False)
        self._autosave()

    def _draw_overlay(self, area, cr, w, h):
        rect = self._image_rect()
        if rect is None or self._canvas_mode is None:
            return
        x0, y0, dw, dh = rect
        mode = self._canvas_mode
        if self.active_index is None or self.active_index >= len(self.stack):
            return
        layer = self.stack.layers[self.active_index]

        if mode == "crop" and self._crop_rect:
            cx, cy, cw, ch = self._crop_rect
            rx, ry = x0 + cx * dw, y0 + cy * dh
            rw, rh = cw * dw, ch * dh
            cr.set_source_rgba(0, 0, 0, 0.55)
            cr.rectangle(x0, y0, dw, dh)
            cr.rectangle(rx, ry, rw, rh)
            cr.set_fill_rule(1)          # even-odd: darken outside only
            cr.fill()
            cr.set_fill_rule(0)
            cr.set_source_rgba(1, 1, 1, 0.9)
            cr.set_line_width(1.5)
            cr.rectangle(rx, ry, rw, rh)
            cr.stroke()
            cr.set_source_rgba(1, 1, 1, 0.35)
            cr.set_line_width(1)
            for i in (1, 2):
                cr.move_to(rx + rw * i / 3, ry)
                cr.line_to(rx + rw * i / 3, ry + rh)
                cr.move_to(rx, ry + rh * i / 3)
                cr.line_to(rx + rw, ry + rh * i / 3)
            cr.stroke()
            cr.set_source_rgb(1, 1, 1)
            for (hx, hy) in ((rx, ry), (rx + rw, ry), (rx, ry + rh),
                             (rx + rw, ry + rh)):
                cr.rectangle(hx - 5, hy - 5, 10, 10)
            cr.fill()

        elif mode == "selective":
            for i, pt in enumerate(layer.params.get("points") or []):
                px, py = x0 + pt["x"] * dw, y0 + pt["y"] * dh
                # Drawn over the photo, where black alone would vanish on
                # dark areas: a white disc in a dark ring reads on anything.
                cr.set_source_rgba(0, 0, 0, 0.6)
                cr.arc(px, py, 11, 0, 2 * math.pi)
                cr.fill()
                cr.set_source_rgb(1, 1, 1)
                cr.arc(px, py, 8, 0, 2 * math.pi)
                cr.fill()
                cr.set_source_rgb(0.114, 0.114, 0.122)
                cr.select_font_face("Sans")
                cr.set_font_size(11)
                cr.move_to(px - 3, py + 4)
                cr.show_text(str(i + 1))

        elif mode in ("brush", "healing"):
            cr.set_source_rgba(1, 1, 1, 0.40)
            for stroke in layer.params.get("strokes") or []:
                r = float(stroke.get("radius", 0.04)) * math.hypot(dw, dh) * 0.5
                for (sx, sy) in stroke.get("points", []):
                    cr.arc(x0 + sx * dw, y0 + sy * dh, max(2, r), 0, 2 * math.pi)
                    cr.fill()

        elif mode in ("vignette", "lens_blur"):
            c = layer.params.get("center") or [0.5, 0.5]
            px, py = x0 + c[0] * dw, y0 + c[1] * dh
            cr.set_source_rgba(1, 1, 1, 0.85)
            cr.set_line_width(1.5)
            cr.arc(px, py, 10, 0, 2 * math.pi)
            cr.stroke()
            cr.move_to(px - 16, py); cr.line_to(px + 16, py)
            cr.move_to(px, py - 16); cr.line_to(px, py + 16)
            cr.stroke()

    def shutdown(self):
        self._render.stop()
