"""The main window: sidebar, grid, viewer and editor in one navigation stack."""
from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path

import gi
from ..i18n import _, ngettext

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gdk, Gio, Graphene, Gtk  # noqa: E402

from .. import devices as devicemod
from .. import sidecars
from ..catalog import Catalog
from ..indexer import Indexer
from ..paths import Library
from ..settings import Settings
from ..thumbs import ThumbCache
from .editor import EditorView
from .grid import PhotoGrid
from .viewer import ViewerView
from .chrome import IS_MAC, PRIMARY_MASK, key_name

# How narrow and how wide the sidebar can be dragged.
from .chrome import SIDEBAR_MIN_WIDTH as SIDEBAR_MIN  # wider on a Mac: window buttons
SIDEBAR_MAX = 420

class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app, library: Library):
        from .chrome import fit
        # Never larger than the screen: a Mac does not shrink a window that
        # opens bigger than the screen, so the bottom of it (and every
        # dialog centred in it) ended up out of reach.
        width, height = fit(1400, 900, share=0.92)
        super().__init__(application=app, title="Piklin",
                         default_width=width, default_height=height)
        self.add_css_class("piklin")
        self.library = library
        self.settings = Settings(library.settings)
        self.catalog = Catalog(library.db)
        # A library that was moved still names its old location in the
        # paths it stores; bring them up to date before anything reads them.
        from ..library_move import relocate
        relocate(library, self.catalog, self.settings)
        self.thumbs = ThumbCache(library.thumbs,
                                 workers=self.settings.get("thumb_workers", 0),
                                 edits=library.edit_sidecar)
        self.indexer = Indexer(
            self.catalog, self.thumbs,
            follow_symlinks=bool(self.settings.get("scan_follow_symlinks")),
            library_root=library.root)

        self._scope = "library"
        self._album_id = None
        self._smart_id = None
        # Set while the sidebar is being rebuilt, so programmatically
        # restoring the selected row does not fire a redundant reload.
        self._syncing_sidebar = False
        # Folders collapsed by the user. Empty means everything starts
        # expanded, which is the more discoverable default for a library
        # that has not been organised into folders yet.
        # Remembered across launches by folder uuid - ids are renumbered
        # when the catalog is rebuilt, uuids are not.
        folded = set(self.settings.get("sidebar_collapsed_folders", []) or [])
        self._collapsed_folders: set[int] = {
            f["id"] for f in self.catalog.folders() if f["uuid"] in folded}
        self._devices: list = []
        self._device: object = None
        # A camera appearing should show up by itself, the way it does
        # when you plug one into any desktop - not after a manual rescan.
        self._device_watcher = devicemod.DeviceWatcher(self._on_devices_changed)

        self.grid = PhotoGrid(self.catalog, self.thumbs, self.settings)
        self.grid.connect("activated", self._on_photo_activated)
        self.grid.connect("open-folder", lambda _g, fid: self._open_folder(fid))
        self.grid.connect("background-menu", lambda _g, src, x, y: self._on_background_menu(src, x, y))
        self.grid.connect("selection-changed", self._on_selection_changed)
        self.grid.connect("context-menu", self._on_photo_context_menu)

        self.viewer = ViewerView(library, self.catalog, self.thumbs,
                                 self.settings)
        self.viewer.connect("closed", lambda *_: (
            self.viewer.is_fullscreen() and self.viewer.toggle_fullscreen(False),
            self._show("grid")))
        self.viewer.connect("edit-requested", self._on_edit_requested)
        self.viewer.connect("changed", lambda *_: self._refresh())
        self.viewer.connect("navigate", self._on_navigate)
        self.viewer.connect("rotate", lambda _v, turns: self._on_rotate(turns))

        self.editor = EditorView(library, self.catalog, self.settings,
                                 self.thumbs)
        self.editor.connect("closed", self._on_editor_closed)
        from .video_editor import VideoEditorView
        self.video_editor = VideoEditorView(library, self.catalog, self.settings,
                                            self.thumbs)
        self.video_editor.connect("closed", self._on_editor_closed)
        self.video_editor.connect("photo-saved", self._on_frame_saved)

        self.stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE,
            transition_duration=120)
        self.stack.add_named(self._build_library_page(), "grid")
        self.stack.add_named(self.viewer, "viewer")
        self.stack.add_named(self.editor, "editor")
        self.stack.add_named(self.video_editor, "video-editor")
        self.toasts = Adw.ToastOverlay()
        # The copy progress card floats over the bottom right of the window.
        overlay = Gtk.Overlay()
        overlay.set_child(self.stack)
        overlay.add_overlay(self._build_copy_card())
        self.toasts.set_child(overlay)
        self.set_content(self.toasts)

        self._install_shortcuts()
        self._sync_content_view()
        # Open centred on the screen the window appears on, whatever the
        # desktop's own placement policy is.
        self._centred = False
        self.connect("map", self._centre_on_screen)
        # Items stay in Recently Deleted for 30 days, then leave the
        # library for good (they are remembered, so a scan does not add
        # them back). The user's file is never erased without them
        # choosing "Delete Files".
        try:
            self.catalog.forget_expired_trash(days=30)
        except Exception:
            pass
        from ..autobackup import AutoBackup
        self.autobackup = AutoBackup(library, self.settings, self._on_backup_status,
                                     sync=self._sync_from_backup)
        GLib.idle_add(self._first_run)

    # ==================================================================
    # layout
    # ==================================================================
    def _build_library_page(self):
        # Drag the sidebar's edge to make it wider, so the names of albums
        # deep inside folders can be read; the width is remembered.
        self.split = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL,
                               wide_handle=False)
        self.split.add_css_class("pika-split")
        sidebar = self._build_sidebar()
        sidebar.set_size_request(SIDEBAR_MIN, -1)
        self.split.set_start_child(sidebar)
        self.split.set_resize_start_child(False)
        self.split.set_shrink_start_child(False)
        self.split.set_end_child(self._build_content())
        self.split.set_resize_end_child(True)
        self.split.set_shrink_end_child(False)
        width = int(self.settings.get("sidebar_width", 280) or 280)
        self.split.set_position(max(SIDEBAR_MIN, min(SIDEBAR_MAX, width)))
        self._sidebar_save = 0
        self.split.connect("notify::position", self._on_sidebar_width)
        return self.split

    def _on_sidebar_width(self, paned, _pspec):
        pos = paned.get_position()
        if pos > SIDEBAR_MAX:
            paned.set_position(SIDEBAR_MAX)
            return
        if pos < SIDEBAR_MIN:
            paned.set_position(SIDEBAR_MIN)
            return
        # saved once the dragging stops, not on every pixel
        if self._sidebar_save:
            GLib.source_remove(self._sidebar_save)

        def save():
            self._sidebar_save = 0
            self.settings.set("sidebar_width", paned.get_position())
            return False
        self._sidebar_save = GLib.timeout_add(400, save)

    def _build_sidebar(self):
        toolbar = Adw.ToolbarView()
        toolbar.add_css_class("pika-sidebar-pane")
        toolbar.set_top_bar_style(Adw.ToolbarStyle.RAISED_BORDER)
        header = Adw.HeaderBar(show_title=True)
        header.add_css_class("pika-header")
        # No window buttons on the sidebar's bar on Linux: they sit on the
        # right of the content bar. On a Mac they belong here, at the top left.
        from .chrome import SIDEBAR_LAYOUT, add_header_controls
        header.set_decoration_layout(SIDEBAR_LAYOUT)
        add_header_controls(header)
        # The app's mark, centred over the sidebar.
        from .brand import BrandMark
        from .chrome import BRAND_IN_SIDEBAR, BRAND_SIZE
        self.brand = BrandMark(BRAND_SIZE)
        if BRAND_IN_SIDEBAR:
            # The bar belongs to the window buttons; the brand opens the
            # sidebar instead, just below them.
            header.set_title_widget(Gtk.Box())
            self.brand.add_css_class("pika-brand-sidebar")
        else:
            header.set_title_widget(self.brand)
        menu = Gio.Menu()
        sort_menu = Gio.Menu()
        for key, label in (("taken_desc", _("Newest First")),
                           ("taken_asc", _("Oldest First")),
                           ("added_desc", _("Recently Added")),
                           ("name_asc", _("Name")),
                           ("size_desc", _("File Size")),
                           ("rating_desc", _("Rating"))):
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value(
                "win.sort", GLib.Variant.new_string(key))
            sort_menu.append_item(item)
        menu.append_submenu(_("Sort By"), sort_menu)
        menu.append(_("Open Library…"), "win.open-library")
        menu.append(_("Add Folder…"), "win.add-folder")
        menu.append(_("Look for New Photos"), "win.rescan")
        section = Gio.Menu()
        section.append(_("Backups…"), "win.remotes")
        section.append(_("Storage…"), "win.storage")
        menu.append_section(None, section)
        end = Gio.Menu()
        end.append(_("Preferences"), "win.preferences")
        help_menu = Gio.Menu()
        help_menu.append(_("How to Use Piklin"), "win.help")
        help_menu.append(_("Check for Updates…"), "win.check-updates")
        help_menu.append(_("Activity Log"), "win.activity-log")
        help_menu.append(_("About Piklin"), "win.about")
        end.append_submenu(_("Help"), help_menu)
        menu.append_section(None, end)
        # An icon-only button has no name a screen reader can announce;
        # the tooltip text doubles as its accessible label.
        btn = Gtk.MenuButton(icon_name="open-menu-symbolic",
                             menu_model=menu, primary=True,
                             tooltip_text=_("Main Menu"))
        header.pack_end(btn)
        toolbar.add_top_bar(header)

        self.sidebar_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.sidebar_list.add_css_class("navigation-sidebar")
        self.sidebar_list.add_css_class("pika-sidebar")
        self.sidebar_list.connect("row-selected", self._on_sidebar_selected)
        # "New Album…" is an action row, not a destination, so it is not
        # selectable and never emits row-selected. Activation is what
        # carries a click on it.
        self.sidebar_list.connect("row-activated", self._on_sidebar_activated)

        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      vexpand=True)
        scroller.set_child(self.sidebar_list)
        if BRAND_IN_SIDEBAR:
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            column.append(self.brand)
            column.append(scroller)
            toolbar.set_content(column)
        else:
            toolbar.set_content(scroller)

        self.scan_bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                                margin_start=12, margin_end=12,
                                margin_top=6, margin_bottom=10)
        self.scan_label = Gtk.Label(xalign=0.0, ellipsize=3)
        self.scan_label.add_css_class("pika-dim")
        self.scan_progress = Gtk.ProgressBar()
        self.scan_bar.append(self.scan_label)
        self.scan_bar.append(self.scan_progress)
        self.scan_bar.set_visible(False)
        toolbar.add_bottom_bar(self.scan_bar)

        # What the library holds and what it costs on disk, quietly, at the
        # foot of the sidebar: "12,480 Photos · 36 Videos" and
        # "41.2 GB of 512 GB", with a hairline meter.
        self.footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.footer.add_css_class("pika-sidebar-footer")
        self.footer_counts = Gtk.Label(xalign=0.0, ellipsize=3)
        self.footer_counts.add_css_class("pika-footer-counts")
        self.footer_meter = Gtk.LevelBar(min_value=0.0, max_value=1.0)
        self.footer_meter.add_css_class("pika-footer-meter")
        self.footer_meter.remove_offset_value("low")
        self.footer_meter.remove_offset_value("high")
        self.footer_meter.remove_offset_value("full")
        self.footer_space = Gtk.Label(xalign=0.0, ellipsize=3)
        self.footer_space.add_css_class("pika-footer-space")
        self.footer.append(self.footer_counts)
        self.footer.append(self.footer_meter)
        self.footer.append(self.footer_space)
        # "Backed up 2 minutes ago", "Backing up to NAS — 12 of 40"
        self.footer_backup = Gtk.Label(xalign=0.0, ellipsize=3, visible=False)
        self.footer_backup.add_css_class("pika-footer-space")
        self.footer_backup.add_css_class("pika-footer-backup")
        self.footer.append(self.footer_backup)
        # "Making videos smaller — 1 of 3 (42%)", while that runs
        self.footer_videos = Gtk.Label(xalign=0.0, ellipsize=3, visible=False)
        self.footer_videos.add_css_class("pika-footer-space")
        self.footer.append(self.footer_videos)
        # Update Available: a black button with a ringing bell, only there
        # when a new Piklin is out. It opens what the update brings.
        inner = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        inner.append(Gtk.Image(icon_name="preferences-system-notifications-symbolic",
                               pixel_size=16))
        inner.append(Gtk.Label(label=_("Update Available")))
        self._update_bell = Gtk.Button(child=inner, visible=False, halign=Gtk.Align.FILL,
                                       margin_top=10,
                                       tooltip_text=_("A new version of Piklin is available"))
        self._update_bell.add_css_class("pika-update-bell")
        self._update_bell.connect("clicked", lambda *_: self._open_update_dialog())
        self._bell_release = None
        self.footer.append(self._update_bell)
        # Supporting Piklin: one quiet button, never a pop-up or a nag.
        from ..paths import SUPPORT_URL
        if SUPPORT_URL:
            coffee = Gtk.Button(halign=Gtk.Align.FILL, margin_top=10,
                                tooltip_text=_("Support Piklin"))
            coffee.add_css_class("pika-support")
            inner = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
            inner.append(Gtk.Image(icon_name="emblem-favorite-symbolic",
                                   pixel_size=15))
            inner.append(Gtk.Label(label=_("Support on Ko-fi")))
            coffee.set_child(inner)
            coffee.connect("clicked", lambda *_: Gtk.UriLauncher.new(
                SUPPORT_URL).launch(self, None, None, None))
            self.footer.append(coffee)
        toolbar.add_bottom_bar(self.footer)
        return toolbar

    def _build_content(self):
        toolbar = Adw.ToolbarView()
        toolbar.add_css_class("pika-content")
        toolbar.set_top_bar_style(Adw.ToolbarStyle.RAISED_BORDER)
        header = Adw.HeaderBar()
        header.add_css_class("pika-header")
        # Minimise, maximise, close on the trailing edge, whatever order
        # the desktop's own setting would put them in (none here on a Mac,
        # where they are at the start of the sidebar's bar).
        from .chrome import CONTENT_LAYOUT, IS_MAC
        header.set_decoration_layout(CONTENT_LAYOUT)
        if IS_MAC:
            # Otherwise the bar keeps an empty slot for the system's buttons.
            header.set_show_start_title_buttons(False)
            header.set_show_end_title_buttons(False)

        self.search = Gtk.SearchEntry(placeholder_text=_("Search photos"),
                                      width_chars=22)
        self.search.add_css_class("pika-search")
        self.search.connect("search-changed", self._on_search)

        # The signature control: how the library is grouped is the
        # main thing you change while browsing, so it belongs in the
        # toolbar as one segmented control - not buried in Preferences,
        # which is where this setting used to live.
        self.view_switch = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.view_switch.add_css_class("linked")
        self.view_switch.add_css_class("pika-viewswitch")
        self._view_buttons = {}
        first = None
        current = self.settings.get("group_by", "day")
        for key, label in (("year", _("Years")), ("month", _("Months")),
                           ("day", _("Days")), ("none", _("All Photos"))):
            btn = Gtk.ToggleButton(label=label)
            if first is None:
                first = btn
            else:
                btn.set_group(first)
            btn.set_active(key == current)
            btn.connect("toggled", self._on_view_changed, key)
            self.view_switch.append(btn)
            self._view_buttons[key] = btn

        header.set_title_widget(self.view_switch)

        self.zoom_scale = zoom = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 90, 400, 10)
        zoom.set_value(self.settings.get("grid_size", 200))
        zoom.set_draw_value(False)
        zoom.set_size_request(104, -1)
        zoom.set_tooltip_text(_("Photo size"))
        zoom.connect("value-changed", self._on_zoom)
        header.pack_start(zoom)

        # Shown in the bar at the bottom, not up here: a label appearing in
        # the header widened it, which narrowed the sidebar and resized
        # every tile - the photos jumped as soon as one was chosen.
        self.select_label = Gtk.Label()
        self.select_label.add_css_class("pika-dim")

        header.pack_end(self.search)

        # Filter: a pop-up of what to show, several
        # at once, inside whichever view is open.
        self.filter_btn = Gtk.MenuButton(label=_("Filter"),
                                         tooltip_text=_("Filter photos"))
        self.filter_btn.add_css_class("flat")
        self.filter_btn.add_css_class("pika-filter")
        pop = Gtk.Popover()
        pbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                       margin_top=8, margin_bottom=8, margin_start=10,
                       margin_end=10)
        self._filter_checks = {}
        for key, label in (("favorites", _("Favourites")), ("edited", _("Edited")),
                           ("photos", _("Photos")), ("videos", _("Videos")),
                           ("screenshots", _("Screenshots")),
                           ("not_in_album", _("Not in an Album")),
                           ("has_location", _("Has Location"))):
            chk = Gtk.CheckButton(label=label)
            chk.connect("toggled", self._on_filter_toggled)
            pbox.append(chk)
            self._filter_checks[key] = chk
        pbox.append(Gtk.Separator(margin_top=4, margin_bottom=4))
        show_all = Gtk.Button(label=_("Show All"))
        show_all.add_css_class("flat")
        show_all.connect("clicked", lambda *_: self._clear_filters())
        pbox.append(show_all)
        pop.set_child(pbox)
        self.filter_btn.set_popover(pop)
        header.pack_end(self.filter_btn)

        # Aspect Ratio: square thumbnails, or each photo at its own shape.
        self.aspect_btn = Gtk.ToggleButton(
            icon_name="view-grid-symbolic",
            tooltip_text=_("Show whole photos instead of squares"),
            active=self.settings.get("grid_aspect") == "original")
        self.aspect_btn.add_css_class("flat")
        self.aspect_btn.connect("toggled", self._on_aspect_toggled)
        header.pack_start(self.aspect_btn)

        self.action_bar = Gtk.ActionBar()
        self.action_bar.add_css_class("pika-toolbar")
        self.action_bar.set_revealed(False)
        self._bar_buttons = {}
        for key, icon, tip, cb in (
                ("favorite", "starred-symbolic", _("Favourite"),
                 self._on_bulk_favorite),
                ("trash", "user-trash-symbolic", _("Move to Recently Deleted"),
                 self._on_trash_button),
                ("export", "document-save-symbolic", _("Export…"),
                 self._on_bulk_export),
                ("album", "list-add-symbolic", _("Add to Album…"),
                 self._on_bulk_album),
                # Turns every selected photo at once.
                ("rotate-ccw", "object-rotate-left-symbolic",
                 _("Rotate Left (Ctrl+Shift+R)"), lambda *_a: self._on_rotate(-1)),
                ("rotate-cw", "object-rotate-right-symbolic",
                 _("Rotate Right (Ctrl+R)"), lambda *_a: self._on_rotate(1))):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip)
            b.connect("clicked", cb)
            self.action_bar.pack_start(b)
            self._bar_buttons[key] = b
        # Only meaningful inside Recently Deleted, so it is hidden until
        # you are actually standing there.
        self._import_btn = Gtk.Button(label=_("Import"))
        self._import_btn.add_css_class("suggested-action")
        self._import_btn.connect("clicked", self._on_import_device)
        self._import_btn.set_visible(False)
        self.action_bar.pack_end(self._import_btn)

        # The import screen: where the photos go, and whether the
        # camera keeps them afterwards.
        self._import_delete = Gtk.CheckButton(label=_("Delete from the camera after importing"))
        self._import_delete.set_visible(False)
        self.action_bar.pack_end(self._import_delete)
        self._import_album = Gtk.DropDown.new_from_strings([_("Library")])
        self._import_album.set_tooltip_text(_("Import to"))
        self._import_album.set_visible(False)
        self._import_album_ids = [None]
        self._import_album_label = Gtk.Label(label=_("Import to:"))
        self._import_album_label.add_css_class("pika-dim")
        self._import_album_label.set_visible(False)
        self.action_bar.pack_start(self._import_album_label)
        self.action_bar.pack_start(self._import_album)

        # Duplicates view only: keep one copy of each selected group.
        self._merge_btn = Gtk.Button(label=_("Keep One"))
        self._merge_btn.add_css_class("suggested-action")
        self._merge_btn.connect("clicked", self._on_merge_duplicates)
        self._merge_btn.set_visible(False)
        self.action_bar.pack_end(self._merge_btn)

        self._purge_btn = Gtk.Button(label=_("Delete Permanently…"))
        self._purge_btn.add_css_class("destructive-action")
        self._purge_btn.connect("clicked", self._on_purge)
        self._purge_btn.set_visible(False)
        self.action_bar.pack_start(self._purge_btn)
        clear = Gtk.Button(label=_("Unselect"))
        clear.add_css_class("flat")
        clear.connect("clicked", lambda *_: self.grid.unselect_all())
        self.action_bar.pack_end(clear)
        self.action_bar.pack_end(self.select_label)

        toolbar.add_top_bar(header)
        # In the library, Years and Months are summary cards; everywhere
        # else - albums, Days, All Photos, a search - it is the photo grid.
        from .summary import SummaryView
        self.summary = SummaryView(self.catalog, self.thumbs)
        self.summary.connect("open-group", self._on_summary_open)
        self.content_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE,
            transition_duration=120)
        self.content_stack.add_named(self.grid, "grid")
        # Photos dragged in from the desktop or a file manager
        self._install_file_drop(self.content_stack)
        self.content_stack.add_named(self.summary, "summary")
        # A folder in the sidebar shows the albums and folders inside it.
        from .folder_view import FolderView
        self.folder_view = FolderView(self.catalog, self.thumbs)
        self.folder_view.connect("open-album", lambda _v, i: self._open_album(i))
        self.folder_view.connect("open-smart", lambda _v, i: self._open_smart(i))
        self.folder_view.connect("open-folder", lambda _v, i: self._open_folder(i))
        self.content_stack.add_named(self.folder_view, "folder")
        toolbar.set_content(self.content_stack)
        toolbar.add_bottom_bar(self.action_bar)
        # The bar lies over the photos rather than taking their space: the
        # view shrinking as it appeared moved the photos up under the pointer.
        toolbar.set_extend_content_to_bottom_edge(True)
        return toolbar

    # ==================================================================
    # sidebar
    # ==================================================================
    @staticmethod
    def _human_bytes(n: float) -> str:
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1000 or unit == "TB":
                return (f"{n:.0f} {unit}" if unit in ("B", "KB") or n >= 100
                        else f"{n:.1f} {unit}")
            n /= 1000.0
        return f"{n:.1f} TB"

    def _update_footer(self, counts):
        import shutil as _sh
        photos = max(0, int(counts.get("library", 0)) - int(counts.get("videos", 0)))
        videos = int(counts.get("videos", 0))
        self.footer_counts.set_text(
            ngettext("{count} Photo", "{count} Photos", photos).format(count=f"{photos:,}")
            + "  ·  "
            + ngettext("{count} Video", "{count} Videos", videos).format(count=f"{videos:,}"))
        used = int(counts.get("bytes", 0))
        try:
            total = _sh.disk_usage(self.library.root).total
        except OSError:
            total = 0
        if total:
            self.footer_space.set_text(
                _("{used} of {total}").format(used=self._human_bytes(used), total=self._human_bytes(total)))
            self.footer_meter.set_value(min(1.0, used / total))
            self.footer_meter.set_visible(True)
        else:
            self.footer_space.set_text(self._human_bytes(used))
            self.footer_meter.set_visible(False)
        if getattr(self, "autobackup", None) is not None:
            self.autobackup.refresh_status()

    def refresh_sidebar(self):
        counts = self.catalog.counts()
        self._update_footer(counts)
        selected_key = getattr(
            self.sidebar_list.get_selected_row(), "_key", "library")
        self._syncing_sidebar = True

        while (row := self.sidebar_list.get_first_child()) is not None:
            self.sidebar_list.remove(row)

        section_state = {"collapsed": False}

        def add(key, label, icon, count=None, album_id=None, indent=0,
                folder_id=None, smart_id=None, cover=None):
            if section_state["collapsed"]:
                return None
            # A folder in the tree is purely organisational - there is no
            # "current folder" scope to browse, so it never shows a
            # persistent selection highlight; clicking it only expands
            # or collapses via row-activated.
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                          margin_start=8 + indent, margin_end=8,
                          margin_top=3, margin_bottom=3)
            if folder_id is not None:
                # A folder's own disclosure triangle: clicking it toggles
                # the subtree without navigating anywhere, since a folder
                # is purely organisational and has no photos of its own.
                collapsed = folder_id in self._collapsed_folders
                arrow = Gtk.Image(
                    icon_name=("pan-end-symbolic" if collapsed
                              else "pan-down-symbolic"),
                    pixel_size=12)
                box.append(arrow)
                fold = Gtk.GestureClick()

                def on_fold(gesture, *_a, f=folder_id):
                    gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                    self._toggle_folder(f)
                fold.connect("pressed", on_fold)
                arrow.add_controller(fold)
                # Double-clicking the folder itself opens or closes it in
                # the sidebar; a single click only shows its albums.
                twice = Gtk.GestureClick()

                def on_twice(gesture, n_press, *_a, f=folder_id):
                    if n_press == 2:
                        self._toggle_folder(f)
                twice.connect("pressed", on_twice)
                box.add_controller(twice)
            elif indent:
                # Album rows nested under a folder line up with the
                # label text of their folder's siblings, not its arrow.
                spacer = Gtk.Box()
                spacer.set_size_request(12, -1)
                box.append(spacer)
            if cover:
                # An album shows a small picture of its cover, not an icon.
                box.append(self._sidebar_thumb(cover))
            else:
                box.append(Gtk.Image(icon_name=icon))
            box.append(Gtk.Label(label=label, xalign=0.0, hexpand=True,
                                 ellipsize=3))
            if count:
                c = Gtk.Label(label=f"{count:,}")
                c.add_css_class("pika-count")
                box.append(c)
            row.set_child(box)
            row._key = key
            row._album_id = album_id
            row._folder_id = folder_id
            row._smart_id = smart_id
            # Right-click (and long-press, for a touchscreen) opens rename
            # / delete for a folder or album row. Library entries like
            # "All Photos" or "Trash" get no gesture at all, so there is
            # nothing for a right-click to open there.
            if album_id is not None or folder_id is not None:
                self._make_drop_target(row, album_id, folder_id)
            drag_value = (f"pika-album:{album_id}" if album_id is not None
                          else f"pika-folder:{folder_id}" if folder_id is not None
                          else f"pika-smart:{smart_id}" if smart_id is not None
                          else None)
            if drag_value is not None:
                # Albums, Smart Albums and folders are dragged onto a folder
                # to go into it, or onto the Albums heading to leave folders.
                src = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
                src.connect(
                    "prepare",
                    lambda _s, _x, _y, v=drag_value:
                        Gdk.ContentProvider.new_for_value(v))
                row.add_controller(src)
            if smart_id is not None:
                rc = Gtk.GestureClick(button=3)
                rc.connect("pressed", self._on_smart_right_click,
                           smart_id, label)
                row.add_controller(rc)
            if album_id is not None or folder_id is not None:
                rc = Gtk.GestureClick(button=3)
                rc.connect("pressed", self._on_sidebar_right_click,
                          album_id, folder_id, label)
                row.add_controller(rc)
                lp = Gtk.GestureLongPress()
                # GestureLongPress's own "pressed" signal has no n_press
                # (a long-press only ever fires once) - pass 1 for that
                # slot so both gestures can share the same handler.
                lp.connect(
                    "pressed",
                    lambda g, x, y, a=album_id, f=folder_id, n=label:
                        self._on_sidebar_right_click(g, 1, x, y, a, f, n))
                row.add_controller(lp)
            self.sidebar_list.append(row)
            return row

        collapsed_sections = set(self.settings.get("sidebar_collapsed", []) or [])

        def header(text, menu_model=None, collapsible=True):
            """A section heading - LIBRARY, ALBUMS, MEDIA TYPES, UTILITIES.

            Clicking the heading (or its chevron) folds the section away and
            back, so a long list of albums does not
            push everything else out of view. What is folded is remembered.
            The "+" beside ALBUMS stays its own button: pressing it adds,
            it does not fold.
            """
            collapsed = collapsible and text in collapsed_sections
            section_state["collapsed"] = collapsed
            row = Gtk.ListBoxRow(selectable=False, activatable=False,
                                 focusable=False)
            row.add_css_class("pika-sidebar-header-row")
            row._key = f"header:{text}"
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            box.add_css_class("pika-sidebar-header-box")

            toggle = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                             hexpand=True)
            toggle.add_css_class("pika-sidebar-toggle")
            toggle.set_cursor_from_name("pointer")
            lbl = Gtk.Label(label=text, xalign=0.0)
            lbl.add_css_class("pika-sidebar-header")
            toggle.append(lbl)
            if collapsible:
                chevron = Gtk.Image(icon_name=("pan-end-symbolic" if collapsed
                                               else "pan-down-symbolic"))
                chevron.add_css_class("pika-sidebar-chevron")
                toggle.append(chevron)
                toggle.update_property(
                    [Gtk.AccessibleProperty.LABEL],
                    [f"{text}, {'collapsed' if collapsed else 'expanded'}"])
                click = Gtk.GestureClick()
                click.connect("released",
                              lambda *_a, t=text: self._toggle_section(t))
                toggle.add_controller(click)
            else:
                toggle.remove_css_class("pika-sidebar-toggle")
                toggle.set_cursor_from_name("default")
            box.append(toggle)

            if menu_model is not None:
                plus = Gtk.MenuButton(icon_name="list-add-symbolic",
                                      menu_model=menu_model,
                                      valign=Gtk.Align.CENTER,
                                      tooltip_text=_("New Album or Folder"))
                plus.add_css_class("flat")
                plus.add_css_class("pika-sidebar-add")
                box.append(plus)
            row.set_child(box)
            self.sidebar_list.append(row)
            return row

        self._devices = devicemod.list_devices()
        if self._devices:
            header(_("Devices"))
            for dev in self._devices:
                add(f"device:{dev.id}", dev.name, dev.icon)

        # The sidebar order: the library and what you most often come back
        # to first, then media types and the utility views that gather
        # photos by what happened to them - all fixed in place. Albums come
        # last, under a dividing line: however many there are, they grow
        # downwards without pushing anything else out of view.
        # Library is where everything starts; it stays open.
        header(_("Library"), collapsible=False)
        add("library", _("All Photos"), "image-x-generic-symbolic",
            counts.get("library"))
        add("favorites", _("Favourites"), "starred-symbolic",
            counts.get("favorites"))
        add("trash", _("Recently Deleted"), "user-trash-symbolic",
            counts.get("trash"))

        header(_("Types"))
        add("videos", _("Videos"), "video-x-generic-symbolic",
            counts.get("videos"))
        add("screenshots", _("Screenshots"), "video-display-symbolic",
            counts.get("screenshots"))

        header(_("Utilities"))
        add("hidden", _("Hidden"), "view-conceal-symbolic", counts.get("hidden"))
        add("duplicates", _("Duplicates"), "edit-copy-symbolic",
            counts.get("duplicates"))
        add("edited", _("Recently Edited"), "document-edit-symbolic",
            counts.get("edited"))
        add("imports", _("Imports"), "document-save-symbolic",
            counts.get("imports"))

        divider = Gtk.ListBoxRow(selectable=False, activatable=False,
                                 focusable=False)
        divider.add_css_class("pika-sidebar-divider")
        divider._key = "divider:albums"
        divider.set_child(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        self.sidebar_list.append(divider)

        add_menu = Gio.Menu()
        add_menu.append(_("New Album…"), "win.new-album")
        add_menu.append(_("New Smart Album…"), "win.new-smart-album")
        add_menu.append(_("New Folder…"), "win.new-folder")
        albums_heading = header(_("Albums"), add_menu)
        self._make_drop_target(albums_heading, None, None, top=True)
        self._add_tree_rows(self.catalog.tree(), add, depth=0)

        for child in self.sidebar_list:
            if getattr(child, "_key", None) == selected_key:
                self.sidebar_list.select_row(child)
                break
        else:
            # The place being viewed is inside a folded section, so its row
            # is not in the list. Keep showing it; only the highlight goes.
            # (Selecting "the second row" instead switched the view on its
            # own - that row can now be a section heading.)
            self.sidebar_list.unselect_all()
        self._syncing_sidebar = False

    def _centre_on_screen(self, *_):
        """Centre the window on its monitor the first time it is shown.

        GTK 4 has no window-position API: on Wayland the compositor alone
        places windows (GNOME and KDE centre or smart-place new ones), so
        nothing can be forced there. On X11 - Cinnamon, MATE, Xfce, KDE on
        X11 and others - the window is moved directly with XMoveWindow once
        it is mapped. A window larger than the screen is shrunk to fit
        first, so it never opens partly off-screen.
        """
        if self._centred:
            return
        self._centred = True

        tries = {"n": 0}

        def centre():
            tries["n"] += 1
            try:
                gi.require_version("GdkX11", "4.0")
                from gi.repository import GdkX11
            except (ValueError, ImportError):
                return GLib.SOURCE_REMOVE
            display = self.get_display()
            surface = self.get_surface()
            if not isinstance(display, GdkX11.X11Display) or surface is None:
                return GLib.SOURCE_REMOVE
            monitor = display.get_monitor_at_surface(surface)
            if monitor is None:
                return GLib.SOURCE_REMOVE
            try:
                area = monitor.get_workarea()
            except Exception:
                area = monitor.get_geometry()
            # A window larger than the screen is shrunk to fit first.
            dw, dh = self.get_default_size()
            max_w, max_h = int(area.width * 0.92), int(area.height * 0.92)
            if dw > max_w or dh > max_h:
                dw, dh = min(dw, max_w), min(dh, max_h)
                self.set_default_size(dw, dh)
            # Right after mapping, the window has not reached its final size
            # yet; centring then used the provisional size and the window
            # landed off-centre. Wait until it is really its size (or give
            # up waiting after ~1.5 s and use whatever it is).
            if (self.get_width() < dw - 2 or self.get_height() < dh - 2) \
                    and tries["n"] < 30:
                return GLib.SOURCE_CONTINUE
            # The X window includes the client-side shadow around the frame.
            sw, sh = surface.get_width(), surface.get_height()
            x = area.x + (area.width - sw) // 2
            y = area.y + (area.height - sh) // 2
            try:
                # GTK hands back its X display as a boxed object, not a raw
                # pointer, so talk to the X server over a short connection of
                # our own: moving a window is a request to the server, and
                # any client may make it.
                import ctypes
                xlib = ctypes.CDLL("libX11.so.6")
                xlib.XOpenDisplay.restype = ctypes.c_void_p
                xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
                xlib.XMoveWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                             ctypes.c_int, ctypes.c_int]
                xlib.XFlush.argtypes = [ctypes.c_void_p]
                xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]
                dpy = xlib.XOpenDisplay(None)
                if dpy:
                    xid = GdkX11.X11Surface.get_xid(surface)
                    xlib.XMoveWindow(dpy, xid, max(area.x, x), max(area.y, y))
                    xlib.XFlush(dpy)
                    xlib.XCloseDisplay(dpy)
            except Exception:
                pass
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(50, centre)

    def _save_folded_folders(self):
        uuids = {f["id"]: f["uuid"] for f in self.catalog.folders()}
        self.settings.set("sidebar_collapsed_folders",
                          sorted(uuids[i] for i in self._collapsed_folders if i in uuids))

    def _toggle_section(self, name):
        folded = list(self.settings.get("sidebar_collapsed", []) or [])
        if name in folded:
            folded.remove(name)
        else:
            folded.append(name)
        self.settings.set("sidebar_collapsed", folded)
        self.refresh_sidebar()

    def _select_sidebar_key(self, key):
        """Move the sidebar's highlight to a given destination."""
        self._syncing_sidebar = True
        for child in self.sidebar_list:
            if getattr(child, "_key", None) == key:
                self.sidebar_list.select_row(child)
                break
        self._syncing_sidebar = False

    def _on_devices_changed(self):
        """A camera was plugged in or pulled out."""
        was_viewing = self._scope == "device"
        self.refresh_sidebar()
        if was_viewing and self._device is not None:
            still_there = any(d.id == self._device.id
                              for d in self._devices)
            if not still_there:
                # The card was pulled while we were looking at it; there
                # is nothing to show, so fall back to the library rather
                # than leave dead thumbnails on screen.
                self._device = None
                self._device_records = []
                self._scope, self._album_id = "library", None
                self.grid.unselect_all()
                self.grid.load("library")
                self._select_sidebar_key("library")
                self._on_selection_changed(self.grid)

    def _open_device(self, device_id):
        """Browse what is on a camera, without importing anything."""
        device = next((d for d in self._devices if d.id == device_id), None)
        if device is None:
            return
        self._device = device
        self._scope, self._album_id = "device", None
        self.scan_label.set_text(_("Reading {device}…").format(device=device.name))
        self.scan_bar.set_visible(True)
        self.scan_progress.set_fraction(0.0)

        self._device_records = []
        self._device_imported = set()
        started = [False]

        def still_here():
            return self._device is not None and self._device.id == device.id

        def work():
            def on_batch(batch):
                def apply():
                    if not still_here():
                        return False
                    start = len(self._device_records)
                    self._device_records.extend(batch)
                    if not started[0]:
                        started[0] = True
                        self._fill_import_albums()
                        self.grid.load_records(batch, subtitle=device.name,
                                               root=device.path)
                    else:
                        self.grid.append_records(batch, start)
                    self.scan_label.set_text(
                        _("Reading {device}… {count} found").format(device=device.name, count=f"{len(self._device_records):,}"))
                    self._on_selection_changed(self.grid)
                    return False
                GLib.idle_add(apply)

            records = devicemod.scan_device(device, on_batch=on_batch)
            already = devicemod.already_imported(self.catalog, records)

            def finish():
                self.scan_bar.set_visible(False)
                if not still_here():
                    return False
                if not started[0]:
                    self.grid.load_records([], subtitle=device.name, root=device.path)
                elif already:
                    self._device_imported = already
                    self.grid.load_records(self._device_records, subtitle=device.name,
                                           already=already, root=device.path)
                self._on_selection_changed(self.grid)
                return False
            GLib.idle_add(finish)
        threading.Thread(target=work, daemon=True).start()

    # -- copying photos in ---------------------------------------------------
    def _build_copy_card(self):
        """Progress for photos being copied in: what, how far, and Stop."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                       halign=Gtk.Align.END, valign=Gtk.Align.END,
                       margin_end=20, margin_bottom=20, visible=False)
        card.add_css_class("pika-copy-card")
        top = Gtk.Box(spacing=10)
        self.copy_label = Gtk.Label(xalign=0.0, hexpand=True, ellipsize=3,
                                    max_width_chars=30)
        self.copy_label.add_css_class("pika-copy-label")
        self.copy_percent = Gtk.Label(xalign=1.0)
        self.copy_percent.add_css_class("pika-copy-percent")
        stop = Gtk.Button(label=_("Stop"), valign=Gtk.Align.CENTER,
                          tooltip_text=_("Stop copying (what is already copied stays)"))
        stop.add_css_class("pika-copy-stop")
        stop.connect("clicked", self._on_stop_copy)
        self.copy_stop = stop
        top.append(self.copy_label)
        top.append(self.copy_percent)
        top.append(stop)
        self.copy_bar = Gtk.ProgressBar()
        self.copy_bar.add_css_class("pika-copy-bar")
        card.append(top)
        card.append(self.copy_bar)
        self.copy_card = card
        self._copy_cancel = None
        return card

    def _copy_progress(self, text, done, total):
        self.copy_card.set_visible(True)
        self.copy_label.set_text(text)
        if total:
            fraction = min(1.0, done / total)
            self.copy_bar.set_fraction(fraction)
            self.copy_percent.set_text(f"{int(fraction * 100)}%")
        else:
            self.copy_bar.pulse()
            self.copy_percent.set_text("")
        return False

    def _copy_finished(self):
        self.copy_card.set_visible(False)
        self.copy_stop.set_sensitive(True)
        self._copy_cancel = None

    def _on_stop_copy(self, _btn):
        if self._copy_cancel is not None:
            self._copy_cancel.set()
            self.copy_stop.set_sensitive(False)
            self.copy_label.set_text(_("Stopping…"))

    def _copy_busy(self):
        if self._copy_cancel is None:
            return False
        self._show_toast(_("Photos are already being copied. Wait until they finish, "
                         "or stop that copy first."))
        return True

    def _import_profiles(self, device=None):
        """What Preferences › Storage says for photos and videos copied into
        the library - from a camera, a memory card, a USB drive, or dragged
        in. Dragged-in files used to be copied as they were, whatever was
        chosen, so the smaller size someone picked was never applied."""
        return {"profile": self.settings.get("storage_profile", "visually_lossless"),
                "video_profile": self.settings.get("storage_video_profile", "original")}

    def _run_import(self, records, *, heading, done_cb, album_id=None,
                    profile="original", video_profile="original", cancel=None):
        """Copy records into the library in the background.

        Progress and a Stop button show at the bottom right, and the photos
        appear in the library - and in the album - as they arrive.
        """
        cancel = cancel or threading.Event()
        self._copy_cancel = cancel
        library = self.library
        total = len(records)
        self._copy_progress(_("{step} — {done} of {total}").format(step=heading, done=0, total=total),
                            0, total)
        self._show_toast(ngettext("Copying {count} item. You can keep using Piklin while it copies.",
                                  "Copying {count} items. You can keep using Piklin while they copy.",
                                  total).format(count=total))
        pending = []
        last_flush = [time.monotonic()]

        def flush():
            batch = pending[:]
            pending.clear()
            last_flush[0] = time.monotonic()
            if batch:
                self.indexer.add_files(batch)
                GLib.idle_add(self._show_arrivals, album_id, batch)

        def work():
            def on_placed(dest):
                pending.append(dest)
                if len(pending) >= 30 or time.monotonic() - last_flush[0] > 2.0:
                    flush()

            def progress(done, count):
                GLib.idle_add(self._copy_progress,
                              _("{step} — {done} of {total}").format(step=heading, done=done, total=count),
                              done, count)
            try:
                result = devicemod.import_photos(
                    library, records, progress, profile=profile,
                    video_profile=video_profile, on_placed=on_placed, cancel=cancel)
            except Exception:
                result = {"copied": [], "skipped": 0, "failed": total, "sources": [],
                          "placed": [], "cancelled": cancel.is_set()}
            flush()

            def finish():
                self._copy_finished()
                if album_id is not None:
                    self._write_album_sidecar(album_id)
                done_cb(result)
                self._backup_soon()
                self._queue_video_shrink(result.get("to_shrink") or [])
                return False
            GLib.idle_add(finish)
        threading.Thread(target=work, daemon=True).start()

    def _show_arrivals(self, album_id, paths):
        """Photos that just arrived: into their album, and on screen."""
        if album_id is not None:
            ids = []
            for path in paths:
                row = self.catalog.photo_by_path(str(Path(path).resolve()))
                if row is not None:
                    ids.append(row["id"])
            if ids:
                self.catalog.album_add(album_id, ids)
        self.grid.refresh()
        self.refresh_sidebar()
        return False

    @staticmethod
    def _import_summary(result, extra=0, album_name=None):
        n = len(result["placed"]) + extra
        if album_name:
            text = ngettext("Imported {count} item into “{album}”",
                            "Imported {count} items into “{album}”", n).format(count=n, album=album_name)
        else:
            text = ngettext("Imported {count} item", "Imported {count} items", n).format(count=n)
        if result["failed"]:
            text += ", " + ngettext("{count} could not be copied", "{count} could not be copied",
                                    result["failed"]).format(count=result["failed"])
        if result.get("cancelled"):
            text = _("Stopped. {summary}").format(summary=text)
        return text

    def _on_import_device(self, _btn):
        """Copy the selected photos off the camera into the library."""
        if self._device is None or self._copy_busy():
            return
        selected = self.grid.selected_items()
        records = [it.record for it in selected if hasattr(it, "record")]
        if not records:
            records = self._new_device_records()
        if not records:
            return
        album_id = self._import_album_ids[self._import_album.get_selected()] \
            if self._import_album.get_selected() < len(self._import_album_ids) else None
        delete_after = self._import_delete.get_active()
        device = self._device

        def done(result):
            # Leaving the device view has to reset everything that
            # belonged to it: the selection, the action bar built
            # around "Import", and the sidebar row still highlighting
            # the camera. Without this the library opened with the
            # camera still marked as the current place and an
            # "Import 3 Selected" button acting on photos that had
            # just been imported.
            self._scope, self._album_id = "library", None
            self._device = None
            self._device_records = []
            self.grid.unselect_all()
            self.grid.load("library")
            self.refresh_sidebar()
            self._select_sidebar_key("library")
            self._on_selection_changed(self.grid)
            self._show_toast(self._import_summary(result))
            if delete_after and result.get("sources") and not result.get("cancelled"):
                self._ask_delete_from_device(device, result["sources"])

        self._run_import(records, heading=_("Importing from {device}").format(device=device.name),
                         album_id=album_id, done_cb=done,
                         **self._import_profiles(device))

    def _import_device_records(self, records, album_id=None):
        """Import photos from the camera into the library and, when an album
        is given, file them there.

        Photos already in the library (same content) are not copied again;
        the copy you already have is what goes into the album.
        """
        device = self._device
        if device is None or self._copy_busy():
            return
        album_name = None
        if album_id is not None:
            row = self.catalog.q1("SELECT name FROM albums WHERE id=?", (album_id,))
            album_name = row["name"] if row else None
        already = getattr(self, "_device_imported", set()) or set()
        existing_ids, to_copy = [], []
        for r in records:
            fp = r.get("fingerprint")
            if fp and fp in already:
                hit = self.catalog.q1(
                    "SELECT id FROM photos WHERE fingerprint=? AND trashed_at IS NULL",
                    (fp,))
                if hit:
                    existing_ids.append(hit["id"])
                    continue
            to_copy.append(r)
        if album_id is not None and existing_ids:
            self.catalog.album_add(album_id, existing_ids)

        def done(result):
            self._show_toast(self._import_summary(result, extra=len(existing_ids),
                                                  album_name=album_name))
            self.refresh_sidebar()
            # Stay on the camera: what was just imported moves to
            # "Already Imported".
            if self._device is not None and self._scope == "device":
                self._open_device(self._device.id)

        if not to_copy:
            if album_id is not None:
                self._write_album_sidecar(album_id)
            done({"placed": [], "copied": [], "skipped": 0, "failed": 0,
                  "sources": [], "cancelled": False})
            return
        heading = (_("Importing into “{album}”").format(album=album_name) if album_name
                   else _("Importing from {device}").format(device=device.name))
        self._run_import(to_copy, heading=heading, album_id=album_id, done_cb=done,
                         **self._import_profiles(device))

    # -- files dropped from outside ---------------------------------------
    def _install_file_drop(self, widget):
        """Dropping photos or folders on the photo area imports them - into
        the album being viewed, or into the library."""
        target = Gtk.DropTarget.new(Gdk.FileList.__gtype__, Gdk.DragAction.COPY)

        def on_drop(_t, value, _x, _y):
            paths = self._paths_from_filelist(value)
            if not paths:
                return False
            album_id = self._album_id if self._scope == "album" else None
            self._import_dropped(paths, album_id=album_id)
            return True
        target.connect("drop", on_drop)
        widget.add_controller(target)

    @staticmethod
    def _paths_from_filelist(value):
        try:
            files = value.get_files()
        except Exception:
            return []
        return [f.get_path() for f in files if f is not None and f.get_path()]

    def _import_dropped(self, paths, album_id=None):
        """Copy dropped photos and videos into the library, filed by date,
        and into the album they were dropped on."""
        if self._copy_busy():
            return
        album_name = None
        if album_id is not None:
            row = self.catalog.q1("SELECT name FROM albums WHERE id=?", (album_id,))
            album_name = row["name"] if row else None
        cancel = threading.Event()
        self._copy_cancel = cancel
        # Shown straight away: finding the photos in a big folder takes a moment.
        self._copy_progress(_("Getting the photos ready…"), 0, 0)

        def collect():
            records = devicemod.files_from_paths(paths)

            def start():
                if cancel.is_set():
                    self._copy_finished()
                    self._show_toast(_("Stopped. Nothing was copied."))
                    return False
                if not records:
                    self._copy_finished()
                    self._show_toast(_("There are no photos or videos in what you dropped."))
                    return False

                def done(result):
                    self._show_toast(self._import_summary(result, album_name=album_name))
                    self._refresh()
                heading = (_("Copying into “{album}”").format(album=album_name) if album_name
                           else _("Copying"))
                self._run_import(records, heading=heading, album_id=album_id,
                                 done_cb=done, cancel=cancel, **self._import_profiles())
                return False
            GLib.idle_add(start)
        threading.Thread(target=collect, daemon=True).start()

    def _new_device_records(self):
        already = getattr(self, "_device_imported", set()) or set()
        return [r for r in getattr(self, "_device_records", [])
                if r.get("fingerprint") not in already]

    def _fill_import_albums(self):
        from ..catalog import natural_key
        albums = sorted(self.catalog.albums(), key=lambda a: natural_key(a["name"]))
        self._import_album_ids = [None] + [a["id"] for a in albums]
        self._import_album.set_model(Gtk.StringList.new(
            [_("Library")] + [a["name"] for a in albums]))
        self._import_album.set_selected(0)

    def _ask_delete_from_device(self, device, sources):
        """After import: delete from the camera, or keep."""
        n = len(sources)
        dialog = Adw.AlertDialog(
            heading=ngettext("Delete {count} item from “{device}”?",
                             "Delete {count} items from “{device}”?",
                             n).format(count=n, device=device.name),
            body=_("They are safely in your library now. Deleting them frees up space "
                   "on the camera and can't be undone."))
        dialog.add_response("keep", _("Keep Items"))
        dialog.add_response("delete", _("Delete Items"))
        dialog.set_response_appearance("delete",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("keep")

        def done(_d, response):
            if response != "delete":
                return

            def work():
                removed, failed = devicemod.delete_from_device(sources)
                msg = ngettext("Deleted {count} item from {device}",
                               "Deleted {count} items from {device}",
                               removed).format(count=removed, device=device.name)
                if failed:
                    msg += " " + ngettext("({count} could not be deleted)",
                                          "({count} could not be deleted)",
                                          failed).format(count=failed)
                GLib.idle_add(self._show_toast, msg)
            threading.Thread(target=work, daemon=True).start()
        dialog.connect("response", done)
        dialog.present(self)

    def _show_toast(self, text):
        try:
            self.toasts.add_toast(Adw.Toast(title=text, timeout=5))
        except Exception:
            pass

    @staticmethod
    def _dragged_item(value):
        """("album" | "folder" | "smart", id) from a sidebar row's drag, else None."""
        for kind in ("album", "folder", "smart"):
            prefix = f"pika-{kind}:"
            if value.startswith(prefix):
                try:
                    return kind, int(value[len(prefix):])
                except ValueError:
                    return None
        return None

    def _make_drop_target(self, row, album_id, folder_id, top=False):
        """Accept photos dropped on an album; albums, Smart Albums and folders
        dropped on a folder, or on the Albums heading (``top``) to leave folders."""
        target = Gtk.DropTarget.new(GObject.TYPE_NONE, Gdk.DragAction.COPY
                                    | Gdk.DragAction.MOVE)
        # Photos from Piklin's own grid arrive as text; files from the
        # desktop or a file manager arrive as a file list.
        target.set_gtypes([Gdk.FileList.__gtype__, GObject.TYPE_STRING])

        def on_drop(_t, value, _x, _y):
            if isinstance(value, Gdk.FileList):
                if album_id is None:
                    return False        # a folder holds albums, not photos
                paths = self._paths_from_filelist(value)
                if not paths:
                    return False
                self._import_dropped(paths, album_id=album_id)
                return True
            if not isinstance(value, str):
                return False
            if value.startswith(PhotoGrid.DEVICE_DRAG_PREFIX):
                # Straight from the camera onto an album: import them and
                # file them there, in one gesture.
                if album_id is None:
                    return False
                indexes = [int(i) for i in
                           value[len(PhotoGrid.DEVICE_DRAG_PREFIX):].split(",") if i]
                records = [r for i, r in enumerate(
                    getattr(self, "_device_records", []) or []) if i in set(indexes)]
                if not records:
                    return False
                self._import_device_records(records, album_id=album_id)
                return True
            if value.startswith(PhotoGrid.DRAG_PREFIX):
                if album_id is None:
                    # A folder holds albums, not photos - dropping a
                    # photo on one has no meaning, so refuse it rather
                    # than silently doing nothing surprising.
                    return False
                # Library photos only: a photo still on a device has a
                # negative id and no row, and adding it failed the whole drop.
                ids = [int(i) for i in
                       value[len(PhotoGrid.DRAG_PREFIX):].split(",") if i and int(i) > 0]
                if not ids:
                    return False
                self.catalog.album_add(album_id, ids)
                self._write_album_sidecar(album_id)
                self._refresh()
                return True
            dragged = self._dragged_item(value)
            if dragged is not None:
                if folder_id is None and not top:
                    return False
                return self._move_items([dragged], folder_id)
            return False

        def on_enter(_t, _x, _y):
            row.add_css_class("pika-drop-into")
            return Gdk.DragAction.COPY

        target.connect("drop", on_drop)
        target.connect("enter", on_enter)
        target.connect("leave", lambda *_: row.remove_css_class("pika-drop-into"))
        row.add_controller(target)

    def _sidebar_thumb(self, path):
        """A tiny rounded picture of an album's cover for its sidebar row.
        Shown from the thumbnails already decoded when there is one, else
        fetched and decoded off the UI thread, so the sidebar never waits."""
        from .grid import _cached_texture, _remember_texture
        from .tile import PhotoTile
        from ..thumbs import GRID_SIZE
        tile = PhotoTile(22, radius=5.0)
        tile.add_css_class("pika-sidebar-thumb")
        tile.set_valign(Gtk.Align.CENTER)
        hit = _cached_texture(path)
        if hit is not None:
            tile.set_paintable(hit[1])
            return tile

        def done(thumb):
            if thumb is None:
                return
            try:
                texture = Gdk.Texture.new_from_filename(str(thumb))
            except Exception:
                return
            _remember_texture(path, str(thumb), texture)
            GLib.idle_add(lambda: (tile.set_paintable(texture), False)[1])
        self.thumbs.request(path, GRID_SIZE, done)
        return tile

    def _add_tree_rows(self, nodes, add_fn, depth):
        """Render the folders/albums tree, respecting collapsed state.

        ``add_fn`` is the ``add(...)`` closure built inside
        ``refresh_sidebar`` - passed in rather than duplicated, so this
        stays the single place a sidebar row is actually constructed.
        Folders sort before albums at each level (``Catalog.tree()``
        already orders them that way); a collapsed folder simply has its
        children skipped, not hidden after the fact, so collapsing a
        folder with a thousand albums under it costs nothing.
        """
        for node in nodes:
            indent = depth * 16
            if node["kind"] == "folder":
                folder = node["row"]
                add_fn(f"folder:{folder['id']}", folder["name"],
                      "folder-symbolic", indent=indent,
                      folder_id=folder["id"])
                if folder["id"] not in self._collapsed_folders:
                    self._add_tree_rows(node["children"], add_fn, depth + 1)
            elif node["kind"] == "smart":
                smart = node["row"]
                add_fn(f"smart:{smart['id']}", smart["name"],
                       "folder-saved-search-symbolic",
                       self.catalog.smart_album_count(smart["id"]),
                       indent=indent, smart_id=smart["id"])
            else:
                album = node["row"]
                add_fn(f"album:{album['id']}", album["name"],
                      "folder-pictures-symbolic", album["n"],
                      album_id=album["id"], indent=indent,
                      cover=album["cover_path"])

    def _toggle_folder(self, folder_id):
        """The triangle beside a folder shows or hides what is inside it."""
        if folder_id in self._collapsed_folders:
            self._collapsed_folders.discard(folder_id)
        else:
            self._collapsed_folders.add(folder_id)
        self._save_folded_folders()
        self.refresh_sidebar()

    def _open_folder(self, folder_id):
        """A folder shows its albums and folders as cards, as in any photo
        library; its photos are in the albums."""
        self._scope, self._album_id, self._smart_id = "folder", None, None
        self._folder_id = folder_id
        self.grid.unselect_all()
        # A click shows the folder's albums on screen and leaves the sidebar
        # as it is; a double click (or the triangle) opens it there.
        self._select_sidebar_key(f"folder:{folder_id}")
        if not getattr(self, "_folder_menus", False):
            self._folder_menus = True
            self.folder_view.connect("background-menu",
                                     lambda _f, src, x, y: self._on_background_menu(src, x, y))
            self.folder_view.connect("cards-menu", self._on_cards_menu)
        self.folder_view.load(folder_id)
        self.content_stack.set_visible_child_name("folder")
        self._on_selection_changed(self.grid)

    def _open_album(self, album_id):
        self._scope, self._album_id, self._smart_id = "album", album_id, None
        self._select_sidebar_key(f"album:{album_id}")
        self.grid.load("album", album_id, self.search.get_text() or None)
        self._sync_content_view()
        self._on_selection_changed(self.grid)

    def _open_smart(self, smart_id):
        self._scope, self._album_id, self._smart_id = "smart", None, smart_id
        self._select_sidebar_key(f"smart:{smart_id}")
        self.grid.load("smart", None, self.search.get_text() or None, smart_id=smart_id)
        self._sync_content_view()
        self._on_selection_changed(self.grid)

    def _on_sidebar_activated(self, _list, row):
        if row is None or self._syncing_sidebar:
            return
        folder_id = getattr(row, "_folder_id", None)
        if folder_id is not None:
            self._open_folder(folder_id)

    def _on_sidebar_selected(self, _list, row):
        if row is None or self._syncing_sidebar:
            return
        key = getattr(row, "_key", "library")
        if key.startswith("folder:"):
            self._open_folder(row._folder_id)
            return
        if key.startswith("device:"):
            self._open_device(key[len("device:"):])
            return
        self._smart_id = None
        if key.startswith("album:"):
            self._scope = "album"
            self._album_id = row._album_id
        elif key.startswith("smart:"):
            self._scope = "smart"
            self._album_id = None
            self._smart_id = row._smart_id
        else:
            self._scope = key
            self._album_id = None
        self.grid.load(self._scope, self._album_id,
                       self.search.get_text() or None,
                       smart_id=self._smart_id)
        self._sync_content_view()
        # The action bar's meaning depends on where you are, not only on
        # what is selected, so it has to be refreshed on navigation too.
        self._on_selection_changed(self.grid)

    # ==================================================================
    # actions
    # ==================================================================
    def _install_shortcuts(self):
        actions = {
            "add-folder": lambda *_: self._on_add_folder(),
            "open-library": lambda *_: self._on_open_library(),
            "rename-photo": lambda *_: self._on_rename_photo(),
            "new-album": lambda *_: self._on_new_album(),
            "new-folder": lambda *_: self._on_new_folder(),
            "rescan": lambda *_: self._start_scan(None),
            "preferences": lambda *_: self._open_settings("general"),
            "storage": lambda *_: self._open_settings("storage"),
            "remotes": lambda *_: self._open_settings("remotes"),
            "about": lambda *_: self._on_about(),
            "help": lambda *_: self._on_help(),
            "activity-log": lambda *_: self._on_activity_log(),
            "select-all": lambda *_: self.grid.select_all(),
            "escape": lambda *_: self._on_escape(),
            "favorite": lambda *_: self._on_bulk_favorite(None),
            "delete": lambda *_: self._on_bulk_trash(None),
            "undo": lambda *_: self._active_editor().undo(),
            "redo": lambda *_: self._active_editor().redo(),
            "export": lambda *_: self._on_bulk_export(None),
        }
        actions.update({
            "new-smart-album": lambda *_: self._on_new_smart_album(),
            "fullscreen": lambda *_: self._on_fullscreen(),
            "view-year": lambda *_: self._set_view("year"),
            "view-month": lambda *_: self._set_view("month"),
            "view-day": lambda *_: self._set_view("day"),
            "view-all": lambda *_: self._set_view("none"),
            "find": lambda *_: self._focus_search(),
            "deselect": lambda *_: self.grid.unselect_all(),
            "hide": lambda *_: self._on_bulk_hide(),
            "info": lambda *_: self.viewer.toggle_info(),
            "zoom-in": lambda *_: self._step_zoom(+1),
            "zoom-out": lambda *_: self._step_zoom(-1),
            "check-updates": lambda *_: self._check_updates(quiet=False),
            "rotate-cw": lambda *_: self._on_rotate(1),
            "rotate-ccw": lambda *_: self._on_rotate(-1),
        })
        for name, cb in actions.items():
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", cb)
            self.add_action(act)

        sort = Gio.SimpleAction.new("sort", GLib.VariantType.new("s"))
        sort.connect("activate",
                     lambda a, p: self.grid.load(self._scope, self._album_id,
                                                 self.search.get_text() or None,
                                                 p.get_string()))
        self.add_action(sort)

        app = self.get_application()
        # These use a modifier, so they can never collide with typing a
        # letter into a field: safe as real, global accelerators.
        # Escape is deliberately not an accelerator: accelerators run in
        # capture phase, before a dialog sees the key, so Escape could not
        # cancel "New Album", "Rename" or Export - it only deselected photos
        # behind the dialog. It is handled in bubble phase below instead.
        for accel, action in (
                              ("<Primary>z", "win.undo"),
                              ("<Primary><Shift>z", "win.redo"),
                              ("<Primary>e", "win.export"),
                              ("<Primary>comma", "win.preferences"),
                              ("<Primary>o", "win.add-folder"),
                              # Rotating is everyday; looking for new photos
                              # is rare, and F5 is where people expect it.
                              ("F5", "win.rescan"),
                              ("F1", "win.help"),
                              ("<Primary>r", "win.rotate-cw"),
                              ("<Primary><Shift>r", "win.rotate-ccw"),
                              # One key for each view.
                              ("<Primary>1", "win.view-year"),
                              ("<Primary>2", "win.view-month"),
                              ("<Primary>3", "win.view-day"),
                              ("<Primary>4", "win.view-all"),
                              ("<Primary>f", "win.find"),
                              ("<Primary>n", "win.new-album"),
                              ("<Primary><Shift>n", "win.new-folder"),
                              ("<Primary><Alt>n", "win.new-smart-album"),
                              ("F11", "win.fullscreen"),
                              ("<Primary><Shift>f", "win.fullscreen"),
                              ("<Primary><Shift>a", "win.deselect"),
                              ("<Primary>l", "win.hide"),
                              ("<Primary>i", "win.info"),
                              # F2 renames, as in every Linux file manager.
                              ("F2", "win.rename-photo")):
            app.set_accels_for_action(action, [accel])
        if IS_MAC:
            # Control-Command-F is full screen in every Mac app.
            app.set_accels_for_action("win.fullscreen",
                                      ["<Primary><Shift>f", "<Control><Meta>f"])
        app.set_accels_for_action("win.zoom-in",
                                  ["<Primary>plus", "<Primary>equal", "<Primary>KP_Add"])
        app.set_accels_for_action("win.zoom-out",
                                  ["<Primary>minus", "<Primary>KP_Subtract"])

        # "f" (favourite), Delete (trash) and Ctrl+A (select all) are
        # genuinely ambiguous: the same keys must type the letter f,
        # delete a character, and select text while the album-name field,
        # the search box, or any other entry has focus.
        #
        # GtkApplication's set_accels_for_action installs its shortcuts
        # at the CAPTURE phase, which fires before the event ever reaches
        # the focused widget - that is what was stealing every "f" and
        # Delete keystroke out of every text field in the app, including
        # the search box and the New Album dialog's name entry.
        #
        # A Gtk.EventControllerKey in the default BUBBLE phase, attached
        # to the window, is different: bubble phase runs *after* the
        # focus widget's own controllers, so when a Gtk.Entry or
        # Gtk.SearchEntry has focus it consumes the keystroke as text
        # first and this handler never sees it. It only fires when no
        # editable widget was focused to begin with - e.g. the photo grid.
        key_controller = Gtk.EventControllerKey()
        key_controller.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
        key_controller.connect("key-pressed", self._on_grid_key)
        key_controller.connect("key-released", self._on_key_released)
        self.add_controller(key_controller)

        # Space, Return and Delete are also the keys a focused list row or
        # button uses to activate itself, so in bubble phase they never
        # arrived: clicking a photo leaves focus on the list's internal
        # row, which swallowed Space and Return. These three are taken in
        # capture phase instead - but only when nobody is typing and no
        # dialog or menu is open, so text fields and dialog buttons still
        # get them.
        capture = Gtk.EventControllerKey()
        capture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        capture.connect("key-pressed", self._on_capture_key)
        self.add_controller(capture)

    def _on_grid_key(self, _controller, keyval, _keycode, state):
        ctrl = bool(state & PRIMARY_MASK)
        name = key_name(keyval)
        page = self.stack.get_visible_child_name()
        if name == "Escape":
            self._on_escape()
            return True
        # Hold M in the editor to see the original.
        if page == "video-editor":
            ed = self.video_editor
            key = name.lower()
            if ctrl:
                return False
            if name == "space":
                ed.player.toggle()
            elif key == "k":
                ed.player.pause()
            elif name in ("comma", "period"):
                ed.player.step(-1 if name == "comma" else 1)
            elif key in ("j", "l"):
                ed.player.skip(-5.0 if key == "j" else 5.0)
            elif key == "i":
                ed.set_start_here()
            elif key == "o":
                ed.set_end_here()
            elif name == "bracketleft":
                ed.mark_in()
            elif name == "bracketright":
                ed.mark_out()
            elif key == "x":
                ed.cut_selection()
            elif name in ("Return", "KP_Enter"):
                ed.close()
            else:
                return False
            return True
        if page == "editor":
            if not ctrl and name.lower() == "m":
                self.editor.hold_compare(True)
                return True
            if name in ("Return", "KP_Enter") and not ctrl:
                self._on_editor_closed(None)
                return True
            return False
        if page == "viewer":
            item = self.viewer.item
            if item is not None and getattr(item, "is_video", False):
                # A video: the keys every player uses.
                player = self.viewer.player
                key = name.lower()
                if ctrl:
                    return False
                if name == "space" or key == "k":
                    if key == "k":
                        player.pause()
                    else:
                        player.toggle()
                    return True
                if name in ("comma", "period"):
                    player.step(-1 if name == "comma" else 1)
                    return True
                if key in ("j", "l"):
                    player.skip(-5.0 if key == "j" else 5.0)
                    return True
                if key == "m":
                    player.toggle_mute()
                    return True
                if key == "f":
                    self.viewer.fav_btn.set_active(not self.viewer.fav_btn.get_active())
                    return True
                if name in ("Return", "KP_Enter"):
                    self._on_edit_requested(self.viewer)   # Return edits, as for photos
                    return True
                return False
            if name == "space":
                self._show("grid")
                return True
            if name in ("Return", "KP_Enter"):
                self._on_edit_requested(self.viewer)
                return True
            if not ctrl and name in ("period", "f", "F"):
                self.viewer.fav_btn.set_active(not self.viewer.fav_btn.get_active())
                return True
            return False
        # "." marks a favourite; F works as well.
        if not ctrl and (name == "period" or name.lower() == "f"):
            self._on_bulk_favorite(None)
            return True
        if name == "space" and not ctrl:
            items = self.grid.selected_items()
            if items:
                self._on_photo_activated(self.grid, items[0])
                return True
            return False
        if name in ("Return", "KP_Enter") and not ctrl:
            items = self.grid.selected_items()
            if len(items) == 1:
                self.viewer.show_photo(items[0])
                self._on_edit_requested(self.viewer)
                return True
            return False
        # Command-delete is also how a Mac moves things to the bin.
        if name == "Delete" and (not ctrl or IS_MAC):
            self._on_bulk_trash(None)
            return True
        if ctrl and name.lower() == "a":
            self.grid.select_all()
            return True
        return False

    _CAPTURED = {"space", "Return", "KP_Enter", "Delete", "KP_Delete", "F11"}

    def _focus_takes_keys(self) -> bool:
        """True while typing in a field or working in a dialog or menu:
        those keep their own meaning for every key."""
        widget = self.get_focus()
        while widget is not None:
            if isinstance(widget, (Gtk.Editable, Gtk.TextView, Adw.Dialog,
                                   Gtk.Popover)):
                return True
            widget = widget.get_parent()
        return False

    def _on_capture_key(self, controller, keyval, keycode, state):
        name = key_name(keyval)
        mods = state & (Gdk.ModifierType.CONTROL_MASK | PRIMARY_MASK
                        | Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.ALT_MASK)
        # Ctrl+A (Command-A on a Mac) selects every photo shown. Taken in
        # capture phase: in bubble phase a focused photo tile or sidebar row
        # used it for its own "select all" first, and the grid never got it.
        if name.lower() == "a" and mods == PRIMARY_MASK:
            if (self._focus_takes_keys()
                    or self.stack.get_visible_child_name() != "grid"
                    or self.content_stack.get_visible_child_name() != "grid"):
                return False
            self.grid.select_all()
            return True
        # Left and Right move between photos in the viewer. Taken here, in
        # capture phase: otherwise the focused toolbar button used the arrow
        # to move focus to its neighbour and the photo never changed.
        if (name in ("Left", "Right", "KP_Left", "KP_Right") and not mods
                and self.stack.get_visible_child_name() == "viewer"
                and not self._focus_takes_keys()):
            self._on_navigate(self.viewer, -1 if name.endswith("Left") else 1)
            return True
        if name not in self._CAPTURED:
            return False
        if name == "F11":
            # Registered as an accelerator too, but F11 never reached it on
            # this desktop; Ctrl+Shift+F did. Taken here directly.
            self._on_fullscreen()
            return True
        if state & (Gdk.ModifierType.CONTROL_MASK | PRIMARY_MASK | Gdk.ModifierType.ALT_MASK) \
                and not (IS_MAC and name == "Delete" and not state & Gdk.ModifierType.ALT_MASK):
            return False
        if self._focus_takes_keys():
            return False
        page = self.stack.get_visible_child_name()
        if page == "grid":
            if not self.grid.selected_ids():
                return False
            if name in ("Delete", "KP_Delete"):
                self._on_bulk_trash(None)
                return True
            return self._on_grid_key(controller, keyval, keycode, state)
        if page in ("viewer", "editor", "video-editor") and name != "Delete":
            return self._on_grid_key(controller, keyval, keycode, state)
        return False

    def _on_key_released(self, _controller, keyval, _keycode, _state):
        if (Gdk.keyval_name(keyval) or "").lower() == "m" and \
                self.stack.get_visible_child_name() == "editor":
            self.editor.hold_compare(False)

    def _set_view(self, key):
        btn = self._view_buttons.get(key)
        if btn is not None and self.stack.get_visible_child_name() == "grid":
            btn.set_active(True)

    def _focus_search(self):
        if self.stack.get_visible_child_name() != "grid":
            self._show("grid")
        self.search.grab_focus()

    def _step_zoom(self, direction):
        adj = self.zoom_scale.get_adjustment()
        step = 30 * direction
        self.zoom_scale.set_value(max(adj.get_lower(),
                                      min(adj.get_upper(), adj.get_value() + step)))

    def _on_bulk_hide(self):
        """Ctrl+L: hide the selection, or bring it back when standing in
        Hidden - the same key both ways."""
        ids = self.grid.selected_ids()
        if not ids and self.stack.get_visible_child_name() == "viewer" \
                and self.viewer.item is not None:
            ids = [self.viewer.item.id]
        if not ids:
            return
        self.catalog.set_hidden(ids, self._scope != "hidden")
        self._mirror_state()
        if self.stack.get_visible_child_name() == "viewer":
            self._show("grid")
        self._refresh()

    def _on_trash_button(self, _btn):
        # In Recently Deleted this button is "Recover"; the Delete key
        # there asks to delete for good instead.
        ids = self.grid.selected_ids()
        if self._scope == "trash":
            ids = ids or [r["id"] for r in self.catalog.q(
                "SELECT id FROM photos WHERE trashed_at IS NOT NULL")]
            if ids:
                self.catalog.untrash(ids)
                self._mirror_state()
                self._refresh()
            return
        self._on_bulk_trash(None)

    def _on_fullscreen(self):
        """Full screen shows one photo on black. From the grid it opens the
        selected photo that way."""
        page = self.stack.get_visible_child_name()
        if page == "grid":
            items = self.grid.selected_items()
            if not items:
                return
            self._on_photo_activated(self.grid, items[0])
            page = "viewer"
        if page == "viewer":
            self.viewer.toggle_fullscreen()

    def _on_escape(self):
        if self.stack.get_visible_child_name() == "viewer" \
                and self.viewer.is_fullscreen():
            self.viewer.toggle_fullscreen(False)
            return
        name = self.stack.get_visible_child_name()
        if name == "editor":
            self._on_editor_closed(None)
        elif name == "video-editor":
            self.video_editor.close()
        elif name == "viewer":
            self._show("grid")
        else:
            self.grid.unselect_all()

    def _show(self, name):
        self.stack.set_visible_child_name(name)
        if name == "grid":
            self._refresh()

    def _mirror_state(self):
        """Keep the readable mirrors in step with the database.

        Called after anything that changes state which lives only in
        catalog.db - marks, the folder tree, the watched folders - so
        that "delete catalog.db and rebuild" stays a true statement
        rather than a promise the README makes and the code breaks.
        """
        try:
            sidecars.write_all(self.library, self.catalog)
        except Exception:
            pass
        self._backup_soon()

    def _refresh(self):
        if self._scope == "folder":
            self.folder_view.load(getattr(self, "_folder_id", None))
        else:
            self.grid.refresh()
        if self.content_stack.get_visible_child_name() == "summary":
            self.summary.load(self.settings.get("group_by"), self.grid.filters())
        self.refresh_sidebar()
        self._mirror_state()

    # -- photo flow ------------------------------------------------------
    def _on_photo_activated(self, _grid, item):
        record = getattr(item, "record", None) or {}
        if record.get("camera") and not Path(item.path).exists():
            # Still on a Mac's camera or phone: copy this one off to open it.
            def work():
                ok = devicemod.imagecapture.fetch(item.path)
                GLib.idle_add(lambda: (self._open_fetched(item) if ok else self._show_toast(
                    _("Couldn't read this photo from {device}.").format(
                        device=getattr(self._device, "name", ""))), False)[1])
            threading.Thread(target=work, daemon=True).start()
            return
        self.viewer.show_photo(item)
        self._show("viewer")

    def _open_fetched(self, item):
        self.viewer.show_photo(item)
        self._show("viewer")

    def _on_navigate(self, _viewer, direction):
        if self.viewer.item is None:
            return
        idx = self.grid.index_of(self.viewer.item)
        if idx < 0:
            return
        nxt = idx + direction
        if 0 <= nxt < self.grid.count():
            self.viewer.show_photo(self.grid.item_at(nxt))

    def _on_edit_requested(self, _viewer):
        item = self.viewer.item
        if item is None:
            return
        if getattr(item, "is_video", False):
            self.viewer.player.unload()
            self.video_editor.open(item.path, item.id, item)
            self._show("video-editor")
            return
        self.editor.open(item.path, item.id)
        self._show("editor")

    def _active_editor(self):
        if self.stack.get_visible_child_name() == "video-editor":
            return self.video_editor
        return self.editor

    def _on_frame_saved(self, _editor, path):
        """A video frame saved as a photo joins the library at once."""
        self._start_scan([str(Path(path).parent)])
        self.toasts.add_toast(Adw.Toast(title=_("Frame saved to your library"),
                                        timeout=3))

    # -- updates ------------------------------------------------------------
    def _check_updates(self, quiet=True, on_open=False):
        """Ask whether a newer Piklin is out. Nothing is ever installed without
        asking: some people prefer to stay on the version they have.

        on_open: Piklin just opened - look now, and ask with the update window.
        Quiet (every hour while open): only show Update Available.
        Otherwise (Check for Updates…): open the window, or say it is up to date."""
        from .. import updates
        from ..app import VERSION
        if quiet and not updates.enabled():
            return
        if quiet and not on_open and not updates.check_due():
            return

        def work():
            try:
                release = updates.latest_release(VERSION)
                error = None
            except Exception as exc:
                release, error = None, exc
            updates.save_state(last_check=time.time())
            GLib.idle_add(show, release, error)

        def show(release, error):
            if release is not None and updates.update_available(release, VERSION):
                self._ring_bell(release)
                if (on_open or not quiet) and not getattr(self, "_updating", False):
                    self._open_update_dialog()
            elif not quiet:
                if error is not None:
                    self._show_toast(_("Couldn't check for updates. Check your "
                                       "internet connection and try again."))
                else:
                    self._show_toast(_("Piklin is up to date ({version}).").format(version=VERSION))
            return False
        threading.Thread(target=work, daemon=True).start()

    def _ring_bell(self, release):
        """Show Update Available for this release - only ever when there is
        one - and ring its bell."""
        bell = self._update_bell
        self._bell_release = release
        bell.set_visible(True)
        bell.remove_css_class("ringing")
        GLib.idle_add(lambda: (bell.add_css_class("ringing"), False)[1])
        GLib.timeout_add(4500, lambda: (bell.remove_css_class("ringing"), False)[1])

    def _open_update_dialog(self):
        """The update window, from Update Available or when Piklin opens."""
        from .. import updates
        from ..app import VERSION
        from .update_dialog import UpdateDialog
        release = self._bell_release
        if release is None:
            return
        current = getattr(self, "_update_dialog", None)
        if current is not None:
            current.present(self)
            return
        dialog = UpdateDialog(release, VERSION, updates.installed_build(),
                              lambda: self._install_update(release, dialog))
        if getattr(self, "_updating", False):
            dialog.show_installing()
        dialog.connect("closed", lambda *_: setattr(self, "_update_dialog", None))
        self._update_dialog = dialog
        dialog.present(self)

    def _install_update(self, release, dialog):
        """Install the update the person chose, showing how it goes in the
        update window, then reopen Piklin in the new version."""
        from .. import logs, updates
        if getattr(self, "_updating", False):
            return
        self._updating = True
        dialog.show_installing()

        def work():
            try:
                updates.install(release.version)
                error = None
            except updates.UpdateError as exc:
                error = exc
            GLib.idle_add(done, error)

        def done(error):
            self._updating = False
            if error is None:
                logs.get("update").info("Installed %s", release.version)
                self._update_bell.set_visible(False)
                dialog.show_installed()
                self._reopen_when_idle()
            elif error.kind == "not-newer":
                logs.get("update").info("%s is already installed", release.version)
                self._update_bell.set_visible(False)
                dialog.force_close()
            elif error.kind == "cancelled":
                logs.get("update").info("Install of %s cancelled", release.version)
                dialog.show_offer()
            else:
                logs.get("update").warning("Install of %s: %s (%s)", release.version,
                                           error.kind, error)
                dialog.show_failed()
            return False
        threading.Thread(target=work, daemon=True).start()

    def _reopen_when_idle(self):
        """Reopen in the new version once nothing would be cut short: no
        photos copying, no backup running, no editor or dialog open."""
        def busy():
            backup = getattr(self, "autobackup", None)
            return (getattr(self, "_copy_cancel", None) is not None
                    or self.stack.get_visible_child_name() in ("editor", "video-editor")
                    or self.get_visible_dialog() is not None
                    or (backup is not None and backup._running))

        def attempt():
            if busy():
                return GLib.SOURCE_CONTINUE
            app = self.get_application()
            app.relaunch_update = True
            app.relaunch_library = str(self.library.root)
            app.quit()
            return GLib.SOURCE_REMOVE
        GLib.timeout_add_seconds(4, attempt)

    def _on_rotate(self, turns, ids=None):
        """A quarter turn without opening the editor. Nothing is lost: it is
        an ordinary edit, undone by turning back or by Revert."""
        from .. import quick_edit
        page = self.stack.get_visible_child_name()
        if page == "viewer" and self.viewer.item is not None and ids is None:
            items = [self.viewer.item]
        elif page == "grid" and self._scope not in ("device", "trash"):
            by_id = getattr(self.grid, "_by_id", {})
            items = ([by_id[i] for i in ids if i in by_id] if ids
                     else self.grid.selected_items())
        else:
            return
        items = [it for it in items if getattr(it, "id", -1) is not None
                 and getattr(it, "id", -1) >= 0]
        if not items:
            self._show_toast(_("Select the photos to rotate."))
            return
        # Each photo's tile turns the moment its edit is saved, from the
        # thumbnail it already had; the exact render follows in the
        # background. A lock keeps quick repeated presses in order.
        thumbs, grid = self.thumbs, self.grid
        if not hasattr(self, "_rotate_lock"):
            self._rotate_lock = threading.Lock()

        def work():
            turned = []
            with self._rotate_lock:
                for it in items:
                    before = thumbs.existing(it.path)
                    try:
                        quick_edit.rotate(self.library, self.catalog, it.id, it.path, turns,
                                          is_video=bool(getattr(it, "is_video", False)),
                                          duration=float(getattr(it, "duration", 0) or 0))
                    except Exception:
                        continue
                    rough = thumbs.turn_cached(it.path, before, turns)
                    turned.append((it, rough))
                    GLib.idle_add(grid.repaint_items, [it.id])
                GLib.idle_add(finished, [it for it, _rough in turned])
                for it, rough in turned:
                    for size in rough:
                        thumbs.generate(it.path, size, force=True)
                    if rough:
                        GLib.idle_add(grid.repaint_items, [it.id])

        def finished(done):
            for it in done:
                row = self.catalog.photo(it.id)
                if row is not None:
                    it.edited = bool(row["edit_version"])
            if page == "viewer" and self.viewer.item is not None:
                self.viewer.show_photo(self.viewer.item)
            self.refresh_sidebar()
            return False
        threading.Thread(target=work, daemon=True).start()

    def _on_editor_closed(self, _editor):
        # Whatever way the editor was left - Back, Esc, Return - the last
        # change is saved before the photo is shown again.
        for ed in (self.editor, getattr(self, "video_editor", None)):
            flush = getattr(ed, "flush", None)
            if flush is not None:
                try:
                    flush()
                except Exception:
                    pass
        if self.viewer.item is not None:
            self.viewer.show_photo(self.viewer.item)
            self._show("viewer")
        else:
            self._show("grid")
        self._refresh()

    def _on_selection_changed(self, _grid):
        n = len(self.grid.selected_ids())
        # Recently Deleted always offers its two actions - on the selection,
        # or on everything when nothing is selected (Recover All / Delete All).
        self.action_bar.set_revealed(n > 0 or self._scope == "trash")
        self.select_label.set_text(_("{count} selected").format(count=n) if n else "")

        # In Recently Deleted the same icons would mean the wrong things:
        # the trash button recovers rather than deletes, and permanent
        # deletion is only offered there.
        on_device = self._scope == "device"
        # A photo still on a camera is not in the library yet, so
        # favouriting, filing or trashing it has nothing to act on -
        # importing is the only thing that makes sense here.
        self._import_btn.set_visible(on_device)
        for w in (self._import_album, self._import_album_label,
                  self._import_delete):
            w.set_visible(on_device)
        if on_device:
            n_new = len(self._new_device_records())
            self._import_btn.set_label(
                _("Import {count} Selected").format(count=n) if n else
                (_("Import All New Items ({count})").format(count=n_new) if n_new
                 else _("All Items Imported")))
            self._import_btn.set_sensitive(bool(n or n_new))
        for key in ("favorite", "trash", "album", "rotate-ccw", "rotate-cw"):
            self._bar_buttons[key].set_visible(not on_device)
        if on_device:
            self.action_bar.set_revealed(True)

        in_trash = self._scope == "trash"
        self._purge_btn.set_visible(in_trash and not on_device)
        if in_trash:
            none = not self.grid.selected_ids()
            self._purge_btn.set_label(_("Delete All…") if none else _("Delete Permanently…"))
        in_dupes = self._scope == "duplicates"
        self._merge_btn.set_visible(in_dupes and not on_device)
        if in_dupes:
            # Say exactly what goes: the copies beyond one in each group.
            extra = self._extra_copies(self.grid.selected_ids())
            if not self.grid.selected_ids():
                self._merge_btn.set_label(_("Keep One of Each"))
                self._merge_btn.set_sensitive(True)
            else:
                self._merge_btn.set_label(ngettext(
                    "Remove {count} Extra Copy", "Remove {count} Extra Copies",
                    extra).format(count=extra))
                self._merge_btn.set_sensitive(extra > 0)
            self.action_bar.set_revealed(True)
        trash_btn = self._bar_buttons["trash"]
        trash_btn.set_icon_name("edit-undo-symbolic" if in_trash
                                else "user-trash-symbolic")
        trash_btn.set_tooltip_text(
            (_("Recover All") if not self.grid.selected_ids() else _("Recover")) if in_trash
            else (_("Remove from this album") if self._scope == "album"
                  else _("Move to Recently Deleted")))
        for key in ("favorite", "album", "rotate-ccw", "rotate-cw"):
            self._bar_buttons[key].set_sensitive(not in_trash)

    def _extra_copies(self, ids) -> int:
        """How many copies Keep One would move away: all but one per group."""
        groups: dict = {}
        by_id = getattr(self.grid, "_by_id", {})
        for pid in ids:
            fp = getattr(by_id.get(pid), "fingerprint", None)
            if fp:
                groups[fp] = groups.get(fp, 0) + 1
        return sum(n - 1 for n in groups.values() if n > 1)

    def _on_merge_duplicates(self, _btn):
        ids = self.grid.selected_ids()
        if not ids:
            # nothing selected: every group of copies
            from ..catalog import DUPLICATE_SQL
            ids = [r["id"] for r in self.catalog.q(
                f"SELECT p.id FROM photos p WHERE p.trashed_at IS NULL AND p.hidden=0 "
                f"AND p.paired_to IS NULL AND {DUPLICATE_SQL}")]
        if not ids:
            return
        result = self.catalog.merge_duplicates(ids)
        self._mirror_state()
        self._refresh()
        n = len(result["trashed"])
        if n:
            self._show_toast(ngettext(
                "Kept one. {count} extra copy moved to Recently Deleted.",
                "Kept one. {count} extra copies moved to Recently Deleted.",
                n).format(count=n))
        else:
            self._show_toast(_("Select at least two copies of the same photo."))

    def _on_purge(self, _btn):
        """Empty selected items out of Recently Deleted.

        Piklin references photos where they live rather than owning
        them, so "delete permanently" has two genuinely different
        meanings and guessing is not acceptable: forgetting the photo
        leaves the user's file untouched, while deleting the file is
        irreversible. Both are offered explicitly.
        """
        ids = self.grid.selected_ids()
        if not ids and self._scope == "trash":
            ids = [r["id"] for r in self.catalog.q(
                "SELECT id FROM photos WHERE trashed_at IS NOT NULL")]
        if not ids:
            return
        rows = [self.catalog.photo(i) for i in ids]
        paths = [r["path"] for r in rows if r]
        dialog = Adw.AlertDialog(
            heading=ngettext("Delete {count} photo permanently?",
                             "Delete {count} photos permanently?",
                             len(ids)).format(count=len(ids)),
            body=_("These photos are in a folder on your computer.\n\n“Remove from "
                   "Library” only takes them out of Piklin. The files stay where they "
                   "are.\n\n“Delete Files” deletes the files from your computer. This "
                   "can't be undone."))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("forget", _("Remove from Library"))
        dialog.add_response("erase", _("Delete Files"))
        dialog.set_response_appearance("erase",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")

        def done(d, response):
            if response not in ("forget", "erase"):
                return
            if response == "erase":
                import os as _os
                for path in paths:
                    try:
                        _os.remove(path)
                    except OSError:
                        pass
            # Remembered as removed either way: with "Remove from Library"
            # the file is still in its folder, and the next scan must not
            # bring it back as a new photo.
            self.catalog.forget_photos(ids)
            self._mirror_state()
            self._refresh()
        dialog.connect("response", done)
        dialog.present(self)

    def _on_view_changed(self, button, key):
        """Regroup the library by year, month, day, or not at all."""
        if not button.get_active():
            return
        self.settings.set("group_by", key)
        self.grid.refresh()
        self._sync_content_view()

    def _summary_applies(self) -> bool:
        return (self._scope == "library"
                and self.settings.get("group_by", "day") in ("year", "month")
                and not (self.search.get_text() or "").strip())

    def _sync_content_view(self):
        if self._scope == "folder":
            self.content_stack.set_visible_child_name("folder")
            return
        if self._summary_applies():
            self.summary.load(self.settings.get("group_by"), self.grid.filters())
            self.content_stack.set_visible_child_name("summary")
        else:
            self.content_stack.set_visible_child_name("grid")

    def _on_summary_open(self, _view, mode, key, first, last):
        """A year card opens Months at that year; a month card opens Days at
        that month - one level deeper."""
        if mode == "year":
            self._view_buttons["month"].set_active(True)
            self.summary.scroll_to_key(key)
        else:
            self._view_buttons["day"].set_active(True)
            self.grid.scroll_to_time(last)

    def _on_search(self, entry):
        self.grid.load(self._scope, self._album_id, entry.get_text() or None,
                       smart_id=self._smart_id)
        self._sync_content_view()

    def _on_filter_toggled(self, _chk):
        if getattr(self, "_filters_syncing", False):
            return
        active = {k for k, c in self._filter_checks.items() if c.get_active()}
        self.filter_btn.set_label(_("Filter ({count})").format(count=len(active))
                                  if active else _("Filter"))
        self.grid.set_filters(active)
        self._sync_content_view()

    def _clear_filters(self):
        self._filters_syncing = True
        for chk in self._filter_checks.values():
            chk.set_active(False)
        self._filters_syncing = False
        self.filter_btn.set_label(_("Filter"))
        self.grid.set_filters(set())
        self._sync_content_view()
        self.filter_btn.popdown()

    def _on_aspect_toggled(self, btn):
        original = btn.get_active()
        self.settings.set("grid_aspect", "original" if original else "square")
        self.grid.set_original_aspect(original)

    def _on_zoom(self, scale):
        px = int(scale.get_value())
        self.settings.set("grid_size", px)
        self.grid.set_tile_size(px)

    # -- bulk actions ----------------------------------------------------
    def _on_bulk_favorite(self, _btn):
        ids = self.grid.selected_ids()
        if not ids:
            return
        rows = [self.catalog.photo(i) for i in ids]
        make_fav = not all(r and r["favorite"] for r in rows if r)
        self.catalog.set_favorite(ids, make_fav)
        self._refresh()

    def _on_bulk_trash(self, _btn):
        """Delete, meaning whatever "delete" means where you are standing.

        Inside an album, removing a photo takes it out of *that album*
        and leaves it in the library - which is what every photo manager
        does, and what people expect: an album is a view onto photos, not
        a place the photo is stored. Only in a library-wide view does
        delete move the photo to the trash. Previously this always
        trashed the photo library-wide, so tidying one album quietly
        removed photos from everywhere.
        """
        ids = self.grid.selected_ids()
        if not ids:
            return
        if self._scope == "trash":
            self._on_purge(None)
            return
        elif self._scope == "album" and self._album_id is not None:
            self.catalog.album_remove(self._album_id, ids)
            self._write_album_sidecar(self._album_id)
        else:
            self.catalog.trash(ids)
        self._refresh()

    def _on_bulk_export(self, _btn):
        items = self.grid.selected_items()
        if not items and self.stack.get_visible_child_name() == "viewer" \
                and self.viewer.item:
            items = [self.viewer.item]
        if not items:
            return
        from .export_dialog import ExportDialog
        ExportDialog(self, self.library, self.settings,
                     [i.path for i in items], catalog=self.catalog).present(self)

    def _on_bulk_album(self, _btn):
        ids = self.grid.selected_ids()
        if not ids:
            return
        from ..catalog import natural_key
        albums = sorted(self.catalog.albums(), key=lambda a: natural_key(a["name"]))
        dialog = Adw.AlertDialog(heading=_("Add to Album"),
                                 body=ngettext("{count} photo", "{count} photos",
                                               len(ids)).format(count=len(ids)))
        names = [a["name"] for a in albums]
        combo = Gtk.DropDown.new_from_strings(names + [_("New Album…")])
        dialog.set_extra_child(combo)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("add", _("Add"))
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)

        def done(d, response):
            if response != "add":
                return
            sel = combo.get_selected()
            if sel >= len(albums):
                self._on_new_album(ids)
            else:
                self.catalog.album_add(albums[sel]["id"], ids)
                self._write_album_sidecar(albums[sel]["id"])
                self._refresh()
        dialog.connect("response", done)
        dialog.present(self)

    def _on_sidebar_right_click(self, gesture, _n_press, x, y, album_id,
                                folder_id, name):
        """Rename / move / delete menu for a folder or album row."""
        if folder_id is not None:
            parent = self.catalog.q1(
                "SELECT p.id, p.name, p.parent_id FROM folders f "
                "JOIN folders p ON p.id=f.parent_id WHERE f.id=?", (folder_id,))
            sections = [
                [("rename", _("Rename…"), None,
                  lambda: self._on_rename_folder(folder_id, name))],
                [("new-album", _("New Album Here…"), None,
                  lambda: self._on_new_album(folder_id=folder_id)),
                 ("new-folder", _("New Folder Here…"), None,
                  lambda: self._on_new_folder(folder_id))],
                [("move-to", _("Move To…"), None,
                  lambda: self._choose_move_target([("folder", folder_id, name)]))]
                + ([("move-out", _("Move Out of “{folder}”").format(folder=parent["name"]), None,
                     lambda: self._move_out(folder_id=folder_id,
                                            to=parent["parent_id"]))]
                   if parent is not None else []),
                [("delete", _("Delete Folder…"), None,
                  lambda: self._on_delete_folder(folder_id, name))],
            ]
        else:
            holder = self.catalog.q1(
                "SELECT f.id, f.name, f.parent_id FROM albums a "
                "JOIN folders f ON f.id=a.folder_id WHERE a.id=?", (album_id,))
            sections = [
                [("rename", _("Rename…"), None,
                  lambda: self._on_rename_album(album_id, name))],
                [("move-to", _("Move To…"), None,
                  lambda: self._choose_move_target([("album", album_id, name)]))]
                + ([("move-out", _("Move Out of “{folder}”").format(folder=holder["name"]), None,
                     lambda: self._move_out(album_id=album_id,
                                            to=holder["parent_id"]))]
                   if holder is not None else []),
                [("delete", _("Delete Album…"), None,
                  lambda: self._on_delete_album(album_id, name))],
            ]
        self._show_context_menu(gesture.get_widget(), x, y, sections)

    def _show_context_menu(self, source, x, y, sections):
        """Open a right-click menu with its top-left corner at the pointer.

        ``sections`` is a list of item lists; each item is
        (key, label, accelerator or None, callback). Empty sections are
        skipped and the rest are divided by separators.

        The menu hangs from the window's content, not from the row or photo that was
        clicked: those are recycled and re-laid-out as the selection
        changes (a photo grid reuses its tiles, the sidebar rebuilds its
        rows), and a popover attached to one of them was placed wherever
        that widget had moved to - often nowhere near the click.
        """
        old = getattr(self, "_context_popover", None)
        if old is not None:
            old.popdown()
        # Anchored to the overlay that fills the window: it is never
        # recycled, and unlike the window itself it honours the point
        # (a popover parented to the window opened at its top-left corner).
        anchor = self.toasts
        ok, point = source.compute_point(anchor, Graphene.Point().init(x, y))
        if not ok:
            return

        popover = Gtk.PopoverMenu()
        group = Gio.SimpleActionGroup()
        menu = Gio.Menu()
        for items in sections:
            if not items:
                continue
            section = Gio.Menu()
            for key, label, accel, callback in items:
                act = Gio.SimpleAction.new(key, None)
                act.connect("activate",
                            lambda *_a, cb=callback: (popover.popdown(), cb()))
                group.add_action(act)
                entry = Gio.MenuItem.new(label, f"ctx.{key}")
                if accel:
                    # shown right-aligned in grey, as in any desktop menu
                    entry.set_attribute_value("accel", GLib.Variant.new_string(accel))
                section.append_item(entry)
            menu.append_section(None, section)

        # Order matters: a PopoverMenu binds each item to its action when
        # the model is attached, through its parent chain - so the parent
        # and the action group come first, then the model. Bound against
        # nothing, every item came out greyed out and did nothing.
        popover.set_parent(anchor)
        popover.insert_action_group("ctx", group)
        popover.set_menu_model(menu)
        popover.add_css_class("pika-context-menu")
        # No arrow: a context menu opens at the point and flips only when
        # it would leave the screen.
        popover.set_has_arrow(False)
        popover.set_halign(Gtk.Align.START)
        popover.set_position(Gtk.PositionType.BOTTOM)
        # Fields set one by one: PyGObject ignores Gdk.Rectangle(x=..., y=...)
        # keyword arguments and hands GTK an empty rectangle at 0,0 - which
        # is why every context menu used to open in the corner of whatever
        # it was attached to instead of at the pointer.
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(point.x), int(point.y), 1, 1
        popover.set_pointing_to(rect)

        def on_closed(p):
            # The unparent has to wait: GtkPopoverMenu closes *before* it
            # runs the chosen item, which needs the popover still in place.
            def drop():
                p.unparent()
                if getattr(self, "_context_popover", None) is p:
                    self._context_popover = None
                return GLib.SOURCE_REMOVE
            GLib.idle_add(drop)
        popover.connect("closed", on_closed)
        self._context_popover = popover
        popover.popup()

    def _on_rename_folder(self, folder_id, current_name):
        dialog = Adw.AlertDialog(heading=_("Rename Folder"))
        entry = Gtk.Entry(text=current_name, activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("rename", _("Rename"))
        dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_close_response("cancel")
        dialog.set_default_response("rename")

        def _focus_once():
            entry.grab_focus()
            entry.select_region(0, -1)
            return GLib.SOURCE_REMOVE
        GLib.idle_add(_focus_once, priority=GLib.PRIORITY_HIGH)

        def done(d, response):
            if response != "rename":
                return
            name = entry.get_text().strip()
            if name:
                self.catalog.rename_folder(folder_id, name)
                self.refresh_sidebar()
                self._mirror_state()
        dialog.connect("response", done)
        dialog.present(self)

    def _on_rename_album(self, album_id, current_name):
        dialog = Adw.AlertDialog(heading=_("Rename Album"))
        entry = Gtk.Entry(text=current_name, activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("rename", _("Rename"))
        dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_close_response("cancel")
        dialog.set_default_response("rename")

        def _focus_once():
            entry.grab_focus()
            entry.select_region(0, -1)
            return GLib.SOURCE_REMOVE
        GLib.idle_add(_focus_once, priority=GLib.PRIORITY_HIGH)

        def done(d, response):
            if response != "rename":
                return
            name = entry.get_text().strip()
            if name:
                self.catalog.rename_album(album_id, name)
                self._write_album_sidecar(album_id)
                self.refresh_sidebar()
                self._mirror_state()
        dialog.connect("response", done)
        dialog.present(self)

    def _on_delete_folder(self, folder_id, name):
        dialog = Adw.AlertDialog(
            heading=_("Delete “{name}”?").format(name=name),
            body=_("This deletes the folder and the albums and folders inside it. Your "
                   "photos are not deleted."))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")

        # Collect every album's uuid this delete will cascade-remove,
        # before it happens, so their JSON sidecars can be cleaned up
        # too - the database cascade has no way to reach back out to
        # files on disk.
        def collect_album_uuids(nodes):
            uuids = []
            for n in nodes:
                if n["kind"] == "album":
                    uuids.append(n["row"]["uuid"])
                else:
                    uuids.extend(collect_album_uuids(n["children"]))
            return uuids

        def find_subtree(nodes, target_id):
            for n in nodes:
                if n["kind"] == "folder" and n["row"]["id"] == target_id:
                    return n["children"]
                if n["kind"] == "folder":
                    found = find_subtree(n["children"], target_id)
                    if found is not None:
                        return found
            return None

        subtree = find_subtree(self.catalog.tree(), folder_id) or []
        album_uuids = collect_album_uuids(subtree)

        def done(d, response):
            if response == "delete":
                self.catalog.delete_folder(folder_id)
                for u in album_uuids:
                    self._delete_album_sidecar(u)
                self.refresh_sidebar()
                self._mirror_state()
                if self._scope == "album":
                    # The current album may have just been deleted along
                    # with the folder; fall back to the library rather
                    # than keep browsing a scope that no longer exists.
                    self._scope, self._album_id = "library", None
                    self.grid.load("library")
        dialog.connect("response", done)
        dialog.present(self)

    def _on_delete_album(self, album_id, name):
        dialog = Adw.AlertDialog(
            heading=_("Delete “{name}”?").format(name=name),
            body=_("This deletes the album. Your photos are not deleted."))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")

        row = self.catalog.q1("SELECT uuid FROM albums WHERE id=?", (album_id,))
        album_uuid = row["uuid"] if row else None

        def done(d, response):
            if response == "delete":
                self.catalog.delete_album(album_id)
                if album_uuid:
                    self._delete_album_sidecar(album_uuid)
                self.refresh_sidebar()
                if self._scope == "album" and self._album_id == album_id:
                    self._scope, self._album_id = "library", None
                    self.grid.load("library")
                elif self._scope == "folder":
                    self.folder_view.load(getattr(self, "_folder_id", None))
        dialog.connect("response", done)
        dialog.present(self)

    def _on_new_folder(self, parent_id=None):
        dialog = Adw.AlertDialog(heading=_("New Folder"),
                                 body=_("Give the folder a name."))
        entry = Gtk.Entry(placeholder_text=_("Folder name"),
                          activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("create", _("Create"))
        dialog.set_response_appearance("create",
                                       Adw.ResponseAppearance.SUGGESTED)
        dialog.set_close_response("cancel")
        dialog.set_default_response("create")

        def _focus_folder_name_once():
            entry.grab_focus()
            return GLib.SOURCE_REMOVE
        GLib.idle_add(_focus_folder_name_once, priority=GLib.PRIORITY_HIGH)

        def done(d, response):
            if response != "create":
                return
            name = entry.get_text().strip() or _("Untitled Folder")
            self.catalog.create_folder(name, parent_id=parent_id)
            self.refresh_sidebar()
            self._mirror_state()
            if self._scope == "folder":
                self.folder_view.load(getattr(self, "_folder_id", None))
        dialog.connect("response", done)
        dialog.present(self)

    def _move_out(self, album_id=None, folder_id=None, to=None):
        if album_id is not None:
            self._move_items([("album", album_id)], to)
        elif folder_id is not None:
            self._move_items([("folder", folder_id)], to)

    def _move_items(self, items, to) -> bool:
        """Put albums, Smart Albums and folders into the folder ``to``, or
        into no folder with None. ``items`` are (kind, id, ...) tuples.
        True when anything moved: a folder is never put inside itself."""
        moved = 0
        for kind, item_id, *_rest in items:
            if kind == "album":
                self.catalog.move_album_to_folder(item_id, to)
                self._write_album_sidecar(item_id)
            elif kind == "smart":
                self.catalog.move_smart_album_to_folder(item_id, to)
            elif kind != "folder" or not self.catalog.move_folder(item_id, to):
                continue
            moved += 1
        if moved:
            # the folder tree and Smart Albums live in the mirrors a backup carries
            self._mirror_state()
            self.refresh_sidebar()
            if self._scope == "folder":
                self.folder_view.load(getattr(self, "_folder_id", None))
        return moved > 0

    def _choose_move_target(self, items):
        """Ask where albums, Smart Albums or folders go: any folder, or the
        top level. ``items`` are (kind, id, name) tuples. A folder's own
        branch is not offered, and where they already are can't be chosen."""
        from .chrome import fit
        blocked = set()
        for kind, item_id, _name in items:
            if kind == "folder":
                blocked |= self.catalog.folder_and_inside(item_id)

        def location(kind, item_id):
            table, column = {"album": ("albums", "folder_id"),
                             "smart": ("smart_albums", "folder_id"),
                             "folder": ("folders", "parent_id")}[kind]
            row = self.catalog.q1(f"SELECT {column} AS f FROM {table} WHERE id=?", (item_id,))
            return row["f"] if row is not None else None
        places = {location(kind, item_id) for kind, item_id, _name in items}
        here = next(iter(places)) if len(places) == 1 else object()

        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE,
                              activate_on_single_click=False)
        listbox.add_css_class("boxed-list")

        def option(label, icon, target, depth):
            row = Gtk.ListBoxRow()
            box = Gtk.Box(spacing=10, margin_start=12 + depth * 18, margin_end=12,
                          margin_top=9, margin_bottom=9)
            box.append(Gtk.Image(icon_name=icon))
            box.append(Gtk.Label(label=label, xalign=0.0, hexpand=True, ellipsize=3))
            if target == here:
                note = Gtk.Label(label=_("Here now"))
                note.add_css_class("pika-dim")
                box.append(note)
                row.set_sensitive(False)
            row.set_child(box)
            row._target, row._label = target, label
            listbox.append(row)

        option(_("Top Level"), "view-list-symbolic", None, 0)

        def walk(nodes, depth):
            for node in nodes:
                if node["kind"] == "folder" and node["row"]["id"] not in blocked:
                    option(node["row"]["name"], "folder-symbolic", node["row"]["id"], depth)
                    walk(node["children"], depth + 1)
        walk(self.catalog.tree(), 0)

        if len(items) == 1:
            heading = _("Move “{name}” To").format(name=items[0][2])
        else:
            heading = ngettext("Move {count} Item To", "Move {count} Items To",
                               len(items)).format(count=len(items))
        dialog = Adw.AlertDialog(heading=heading, body=_("Choose a folder."))
        dialog.set_extra_child(Gtk.ScrolledWindow(
            child=listbox, hscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True, max_content_height=fit(0, 360)[1]))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("move", _("Move"))
        dialog.set_response_appearance("move", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_response_enabled("move", False)
        dialog.set_close_response("cancel")
        listbox.connect("row-selected",
                        lambda _l, row: dialog.set_response_enabled("move", row is not None))

        def move_to(row):
            if row is None or not self._move_items(items, row._target):
                return
            self._show_toast(_("Moved to Top Level") if row._target is None
                             else _("Moved to “{folder}”").format(folder=row._label))

        def activated(_l, row):              # a double-click moves straight away
            dialog.force_close()
            move_to(row)
        listbox.connect("row-activated", activated)
        dialog.connect("response", lambda _d, response: response == "move"
                       and move_to(listbox.get_selected_row()))
        dialog.present(self)

    def _on_new_smart_album(self, smart_id=None, folder_id=None):
        """New Smart Album; also Edit Smart Album."""
        from .smart_album_dialog import SmartAlbumDialog
        dialog = SmartAlbumDialog(self.catalog, smart_id=smart_id,
                                  folder_id=folder_id)

        def saved(_d, sid):
            self._mirror_state()
            self.refresh_sidebar()
            self._select_sidebar_key(f"smart:{sid}")
            self._scope, self._album_id, self._smart_id = "smart", None, sid
            self.grid.load("smart", None, self.search.get_text() or None,
                           smart_id=sid)
        dialog.connect("saved", saved)
        dialog.present(self)

    def _on_smart_right_click(self, gesture, _n, x, y, smart_id, name):
        self._show_context_menu(gesture.get_widget(), x, y, [
            [("edit", _("Edit Smart Album…"), None,
              lambda: self._on_new_smart_album(smart_id=smart_id))],
            [("move-to", _("Move To…"), None,
              lambda: self._choose_move_target([("smart", smart_id, name)]))],
            [("delete", _("Delete Smart Album…"), None,
              lambda: self._on_delete_smart_album(smart_id, name))],
        ])

    def _on_delete_smart_album(self, smart_id, name):
        dialog = Adw.AlertDialog(
            heading=_("Delete “{name}”?").format(name=name),
            body=_("This deletes the smart album. Your photos are not deleted."))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")

        def done(_d, response):
            if response != "delete":
                return
            self.catalog.delete_smart_album(smart_id)
            self._mirror_state()
            if self._scope == "smart" and self._smart_id == smart_id:
                self._select_sidebar_key("library")
                self._scope, self._smart_id = "library", None
                self.grid.load("library")
            self.refresh_sidebar()
        dialog.connect("response", done)
        dialog.present(self)

    def _on_new_album(self, ids=None, folder_id=None):
        dialog = Adw.AlertDialog(heading=_("New Album"),
                                 body=_("Give the album a name."))
        entry = Gtk.Entry(placeholder_text=_("Album name"),
                          activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("create", _("Create"))
        dialog.set_response_appearance("create",
                                       Adw.ResponseAppearance.SUGGESTED)
        # Escape and the close button both map to Cancel, and Cancel
        # simply closes: rebuilding the sidebar here used to re-select
        # "New Album…" and immediately reopen this dialog.
        dialog.set_close_response("cancel")
        dialog.set_default_response("create")
        # Without this the text field never receives keyboard focus when
        # the dialog opens - GTK leaves focus on the default response
        # button instead, so every keystroke either does nothing or (for
        # Space/Enter) activates that button. grab_focus() must run after
        # the dialog is actually mapped, hence idle_add rather than
        # calling it immediately - but at PRIORITY_HIGH, not the default
        # idle priority: GDK's event source dispatches queued key events
        # at the default priority, which runs *before* a plain idle
        # callback in the same main-loop pass. At default idle priority,
        # a keystroke typed fast enough after the dialog opens (a script,
        # or someone who types quickly) could still be delivered to the
        # old focus widget a moment before this callback ever moves focus
        # to the entry - exactly the bug this whole fix is for, just
        # narrowed instead of closed. PRIORITY_HIGH runs ahead of that
        # dispatch, so focus is always in place first.
        def _focus_album_name_once():
            entry.grab_focus()
            return GLib.SOURCE_REMOVE      # run once, not forever
        GLib.idle_add(_focus_album_name_once, priority=GLib.PRIORITY_HIGH)

        def done(d, response):
            if response != "create":
                return
            name = entry.get_text().strip() or _("Untitled Album")
            aid = self.catalog.create_album(name, folder_id=folder_id)
            if ids:
                self.catalog.album_add(aid, ids)
            self._write_album_sidecar(aid)
            self.refresh_sidebar()
            # Select the album that was just created, so creating one
            # visibly does something even when it is still empty.
            for child in self.sidebar_list:
                if getattr(child, "_album_id", None) == aid:
                    self.sidebar_list.select_row(child)
                    break
            self._scope, self._album_id = "album", aid
            self.grid.load("album", aid, self.search.get_text() or None)
        dialog.connect("response", done)
        dialog.present(self)

    def _write_album_sidecar(self, album_id):
        """Mirror the album to a readable JSON file.

        The catalog is derived state; the album only truly exists once it
        is written somewhere a person can read and a backup can carry.
        The filename is derived from the album's name, which means a
        rename changes it - so any older file for this same album (found
        by matching the uuid inside it, not the filename) is removed
        first, or renaming would leave the old name's file behind as a
        stale duplicate forever.
        """
        if sidecars.write_album(self.library, self.catalog, album_id):
            self._backup_soon()

    def _delete_album_sidecar(self, album_uuid):
        """Remove an album's JSON file when the album itself is deleted, and
        remember it, so another computer sharing the backup deletes it too."""
        sidecars.delete_album_file(self.library, album_uuid)

    # -- photo menu & rename ---------------------------------------------
    def _on_rename_photo(self):
        visible_dialog = getattr(self, "get_visible_dialog", None)
        if visible_dialog is not None and visible_dialog() is not None:
            return                      # F2 inside a dialog: not for us
        page = self.stack.get_visible_child_name()
        if page == "viewer":
            self.viewer.rename_file()
        elif page == "grid" and self._scope != "device":
            ids = self.grid.selected_ids()
            if len(ids) == 1:
                self._rename_photo_id(ids[0])

    def _rename_photo_id(self, photo_id):
        from .rename_dialog import ask_rename_photo
        ask_rename_photo(self, self.library, self.catalog, photo_id,
                         lambda _new: self._refresh())

    def _on_photo_context_menu(self, _grid, item, widget, x, y):
        from .models import PhotoItem
        if not isinstance(item, PhotoItem):
            return                      # a camera's photo: not in the library
        ids = self.grid.selected_ids() or [item.id]
        single = len(ids) == 1
        if self._scope == "trash":
            # In Recently Deleted the choices are to bring back or erase.
            self._show_context_menu(widget, x, y, [
                [("recover", _("Recover"), None, lambda: self._on_trash_button(None))],
                [("purge", _("Delete Permanently…"), "Delete", lambda: self._on_purge(None))],
            ])
            return
        marks = ",".join("?" * len(ids))
        all_fav = bool(self.catalog.scalar(
            f"SELECT MIN(favorite) FROM photos WHERE id IN ({marks})", ids, 0))
        n_sel = len(ids)
        if self._scope == "hidden":
            hide_label = _("Unhide") if single else _("Unhide {count} Photos").format(count=n_sel)
        else:
            hide_label = _("Hide") if single else _("Hide {count} Photos").format(count=n_sel)
        export_label = _("Export…") if single else _("Export {count} Photos…").format(count=n_sel)
        delete_label = _("Delete") if single else _("Delete {count} Photos").format(count=n_sel)
        self._show_context_menu(widget, x, y, [
            [("open", _("Open"), "space",
              lambda: self._on_photo_activated(self.grid, item)),
             ("rename", _("Rename…"), "F2",
              lambda: self._rename_photo_id(item.id))] if single else [],
            [("favorite", _("Remove from Favourites") if all_fav else _("Favourite"),
              "period", lambda: self._on_bulk_favorite(None)),
             ("hide", hide_label,
              "<Primary>l", lambda: self._on_bulk_hide())],
            [("rotate-ccw", _("Rotate Left"), "<Primary><Shift>r",
              lambda: self._on_rotate(-1, ids)),
             ("rotate-cw", _("Rotate Right"), "<Primary>r",
              lambda: self._on_rotate(1, ids))],
            [("export", export_label, "<Primary>e",
              lambda: self._on_bulk_export(None))],
            [("cover", _("Make Album Cover"), None,
              lambda: self._make_album_cover(item))]
            if single and self._scope == "album" and self._album_id is not None else [],
            [("delete", delete_label, "Delete",
              lambda: self._on_bulk_trash(None))],
        ])

    def _folder_here(self):
        """Where a new album or folder made from the view on screen goes: the
        folder being shown, the folder of the album being shown, or the top."""
        if self._scope == "folder":
            return getattr(self, "_folder_id", None)
        return self._current_album_folder()

    def _new_here_items(self):
        target = self._folder_here()
        return [("new-album", _("New Album…"), "<Primary>n",
                 lambda: self._on_new_album(None, folder_id=target)),
                ("new-folder", _("New Folder…"), "<Primary><Shift>n",
                 lambda: self._on_new_folder(target))]

    def _on_background_menu(self, source, x, y):
        """Right-click on empty space: make an album or a folder right here,
        without going to the sidebar."""
        self._show_context_menu(source, x, y, [self._new_here_items()])

    def _on_cards_menu(self, _view, keys, x, y):
        """Right-click on album or folder cards inside a folder."""
        sections = []
        if len(keys) == 1:
            kind, item_id, _name = keys[0]
            opener = {"folder": self._open_folder, "smart": self._open_smart}.get(
                kind, self._open_album)
            sections.append([("open", _("Open"), None, lambda: opener(item_id))])
        chosen = list(keys)
        sections.append([("move-to", _("Move To…"), None,
                          lambda: self._choose_move_target(chosen))])
        albums =[(item_id, name) for kind, item_id, name in keys if kind == "album"]
        if len(albums) == 1 and len(keys) == 1:
            sections.append([("delete", _("Delete Album…"), None,
                              lambda: self._on_delete_album(*albums[0]))])
        elif albums:
            sections.append([("delete", ngettext("Delete {count} Album…", "Delete {count} Albums…",
                                                 len(albums)).format(count=len(albums)), None,
                              lambda: self._delete_albums([a for a, _n in albums]))])
        sections.append(self._new_here_items())
        self._show_context_menu(self.folder_view._overlay, x, y, sections)

    def _delete_albums(self, album_ids):
        n = len(album_ids)
        dialog = Adw.AlertDialog(
            heading=ngettext("Delete {count} album?", "Delete {count} albums?", n).format(count=n),
            body=_("This deletes the albums. Your photos are not deleted."))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")

        def done(_d, response):
            if response != "delete":
                return
            for album_id in album_ids:
                row = self.catalog.q1("SELECT uuid FROM albums WHERE id=?", (album_id,))
                self.catalog.delete_album(album_id)
                if row is not None:
                    self._delete_album_sidecar(row["uuid"])
            self.refresh_sidebar()
            if self._scope == "folder":
                self.folder_view.load(getattr(self, "_folder_id", None))
        dialog.connect("response", done)
        dialog.present(self)

    def _current_album_folder(self):
        """The folder of the album on screen, or None outside an album."""
        if self._scope != "album" or self._album_id is None:
            return None
        row = self.catalog.q1("SELECT folder_id FROM albums WHERE id=?", (self._album_id,))
        return row["folder_id"] if row is not None else None

    def _make_album_cover(self, item):
        """Show this photo for the album in folders and wherever albums appear."""
        album_id = self._album_id
        if album_id is None:
            return
        self.catalog.set_album_cover(album_id, item.id)
        self._write_album_sidecar(album_id)
        self.refresh_sidebar()
        self._show_toast(_("Album cover changed"))

    # -- libraries -------------------------------------------------------
    def _on_open_library(self):
        dialog = Gtk.FileDialog(title=_("Open a Piklin Library"))

        def done(dlg, res):
            try:
                folder = dlg.select_folder_finish(res)
            except GLib.Error:
                return
            if folder is not None and folder.get_path():
                self._switch_library(folder.get_path())
        dialog.select_folder(self, None, done)

    def _switch_library(self, path):
        from ..paths import remember_library
        root = Path(path).expanduser().resolve()
        if root == self.library.root:
            return
        if not (root / "catalog.db").is_file():
            dlg = Adw.AlertDialog(
                heading=_("Not a Piklin Library"),
                body=_("“{name}” is not a Piklin library. Choose a library, such as "
                       "“Piklin Library.piklin”.").format(name=root.name))
            dlg.add_response("ok", _("OK"))
            dlg.present(self)
            return
        remember_library(root)
        app = self.get_application()
        app.relaunch_library = str(root)
        app.quit()

    # -- folders & scanning ---------------------------------------------
    def _on_add_folder(self):
        dialog = Gtk.FileDialog(title=_("Choose a Folder with Photos"))

        def done(dlg, res):
            try:
                folder = dlg.select_folder_finish(res)
            except GLib.Error:
                return
            if folder and folder.get_path():
                self._start_scan([folder.get_path()])
        dialog.select_folder(self, None, done)

    def _start_scan(self, roots):
        self.scan_bar.set_visible(True)
        self.scan_label.set_text(_("Looking for photos…"))

        def progress(p):
            def apply():
                if p.phase in ("done", "cancelled"):
                    self.scan_bar.set_visible(False)
                    self._refresh()
                    return False
                self.scan_bar.set_visible(True)
                label = {"scanning": _("Looking for photos"),
                         "probing": _("Reading photo details"),
                         "thumbnails": _("Preparing previews")}.get(p.phase, p.phase)
                extra = ("  ·  " + _("{count} new").format(count=p.added)) if p.added else ""
                self.scan_label.set_text(
                    (_("{step} — {done} of {total}").format(
                        step=label, done=f"{p.done:,}", total=f"{p.total:,}")
                     if p.total else f"{label} — {p.done:,}")
                    + extra)
                self.scan_progress.set_fraction(p.fraction)
                return False
            GLib.idle_add(apply)

        self.indexer.start(roots, progress, with_thumbnails=True)

    # -- videos made smaller after they are copied --------------------------
    def _queue_video_shrink(self, paths):
        """Videos just copied in as they are, to make smaller one at a time
        in the background. The list is kept with the library, so what is
        left carries on the next time Piklin opens."""
        if paths:
            queue = list(self.settings.get("videos_to_shrink") or [])
            for p in map(str, paths):
                if p not in queue:
                    queue.append(p)
            self.settings.set("videos_to_shrink", queue)
        if self.settings.get("videos_to_shrink") and not getattr(self, "_shrinking", False):
            self._shrinking = True
            threading.Thread(target=self._shrink_videos, daemon=True).start()

    def _shrink_videos(self):
        total = len(self.settings.get("videos_to_shrink") or [])
        done = 0
        while True:
            # never while photos are still being copied: that comes first
            while getattr(self, "_copy_cancel", None) is not None:
                time.sleep(2)
            queue = list(self.settings.get("videos_to_shrink") or [])
            if not queue:
                break
            total = max(total, done + len(queue))
            path = queue[0]
            row = self.catalog.photo_by_path(path)

            def progress(fraction, d=done, t=total):
                GLib.idle_add(self._video_shrink_status, d + 1, t, fraction)
            outcome = "gone"
            if row is not None:
                progress(0.0)
                outcome = devicemod.shrink_video(self.library, self.catalog, row["id"],
                                                 on_progress=progress)
            remaining = [p for p in (self.settings.get("videos_to_shrink") or []) if p != path]
            GLib.idle_add(self.settings.set, "videos_to_shrink", remaining)
            done += 1
            if outcome == "smaller":
                GLib.idle_add(self._video_shrunk, row["id"])
            time.sleep(0.5)                 # let the setting land before the next round
        GLib.idle_add(self._video_shrink_done)

    def _video_shrink_status(self, done, total, fraction):
        self.footer_videos.set_text(
            _("Making videos smaller — {done} of {total} ({percent}%)").format(
                done=done, total=total, percent=int(max(0.0, min(1.0, fraction)) * 100)))
        self.footer_videos.set_visible(True)
        return False

    def _video_shrunk(self, photo_id):
        """A video now smaller: its tile follows the new file at once."""
        row = self.catalog.photo(photo_id)
        item = getattr(self.grid, "_by_id", {}).get(photo_id)
        if row is not None and item is not None:
            item.path = row["path"]
            item.filename = row["filename"]
            item.bytes = row["bytes"]
            self.grid.repaint_items([photo_id])
        self._backup_soon()
        return False

    def _video_shrink_done(self):
        self._shrinking = False
        self.footer_videos.set_visible(False)
        if self.settings.get("videos_to_shrink"):
            self._queue_video_shrink([])    # added while the last one ran
        return False

    def _repair_video_sizes(self):
        """Once per library: upright phone videos were recorded as wide
        (1280x720 for a 720x1280 video) before the size came from a decoded
        frame. Measure each video again, in the background."""
        if self.settings.get("video_sizes_checked_v2"):
            return
        from ..catalog import VIDEO_SQL
        from ..video import stream_info
        rows = self.catalog.q(
            f"SELECT p.id, p.path, p.width, p.height FROM photos p WHERE {VIDEO_SQL}")

        def work():
            fixed = []
            for r in rows:
                try:
                    info = stream_info(r["path"])
                except Exception:
                    info = None
                if info and (info["width"], info["height"]) != (r["width"], r["height"]):
                    fixed.append((info["width"], info["height"], r["id"]))
            if fixed:
                with self.catalog.write() as cur:
                    cur.executemany("UPDATE photos SET width=?, height=? WHERE id=?", fixed)
            GLib.idle_add(lambda: (self.settings.set("video_sizes_checked_v2", True),
                                   fixed and self.grid.refresh(), False)[2])
        threading.Thread(target=work, daemon=True).start()

    def _first_run(self):
        GLib.timeout_add_seconds(20, lambda: (self._repair_video_sizes(), False)[1])
        # videos left to make smaller when Piklin was last closed
        GLib.timeout_add_seconds(30, lambda: (self._queue_video_shrink([]), False)[1])
        self.refresh_sidebar()
        # A few seconds in, so opening Piklin is never slowed by the network.
        # Every time Piklin opens it looks for a new version - a few seconds
        # in, so opening is never slowed by the network - and asks.
        GLib.timeout_add_seconds(4, lambda: (self._check_updates(quiet=True, on_open=True), False)[1])
        # and every hour after that while Piklin stays open (only when
        # updates are on, and never more than once an hour - see updates.due)
        GLib.timeout_add_seconds(60 * 60, lambda: (self._check_updates(quiet=True), True)[1])
        self.grid.load("library")
        if not self.catalog.roots():
            self._show_welcome()
        else:
            self._start_scan(None)
            GLib.timeout_add(800, self._maybe_offer_backup)
        return False

    def _backup_soon(self):
        if getattr(self, "autobackup", None) is not None:
            self.autobackup.mark_changed()

    def _sync_from_backup(self, backend, progress=None):
        """Bring in what another computer sent to this backup (off the UI
        thread), then show it."""
        from .. import sync
        result = sync.pull(self.library, self.catalog, backend, progress)
        if result.changed:
            GLib.idle_add(self._after_sync)
        return result

    def _after_sync(self):
        self._refresh()
        return False

    def _on_backup_status(self, text):
        self.footer_backup.set_text(text)
        self.footer_backup.set_tooltip_text(text or None)
        self.footer_backup.set_visible(bool(text))

    def _maybe_offer_backup(self):
        """Once, at the start: where should a copy of the photos go?"""
        if self.settings.get("backup_onboarding_done") or self.settings.get("remotes"):
            return False
        if self.get_visible_dialog() is not None:
            return True                 # after the welcome is answered
        dialog = Adw.AlertDialog(
            heading=_("Keep a Copy of Your Photos"),
            body=_("Piklin can back up your photos and videos to a drive, a NAS "
                 "or a cloud service. Once one is connected, new photos, "
                 "videos and edits are copied there automatically.\n\n"
                 "Nothing leaves your computer unless you choose where."))
        # Stacked buttons read bottom-up, so "Not Now" is added first to end
        # up last, under the three choices.
        dialog.add_response("later", _("Not Now"))
        dialog.add_response("rclone", _("Cloud Service"))
        dialog.add_response("webdav", _("NAS or WebDAV Server"))
        dialog.add_response("local", _("Folder or Drive"))
        dialog.set_close_response("later")

        def done(_d, response):
            self.settings.set("backup_onboarding_done", True)
            if response in ("local", "webdav", "rclone"):
                settings = self._open_settings("remotes")
                settings._on_add_remote(None, response)
        dialog.connect("response", done)
        dialog.present(self)
        return False

    def _show_welcome(self):
        from ..paths import pictures_dir
        pictures = pictures_dir()
        dialog = Adw.AlertDialog(
            heading=_("Welcome to Piklin"),
            body=_("Choose a folder with your photos and Piklin will organize them.\n\n"
                   "Your photos are never moved or changed. Edits are saved separately, "
                   "so your originals stay exactly as they are."))
        dialog.add_response("later", _("Later"))
        if pictures.is_dir():
            dialog.add_response("pictures", _("Use {folder}").format(folder=pictures.name))
            dialog.set_response_appearance("pictures",
                                           Adw.ResponseAppearance.SUGGESTED)
        dialog.add_response("choose", _("Choose Folder…"))
        dialog.set_close_response("later")      # Escape means "Later"

        # The language comes first: nothing has happened yet, so picking one
        # reopens Piklin straight away and the welcome returns in it.
        from .. import i18n
        codes = [code for code, _name in i18n.LANGUAGES]
        names = [_(name) if not code else name for code, name in i18n.LANGUAGES]
        language = Adw.ComboRow(title=_("Language"), model=Gtk.StringList.new(names))
        chosen = i18n.chosen_language()
        language.set_selected(codes.index(chosen) if chosen in codes else 0)
        switching = []

        def switch(row, _pspec):
            code = codes[row.get_selected()]
            if code == i18n.chosen_language():
                return
            i18n.choose_language(code)
            switching.append(code)
            dialog.force_close()
            app = self.get_application()
            app.relaunch_library = str(self.library.root)
            app.quit()
        language.connect("notify::selected", switch)
        rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        rows.add_css_class("boxed-list")
        rows.append(language)
        dialog.set_extra_child(rows)

        def done(d, response):
            if switching:
                return
            if response == "pictures":
                self._start_scan([str(pictures)])
            elif response == "choose":
                self._on_add_folder()
            GLib.timeout_add(600, self._maybe_offer_backup)
        dialog.connect("response", done)
        dialog.present(self)

    def _open_settings(self, page="general"):
        from .settings_dialog import SettingsDialog
        dialog = SettingsDialog(self, self.library, self.catalog, self.settings,
                                self.thumbs, page)
        dialog.present(self)
        return dialog

    def _on_help(self):
        from .help_dialog import HelpDialog
        HelpDialog().present(self)

    def _on_activity_log(self):
        from .log_dialog import LogDialog
        LogDialog().present(self)

    def _on_about(self):
        from ..app import VERSION
        from ..engine import tools as tools_mod
        from .. import imageio as iio_mod
        about = Adw.AboutDialog(
            application_name="Piklin",
            application_icon="piklin",
            version=VERSION,
            developer_name="Vezzu Studio",
            website="https://vezzu.studio",
            copyright=_("© 2026 Vezzu Studio. All rights reserved."),
            comments=(
                _("Piklin was created by Vezzu Studio.\n\nA photo and video library and "
                  "editor for Linux. {tools} editing tools · {formats} file formats · "
                  "everything runs on this computer.").format(
                    tools=len(tools_mod.REGISTRY),
                    formats=len(iio_mod.supported_extensions()))),
            license_type=Gtk.License.CUSTOM,
            license=(
                _("Piklin is free to use under the Piklin License 1.0. "
                "© 2026 Vezzu Studio.\n\n"
                "You may install and use Piklin on as many of your own "
                "computers as you like, at home or for work, including "
                "commercial use. You may not modify, redistribute or sell "
                "it.\n\nYour photos, edits and everything you make with "
                "Piklin are yours.")))
        # Libraries Piklin ships, each under its own licence (full texts in
        # /usr/share/doc/piklin/third-party).
        for title, licence in (
                ("FFmpeg (LGPL build, with OpenH264, libvpx, Opus, dav1d)", Gtk.License.LGPL_2_1),
                ("PyAV", Gtk.License.BSD_3),
                ("OpenCV", Gtk.License.APACHE_2_0),
                ("NumPy", Gtk.License.BSD_3),
                ("Pillow, pi-heif, rawpy, miniaudio", Gtk.License.MIT_X11),
                (_("Inter typeface"), Gtk.License.CUSTOM)):
            about.add_legal_section(
                title, None, licence,
                "SIL Open Font License 1.1" if licence == Gtk.License.CUSTOM else None)
        about.present(self)

    def do_close_request(self):
        if (getattr(self, "_copy_cancel", None) is not None
                and not getattr(self, "_closing_after_copy", False)):
            dialog = Adw.AlertDialog(
                heading=_("Photos Are Still Being Copied"),
                body=_("If you close Piklin now, the copy stops. The photos "
                       "already copied stay in your library."))
            dialog.add_response("keep", _("Keep Copying"))
            dialog.add_response("close", _("Stop and Close"))
            dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("keep")
            dialog.set_close_response("keep")

            def done(_d, response):
                if response != "close":
                    return
                self._closing_after_copy = True
                if self._copy_cancel is not None:
                    self._copy_cancel.set()
                ticks = [0]

                def wait():
                    # let the files being copied right now finish
                    ticks[0] += 1
                    if self._copy_cancel is None or ticks[0] > 40:
                        self.close()
                        return False
                    return True
                GLib.timeout_add(250, wait)
            dialog.connect("response", done)
            dialog.present(self)
            return True
        self.indexer.stop(timeout=2.0)
        self.thumbs.shutdown()
        self.editor.shutdown()
        self.video_editor.shutdown()
        self.viewer.shutdown()
        return False
