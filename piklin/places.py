"""Naming the places photos were taken, without asking anyone.

Turning coordinates into "Santo Domingo" is normally done by asking a
geocoding service, which means handing someone else the coordinates of
a person's photographs. Piklin carries the answer instead: a list of the
world's towns and cities, read from disk, consulted locally. Nothing
about where anyone has been leaves the computer.

The list is GeoNames' ``cities15000`` - every settlement over about
fifteen thousand people, some thirty-four thousand of them - reduced to
the six fields a name needs and stored gzipped, around 600 KB. It is
licensed CC BY 4.0; see NOTICE.md.
"""
from __future__ import annotations

import gzip
import math
import os
import threading
from pathlib import Path

from .i18n import _, ngettext

FILENAME = "places.tsv.gz"

# How far a pin's photos reach decides what the pin is called. Under a
# few kilometres it is somewhere you stood; up to a couple of hundred it
# is a city and the country it is in; beyond that only the country means
# anything.
PLACE_KM = 3.0
# A city and its suburbs, which people speak of as one place.
METRO_KM = 40.0
CITY_KM = 200.0
# Below this a settlement is a district rather than a city: fine for
# naming a street corner, wrong for naming a whole visit.
CITY_POP = 100_000
# How far a town's name still describes where somebody stood, and how
# far a country's does. Beyond both - mid-ocean, deep desert, Antarctica
# - a pin goes unnamed rather than wrongly named.
NAME_MAX_KM = 60.0
COUNTRY_MAX_KM = 400.0
# South of this there are no towns on the list, and the continent is
# what anyone would call it anyway. It is the Antarctic Treaty's line.
ANTARCTIC = -60.0
# The index is a degree grid; a degree is about 111 km, so a lookup
# usually settles within the first ring or two.
CELL = 1.0


def data_path() -> Path | None:
    """Where the place list sits, wherever Piklin was installed from."""
    here = Path(__file__).resolve()
    candidates = []
    if os.environ.get("APPDIR"):
        candidates.append(Path(os.environ["APPDIR"]) / "usr/share/piklin/data" / FILENAME)
    candidates.append(here.parent.parent / "data" / FILENAME)
    candidates.append(Path("/usr/share/piklin") / FILENAME)
    if os.name == "nt":
        candidates.append(here.parents[2] / "data" / FILENAME)
    return next((c for c in candidates if c.is_file()), None)


class Place(tuple):
    """name, region (state or province), country, latitude, longitude,
    population - as read from the list."""
    __slots__ = ()
    name = property(lambda self: self[0])
    region = property(lambda self: self[1])
    country = property(lambda self: self[2])
    lat = property(lambda self: self[3])
    lon = property(lambda self: self[4])
    population = property(lambda self: self[5])


class Places:
    """The world's towns, on disk, read once and asked locally."""

    def __init__(self, path: Path | str | None = None):
        self._path = Path(path) if path is not None else data_path()
        self._all: list[Place] = []
        self._index: dict[int, dict[tuple[int, int], list[int]]] = {}
        self._loaded = False
        # The map counts countries on a worker thread while the panel
        # names a pin on the UI thread; both may be the first to ask.
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    def _load(self) -> None:
        with self._lock:
            self._load_locked()

    def _load_locked(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self._path is None:
            return
        rows = []
        try:
            with gzip.open(self._path, "rt", encoding="utf-8") as f:
                for line in f:
                    name, region, country, lat, lon, pop = line.rstrip("\n").split("\t")
                    rows.append(Place((name, region, country,
                                       float(lat), float(lon), int(pop))))
        except (OSError, ValueError):
            # Without the list the map still works; its pins simply go
            # unnamed, which is better than refusing to open.
            rows = []
        self._all = rows

    def _grid(self, min_pop: int) -> dict[tuple[int, int], list[int]]:
        with self._lock:
            self._load_locked()
            grid = self._index.get(min_pop)
            if grid is None:
                grid = {}
                for i, p in enumerate(self._all):
                    if p.population >= min_pop:
                        grid.setdefault((int(p.lat / CELL), int(p.lon / CELL)), []).append(i)
                self._index[min_pop] = grid
            return grid

    def nearest(self, lat: float, lon: float, min_pop: int = 0,
                max_km: float | None = None) -> Place | None:
        """The closest place to a point.

        ``max_km`` is how far away a name is still worth having. Over an
        ocean or a desert the closest town can be hundreds of kilometres
        off, and naming a photograph after somewhere nobody went is
        worse than leaving it unnamed.
        """
        grid = self._grid(min_pop)
        if not grid:
            return None
        squeeze = math.cos(math.radians(lat)) or 1.0
        ci, cj = int(lat / CELL), int(lon / CELL)
        best, best_d = None, None
        for ring in range(9):
            for di in range(-ring, ring + 1):
                for dj in range(-ring, ring + 1):
                    if ring and max(abs(di), abs(dj)) != ring:
                        continue
                    for i in grid.get((ci + di, cj + dj), ()):
                        p = self._all[i]
                        dy = p.lat - lat
                        dx = (p.lon - lon) * squeeze
                        d = dx * dx + dy * dy
                        if best_d is None or d < best_d:
                            best, best_d = p, d
            # One ring beyond the first hit, in case a nearer place sits
            # just over a cell edge.
            if best is not None and ring >= 1:
                break
        if best is not None and max_km is not None:
            if math.sqrt(best_d) * 111.32 > max_km:
                return None
        return best

    def largest_within(self, lat: float, lon: float, km: float) -> Place | None:
        """The biggest city within reach of a point.

        Across a whole metropolis the nearest city is the wrong answer:
        standing in a suburb, the closest name on the list is that
        suburb's, while the place people would say they were is the big
        one it belongs to.
        """
        grid = self._grid(CITY_POP)
        if not grid:
            return None
        squeeze = math.cos(math.radians(lat)) or 1.0
        span = km / 111.32
        reach = int(span / CELL) + 1
        ci, cj = int(lat / CELL), int(lon / CELL)
        best = None
        for di in range(-reach, reach + 1):
            for dj in range(-reach, reach + 1):
                for i in grid.get((ci + di, cj + dj), ()):
                    p = self._all[i]
                    dy = p.lat - lat
                    dx = (p.lon - lon) * squeeze
                    if dx * dx + dy * dy > span * span:
                        continue
                    if best is None or p.population > best.population:
                        best = p
        return best

    # ------------------------------------------------------------------
    def locate(self, lat: float, lon: float) -> tuple[str, str]:
        """The country and the city a point belongs to, either "" when
        nothing on the list is near enough to say.

        Used to count where somebody has been, so a suburb is folded into
        the city it belongs to: a trip to Santo Domingo is one city, not
        the four districts the list happens to hold.
        """
        self._load()
        if not self._all:
            return "", ""
        if lat <= ANTARCTIC:
            return _("Antarctica"), ""
        near = self.nearest(lat, lon, max_km=COUNTRY_MAX_KM)
        if near is None:
            return "", ""
        country = near.country
        town = self.nearest(lat, lon, max_km=NAME_MAX_KM)
        if town is None:
            return country, ""
        big = self.largest_within(lat, lon, METRO_KM)
        if big is not None and big.country == country:
            town = big
        return country, town.name

    def find(self, text: str, country: str = "", limit: int = 6) -> list[Place]:
        """Towns on the list whose name matches, the biggest first.

        The answer Piklin gives with no network: it knows towns, not
        streets, but a town is where a search for a trip starts.
        """
        self._load()
        want = _fold(text)
        if not want:
            return []
        only = _fold(country)
        hits = []
        for p in self._all:
            if only and only not in _fold(p.country):
                continue
            name = _fold(p.name)
            if name == want:
                hits.append((0, p))
            elif name.startswith(want):
                hits.append((1, p))
        hits.sort(key=lambda h: (h[0], -h[1].population))
        return [p for _rank, p in hits[:limit]]

    def describe(self, coords: list[tuple[float, float]]) -> str:
        """What to call a pin holding photos taken at these points.

        The answer is drawn from the points themselves rather than from
        the middle of them: a pin covering two countries has its centre
        in neither, and would otherwise be named after whichever happened
        to be closest to the empty sea between them.
        """
        self._load()
        if not self._all or not coords:
            return ""
        span = _span_km(coords)
        lat = sum(c[0] for c in coords) / len(coords)
        lon = sum(c[1] for c in coords) / len(coords)

        if lat <= ANTARCTIC:
            # No settlement on the list is anywhere near, and the whole
            # continent is one place people speak of by name.
            return _("Antarctica")

        if span <= PLACE_KM:
            here = self.nearest(lat, lon, max_km=NAME_MAX_KM)
            if here is not None:
                return f"{here.name}, {here.region or here.country}"
            # Nowhere near a town: the country is still worth saying, and
            # is right over a far wider area than a town's name is.
            far = self.nearest(lat, lon, max_km=COUNTRY_MAX_KM)
            return far.country if far is not None else ""

        # Wider than one spot: name it after what its photos have in
        # common, which is a question about all of them.
        sample = coords if len(coords) <= 40 else coords[::len(coords) // 40]
        cities, countries = [], []
        for la, lo in sample:
            p = self.nearest(la, lo, CITY_POP)
            if p is not None:
                cities.append(p.name)
                countries.append(p.country)
        if not countries:
            return ""
        if len(set(countries)) > 1:
            return ngettext("{count} country", "{count} countries",
                            len(set(countries))).format(count=len(set(countries)))
        country = countries[0]
        if span > CITY_KM:
            return country
        # Across a city and its suburbs the biggest name wins, not the
        # closest one: the list holds districts with populations to rival
        # the city they sit in, so the nearest is often a part of the
        # place rather than the place.
        if span <= METRO_KM:
            middle = self.largest_within(lat, lon, METRO_KM)
            if middle is not None:
                return f"{middle.name}, {country}"
        if len(set(cities)) == 1:
            return f"{cities[0]}, {country}"
        # Further apart than one city: naming any of them would be
        # picking a favourite.
        return _("{count} places in {country}").format(count=len(set(cities)),
                                                       country=country)


def _fold(text: str) -> str:
    """Lower case without accents, so "Bogota" finds "Bogotá"."""
    import unicodedata
    text = unicodedata.normalize("NFD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).casefold().strip()


def _span_km(coords: list[tuple[float, float]]) -> float:
    """How far apart the two furthest-apart points are, roughly."""
    lats = [c[0] for c in coords]
    lons = [c[1] for c in coords]
    mid = math.cos(math.radians(sum(lats) / len(lats))) or 1.0
    dy = (max(lats) - min(lats)) * 111.32
    dx = (max(lons) - min(lons)) * 111.32 * mid
    return math.hypot(dx, dy)
