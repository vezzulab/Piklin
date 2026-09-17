"""The workshop: one creation, the whole window, and what you can change
about it.

The page itself is the thing being edited, so what is shown is the page -
drawn as it will be saved, not an approximation with handles over it.
Every control redraws it, off the main loop, at the size the window has
room for; the file is only written when Save is pressed.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gdk, GObject, Gtk  # noqa: E402

from .. import create, creations
from ..i18n import N_, _, ngettext

# How large the drawing shown on screen is. Big enough to judge a collage
# on a large display, small enough to redraw while a slider moves.
PREVIEW_SIDE = 1400

SHAPES = (
    ("square", N_("Square")),
    ("portrait", N_("Portrait")),
    ("landscape", N_("Landscape")),
    ("wide", N_("Widescreen")),
    ("a4", N_("A4 page")),
    ("a4-landscape", N_("A4 sideways")),
    ("letter", N_("Letter page")),
)

STYLES = (
    ("mosaic", N_("Mosaic")),
    ("grid", N_("Grid")),
    ("feature", N_("One large")),
)

# The themes, named for the panel. The look itself lives in create.THEMES,
# so a theme is a thing the page carries, not a thing this window does.
THEMES = (
    ("clean", N_("Clean")),
    ("gallery", N_("Gallery")),
    ("polaroid", N_("Polaroid")),
    ("cream", N_("Cream")),
    ("midnight", N_("Midnight")),
    ("party", N_("Party")),
    ("hearts", N_("Hearts")),
    ("winter", N_("Winter")),
    ("little", N_("Little One")),
)

# How much of the page the words get when there are any.
TEXT_ROOM = 0.11


class CreationView(Gtk.Box):
    """A page of the main window's stack, opened with ``open()``."""

    __gtype_name__ = "PikaCreationView"
    __gsignals__ = {
        "closed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "saved": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }

    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        self.library = window.library
        self.catalog = window.catalog
        self.creation: creations.Creation | None = None
        self._aspects: list[float] = []
        self._style = "mosaic"
        self._chosen = -1                 # which photo is picked, if any
        self._draw_timer = 0
        self._token = 0
        self._busy = False
        self._dirty = False

        view = Adw.ToolbarView()
        view.add_top_bar(self._header())
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        body.append(self._controls())
        body.append(self._stage())
        view.set_content(body)
        self.append(view)

    # -- the bar at the top ---------------------------------------------
    def _header(self):
        header = Adw.HeaderBar()
        header.add_css_class("pika-header")
        back = Gtk.Button(icon_name="go-previous-symbolic",
                          tooltip_text=_("Back"))
        back.add_css_class("flat")
        back.connect("clicked", lambda *_a: self._close())
        header.pack_start(back)

        self.name_entry = Gtk.Entry(placeholder_text=_("Name this creation"),
                                    width_chars=26, xalign=0.5)
        self.name_entry.add_css_class("pika-create-name")
        self.name_entry.connect("changed", lambda *_a: self._touch())
        header.set_title_widget(self.name_entry)

        self.save_btn = Gtk.Button(label=_("Save to Library"))
        self.save_btn.add_css_class("suggested-action")
        self.save_btn.add_css_class("pill")
        self.save_btn.connect("clicked", lambda *_a: self._save())
        header.pack_end(self.save_btn)

        export = Gtk.Button(label=_("Export…"))
        export.add_css_class("flat")
        export.connect("clicked", lambda *_a: self._export())
        header.pack_end(export)
        return header

    # -- the column of controls -----------------------------------------
    def _controls(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                      margin_top=16, margin_bottom=16,
                      margin_start=16, margin_end=16)
        box.set_size_request(268, -1)
        box.add_css_class("pika-create-controls")

        self.shape_drop = self._drop(_("Shape"), [s[1] for s in SHAPES],
                                     self._on_shape, box)
        self.style_drop = self._drop(_("Arrangement"), [s[1] for s in STYLES],
                                     self._on_style, box)
        self.theme_drop = self._drop(_("Theme"), [t[1] for t in THEMES],
                                     self._on_theme, box)

        self.gap = self._slider(_("Spacing"), 0.0, 0.05, 0.002, box)
        self.gap.connect("value-changed", lambda *_a: self._on_gap())
        self.corner = self._slider(_("Rounded corners"), 0.0, 0.06, 0.002, box)
        self.corner.connect("value-changed", lambda *_a: self._on_corner())

        words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        label = Gtk.Label(label=_("Words"), xalign=0.0)
        label.add_css_class("heading")
        words.append(label)
        self.title_entry = Gtk.Entry(placeholder_text=_("A title, a date, a name"))
        self.title_entry.connect("changed", lambda *_a: self._on_title())
        words.append(self.title_entry)
        words.append(self._font_drop())
        box.append(words)

        self.faces = Gtk.CheckButton(label=_("Keep faces in the picture"),
                                     active=True)
        self.faces.set_tooltip_text(
            _("Photos are cropped to fit their place; this moves the crop so "
              "the faces stay in."))
        self.faces.connect("toggled", lambda *_a: self._on_faces())
        box.append(self.faces)

        shuffle = Gtk.Button(label=_("Shuffle"))
        shuffle.connect("clicked", lambda *_a: self._shuffle())
        box.append(shuffle)

        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                    vexpand=True, child=box)
        scroll.set_size_request(300, -1)
        return scroll

    def _font_drop(self):
        """The typeface for the words - every name written in the typeface
        it names, because that is the only way to choose one."""
        names = Gtk.StringList()
        self._families = list(create.FAMILY_ORDER)
        for key in self._families:
            names.append(create.family_name(key))

        factory = Gtk.SignalListItemFactory()

        def setup(_f, item):
            item.set_child(Gtk.Label(xalign=0.0, ellipsize=3))

        def bind(_f, item):
            label = item.get_child()
            name = item.get_item().get_string()
            label.set_text(name)
            # Fontconfig has been told about the typefaces Piklin carries
            # (see app.py), so the list can be drawn in them.
            label.set_attributes(None)
            from gi.repository import Pango
            attrs = Pango.AttrList()
            attrs.insert(Pango.attr_family_new(name))
            attrs.insert(Pango.attr_size_new(int(13 * Pango.SCALE)))
            label.set_attributes(attrs)

        factory.connect("setup", setup)
        factory.connect("bind", bind)
        self.font_drop = Gtk.DropDown(model=names, factory=factory)
        self.font_drop.set_tooltip_text(_("The typeface for the words"))
        self.font_drop.connect("notify::selected", self._on_font)
        return self.font_drop

    def _on_font(self, drop, _p):
        page = self.creation.page
        if not page.texts:
            return
        key = self._families[drop.get_selected()]
        for t in page.texts:
            t.family = key
            t.chosen_family = True
        self._touch()
        self._render_soon()

    def _drop(self, title, labels, on_change, box):
        group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        head = Gtk.Label(label=title, xalign=0.0)
        head.add_css_class("heading")
        group.append(head)
        drop = Gtk.DropDown.new_from_strings([_(x) for x in labels])
        drop.connect("notify::selected", on_change)
        group.append(drop)
        box.append(group)
        return drop

    def _slider(self, title, low, high, step, box):
        group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        head = Gtk.Label(label=title, xalign=0.0)
        head.add_css_class("heading")
        group.append(head)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, low, high, step)
        scale.set_draw_value(False)
        group.append(scale)
        box.append(group)
        return scale

    # -- the page itself -------------------------------------------------
    def _stage(self):
        self.picture = Gtk.Picture(hexpand=True, vexpand=True,
                                   content_fit=Gtk.ContentFit.CONTAIN)
        self.picture.set_margin_top(18)
        self.picture.set_margin_bottom(6)
        self.picture.set_margin_start(6)
        self.picture.set_margin_end(18)
        click = Gtk.GestureClick()
        click.connect("pressed", self._on_click)
        self.picture.add_controller(click)

        self.spinner = Gtk.Spinner(halign=Gtk.Align.END, valign=Gtk.Align.START,
                                   margin_top=24, margin_end=28)
        overlay = Gtk.Overlay()
        overlay.set_child(self.picture)
        overlay.add_overlay(self.spinner)

        stage = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                        hexpand=True, vexpand=True)
        stage.append(overlay)
        stage.append(self._photo_bar())
        return stage

    def _photo_bar(self):
        """What can be done to the one photo that is picked."""
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                      halign=Gtk.Align.CENTER, margin_bottom=16)
        self.pick_label = Gtk.Label()
        self.pick_label.add_css_class("pika-dim")
        bar.append(self.pick_label)
        for icon, tip, cb in (
                ("go-previous-symbolic", _("Move earlier"), lambda *_a: self._move(-1)),
                ("go-next-symbolic", _("Move later"), lambda *_a: self._move(1)),
                ("zoom-out-symbolic", _("Show more of this photo"),
                 lambda *_a: self._zoom(1 / 1.12)),
                ("zoom-in-symbolic", _("Fill more of its place"),
                 lambda *_a: self._zoom(1.12)),
                ("list-remove-symbolic", _("Take this photo out"),
                 lambda *_a: self._remove())):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip)
            b.add_css_class("flat")
            b.connect("clicked", cb)
            bar.append(b)
        self.photo_bar = bar
        bar.set_visible(False)
        return bar

    # ==================================================================
    # opening
    # ==================================================================
    def open(self, kind: str, items, creation=None) -> None:
        """Start a new creation from these photos, or reopen a saved one."""
        if creation is not None:
            self.creation = creation
            self._aspects = [1.0] * len(creation.page.slots)
            for i, slot in enumerate(creation.page.slots):
                row = self.catalog.photo(slot.photo_id) if slot.photo_id else None
                if row is not None and row["width"] and row["height"]:
                    self._aspects[i] = row["width"] / row["height"]
        else:
            from .create_panel import as_photo
            photos = [as_photo(i) for i in items]
            self.creation = creations.new(kind, self._suggest_name(photos))
            shape = "square" if kind == "collage" else "portrait"
            page = create.make_page(photos, shape=shape,
                                    style="mosaic" if kind == "collage" else "feature",
                                    margin=0.035 if kind == "collage" else 0.06,
                                    corner=0.01 if kind == "collage" else 0.0)
            self.creation.pages = [page]
            self._aspects = [(p["width"] / p["height"])
                             if p["width"] and p["height"] else 1.0 for p in photos]
            if kind == "poster":
                page.background = "#111111"
                page.texts.append(create.Text(
                    text="", y=0.88, size=0.05, color="#ffffff",
                    weight="bold", tracking=0.12))
        self._chosen = -1
        self._dirty = False
        self._fill_controls()
        self._render_soon(0)
        if self.faces.get_active():
            self._run_faces()

    def _suggest_name(self, photos) -> str:
        from .create_panel import span_of
        span = span_of(photos) if photos else ""
        return _("Collage {when}").format(when=span) if span else _("Collage")

    def _fill_controls(self):
        page = self.creation.page
        self.name_entry.set_text(self.creation.name)
        for i, (key, _label) in enumerate(SHAPES):
            if key == page.shape:
                self.shape_drop.set_selected(i)
        for i, (key, _label) in enumerate(STYLES):
            if key == self._style:
                self.style_drop.set_selected(i)
        for i, (key, _label) in enumerate(THEMES):
            if key == page.theme:
                self.theme_drop.set_selected(i)
        self.gap.set_value(page.gap)
        self.corner.set_value(page.corner)
        self.title_entry.set_text(page.texts[0].text if page.texts else "")
        self._show_font()

    # ==================================================================
    # changing it
    # ==================================================================
    def _touch(self):
        self._dirty = True

    def _rearrange(self):
        create.arrange(self.creation.page, self._aspects, self._style)
        self._touch()
        self._render_soon()

    def _on_shape(self, drop, _p):
        self.creation.page.shape = SHAPES[drop.get_selected()][0]
        self._rearrange()

    def _on_style(self, drop, _p):
        self._style = STYLES[drop.get_selected()][0]
        self._rearrange()

    def _on_theme(self, drop, _p):
        create.apply_theme(self.creation.page, THEMES[drop.get_selected()][0])
        self.gap.set_value(self.creation.page.gap)
        self.corner.set_value(self.creation.page.corner)
        self._show_font()
        self._rearrange()

    def _show_font(self):
        """Point the typeface list at whatever the page is using."""
        page = self.creation.page
        key = page.texts[0].family if page.texts else "inter"
        if key in self._families:
            self.font_drop.set_selected(self._families.index(key))

    def _on_gap(self):
        self.creation.page.gap = self.gap.get_value()
        self._rearrange()

    def _on_corner(self):
        self.creation.page.corner = self.corner.get_value()
        self._touch()
        self._render_soon()

    def _on_title(self):
        page = self.creation.page
        text = self.title_entry.get_text()
        if not page.texts:
            page.texts.append(create.Text(text=text, size=0.038))
            create.apply_theme(page, page.theme)      # the theme's own lettering
        else:
            page.texts[0].text = text
        # Words need a band of their own, or they land on a face. The
        # band is given back when the words are taken away.
        page.text_room = TEXT_ROOM if text.strip() else 0.0
        self._rearrange()

    def _on_faces(self):
        if self.faces.get_active():
            self._run_faces()
        else:
            for slot in self.creation.page.slots:
                slot.cx = slot.cy = 0.5
            self._touch()
            self._render_soon()

    def _run_faces(self):
        page = self.creation.page
        token = self._token

        def work():
            try:
                create.focus_slots(page)
            except Exception:
                return
            GLib.idle_add(done)

        def done():
            if token == self._token:
                self._render_soon(0)
            return False
        threading.Thread(target=work, daemon=True).start()

    def _shuffle(self):
        import random
        page = self.creation.page
        order = list(range(len(page.slots)))
        random.shuffle(order)
        page.slots = [page.slots[i] for i in order]
        self._aspects = [self._aspects[i] for i in order]
        self._chosen = -1
        self._show_pick()
        self._rearrange()

    # -- one photo -------------------------------------------------------
    def _on_click(self, _gesture, _n, x, y):
        """Pick the photo under the pointer, in the page's own terms."""
        page = self.creation.page
        paintable = self.picture.get_paintable()
        if paintable is None:
            return
        pw, ph = paintable.get_intrinsic_width(), paintable.get_intrinsic_height()
        w, h = self.picture.get_width(), self.picture.get_height()
        if not pw or not ph or not w or not h:
            return
        # Gtk.Picture with CONTAIN puts the drawing in the middle.
        scale = min(w / pw, h / ph)
        dw, dh = pw * scale, ph * scale
        fx = (x - (w - dw) / 2) / dw
        fy = (y - (h - dh) / 2) / dh
        for i, slot in enumerate(page.slots):
            if slot.x <= fx <= slot.x + slot.w and slot.y <= fy <= slot.y + slot.h:
                self._chosen = -1 if self._chosen == i else i
                break
        else:
            self._chosen = -1
        self._show_pick()

    def _show_pick(self, redraw: bool = True):
        ok = 0 <= self._chosen < len(self.creation.page.slots)
        self.photo_bar.set_visible(ok)
        if ok:
            self.pick_label.set_text(_("Photo {n} of {total}").format(
                n=self._chosen + 1, total=len(self.creation.page.slots)))
        if redraw:
            self._render_soon(40)

    def _move(self, step):
        page = self.creation.page
        i = self._chosen
        j = i + step
        if not (0 <= i < len(page.slots) and 0 <= j < len(page.slots)):
            return
        page.slots[i], page.slots[j] = page.slots[j], page.slots[i]
        self._aspects[i], self._aspects[j] = self._aspects[j], self._aspects[i]
        self._chosen = j
        self._show_pick()
        self._rearrange()

    def _zoom(self, by):
        i = self._chosen
        if not (0 <= i < len(self.creation.page.slots)):
            return
        slot = self.creation.page.slots[i]
        slot.zoom = max(1.0, min(3.0, slot.zoom * by))
        self._touch()
        self._render_soon()

    def _remove(self):
        page = self.creation.page
        i = self._chosen
        if not (0 <= i < len(page.slots)) or len(page.slots) <= 1:
            return
        page.slots.pop(i)
        self._aspects.pop(i)
        self._chosen = -1
        self._show_pick()
        self._rearrange()

    # ==================================================================
    # drawing
    # ==================================================================
    def _render_soon(self, delay: int = 140):
        if self._draw_timer:
            GLib.source_remove(self._draw_timer)
        self._draw_timer = GLib.timeout_add(max(1, delay), self._render_now)

    def _render_now(self):
        self._draw_timer = 0
        self._token += 1
        token = self._token
        page = create.Page.from_dict(self.creation.page.to_dict())
        picked = self._chosen if 0 <= self._chosen < len(page.slots) else None
        self.spinner.start()
        self.spinner.set_visible(True)

        def work():
            try:
                image = create.render(page, PREVIEW_SIDE, highlight=picked, edge=True)
            except Exception:
                image = None
            GLib.idle_add(show, image)

        def show(image):
            if token != self._token:
                return False
            self.spinner.stop()
            self.spinner.set_visible(False)
            if image is not None:
                import numpy as np
                from .render_thread import texture_from_array
                self.picture.set_paintable(
                    texture_from_array(np.asarray(image, dtype=np.uint8)))
            return False
        threading.Thread(target=work, daemon=True).start()
        return GLib.SOURCE_REMOVE

    # ==================================================================
    # keeping it
    # ==================================================================
    def _save(self):
        if self.creation is None or self._busy:
            return
        self._busy = True
        self.creation.name = (self.name_entry.get_text().strip()
                              or _("Collage"))
        self.save_btn.set_sensitive(False)
        self.save_btn.set_label(_("Saving…"))
        creation = self.creation

        def work():
            try:
                creations.save(self.library, self.catalog, creation,
                               indexer=getattr(self.window, "indexer", None))
                error = None
            except Exception as exc:                   # a full disk, a bad path
                error = exc
            GLib.idle_add(done, error)

        def done(error):
            self._busy = False
            self.save_btn.set_sensitive(True)
            self.save_btn.set_label(_("Save to Library"))
            if error is not None:
                self.window.show_toast(_("Couldn't save this creation: {why}")
                                       .format(why=error))
                return False
            self._dirty = False
            self.window.show_toast(_("Saved to your library"))
            self.emit("saved", creation.photo_id or 0)
            return False
        threading.Thread(target=work, daemon=True).start()

    def _export(self):
        """Write it somewhere of their own choosing, at print size."""
        if self.creation is None:
            return
        dialog = Gtk.FileDialog()
        dialog.set_title(_("Export Creation"))
        name = (self.name_entry.get_text().strip() or _("Collage"))
        dialog.set_initial_name(f"{name}.jpg")

        def chosen(dlg, result):
            try:
                gfile = dlg.save_finish(result)
            except GLib.Error:
                return
            if gfile is None:
                return
            dest = Path(gfile.get_path())
            pages = [create.Page.from_dict(p.to_dict()) for p in self.creation.pages]
            self.window.show_toast(_("Exporting…"))

            def work():
                try:
                    create.save(pages, dest, long_side=(3508 if dest.suffix.lower()
                                                        == ".pdf" else 3000))
                    message = _("Exported to {name}").format(name=dest.name)
                except Exception as exc:
                    message = _("Couldn't export: {why}").format(why=exc)
                GLib.idle_add(lambda: (self.window.show_toast(message), False)[1])
            threading.Thread(target=work, daemon=True).start()
        dialog.save(self.window, None, chosen)

    def _close(self):
        if self._dirty:
            ask = Adw.AlertDialog(
                heading=_("Leave without saving?"),
                body=_("This creation has changes that are not in your library yet."))
            ask.add_response("stay", _("Keep Working"))
            ask.add_response("leave", _("Leave"))
            ask.set_response_appearance("leave", Adw.ResponseAppearance.DESTRUCTIVE)
            ask.set_default_response("stay")

            def answered(_d, response):
                if response == "leave":
                    self._dirty = False
                    self.emit("closed")
            ask.connect("response", answered)
            ask.present(self.window)
            return
        self.emit("closed")
