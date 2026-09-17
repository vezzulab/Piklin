"""Create: the panel at the right, where photos are gathered and what can
be made out of them is offered.

It is a way in, not a workshop. Its one idea is the gathering: photos are
added to it from wherever they are, an album at a time, a day at a time,
and they stay there while you go and look somewhere else. A creation is
rarely made of one album - a child's year is spread over a dozen - and a
selection that is lost the moment you open another album cannot make one.

Choosing what to make opens the whole window to work in.
"""
from __future__ import annotations

from datetime import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from ..i18n import N_, _, ngettext

PANEL_WIDTH = 320
TRAY_TILE = 52

# What can be made, in the order it is offered, with the smallest and
# largest number of photos each one is worth doing with.
KINDS = (
    ("collage", N_("Collage"),
     N_("Your photos side by side on one picture, each at its own shape."),
     2, 60),
    ("poster", N_("Poster"),
     N_("One picture to print and frame, with room for a few words."),
     1, 12),
)


def span_of(photos) -> str:
    """"2019–2024", or one date when they are all from the same day."""
    times = sorted(p.get("taken_at") for p in photos if p.get("taken_at"))
    if not times:
        return ""
    first = datetime.fromtimestamp(times[0])
    last = datetime.fromtimestamp(times[-1])
    if first.year != last.year:
        return f"{first.year}–{last.year}"
    if (first.month, first.day) == (last.month, last.day):
        return first.strftime("%d/%m/%Y")
    return first.strftime("%Y")


def as_photo(item) -> dict:
    """One photo as this panel keeps it, from a grid item or a catalog row."""
    get = (item.get if isinstance(item, dict)
           else lambda k, d=None: getattr(item, k, d))
    return {"id": int(get("id") or 0), "path": str(get("path") or ""),
            "width": int(get("width") or 0), "height": int(get("height") or 0),
            "taken_at": get("taken_at")}


class CreatePanel(Gtk.Box):
    """The right-hand panel. ``window`` opens what is chosen here."""

    __gtype_name__ = "PikaCreatePanel"

    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.window = window
        self.add_css_class("pika-create-panel")
        self.set_size_request(PANEL_WIDTH, -1)
        self._rows = []
        self.photos: list[dict] = []

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                       margin_top=14, margin_bottom=6,
                       margin_start=16, margin_end=10)
        title = Gtk.Label(label=_("Create"), xalign=0.0, hexpand=True)
        title.add_css_class("heading")
        head.append(title)
        close = Gtk.Button(icon_name="window-close-symbolic",
                           tooltip_text=_("Hide Create"))
        close.add_css_class("flat")
        close.connect("clicked", lambda *_a: window.show_create_panel(False))
        head.append(close)
        self.append(head)

        self.summary = Gtk.Label(xalign=0.0, wrap=True, margin_start=16,
                                 margin_end=16, margin_bottom=8)
        self.summary.add_css_class("pika-dim")
        self.append(self.summary)

        # The gathered photos, and a way to take one back out.
        self.tray = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                                homogeneous=True, row_spacing=6,
                                column_spacing=6, margin_start=16,
                                margin_end=16, margin_bottom=8,
                                min_children_per_line=4,
                                max_children_per_line=5)
        self.tray_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.tray,
            min_content_height=64, max_content_height=232, propagate_natural_height=True)
        self.append(self.tray_scroll)

        self.clear_btn = Gtk.Button(label=_("Empty the tray"))
        self.clear_btn.add_css_class("flat")
        self.clear_btn.set_margin_start(16)
        self.clear_btn.set_margin_end(16)
        self.clear_btn.set_margin_bottom(10)
        self.clear_btn.connect("clicked", lambda *_a: self.clear())
        self.append(self.clear_btn)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      margin_start=12, margin_end=12, margin_bottom=12)
        for kind, label, blurb, least, most in KINDS:
            box.append(self._kind_row(kind, label, blurb, least, most))
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                    vexpand=True, child=box)
        self.append(scroll)

        self.mine = Gtk.Button()
        self.mine.add_css_class("flat")
        self.mine.connect("clicked", lambda *_a: window.open_scope("creations"))
        self.mine.set_margin_start(12)
        self.mine.set_margin_end(12)
        self.mine.set_margin_bottom(12)
        self.append(self.mine)

        # Photos dragged from anywhere in the library land here too, which
        # is the shortest way to gather from several albums.
        drop = Gtk.DropTarget.new(str, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_drop)
        self.add_controller(drop)

        self._restore()
        self.refresh()

    # ==================================================================
    # the gathered photos
    # ==================================================================
    def _restore(self):
        """The tray as it was left: gathering photos for a collage can take
        a few evenings, and closing Piklin must not undo it."""
        ids = self.window.settings.get("create_tray") or []
        if not ids:
            return
        rows = {}
        for photo_id in ids:
            row = self.window.catalog.photo(photo_id)
            if row is not None and row["trashed_at"] is None:
                rows[photo_id] = as_photo(dict(row))
        self.photos = [rows[i] for i in ids if i in rows]

    def _remember(self):
        self.window.settings.set("create_tray", [p["id"] for p in self.photos])

    def gather(self, photos) -> int:
        """Add these to the tray, ignoring the ones already in it and
        anything that is not a photograph. Returns how many were new."""
        from ..video import is_video
        have = {p["id"] for p in self.photos}
        added = 0
        for item in photos:
            photo = as_photo(item)
            if not photo["id"] or photo["id"] in have or is_video(photo["path"]):
                continue
            have.add(photo["id"])
            self.photos.append(photo)
            added += 1
        if added:
            self._remember()
            self.refresh()
        return added

    def add_selection(self):
        items = self.window.grid.selected_items()
        if not items:
            self.window.show_toast(
                _("Choose photos in your library, then press +"))
            return
        added = self.gather(items)
        skipped = len(items) - added
        if added and skipped:
            self.window.show_toast(ngettext(
                "{count} photo added; {skipped} was already there or is a video",
                "{count} photos added; {skipped} were already there or are videos",
                added).format(count=added, skipped=skipped))
        elif not added and items:
            self.window.show_toast(_("Those are already in the tray"))

    def take_out(self, photo_id: int):
        self.photos = [p for p in self.photos if p["id"] != photo_id]
        self._remember()
        self.refresh()

    def clear(self):
        self.photos = []
        self._remember()
        self.refresh()

    def for_creation(self) -> list[dict]:
        """What a creation would be made of: the tray, or what is selected
        when the tray is empty, so choosing a few photos and making a
        collage of them still takes one click."""
        if self.photos:
            return list(self.photos)
        return [as_photo(i) for i in self.window.grid.selected_items()]

    # ==================================================================
    # the look of it
    # ==================================================================
    def _kind_row(self, kind, label, blurb, least, most):
        button = Gtk.Button()
        button.add_css_class("card")
        button.add_css_class("pika-create-kind")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3,
                        margin_top=10, margin_bottom=10,
                        margin_start=12, margin_end=12)
        name = Gtk.Label(label=_(label), xalign=0.0)
        name.add_css_class("heading")
        inner.append(name)
        why = Gtk.Label(label=_(blurb), xalign=0.0, wrap=True)
        why.add_css_class("pika-dim")
        why.add_css_class("caption")
        inner.append(why)
        note = Gtk.Label(xalign=0.0, wrap=True, visible=False)
        note.add_css_class("caption")
        note.add_css_class("pika-dim")
        inner.append(note)
        button.set_child(inner)
        button.connect("clicked", lambda *_a, k=kind: self._open(k))
        self._rows.append((button, note, kind, least, most))
        return button

    def _add_tile(self):
        """The empty box with a + in it: where photos go in.

        A place to put things reads as a place to put things; a button
        labelled "add" has to be read first. Photos dragged from the
        library land on it too.
        """
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                      halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        box.append(Gtk.Image(icon_name="list-add-symbolic", pixel_size=22))
        button = Gtk.Button(child=box)
        button.add_css_class("flat")
        button.add_css_class("pika-tray-add")
        button.set_size_request(TRAY_TILE, TRAY_TILE)
        button.set_tooltip_text(_("Add the photos you have chosen"))
        button.connect("clicked", lambda *_a: self.add_selection())
        return button

    def _tray_tile(self, photo):
        from .grid import _cached_texture, thumb_texture
        from .tile import PhotoTile
        from ..thumbs import GRID_SIZE
        tile = PhotoTile(TRAY_TILE, radius=7.0)
        button = Gtk.Button(child=tile, tooltip_text=_("Take out of the tray"))
        button.add_css_class("flat")
        button.add_css_class("pika-tray-tile")
        button.connect("clicked", lambda *_a, i=photo["id"]: self.take_out(i))
        hit = _cached_texture(photo["path"])
        if hit is not None:
            tile.set_paintable(hit[1])
            return button
        need = TRAY_TILE * max(1, self.get_scale_factor())

        def done(thumb):
            if thumb is None:
                return
            try:
                texture = thumb_texture(thumb, need)
            except Exception:
                return
            GLib.idle_add(lambda: (tile.set_paintable(texture), False)[1])
        self.window.thumbs.request(photo["path"], GRID_SIZE, done)
        return button

    def _fill_tray(self):
        child = self.tray.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.tray.remove(child)
            child = nxt
        for photo in self.photos:
            self.tray.append(self._tray_tile(photo))
        self.tray.append(self._add_tile())
        self.clear_btn.set_visible(bool(self.photos))

    def refresh(self, *_a):
        """Say what has been gathered, what is selected, and which
        creations that is enough for."""
        selected = self.window.grid.selected_items() if self.window.grid else []
        photos = self.for_creation()
        n = len(photos)
        self._fill_tray()

        if self.photos:
            span = span_of(self.photos)
            text = ngettext("{count} photo in the tray", "{count} photos in the tray",
                            len(self.photos)).format(count=len(self.photos))
            self.summary.set_text(text + (f"  ·  {span}" if span else ""))
        elif selected:
            span = span_of([as_photo(i) for i in selected])
            text = ngettext("{count} photo chosen", "{count} photos chosen",
                            len(selected)).format(count=len(selected))
            self.summary.set_text(text + (f"  ·  {span}" if span else ""))
        else:
            self.summary.set_text(_(
                "Gather photos here from any album or any day - they stay while "
                "you look somewhere else - then pick what to make."))

        if selected and self.photos:
            self.summary.set_text(self.summary.get_text() + "  ·  " + ngettext(
                "+ adds {count} more", "+ adds {count} more",
                len(selected)).format(count=len(selected)))

        for button, note, _kind, least, most in self._rows:
            if n == 0:
                button.set_sensitive(False)
                note.set_visible(False)
            elif n < least:
                button.set_sensitive(False)
                note.set_text(ngettext("Needs at least {count} photo",
                                       "Needs at least {count} photos",
                                       least).format(count=least))
                note.set_visible(True)
            elif n > most:
                # Still allowed: it is their photo set, and a poster of
                # twenty is a choice, not a mistake. It just says so.
                button.set_sensitive(True)
                note.set_text(_("Works best with {count} or fewer").format(count=most))
                note.set_visible(True)
            else:
                button.set_sensitive(True)
                note.set_visible(False)

        try:
            from .. import creations
            made = creations.count(self.window.catalog)
        except Exception:
            made = 0
        self.mine.set_label(ngettext("{count} creation you made",
                                     "{count} creations you made", made
                                     ).format(count=made) if made
                            else _("Nothing made yet"))
        self.mine.set_sensitive(bool(made))

    # ==================================================================
    # doing something with them
    # ==================================================================
    def _open(self, kind):
        self.window.open_creation(kind, self.for_creation())

    def _on_drop(self, _target, value, _x, _y):
        from .grid import PhotoGrid
        text = value if isinstance(value, str) else ""
        if not text.startswith(PhotoGrid.DRAG_PREFIX):
            return False
        ids = []
        for part in text[len(PhotoGrid.DRAG_PREFIX):].split(","):
            try:
                ids.append(int(part))
            except ValueError:
                continue
        rows = [self.window.catalog.photo(i) for i in ids]
        added = self.gather([dict(r) for r in rows if r is not None])
        if added:
            self.window.show_toast(ngettext(
                "{count} photo gathered", "{count} photos gathered",
                added).format(count=added))
        return True
