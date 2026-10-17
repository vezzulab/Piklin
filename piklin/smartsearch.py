"""The search box, understanding what is typed.

"Ana y Luis 2022 Miami videos" is a person and another person, a year, a place
and a kind of file: not five words to look for in file names. This reads what
was typed, in Spanish or English, and says which parts it understood - each
becomes a chip the person can see and take away - and what is left over is
looked for as words, as it always was.

It knows what is in this library: the people that have a name, the places the
photos were taken (named from their coordinates, without the network), the
cameras. Nothing here guesses with a model: a word is understood when it is
exactly something the library has, or one of a short list of words with one
meaning, and otherwise it is just a word.
"""
from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

# -- words -----------------------------------------------------------------
MONTHS = {
    "enero": 1, "ene": 1, "january": 1, "jan": 1, "febrero": 2, "feb": 2, "february": 2,
    "marzo": 3, "mar": 3, "march": 3, "abril": 4, "abr": 4, "april": 4, "apr": 4,
    "mayo": 5, "may": 5, "junio": 6, "jun": 6, "june": 6, "julio": 7, "jul": 7, "july": 7,
    "agosto": 8, "ago": 8, "august": 8, "aug": 8, "septiembre": 9, "setiembre": 9,
    "sep": 9, "sept": 9, "september": 9, "octubre": 10, "oct": 10, "october": 10,
    "noviembre": 11, "nov": 11, "november": 11, "diciembre": 12, "dic": 12,
    "december": 12, "dec": 12,
}
WEEKDAYS = {   # SQLite's strftime('%w'): Sunday is 0
    "domingo": 0, "domingos": 0, "sunday": 0, "sundays": 0, "lunes": 1, "monday": 1,
    "mondays": 1, "martes": 2, "tuesday": 2, "tuesdays": 2, "miercoles": 3,
    "wednesday": 3, "wednesdays": 3, "jueves": 4, "thursday": 4, "thursdays": 4,
    "viernes": 5, "friday": 5, "fridays": 5, "sabado": 6, "sabados": 6,
    "saturday": 6, "saturdays": 6,
}
# little words that join others: "Ana y Luis juntos", "fotos de Ana en Miami"
STOPWORDS = {"y", "e", "and", "con", "with", "juntos", "juntas", "together", "&", "de", "del", "la",
             "el", "los", "las", "en", "of", "in", "the", "a", "an", "at", "from", "un", "una",
             "por", "para", "sin", "al", "lo", "o", "or", "on", "to", "que", "my", "mi", "mis"}
KINDS = {
    "videos": {"video", "videos", "videoclip", "videoclips", "clip", "clips"},
    "photos": {"foto", "fotos", "photo", "photos", "imagen", "imagenes", "image", "images"},
    "screenshots": {"captura", "capturas", "screenshot", "screenshots", "pantallazo", "pantallazos"},
    "favorites": {"favorita", "favoritas", "favorito", "favoritos", "favorite", "favorites",
                  "favourite", "favourites"},
    "edited": {"editada", "editadas", "editado", "editados", "edited", "retocada", "retocadas"},
    "vertical": {"vertical", "verticales", "portrait", "retrato", "retratos"},
    "horizontal": {"horizontal", "horizontales", "landscape", "paisaje", "paisajes", "apaisada",
                   "apaisadas"},
    "square": {"cuadrada", "cuadradas", "cuadrado", "square", "squares"},
    "4k": {"4k", "uhd"},
    "raw": {"raw", "crudo"},
    "live": {"live", "en vivo"},
}
EXTENSIONS = {"jpg", "jpeg", "png", "heic", "heif", "avif", "webp", "gif", "tif", "tiff",
              "mov", "mp4", "m4v", "avi", "mkv", "webm", "cr2", "cr3", "nef", "arw", "dng", "raf"}
PHRASES = [   # (words, what) - tried before single words, longest first
    (("con", "ubicacion"), "located"), (("with", "location"), "located"),
    (("geolocalizadas",), "located"), (("geolocalizada",), "located"), (("located",), "located"),
    (("sin", "ubicacion"), "unlocated"), (("no", "location"), "unlocated"),
    (("without", "location"), "unlocated"),
    (("con", "personas"), "with_people"), (("with", "people"), "with_people"),
    (("con", "caras"), "with_people"), (("with", "faces"), "with_people"),
    (("sin", "personas"), "no_people"), (("no", "people"), "no_people"),
    (("sin", "caras"), "no_people"), (("without", "people"), "no_people"),
    (("sin", "gente"), "no_people"),
    (("el", "ano", "pasado"), "last_year"), (("ano", "pasado"), "last_year"),
    (("last", "year"), "last_year"), (("este", "ano"), "this_year"), (("this", "year"), "this_year"),
    (("este", "mes"), "this_month"), (("this", "month"), "this_month"),
    (("el", "mes", "pasado"), "last_month"), (("mes", "pasado"), "last_month"),
    (("last", "month"), "last_month"),
    (("esta", "semana"), "this_week"), (("this", "week"), "this_week"),
    (("la", "semana", "pasada"), "last_week"), (("semana", "pasada"), "last_week"),
    (("last", "week"), "last_week"),
    (("hoy",), "today"), (("today",), "today"), (("ayer",), "yesterday"), (("yesterday",), "yesterday"),
]
SIZE_RE = re.compile(r"^(>|<|>=|<=)?(\d+(?:[.,]\d+)?)(kb|mb|gb)$")


from .textfold import fold  # noqa: E402  (re-exported: how words are compared)


@dataclass
class Term:
    kind: str                   # person | date | place | camera | type | rating | size | text | not
    label: str                  # what the chip says
    sql: str = ""               # a condition on photos p; "" for plain words
    params: list = field(default_factory=list)
    start: int = 0              # where it was in what was typed, to take it away
    end: int = 0
    folder_ids: list = field(default_factory=list)   # the sidebar folder it stands for, if it is one


@dataclass
class Parsed:
    terms: list[Term] = field(default_factory=list)     # understood
    words: list[str] = field(default_factory=list)      # looked for as words
    not_words: list[str] = field(default_factory=list)  # and not these


# -- what the library has ---------------------------------------------------
class Vocabulary:
    """The people, places and cameras of one library, folded for comparing.
    Read once in a while, not on every key."""
    TTL = 5.0

    def __init__(self, catalog):
        self.catalog = catalog
        self._at = 0.0
        self.people: dict[str, list[int]] = {}      # folded whole name -> person ids
        self.firsts: dict[str, list[int]] = {}      # folded first name -> person ids
        self.person_names: dict[int, str] = {}
        self.places: dict[str, tuple[str, str]] = {}  # folded -> (column, shown)
        self.cameras: dict[str, str] = {}           # folded model -> shown
        self.dirs: dict[str, str] = {}              # folded folder name on disk -> shown
        self.albums: dict[str, tuple[str, list[int]]] = {}    # folded name -> (shown, album ids)
        self.sidefolders: dict[str, tuple[str, list[int]]] = {}   # sidebar folder -> its albums' ids
        self.sidekids: dict[str, list[tuple[str, str]]] = {}      # sidebar folder -> (kind, name) it holds
        self.events: dict[str, tuple[str, list[tuple[float, float]]]] = {}   # event name -> (shown, time spans)
        self.sidefolder_ids: dict[str, list[int]] = {}            # sidebar folder -> its own id(s)
        self._dirs_at = -1e9

    def refresh(self, force: bool = False) -> None:
        if not force and time.time() - self._at < self.TTL:
            return
        self._at = time.time()
        con = self.catalog.con
        people: dict[str, list[int]] = {}
        firsts: dict[str, list[int]] = {}
        names: dict[int, str] = {}
        try:
            for r in con.execute("SELECT id, name FROM people WHERE name IS NOT NULL AND hidden=0"):
                full = fold(r["name"]).strip()
                names[r["id"]] = r["name"]
                people.setdefault(full, []).append(r["id"])
                first = full.split()[0] if full.split() else ""
                if first and first != full:
                    firsts.setdefault(first, []).append(r["id"])
        except Exception:
            pass
        places: dict[str, tuple[str, str]] = {}
        try:
            for col in ("country", "region", "city"):
                for r in con.execute(
                        f"SELECT {col} AS v, COUNT(*) AS n FROM photo_places "
                        f"WHERE {col} IS NOT NULL AND {col}<>'' GROUP BY {col} ORDER BY n DESC"):
                    places.setdefault(fold(r["v"]), (col, r["v"]))
        except Exception:
            pass
        cameras: dict[str, str] = {}
        try:
            for col in ("camera_model", "camera_make"):
                for r in con.execute(
                        f"SELECT {col} AS v, COUNT(*) AS n FROM photos "
                        f"WHERE {col} IS NOT NULL AND {col}<>'' "
                        f"GROUP BY {col} ORDER BY n DESC LIMIT 400"):
                    cameras.setdefault(fold(r["v"]), r["v"])
        except Exception:
            pass
        # albums, and the folders of the sidebar (which hold albums), kept apart: the
        # person chooses which one is meant. A folder stands for every album inside it.
        albums: dict[str, tuple[str, list[int]]] = {}
        sidefolders: dict[str, tuple[str, list[int]]] = {}
        sidekids: dict[str, list[tuple[str, str]]] = {}
        sidefolder_ids: dict[str, list[int]] = {}
        try:
            alb = {r["id"]: (r["name"], r["folder_id"]) for r in
                   con.execute("SELECT id, name, folder_id FROM albums ORDER BY position, name")}
            fol = {r["id"]: (r["name"], r["parent_id"]) for r in
                   con.execute("SELECT id, name, parent_id FROM folders")}
            for aid, (nm, _f) in alb.items():
                albums.setdefault(fold(nm), (nm, []))[1].append(aid)
            children: dict = {}
            for fid, (_n, parent) in fol.items():
                children.setdefault(parent, []).append(fid)

            def albums_in(fid, seen):
                found = [a for a, (_n, f) in alb.items() if f == fid]
                for sub in children.get(fid, ()):
                    if sub not in seen:
                        found += albums_in(sub, seen | {sub})
                return found
            for fid, (nm, _p) in fol.items():
                ids = albums_in(fid, {fid})
                if ids:
                    sidefolders.setdefault(fold(nm), (nm, []))[1].extend(ids)
                sidefolder_ids.setdefault(fold(nm), []).append(fid)
                kids = [("folder", fol[c][0]) for c in children.get(fid, ())]
                kids += [("album", a_nm) for a_nm, f in alb.values() if f == fid]
                if kids:
                    sidekids.setdefault(fold(nm), []).extend(kids)
        except Exception:
            pass
        self.people, self.firsts = people, firsts
        self.person_names, self.places, self.cameras = names, places, cameras
        self.albums, self.sidefolders, self.sidekids = albums, sidefolders, sidekids
        try:
            from . import events as _events
            evs: dict = {}
            for r in con.execute("SELECT * FROM events WHERE photos+videos>=1"):
                nm = _events.name_of(r)
                evs.setdefault(fold(nm), (nm, []))[1].append((r["start_at"], r["end_at"]))
            self.events = evs
        except Exception:
            self.events = {}
        self.sidefolder_ids = sidefolder_ids
        # the folders photos are in on disk: seen less often, they change less
        if force or time.time() - self._dirs_at > 60:
            self._dirs_at = time.time()
            dirs: dict[str, str] = {}
            try:
                paths = [r["d"] or "" for r in con.execute(
                    "SELECT DISTINCT rtrim(substr(path, 1, length(path) - length(filename)), '/\\') AS d "
                    "FROM photos WHERE trashed_at IS NULL")]
                split = [[p for p in re.split(r"[\\/]", d) if p] for d in paths]
                # what every path begins with (/home/ana/Pictures/Library) says nothing
                # about one photo: only the folders that tell photos apart are names
                common = 0
                if split:
                    first = split[0]
                    while common < len(first) and all(len(p) > common and p[common] == first[common]
                                                      for p in split):
                        common += 1
                for parts in split:
                    for part in parts[common:]:
                        key = fold(part).strip()
                        if (len(key) >= 3 and key not in STOPWORDS and key != "originals"
                                and not re.fullmatch(r"[\d\-_. ]+", key)       # 2018-06-22 is a date
                                and not key.endswith(".piklin")):
                            dirs.setdefault(key, part)
            except Exception:
                pass
            self.dirs = dirs


_vocab: dict[int, Vocabulary] = {}


def vocabulary(catalog) -> Vocabulary:
    v = _vocab.get(id(catalog))
    if v is None:
        v = _vocab[id(catalog)] = Vocabulary(catalog)
    v.refresh()
    return v


# -- reading what was typed ---------------------------------------------------
_TOKEN = re.compile(r'[^\s"]*"[^"]*"|\S+')


def tokens(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(0), m.start(), m.end()) for m in _TOKEN.finditer(text)]


def _day_bounds(d: date) -> tuple[float, float]:
    a = datetime(d.year, d.month, d.day)
    return a.timestamp(), (a + timedelta(days=1)).timestamp()


def _span(a: date, b: date) -> tuple[float, float]:
    """From the start of day a to the end of day b."""
    return _day_bounds(a)[0], _day_bounds(b)[1]


def _between(col: str, lo: float, hi: float) -> tuple[str, list]:
    return f"({col} >= ? AND {col} < ?)", [lo, hi]


def _month_end(year: int, month: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return nxt - timedelta(days=1)


# -- dates written out, and before / after / between ---------------------------
_BEFORE = {"antes", "before"}
_AFTER = {"despues", "after"}
_SINCE = {"desde", "since", "from"}
_UNTIL = {"hasta", "until", "through", "thru"}
_BETWEEN = {"entre", "between"}
_AND = {"y", "and", "a", "to", "-"}
_REAL_MONTHS = {w: n for w, n in MONTHS.items() if len(w) > 3 or w in (
    "enero", "marzo", "abril", "mayo", "junio", "julio")}


def _year(w):
    return int(w) if re.fullmatch(r"(19|20)\d\d", w) else None


def _day_no(w):
    return int(w) if re.fullmatch(r"\d{1,2}", w) and 1 <= int(w) <= 31 else None


def _point_at(folded, i):
    """A date or a stretch of time written at i: (words used, first day, last day,
    what to call it) - a year, a month and year, a whole date - or None."""
    w = folded[i]
    m = re.fullmatch(r"(\d{1,2})[/.\-](\d{1,2})[/.\-]((?:19|20)\d\d)", w)
    if m:                                       # 15/07/2019: day first, as read here
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if mo > 12 and d <= 12:                 # 07/15/2019 can only be month first
            d, mo = mo, d
        try:
            day = date(y, mo, d)
        except ValueError:
            return None
        return 1, day, day, f"{d} {_month_name(mo)} {y}"
    d = _day_no(w)
    if d is not None:                           # 15 julio 2019, 15 de julio de 2019
        j = i + 1
        if j < len(folded) and folded[j] == "de":
            j += 1
        if j < len(folded) and folded[j] in _REAL_MONTHS:
            mo = _REAL_MONTHS[folded[j]]
            j += 1
            k = j + 1 if j < len(folded) and folded[j] == "de" else j
            y = _year(folded[k]) if k < len(folded) else None
            if y:
                try:
                    day = date(y, mo, d)
                except ValueError:
                    return None
                return k + 1 - i, day, day, f"{d} {_month_name(mo)} {y}"
        return None
    if w in _REAL_MONTHS:
        mo = _REAL_MONTHS[w]
        if i + 2 < len(folded) and _day_no(folded[i + 1]) and _year(folded[i + 2]):   # julio 15 2019
            try:
                day = date(int(folded[i + 2]), mo, int(folded[i + 1]))
                return 3, day, day, f"{int(folded[i + 1])} {_month_name(mo)} {folded[i + 2]}"
            except ValueError:
                return None
        y = _year(folded[i + 1]) if i + 1 < len(folded) else None
        if y:
            return 2, date(y, mo, 1), _month_end(y, mo), f"{_month_name(mo)} {y}"
        return None
    y = _year(w)
    if y:
        return 1, date(y, 1, 1), date(y, 12, 31), str(y)
    return None


def _relation_at(folded, i):
    """before 2020, after julio 2019, since 15/03/2021, until 2022, between 2018 and 2020:
    (words used, Term) or None."""
    w = folded[i]
    kinds = ((_BEFORE, "before"), (_AFTER, "after"), (_SINCE, "since"), (_UNTIL, "until"),
             (_BETWEEN, "between"))
    for words, kind in kinds:
        if w not in words:
            continue
        j = i + 1
        if kind in ("before", "after") and j < len(folded) and folded[j] in ("de", "del", "of"):
            j += 1
        first = _point_at(folded, j) if j < len(folded) else None
        if first is None:
            return None
        n1, a1, b1, lab1 = first
        used = j + n1 - i
        if kind == "between":
            k = j + n1
            if k < len(folded) and folded[k] in _AND:
                second = _point_at(folded, k + 1) if k + 1 < len(folded) else None
                if second is not None:
                    n2, a2, b2, lab2 = second
                    lo, hi = _span(a1, b2)
                    s, p = _between("p.taken_at", lo, hi)
                    return k + 1 + n2 - i, Term("date", _("between {a} and {b}").format(a=lab1, b=lab2), s, p)
            return None
        if kind == "before":
            return used, Term("date", _("before {when}").format(when=lab1),
                              "p.taken_at < ?", [_day_bounds(a1)[0]])
        if kind == "after":
            return used, Term("date", _("after {when}").format(when=lab1),
                              "p.taken_at >= ?", [_day_bounds(b1)[1]])
        if kind == "since":
            return used, Term("date", _("since {when}").format(when=lab1),
                              "p.taken_at >= ?", [_day_bounds(a1)[0]])
        return used, Term("date", _("until {when}").format(when=lab1),
                          "p.taken_at < ?", [_day_bounds(b1)[1]])
    return None


def _day_month_at(folded, i):
    """15 de julio, 4 julio: a day of the year, any year: (words used, Term) or None."""
    d = _day_no(folded[i])
    if d is None:
        return None
    j = i + 1
    if j < len(folded) and folded[j] == "de":
        j += 1
    if j < len(folded) and folded[j] in _REAL_MONTHS:
        mo = _REAL_MONTHS[folded[j]]
        return j + 1 - i, Term(
            "date", f"{d} {_month_name(mo)}",
            "(CAST(strftime('%m', p.taken_at, 'unixepoch', 'localtime') AS INTEGER) = ? AND "
            "CAST(strftime('%d', p.taken_at, 'unixepoch', 'localtime') AS INTEGER) = ?)", [mo, d])
    return None


def _relative(what: str, today: date) -> tuple[str, str, list] | None:
    """(label, sql, params) for a phrase about time."""
    col = "p.taken_at"
    if what == "today":
        lo, hi = _day_bounds(today)
        s, p = _between(col, lo, hi)
        return _("today"), s, p
    if what == "yesterday":
        lo, hi = _day_bounds(today - timedelta(days=1))
        s, p = _between(col, lo, hi)
        return _("yesterday"), s, p
    if what in ("this_week", "last_week"):
        monday = today - timedelta(days=today.weekday())
        if what == "last_week":
            monday -= timedelta(days=7)
        lo, hi = _span(monday, monday + timedelta(days=6))
        s, p = _between(col, lo, hi)
        return (_("this week") if what == "this_week" else _("last week")), s, p
    if what in ("this_month", "last_month"):
        y, m = today.year, today.month
        if what == "last_month":
            y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        lo, hi = _span(date(y, m, 1), _month_end(y, m))
        s, p = _between(col, lo, hi)
        return (_("this month") if what == "this_month" else _("last month")), s, p
    if what in ("this_year", "last_year"):
        y = today.year - (what == "last_year")
        lo, hi = _span(date(y, 1, 1), date(y, 12, 31))
        s, p = _between(col, lo, hi)
        return (_("this year") if what == "this_year" else _("last year")), s, p
    return None


def _month_name(n: int) -> str:
    return datetime(2000, n, 1).strftime("%B")


def parse(catalog, text: str, today: date | None = None) -> Parsed:
    """Read what was typed. ``today`` is for tests."""
    today = today or date.today()
    vocab = vocabulary(catalog)
    toks = tokens(text or "")
    folded = [fold(t[0]).strip('",;') for t in toks]
    out = Parsed()
    used = [False] * len(toks)

    def take(i: int, j: int, term: Term) -> None:
        term.start, term.end = toks[i][1], toks[j - 1][2]
        out.terms.append(term)
        for k in range(i, j):
            used[k] = True

    def year_of(w: str) -> int | None:
        return int(w) if re.fullmatch(r"(19|20)\d\d", w) else None

    i = 0
    while i < len(toks):
        w = folded[i]
        raw = toks[i][0]
        # -word: not this word
        if raw.startswith("-") and len(raw) > 1 and not raw[1:].replace(".", "").isdigit():
            rest = [fold(raw[1:]).strip('",;')] + folded[i + 1:]
            # -Ana: the photos without Ana; -videos: the ones that are not videos
            hit = _person_at(vocab, rest, 0)
            if hit is not None:
                n, ids = hit
                names = sorted({vocab.person_names[p] for p in ids})
                shown = names[0] if len(names) == 1 else " / ".join(names)
                term = Term("not", "−" + shown, "NOT " + _person_sql(ids), list(ids))
                term.start, term.end = toks[i][1], toks[i + n - 1][2]
                out.terms.append(term)
                for k in range(i, i + n):
                    used[k] = True
                i += n
                continue
            kind = _word_term(rest[0], rest, 0)
            if kind is not None and kind[0] == 1 and kind[1].sql:
                term = Term("not", "−" + kind[1].label, f"NOT ({kind[1].sql})", list(kind[1].params))
                take(i, i + 1, term)
                i += 1
                continue
            out.not_words.append(fold(raw[1:]).strip('",'))
            term = Term("not", "−" + raw[1:])
            take(i, i + 1, term)
            i += 1
            continue
        # key:value
        m = re.match(r"^([a-zñ]+):(.+)$", w)
        if m and _keyed(catalog, vocab, m.group(1), m.group(2).strip('"'), toks, i, take, today):
            i += 1
            continue
        # a quoted phrase is words, kept together
        if raw.startswith('"') and raw.endswith('"') and len(raw) > 2:
            inner = fold(raw.strip('"')).strip()
            found = _named(vocab, inner)
            if found:
                take(i, i + 1, _named_term(found))
                i += 1
                continue
            if _people_named(vocab, inner):
                ids = _people_named(vocab, inner)
                names = sorted({vocab.person_names[p] for p in ids})
                take(i, i + 1, Term("person", names[0] if len(names) == 1 else " / ".join(names),
                                    _person_sql(ids), list(ids)))
                i += 1
                continue
            if inner in vocab.places:
                col, shown = vocab.places[inner]
                take(i, i + 1, Term("place", shown, f"EXISTS (SELECT 1 FROM photo_places pp WHERE "
                                                    f"pp.photo_id=p.id AND pp.{col}_f = ?)", [inner]))
                i += 1
                continue
            out.words.append(fold(raw.strip('"')))
            used[i] = True
            i += 1
            continue
        # people first: the longest name that begins here ("ana maria", then "ana")
        hit = _person_at(vocab, folded, i)
        if hit is not None:
            n, ids = hit
            names = sorted({vocab.person_names[p] for p in ids})
            take(i, i + n, Term("person", names[0] if len(names) == 1 else " / ".join(names),
                                _person_sql(ids), list(ids)))
            i += n
            continue
        # a name of several words that is an album, folder or event wins over a place that
        # is only the beginning of it ("Miami trip" is not "Miami")
        longer = None
        for n in (6, 5, 4, 3, 2):
            if i + n > len(toks) or any(used[i:i + n]):
                continue
            found = _named(vocab, " ".join(folded[i:i + n]))
            if found:
                longer = (n, found)
                break
        if longer is not None:
            take(i, i + longer[0], _named_term(longer[1]))
            i += longer[0]
            continue
        # places, up to three words ("santo domingo")
        placed = False
        for n in (3, 2, 1):
            if i + n > len(toks) or any(used[i:i + n]):
                continue
            key = " ".join(folded[i:i + n])
            if key in vocab.places:
                col, shown = vocab.places[key]
                take(i, i + n, Term("place", shown,
                                    f"EXISTS (SELECT 1 FROM photo_places pp WHERE pp.photo_id=p.id "
                                    f"AND pp.{col}_f = ?)", [key]))
                i += n
                placed = True
                break
        if placed:
            continue
        # an album or a folder of the sidebar, then a folder on disk, up to three words
        gathered = False
        for n in (6, 5, 4, 3, 2, 1):
            if i + n > len(toks) or any(used[i:i + n]):
                continue
            key = " ".join(folded[i:i + n])
            if not key or key.isdigit() or (n == 1 and key in STOPWORDS):
                continue
            found = _named(vocab, key)
            if found:
                take(i, i + n, _named_term(found))
                i += n
                gathered = True
                break
        if gathered:
            continue
        # phrases about time, place and people ("el año pasado", "sin personas")
        matched = False
        for words, what in sorted(PHRASES, key=lambda x: -len(x[0])):
            n = len(words)
            if tuple(folded[i:i + n]) == words:
                term = _phrase_term(what, today)
                if term is not None:
                    take(i, i + n, term)
                    i += n
                    matched = True
                    break
        if matched:
            continue
        # before / after / since / until / between, and dates written out
        rel = _relation_at(folded, i)
        if rel is not None:
            n, term = rel
            take(i, i + n, term)
            i += n
            continue
        pt = _point_at(folded, i)
        if pt is not None and not (pt[0] == 1 and _year(w) and re.fullmatch(r"(19|20)\d\d", w)):
            n, a, b, label = pt
            lo, hi = _span(a, b)
            sql, params = _between("p.taken_at", lo, hi)
            take(i, i + n, Term("date", label, sql, params))
            i += n
            continue
        dm = _day_month_at(folded, i)
        if dm is not None:
            n, term = dm
            take(i, i + n, term)
            i += n
            continue
        # a range of years: 2019-2021, 2019 a 2021, 2019 to 2021
        rng = re.fullmatch(r"((?:19|20)\d\d)[-–]((?:19|20)\d\d)", w)
        if rng and int(rng.group(1)) <= int(rng.group(2)):
            a, b = int(rng.group(1)), int(rng.group(2))
            s, p = _between("p.taken_at", *_span(date(a, 1, 1), date(b, 12, 31)))
            take(i, i + 1, Term("date", f"{a}–{b}", s, p))
            i += 1
            continue
        if (year_of(w) and i + 2 < len(toks) and folded[i + 1] in ("a", "to", "al", "hasta", "-")
                and year_of(folded[i + 2]) and int(w) <= int(folded[i + 2])):
            a, b = int(w), int(folded[i + 2])
            s, p = _between("p.taken_at", *_span(date(a, 1, 1), date(b, 12, 31)))
            take(i, i + 3, Term("date", f"{a}–{b}", s, p))
            i += 3
            continue
        # a month, with or without a year after it: "octubre 2022", "oct 2022", "octubre".
        # A three-letter abbreviation on its own is a word as often as a month
        # ("mar" is the sea, "may" a verb): it is a month only before a year.
        if w in MONTHS:
            mo = MONTHS[w]
            nxt = year_of(folded[i + 1]) if i + 1 < len(toks) else None
            if nxt:
                s, p = _between("p.taken_at", *_span(date(nxt, mo, 1), _month_end(nxt, mo)))
                take(i, i + 2, Term("date", f"{_month_name(mo)} {nxt}", s, p))
                i += 2
                continue
            if len(w) > 4 or w in ("junio", "julio", "mayo", "abril", "marzo", "enero"):
                take(i, i + 1, Term("date", _month_name(mo),
                                    "CAST(strftime('%m', p.taken_at, 'unixepoch', 'localtime') AS INTEGER) = ?",
                                    [mo]))
                i += 1
                continue
        # an ISO date or month: 2022-10-03, 2022-10
        iso = re.fullmatch(r"((?:19|20)\d\d)-(\d\d)(?:-(\d\d))?", w)
        if iso:
            y, mo = int(iso.group(1)), int(iso.group(2))
            try:
                if iso.group(3):
                    lo, hi = _day_bounds(date(y, mo, int(iso.group(3))))
                else:
                    lo, hi = _span(date(y, mo, 1), _month_end(y, mo))
                s, p = _between("p.taken_at", lo, hi)
                take(i, i + 1, Term("date", w, s, p))
                i += 1
                continue
            except ValueError:
                pass
        if year_of(w):
            y = int(w)
            s, p = _between("p.taken_at", *_span(date(y, 1, 1), date(y, 12, 31)))
            take(i, i + 1, Term("date", str(y), s, p))
            i += 1
            continue
        if w in WEEKDAYS:
            take(i, i + 1, Term("date", _WEEKDAY_SHOWN.get(w, w.capitalize()),
                                "CAST(strftime('%w', p.taken_at, 'unixepoch', 'localtime') AS INTEGER) = ?",
                                [WEEKDAYS[w]]))
            i += 1
            continue
        # kinds, extensions, ratings, sizes, iso, aperture
        term = _word_term(w, folded, i)
        if term is not None:
            n, t = term
            take(i, i + n, t)
            i += n
            continue
        # a camera: a whole model name
        cam = _camera_at(vocab, folded, toks, i)
        if cam is not None:
            n, shown = cam
            key = " ".join(folded[i:i + n])
            take(i, i + n, Term("camera", shown,
                                "(p.camera_model LIKE ? ESCAPE '\\' OR p.camera_make LIKE ? ESCAPE '\\')",
                                [f"%{_like(key)}%"] * 2))
            i += n
            continue
        out.words.append(w)
        used[i] = True
        i += 1
    # Little words that join others ("fotos de Ana en Miami") are not looked
    # for in file names: nothing is called "de". Unless that is all there is.
    kept = [x for x in out.words if x not in STOPWORDS]
    if kept or out.terms:
        out.words = kept
    return out


def _album_cond(ids) -> str:
    return ("p.id IN (SELECT ai.photo_id FROM album_items ai WHERE ai.album_id IN ("
            + ",".join("?" * len(ids)) + "))")


# What the person chose when a name is both an album and a folder: kept here, not in the
# text, so the box shows just the name. Cleared when the search is emptied.
_prefer: dict[str, str] = {}


def prefer(name: str, kind: str) -> None:
    _prefer[fold(name).strip()] = kind


def forget_preferences() -> None:
    _prefer.clear()


def _named(vocab, key, only=None):
    """What a name stands for: the albums, the folders of the sidebar and the folders
    on disk called that: [(kind, shown, condition, values)]. only: "album" or "folder"."""
    out = []
    if only is None:
        only = _prefer.get(key)
    if only in (None, "album") and key in vocab.albums:
        shown, ids = vocab.albums[key]
        out.append(("album", shown, _album_cond(ids), list(ids), []))
    if only in (None, "folder") and key in vocab.sidefolders:
        shown, ids = vocab.sidefolders[key]
        out.append(("folder", shown, _album_cond(ids), list(ids), list(vocab.sidefolder_ids.get(key, ()))))
    if only in (None, "folder") and key in vocab.dirs:
        out.append(("folder", vocab.dirs[key], _dir_sql(), [_dir_param(key)], []))
    if only in (None, "event") and key in vocab.events:
        shown, spans = vocab.events[key]
        cond = "(" + " OR ".join("(p.taken_at>=? AND p.taken_at<?)" for _s in spans) + ")"
        out.append(("event", shown, cond, [x for lo, hi in spans for x in (lo, hi + 0.001)], []))
    return out


def _named_term(found) -> Term:
    """One chip for what a name stands for; when it is both an album and a folder,
    both (the person chooses with the suggestions, or with album: and folder:)."""
    if len(found) == 1:
        kind, shown, sql, params, fids = found[0]
        return Term(kind, shown, sql, params, folder_ids=fids)
    sql = "(" + " OR ".join(f[2] for f in found) + ")"
    params = [p for f in found for p in f[3]]
    return Term("folder", found[0][1], sql, params)


def _dir_sql() -> str:
    return "piklin_fold(p.path) LIKE ? ESCAPE '\\'"


def _dir_param(key: str) -> str:
    # the folder's name between two slashes: "cumpleanos" is not "cumpleanos viejos"
    return "%/" + _like(key) + "/%"


def _like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _next_is_person(vocab, folded, i) -> bool:
    return i < len(folded) and _person_at(vocab, folded, i) is not None


def _last_term_is(out: Parsed, kind: str) -> bool:
    return bool(out.terms) and out.terms[-1].kind == kind


def _people_named(vocab, key: str):
    """Whoever is called this: the whole name first ("ana" is Ana, and not also
    Ana María), then a first name ("luis" is Luis García)."""
    return vocab.people.get(key) or vocab.firsts.get(key)


def _person_at(vocab, folded, i):
    for n in (3, 2, 1):
        if n == 1 and folded[i] in STOPWORDS:
            continue            # "el año pasado" is not El Tete
        if i + n <= len(folded):
            ids = _people_named(vocab, " ".join(folded[i:i + n]))
            if ids:
                return n, ids
    return None


def _person_sql(ids) -> str:
    marks = ",".join("?" * len(ids))
    # IN, not a correlated EXISTS: SQLite answered that by going through all of
    # the person's faces for every photo, 9 to 15 seconds for a person with
    # thousands of photos; this way it takes their faces once
    return (f"p.id IN (SELECT f.photo_id FROM faces f WHERE f.state<2 "
            f"AND f.person_id IN ({marks}))")


def _camera_at(vocab, folded, toks, i):
    """A camera: words that are all, or part, of a make or model the library
    has ("iphone", "iphone 13", "canon eos"): (words used, how to show them)."""
    for n in (4, 3, 2, 1):
        if i + n <= len(folded):
            phrase = " ".join(folded[i:i + n])
            if len(phrase) < 4:
                continue
            pat = re.compile(r"(^|\s)" + re.escape(phrase) + r"(\s|$)")
            if any(pat.search(key) for key in vocab.cameras):
                return n, " ".join(t[0] for t in toks[i:i + n])
    return None


def _phrase_term(what: str, today: date) -> Term | None:
    rel = _relative(what, today)
    if rel is not None:
        label, sql, params = rel
        return Term("date", label, sql, params)
    if what == "located":
        return Term("type", _("with a place"), "p.gps_lat IS NOT NULL")
    if what == "unlocated":
        return Term("type", _("without a place"), "p.gps_lat IS NULL")
    if what == "with_people":
        return Term("type", _("with people"),
                    "EXISTS (SELECT 1 FROM faces f WHERE f.photo_id=p.id AND f.state<2 "
                    "AND f.person_id IS NOT NULL)")
    if what == "no_people":
        return Term("type", _("without people"),
                    "NOT EXISTS (SELECT 1 FROM faces f WHERE f.photo_id=p.id AND f.state<2 "
                    "AND f.person_id IS NOT NULL)")
    return None


def _word_term(w: str, folded, i):
    """(words used, Term) for a word with one meaning."""
    from .catalog import SCREENSHOT_SQL, VIDEO_SQL
    for kind, words in KINDS.items():
        if w in words:
            if kind == "videos":
                return 1, Term("type", _("videos"), VIDEO_SQL)
            if kind == "photos":
                return 1, Term("type", _("photos"), f"NOT ({VIDEO_SQL})")
            if kind == "screenshots":
                return 1, Term("type", _("screenshots"), SCREENSHOT_SQL)
            if kind == "favorites":
                return 1, Term("type", _("favourites"), "p.favorite=1")
            if kind == "edited":
                return 1, Term("type", _("edited"), "p.edit_version>0")
            if kind == "vertical":
                return 1, Term("type", _("vertical"), "p.height > p.width")
            if kind == "horizontal":
                return 1, Term("type", _("horizontal"), "p.width > p.height")
            if kind == "square":
                return 1, Term("type", _("square"),
                               "p.width > 0 AND ABS(p.width - p.height) * 20 <= p.width")
            if kind == "4k":
                return 1, Term("type", "4K", f"({VIDEO_SQL}) AND MAX(p.width, p.height) >= 3840")
            if kind == "raw":
                return 1, Term("type", "RAW",
                               "p.ext IN ('cr2','cr3','nef','nrw','arw','srf','sr2','raf','orf','rw2',"
                               "'pef','dng','raw','erf','kdc','mos','mrw','x3f','3fr','iiq','rwl','srw')")
            if kind == "live":
                return 1, Term("type", "Live", "p.has_live=1")
    ext = w.lstrip(".")
    if (w.startswith(".") or ext in EXTENSIONS) and ext in EXTENSIONS:
        return 1, Term("type", "." + ext, "p.ext = ?", [ext])
    # ratings: "5 estrellas", "4+ stars", ★★★★
    if re.fullmatch(r"[★]{1,5}", w):
        return 1, Term("rating", w, "p.rating = ?", [len(w)])
    m = re.fullmatch(r"([1-5])(\+?)", w)
    if m and i + 1 < len(folded) and folded[i + 1] in ("estrella", "estrellas", "star", "stars"):
        n, plus = int(m.group(1)), m.group(2)
        return 2, Term("rating", f"{n}{'+' if plus else ''} ★",
                       "p.rating >= ?" if plus else "p.rating = ?", [n])
    # sizes: "mayor de 5mb", "> 5 mb", "menos de 1 gb"
    size = _size_at(folded, i)
    if size is not None:
        return size
    # iso 800, f/1.8
    if w == "iso" and i + 1 < len(folded) and re.fullmatch(r"\d{2,6}", folded[i + 1]):
        return 2, Term("type", f"ISO {folded[i + 1]}", "p.iso = ?", [int(folded[i + 1])])
    m = re.fullmatch(r"iso(\d{2,6})", w)
    if m:
        return 1, Term("type", f"ISO {m.group(1)}", "p.iso = ?", [int(m.group(1))])
    m = re.fullmatch(r"f/?(\d+(?:[.,]\d)?)", w)
    if m and "/" in w:
        f = float(m.group(1).replace(",", "."))
        return 1, Term("type", f"f/{f:g}", "ABS(p.f_number - ?) < 0.05", [f])
    return None


_MORE = {"mayor", "mayores", "mas", "bigger", "larger", "over", "above"}
_LESS = {"menor", "menores", "menos", "smaller", "less", "under", "below"}
_WEEKDAY_SHOWN = {"miercoles": "Miércoles", "sabado": "Sábado", "sabados": "Sábados"}
_FILL = {"de", "than", "a", "que", "to"}


def _size_at(folded, i):
    w = folded[i]
    op = None
    n = 1
    if w in _MORE or w == ">":
        op = ">"
    elif w in _LESS or w == "<":
        op = "<"
    else:
        return None
    j = i + 1
    while j < len(folded) and folded[j] in _FILL:
        j += 1
    if j >= len(folded):
        return None
    num = folded[j]
    unit = None
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)(kb|mb|gb)", num)
    if m:
        value, unit, last = float(m.group(1).replace(",", ".")), m.group(2), j + 1
    elif re.fullmatch(r"\d+(?:[.,]\d+)?", num) and j + 1 < len(folded) and folded[j + 1] in ("kb", "mb", "gb"):
        value, unit, last = float(num.replace(",", ".")), folded[j + 1], j + 2
    else:
        return None
    factor = {"kb": 1000, "mb": 1000 ** 2, "gb": 1000 ** 3}[unit]
    label = f"{'>' if op == '>' else '<'} {value:g} {unit.upper()}"
    return last - i, Term("size", label, f"p.bytes {op} ?", [int(value * factor)])


def _keyed(catalog, vocab, key, value, toks, i, take, today) -> bool:
    """person:Ana, camera:iphone, year:2022, place:miami, type:videos, rating:5."""
    v = fold(value)
    if key in ("person", "persona", "who", "quien"):
        ids = _people_named(vocab, v)
        if not ids:
            return False
        names = sorted({vocab.person_names[p] for p in ids})
        take(i, i + 1, Term("person", names[0], _person_sql(ids), list(ids)))
        return True
    if key in ("camera", "camara", "cam"):
        take(i, i + 1, Term("camera", value, "(p.camera_make LIKE ? ESCAPE '\\' OR p.camera_model LIKE ? ESCAPE '\\')",
                            [f"%{_like(v)}%"] * 2))
        return True
    if key in ("lens", "lente"):
        take(i, i + 1, Term("camera", value, "p.lens LIKE ? ESCAPE '\\'", [f"%{_like(v)}%"]))
        return True
    if key in ("year", "ano", "año"):
        if re.fullmatch(r"(19|20)\d\d", v):
            y = int(v)
            s, p = _between("p.taken_at", *_span(date(y, 1, 1), date(y, 12, 31)))
            take(i, i + 1, Term("date", v, s, p))
            return True
        return False
    if key in ("place", "lugar", "city", "ciudad", "country", "pais"):
        col, shown = vocab.places.get(v, (None, None))
        if col is None:
            return False
        take(i, i + 1, Term("place", shown, f"EXISTS (SELECT 1 FROM photo_places pp WHERE "
                                          f"pp.photo_id=p.id AND pp.{col}_f = ?)", [v]))
        return True
    if key in ("folder", "carpeta", "dir", "directorio", "album", "albumes", "álbum"):
        only = "album" if key in ("album", "albumes", "álbum") else "folder"
        found = _named(vocab, v, only)
        if not found:                    # a part of a name is enough
            part = next((k for k in list(vocab.albums if only == "album" else ()) +
                         list(vocab.sidefolders if only == "folder" else ()) +
                         list(vocab.dirs if only == "folder" else ()) if v and v in k), None)
            found = _named(vocab, part, only) if part else []
        if not found:
            return False
        take(i, i + 1, _named_term(found))
        return True
    if key in ("ext", "type", "tipo", "format", "formato"):
        ext = v.lstrip(".")
        if ext in EXTENSIONS:
            take(i, i + 1, Term("type", "." + ext, "p.ext = ?", [ext]))
            return True
        for kind, words in KINDS.items():
            if v in words:
                t = _word_term(v, [v], 0)
                if t:
                    take(i, i + 1, t[1])
                    return True
        return False
    if key in ("rating", "estrellas", "stars"):
        m = re.fullmatch(r"([1-5])(\+?)", v)
        if m:
            n = int(m.group(1))
            take(i, i + 1, Term("rating", f"{n}{m.group(2)} ★",
                                "p.rating >= ?" if m.group(2) else "p.rating = ?", [n]))
            return True
    return False


# -- using it ------------------------------------------------------------------
def compile(catalog, text: str, today: date | None = None) -> tuple[list[str], list, list[str], Parsed]:
    """(conditions, their values, words left to look for, what was understood):
    what the catalog needs to find the photos, and what the window needs to show
    the person how it was read."""
    parsed = parse(catalog, text, today)
    where: list[str] = []
    params: list = []
    # people are all there: "Ana y Luis" are the photos with both
    for t in parsed.terms:
        if t.sql:
            where.append(t.sql)
            params.extend(t.params)
    return where, params, parsed.words, parsed


class Put(str):
    """What a suggestion puts in the box, and the folder it was found in, whose
    name is what was typed."""
    def __new__(cls, text, parent=""):
        obj = super().__new__(cls, text)
        obj.parent = parent
        return obj


def words_match(typed, name_key) -> bool:
    """Whether every word typed begins a word of the name (folded)."""
    parts = name_key.split()
    return all(any(p.startswith(w) for p in parts) for w in typed)


def keyed(kind_key, name):
    return f'{kind_key}:"{name}"' if " " in name else f"{kind_key}:{name}"


def folder_for(catalog, text: str):
    """The folder of the sidebar the text is about, when it is only that: its own name,
    or the words of it ("viaje disney" for "Viaje Disney 2018"), and nothing else - the
    search then shows the folder with its albums, as clicking it does. None otherwise."""
    if not (text or "").strip():
        return None
    parsed = parse(catalog, text)
    if parsed.not_words:
        return None
    if (len(parsed.terms) == 1 and not parsed.words and parsed.terms[0].kind == "folder"
            and len(parsed.terms[0].folder_ids) == 1):
        return parsed.terms[0].folder_ids[0]
    vocab = vocabulary(catalog)
    ws = [w for w in (fold(t[0]) for t in tokens(text)) if w and w not in STOPWORDS]
    if not ws or any(len(w) < 2 for w in ws):
        return None
    if len(ws) == 1 and any(t.kind != "folder" for t in parsed.terms):
        return None             # one word that is a place, a person, an album: that is what was asked for
    keys = [k for k in vocab.sidefolders if words_match(ws, k)]
    if len(keys) == 1 and len(vocab.sidefolder_ids.get(keys[0], ())) == 1:
        return vocab.sidefolder_ids[keys[0]][0]
    return None


def suggest(catalog, text: str, limit: int = 8) -> list[tuple[str, str, str]]:
    """What the last word typed could become: (kind, what to show, what to put
    in its place). Names of people, places, cameras, months, years, kinds."""
    toks = tokens(text or "")
    if not toks or (text and text[-1].isspace()):
        return []
    last = fold(toks[-1][0]).lstrip("-")
    if len(last) < 1 or ":" in last:
        return []
    vocab = vocabulary(catalog)
    out: list[tuple[str, str, str]] = []
    seen = set()

    def add(kind, shown, put=None):
        key = (kind, fold(shown))
        if key not in seen and fold(shown) != last and len(out) < limit * 4:
            seen.add(key)
            out.append((kind, shown, put or shown))

    for pid, name in sorted(vocab.person_names.items(), key=lambda x: x[1]):
        if fold(name).startswith(last) or any(part.startswith(last) for part in fold(name).split()):
            add("person", name)
    for key, (_col, shown) in vocab.places.items():
        if key.startswith(last):
            add("place", shown)
    # albums and folders: the words typed so far - as many of the last ones as name
    # something - each beginning a word of the name ("viaje disney" is "Viaje Disney 2018")
    words_typed = [fold(t[0]).lstrip("-") for t in toks[-4:]]

    every_name = list(vocab.sidefolders) + list(vocab.dirs) + list(vocab.albums) + list(vocab.events)
    typed_n = 0
    for n in (4, 3, 2, 1):
        if n > len(words_typed):
            continue
        ws = words_typed[-n:]
        if any(":" in w for w in ws) or (n == 1 and len(ws[0]) < 2):
            continue
        if any(words_match(ws, k) for k in every_name):
            typed_n = n         # the same words for folders and albums, so a choice replaces them all
            break

    def named_by_typed(names):
        if not typed_n:
            return []
        ws = words_typed[-typed_n:]
        return sorted(k for k in names if words_match(ws, k))[:6]
    folder_hits = named_by_typed(vocab.sidefolders)
    for key in folder_hits:
        add("folder", f"{vocab.sidefolders[key][0]}  ·  " + _("Folder"), vocab.sidefolders[key][0])
    # and what each of those folders holds, so the one wanted can be chosen
    # ("viaje disney": Epcot, Magic Kingdom, Hollywood Studios...)
    for key in folder_hits[:2]:
        parent = vocab.sidefolders[key][0]
        for kind, name in vocab.sidekids.get(key, ())[:12]:
            label = _("Folder") if kind == "folder" else _("Album")
            add(kind, f"{name}  ·  {label}  ·  " + _("in {folder}").format(folder=parent), Put(name, parent))
    seen_folders = set(vocab.sidefolders)
    for key in named_by_typed([k for k in vocab.dirs if k not in seen_folders]):
        add("folder", f"{vocab.dirs[key]}  ·  " + _("Folder on disk"), vocab.dirs[key])
    for key in named_by_typed(vocab.events):
        add("event", f"{vocab.events[key][0]}  ·  " + _("Event"), vocab.events[key][0])
    for key in named_by_typed(vocab.albums):
        add("album", f"{vocab.albums[key][0]}  ·  " + _("Album"), vocab.albums[key][0])
    for key, shown in vocab.cameras.items():
        if key.startswith(last) and len(last) >= 2:
            add("camera", shown)
    for word, n in MONTHS.items():
        if len(word) > 3 and word.startswith(last) and len(last) >= 2:
            add("date", _month_name(n))
    for word, n in WEEKDAYS.items():
        if word.startswith(last) and len(last) >= 2 and not word.endswith("s"):
            add("date", word.capitalize())
    if last.isdigit() and len(last) >= 2:
        try:
            for r in catalog.con.execute(
                    "SELECT DISTINCT strftime('%Y', taken_at, 'unixepoch', 'localtime') AS y "
                    "FROM photos WHERE taken_at IS NOT NULL ORDER BY y DESC"):
                if r["y"] and r["y"].startswith(last):
                    add("date", r["y"])
        except Exception:
            pass
    for label, words in (("videos", ("videos", "video")), ("photos", ("fotos", "photos")),
                         ("favourites", ("favoritas", "favorites")),
                         ("screenshots", ("capturas", "screenshots")),
                         ("edited", ("editadas", "edited"))):
        for wd in words:
            if wd.startswith(last) and len(last) >= 2:
                add("type", wd)
                break
    # people first, then places, then the rest
    order = {"person": 0, "place": 1, "event": 1.5, "folder": 2, "album": 3, "date": 4, "type": 5, "camera": 6}
    if last.isdigit():             # typing a number: years first, then what is named with one
        order["date"] = 1.5
    out.sort(key=lambda s: order.get(s[0], 9))
    return out[:max(limit, 12) if folder_hits else limit]


def without(text: str, term: Term) -> str:
    """The text with one chip's words taken out."""
    return " ".join((text[:term.start] + " " + text[term.end:]).split())


# the gettext marker, so that the labels above are translated
from .i18n import _  # noqa: E402
