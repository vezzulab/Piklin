"""Is there a newer Piklin, and installing it.

Piklin asks GitHub for its latest release - at most once a day, a few
seconds after it opens, and only while updates are on in Preferences. The
request carries nothing about the person or their photos: it is the same
public page anyone can open in a browser.

An installed .deb updates itself: /usr/lib/piklin/piklin-update, run through
pkexec, downloads the new version, installs it only if it is signed with
Vezzu Studio's release key, and Piklin then reopens. Anywhere that helper is
missing (running from source, an old package) Piklin points to the download.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO = "vezzulab/Piklin"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
DAY = 24 * 60 * 60
HELPER = "/usr/lib/piklin/piklin-update"
INSTALLED_CODE = "/usr/share/piklin/"
LAUNCHER = "/usr/bin/piklin"


class UpdateError(Exception):
    """Installing failed. ``kind``: cancelled, not-newer, download,
    verification or install."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(detail or kind)
        self.kind = kind


def can_install_itself() -> bool:
    """True for Piklin installed from its .deb, carrying the update helper."""
    here = str(Path(__file__).resolve())
    return (here.startswith(INSTALLED_CODE) and os.access(HELPER, os.X_OK)
            and shutil.which("pkexec") is not None)


def install(version: str) -> None:
    """Download, check and install ``version``. Blocks for a while: call it
    off the UI thread. Raises UpdateError when it did not install."""
    kinds = {3: "not-newer", 4: "download", 5: "verification", 6: "install",
             126: "cancelled", 127: "cancelled"}
    try:
        proc = subprocess.run(["pkexec", HELPER, version], capture_output=True,
                              text=True, timeout=45 * 60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError("install", str(exc))
    if proc.returncode != 0:
        raise UpdateError(kinds.get(proc.returncode, "install"),
                          (proc.stderr or proc.stdout).strip())


@dataclass
class Release:
    version: str
    url: str
    notes: str = ""


def parse_version(text: str) -> tuple[int, ...]:
    """ "v1.0.3" -> (1, 0, 3); anything unreadable sorts as oldest."""
    numbers = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in numbers[:4]) or (0,)


def is_newer(latest: str, current: str) -> bool:
    return parse_version(latest) > parse_version(current)


def latest_release(current: str, timeout: float = 10.0) -> Release:
    """The newest published release. Raises OSError when GitHub can't be
    reached."""
    req = urllib.request.Request(RELEASES_API, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"Piklin/{current}",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    tag = data.get("tag_name") or data.get("name") or ""
    return Release(version=tag.lstrip("vV"), url=data.get("html_url") or RELEASES_PAGE,
                   notes=data.get("body") or "")


# -- remembered per person, not per library -----------------------------------
def _state_file() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "piklin" / "updates.json"


def load_state() -> dict:
    try:
        data = json.loads(_state_file().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(**values) -> None:
    state = load_state()
    state.update(values)
    path = _state_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state))
        tmp.replace(path)
    except OSError:
        pass


def enabled() -> bool:
    return bool(load_state().get("enabled", True))


def due(now: float | None = None) -> bool:
    """Checking is allowed and the last check was more than a day ago."""
    state = load_state()
    if not state.get("enabled", True):
        return False
    return (now or time.time()) - float(state.get("last_check") or 0) >= DAY
