"""The look of Piklin's map: quiet greys and whites, so the pins are what
stands out, with the names of streets, parks and places in grey.

Ordinary map tiles come with a red cross on every hospital and pharmacy,
which is what a red pin looks like from a distance. This draws the map
from vector data instead - OpenFreeMap's, free, no key, nothing to sign up
for - in a style of our own, so a place is named in grey and never
pictured in red.

Only the part of the map being looked at is asked for, as with any tiles;
nothing about the photos goes with it. If the vector map cannot be had -
an older map library without it, no connection the first time - the
ordinary tiles stay, so the map always shows something.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request

from .i18n import current_language
from .logs import log
from .paths import config_dir

TILEJSON = "https://tiles.openfreemap.org/planet"
LICENSE = "© OpenFreeMap © OpenMapTiles Data from OpenStreetMap"
LICENSE_URI = "https://openfreemap.org/"
TIMEOUT = 10.0
# The address of the current tiles changes when the map data is refreshed
# (about weekly), so the last one is kept for a day, not for good.
FRESH_FOR = 24 * 3600

_SOURCE = "openmaptiles"
_FONT = ["Noto Sans Regular"]


def _name(language: str) -> list:
    """The name to show: in the language Piklin is in when the map has it,
    else in English, else as written where the place is. So Japan is not
    日本 to somebody reading Spanish."""
    fields = []
    if language and language != "en":
        fields.append(["get", f"name:{language}"])
    fields += [["get", "name_en"], ["get", "name:latin"], ["get", "name"]]
    return ["coalesce"] + fields


def _match(field: str, *values) -> list:
    return ["match", ["get", field], list(values), True, False]


def _fill(id, layer, colour, flt=None, minzoom=None) -> dict:
    layer_ = {"id": id, "type": "fill", "source": _SOURCE, "source-layer": layer,
              "paint": {"fill-color": colour}}
    if flt:
        layer_["filter"] = flt
    if minzoom is not None:
        layer_["minzoom"] = minzoom
    return layer_


def _line(id, layer, colour, width, flt=None, minzoom=None, dash=None) -> dict:
    layer_ = {"id": id, "type": "line", "source": _SOURCE, "source-layer": layer,
              "layout": {"line-cap": "round", "line-join": "round"},
              "paint": {"line-color": colour, "line-width": width}}
    if flt:
        layer_["filter"] = flt
    if minzoom is not None:
        layer_["minzoom"] = minzoom
    if dash:
        layer_["paint"]["line-dasharray"] = dash
    return layer_


def _label(id, layer, size, colour, flt=None, minzoom=None, maxzoom=None,
           along_line=False, language: str = "en") -> dict:
    layout = {"text-field": _name(language), "text-font": _FONT, "text-size": size}
    if along_line:
        layout["symbol-placement"] = "line"
    layer_ = {"id": id, "type": "symbol", "source": _SOURCE, "source-layer": layer,
              "layout": layout,
              "paint": {"text-color": colour, "text-halo-color": "#ffffff",
                        "text-halo-width": 1.5}}
    if flt:
        layer_["filter"] = flt
    if minzoom is not None:
        layer_["minzoom"] = minzoom
    if maxzoom is not None:
        layer_["maxzoom"] = maxzoom
    return layer_


# Places that are only noise on a map of holidays: stops, car parks, gates.
_NOISE = ["all"] + [["!=", ["get", "class"], c] for c in
                    ("bus", "railway", "parking", "information", "gate", "fence")]


def style(tiles: list[str], maxzoom: int = 14, language: str = "en") -> dict:
    """The map's style for tiles served from ``tiles``, its names in ``language``."""
    named = ["has", "name"]
    label = lambda *a, **k: _label(*a, language=language, **k)
    layers = [
        {"id": "background", "type": "background",
         "paint": {"background-color": "#f3f3f1"}},
        _fill("wood", "landcover", "#e6ebe1", _match("class", "wood")),
        _fill("grass", "landcover", "#ecefe6", _match("class", "grass", "farmland")),
        _fill("park", "park", "#e3eadb"),
        _fill("water", "water", "#cddfea"),
        _line("waterway", "waterway", "#cddfea", 1),
        _fill("building", "building", "#e8e8e5", None, 14),
        _line("road_minor_casing", "transportation", "#dcdcd8", 3,
              _match("class", "minor", "service", "tertiary"), 13),
        _line("road_minor", "transportation", "#ffffff", 2,
              _match("class", "minor", "service", "tertiary"), 13),
        _line("road_major_casing", "transportation", "#cfcfca", 5,
              _match("class", "primary", "secondary", "trunk", "motorway"), 8),
        _line("road_major", "transportation", "#ffffff", 3.5,
              _match("class", "primary", "secondary", "trunk", "motorway"), 8),
        _line("country", "boundary", "#aeaeaa", 1, ["==", ["get", "admin_level"], 2],
              None, [4, 2]),
        # Names, from the biggest down; more of them the closer the map is.
        label("place_country", "place", 14, "#555555", _match("class", "country"),
               None, 6),
        label("place_state", "place", 12, "#77777c", _match("class", "state"), 4, 7),
        label("place_city", "place", 13, "#3b3b3b", _match("class", "city"), 4),
        label("place_town", "place", 11.5, "#555555",
               _match("class", "town", "village", "suburb", "neighbourhood",
                      "quarter"), 9),
        label("water_names", "water_name", 11, "#7f95a3", named, 8),
        label("street_names", "transportation_name", 11, "#66666b", named, 14,
               None, along_line=True),
        label("park_names", "park", 11, "#5f6b58", named, 14),
        # Places to go, in grey: the important ones first, the rest as the
        # map is brought closer.
        label("poi_major", "poi", 11, "#5c5c61",
               ["all", named, ["<=", ["get", "rank"], 6], _NOISE], 14),
        label("poi_more", "poi", 11, "#66666b",
               ["all", named, ["<=", ["get", "rank"], 20], _NOISE], 16),
        label("poi_all", "poi", 10.5, "#75757a", ["all", named, _NOISE], 17),
    ]
    return {"version": 8, "name": "Piklin",
            "sources": {_SOURCE: {"type": "vector", "tiles": tiles,
                                  "minzoom": 0, "maxzoom": maxzoom}},
            "layers": layers}


# -- where the tiles are ------------------------------------------------------
def _cache_file():
    return config_dir() / "map-tiles.json"


def _cached() -> tuple[list[str], int, float] | None:
    try:
        data = json.loads(_cache_file().read_text(encoding="utf-8"))
        return list(data["tiles"]), int(data.get("maxzoom", 14)), float(data["when"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _fetch() -> tuple[list[str], int]:
    """Ask where the current tiles are. Raises OSError."""
    from .app import VERSION
    request = urllib.request.Request(
        TILEJSON, headers={"User-Agent": f"Piklin/{VERSION} (+https://vezzu.studio)"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tiles = [t for t in data["tiles"] if isinstance(t, str)]
        maxzoom = int(data.get("maxzoom", 14))
    except (ValueError, KeyError, TypeError) as exc:
        raise OSError(str(exc)) from exc
    if not tiles:
        raise OSError("no tiles")
    try:
        _cache_file().write_text(json.dumps(
            {"tiles": tiles, "maxzoom": maxzoom, "when": time.time()}), encoding="utf-8")
    except OSError:
        pass
    return tiles, maxzoom


def _renderer(tiles: list[str], maxzoom: int):
    """The map source for these tiles, or None when this map library cannot
    draw vector maps or does not like the style."""
    try:
        import gi
        gi.require_version("Shumate", "1.0")
        from gi.repository import Shumate
        if not hasattr(Shumate, "VectorRenderer"):
            return None
        renderer = Shumate.VectorRenderer.new(
            "piklin-map", json.dumps(style(tiles, maxzoom, current_language() or "en")))
    except Exception as exc:                       # a GLib.Error, or no vector support
        log.warning("the clean map is unavailable, using ordinary tiles: %s", exc)
        return None
    renderer.set_license(LICENSE)
    renderer.set_license_uri(LICENSE_URI)
    return renderer


def use_clean_map(simple_map, after=None) -> None:
    """Give a ``Shumate.SimpleMap`` the clean map. The ordinary tiles it was
    made with stay until the new ones are ready, and for good if they never are.
    ``after`` is called once the map has changed, to put back any limits."""
    from gi.repository import GLib

    def apply(tiles, maxzoom):
        renderer = _renderer(tiles, maxzoom)
        if renderer is not None:
            simple_map.set_map_source(renderer)
            if after is not None:
                after()
        return GLib.SOURCE_REMOVE

    cached = _cached()
    fresh = cached is not None and time.time() - cached[2] < FRESH_FOR
    if fresh:
        apply(cached[0], cached[1])
        return

    def work():
        try:
            tiles, maxzoom = _fetch()
        except OSError:
            # No connection now: the last known address may still serve.
            if cached is None:
                return
            tiles, maxzoom = cached[0], cached[1]
        GLib.idle_add(apply, tiles, maxzoom)
    threading.Thread(target=work, daemon=True, name="pika-map-style").start()
