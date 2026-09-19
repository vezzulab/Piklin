"""GObject wrappers so catalog rows can drive GTK list widgets."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GObject, Gio, Gtk  # noqa: E402


class PhotoItem(GObject.Object):
    """One photo in the grid.

    Deliberately thin: a library of 80,000 photos means 80,000 of these,
    so it carries only what a tile draws plus the id needed to fetch the
    rest on demand.
    """
    __gtype_name__ = "PikaPhotoItem"

    def __init__(self, row):
        super().__init__()
        self.id = row["id"]
        self.path = row["path"]
        self.filename = row["filename"]
        self.width = row["width"]
        self.height = row["height"]
        self.taken_at = row["taken_at"]
        self.favorite = bool(row["favorite"])
        self.rating = row["rating"]
        self.edited = bool(row["edit_version"])
        self.thumb_state = row["thumb_state"]
        self.bytes = row["bytes"]
        self.duration = row["duration"] if "duration" in row.keys() else None
        self.has_live = bool(row["has_live"]) if "has_live" in row.keys() else False
        # the content fingerprint: identical copies share it (Duplicates)
        self.fingerprint = row["fingerprint"] if "fingerprint" in row.keys() else None
        # the name of the group the photo was placed in, if it was given one
        self.place_name = row["place_name"] if "place_name" in row.keys() else None
        self.texture = None            # cached Gdk.Texture once loaded
        self.selected = False

    @property
    def aspect(self) -> float:
        if self.width and self.height:
            return self.width / self.height
        return 1.0

    @property
    def is_video(self) -> bool:
        from ..video import is_video
        return is_video(self.path)

    @property
    def missing(self) -> bool:
        # The catalog's flag can be stale (a scan that could not see the
        # file, a drive plugged back in). A photo that is on disk is never
        # drawn as missing, whatever the flag says - asked only for flagged
        # photos, so a normal grid costs no extra disk access.
        if self.thumb_state != 3:
            return False
        import os
        return not os.path.exists(self.path)


class AlbumItem(GObject.Object):
    __gtype_name__ = "PikaAlbumItem"

    def __init__(self, row):
        super().__init__()
        self.id = row["id"]
        self.name = row["name"]
        self.count = row["n"]
        self.cover_path = row["cover_path"]


class SidebarItem(GObject.Object):
    __gtype_name__ = "PikaSidebarItem"

    def __init__(self, key, label, icon, count=0, album_id=None,
                 is_header=False):
        super().__init__()
        self.key = key
        self.label = label
        self.icon = icon
        self.count = count
        self.album_id = album_id
        self.is_header = is_header


class DeviceItem(GObject.Object):
    """A photo sitting on a camera or card, not in the library.

    Deliberately shaped like PhotoItem so the grid can draw it without
    caring which it has - but it carries no database id, because it has
    no row: nothing on a device is in the catalog until it is imported.
    """
    __gtype_name__ = "PikaDeviceItem"

    def __init__(self, index, record):
        super().__init__()
        self.id = -(index + 1)          # negative: never a real photo id
        self.record = record
        self.path = record.get("path", "")
        # A photo still on a Mac's camera or phone is drawn from a small
        # preview until it is copied off (see imagecapture.py).
        self.thumb_path = record.get("preview") or self.path
        self.filename = record.get("filename", "")
        self.width = record.get("width", 0)
        self.height = record.get("height", 0)
        self.taken_at = record.get("taken_at")
        self.bytes = record.get("bytes", 0)
        self.duration = record.get("duration")
        self.has_live = False
        self.favorite = False
        self.rating = 0
        self.edited = False
        self.thumb_state = 0
        self.texture = None
        self.selected = False
        self.already_imported = False

    @property
    def is_video(self) -> bool:
        from ..video import is_video
        return is_video(self.path)

    @property
    def aspect(self) -> float:
        if self.width and self.height:
            return self.width / self.height
        return 1.0

    @property
    def missing(self) -> bool:
        return False
