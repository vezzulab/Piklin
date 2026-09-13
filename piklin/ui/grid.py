"""The photo grid, grouped into dated sections.

Photographs are grouped by the day they were taken, with a heading and a
hairline between bands, because that is how people look for a picture -
"the afternoon at the lake", not "item 4,312".

Virtualisation happens at the section level: the widget is a
``Gtk.ListView`` whose rows are days, so only the few days on screen have
their tiles built.  GTK 4.14's ``GridView`` has no section headers
(``set_header_factory`` is ListView-only), which is why the grid is a
list of days rather than one flat grid of photos.

A day with thousands of photos would defeat that, so long days are split
into several sections; no single row is ever unbounded.
"""
from __future__ import annotations

from pathlib import Path

import threading
from datetime import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gdk, Gio, Gtk  # noqa: E402

from ..thumbs import GRID_SIZE
from .models import PhotoItem
from .tile import PhotoTile

PAGE = 600              # rows fetched per database page
SECTION_PADDING = 20    # must match .pika-section in style.css
SCROLLBAR_ALLOWANCE = 14
MAX_PER_SECTION = 120   # cap on one row's tiles, so a huge day still scrolls


class DaySection(GObject.Object):
    """One dated band of photographs."""
    __gtype_name__ = "PikaDaySection"

    def __init__(self, title, subtitle, items):
        super().__init__()
        self.title = title
        self.subtitle = subtitle
        self.items = items


def _section_key(ts, mode):
    if not ts:
        return ("unknown", "Undated", "")
    d = datetime.fromtimestamp(ts)
    if mode == "year":
        return (d.strftime("%Y"), d.strftime("%Y"), "")
    if mode == "month":
        return (d.strftime("%Y-%m"), d.strftime("%B %Y"), "")
    if mode == "none":
        return ("all", "", "")
    today = datetime.now().date()
    delta = (today - d.date()).days
    if delta == 0:
        title = "Today"
    elif delta == 1:
        title = "Yesterday"
    elif delta < 7:
        title = d.strftime("%A")
    else:
        title = d.strftime("%A, %-d %B %Y")
    return (d.strftime("%Y-%m-%d"), title, d.strftime("%H:%M"))


class PhotoGrid(Gtk.Box):
    """Scrollable, selectable grid of photographs."""

    __gsignals__ = {
        "activated": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "selection-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # right-click on a photo: (item, tile widget, x, y)
        "context-menu": (GObject.SignalFlags.RUN_FIRST, None,
                         (object, object, float, float)),
    }

    def __init__(self, catalog, thumbs, settings):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.catalog = catalog
        self.thumbs = thumbs
        self.settings = settings

        self.sections = Gio.ListStore(item_type=DaySection)
        self._items: list[PhotoItem] = []
        self._by_id: dict[int, PhotoItem] = {}
        self._selected: set[int] = set()
        self._tile_widgets: dict[int, list] = {}
        # Separate from _tile_widgets (which holds the PhotoTile used
        # for painting thumbnails): this holds each tile's outer
        # container, which is what carries the "selected" CSS class.
        self._tile_containers: dict[int, list] = {}
        # Focusable, so a click on a photo takes keyboard focus away from
        # whatever had it (a sidebar row): otherwise Space and Return went
        # to that row instead of opening the photo.
        self.set_focusable(True)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_setup)
        factory.connect("bind", self._on_bind)
        factory.connect("unbind", self._on_unbind)

        self.view = Gtk.ListView(
            model=Gtk.NoSelection(model=self.sections), factory=factory,
            vexpand=True, single_click_activate=False)
        self.view.add_css_class("pika-grid")

        self.scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.scroller.set_child(self.view)
        self.scroller.get_vadjustment().connect("value-changed",
                                                self._maybe_load_more)
        self.append(self.scroller)

        self.status = Adw.StatusPage(
            icon_name="image-x-generic-symbolic",
            title="No photos here yet",
            description="Add a folder and Piklin will build your library.")
        self.status.set_vexpand(True)
        self.status.set_visible(False)
        self.append(self.status)

        self._scope = "library"
        self._album_id = None
        self._smart_id = None
        self._search = None
        self._order = "taken_desc"
        # The toolbar's Filter menu and Aspect Ratio button: both apply to
        # whatever view is open and survive moving between views.
        self._filters: set[str] = set()
        self._fit = "contain" if settings.get("grid_aspect") == "original" else "cover"
        if self._fit == "contain":
            self.add_css_class("pika-original")
        self._offset = 0
        self._exhausted = False
        self._loading = False
        self._generation = 0
        self._tail = None             # section still open for more photos

        self.tile_px = max(90, min(420, int(settings.get("grid_size", 200))))
        self._target_px = self.tile_px
        self._columns = 0
        self._flows: list = []
        self._last_width = 0
        # Recompute the tile size when the pane's width changes.  A tick
        # callback is used rather than a size_allocate override: that
        # vfunc, overridden from Python, stops a Box allocating its
        # children at all.  The check below is two integer comparisons
        # per frame and does nothing until the width really changes.
        self.add_tick_callback(self._on_tick)

    def _on_tick(self, _widget, _clock):
        width = self.scroller.get_width()
        if width and width != self._last_width:
            self._last_width = width
            self._fit_tiles()
        return GLib.SOURCE_CONTINUE

    def _fit_tiles(self) -> None:
        """Size tiles so a whole number of them spans the width exactly.

        Photographs should meet edge to edge with no seam.  A fixed tile
        size leaves whatever does not divide evenly as a gap between
        columns, so the size is nudged to the nearest value that divides
        the available width.
        """
        # The band the tiles actually live in is inset by the section's
        # own horizontal padding and has to leave room for the
        # scrollbar; measuring against the raw scroller width made the
        # last column overflow and get clipped at the window edge.
        raw = max(1, self._last_width or self.scroller.get_width())
        avail = max(1, raw - SECTION_PADDING * 2 - SCROLLBAR_ALLOWANCE)
        target = max(90, min(420, int(self._target_px)))
        cols = max(1, int(round(avail / target)))
        px = max(60, avail // cols)
        if px == self.tile_px and cols == self._columns:
            return
        self.tile_px = px
        self._columns = cols
        for widgets in self._tile_widgets.values():
            for w in widgets:
                w.set_tile_size(px)
        for flow in self._flows:
            flow.set_min_children_per_line(cols)
            flow.set_max_children_per_line(cols)

    # -- tile size -------------------------------------------------------
    def set_tile_size(self, px: int) -> None:
        """Set the requested tile size; the grid fits it to the width."""
        self._target_px = max(90, min(420, int(px)))
        self._last_width = 0            # force a refit on the next tick
        self._fit_tiles()

    # ==================================================================
    # section rows
    # ==================================================================
    def _on_setup(self, _factory, list_item):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.add_css_class("pika-section")
        title = Gtk.Label(xalign=0.0, ellipsize=3)
        title.add_css_class("pika-section-title")
        sub = Gtk.Label(xalign=0.0, ellipsize=3)
        sub.add_css_class("pika-section-sub")
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                           homogeneous=False, row_spacing=0,
                           column_spacing=0, max_children_per_line=64,
                           min_children_per_line=1, valign=Gtk.Align.START,
                           halign=Gtk.Align.START)
        box.append(title)
        box.append(sub)
        box.append(flow)
        list_item.set_child(box)
        list_item._title = title
        list_item._sub = sub
        list_item._flow = flow
        list_item._tiles = []
        self._flows.append(flow)
        if self._columns:
            flow.set_min_children_per_line(self._columns)
            flow.set_max_children_per_line(self._columns)

    def _on_bind(self, _factory, list_item):
        section = list_item.get_item()
        list_item._title.set_text(section.title or "")
        list_item._title.set_visible(bool(section.title))
        list_item._sub.set_text(section.subtitle or "")
        list_item._sub.set_visible(bool(section.subtitle))

        flow = list_item._flow
        while (child := flow.get_first_child()) is not None:
            flow.remove(child)
        list_item._tiles = []

        for item in section.items:
            tile = self._make_tile(item, list_item)
            flow.append(tile)
            # FlowBox wraps each tile in a FlowBoxChild, and that wrapper is
            # the node assistive technology sees as the grid cell - label it,
            # not just the inner box, or every photo is an unnamed cell.
            cell = tile.get_parent()
            if cell is not None:
                cell.update_property([Gtk.AccessibleProperty.LABEL],
                                     [getattr(item, "filename", "") or "Photo"])

    def _on_unbind(self, _factory, list_item):
        flow = list_item._flow
        while (child := flow.get_first_child()) is not None:
            flow.remove(child)
        for item_id, picture, container in list_item._tiles:
            lst = self._tile_widgets.get(item_id)
            if lst and picture in lst:
                lst.remove(picture)
            clst = self._tile_containers.get(item_id)
            if clst and container in clst:
                clst.remove(container)
        list_item._tiles = []

    def _make_tile(self, item, list_item):
        # A plain Box, not a Gtk.Button: GtkButton owns its own internal
        # GtkGestureClick to implement the "clicked" signal, and adding a
        # second GestureClick on top of it does not share the pointer
        # sequence - the two compete to claim it, and in practice the
        # button's own recognizer wins every time, silently swallowing
        # every click before this widget's handler ever sees it.
        # Confirmed live: an external GestureClick on a Gtk.Button never
        # fired on a real X11 click; the identical setup on a plain Box
        # fired every time. That silent failure is why clicking a photo
        # did nothing at all.
        container = Gtk.Box()
        # A tile is otherwise an unnamed box to a screen reader; give it
        # the photo's name so each one can be told apart and announced.
        container.update_property([Gtk.AccessibleProperty.LABEL],
                                  [getattr(item, "filename", "") or "Photo"])
        container.add_css_class("pika-tile-button")
        container.set_can_target(True)
        container.set_cursor_from_name("pointer")
        # FlowBox hands each child the full column width.  Without this
        # the tiles stretch into wide letterboxes instead of staying the
        # square the size request asks for.
        container.set_halign(Gtk.Align.START)
        container.set_valign(Gtk.Align.START)
        container.set_hexpand(False)
        container.set_vexpand(False)

        overlay = Gtk.Overlay()
        picture = PhotoTile(self.tile_px)
        picture.set_fit(self._fit)
        picture.add_css_class("pika-tile")
        overlay.set_child(picture)

        badges = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2,
                         halign=Gtk.Align.END, valign=Gtk.Align.END)
        if getattr(item, "is_video", False):
            # Length in the corner of every video tile.
            from ..video import format_duration
            vb = Gtk.Box(spacing=3)
            vb.add_css_class("pika-badge")
            vb.add_css_class("pika-video-badge")
            vb.append(Gtk.Image(icon_name="media-playback-start-symbolic",
                                pixel_size=9))
            vb.append(Gtk.Label(label=format_duration(item.duration)))
            badges.append(vb)
        if getattr(item, "has_live", False):
            live = Gtk.Label(label="LIVE")
            live.add_css_class("pika-badge")
            live.add_css_class("pika-live-badge")
            badges.append(live)
        if item.edited:
            b = Gtk.Image(icon_name="document-edit-symbolic", pixel_size=12)
            b.add_css_class("pika-badge")
            badges.append(b)
        if item.favorite:
            b = Gtk.Image(icon_name="starred-symbolic", pixel_size=12)
            b.add_css_class("pika-badge")
            badges.append(b)
        overlay.add_overlay(badges)
        # Selection mark: a black disc with a white check in the corner, on
        # top of the heavier selected border - so it is plain at a glance
        # which photos are selected, without any colour.
        check = Gtk.Image(icon_name="object-select-symbolic",
                          halign=Gtk.Align.END, valign=Gtk.Align.START)
        check.add_css_class("pika-select-check")
        check.set_can_target(False)
        overlay.add_overlay(check)
        container.append(overlay)

        if item.id in self._selected:
            container.add_css_class("selected")
        if item.missing:
            picture.add_css_class("pika-tile-missing")

        container._item = item
        container._picture = picture
        self._tile_widgets.setdefault(item.id, []).append(picture)
        self._tile_containers.setdefault(item.id, []).append(container)
        list_item._tiles.append((item.id, picture, container))

        click = Gtk.GestureClick()
        click.connect("released", self._on_tile_click, container, item)
        container.add_controller(click)
        rclick = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        rclick.connect("pressed", self._on_tile_right_click, container, item)
        container.add_controller(rclick)
        if getattr(item, "is_video", False) and item.id > 0:
            # Rest the pointer on a video and it plays, silently, in place.
            hover = Gtk.EventControllerMotion()
            hover.connect("enter", self._on_video_enter, item, picture)
            hover.connect("leave", self._on_video_leave, item, picture)
            container.add_controller(hover)

        # Dragging photos onto an album in the sidebar is how a photo
        # gets filed - the gesture people reach for first. The payload is a plain string rather
        # than a boxed object so it survives crossing the drag boundary
        # without needing a registered GType.
        drag = Gtk.DragSource(actions=Gdk.DragAction.COPY)
        drag.connect("prepare", self._on_drag_prepare, item)
        drag.connect("drag-begin", self._on_drag_begin, item)
        container.add_controller(drag)

        if item.texture is not None:
            picture.set_paintable(item.texture)
        elif not item.missing:
            self._request_thumb(item, picture)
        return container

    # -- video preview under the pointer ---------------------------------
    def _on_video_enter(self, _ctrl, _x, _y, item, picture):
        self._stop_preview()
        self._preview_timer = GLib.timeout_add(450, self._start_preview,
                                               item, picture)

    def _on_video_leave(self, _ctrl, _item, _picture):
        self._stop_preview()

    def _start_preview(self, item, picture):
        self._preview_timer = 0
        from .player import _AvEngine, _av
        if _av() is None:
            return GLib.SOURCE_REMOVE
        self._preview_gen = getattr(self, "_preview_gen", 0) + 1
        gen = self._preview_gen

        def on_frame(w, h, data, stride):
            GLib.idle_add(self._paint_preview, gen, picture, w, h, data, stride)
        engine = _AvEngine(on_frame, lambda *_a: None, sound=False,
                           max_frame=(480, 480))
        engine.loop = True
        engine.load(item.path, item.width or 0, item.height or 0, 0.0)
        engine.play()
        self._preview = (engine, item, picture)
        return GLib.SOURCE_REMOVE

    def _paint_preview(self, gen, picture, w, h, data, stride):
        if gen == getattr(self, "_preview_gen", 0) and getattr(self, "_preview", None):
            picture.set_paintable(Gdk.MemoryTexture.new(
                w, h, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(data), stride))
        return GLib.SOURCE_REMOVE

    def _stop_preview(self):
        if getattr(self, "_preview_timer", 0):
            GLib.source_remove(self._preview_timer)
            self._preview_timer = 0
        preview = getattr(self, "_preview", None)
        self._preview = None
        self._preview_gen = getattr(self, "_preview_gen", 0) + 1
        if preview is not None:
            engine, item, picture = preview
            threading.Thread(target=engine.unload, daemon=True).start()
            if item.texture is not None:
                picture.set_paintable(item.texture)

    def _request_thumb(self, item, picture):
        def done(path):
            def apply():
                if path is None:
                    picture.add_css_class("pika-tile-missing")
                    return False
                try:
                    item.texture = Gdk.Texture.new_from_filename(str(path))
                except Exception:
                    return False
                # The tile may have been recycled; paint every live widget
                # currently showing this photo.
                for w in self._tile_widgets.get(item.id, []):
                    try:
                        w.set_paintable(item.texture)
                    except Exception:
                        pass
                return False
            GLib.idle_add(apply, priority=GLib.PRIORITY_DEFAULT_IDLE)
        self.thumbs.request(item.path, GRID_SIZE, done)

    # -- drag and drop ---------------------------------------------------
    DRAG_PREFIX = "pika-photos:"
    DEVICE_DRAG_PREFIX = "pika-device:"

    def _on_drag_prepare(self, _source, _x, _y, item):
        """Carry the whole selection, or just this photo if it is not in it.

        Dragging one photo out of a selection of twenty should move the
        twenty - that is what the selection is for - but dragging an
        unselected photo should not silently drag everything else.
        """
        ids = (self.selected_ids() if item.id in self._selected
               else [item.id])
        if self._scope == "device":
            # Photos on a camera are not in the catalog and have no ids;
            # carry their positions in the camera listing instead, so an
            # album they are dropped on can import them.
            indexes = [-i - 1 for i in ids if i < 0]
            payload = self.DEVICE_DRAG_PREFIX + ",".join(str(i) for i in indexes)
        else:
            payload = self.DRAG_PREFIX + ",".join(str(i) for i in ids)
        return Gdk.ContentProvider.new_for_value(payload)

    def _on_drag_begin(self, source, drag, item):
        """A small photo under the pointer, with a count when several are
        dragged.

        Handing GTK the thumbnail texture itself made the drag image the
        full 400 px thumbnail: a raw, square-cornered picture covering half
        the sidebar, including the album you were trying to drop onto.
        """
        ids = self.selected_ids() if item.id in self._selected else [item.id]
        overlay = Gtk.Overlay()
        overlay.add_css_class("pika-drag-icon")
        tile = PhotoTile(84, radius=8.0)
        tile.set_size_request(84, 84)
        if item.texture is not None:
            tile.set_paintable(item.texture)
        overlay.set_child(tile)
        if len(ids) > 1:
            badge = Gtk.Label(label=f"{len(ids):,}", halign=Gtk.Align.END,
                              valign=Gtk.Align.START)
            badge.add_css_class("pika-drag-count")
            overlay.add_overlay(badge)
        Gtk.DragIcon.get_for_drag(drag).set_child(overlay)
        try:
            drag.set_hotspot(-12, -12)
        except Exception:
            pass

    # -- clicks ----------------------------------------------------------
    def _on_tile_click(self, gesture, n_press, x, y, button, item):
        self.grab_focus()
        state = gesture.get_current_event_state()
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)

        if n_press >= 2 and not (ctrl or shift):
            self.emit("activated", item)
            return
        if ctrl:
            self._toggle(item)
        elif shift and self._selected:
            self._select_range(item)
        else:
            if self._selected == {item.id}:
                self._set_selection(set())
            else:
                self._set_selection({item.id})

    def _on_tile_right_click(self, gesture, _n, x, y, container, item):
        """Right-click acts on the photo under the pointer - and on the
        whole selection when that photo is part of it, as in Files."""
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.grab_focus()
        if item.id not in self._selected:
            self._set_selection({item.id})
        self.emit("context-menu", item, container, x, y)

    def _toggle(self, item):
        sel = set(self._selected)
        sel.symmetric_difference_update({item.id})
        self._set_selection(sel)

    def _select_range(self, item):
        try:
            end = self._items.index(item)
        except ValueError:
            return
        anchors = [i for i, it in enumerate(self._items)
                   if it.id in self._selected]
        if not anchors:
            self._set_selection({item.id})
            return
        start = min(anchors, key=lambda i: abs(i - end))
        lo, hi = sorted((start, end))
        self._set_selection({it.id for it in self._items[lo:hi + 1]})

    def _set_selection(self, ids: set):
        changed = ids.symmetric_difference(self._selected)
        self._selected = ids
        for item_id in changed:
            for container in self._tile_containers.get(item_id, []):
                if item_id in ids:
                    container.add_css_class("selected")
                else:
                    container.remove_css_class("selected")
        self.emit("selection-changed")

    # ==================================================================
    # querying
    # ==================================================================
    def load_records(self, records, subtitle="", already=None, root=None) -> None:
        """Show photos that are not in the catalog - a camera, a card or a
        USB drive.

        Device photos deliberately never enter the database: they are
        somewhere you are looking, not part of the library, until they
        are imported. They are grouped by the folder they are in, and a big
        folder is split into several rows, so only what is on screen is
        built - a drive with thousands of photos opens at once.
        """
        self._generation += 1
        self._scope = "device"
        self._album_id = None
        self._smart_id = None
        self._exhausted = True
        self._loading = False
        self._tail = None
        self._items = []
        self._by_id = {}
        self._selected = set()
        self._tile_widgets = {}
        self._tile_containers = {}
        self.sections.remove_all()
        self._device_root = Path(root) if root else None
        self._device_name = subtitle
        # The import screen: what is new first, then what is already in the
        # library - still visible, so nothing seems to have vanished from
        # the card, but kept apart and out of "Import All New Items".
        self._device_already = set(already or ())
        self._device_old = []
        self.append_records(records, 0)
        old = self._device_old
        for i in range(0, len(old), MAX_PER_SECTION):
            chunk = old[i:i + MAX_PER_SECTION]
            self.sections.append(DaySection(
                "Already Imported" if i == 0 else "",
                (f"{len(old)} item" + ("s" if len(old) != 1 else "")
                 + " already in your library") if i == 0 else "", chunk))
        self._items.extend(old)

    def append_records(self, records, start) -> None:
        """More photos found on a device while it is still being read."""
        from .models import DeviceItem
        prev_tail = self._tail
        grew_tail = False
        for i, rec in enumerate(records, start):
            item = DeviceItem(i, rec)
            self._by_id[item.id] = item
            fp = rec.get("fingerprint")
            if fp and fp in self._device_already:
                self._device_old.append(item)
                continue
            self._items.append(item)
            folder = Path(rec.get("path", "")).parent
            try:
                rel = folder.relative_to(self._device_root) if self._device_root else folder
                key = str(rel)
            except ValueError:
                key = str(folder)
            title = (self._device_name or "New Items") if key in ("", ".") else key
            tail = self._tail
            if (tail is not None and tail[0] == key
                    and len(tail[1].items) < MAX_PER_SECTION):
                tail[1].items.append(item)
                if tail is prev_tail:
                    grew_tail = True
                continue
            # a folder's first row carries its name; the rows after it don't
            section = DaySection(title if tail is None or tail[0] != key else "", "",
                                 [item])
            self._tail = (key, section)
            self.sections.append(section)
        if grew_tail and prev_tail is not None:
            # the row already on screen gained photos: have it redrawn
            found, pos = self.sections.find(prev_tail[1])
            if found:
                self.sections.items_changed(pos, 1, 1)
        has_items = bool(self._items or self._device_old)
        self.status.set_visible(not has_items)
        self.scroller.set_visible(has_items)

    # What an empty view says depends on where you are standing: an empty
    # album is not an empty library, and "add a folder" was the wrong
    # advice everywhere except the library itself.
    _EMPTY = {
        "library": ("No Photos", "Add a folder of photos, or connect a camera, "
                                 "and they will appear here."),
        "favorites": ("No Favourites", "Select photos and press . (period) to "
                                       "mark them as favourites."),
        "edited": ("No Edited Photos", "Photos you edit appear here. Your "
                                       "originals are never changed."),
        "hidden": ("No Hidden Photos", "Select photos and press Ctrl+L to hide "
                                       "them from the library."),
        "trash": ("No Recently Deleted Items", "Deleted photos stay here for "
                                               "30 days before they leave the "
                                               "library."),
        "album": ("This Album Is Empty", "Drag photos onto the album in the "
                                         "sidebar to add them."),
        "screenshots": ("No Screenshots", "Screenshots in your folders appear "
                                          "here."),
        "videos": ("No Videos", "Videos in your folders and from your camera "
                                "appear here."),
        "duplicates": ("No Duplicates", "Photos that are exact copies of each "
                                        "other are gathered here."),
        "imports": ("No Imports", "Photos you import from a camera appear here, "
                                  "newest import first."),
    }

    def scroll_to_time(self, ts: float) -> None:
        """Scroll to the newest photo taken at or before ``ts``.

        Opening a month from the Months cards lands here. The photos load a
        page at a time, so keep loading until that point is in the list,
        then scroll to its section.
        """
        self._reveal_ts = float(ts)
        self._continue_reveal()

    def _continue_reveal(self) -> None:
        ts = getattr(self, "_reveal_ts", None)
        if ts is None:
            return
        for i in range(self.sections.get_n_items()):
            sec = self.sections.get_item(i)
            if sec.items and (sec.items[0].taken_at or 0) <= ts:
                self._reveal_ts = None
                target = i

                def go():
                    try:
                        self.view.scroll_to(target, Gtk.ListScrollFlags.NONE, None)
                    except Exception:
                        pass
                    return GLib.SOURCE_REMOVE
                GLib.idle_add(go)
                return
        if self._exhausted:
            self._reveal_ts = None
        elif not self._loading:
            self._load_page()

    def set_filters(self, filters) -> None:
        self._filters = set(filters)
        self.refresh()

    def filters(self) -> set:
        return set(self._filters)

    def set_original_aspect(self, original: bool) -> None:
        """Square thumbnails, or each photo at the shape it was taken."""
        self._fit = "contain" if original else "cover"
        if original:
            self.add_css_class("pika-original")
        else:
            self.remove_css_class("pika-original")
        for widgets in self._tile_widgets.values():
            for w in widgets:
                w.set_fit(self._fit)

    def _describe_empty(self):
        if self._filters and not self._search:
            self.status.set_title("No Matching Photos")
            self.status.set_description("Nothing here matches the filter. "
                                        "Choose Show All in the Filter menu.")
            self.status.set_icon_name("edit-find-symbolic")
            return
        if self._search:
            title, desc = ("No Results", f"Nothing matches \u201c{self._search}\u201d.")
            icon = "system-search-symbolic"
        else:
            title, desc = self._EMPTY.get(self._scope, self._EMPTY["library"])
            icon = {"trash": "user-trash-symbolic",
                    "favorites": "starred-symbolic",
                    "hidden": "view-conceal-symbolic",
                    "videos": "video-x-generic-symbolic"}.get(self._scope,
                                                           "image-x-generic-symbolic")
        self.status.set_title(title)
        self.status.set_description(desc)
        self.status.set_icon_name(icon)

    def load(self, scope="library", album_id=None, search=None,
             order=None, smart_id=None, keep_selection=None) -> None:
        self._scope = scope
        self._album_id = album_id
        self._smart_id = smart_id
        self._search = search or None
        self._order = order or self._order
        self._offset = 0
        self._exhausted = False
        self._loading = False        # abandon any fetch still in flight
        self._generation += 1
        self._tail = None
        self._items = []
        self._by_id = {}
        # A refresh after marking a favourite or hiding keeps what was
        # selected - a selection is never dropped for that.
        self._selected = set(keep_selection or ())
        self._tile_widgets = {}
        self._tile_containers = {}
        self.sections.remove_all()
        self._load_page()

    def refresh(self) -> None:
        if self._scope == "device":
            return          # a device's contents are not ours to refresh
        self.load(self._scope, self._album_id, self._search, self._order,
                  self._smart_id, keep_selection=self._selected)

    def _load_page(self) -> None:
        if self._loading or self._exhausted:
            return
        self._loading = True
        gen = self._generation
        offset = self._offset

        def work():
            try:
                rows = self.catalog.browse(
                    scope=self._scope, album_id=self._album_id,
                    smart_id=self._smart_id,
                    search=self._search, order=self._order,
                    filters=self._filters,
                    limit=PAGE, offset=offset)
            except Exception:
                rows = []

            def apply():
                # Clear the in-flight flag before anything can return
                # early.  A superseded page that left this set would jam
                # the grid permanently: every later load() would see
                # _loading and decline to fetch, so the view stayed empty.
                self._loading = False
                if gen != self._generation:
                    return False
                if rows:
                    self._append_rows(rows)
                    self._offset += len(rows)
                if not rows or len(rows) < PAGE:
                    self._exhausted = True
                self._continue_reveal()
                empty = not self._items
                if empty:
                    self._describe_empty()
                self.status.set_visible(empty)
                self.scroller.set_visible(not empty)
                return False
            GLib.idle_add(apply)
        threading.Thread(target=work, daemon=True).start()

    def _append_rows(self, rows) -> None:
        mode = self.settings.get("group_by", "day")
        sort_by_date = self._order.startswith("taken")
        new_sections = []
        for row in rows:
            item = PhotoItem(row)
            self._items.append(item)
            self._by_id[item.id] = item

            if not sort_by_date or mode == "none":
                key, title, sub = ("all", "", "")
            else:
                key, title, sub = _section_key(item.taken_at, mode)

            tail = self._tail
            if (tail is not None and tail[0] == key
                    and len(tail[1].items) < MAX_PER_SECTION):
                tail[1].items.append(item)
                continue
            section = DaySection(title, "", [item])
            self._tail = (key, section)
            new_sections.append(section)

        for section in new_sections:
            self.sections.append(section)
        # Counts are only final once a section stops growing, so refresh
        # the subtitle of everything now that this page is grouped.
        for i in range(self.sections.get_n_items()):
            sec = self.sections.get_item(i)
            n = len(sec.items)
            sec.subtitle = f"{n} photo" + ("s" if n != 1 else "")
        # Re-emit so rows already on screen pick up the counts, which are
        # only final once the whole page has been grouped.
        n_items = self.sections.get_n_items()
        if n_items:
            self.sections.items_changed(0, n_items, n_items)

    def _maybe_load_more(self, adj) -> None:
        if self._exhausted or self._loading:
            return
        if adj.get_value() + adj.get_page_size() * 2.5 >= adj.get_upper():
            self._load_page()

    # ==================================================================
    # selection API
    # ==================================================================
    def selected_items(self) -> list[PhotoItem]:
        return [self._by_id[i] for i in self._selected if i in self._by_id]

    def selected_ids(self) -> list[int]:
        # Only what is actually in view: a kept selection can include a
        # photo that has since left this view (hidden, deleted).
        return [i for i in self._selected if i in self._by_id]

    def select_all(self) -> None:
        self._set_selection({i.id for i in self._items})

    def unselect_all(self) -> None:
        self._set_selection(set())

    def item_at(self, index: int):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def index_of(self, item) -> int:
        try:
            return self._items.index(item)
        except ValueError:
            return -1

    def count(self) -> int:
        return len(self._items)
