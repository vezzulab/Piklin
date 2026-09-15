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

from collections import OrderedDict
from pathlib import Path

import threading
from datetime import datetime

import gi
from ..i18n import _, N_, ngettext, month_year, weekday_name, long_date

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, GLib, GObject, Gdk, Gio, Graphene, Gtk  # noqa: E402

from ..thumbs import GRID_SIZE
from .models import PhotoItem
from .chrome import PRIMARY_MASK
from .tile import PhotoTile

PAGE = 600              # rows fetched per database page
# The first page is small, so a view appears at once; the rest follows in
# the background, page by page, while nobody is looking at it yet.
FIRST_PAGE = 120
SECTION_PADDING = 20    # must match .pika-section in style.css
# A hairline of white between photos, so each one reads as its own picture
# instead of one long mosaic. Also between the rows a long day is split into
# (see .pika-section-continued in style.css).
TILE_GAP = 2
SCROLLBAR_ALLOWANCE = 14
# Tiles in one list row. The list only builds the rows on screen, so a
# small row means opening an album builds and decodes about what is visible
# instead of everything in the first page. A day with more photos simply
# continues in the next row, without a gap.
MAX_PER_SECTION = 36

# Thumbnails already decoded, kept across views so going back to an album
# or to All Photos shows them at once: photo path -> (thumbnail file,
# texture, bytes). Bounded by memory, oldest first out.
_TEXTURE_BUDGET = 256 * 1024 * 1024
_textures: OrderedDict = OrderedDict()
_texture_bytes = 0
_texture_lock = threading.Lock()


def count_label(photos: int, videos: int) -> str:
    """"12 photos", "12 videos", or "3 photos and 2 videos" - never calling
    videos photos."""
    p = ngettext("{count} photo", "{count} photos", photos).format(count=f"{photos:,}")
    v = ngettext("{count} video", "{count} videos", videos).format(count=f"{videos:,}")
    if photos and videos:
        return _("{photos} and {videos}").format(photos=p, videos=v)
    return v if videos else p


def _cached_texture(photo_path):
    with _texture_lock:
        hit = _textures.get(photo_path)
        if hit is not None:
            _textures.move_to_end(photo_path)
        return hit


def _remember_texture(photo_path, thumb_path, texture) -> None:
    global _texture_bytes
    size = texture.get_width() * texture.get_height() * 4
    with _texture_lock:
        old = _textures.pop(photo_path, None)
        if old is not None:
            _texture_bytes -= old[2]
        _textures[photo_path] = (thumb_path, texture, size)
        _texture_bytes += size
        while _texture_bytes > _TEXTURE_BUDGET and len(_textures) > 1:
            _key, (_thumb, _tex, gone) = _textures.popitem(last=False)
            _texture_bytes -= gone


class DaySection(GObject.Object):
    """One dated band of photographs."""
    __gtype_name__ = "PikaDaySection"

    def __init__(self, title, subtitle, items, heading=False):
        super().__init__()
        self.title = title
        self.subtitle = subtitle
        self.items = items
        # the name of the album at the top of the view, above its days
        self.heading = heading
        # (folder id, folder name) the back arrow beside the heading opens
        self.back_to = None
        # the subtitle label while this section is on screen, so a growing
        # photo count updates the text without rebuilding the tiles
        self.sub_label = None


def _section_key(ts, mode):
    if not ts:
        return ("unknown", _("No Date"), "")
    d = datetime.fromtimestamp(ts)
    if mode == "year":
        return (d.strftime("%Y"), d.strftime("%Y"), "")
    if mode == "month":
        return (d.strftime("%Y-%m"), month_year(d), "")
    if mode == "none":
        return ("all", "", "")
    today = datetime.now().date()
    delta = (today - d.date()).days
    if delta == 0:
        title = _("Today")
    elif delta == 1:
        title = _("Yesterday")
    elif delta < 7:
        title = weekday_name(d.weekday())
    else:
        title = long_date(d)
    return (d.strftime("%Y-%m-%d"), title, d.strftime("%H:%M"))


class PhotoGrid(Gtk.Box):
    """Scrollable, selectable grid of photographs."""

    __gsignals__ = {
        "activated": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        # the back arrow beside an album's name: the folder it is in
        "open-folder": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        # a right-click on empty space: (widget, x, y) for a New Album menu
        "background-menu": (GObject.SignalFlags.RUN_FIRST, None, (object, float, float)),
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
        # (photo id, time) of the last plain press, to recognise a double
        # click that GTK counted as two single ones (see _on_tile_press),
        # and the photo a double click just opened, whose release then
        # changes nothing.
        self._last_press = None
        self._opened_on_press = None
        self._chose_on_press = None
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
        # rows bound but not yet given their tiles (see _on_bind)
        self._pending_rows = set()
        self._build_scheduled = False
        self.scroller.get_vadjustment().connect("value-changed", self._schedule_build)
        # Notes in the activity log when the photos jump soon after a click,
        # with what was going on, to find the cause of a view that moves.
        self._click_mark = None
        self.scroller.get_vadjustment().connect("value-changed", self._watch_jump)
        # Dragging from empty space draws a rectangle that chooses the photos
        # it touches; the rectangle is painted on a layer above the photos.
        overlay = Gtk.Overlay(vexpand=True)
        overlay.set_child(self.scroller)
        self._band = None
        self._band_tick = 0
        self._band_area = Gtk.DrawingArea(can_target=False)
        self._band_area.set_draw_func(self._draw_band)
        overlay.add_overlay(self._band_area)
        # A touchpad flick keeps the photos gliding after the fingers lift,
        # and a click did not stop it: the photo clicked slid away from
        # under the pointer. Any press now stops the glide where it is.
        halt = Gtk.GestureClick(button=0)
        halt.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        halt.connect("pressed", self._halt_glide)
        self.scroller.add_controller(halt)
        band = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        band.connect("drag-begin", self._band_begin)
        band.connect("drag-update", self._band_update)
        band.connect("drag-end", self._band_end)
        self.scroller.add_controller(band)
        menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        menu.connect("pressed", self._on_background_press, self.scroller)
        self.scroller.add_controller(menu)
        self._build_album_bar()
        self.append(overlay)
        self._overlay = overlay

        self.status = Adw.StatusPage(
            icon_name="image-x-generic-symbolic",
            title=_("No photos here yet"),
            description=_("Add a folder and Piklin will build your library."))
        self.status.set_vexpand(True)
        self.status.set_visible(False)
        # an empty album or view offers New Album too, on a right-click
        empty_menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        empty_menu.connect("pressed", self._on_background_press, self.status)
        self.status.add_controller(empty_menu)
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

    def _build_album_bar(self) -> None:
        """An album's name, fixed above its photos so its back arrow is always
        at hand however far down you scroll. An album with photos and videos
        both has a switch at its right to see only one kind."""
        self._kind = None               # None, "photos" or "videos"
        self._kind_view = None          # the view the kind was chosen in
        self._syncing_kind = False
        self._bar_back_to = None
        bar = Gtk.CenterBox()
        bar.add_css_class("pika-album-bar")
        bar.set_visible(False)
        start = Gtk.Box(spacing=4)
        back = Gtk.Button(icon_name="go-previous-symbolic", visible=False,
                          valign=Gtk.Align.CENTER)
        back.add_css_class("flat")
        back.add_css_class("pika-heading-back")
        back.connect("clicked", self._on_bar_back)
        start.append(back)
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        title = Gtk.Label(xalign=0.0, ellipsize=3)
        title.add_css_class("pika-album-title")
        sub = Gtk.Label(xalign=0.0, ellipsize=3)
        sub.add_css_class("pika-album-sub")
        names.append(title)
        names.append(sub)
        start.append(names)
        bar.set_start_widget(start)
        kinds = Gtk.Box(valign=Gtk.Align.CENTER)
        kinds.add_css_class("pika-kind-switch")
        self._kind_buttons = {}
        first = None
        for kind, label, icon in ((None, _("All"), None),
                                  ("photos", _("Photos"), "image-x-generic-symbolic"),
                                  ("videos", _("Videos"), "video-x-generic-symbolic")):
            button = Gtk.ToggleButton(active=kind is None)
            if icon:
                button.set_child(Adw.ButtonContent(icon_name=icon, label=label))
            else:
                button.set_label(label)
            if first is None:
                first = button
            else:
                button.set_group(first)
            button.connect("toggled", self._on_kind_toggled, kind)
            kinds.append(button)
            self._kind_buttons[kind] = button
        # at the right, not in the middle: the middle of the toolbar above
        # already holds Years, Months and Days, and two switches one above
        # the other read as one
        bar.set_end_widget(kinds)
        self.append(bar)
        self._album_bar, self._bar_back = bar, back
        self._bar_title, self._bar_sub, self._kind_switch = title, sub, kinds
        # a hairline under the name only once photos pass beneath it
        self.scroller.get_vadjustment().connect("value-changed", self._on_bar_scroll)

    def _on_bar_scroll(self, adj) -> None:
        if adj.get_value() > 1:
            self._album_bar.add_css_class("pika-scrolled")
        else:
            self._album_bar.remove_css_class("pika-scrolled")

    def _on_bar_back(self, _button) -> None:
        if self._bar_back_to is not None:
            self.emit("open-folder", self._bar_back_to[0])

    def _on_kind_toggled(self, button, kind) -> None:
        if self._syncing_kind or not button.get_active() or kind == self._kind:
            return
        self._kind = kind
        self.refresh()
        adj = self.scroller.get_vadjustment()
        adj.set_value(adj.get_lower())

    def _show_heading(self) -> None:
        """The album bar for an album or Smart Album; for Duplicates, its
        explanation as the first row of the list."""
        heading = self._heading_section()
        if heading is None or self._scope not in ("album", "smart"):
            self._album_bar.set_visible(False)
            self._kind = None
            if heading is not None:
                self.sections.append(heading)
            return
        photos, videos = heading.counts
        self._bar_title.set_text(heading.title or "")
        self._bar_sub.set_text(heading.subtitle or "")
        self._bar_back_to = heading.back_to
        self._bar_back.set_visible(heading.back_to is not None)
        if heading.back_to is not None:
            self._bar_back.set_tooltip_text(
                _("Back to {folder}").format(folder=heading.back_to[1]))
        both = bool(photos and videos)
        if not both:
            self._kind = None             # only one kind: nothing to switch
        self._kind_switch.set_visible(both)
        self._syncing_kind = True
        self._kind_buttons[self._kind].set_active(True)
        self._syncing_kind = False
        self._album_bar.set_visible(True)

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
        avail = max(1, raw - SECTION_PADDING * 2 - self._scrollbar_width())
        target = max(90, min(420, int(self._target_px)))
        cols = max(1, int(round((avail + TILE_GAP) / (target + TILE_GAP))))
        px = max(60, (avail - TILE_GAP * (cols - 1)) // cols)
        if px == self.tile_px and cols == self._columns:
            return
        old_cols = self._columns
        if cols == old_cols:
            self._keep_place()
        self.tile_px = px
        self._columns = cols
        for widgets in self._tile_widgets.values():
            for w in widgets:
                w.set_tile_size(px)
        for flow in self._flows:
            flow.set_min_children_per_line(cols)
            flow.set_max_children_per_line(cols)
        for list_item in list(self._pending_rows):
            self._reserve_height(list_item)
        self._schedule_build()
        if (cols != old_cols and self._items and self._scope != "device"
                and not getattr(self, "_regroup_pending", False)):
            self._regroup_pending = True
            GLib.idle_add(self._regroup)

    def _scrollbar_width(self) -> int:
        """Room kept for the scrollbar at the right. A scrollbar that floats
        over the photos (a Mac, and GTK's default) takes none: keeping room
        for it anyway left a wider margin at the right than at the left."""
        settings = Gtk.Settings.get_default()
        floating = self.scroller.get_overlay_scrolling() and (
            settings is None or settings.props.gtk_overlay_scrolling)
        return 0 if floating else SCROLLBAR_ALLOWANCE

    def _keep_place(self) -> None:
        """Tiles are about to change size: keep the photo at the top of the
        view where it is. Every row above it grows or shrinks a little, and
        far down a big library that added up to a jump of whole screens -
        when choosing a photo narrowed the sidebar, for one."""
        if self._band is not None:
            return
        anchor = None
        for pid, containers in self._tile_containers.items():
            for c in containers:
                if not c.get_mapped():
                    continue
                ok, r = c.compute_bounds(self.scroller)
                if ok and r.get_y() + r.get_height() > 0 and (
                        anchor is None or r.get_y() < anchor[1]):
                    anchor = (c, r.get_y())
        if anchor is None:
            return
        container, before = anchor
        ticks = [0]

        def settle(_widget, _clock):
            ticks[0] += 1
            ok, r = container.compute_bounds(self.scroller)
            if not ok or not container.get_mapped():
                return GLib.SOURCE_REMOVE
            shift = r.get_y() - before
            if abs(shift) >= 1:
                adj = self.scroller.get_vadjustment()
                adj.set_value(max(adj.get_lower(), min(
                    adj.get_upper() - adj.get_page_size(), adj.get_value() + shift)))
            # the new size takes a frame or two to be laid out
            return GLib.SOURCE_CONTINUE if ticks[0] < 3 else GLib.SOURCE_REMOVE
        self.add_tick_callback(settle)

    def _regroup(self):
        """Re-cut the list rows for a new number of columns, from the photos
        already loaded - no database work - keeping roughly the same place."""
        self._regroup_pending = False
        if not self._items or self._scope == "device":
            return GLib.SOURCE_REMOVE
        adj = self.scroller.get_vadjustment()
        upper = max(1.0, adj.get_upper())
        anchor = self._items[min(len(self._items) - 1,
                                 int(len(self._items) * adj.get_value() / upper))]
        self._tail = None
        self._day_counts, self._day_heads, self._day_videos = {}, {}, {}
        self.sections.remove_all()
        if self._scope not in ("album", "smart"):
            heading = self._heading_section()
            if heading is not None:
                self.sections.append(heading)
        self._group(list(self._items))
        for i in range(self.sections.get_n_items()):
            if anchor in self.sections.get_item(i).items:
                if i:
                    try:
                        self.view.scroll_to(i, Gtk.ListScrollFlags.NONE, None)
                    except Exception:
                        pass
                break
        return GLib.SOURCE_REMOVE

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
                           homogeneous=False, row_spacing=TILE_GAP,
                           column_spacing=TILE_GAP, max_children_per_line=64,
                           min_children_per_line=1, valign=Gtk.Align.START,
                           halign=Gtk.Align.START, focusable=False,
                           activate_on_single_click=False)
        # A heading's back arrow sits beside its title; hidden on day rows.
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        back = Gtk.Button(icon_name="go-previous-symbolic", visible=False,
                          valign=Gtk.Align.CENTER)
        back.add_css_class("flat")
        back.add_css_class("pika-heading-back")
        back.connect("clicked", self._on_heading_back, list_item)
        head.append(back)
        head.append(title)
        box.append(head)
        box.append(sub)
        box.append(flow)
        list_item.set_child(box)
        # A click on a photo made the list focus the whole row, and the list
        # scrolls a focused row into view. A row of six grid rows is taller
        # than the window, so it was pulled to the top or centred: the activity
        # log showed the photos jumping 1074 px (one row) or half a window
        # right after a click. Rows never take focus now; the grid does.
        list_item.set_focusable(False)
        list_item.set_activatable(False)
        list_item.set_selectable(False)
        list_item._back = back
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
        # A row that continues the day or folder above (a big day is split
        # into rows of MAX_PER_SECTION so scrolling stays fast) joins it with
        # no divider or gap: the split is for speed and should not show.
        box = list_item.get_child()
        if section.title:
            box.remove_css_class("pika-section-continued")
        else:
            box.add_css_class("pika-section-continued")
        if getattr(section, "heading", False):
            box.add_css_class("pika-view-heading")
        else:
            box.remove_css_class("pika-view-heading")
        back_to = getattr(section, "back_to", None)
        list_item._back.set_visible(back_to is not None)
        if back_to is not None:
            list_item._back.set_tooltip_text(_("Back to {folder}").format(folder=back_to[1]))
        # a heading's line under the title may be a sentence: let it wrap
        heading = getattr(section, "heading", False)
        list_item._sub.set_wrap(heading)
        list_item._sub.set_ellipsize(0 if heading else 3)
        list_item._sub.set_max_width_chars(90 if heading else -1)
        list_item._title.set_text(section.title or "")
        list_item._title.set_visible(bool(section.title))
        list_item._sub.set_text(section.subtitle or "")
        list_item._sub.set_visible(bool(section.subtitle))
        section.sub_label = list_item._sub

        flow = list_item._flow
        while (child := flow.get_first_child()) is not None:
            flow.remove(child)
        list_item._tiles = []

        # The list binds far more rows than are on screen (it cannot know
        # their height before they are built), and building every tile of
        # them is what made a big album take a second to open. A row takes
        # its full height at once, empty, and gets its tiles only when it
        # comes near the screen.
        self._reserve_height(list_item)
        self._pending_rows.add(list_item)
        self._schedule_build()

    def _reserve_height(self, list_item) -> None:
        section = list_item.get_item()
        if section is None:
            return
        cols = max(1, self._columns or 1)
        rows = -(-len(section.items) // cols)
        list_item._flow.set_size_request(-1, rows * self.tile_px + max(0, rows - 1) * TILE_GAP)

    def _build_row(self, list_item) -> None:
        self._pending_rows.discard(list_item)
        section = list_item.get_item()
        if section is None or list_item._tiles:
            return
        flow = list_item._flow
        for item in section.items:
            tile = self._make_tile(item, list_item)
            flow.append(tile)
            # FlowBox wraps each tile in a FlowBoxChild, and that wrapper is
            # the node assistive technology sees as the grid cell - label it,
            # not just the inner box, or every photo is an unnamed cell.
            cell = tile.get_parent()
            if cell is not None:
                # Clicking a photo gave its FlowBox cell the focus, and the list
                # scrolled that cell's whole row - taller than the window - into
                # view: the photos jumped a row away. The activity log showed
                # "focus on FlowBoxChild". Cells never take focus now.
                cell.set_focusable(False)
                cell.update_property([Gtk.AccessibleProperty.LABEL],
                                     [getattr(item, "filename", "") or _("Photo")])
        flow.set_size_request(-1, -1)

    def _schedule_build(self, *_args) -> None:
        if not self._build_scheduled:
            self._build_scheduled = True
            GLib.idle_add(self._build_visible)

    def _build_visible(self):
        """Give tiles to the rows on screen, and to a screen's worth above
        and below, so scrolling finds them ready."""
        self._build_scheduled = False
        if not self._pending_rows:
            return GLib.SOURCE_REMOVE
        view_h = self.scroller.get_height() or 800
        origin = Graphene.Point().init(0, 0)
        near = []
        for list_item in self._pending_rows:
            box = list_item.get_child()
            if box is None or not box.get_mapped():
                continue
            ok, pt = box.compute_point(self.scroller, origin)
            if ok and pt.y + box.get_height() >= -view_h and pt.y <= 2 * view_h:
                near.append((pt.y, list_item))
        # top to bottom, so what is on screen is built first
        for _y, list_item in sorted(near, key=lambda n: n[0]):
            self._build_row(list_item)
        return GLib.SOURCE_REMOVE

    def _on_unbind(self, _factory, list_item):
        section = list_item.get_item()
        if section is not None and getattr(section, "sub_label", None) is list_item._sub:
            section.sub_label = None
        self._pending_rows.discard(list_item)
        list_item._flow.set_size_request(-1, -1)
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
                                  [getattr(item, "filename", "") or _("Photo")])
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
            b.add_css_class("pika-edited-badge")
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
        click.connect("pressed", self._on_tile_press, item)
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
            # Seen before in this session: shown at once, then checked in
            # the background in case the photo was edited since.
            hit = _cached_texture(item.path)
            if hit is not None:
                item.texture = hit[1]
                picture.set_paintable(hit[1])
            self._request_thumb(item, picture, known=hit[0] if hit else None)
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

    # -- right-click on empty space, and choosing by dragging a rectangle -------
    def _on_tile_at(self, x, y) -> bool:
        """Whether the point (in the scroller) is on a photo."""
        w = self.scroller.pick(x, y, Gtk.PickFlags.DEFAULT)
        while w is not None and w is not self.scroller:
            if w.has_css_class("pika-tile-button"):
                return True
            w = w.get_parent()
        return False

    def _on_background_press(self, gesture, _n, x, y, source):
        if self._scope == "device":
            return
        if source is self.scroller and self._on_tile_at(x, y):
            return                              # a photo has its own menu
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.emit("background-menu", source, x, y)

    def _halt_glide(self, gesture, *_args):
        # Turning kinetic scrolling off cancels a glide in progress.
        self.scroller.set_kinetic_scrolling(False)
        self.scroller.set_kinetic_scrolling(True)
        adj = self.scroller.get_vadjustment()
        adj.set_value(adj.get_value())
        gesture.set_state(Gtk.EventSequenceState.DENIED)

    def _band_begin(self, gesture, x, y):
        self._band = None
        if self._on_tile_at(x, y):
            # on a photo: its own click, double click and drag stay as they are
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        state = gesture.get_current_event_state()
        keep = bool(state & (PRIMARY_MASK | Gdk.ModifierType.SHIFT_MASK))
        top = self.scroller.get_vadjustment().get_value()
        self._band = {"x": x, "sy": y, "y": y + top, "dx": 0.0, "dy": 0.0,
                      "base": set(self._selected) if keep else set(),
                      "hits": set(), "moved": False}
        if not self._band_tick:
            self._band_tick = GLib.timeout_add(40, self._band_autoscroll)

    def _band_update(self, gesture, dx, dy):
        b = self._band
        if b is None:
            return
        b["dx"], b["dy"] = dx, dy
        if not b["moved"] and (abs(dx) > 4 or abs(dy) > 4):
            b["moved"] = True
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        if b["moved"]:
            self._band_apply()

    def _band_apply(self):
        b = self._band
        top = self.scroller.get_vadjustment().get_value()
        x0, x1 = sorted((b["x"], b["x"] + b["dx"]))
        # the start stays with the photos it began on as the view scrolls
        y0, y1 = sorted((b["y"] - top, b["sy"] + b["dy"]))
        b["rect"] = (x0, y0, x1 - x0, y1 - y0)
        on_screen, touched = set(), set()
        for pid, containers in self._tile_containers.items():
            for c in containers:
                if not c.get_mapped():
                    continue
                on_screen.add(pid)
                ok, r = c.compute_bounds(self.scroller)
                if (ok and r.get_x() < x1 and r.get_x() + r.get_width() > x0
                        and r.get_y() < y1 and r.get_y() + r.get_height() > y0):
                    touched.add(pid)
                    break
        # photos scrolled out of sight while dragging stay chosen
        b["hits"] = {p for p in b["hits"] if p not in on_screen} | touched
        self._set_selection(b["base"] | b["hits"])
        self._band_area.queue_draw()

    def _band_autoscroll(self):
        b = self._band
        if b is None:
            self._band_tick = 0
            return GLib.SOURCE_REMOVE
        if b["moved"]:
            h = self.scroller.get_height()
            py = b["sy"] + b["dy"]
            step = -28 if py < 36 else (28 if py > h - 36 else 0)
            if step:
                adj = self.scroller.get_vadjustment()
                adj.set_value(max(adj.get_lower(), min(adj.get_upper() - adj.get_page_size(),
                                                        adj.get_value() + step)))
                self._band_apply()
        return GLib.SOURCE_CONTINUE

    def _band_end(self, _gesture, _dx, _dy):
        b, self._band = self._band, None
        if self._band_tick:
            GLib.source_remove(self._band_tick)
            self._band_tick = 0
        self._band_area.queue_draw()
        if b is not None and not b["moved"] and not b["base"] and self._selected:
            self._set_selection(set())          # a click on empty space lets go

    def _draw_band(self, _area, cr, _w, _h):
        b = self._band
        if not b or not b.get("moved") or "rect" not in b:
            return
        x, y, bw, bh = b["rect"]
        cr.set_source_rgba(0.114, 0.114, 0.122, 0.10)
        cr.rectangle(x, y, bw, bh)
        cr.fill()
        cr.set_source_rgba(0.114, 0.114, 0.122, 0.55)
        cr.set_line_width(1.0)
        cr.rectangle(x + 0.5, y + 0.5, max(0.0, bw - 1), max(0.0, bh - 1))
        cr.stroke()

    def repaint_items(self, ids) -> None:
        """Show these photos' current thumbnails in place, without reloading
        the grid (a reload blanks every tile while they decode again)."""
        for pid in ids:
            item = self._by_id.get(pid)
            if item is None:
                continue
            pictures = self._tile_widgets.get(pid, [])
            if pictures:
                self._request_thumb(item, pictures[0])
            else:
                item.texture = None     # not on screen: fetched when it is

    def _request_thumb(self, item, picture, known=None):
        """Fetch the thumbnail and paint it. ``known``: the thumbnail file
        already on screen from the cache - nothing to do if it is current."""
        def done(path):
            # Runs on a thumbnail worker thread.
            if path is None:
                GLib.idle_add(lambda: (picture.add_css_class("pika-tile-missing"), False)[1])
                return
            if known is not None and str(path) == known:
                return
            try:
                # Decoded here, off the UI thread: decoding hundreds of
                # JPEGs on it is what made opening a big album take seconds.
                texture = Gdk.Texture.new_from_filename(str(path))
            except Exception:
                return
            _remember_texture(item.path, str(path), texture)

            def apply():
                item.texture = texture
                # The tile may have been recycled; paint every live widget
                # currently showing this photo.
                for w in self._tile_widgets.get(item.id, []):
                    try:
                        w.set_paintable(texture)
                    except Exception:
                        pass
                return False
            GLib.idle_add(apply, priority=GLib.PRIORITY_DEFAULT_IDLE)
        self.thumbs.request(getattr(item, "thumb_path", None) or item.path, GRID_SIZE, done)

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
    def _watch_jump(self, adj):
        mark = self._click_mark
        if mark is None or self._band is not None:
            return
        elapsed = GLib.get_monotonic_time() - mark[0]
        if elapsed > 1_500_000:
            self._click_mark = None
            return
        moved = adj.get_value() - mark[1]
        if abs(moved) > adj.get_page_size() / 2:
            self._click_mark = None
            from .. import logs
            root = self.get_root()
            focus = root.get_focus() if root is not None else None
            logs.get("grid").warning(
                "View moved %+d px %.2f s after clicking photo %s (scope %s, "
                "%d photos loaded, %s, tile %d px x %d columns, page %d of %d px, "
                "window %d px wide, focus on %s)",
                moved, elapsed / 1e6, mark[2], self._scope, len(self._items),
                "all loaded" if self._exhausted else "still loading",
                self.tile_px, self._columns, adj.get_page_size(), adj.get_upper(),
                self.get_width(), type(focus).__name__ if focus is not None else "nothing")

    def _on_tile_press(self, gesture, n_press, _x, _y, item):
        """A double click opens the photo, on its second press.

        Opening on the release lost double clicks: the slightest movement
        between press and release starts dragging the photo (a trackpad
        click on a Mac nearly always moves a little), and a drag never
        delivers the release. Two presses on the same photo within the
        double-click time open it, however GTK counted them - and the
        gesture is claimed, so no drag starts from that press.

        A single click chooses the photo on the press too, for the same
        reason: choosing it on the release lost every click that moved a
        little. Only a press on a photo already among several chosen waits
        for the release, so those several can still be dragged together.
        """
        self._chose_on_press = None
        state = gesture.get_current_event_state()
        ctrl = bool(state & PRIMARY_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if ctrl or shift:
            self._last_press = None
            self._chose_on_press = item.id
            self._choose(item, ctrl, shift)
            return
        now = GLib.get_monotonic_time()
        limit = (Gtk.Settings.get_default().get_property("gtk-double-click-time") or 400) * 1000
        last, self._last_press = self._last_press, (item.id, now)
        quick_again = last is not None and last[0] == item.id and now - last[1] <= limit
        if n_press >= 2 or quick_again:
            self._last_press = None
            self._opened_on_press = item.id
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.emit("activated", item)
            return
        if item.id not in self._selected or len(self._selected) == 1:
            self._chose_on_press = item.id
            self._choose(item, False, False)

    def _on_tile_click(self, gesture, n_press, x, y, button, item):
        """The release of a click. The press has usually chosen the photo
        already (see _on_tile_press); a click on one of several chosen photos,
        without dragging them, chooses just that one."""
        if self._opened_on_press == item.id:
            self._opened_on_press = None           # the release of a double click
            return
        if self._chose_on_press == item.id:
            self._chose_on_press = None
            return
        self._choose(item, False, False)

    def _choose(self, item, ctrl, shift):
        """A single click chooses the photo, as in Photos on a Mac: clicking it
        again keeps it chosen - it used to unchoose it, so a double click a
        little slower than the system's marked and unmarked the photo and
        never opened it. ⌘ (Ctrl) adds or removes one; Shift chooses a range."""
        self._click_mark = (GLib.get_monotonic_time(),
                            self.scroller.get_vadjustment().get_value(), item.id)
        self.grab_focus()
        if ctrl:
            self._toggle(item)
        elif shift and self._selected:
            self._select_range(item)
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
        # The check marks show only while several photos are being chosen;
        # one photo clicked is simply framed, as in Photos.
        if len(ids) > 1:
            self.add_css_class("pika-choosing")
        else:
            self.remove_css_class("pika-choosing")
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
        size = self._chunk_size()
        for i in range(0, len(old), size):
            chunk = old[i:i + size]
            self.sections.append(DaySection(
                _("Already Imported") if i == 0 else "",
                ngettext("{count} item already in your library",
                         "{count} items already in your library",
                         len(old)).format(count=len(old)) if i == 0 else "", chunk))
        self._items.extend(old)

    def append_records(self, records, start) -> None:
        """More photos found on a device while it is still being read."""
        from .models import DeviceItem
        prev_tail = self._tail
        grew_tail = False
        size = self._chunk_size()
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
            title = (self._device_name or _("New Items")) if key in ("", ".") else key
            tail = self._tail
            if (tail is not None and tail[0] == key
                    and len(tail[1].items) < size):
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
        self._show_grid(has_items)

    # What an empty view says depends on where you are standing: an empty
    # album is not an empty library, and "add a folder" was the wrong
    # advice everywhere except the library itself.
    _EMPTY = {
        "library": (N_("No Photos"), N_("Add a folder of photos, or connect a camera, "
                                 "and they will appear here.")),
        "favorites": (N_("No Favourites"), N_("Select photos and press the period key (.) to add them to your "
                                         "favourites.")),
        "edited": (N_("No Edited Photos"), N_("Photos you edit appear here. Your "
                                       "originals are never changed.")),
        "hidden": (N_("No Hidden Photos"), N_("Select photos and press Ctrl+L to hide "
                                       "them from the library.")),
        "trash": (N_("No Recently Deleted Items"), N_("Deleted photos stay here for "
                                               "30 days before they leave the "
                                               "library.")),
        "album": (N_("This Album Is Empty"), N_("Drag photos onto the album in the "
                                         "sidebar to add them.")),
        "screenshots": (N_("No Screenshots"), N_("Screenshots in your folders appear "
                                          "here.")),
        "videos": (N_("No Videos"), N_("Videos in your folders and from your camera "
                                "appear here.")),
        "duplicates": (N_("No Duplicates"), N_("Photos that are exact copies of each "
                                        "other are gathered here.")),
        "imports": (N_("No Imports"), N_("Photos you import from a camera appear here, "
                                  "newest import first.")),
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

    def _show_grid(self, visible: bool) -> None:
        """Show the photos, or leave the whole height to the empty page.

        The overlay the photos sit in expands to fill the view; hiding only
        the scroller inside it left that empty overlay taking the top half,
        so an empty album's icon and title sat low instead of centred.
        """
        self.scroller.set_visible(visible)
        self._overlay.set_visible(visible)

    def _describe_empty(self):
        if self._filters and not self._search:
            self.status.set_title(_("No Matching Photos"))
            self.status.set_description(_("Nothing here matches the filter. "
                                        "Choose Show All in the Filter menu."))
            self.status.set_icon_name("edit-find-symbolic")
            return
        if self._search:
            title, desc = (_("No Results"),
                           _("Nothing matches “{search}”.").format(search=self._search))
            icon = "system-search-symbolic"
        else:
            title, desc = self._EMPTY.get(self._scope, self._EMPTY["library"])
            title, desc = _(title), _(desc)
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
        if (scope, album_id, smart_id) != self._kind_view:
            # another album opens showing everything in it
            self._kind_view = (scope, album_id, smart_id)
            self._kind = None
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
        self._day_counts = {}         # section key -> photos in that day so far
        self._day_videos = {}         # section key -> how many of those are videos
        self._day_heads = {}          # section key -> the section with its title
        self._items = []
        self._by_id = {}
        # A refresh after marking a favourite or hiding keeps what was
        # selected - a selection is never dropped for that.
        self._selected = set(keep_selection or ())
        self._tile_widgets = {}
        self._tile_containers = {}
        self.sections.remove_all()
        self._show_heading()
        self._load_page()

    def _on_heading_back(self, _btn, list_item):
        section = list_item.get_item()
        back_to = getattr(section, "back_to", None)
        if back_to is not None:
            self.emit("open-folder", back_to[0])

    def _heading_section(self):
        """An album's name and size, shown in the bar above its photos. None
        for views that are not an album."""
        try:
            from ..catalog import VIDEO_SQL
            from ..video import is_video
            if self._scope == "album" and self._album_id is not None:
                row = self.catalog.q1("SELECT name, folder_id FROM albums WHERE id=?",
                                      (self._album_id,))
                n = int(self.catalog.scalar(
                    "SELECT COUNT(*) FROM album_items WHERE album_id=?", (self._album_id,), 0))
                videos = int(self.catalog.scalar(
                    f"SELECT COUNT(*) FROM album_items ai JOIN photos p ON p.id=ai.photo_id "
                    f"WHERE ai.album_id=? AND {VIDEO_SQL}", (self._album_id,), 0))
            elif self._scope == "smart" and self._smart_id is not None:
                row = self.catalog.q1("SELECT name, folder_id FROM smart_albums WHERE id=?",
                                      (self._smart_id,))
                found = self.catalog.browse(scope="smart", smart_id=self._smart_id,
                                            limit=None, offset=0)
                n = len(found)
                videos = sum(1 for r in found if is_video(r["path"]))
            elif self._scope == "duplicates":
                # What the button below does, said before anyone has to guess.
                return DaySection(
                    _("Duplicates"),
                    _("Each group below is the same photo more than once. Keep One of "
                      "Each keeps the best copy of every group - the favourite, edited "
                      "or in albums - and moves the extra copies to Recently Deleted, "
                      "where they stay for 30 days. Select some groups to do it for "
                      "just those."), [], heading=True)
            else:
                return None
        except Exception:
            return None
        if row is None:
            return None
        section = DaySection(row["name"], count_label(n - videos, videos), [], heading=True)
        section.counts = (n - videos, videos)
        # Inside a folder, a back arrow beside the name returns to it.
        if row["folder_id"] is not None:
            folder = self.catalog.q1("SELECT id, name FROM folders WHERE id=?",
                                     (row["folder_id"],))
            if folder is not None:
                section.back_to = (int(folder["id"]), folder["name"])
        return section

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
        limit = FIRST_PAGE if offset == 0 else PAGE
        # the album bar's Photos or Videos, on top of the toolbar's filters
        filters = self._filters | ({self._kind} if self._kind else set())

        def work():
            try:
                rows = self.catalog.browse(
                    scope=self._scope, album_id=self._album_id,
                    smart_id=self._smart_id,
                    search=self._search, order=self._order,
                    filters=filters,
                    limit=limit, offset=offset)
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
                if not rows or len(rows) < limit:
                    self._exhausted = True
                self._continue_reveal()
                empty = not self._items
                if empty:
                    self._describe_empty()
                self.status.set_visible(empty)
                self._show_grid(not empty)
                if not self._exhausted and not self._loading:
                    # The rest arrives in the background, a page at a time,
                    # with a pause between pages so the window stays smooth.
                    GLib.timeout_add(40, lambda: (gen == self._generation
                                                  and self._load_page(), False)[1])
                return False
            GLib.idle_add(apply)
        threading.Thread(target=work, daemon=True).start()

    def _chunk_size(self) -> int:
        """Tiles per list row: always whole rows of the grid, so a day that
        continues in the next list row never shows a short row in the middle."""
        return max(1, self._columns or 6) * 6

    def _append_rows(self, rows) -> None:
        items = [PhotoItem(row) for row in rows]
        for item in items:
            self._items.append(item)
            self._by_id[item.id] = item
        self._group(items)

    def _group(self, items) -> None:
        mode = self.settings.get("group_by", "day")
        sort_by_date = self._order.startswith("taken")
        if not hasattr(self, "_day_counts"):
            self._day_counts, self._day_heads = {}, {}
        if not hasattr(self, "_day_videos"):
            self._day_videos = {}
        chunk = self._chunk_size()
        new_sections, new_ids = [], set()
        touched = set()
        grown = None
        tail = self._tail
        dupes = self._scope == "duplicates"
        for item in items:
            if dupes:
                # one set of identical copies per block, named after the photo
                key, title, sub = (item.fingerprint or f"id{item.id}", item.filename, "")
            elif not sort_by_date or mode == "none":
                key, title, sub = ("all", "", "")
            else:
                key, title, sub = _section_key(item.taken_at, mode)
            self._day_counts[key] = self._day_counts.get(key, 0) + 1
            if getattr(item, "is_video", False):
                self._day_videos[key] = self._day_videos.get(key, 0) + 1
            touched.add(key)

            if (tail is not None and tail[0] == key
                    and len(tail[1].items) < chunk):
                tail[1].items.append(item)
                if id(tail[1]) not in new_ids:
                    grown = tail[1]         # the last row of the page before
                continue
            # A day that runs past the chunk continues in a row of its own,
            # without repeating the day's title.
            continued = tail is not None and tail[0] == key
            section = DaySection("" if continued else title, "", [item])
            if not continued:
                self._day_heads[key] = section
            tail = (key, section)
            new_sections.append(section)
            new_ids.add(id(section))
        self._tail = tail
        if grown is not None:
            # Only that one row is rebuilt, to fill its last grid row.
            found, pos = self.sections.find(grown)
            if found:
                self.sections.items_changed(pos, 1, 1)

        # The day's photo count sits under its title, which may be a row
        # already on screen: update just that label, not the whole list,
        # so nothing is rebuilt while more photos arrive.
        for key in touched:
            head = self._day_heads.get(key)
            if head is None or not head.title:
                continue
            n = self._day_counts[key]
            if dupes:
                head.subtitle = ngettext("{count} identical copy", "{count} identical copies",
                                         n).format(count=n)
            else:
                videos = self._day_videos.get(key, 0)
                head.subtitle = count_label(n - videos, videos)
            label = head.sub_label
            if label is not None:
                label.set_text(head.subtitle)
                label.set_visible(True)

        if new_sections:
            self.sections.splice(self.sections.get_n_items(), 0, new_sections)

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
