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
from ..i18n import _, N_, ngettext

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gdk, Gio, Gtk  # noqa: E402

import numpy as np

from .. import imageio as iio
from ..engine import looks as looks_mod
from ..engine import autoimprove, ops, tools
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
        "copy-adjustments": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "hint": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
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
        self.group: list[tuple[Path, int | None]] = []     # the photos edited together: one edit for all of them
        self.group_index = 0
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
        box.append(self._build_strip())
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

    def _build_strip(self):
        """When several photos are edited together, all of them lie in a row under the picture, as the boards of a
        document do: the one in use is ringed, and what is done to it is done to every one."""
        self.strip_rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_UP, reveal_child=False)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=8, margin_bottom=10,
                        margin_start=12, margin_end=12)
        outer.add_css_class("pika-strip")
        self.strip_note = Gtk.Label(xalign=0.5)
        self.strip_note.add_css_class("pika-dim")
        self.strip_note.add_css_class("caption")
        outer.append(self.strip_note)
        self.strip_box = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        scroller = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER, hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                      propagate_natural_height=True)
        scroller.set_child(self.strip_box)
        outer.append(scroller)
        self.strip_rev.set_child(outer)
        self.strip_rev.set_visible(False)
        self._strip_tiles: list[Gtk.Button] = []
        return self.strip_rev

    def _fill_strip(self):
        while (c := self.strip_box.get_first_child()) is not None:
            self.strip_box.remove(c)
        self._strip_tiles = []
        many = len(self.group) > 1
        self.strip_rev.set_visible(many)
        self.strip_rev.set_reveal_child(many)
        if not many:
            return
        self.strip_note.set_text(ngettext("{count} photo share this edit", "{count} photos share this edit",
                                          len(self.group)).format(count=len(self.group)))
        for i, (path, _pid) in enumerate(self.group):
            btn = Gtk.Button()
            btn.add_css_class("flat")
            btn.add_css_class("pika-strip-tile")
            pic = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
            pic.set_size_request(84, 60)
            pic.set_overflow(Gtk.Overflow.HIDDEN)
            pic.add_css_class("pika-strip-pic")
            btn.set_child(pic)
            btn.set_tooltip_text(Path(path).name)
            btn.connect("clicked", lambda _b, i=i: self.switch_to(i))
            self.strip_box.append(btn)
            self._strip_tiles.append(btn)

            def done(thumb, pic=pic):
                def put():
                    try:
                        pic.set_paintable(Gdk.Texture.new_from_filename(str(thumb)))
                    except Exception:
                        pass
                    return False
                if thumb:
                    GLib.idle_add(put)
            try:
                self._thumbs.request(path, 160, done)
            except Exception:
                pass
        self._sync_strip()

    def _sync_strip(self):
        for i, btn in enumerate(self._strip_tiles):
            (btn.add_css_class if i == self.group_index else btn.remove_css_class)("selected")

    def open_many(self, photos, start: int = 0):
        """Edit several photos together: ``photos`` is [(path, photo_id)]. They all take the edit of the first one
        and keep taking whatever is done from here on."""
        photos = [(Path(p), i) for p, i in photos]
        if not photos:
            return
        start = max(0, min(start, len(photos) - 1))
        self.open(photos[start][0], photos[start][1])
        self.group = photos
        self.group_index = start
        self._fill_strip()
        self._autosave()                       # the others take the edit at once, not only after the next change

    def switch_to(self, index: int):
        """Another photo of the group in the canvas, with the edit they share."""
        if not (0 <= index < len(self.group)) or index == self.group_index or self.source is None:
            return
        self.flush()
        self.group_index = index
        path, pid = self.group[index]
        self.photo_path, self.photo_id = Path(path), pid
        self.title_label.set_text(self.photo_path.name)
        self.renderer.invalidate()
        self.source = None
        self.active_index = None
        self._close_params()
        self._sync_strip()
        self._load_source()

    def _build_canvas_bar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                      margin_top=6, margin_bottom=6, margin_start=10,
                      margin_end=10)
        back = Gtk.Button(icon_name="pk-arrow-left-symbolic",
                          tooltip_text=_("Back to library"))
        back.connect("clicked", lambda *_: (self.flush(), self.emit("closed")))
        bar.append(back)

        self.title_label = Gtk.Label(xalign=0.0, hexpand=True)
        self.title_label.add_css_class("pika-dim")
        bar.append(self.title_label)

        self.undo_btn = Gtk.Button(icon_name="pk-arrow-counter-clockwise-symbolic",
                                   tooltip_text=_("Undo (Ctrl+Z)"), sensitive=False)
        self.undo_btn.connect("clicked", lambda *_: self.undo())
        self.redo_btn = Gtk.Button(icon_name="pk-arrow-clockwise-symbolic",
                                   tooltip_text=_("Redo (Ctrl+Shift+Z)"),
                                   sensitive=False)
        self.redo_btn.connect("clicked", lambda *_: self.redo())
        bar.append(self.undo_btn)
        bar.append(self.redo_btn)

        compare = Gtk.ToggleButton(icon_name="pk-eye-symbolic",
                                   tooltip_text=_("Hold to compare with original (M)"))
        compare.connect("toggled", self._on_compare)
        bar.append(compare)
        self.compare_btn = compare

        copy = Gtk.Button(icon_name="pk-copy-symbolic",
                          tooltip_text=_("Copy adjustments to paste on other photos"))
        copy.connect("clicked", lambda *_: (self.flush(), self.emit("copy-adjustments")))
        bar.append(copy)

        revert = Gtk.Button(icon_name="pk-eraser-symbolic",
                            tooltip_text=_("Remove all edits"))
        revert.connect("clicked", self._on_revert)
        bar.append(revert)

        export = Gtk.Button(label=_("Export…"))
        export.add_css_class("suggested-action")
        export.connect("clicked", self._on_export)
        bar.append(export)
        # The editor replaces the library's header, so it carries the
        # window buttons too - same place, same style as everywhere else.
        from .chrome import add_window_controls
        add_window_controls(bar)
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
            "pk-sliders-horizontal-symbolic")
        self.switcher.add_titled_with_icon(
            self._build_looks_page(), "looks", _("Looks"),
            "pk-magic-wand-symbolic")
        self.switcher.add_titled_with_icon(
            self._build_layers_page(), "layers", _("Edits"),
            "pk-list-bullets-symbolic")

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

    # What Simple mode offers, in plain words: (tool, name, icon)
    SIMPLE_TOOLS = (
        ("tune", N_("Light and colour"), "sliders-horizontal"),
        ("crop", N_("Crop"), "crop"),
        ("rotate", N_("Rotate"), "arrow-clockwise"),
        ("details", N_("Sharper"), "magnifying-glass-plus"),
        ("white_balance", N_("Warmer or cooler"), "thermometer"),
        ("black_white", N_("Black and white"), "circle-half-tilt"),
    )

    def _build_tools_page(self):
        """Tools come in two layers over the same edits: Simple, a few things
        in plain words with a one-click Improve, and Advanced, every tool."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        modes = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0,
                        halign=Gtk.Align.CENTER, margin_top=8, margin_bottom=4)
        modes.add_css_class("linked")
        self.mode_simple = Gtk.ToggleButton(label=_("Simple"))
        self.mode_advanced = Gtk.ToggleButton(label=_("Advanced"),
                                              group=self.mode_simple)
        modes.append(self.mode_simple)
        modes.append(self.mode_advanced)
        box.append(modes)
        self.mode_stack = Gtk.Stack(vexpand=True)
        self.mode_stack.add_named(self._build_simple_page(), "simple")
        self.mode_stack.add_named(self._build_advanced_tools_page(), "advanced")
        box.append(self.mode_stack)
        advanced = self.settings.get("editor_mode", "simple") == "advanced"
        self.mode_advanced.set_active(advanced)
        self.mode_simple.set_active(not advanced)
        self.mode_stack.set_visible_child_name("advanced" if advanced else "simple")
        self.mode_advanced.connect("toggled", self._on_mode_toggled)
        return box

    def _on_mode_toggled(self, btn):
        mode = "advanced" if btn.get_active() else "simple"
        self.mode_stack.set_visible_child_name(mode)
        self.settings.set("editor_mode", mode)

    def _build_simple_page(self):
        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      margin_top=8, margin_bottom=12, margin_start=10,
                      margin_end=10)
        improve = Gtk.Button()
        improve.add_css_class("suggested-action")
        row = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER, margin_top=6,
                      margin_bottom=6)
        row.append(Gtk.Image(icon_name="pk-sparkle-symbolic", pixel_size=18))
        row.append(Gtk.Label(label=_("Improve")))
        improve.set_child(row)
        improve.set_tooltip_text(_("Fixes the light and colour with one click"))
        improve.connect("clicked", self._on_improve)
        box.append(improve)
        hint = Gtk.Label(label=_("One click. You can change or undo it after."),
                         wrap=True, xalign=0.0)
        hint.add_css_class("pika-dim")
        box.append(hint)
        header = Gtk.Label(label=_("Quick fixes"), xalign=0.0)
        header.add_css_class("pika-tool-group")
        box.append(header)
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                           max_children_per_line=3, min_children_per_line=3,
                           homogeneous=True, row_spacing=2, column_spacing=2)
        for tool_id, name, icon in self.SIMPLE_TOOLS:
            btn = Gtk.Button()
            btn.add_css_class("flat")
            btn.add_css_class("pika-tool-tile")
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            inner.append(Gtk.Image(icon_name=f"pk-{icon}-symbolic", pixel_size=22))
            label = Gtk.Label(label=_(name), wrap=True, justify=2,
                              max_width_chars=11)
            label.add_css_class("pika-tool-name")
            inner.append(label)
            btn.set_child(inner)
            spec = tools.REGISTRY.get(tool_id)
            btn.set_tooltip_text(_(spec.description) if spec and spec.description
                                 else _(name))
            btn.connect("clicked", self._on_pick_tool, tool_id)
            flow.append(btn)
        box.append(flow)
        more = Gtk.Button(label=_("All tools"))
        more.add_css_class("flat")
        more.set_margin_top(10)
        more.connect("clicked", lambda *_a: self.mode_advanced.set_active(True))
        box.append(more)
        scroller.set_child(box)
        return scroller

    def _on_improve(self, _btn):
        if self.source is None:
            return
        self.stack.snapshot()
        for tool_id, params in autoimprove.suggest(self.source):
            # An Improve already made is replaced, not piled on.
            old = next((i for i, l in enumerate(self.stack.layers)
                        if l.tool == tool_id and l.params.get("_improve")), None)
            params = dict(params, _improve=True)
            if old is not None:
                self.stack.layers.pop(old)
            self.stack.add(tool_id, params, record=False)
        self._refresh_layers()
        self._request_render(draft=False)
        self._autosave()
        self._update_history_buttons()
        self._show_toast_hint(_("Improved. Open Edits to change or remove it."))

    def _show_toast_hint(self, text):
        self.emit("hint", text)

    def _build_advanced_tools_page(self):
        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                      margin_top=8, margin_bottom=12, margin_start=10,
                      margin_end=10)
        icons = {
            "tune": "sliders-horizontal", "details": "magnifying-glass-plus",
            "curves": "wave-sine", "white_balance": "thermometer",
            "tonal_contrast": "circle-half", "hdr": "mountains",
            "glamour": "sparkle", "drama": "lightning",
            "vignette": "selection-background", "vintage": "film-strip",
            "grainy_film": "dots-nine", "retrolux": "camera",
            "grunge": "scribble", "black_white": "circle-half-tilt",
            "noir": "moon", "lens_blur": "aperture", "crop": "crop",
            "rotate": "arrow-clockwise", "perspective": "perspective",
            "expand": "arrows-out", "selective": "circle-dashed",
            "brush": "paint-brush", "healing": "bandaids", "portrait": "user",
            "head_pose": "smiley", "double_exposure": "stack",
            "text": "text-t", "frames": "frame-corners",
        }
        # The tools in rows that are always whole: six in two rows of three, four in two of two, and so on. Whatever a new
        # tool brings that is not named here goes to the end of its own group.
        layout = (
            (N_("Adjust"), ("tune", "details", "curves", "white_balance", "black_white", "tonal_contrast")),
            (N_("Effects"), ("hdr", "glamour", "drama", "vignette", "lens_blur", "double_exposure")),
            (N_("Geometry"), ("crop", "rotate", "perspective", "expand")),
            (N_("Local"), ("selective", "brush", "healing")),
            (N_("Portrait"), ("portrait", "head_pose")),
            (N_("Overlay"), ("text", "frames")),
        )
        by_id = {t.id: t for ts in tools.groups().values() for t in ts}
        placed: set[str] = set()
        sections = []
        for title, ids in layout:
            got = [by_id[i] for i in ids if i in by_id]
            placed.update(t.id for t in got)
            sections.append((title, got))
        for group, tool_list in tools.groups().items():
            rest = [t for t in tool_list if t.id in by_id and t.id not in placed]
            if rest:
                sections.append((group, rest))
        for group, tool_list in sections:
            if not tool_list:
                continue
            header = Gtk.Label(label=_(group), xalign=0.0)
            header.add_css_class("pika-tool-group")
            box.append(header)
            cols = 3 if len(tool_list) % 3 == 0 or len(tool_list) > 4 else 2
            flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                               max_children_per_line=cols,
                               min_children_per_line=cols,
                               homogeneous=True, row_spacing=6,
                               column_spacing=6)
            for tool in tool_list:
                btn = Gtk.Button()
                btn.add_css_class("flat")
                btn.add_css_class("pika-tool-tile")
                inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
                img = Gtk.Image(icon_name="pk-%s-symbolic" % icons.get(
                                    tool.id, "sliders-horizontal"),
                                pixel_size=22)
                name = Gtk.Label(label=_(tool.name), wrap=True, justify=Gtk.Justification.CENTER,
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
    
    def _on_apply_look(self, _btn, name):
        if self.source is None:
            return
        self.stack.apply_look(name)
        self._refresh_layers()
        self._request_render(draft=False)
        self._autosave()
        self._update_history_buttons()
    

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
        back = Gtk.Button(icon_name="pk-arrow-left-symbolic")
        back.add_css_class("flat")
        back.connect("clicked", lambda *_: self._close_params())
        head.append(back)
        self.param_title = Gtk.Label(xalign=0.0, hexpand=True)
        self.param_title.add_css_class("heading")
        head.append(self.param_title)
        drop = Gtk.Button(icon_name="pk-trash-symbolic",
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
        self.group = [(self.photo_path, photo_id)]
        self.group_index = 0
        self._fill_strip()
        self._refresh_layers()
        self._load_source()

    def _load_source(self):
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
        # While the crop is being set, show the photo as it is before that
        # crop, whole: drawing the rectangle over the already-cropped result
        # made every drag crop the crop again, and the rectangle jumped.
        upto = (self.active_index if self._canvas_mode == "crop"
                and self.active_index is not None else None)

        def work():
            img = source
            if draft and max(img.shape[:2]) > drag_side:
                img = ops.fit_within(img, drag_side)
            scale = (max(img.shape[:2]) / max(full)) if max(full) else 1.0
            return self.renderer.render(
                img, stack, scale=scale, full_size=full, draft=draft, upto=upto,
                face_detection=bool(self.settings.get("face_detection", True)))

        if not draft:
            self.spinner.start()
            self._pending_full = None
        self._render.submit(work, tag="draft" if draft else "full")

    def _autosave(self):
        if not self.settings.get("edit_autosave", True) or not self.photo_path:
            return
        for path, pid in (self.group or [(self.photo_path, self.photo_id)]):      # one edit, kept for each photo of the group
            sidecar = self.library.edit_sidecar(path)
            try:
                if len(self.stack):
                    self.stack.save(sidecar, str(path))
                elif sidecar.exists():
                    sidecar.unlink()
                if pid is not None:
                    self.catalog.note_edit(pid, len(self.stack))
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

        was_crop = self._canvas_mode == "crop"
        self._canvas_mode = spec.id if spec.id in CANVAS_TOOLS else None
        if spec.id == "crop":
            r = layer.params.get("rect") or [0, 0, 1, 1]
            self._crop_rect = list(r)
        if was_crop or self._canvas_mode == "crop":
            # entering Crop shows the whole photo; leaving it shows the crop
            self._request_render(draft=False)

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
            self._autosave_soon()

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
        self._autosave_soon()

    def _autosave_soon(self):
        """Save a moment after the last change while a control is moving.
        Only saving on release lost the value whenever no release came - a
        key press, the scroll wheel, typing a number - and going back then
        kept the tool with all its values at zero."""
        timer = getattr(self, "_save_timer", 0)
        if timer:
            GLib.source_remove(timer)

        def fire():
            self._save_timer = 0
            self._autosave()
            return GLib.SOURCE_REMOVE
        self._save_timer = GLib.timeout_add(400, fire)

    def flush(self):
        """Save now: called whenever the editor is left, by any way out."""
        timer = getattr(self, "_save_timer", 0)
        if timer:
            GLib.source_remove(timer)
            self._save_timer = 0
        if getattr(self, "photo_path", None):
            self._autosave()

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
        if layer.tool == "crop" and param.key == "aspect":
            # choosing Square, 16:9... reshapes the rectangle at once
            rect = layer.params.get("rect") or [0.0, 0.0, 1.0, 1.0]
            fitted = self._fit_crop_aspect(rect, value)
            self.stack.set_params(index, {"rect": fitted})
            self._crop_rect = list(fitted)
            self.overlay_area.queue_draw()
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
            delete = Gtk.Button(icon_name="pk-trash-symbolic",
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
        was_crop = self._canvas_mode == "crop"
        self.active_index = None
        self._canvas_mode = None
        self.panel_stack.set_visible_child_name("browse")
        self.overlay_area.queue_draw()
        if was_crop:
            self._request_render(draft=False)       # show the crop applied again

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
        if len(self.group) > 1:
            dialog.set_body(_("The photos are untouched either way — this clears the adjustments they share."))
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
        paths = [p for p, _i in self.group] or [self.photo_path]
        ExportDialog(self.get_root(), self.library, self.settings,
                     paths, stack=self.stack, catalog=self.catalog).present(self.get_root())

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

    def _fit_crop_aspect(self, r, aspect):
        """Shape a crop rectangle to an aspect, keeping it inside what was
        drawn and centred on it. "Original" is the photo's own shape.

        Measured on the photo as it arrives at the crop - turned, if a
        Rotate came first - which is what is on screen while cropping."""
        ratio = tools.CROP_ASPECTS.get(aspect)
        if ratio is None:
            return list(r)
        if self.rendered is not None:
            ih, iw = self.rendered.shape[:2]
        else:
            iw, ih = self.full_size
        iw, ih = max(iw, 1), max(ih, 1)
        if not ratio:
            ratio = iw / ih
        x, y, w, h = r
        cur = (w * iw) / max(h * ih, 1e-6)
        if cur > ratio:
            nw, nh = h * ratio * ih / iw, h
        else:
            nw, nh = w, w / ratio * iw / ih
        nx = min(max(0.0, x + (w - nw) / 2), 1.0 - nw)
        ny = min(max(0.0, y + (h - nh) / 2), 1.0 - nh)
        return [nx, ny, nw, nh]

    def _crop_hit(self, pt):
        """What a press at ``pt`` grabs: a corner, an edge, the whole
        rectangle to move it, or nothing (a new crop is drawn)."""
        if not self._crop_rect:
            return None
        x, y, w, h = self._crop_rect
        # About 14 screen pixels either way, however big the photo is shown.
        rect = self._image_rect()
        tx = 14.0 / rect[2] if rect and rect[2] else 0.02
        ty = 14.0 / rect[3] if rect and rect[3] else 0.02
        near_l, near_r = abs(pt[0] - x) < tx, abs(pt[0] - (x + w)) < tx
        near_t, near_b = abs(pt[1] - y) < ty, abs(pt[1] - (y + h)) < ty
        within_x = x - tx < pt[0] < x + w + tx
        within_y = y - ty < pt[1] < y + h + ty
        if near_t and near_l:
            return "nw"
        if near_t and near_r:
            return "ne"
        if near_b and near_l:
            return "sw"
        if near_b and near_r:
            return "se"
        if near_t and within_x:
            return "n"
        if near_b and within_x:
            return "s"
        if near_l and within_y:
            return "w"
        if near_r and within_y:
            return "e"
        if x < pt[0] < x + w and y < pt[1] < y + h:
            # A rectangle still covering the whole photo cannot be moved:
            # dragging inside it draws the crop instead.
            if w >= 0.98 and h >= 0.98:
                return None
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
                r = self._fit_crop_aspect(r, layer.params.get("aspect", "Free"))
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
