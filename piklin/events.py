"""Events: the days and trips the photos fall into.

Piklin finds them by itself, from when and where each photo was taken, and gives
each a name from what it knows (the city, the month, Christmas). A person can
rename one, join two that belong together or split one in two; what was done by
hand is kept when the rest are found again.

A photo is in the event whose time span holds its date, so nothing is copied:
an event is a name, a start and an end. Everything here is on this computer.
"""
from __future__ import annotations

import uuid as _uuid
from datetime import datetime

from .catalog import VIDEO_SQL
from .i18n import _, day_month, long_date, month_name, month_year

GAP = 6 * 3600          # a pause this long between two photos ends an event
CITY_GAP = 90 * 60      # ...or this long, when the city changed as well
MIN_PHOTOS = 5          # a handful of photos is not yet an event worth a card
TRIP_AWAY = 2           # events this many days apart, in another place, are a trip

_VISIBLE = "p.trashed_at IS NULL AND p.hidden=0 AND p.taken_at IS NOT NULL AND p.paired_to IS NULL"


# -- finding them ------------------------------------------------------------
def _spans(rows):
    """[(start, end, count)] from (taken_at, city) rows sorted by date."""
    spans = []
    start = last = None
    count = 0
    last_city = None
    for ts, city in rows:
        if start is not None:
            gap = ts - last
            if gap > GAP or (gap > CITY_GAP and city and last_city and city != last_city):
                spans.append((start, last, count))
                start, count = None, 0
        if start is None:
            start, count = ts, 0
        last = ts
        count += 1
        if city:
            last_city = city
    if start is not None:
        spans.append((start, last, count))
    return spans


def refresh(catalog) -> int:
    """Find the events again: the ones a person shaped stay, the rest are redone from the
    photos as they are now. Returns how many events there are."""
    custom = [(r["start_at"], r["end_at"]) for r in catalog.q(
        "SELECT start_at, end_at FROM events WHERE custom=1 ORDER BY start_at")]
    rows = catalog.q(
        f"SELECT p.taken_at AS t, pp.city AS city FROM photos p "
        f"LEFT JOIN photo_places pp ON pp.photo_id=p.id WHERE {_VISIBLE} ORDER BY p.taken_at")
    free = []
    ci = 0
    for r in rows:
        t = r["t"]
        while ci < len(custom) and custom[ci][1] < t:
            ci += 1
        if ci < len(custom) and custom[ci][0] <= t <= custom[ci][1]:
            continue                        # inside an event somebody shaped
        free.append((t, r["city"]))
    found = [s for s in _spans(free) if s[2] >= MIN_PHOTOS]
    with catalog.write() as cur:
        cur.execute("DELETE FROM events WHERE custom=0")
        old = {r[0]: r[1] for r in cur.execute("SELECT start_at, id FROM events")}
        for start, end, _n in found:
            if start in old:
                continue
            cur.execute("INSERT INTO events(uuid,name,start_at,end_at) VALUES(?,?,?,?)",
                        (_uuid.uuid4().hex, None, start, end))
        _describe(cur)
    return int(catalog.scalar("SELECT COUNT(*) FROM events", (), 0))


def _describe(cur) -> None:
    """Counts, the cover and the place of every event, written in the same step that made
    them, so nobody ever sees an event with nothing in it."""
    for ev in cur.execute("SELECT id, start_at, end_at FROM events").fetchall():
        lo, hi = ev["start_at"], ev["end_at"] + 0.001
        n = cur.execute(
            f"SELECT COUNT(*), COALESCE(SUM({VIDEO_SQL}),0) FROM photos p "
            f"WHERE {_VISIBLE} AND p.taken_at>=? AND p.taken_at<?", (lo, hi)).fetchone()
        cover = cur.execute(
            f"SELECT p.id FROM photos p WHERE {_VISIBLE} AND p.taken_at>=? AND p.taken_at<? "
            f"AND NOT ({VIDEO_SQL}) "
            f"ORDER BY p.favorite DESC, p.rating DESC, p.bytes DESC LIMIT 1", (lo, hi)).fetchone()
        place = cur.execute(
            f"SELECT pp.city, pp.country, COUNT(*) AS n FROM photo_places pp JOIN photos p ON p.id=pp.photo_id "
            f"WHERE {_VISIBLE} AND p.taken_at>=? AND p.taken_at<? AND pp.city IS NOT NULL "
            f"GROUP BY pp.city, pp.country ORDER BY n DESC LIMIT 1", (lo, hi)).fetchone()
        cur.execute("UPDATE events SET photos=?, videos=?, cover_id=?, place=?, country=? WHERE id=?",
                    (n[0] - n[1], n[1], cover[0] if cover else None,
                     place[0] if place else None, place[1] if place else None, ev["id"]))


def _home(catalog) -> str | None:
    """The city most photos were taken in: where the person lives, as far as Piklin can tell."""
    r = catalog.q1("SELECT city, COUNT(*) n FROM photo_places WHERE city IS NOT NULL GROUP BY city ORDER BY n DESC LIMIT 1")
    return r["city"] if r else None


# -- names -----------------------------------------------------------------------
def default_name(ev) -> str:
    """What an event is called until somebody names it."""
    a = datetime.fromtimestamp(ev["start_at"])
    b = datetime.fromtimestamp(ev["end_at"])
    days = (b.date() - a.date()).days
    holiday = None
    if a.month == 12 and 22 <= a.day <= 26 or (b.month == 12 and 22 <= b.day <= 26 and days <= 3):
        holiday = _("Christmas")
    elif (a.month == 12 and a.day == 31) or (a.month == 1 and a.day == 1):
        holiday = _("New Year")
    place = ev["place"]
    when = month_year(a) if days > 0 or place else long_date(a)
    if holiday:
        return f"{holiday} {a.year if not (a.month == 12 and a.day == 31) else a.year}"
    if place and days == 0:
        return f"{place} · {day_month(a)} {a.year}"
    if place:
        return f"{place} · {month_year(a)}"
    if days == 0:
        return when
    if (a.year, a.month) == (b.year, b.month):
        return f"{a.day}–{b.day} {month_name(a.month)} {a.year}"
    return f"{day_month(a)} – {day_month(b)} {b.year}"


def name_of(ev) -> str:
    return ev["name"] or default_name(ev)


def dates_of(ev) -> str:
    a = datetime.fromtimestamp(ev["start_at"])
    b = datetime.fromtimestamp(ev["end_at"])
    if a.date() == b.date():
        return long_date(a)
    return f"{day_month(a)} – {day_month(b)} {b.year}" if a.year == b.year else f"{day_month(a)} {a.year} – {day_month(b)} {b.year}"


# -- reading them ---------------------------------------------------------------------
def all_events(catalog, year: int | None = None):
    sql = ("SELECT e.*, p.path AS cover_path FROM events e LEFT JOIN photos p ON p.id=e.cover_id "
           "WHERE e.photos + e.videos >= 1")
    params = ()
    if year:
        lo = datetime(year, 1, 1).timestamp()
        hi = datetime(year + 1, 1, 1).timestamp()
        sql += " AND e.start_at>=? AND e.start_at<?"
        params = (lo, hi)
    return catalog.q(sql + " ORDER BY e.start_at DESC", params)


def years(catalog) -> list[int]:
    return [int(r[0]) for r in catalog.con.execute(
        "SELECT DISTINCT CAST(strftime('%Y', start_at, 'unixepoch', 'localtime') AS INTEGER) y FROM events ORDER BY y DESC")]


def event(catalog, event_id: int):
    return catalog.q1("SELECT e.*, p.path AS cover_path FROM events e LEFT JOIN photos p ON p.id=e.cover_id WHERE e.id=?",
                      (event_id,))


def photo_ids(catalog, event_id: int) -> list[int]:
    ev = event(catalog, event_id)
    if ev is None:
        return []
    return [r[0] for r in catalog.con.execute(
        f"SELECT p.id FROM photos p WHERE {_VISIBLE} AND p.taken_at>=? AND p.taken_at<? ORDER BY p.taken_at DESC",
        (ev["start_at"], ev["end_at"] + 0.001))]


def of_photo(catalog, photo_id: int):
    r = catalog.q1("SELECT taken_at FROM photos WHERE id=?", (photo_id,))
    if r is None or r["taken_at"] is None:
        return None
    return catalog.q1("SELECT * FROM events WHERE start_at<=? AND end_at>=? ORDER BY start_at DESC LIMIT 1",
                      (r["taken_at"], r["taken_at"]))


def trips(catalog, home=None):
    """Runs of events far from home, as (first, last, place, events) newest first."""
    home = home or _home(catalog)
    out, cur = [], None
    for ev in sorted(all_events(catalog), key=lambda e: e["start_at"]):
        away = bool(ev["place"] and home and ev["place"] != home)
        if away and cur and cur["place"] == ev["place"] and ev["start_at"] - cur["end"] <= TRIP_AWAY * 86400:
            cur["end"], cur["n"] = ev["end_at"], cur["n"] + 1
        elif away:
            cur = {"start": ev["start_at"], "end": ev["end_at"], "place": ev["place"], "n": 1}
            out.append(cur)
        else:
            cur = None
    return [t for t in reversed(out) if t["n"] > 1]


# -- what a person does ---------------------------------------------------------------
def _freeze(cur, ev_id):
    cur.execute("UPDATE events SET custom=1 WHERE id=?", (ev_id,))


def rename(catalog, event_id: int, name: str) -> None:
    name = (name or "").strip()
    with catalog.write() as cur:
        cur.execute("UPDATE events SET name=?, custom=1 WHERE id=?", (name or None, event_id))


def join(catalog, first_id: int, second_id: int) -> int | None:
    """Two events become one."""
    a, b = event(catalog, first_id), event(catalog, second_id)
    if a is None or b is None or a["id"] == b["id"]:
        return None
    lo, hi = min(a["start_at"], b["start_at"]), max(a["end_at"], b["end_at"])
    name = a["name"] or b["name"]
    with catalog.write() as cur:
        cur.execute("DELETE FROM events WHERE id IN (?,?)", (a["id"], b["id"]))
        cur.execute("INSERT INTO events(uuid,name,start_at,end_at,custom) VALUES(?,?,?,?,1)",
                    (_uuid.uuid4().hex, name, lo, hi))
        new_id = cur.lastrowid
        _describe(cur)
    return new_id


def neighbour(catalog, event_id: int, later: bool):
    ev = event(catalog, event_id)
    if ev is None:
        return None
    if later:
        return catalog.q1("SELECT id FROM events WHERE start_at>? ORDER BY start_at LIMIT 1", (ev["end_at"],))
    return catalog.q1("SELECT id FROM events WHERE end_at<? ORDER BY end_at DESC LIMIT 1", (ev["start_at"],))


def split(catalog, event_id: int, at: float) -> tuple[int, int] | None:
    """One event becomes two, the second starting with the photo taken at ``at``."""
    ev = event(catalog, event_id)
    if ev is None or not (ev["start_at"] < at <= ev["end_at"]):
        return None
    before = catalog.scalar(
        f"SELECT MAX(p.taken_at) FROM photos p WHERE {_VISIBLE} AND p.taken_at>=? AND p.taken_at<?",
        (ev["start_at"], at))
    if before is None:
        return None
    with catalog.write() as cur:
        cur.execute("DELETE FROM events WHERE id=?", (ev["id"],))
        cur.execute("INSERT INTO events(uuid,name,start_at,end_at,custom) VALUES(?,?,?,?,1)",
                    (_uuid.uuid4().hex, ev["name"], ev["start_at"], before))
        a = cur.lastrowid
        cur.execute("INSERT INTO events(uuid,name,start_at,end_at,custom) VALUES(?,?,?,?,1)",
                    (_uuid.uuid4().hex, None, at, ev["end_at"]))
        b = cur.lastrowid
        _describe(cur)
    return a, b


def to_album(catalog, event_id: int) -> int | None:
    ids = photo_ids(catalog, event_id)
    ev = event(catalog, event_id)
    if not ids or ev is None:
        return None
    album = catalog.create_album(name_of(ev))
    album = album["id"] if hasattr(album, "keys") else album
    catalog.album_add(album, ids)
    return album
