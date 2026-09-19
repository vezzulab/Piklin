"""Finding where a place is from what it is called.

Putting a pin on the exact spot by hand is fiddly; typing "Colonial Zone,
Santo Domingo, Dominican Republic" is not. This turns those words into
coordinates by asking OpenStreetMap's Nominatim, which is free and needs
no account or key.

What is sent is only the words somebody typed into the search boxes. No
photo, no filename, no coordinate from the library and nothing about
who is asking goes with it. If the network is not there, or the answer
does not come, the search falls back to the list of towns Piklin carries
(see places.py), which knows every town over about fifteen thousand
people - enough to land a trip in the right city with no connection.

Nominatim's usage policy allows about one request a second, wants the
application to say who it is, and does not want a search on every
keystroke; the dialog searches when asked, and this module spaces out
whatever it is given.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .places import Places

ENDPOINT = "https://nominatim.openstreetmap.org/search"
TIMEOUT = 8.0
# Nominatim's policy: at most one request a second.
MIN_GAP = 1.1

_gap_lock = threading.Lock()
_last = [0.0]


@dataclass
class Found:
    """One answer: what to call it, where it is, and how it was found."""
    title: str
    detail: str
    lat: float
    lon: float
    online: bool = True


def _wait_turn() -> None:
    with _gap_lock:
        wait = _last[0] + MIN_GAP - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.monotonic()


def _title_of(item: dict) -> str:
    """The most specific name Nominatim has for a result."""
    name = item.get("name")
    if name:
        return name
    return (item.get("display_name") or "").split(",")[0].strip()


def search_online(query: str, language: str = "en", limit: int = 6) -> list[Found]:
    """Ask Nominatim. Raises OSError when it cannot be reached."""
    from .app import VERSION
    params = urllib.parse.urlencode({
        "q": query, "format": "jsonv2", "limit": limit,
        "addressdetails": 0, "accept-language": language,
    })
    request = urllib.request.Request(
        f"{ENDPOINT}?{params}",
        headers={"User-Agent": f"Piklin/{VERSION} (+https://vezzu.studio)",
                 "Accept": "application/json"})
    _wait_turn()
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:
            items = json.loads(resp.read().decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise OSError(str(exc)) from exc
    found = []
    for item in items:
        try:
            lat, lon = float(item["lat"]), float(item["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        display = item.get("display_name") or ""
        title = _title_of(item)
        rest = display.split(",", 1)[1].strip() if "," in display else ""
        found.append(Found(title, rest, lat, lon, True))
    return found


def search_offline(places: Places, country: str, city: str,
                   place: str) -> list[Found]:
    """The towns Piklin carries, matched by name.

    A specific place - a beach, a hotel - is not on that list, so the
    answer is the town it is in, which the caller can then refine.
    """
    name = city or place
    if not name:
        if not country:
            return []
        # A country alone: its biggest towns are the nearest thing to it.
        name = country
    towns = places.find(name, country if city or place else "")
    return [Found(t.name, ", ".join(x for x in (t.region, t.country) if x),
                  t.lat, t.lon, False) for t in towns]


def search(places: Places, country: str, city: str, place: str,
           language: str = "en") -> tuple[list[Found], bool]:
    """Look up ``place`` in ``city`` in ``country``, any of them optional
    except that something must be given.

    Returns the answers and whether the network was used. Words are
    joined from the most specific to the most general, the order a
    postal address is written in, which is what Nominatim reads best.
    """
    words = [w.strip() for w in (place, city, country) if w and w.strip()]
    if not words:
        return [], False
    try:
        found = search_online(", ".join(words), language)
        if found:
            return found, True
    except OSError:
        pass
    return search_offline(places, country.strip(), city.strip(), place.strip()), False
