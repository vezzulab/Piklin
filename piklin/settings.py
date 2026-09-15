"""User settings, stored with the library.

Settings live in the library folder rather than ~/.config so that moving
or backing up the library carries its configuration with it - which is
the same reason the catalog lives there.  A settings file that fails to
parse is replaced by defaults rather than stopping the app.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    # -- storage & compression ------------------------------------------
    # What happens to files brought into the library.
    "import_policy": "reference",      # reference | copy | move
    # How photos imported from a camera are stored in the library. Smaller
    # without visible loss by default; "original" keeps camera files as-is.
    "storage_profile": "visually_lossless",
    # Videos from a camera: kept as they are unless the user asks for
    # smaller files (re-encoding a video always costs some detail).
    "storage_video_profile": "original",   # original | h264
    # Videos copied in as they are, still to be made smaller (paths).
    "videos_to_shrink": [],
    # Sizes of upright phone videos measured again once (see the window).
    "video_sizes_checked_v2": False,
    # Measured again once more, for videos whose pixels aren't square. Missing
    # from this list, the note was dropped at every launch and every video was
    # measured again each time Piklin opened, with a jump in memory.
    "video_sizes_checked_v3": False,
    "export_profile": "visually_lossless",
    "export_format": "keep",           # keep | jpeg | webp | avif | png
    "export_strip_metadata": False,
    # Export options. Every key a dialog saves must be
    # listed here: load() keeps only known keys, so a missing one was
    # silently dropped on the next launch and the choice forgotten.
    "export_include_location": False,  # location off unless chosen
    "export_size": 0,                  # 0 full, 1 large, 2 medium, 3 small
    "export_naming": "filename",       # filename | title | sequential
    "export_subfolder": "none",        # none | day
    "export_video_format": "original", # original | mp4 | webm | gif
    "export_video_size": 0,            # 0 original, 1 4K, 2 1080p, 3 720p, 4 480p
    # Recompressing files already in the library is opt-in and always
    # keeps a recoverable copy first.
    "optimize_storage": False,
    "optimize_min_saving_percent": 15.0,

    # -- appearance ------------------------------------------------------
    "theme": "light",                   # auto | light | dark
    "grid_size": 200,                  # thumbnail edge in px
    "grid_spacing": 4,
    "show_filenames": False,
    "group_by": "day",                 # none | day | month | year
    "grid_aspect": "square",           # square | original (Aspect Ratio)
    "sidebar_width": 280,              # px, dragged wider or narrower
    "sidebar_collapsed": [],           # sidebar sections folded away
    "sidebar_collapsed_folders": [],   # album folders folded away, by uuid
    # Where this library was when last opened; a different current
    # location means it was moved and its stored paths need updating.
    "library_root_last": "",

    # -- performance -----------------------------------------------------
    "preview_max_side": 1600,          # interactive edit resolution
    "drag_max_side": 900,              # while a slider is moving
    "thumb_workers": 0,                # 0 = choose from CPU count
    "scan_follow_symlinks": False,

    # -- editing ---------------------------------------------------------
    "auto_enhance_on_open": False,
    "edit_autosave": True,

    # -- remotes ---------------------------------------------------------
    # List of dicts; see remote.py for the shape.
    "remotes": [],
    "remote_autosync": False,
    # Days a replaced file stays in .piklin-versions on the destination.
    "backup_keep_versions_days": 30,
    # The first-launch question about backups was answered.
    "backup_onboarding_done": False,
    # The first steps (language, library, photos, backup) were answered or
    # put off: they are not shown again.
    "onboarding_done": False,

    # -- privacy ---------------------------------------------------------
    "face_detection": True,            # local only; used by portrait tools
}


class Settings:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: dict[str, Any] = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text())
            if isinstance(raw, dict):
                # Merge rather than replace, so a file written by an older
                # version does not lose keys added since.
                for k, v in raw.items():
                    if k in DEFAULTS:
                        self._data[k] = v
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            try:
                tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True))
                tmp.replace(self.path)
            except OSError:
                pass

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any, save: bool = True) -> None:
        self._data[key] = value
        if save:
            self.save()

    def update(self, values: dict, save: bool = True) -> None:
        self._data.update(values)
        if save:
            self.save()

    def reset(self) -> None:
        self._data = dict(DEFAULTS)
        self.save()

    def as_dict(self) -> dict:
        return dict(self._data)

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def __setitem__(self, key: str, value: Any) -> None:
        self.set(key, value)
