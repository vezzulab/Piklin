"""The main window: sidebar, grid, viewer and editor in one navigation stack."""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gdk, Gio, Graphene, Gtk  # noqa: E402

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


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app, library: Library):
        super().__init__(application=app, title="Piklin",
                         default_width=1400, default_height=900)
        self.add_css_class("pikalicious")
        self.library = library
        self.settings = Settings(library.settings)
        self.catalog = Catalog(library.db)
        # A library that was moved still names its old location in the
        # paths it stores; bring them up to date before anything reads them.
        from ..library_move import relocate
        relocate(library, self.catalog, self.settings)
        self.thumbs = ThumbCache(library.thumbs,
                                 workers=self.settings.get("thumb_workers", 0))
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
        self.toasts.set_child(self.stack)
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
        GLib.idle_add(self._first_run)

    # ==================================================================
    # layout
    # ==================================================================
    def _build_library_page(self):
        self.split = Adw.NavigationSplitView(
            min_sidebar_width=210, max_sidebar_width=280)
        self.split.set_sidebar(Adw.NavigationPage(
            child=self._build_sidebar(), title="Library"))
        self.split.set_content(Adw.NavigationPage(
            child=self._build_content(), title="Photos"))
        return self.split

    def _build_sidebar(self):
        toolbar = Adw.ToolbarView()
        toolbar.add_css_class("pika-sidebar-pane")
        toolbar.set_top_bar_style(Adw.ToolbarStyle.RAISED_BORDER)
        header = Adw.HeaderBar(show_title=True)
        header.add_css_class("pika-header")
        # No window buttons on the sidebar's bar: they sit on the right of
        # the content bar, in the app's own style.
        header.set_decoration_layout(":")
        # The app's mark, centred over the sidebar.
        from .brand import BrandMark
        self.brand = BrandMark(34)
        header.set_title_widget(self.brand)
        menu = Gio.Menu()
        sort_menu = Gio.Menu()
        for key, label in (("taken_desc", "Newest First"),
                           ("taken_asc", "Oldest First"),
                           ("added_desc", "Recently Added"),
                           ("name_asc", "Name"),
                           ("size_desc", "File Size"),
                           ("rating_desc", "Rating")):
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value(
                "win.sort", GLib.Variant.new_string(key))
            sort_menu.append_item(item)
        menu.append_submenu("Sort By", sort_menu)
        menu.append("Open Library…", "win.open-library")
        menu.append("Add Folder…", "win.add-folder")
        menu.append("Rescan Library", "win.rescan")
        section = Gio.Menu()
        section.append("Back Up & Cloud…", "win.remotes")
        section.append("Storage & Compression…", "win.storage")
        menu.append_section(None, section)
        end = Gio.Menu()
        end.append("Preferences", "win.preferences")
        end.append("About Piklin", "win.about")
        menu.append_section(None, end)
        # An icon-only button has no name a screen reader can announce;
        # the tooltip text doubles as its accessible label.
        btn = Gtk.MenuButton(icon_name="open-menu-symbolic",
                             menu_model=menu, primary=True,
                             tooltip_text="Main Menu")
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
        # Supporting Piklin: one quiet button, never a pop-up or a nag.
        from ..paths import SUPPORT_URL
        if SUPPORT_URL:
            coffee = Gtk.Button(halign=Gtk.Align.FILL, margin_top=10,
                                tooltip_text="Support Piklin")
            coffee.add_css_class("pika-support")
            inner = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
            inner.append(Gtk.Image(icon_name="emblem-favorite-symbolic",
                                   pixel_size=15))
            inner.append(Gtk.Label(label="Support on Ko-fi"))
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
        # the desktop's own setting would put them in.
        header.set_decoration_layout(":minimize,maximize,close")

        self.search = Gtk.SearchEntry(placeholder_text="Search photos",
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
        for key, label in (("year", "Years"), ("month", "Months"),
                           ("day", "Days"), ("none", "All Photos")):
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
        zoom.set_tooltip_text("Thumbnail size")
        zoom.connect("value-changed", self._on_zoom)
        header.pack_start(zoom)

        self.select_label = Gtk.Label()
        self.select_label.add_css_class("pika-dim")
        header.pack_start(self.select_label)

        header.pack_end(self.search)

        # Filter: a pop-up of what to show, several
        # at once, inside whichever view is open.
        self.filter_btn = Gtk.MenuButton(label="Filter",
                                         tooltip_text="Filter photos")
        self.filter_btn.add_css_class("flat")
        self.filter_btn.add_css_class("pika-filter")
        pop = Gtk.Popover()
        pbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                       margin_top=8, margin_bottom=8, margin_start=10,
                       margin_end=10)
        self._filter_checks = {}
        for key, label in (("favorites", "Favourites"), ("edited", "Edited"),
                           ("photos", "Photos"), ("videos", "Videos"),
                           ("screenshots", "Screenshots"),
                           ("not_in_album", "Not in an Album"),
                           ("has_location", "Has Location")):
            chk = Gtk.CheckButton(label=label)
            chk.connect("toggled", self._on_filter_toggled)
            pbox.append(chk)
            self._filter_checks[key] = chk
        pbox.append(Gtk.Separator(margin_top=4, margin_bottom=4))
        show_all = Gtk.Button(label="Show All")
        show_all.add_css_class("flat")
        show_all.connect("clicked", lambda *_: self._clear_filters())
        pbox.append(show_all)
        pop.set_child(pbox)
        self.filter_btn.set_popover(pop)
        header.pack_end(self.filter_btn)

        # Aspect Ratio: square thumbnails, or each photo at its own shape.
        self.aspect_btn = Gtk.ToggleButton(
            icon_name="view-grid-symbolic",
            tooltip_text="Show photos at their original aspect ratio",
            active=self.settings.get("grid_aspect") == "original")
        self.aspect_btn.add_css_class("flat")
        self.aspect_btn.connect("toggled", self._on_aspect_toggled)
        header.pack_start(self.aspect_btn)

        self.action_bar = Gtk.ActionBar()
        self.action_bar.add_css_class("pika-toolbar")
        self.action_bar.set_revealed(False)
        self._bar_buttons = {}
        for key, icon, tip, cb in (
                ("favorite", "starred-symbolic", "Favourite",
                 self._on_bulk_favorite),
                ("trash", "user-trash-symbolic", "Move to Recently Deleted",
                 self._on_trash_button),
                ("export", "document-save-symbolic", "Export…",
                 self._on_bulk_export),
                ("album", "list-add-symbolic", "Add to album…",
                 self._on_bulk_album)):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip)
            b.connect("clicked", cb)
            self.action_bar.pack_start(b)
            self._bar_buttons[key] = b
        # Only meaningful inside Recently Deleted, so it is hidden until
        # you are actually standing there.
        self._import_btn = Gtk.Button(label="Import")
        self._import_btn.add_css_class("suggested-action")
        self._import_btn.connect("clicked", self._on_import_device)
        self._import_btn.set_visible(False)
        self.action_bar.pack_end(self._import_btn)

        # The import screen: where the photos go, and whether the
        # camera keeps them afterwards.
        self._import_delete = Gtk.CheckButton(label="Delete items after import")
        self._import_delete.set_visible(False)
        self.action_bar.pack_end(self._import_delete)
        self._import_album = Gtk.DropDown.new_from_strings(["Library"])
        self._import_album.set_tooltip_text("Import to")
        self._import_album.set_visible(False)
        self._import_album_ids = [None]
        self._import_album_label = Gtk.Label(label="Import to:")
        self._import_album_label.add_css_class("pika-dim")
        self._import_album_label.set_visible(False)
        self.action_bar.pack_start(self._import_album_label)
        self.action_bar.pack_start(self._import_album)

        # Duplicates view only: keep one copy of each selected group.
        self._merge_btn = Gtk.Button(label="Merge")
        self._merge_btn.add_css_class("suggested-action")
        self._merge_btn.connect("clicked", self._on_merge_duplicates)
        self._merge_btn.set_visible(False)
        self.action_bar.pack_end(self._merge_btn)

        self._purge_btn = Gtk.Button(label="Delete Permanently…")
        self._purge_btn.add_css_class("destructive-action")
        self._purge_btn.connect("clicked", self._on_purge)
        self._purge_btn.set_visible(False)
        self.action_bar.pack_start(self._purge_btn)
        clear = Gtk.Button(label="Clear selection")
        clear.add_css_class("flat")
        clear.connect("clicked", lambda *_: self.grid.unselect_all())
        self.action_bar.pack_end(clear)

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
        self.content_stack.add_named(self.summary, "summary")
        toolbar.set_content(self.content_stack)
        toolbar.add_bottom_bar(self.action_bar)
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
            f"{photos:,} Photo" + ("s" if photos != 1 else "")
            + f"  \u00b7  {videos:,} Video" + ("s" if videos != 1 else ""))
        used = int(counts.get("bytes", 0))
        try:
            total = _sh.disk_usage(self.library.root).total
        except OSError:
            total = 0
        if total:
            self.footer_space.set_text(
                f"{self._human_bytes(used)} of {self._human_bytes(total)}")
            self.footer_meter.set_value(min(1.0, used / total))
            self.footer_meter.set_visible(True)
        else:
            self.footer_space.set_text(self._human_bytes(used))
            self.footer_meter.set_visible(False)

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
                folder_id=None, smart_id=None):
            if section_state["collapsed"]:
                return None
            # A folder in the tree is purely organisational - there is no
            # "current folder" scope to browse, so it never shows a
            # persistent selection highlight; clicking it only expands
            # or collapses via row-activated.
            row = Gtk.ListBoxRow(selectable=folder_id is None)
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
            elif indent:
                # Album rows nested under a folder line up with the
                # label text of their folder's siblings, not its arrow.
                spacer = Gtk.Box()
                spacer.set_size_request(12, -1)
                box.append(spacer)
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
            if album_id is not None:
                # An album can be dragged into a folder: this is the only way to move an album
                # that already exists into a folder.
                src = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
                src.connect(
                    "prepare",
                    lambda _s, _x, _y, a=album_id:
                        Gdk.ContentProvider.new_for_value(f"pika-album:{a}"))
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
                                      tooltip_text="New Album or Folder")
                plus.add_css_class("flat")
                plus.add_css_class("pika-sidebar-add")
                box.append(plus)
            row.set_child(box)
            self.sidebar_list.append(row)
            return row

        self._devices = devicemod.list_devices()
        if self._devices:
            header("Devices")
            for dev in self._devices:
                add(f"device:{dev.id}", dev.name, dev.icon)

        # The sidebar order: the library and what you most often
        # come back to first, then your albums, then media types and the
        # utility views that gather photos by what happened to them.
        # Library is where everything starts; it stays open.
        header("Library", collapsible=False)
        add("library", "All Photos", "image-x-generic-symbolic",
            counts.get("library"))
        add("favorites", "Favourites", "starred-symbolic",
            counts.get("favorites"))
        add("trash", "Recently Deleted", "user-trash-symbolic",
            counts.get("trash"))

        add_menu = Gio.Menu()
        add_menu.append("New Album…", "win.new-album")
        add_menu.append("New Smart Album…", "win.new-smart-album")
        add_menu.append("New Folder…", "win.new-folder")
        header("Albums", add_menu)
        self._add_tree_rows(self.catalog.tree(), add, depth=0)

        header("Media Types")
        add("videos", "Videos", "video-x-generic-symbolic",
            counts.get("videos"))
        add("screenshots", "Screenshots", "video-display-symbolic",
            counts.get("screenshots"))

        header("Utilities")
        add("hidden", "Hidden", "view-conceal-symbolic", counts.get("hidden"))
        add("duplicates", "Duplicates", "edit-copy-symbolic",
            counts.get("duplicates"))
        add("edited", "Recently Edited", "document-edit-symbolic",
            counts.get("edited"))
        add("imports", "Imports", "document-save-symbolic",
            counts.get("imports"))

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
        self.scan_label.set_text(f"Reading {device.name}…")
        self.scan_bar.set_visible(True)
        self.scan_progress.set_fraction(0.0)

        def work():
            records = devicemod.scan_device(device)
            already = devicemod.already_imported(self.catalog, records)

            def apply():
                self.scan_bar.set_visible(False)
                if self._device is None or self._device.id != device.id:
                    return False
                self._device_records = records
                self._device_imported = already
                self._fill_import_albums()
                self.grid.load_records(records, subtitle=device.name,
                                       already=already)
                self._on_selection_changed(self.grid)
                return False
            GLib.idle_add(apply)
        threading.Thread(target=work, daemon=True).start()

    def _on_import_device(self, _btn):
        """Copy the selected photos off the camera into the library."""
        if self._device is None:
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
        library = self.library
        self.scan_bar.set_visible(True)
        self.scan_label.set_text(f"Importing from {device.name}…")

        def work():
            def progress(done, total):
                GLib.idle_add(self.scan_progress.set_fraction,
                              done / max(total, 1))
                GLib.idle_add(self.scan_label.set_text,
                              f"Importing {done} of {total}…")
            result = devicemod.import_photos(
                library, records, progress,
                profile=self.settings.get("storage_profile", "visually_lossless"),
                video_profile=self.settings.get("storage_video_profile", "original"))

            def finish():
                self.scan_label.set_text("Adding to your library…")
                # Only the folder the photos landed in needs indexing,
                # not the whole library again.
                self.indexer.scan([str(library.originals)],
                                  lambda p: None)
                self.scan_bar.set_visible(False)
                if album_id is not None:
                    ids = [r["id"] for r in (self.catalog.photo_by_path(str(p))
                                             for p in result["copied"]) if r]
                    if ids:
                        self.catalog.album_add(album_id, ids)
                        self._write_album_sidecar(album_id)
                n = len(result["copied"])
                toast = (f"Imported {n} photo" + ("s" if n != 1 else ""))
                if result["skipped"]:
                    toast += f", {result['skipped']} already in your library"
                if result["failed"]:
                    toast += f", {result['failed']} failed"
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
                self._show_toast(toast)
                if delete_after and result.get("sources"):
                    self._ask_delete_from_device(device, result["sources"])
                return False
            GLib.idle_add(finish)
        threading.Thread(target=work, daemon=True).start()

    def _import_device_records(self, records, album_id=None):
        """Import photos from the camera into the library and, when an album
        is given, file them there.

        Photos already in the library (same content) are not copied again;
        the copy you already have is what goes into the album.
        """
        device = self._device
        if device is None:
            return
        library = self.library
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

        self.scan_bar.set_visible(True)
        self.scan_progress.set_fraction(0.0)
        self.scan_label.set_text(
            f"Importing to \u201c{album_name}\u201d…" if album_name
            else f"Importing from {device.name}…")

        def work():
            def progress(done, total):
                GLib.idle_add(self.scan_progress.set_fraction, done / max(total, 1))
                GLib.idle_add(self.scan_label.set_text, f"Importing {done} of {total}…")
            result = (devicemod.import_photos(
                          library, to_copy, progress,
                          profile=self.settings.get("storage_profile",
                                                    "visually_lossless"),
                          video_profile=self.settings.get("storage_video_profile",
                                                          "original"))
                      if to_copy
                      else {"copied": [], "skipped": 0, "failed": 0,
                            "sources": [], "placed": []})

            def finish():
                self.scan_label.set_text("Adding to your library…")
                if result["placed"]:
                    self.indexer.scan([str(library.originals)], lambda p: None)
                ids = list(existing_ids)
                for path in result["placed"]:
                    row = self.catalog.photo_by_path(str(Path(path).resolve()))
                    if row is not None:
                        ids.append(row["id"])
                if album_id is not None and ids:
                    self.catalog.album_add(album_id, ids)
                    self._write_album_sidecar(album_id)
                self.scan_bar.set_visible(False)
                n = len(ids)
                msg = f"Imported {n} photo" + ("s" if n != 1 else "")
                if album_name:
                    msg += f" into \u201c{album_name}\u201d"
                if result["failed"]:
                    msg += f", {result['failed']} failed"
                self._show_toast(msg)
                self.refresh_sidebar()
                # Stay on the camera: what was just imported moves to
                # "Already Imported".
                if self._device is not None and self._scope == "device":
                    self._open_device(self._device.id)
                return False
            GLib.idle_add(finish)
        threading.Thread(target=work, daemon=True).start()

    def _new_device_records(self):
        already = getattr(self, "_device_imported", set()) or set()
        return [r for r in getattr(self, "_device_records", [])
                if r.get("fingerprint") not in already]

    def _fill_import_albums(self):
        albums = self.catalog.albums()
        self._import_album_ids = [None] + [a["id"] for a in albums]
        self._import_album.set_model(Gtk.StringList.new(
            ["Library"] + [a["name"] for a in albums]))
        self._import_album.set_selected(0)

    def _ask_delete_from_device(self, device, sources):
        """After import: delete from the camera, or keep."""
        n = len(sources)
        dialog = Adw.AlertDialog(
            heading=f"Delete {n} item" + ("s" if n != 1 else "")
                    + f" from \u201c{device.name}\u201d?",
            body="They are safely in your library. Deleting frees space on "
                 "the camera; it cannot be undone there.")
        dialog.add_response("keep", "Keep Items")
        dialog.add_response("delete", "Delete Items")
        dialog.set_response_appearance("delete",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("keep")

        def done(_d, response):
            if response != "delete":
                return

            def work():
                removed, failed = devicemod.delete_from_device(sources)
                msg = f"Deleted {removed} item" + ("s" if removed != 1 else "") \
                      + f" from {device.name}"
                if failed:
                    msg += f" ({failed} could not be deleted)"
                GLib.idle_add(self._show_toast, msg)
            threading.Thread(target=work, daemon=True).start()
        dialog.connect("response", done)
        dialog.present(self)

    def _show_toast(self, text):
        try:
            self.toasts.add_toast(Adw.Toast(title=text, timeout=5))
        except Exception:
            pass

    def _make_drop_target(self, row, album_id, folder_id):
        """Accept photos dropped on an album, and albums dropped on a folder."""
        target = Gtk.DropTarget.new(str, Gdk.DragAction.COPY
                                    | Gdk.DragAction.MOVE)

        def on_drop(_t, value, _x, _y):
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
                ids = [int(i) for i in
                       value[len(PhotoGrid.DRAG_PREFIX):].split(",") if i]
                if not ids:
                    return False
                self.catalog.album_add(album_id, ids)
                self._write_album_sidecar(album_id)
                self._refresh()
                return True
            if value.startswith("pika-album:"):
                if folder_id is None:
                    return False
                try:
                    dragged = int(value.split(":", 1)[1])
                except ValueError:
                    return False
                self.catalog.move_album_to_folder(dragged, folder_id)
                self._write_album_sidecar(dragged)
                self._refresh()
                return True
            return False

        def on_enter(_t, _x, _y):
            row.add_css_class("pika-drop-into")
            return Gdk.DragAction.COPY

        target.connect("drop", on_drop)
        target.connect("enter", on_enter)
        target.connect("leave", lambda *_: row.remove_css_class("pika-drop-into"))
        row.add_controller(target)

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
                      album_id=album["id"], indent=indent)

    def _on_sidebar_activated(self, _list, row):
        if row is None or self._syncing_sidebar:
            return
        folder_id = getattr(row, "_folder_id", None)
        if folder_id is not None:
            if folder_id in self._collapsed_folders:
                self._collapsed_folders.discard(folder_id)
            else:
                self._collapsed_folders.add(folder_id)
            self._save_folded_folders()
            self.refresh_sidebar()

    def _on_sidebar_selected(self, _list, row):
        if row is None or self._syncing_sidebar:
            return
        key = getattr(row, "_key", "library")
        if key.startswith("folder:"):
            return          # organisational only; activation toggles it
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
                              ("<Ctrl>z", "win.undo"),
                              ("<Ctrl><Shift>z", "win.redo"),
                              ("<Ctrl>e", "win.export"),
                              ("<Ctrl>comma", "win.preferences"),
                              ("<Ctrl>o", "win.add-folder"),
                              ("<Ctrl>r", "win.rescan"),
                              # One key for each view.
                              ("<Ctrl>1", "win.view-year"),
                              ("<Ctrl>2", "win.view-month"),
                              ("<Ctrl>3", "win.view-day"),
                              ("<Ctrl>4", "win.view-all"),
                              ("<Ctrl>f", "win.find"),
                              ("<Ctrl>n", "win.new-album"),
                              ("<Ctrl><Shift>n", "win.new-folder"),
                              ("<Ctrl><Alt>n", "win.new-smart-album"),
                              ("F11", "win.fullscreen"),
                              ("<Ctrl><Shift>f", "win.fullscreen"),
                              ("<Ctrl><Shift>a", "win.deselect"),
                              ("<Ctrl>l", "win.hide"),
                              ("<Ctrl>i", "win.info"),
                              # F2 renames, as in every Linux file manager.
                              ("F2", "win.rename-photo")):
            app.set_accels_for_action(action, [accel])
        app.set_accels_for_action("win.zoom-in",
                                  ["<Ctrl>plus", "<Ctrl>equal", "<Ctrl>KP_Add"])
        app.set_accels_for_action("win.zoom-out",
                                  ["<Ctrl>minus", "<Ctrl>KP_Subtract"])

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
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        name = Gdk.keyval_name(keyval) or ""
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
        if not ctrl and name == "Delete":
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
        name = Gdk.keyval_name(keyval) or ""
        mods = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
                        | Gdk.ModifierType.ALT_MASK)
        # Ctrl+A selects every photo shown. Taken in capture phase: in bubble
        # phase a focused photo tile or sidebar row used Ctrl+A for its own
        # "select all" first, and the grid never got it.
        if name.lower() == "a" and mods == Gdk.ModifierType.CONTROL_MASK:
            if (self._focus_takes_keys()
                    or self.stack.get_visible_child_name() != "grid"
                    or self.content_stack.get_visible_child_name() != "grid"):
                return False
            self.grid.select_all()
            return True
        if name not in self._CAPTURED:
            return False
        if name == "F11":
            # Registered as an accelerator too, but F11 never reached it on
            # this desktop; Ctrl+Shift+F did. Taken here directly.
            self._on_fullscreen()
            return True
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
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

    def _refresh(self):
        self.grid.refresh()
        if self.content_stack.get_visible_child_name() == "summary":
            self.summary.load(self.settings.get("group_by"), self.grid.filters())
        self.refresh_sidebar()
        self._mirror_state()

    # -- photo flow ------------------------------------------------------
    def _on_photo_activated(self, _grid, item):
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
        self.toasts.add_toast(Adw.Toast(title="Frame saved to your library",
                                        timeout=3))

    def _on_editor_closed(self, _editor):
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
        self.select_label.set_text(f"{n} selected" if n else "")

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
                f"Import {n} Selected" if n else
                (f"Import All New Items ({n_new})" if n_new else "All Items Imported"))
            self._import_btn.set_sensitive(bool(n or n_new))
        for key in ("favorite", "trash", "album"):
            self._bar_buttons[key].set_visible(not on_device)
        if on_device:
            self.action_bar.set_revealed(True)

        in_trash = self._scope == "trash"
        self._purge_btn.set_visible(in_trash and not on_device)
        if in_trash:
            none = not self.grid.selected_ids()
            self._purge_btn.set_label("Delete All…" if none else "Delete Permanently…")
        in_dupes = self._scope == "duplicates"
        self._merge_btn.set_visible(in_dupes and not on_device)
        if in_dupes:
            n = len(self.grid.selected_ids())
            self._merge_btn.set_label(f"Merge {n} Item" + ("s" if n != 1 else ""))
        trash_btn = self._bar_buttons["trash"]
        trash_btn.set_icon_name("edit-undo-symbolic" if in_trash
                                else "user-trash-symbolic")
        trash_btn.set_tooltip_text(
            ("Recover All" if not self.grid.selected_ids() else "Recover") if in_trash
            else ("Remove from this album" if self._scope == "album"
                  else "Move to Recently Deleted"))
        for key in ("favorite", "album"):
            self._bar_buttons[key].set_sensitive(not in_trash)

    def _on_merge_duplicates(self, _btn):
        ids = self.grid.selected_ids()
        if not ids:
            return
        result = self.catalog.merge_duplicates(ids)
        self._mirror_state()
        self._refresh()
        n = len(result["trashed"])
        if n:
            self._show_toast(f"Merged. {n} extra cop" + ("ies" if n != 1 else "y")
                        + " moved to Recently Deleted.")
        else:
            self._show_toast("Select at least two copies of the same photo to merge.")

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
            heading=f"Delete {len(ids)} photo" + ("s" if len(ids) != 1 else "")
                    + " permanently?",
            body="Piklin does not own these files - it indexes them "
                 "where they live.\n\n"
                 "“Remove from Library” forgets them here and leaves the "
                 "files on disk, untouched.\n\n"
                 "“Delete Files” erases the actual files. That cannot be "
                 "undone.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("forget", "Remove from Library")
        dialog.add_response("erase", "Delete Files")
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
        self.filter_btn.set_label(f"Filter ({len(active)})" if active else "Filter")
        self.grid.set_filters(active)
        self._sync_content_view()

    def _clear_filters(self):
        self._filters_syncing = True
        for chk in self._filter_checks.values():
            chk.set_active(False)
        self._filters_syncing = False
        self.filter_btn.set_label("Filter")
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
        albums = self.catalog.albums()
        dialog = Adw.AlertDialog(heading="Add to album",
                                 body=f"{len(ids)} photo"
                                      + ("s" if len(ids) != 1 else ""))
        names = [a["name"] for a in albums]
        combo = Gtk.DropDown.new_from_strings(names + ["New album…"])
        dialog.set_extra_child(combo)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("add", "Add")
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
                [("rename", "Rename…", None,
                  lambda: self._on_rename_folder(folder_id, name))],
                [("new-album", "New Album Here…", None,
                  lambda: self._on_new_album(folder_id=folder_id)),
                 ("new-folder", "New Folder Here…", None,
                  lambda: self._on_new_folder(folder_id))],
                [("move-out", f"Move Out of \u201c{parent['name']}\u201d", None,
                  lambda: self._move_out(folder_id=folder_id,
                                         to=parent["parent_id"]))]
                if parent is not None else [],
                [("delete", "Delete Folder…", None,
                  lambda: self._on_delete_folder(folder_id, name))],
            ]
        else:
            holder = self.catalog.q1(
                "SELECT f.id, f.name, f.parent_id FROM albums a "
                "JOIN folders f ON f.id=a.folder_id WHERE a.id=?", (album_id,))
            sections = [
                [("rename", "Rename…", None,
                  lambda: self._on_rename_album(album_id, name))],
                [("move-out", f"Move Out of \u201c{holder['name']}\u201d", None,
                  lambda: self._move_out(album_id=album_id,
                                         to=holder["parent_id"]))]
                if holder is not None else [],
                [("delete", "Delete Album…", None,
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
        dialog = Adw.AlertDialog(heading="Rename folder")
        entry = Gtk.Entry(text=current_name, activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("rename", "Rename")
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
        dialog = Adw.AlertDialog(heading="Rename album")
        entry = Gtk.Entry(text=current_name, activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("rename", "Rename")
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
            heading=f'Delete "{name}"?',
            body="This deletes the folder and everything organised "
                 "inside it - its albums and any folders nested in it. "
                 "Your photos themselves are never deleted; only the "
                 "folder and album organisation is removed.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
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
            heading=f'Delete "{name}"?',
            body="This removes the album. Your photos are not deleted "
                 "and stay exactly where they are.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
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
        dialog.connect("response", done)
        dialog.present(self)

    def _on_new_folder(self, parent_id=None):
        dialog = Adw.AlertDialog(heading="New folder",
                                 body="Give the folder a name.")
        entry = Gtk.Entry(placeholder_text="Folder name",
                          activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("create", "Create")
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
            name = entry.get_text().strip() or "Untitled Folder"
            self.catalog.create_folder(name, parent_id=parent_id)
            self.refresh_sidebar()
            self._mirror_state()
        dialog.connect("response", done)
        dialog.present(self)

    def _move_out(self, album_id=None, folder_id=None, to=None):
        if album_id is not None:
            self.catalog.move_album_to_folder(album_id, to)
            self._write_album_sidecar(album_id)
        elif folder_id is not None:
            self.catalog.move_folder(folder_id, to)
        self._mirror_state()
        self.refresh_sidebar()

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
            [("edit", "Edit Smart Album…", None,
              lambda: self._on_new_smart_album(smart_id=smart_id))],
            [("delete", "Delete Smart Album…", None,
              lambda: self._on_delete_smart_album(smart_id, name))],
        ])

    def _on_delete_smart_album(self, smart_id, name):
        dialog = Adw.AlertDialog(
            heading=f"Delete \u201c{name}\u201d?",
            body="This removes the Smart Album. No photos are deleted.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
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
        dialog = Adw.AlertDialog(heading="New album",
                                 body="Give the album a name.")
        entry = Gtk.Entry(placeholder_text="Album name",
                          activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("create", "Create")
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
            name = entry.get_text().strip() or "Untitled Album"
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
        import json
        row = self.catalog.q1("SELECT * FROM albums WHERE id=?", (album_id,))
        if row is None:
            return
        # The folder is recorded by uuid, not by id: ids are handed out
        # fresh when the database is rebuilt, so an id here would point
        # at whatever folder happened to land on that number.
        folder_uuid = None
        if row["folder_id"] is not None:
            frow = self.catalog.q1("SELECT uuid FROM folders WHERE id=?",
                                   (row["folder_id"],))
            folder_uuid = frow["uuid"] if frow else None
        payload = {"format": "pikalicious-album", "version": 1,
                   "uuid": row["uuid"], "name": row["name"],
                   "created_at": row["created_at"],
                   "folder_uuid": folder_uuid,
                   "photos": self.catalog.album_photo_paths(album_id)}
        safe = "".join(c if c.isalnum() or c in " -_" else "_"
                       for c in row["name"])[:80] or row["uuid"]
        try:
            self.library.albums.mkdir(parents=True, exist_ok=True)
            target = self.library.albums / f"{safe}.json"
            for existing in self.library.albums.glob("*.json"):
                if existing == target:
                    continue
                try:
                    data = json.loads(existing.read_text())
                except (OSError, ValueError):
                    continue
                if data.get("uuid") == row["uuid"]:
                    existing.unlink(missing_ok=True)
            target.write_text(json.dumps(payload, indent=2))
        except OSError:
            pass

    def _delete_album_sidecar(self, album_uuid):
        """Remove an album's JSON file when the album itself is deleted."""
        import json
        if not self.library.albums.exists():
            return
        for existing in self.library.albums.glob("*.json"):
            try:
                data = json.loads(existing.read_text())
            except (OSError, ValueError):
                continue
            if data.get("uuid") == album_uuid:
                existing.unlink(missing_ok=True)

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
                [("recover", "Recover", None, lambda: self._on_trash_button(None))],
                [("purge", "Delete Permanently…", "Delete", lambda: self._on_purge(None))],
            ])
            return
        marks = ",".join("?" * len(ids))
        all_fav = bool(self.catalog.scalar(
            f"SELECT MIN(favorite) FROM photos WHERE id IN ({marks})", ids, 0))
        count = "" if single else f" {len(ids)} Photos"
        self._show_context_menu(widget, x, y, [
            [("open", "Open", "space",
              lambda: self._on_photo_activated(self.grid, item)),
             ("rename", "Rename…", "F2",
              lambda: self._rename_photo_id(item.id))] if single else [],
            [("favorite", "Remove from Favourites" if all_fav else "Favourite",
              "period", lambda: self._on_bulk_favorite(None)),
             ("hide", ("Unhide" if self._scope == "hidden" else "Hide") + count,
              "<Ctrl>l", lambda: self._on_bulk_hide())],
            [("export", f"Export{count}…", "<Ctrl>e",
              lambda: self._on_bulk_export(None))],
            [("delete", f"Delete{count}", "Delete",
              lambda: self._on_bulk_trash(None))],
        ])

    # -- libraries -------------------------------------------------------
    def _on_open_library(self):
        dialog = Gtk.FileDialog(title="Open a Piklin Library")

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
                heading="Not a Piklin Library",
                body=f"“{root.name}” does not contain a library. Choose "
                     f"a library package, such as “Piklin Library.piklin”.")
            dlg.add_response("ok", "OK")
            dlg.present(self)
            return
        remember_library(root)
        app = self.get_application()
        app.relaunch_library = str(root)
        app.quit()

    # -- folders & scanning ---------------------------------------------
    def _on_add_folder(self):
        dialog = Gtk.FileDialog(title="Choose a folder of photos")

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
        self.scan_label.set_text("Scanning…")

        def progress(p):
            def apply():
                if p.phase in ("done", "cancelled"):
                    self.scan_bar.set_visible(False)
                    self._refresh()
                    return False
                self.scan_bar.set_visible(True)
                label = {"scanning": "Looking for photos",
                         "probing": "Reading photo details",
                         "thumbnails": "Making thumbnails"}.get(p.phase, p.phase)
                extra = f"  ·  {p.added} new" if p.added else ""
                self.scan_label.set_text(f"{label} — {p.done:,}"
                                         + (f" of {p.total:,}" if p.total else "")
                                         + extra)
                self.scan_progress.set_fraction(p.fraction)
                return False
            GLib.idle_add(apply)

        self.indexer.start(roots, progress, with_thumbnails=True)

    def _first_run(self):
        self.refresh_sidebar()
        self.grid.load("library")
        if not self.catalog.roots():
            self._show_welcome()
        else:
            self._start_scan(None)
        return False

    def _show_welcome(self):
        from ..paths import pictures_dir
        pictures = pictures_dir()
        dialog = Adw.AlertDialog(
            heading="Welcome to Piklin",
            body=f"Point it at a folder and it will build your library.\n\n"
                 f"Your photos are never moved or modified — edits are saved "
                 f"alongside them and your originals stay exactly as they are.")
        dialog.add_response("later", "Later")
        if pictures.is_dir():
            dialog.add_response("pictures", f"Use {pictures.name}")
            dialog.set_response_appearance("pictures",
                                           Adw.ResponseAppearance.SUGGESTED)
        dialog.add_response("choose", "Choose Folder…")

        def done(d, response):
            if response == "pictures":
                self._start_scan([str(pictures)])
            elif response == "choose":
                self._on_add_folder()
        dialog.connect("response", done)
        dialog.present(self)

    def _open_settings(self, page="general"):
        from .settings_dialog import SettingsDialog
        SettingsDialog(self, self.library, self.catalog, self.settings,
                       self.thumbs, page).present(self)

    def _on_about(self):
        from ..engine import tools as tools_mod
        from .. import imageio as iio_mod
        about = Adw.AboutDialog(
            application_name="Piklin",
            application_icon="piklin",
            version="1.0.0",
            developer_name="Vezzu Studio",
            website="https://vezzu.studio",
            copyright="© 2026 Vezzu Studio. All rights reserved.",
            comments=(
                f"Piklin was created by Vezzu Studio.\n\n"
                f"A photo and video library and editor for Linux. "
                f"{len(tools_mod.REGISTRY)} editing tools · "
                f"{len(iio_mod.supported_extensions())} file formats · "
                f"everything runs on this machine."),
            license_type=Gtk.License.CUSTOM,
            license=(
                "Piklin is proprietary software. © 2026 Vezzu Studio. "
                "All rights reserved.\n\n"
                "Your licence lets you install and use Piklin on your own "
                "computers. You may not copy, redistribute, resell or "
                "reverse-engineer it."))
        # Libraries Piklin ships, each under its own licence (full texts in
        # /usr/share/doc/piklin/third-party).
        for title, licence in (
                ("FFmpeg (LGPL build, with OpenH264, libvpx, Opus, dav1d)", Gtk.License.LGPL_2_1),
                ("PyAV", Gtk.License.BSD_3),
                ("OpenCV", Gtk.License.APACHE_2_0),
                ("NumPy", Gtk.License.BSD_3),
                ("Pillow, pi-heif, rawpy, miniaudio", Gtk.License.MIT_X11),
                ("Inter typeface", Gtk.License.CUSTOM)):
            about.add_legal_section(
                title, None, licence,
                "SIL Open Font License 1.1" if licence == Gtk.License.CUSTOM else None)
        about.present(self)

    def do_close_request(self):
        self.indexer.stop(timeout=2.0)
        self.thumbs.shutdown()
        self.editor.shutdown()
        self.video_editor.shutdown()
        self.viewer.shutdown()
        return False
