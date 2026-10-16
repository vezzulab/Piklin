"""Naming where each photo was taken, for the search box (see smartsearch.py).

Every photo with coordinates is named from the list of towns Piklin carries,
without the network: the city, the state or province and the country. So that
"Miami" or "Dominican Republic" finds the photos taken there.

It runs in the background, a few thousand photos at a time, and only for the
photos that are not named yet or whose coordinates changed (placed again by
hand): when there is nothing to do it is one quick question to the catalog.
"""
from __future__ import annotations

from .places import Places
from .smartsearch import fold

_places: Places | None = None


def shared_places() -> Places:
    global _places
    if _places is None:
        _places = Places()
    return _places


def pending(catalog) -> int:
    return catalog.con.execute(
        """SELECT COUNT(*) FROM photos p LEFT JOIN photo_places pp ON pp.photo_id=p.id
           WHERE p.gps_lat IS NOT NULL AND p.gps_lon IS NOT NULL
             AND NOT (p.gps_lat=0 AND p.gps_lon=0)
             AND (pp.photo_id IS NULL OR pp.lat IS NOT p.gps_lat OR pp.lon IS NOT p.gps_lon)
        """).fetchone()[0]


def index_new(catalog, places: Places | None = None, limit: int = 4000) -> int:
    """Name up to ``limit`` photos that are not named yet. Returns how many."""
    places = places or shared_places()
    rows = catalog.con.execute(
        """SELECT p.id, p.gps_lat AS lat, p.gps_lon AS lon FROM photos p
           LEFT JOIN photo_places pp ON pp.photo_id=p.id
           WHERE p.gps_lat IS NOT NULL AND p.gps_lon IS NOT NULL
             AND NOT (p.gps_lat=0 AND p.gps_lon=0)
             AND (pp.photo_id IS NULL OR pp.lat IS NOT p.gps_lat OR pp.lon IS NOT p.gps_lon)
           LIMIT ?""", (limit,)).fetchall()
    if not rows:
        return 0
    seen: dict[tuple[float, float], tuple[str, str, str]] = {}
    out = []
    for r in rows:
        key = (round(r["lat"], 2), round(r["lon"], 2))     # a street is a street: one answer
        named = seen.get(key)
        if named is None:
            country, city = places.locate(r["lat"], r["lon"])
            region = ""
            if city:
                town = places.nearest(r["lat"], r["lon"], max_km=60.0)
                region = town.region if town is not None and town.country == country else ""
            named = seen[key] = (city or "", region or "", country or "")
        city, region, country = named
        out.append((r["id"], r["lat"], r["lon"], city or None, region or None, country or None,
                    fold(city) or None, fold(region) or None, fold(country) or None))
    with catalog.write() as cur:
        cur.executemany(
            "INSERT INTO photo_places(photo_id,lat,lon,city,region,country,city_f,region_f,country_f) "
            "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(photo_id) DO UPDATE SET lat=excluded.lat, "
            "lon=excluded.lon, city=excluded.city, region=excluded.region, country=excluded.country, "
            "city_f=excluded.city_f, region_f=excluded.region_f, country_f=excluded.country_f", out)
    return len(out)
