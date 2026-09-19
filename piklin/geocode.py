"""Finding where a place is from what it is called.

Putting a pin on the exact spot by hand is fiddly; typing "Colonial Zone,
Santo Domingo, Dominican Republic" is not. This turns those words into
coordinates by asking OpenStreetMap's Nominatim, which is free and needs
no account or key.

What is sent is only the words somebody typed into the search boxes - to
OpenStreetMap's Nominatim and to Photon, a second search over the same map
data that finds names Nominatim misses. No photo, no filename, no
coordinate from the library and nothing about who is asking goes with it.
A place that only Google Maps knows can be pasted as a link or as
coordinates; the link is read here, and only a shortened one is opened (to
see where it leads). If the network is not there, or the answer
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
import re
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .i18n import N_, _
from .places import Places

ENDPOINT = "https://nominatim.openstreetmap.org/search"
PHOTON = "https://photon.komoot.io/api/"
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
    kind: str = ""          # what sort of place: "Museum", "Neighbourhood"...


def _wait_turn() -> None:
    with _gap_lock:
        wait = _last[0] + MIN_GAP - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.monotonic()


_GAP_PHOTON = threading.Lock()
_last_photon = [0.0]

# What OpenStreetMap calls a kind of place, in words a person would use.
KINDS = {
    "city": N_("City"), "town": N_("Town"), "village": N_("Village"), "hamlet": N_("Hamlet"),
    "suburb": N_("Neighbourhood"), "neighbourhood": N_("Neighbourhood"),
    "quarter": N_("Neighbourhood"), "city_district": N_("District"),
    "district": N_("District"), "borough": N_("District"), "county": N_("County"),
    "state": N_("State"), "country": N_("Country"), "island": N_("Island"),
    "museum": N_("Museum"), "gallery": N_("Gallery"), "attraction": N_("Attraction"),
    "viewpoint": N_("Viewpoint"), "monument": N_("Monument"), "memorial": N_("Memorial"),
    "castle": N_("Castle"), "ruins": N_("Ruins"), "theme_park": N_("Theme park"),
    "zoo": N_("Zoo"), "park": N_("Park"), "garden": N_("Garden"), "beach": N_("Beach"),
    "peak": N_("Mountain"), "volcano": N_("Volcano"), "water": N_("Lake"), "bay": N_("Bay"),
    "restaurant": N_("Restaurant"), "cafe": N_("Café"), "fast_food": N_("Fast food"),
    "bar": N_("Bar"), "pub": N_("Pub"), "hotel": N_("Hotel"), "hostel": N_("Hostel"),
    "guest_house": N_("Guest house"), "apartments": N_("Apartments"),
    "supermarket": N_("Supermarket"), "mall": N_("Shopping centre"),
    "marketplace": N_("Market"), "school": N_("School"), "university": N_("University"),
    "college": N_("College"), "library": N_("Library"), "hospital": N_("Hospital"),
    "place_of_worship": N_("Place of worship"), "church": N_("Church"),
    "cathedral": N_("Cathedral"), "mosque": N_("Mosque"), "temple": N_("Temple"),
    "stadium": N_("Stadium"), "sports_centre": N_("Sports centre"),
    "station": N_("Station"), "stop": N_("Stop"), "aerodrome": N_("Airport"),
    "airport": N_("Airport"), "bus_station": N_("Bus station"), "harbour": N_("Harbour"),
    "townhall": N_("Town hall"), "theatre": N_("Theatre"), "cinema": N_("Cinema"),
    "commercial": N_("Business"), "office": N_("Office"), "residential": N_("Street"),
    "unclassified": N_("Street"), "tertiary": N_("Street"), "secondary": N_("Street"),
    "primary": N_("Street"), "house": N_("House"), "yes": "",
}


def kind_label(kind: str) -> str:
    """A kind of place as it is shown: translated when Piklin knows it."""
    kind = (kind or "").strip()
    if not kind:
        return ""
    words = KINDS.get(kind)
    if words is None:
        words = kind.replace("_", " ").capitalize()
    return _(words) if words else ""


def _title_of(item: dict) -> str:
    """The most specific name Nominatim has for a result."""
    name = item.get("name")
    if name:
        return name
    return (item.get("display_name") or "").split(",")[0].strip()


def _detail(address: dict, title: str) -> str:
    """Where a place is, briefly: its neighbourhood, town, state and country.
    The street number and postcode that Nominatim's own line is full of are
    what made three different Paris results look identical."""
    def first(*keys):
        for key in keys:
            if address.get(key):
                return address[key]
        return ""
    parts = [first("suburb", "neighbourhood", "quarter", "city_district", "district"),
             first("city", "town", "village", "hamlet", "municipality", "county"),
             first("state", "region", "province"),
             address.get("country", "")]
    out: list[str] = []
    for part in parts:
        if part and part != title and part not in out:
            out.append(part)
    return ", ".join(out)


def search_online(query: str, language: str = "en", limit: int = 8) -> list[Found]:
    """Ask Nominatim. Raises OSError when it cannot be reached."""
    from .app import VERSION
    params = urllib.parse.urlencode({
        "q": query, "format": "jsonv2", "limit": limit,
        "addressdetails": 1, "accept-language": language,
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
        title = _title_of(item)
        address = item.get("address") or {}
        detail = _detail(address, title)
        if not detail:
            display = item.get("display_name") or ""
            detail = display.split(",", 1)[1].strip() if "," in display else ""
        # "museum" says more than the "tourism" it is filed under; the
        # broad types of towns and boundaries say nothing, so those use
        # what the place is in its address.
        kind = item.get("type") or ""
        if kind in ("", "yes", "administrative", "boundary", "house", "residential"):
            kind = item.get("addresstype") or kind
        found.append(Found(title, detail, lat, lon, True, kind_label(kind)))
    return found


def search_photon(query: str, language: str = "en", limit: int = 8) -> list[Found]:
    """Ask Photon, which searches the same OpenStreetMap data but finds names
    and half-typed words that Nominatim does not. Raises OSError."""
    from .app import VERSION
    lang = language if language in ("en", "de", "fr", "it") else "en"
    params = urllib.parse.urlencode({"q": query, "limit": limit, "lang": lang})
    request = urllib.request.Request(
        f"{PHOTON}?{params}",
        headers={"User-Agent": f"Piklin/{VERSION} (+https://vezzu.studio)",
                 "Accept": "application/json"})
    with _GAP_PHOTON:
        wait = _last_photon[0] + MIN_GAP - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_photon[0] = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise OSError(str(exc)) from exc
    found = []
    for feature in data.get("features") or []:
        props = feature.get("properties") or {}
        try:
            lon, lat = feature["geometry"]["coordinates"][:2]
        except (KeyError, TypeError, ValueError):
            continue
        title = props.get("name") or props.get("street") or ""
        if not title:
            continue
        address = {"suburb": props.get("district"), "city": props.get("city"),
                   "state": props.get("state"), "country": props.get("country")}
        found.append(Found(title, _detail(address, title), float(lat), float(lon),
                           True, kind_label(props.get("osm_value") or "")))
    return found


def _same(a: Found, b: Found) -> bool:
    """Two answers for one place: the same name within a few hundred metres,
    or the same name and the same surroundings."""
    from .ui.list_tools import fold
    if fold(a.title) != fold(b.title):
        return False
    close = abs(a.lat - b.lat) < 0.004 and abs(a.lon - b.lon) < 0.004
    return close or fold(a.detail) == fold(b.detail)


def merge(*lists: list[Found]) -> list[Found]:
    """The answers of several searches in one list, each place once, in the
    order they were given - and where two describe the same place, the one
    that says what it is."""
    out: list[Found] = []
    for answers in lists:
        for item in answers:
            for i, seen in enumerate(out):
                if _same(seen, item):
                    if not seen.kind and item.kind:
                        out[i] = item
                    break
            else:
                out.append(item)
    return out


# -- a place pasted from somewhere else ---------------------------------------
_NUM = r"[-+]?\d{1,3}(?:\.\d+)?"
_DMS = re.compile(
    r"(\d{1,3})\s*[°º]\s*(?:(\d{1,2})\s*['′’]\s*)?(?:(\d{1,2}(?:\.\d+)?)\s*(?:\"|″|”|'')\s*)?([NSEW])",
    re.I)
_SHORT_HOSTS = ("maps.app.goo.gl", "goo.gl", "g.co", "share.google")


def _valid(lat: float, lon: float) -> bool:
    return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0 and (lat or lon)


def parse_location(text: str) -> tuple[float, float] | None:
    """Coordinates or the link to a map, as the coordinates they hold.

    Understands "42.19, -71.20", the degrees-minutes-seconds Google Maps
    shows ("42°11'38.8"N 71°11'58.2"W") and the addresses of Google Maps,
    Apple Maps and OpenStreetMap. The pin of a Google link (``!3d…!4d…``)
    is preferred to the middle of the map it was shared from (``@…``).
    Returns None for anything else, so ordinary words are searched for.
    """
    text = (text or "").strip()
    if not text:
        return None
    text = urllib.parse.unquote(text)
    patterns = (
        rf"!3d({_NUM})!4d({_NUM})",                 # Google: the marked place
        rf"[?&](?:q|ll|query|destination|center|sll)=({_NUM})[,+ ]+({_NUM})",
        rf"mlat=({_NUM})&mlon=({_NUM})",            # OpenStreetMap
        rf"#map=\d+/({_NUM})/({_NUM})",
        rf"@({_NUM}),({_NUM})",                     # Google: the map's middle
    )
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            lat, lon = float(m.group(1)), float(m.group(2))
            if _valid(lat, lon):
                return lat, lon
    dms = _DMS.findall(text)
    if len(dms) >= 2:
        values = {}
        for deg, minute, second, hemi in dms[:2]:
            value = float(deg) + float(minute or 0) / 60 + float(second or 0) / 3600
            hemi = hemi.upper()
            values[hemi] = -value if hemi in "SW" else value
        lat = next((v for k, v in values.items() if k in "NS"), None)
        lon = next((v for k, v in values.items() if k in "EW"), None)
        if lat is not None and lon is not None and _valid(lat, lon):
            return lat, lon
    m = re.fullmatch(rf"\(?\s*({_NUM})\s*[,;\s]\s*({_NUM})\s*\)?", text)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if _valid(lat, lon):
            return lat, lon
    return None


def is_short_link(text: str) -> bool:
    """A shortened map link, which holds no coordinates until it is opened."""
    try:
        parts = urllib.parse.urlparse((text or "").strip())
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and (parts.hostname or "") in _SHORT_HOSTS


def resolve_link(link: str) -> str:
    """Where a shortened link leads. Raises OSError when it cannot be opened.
    Only the link somebody pasted is asked for, and nothing else is sent."""
    from .app import VERSION
    request = urllib.request.Request(
        link.strip(), headers={"User-Agent": f"Piklin/{VERSION} (+https://vezzu.studio)"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:
            final = resp.geturl()
    except ValueError as exc:
        raise OSError(str(exc)) from exc
    # A consent page in front of the map carries the real address along.
    got = urllib.parse.parse_qs(urllib.parse.urlparse(final).query).get("continue")
    return got[0] if got else final


def pasted_place(words: list[str]) -> Found | None:
    """The place a search box holds, when what was typed is a place already:
    coordinates, or the link to a map."""
    for word in words:
        where = parse_location(word)
        if where is None and is_short_link(word):
            try:
                where = parse_location(resolve_link(word))
            except OSError:
                where = None
        if where is not None:
            return Found(_("Pasted place"), f"{where[0]:.5f}, {where[1]:.5f}",
                         where[0], where[1], True, "")
    return None


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
                  t.lat, t.lon, False, kind_label("city")) for t in towns]


def _queries(country: str, city: str, place: str) -> list[str]:
    """What to ask, from the most exact to the most forgiving: the words as
    an address, then without the town or country, which may be spelt some
    other way or be where the map does not have it."""
    full = [w for w in (place, city, country) if w]
    tries = [", ".join(full)]
    if place and (city or country):
        tries.append(", ".join(w for w in (place, city) if w))
        tries.append(", ".join(w for w in (place, country) if w))
        tries.append(place)
    elif city and country:
        tries.append(city)
    seen: list[str] = []
    for q in tries:
        if q and q not in seen:
            seen.append(q)
    return seen


def search(places: Places, country: str, city: str, place: str,
           language: str = "en") -> tuple[list[Found], bool]:
    """Look up ``place`` in ``city`` in ``country``, any of them optional
    except that something must be given.

    Coordinates or a map link in any box are taken as the answer. Otherwise
    both searches are asked, in parallel, and their answers joined; when the
    exact words find nothing, fewer of them are tried. Returns the answers
    and whether the network was used.
    """
    words = [w.strip() for w in (place, city, country) if w and w.strip()]
    if not words:
        return [], False
    pasted = pasted_place(words)
    if pasted is not None:
        return [pasted], True

    def ask(query: str) -> tuple[list[Found], bool]:
        results: dict[str, list[Found]] = {}
        failed: list[bool] = []

        def run(name, fn):
            try:
                results[name] = fn(query, language)
            except OSError:
                results[name] = []
                failed.append(True)
        threads = [threading.Thread(target=run, args=(n, f), daemon=True)
                   for n, f in (("nominatim", search_online), ("photon", search_photon))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(TIMEOUT + 2)
        both_down = len(failed) >= 2
        return merge(results.get("nominatim", []), results.get("photon", [])), both_down

    network = False
    for query in _queries(country.strip(), city.strip(), place.strip()):
        found, down = ask(query)
        if down:
            break
        network = True
        if found:
            return found, True
    return search_offline(places, country.strip(), city.strip(), place.strip()), False
