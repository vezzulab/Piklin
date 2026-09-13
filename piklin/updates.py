"""Is there a newer Piklin?

Piklin asks GitHub for its latest release - at most once a day, a few
seconds after it opens, and only while "Check for updates" is on in
Preferences. The request carries nothing about the person or their photos:
it is the same public page anyone can open in a browser. A .deb can't
install itself without an administrator password, so Piklin points to the
download instead.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO = "vezzulab/Piklin"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
DAY = 24 * 60 * 60


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
