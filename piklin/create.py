"""Making something out of your photos: the page underneath every
creation, and how it is drawn.

One creation is a list of pages; one page is photos in their places, and
words over them. A collage, a poster, a calendar month, a card and a page
of a book differ in what fills the page, not in how it is drawn, so they
all come through here, and the drawing is done once.

Nothing in this file knows about the interface or the catalog: a page is
data, and rendering it is a function of that data and the photo files, so
a creation can be drawn again at any size, on any computer, from what was
written down - which is what makes a creation you saved still editable
tomorrow, and the same on your other computers.
"""
from __future__ import annotations

import math
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Shapes a page can have, as width:height. Paper sizes are the real
# thing rather than an approximation, so a poster sent to a print shop
# comes back with the photographs where they were put.
SHAPES: dict[str, tuple[float, float]] = {
    "square": (1.0, 1.0),
    "portrait": (4.0, 5.0),        # what a phone screen and Instagram like
    "landscape": (3.0, 2.0),
    "wide": (16.0, 9.0),
    "a4": (210.0, 297.0),
    "a4-landscape": (297.0, 210.0),
    "a3": (297.0, 420.0),
    "letter": (8.5, 11.0),
    "letter-landscape": (11.0, 8.5),
}

# Printing wants 300 dots per inch; a screen wants pixels. Both are just
# a long side in pixels, so one number covers them.
PRINT_DPI = 300


def shape_aspect(shape: str) -> float:
    w, h = SHAPES.get(shape, SHAPES["square"])
    return w / h


# ======================================================================
# what a page is made of
# ======================================================================
@dataclass
class Slot:
    """One photograph in its place on the page.

    Positions are fractions of the page, not pixels, so the same page
    draws at any size: on screen while you arrange it, and at print size
    when you are done.
    """
    photo_id: int = 0
    path: str = ""
    x: float = 0.0
    y: float = 0.0
    w: float = 1.0
    h: float = 1.0
    # How the photo sits inside its place: zoomed in a little, and which
    # part of it the place shows. A face near the edge is kept by moving
    # the middle, never by squashing the photo.
    zoom: float = 1.0
    cx: float = 0.5
    cy: float = 0.5

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Slot":
        return cls(**{k: v for k, v in (d or {}).items()
                      if k in cls.__dataclass_fields__})


@dataclass
class Text:
    """Words on the page: a title, a date, a name."""
    text: str = ""
    x: float = 0.5
    y: float = 0.5
    w: float = 0.8
    size: float = 0.06            # of the page's height
    weight: str = "bold"          # regular | medium | semibold | bold
    family: str = "inter"         # a key of create.FAMILIES
    chosen_family: bool = False   # True once someone picked it themselves
    color: str = "#111111"
    align: str = "center"         # left | center | right
    tracking: float = 0.0         # extra space between letters, of the size

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Text":
        return cls(**{k: v for k, v in (d or {}).items()
                      if k in cls.__dataclass_fields__})


@dataclass
class Page:
    shape: str = "square"
    # A colour, or "a,b" for a wash from one colour to the other.
    background: str = "#ffffff"
    margin: float = 0.035         # of the shorter side
    gap: float = 0.012
    corner: float = 0.0           # rounded photo corners, of the shorter side
    # The look a theme gives the photos on it. All of these are fractions
    # of the shorter side, so a page drawn at any size looks the same.
    theme: str = "clean"
    frame: float = 0.0            # a border around each photo, like a print
    frame_color: str = "#ffffff"
    frame_foot: float = 0.0       # extra border below it, as a Polaroid has
    shadow: float = 0.0           # how far the photo's shadow reaches
    tilt: float = 0.0             # how far photos may lean, in degrees
    sprinkle: str = ""            # dots | hearts | stars | snow
    sprinkle_color: str = "#ffffff"
    # A band at the foot kept clear for the words, as a fraction of the
    # page's height. Words with no room of their own land on a face.
    text_room: float = 0.0
    slots: list[Slot] = field(default_factory=list)
    texts: list[Text] = field(default_factory=list)

    @property
    def aspect(self) -> float:
        return shape_aspect(self.shape)

    def to_dict(self) -> dict:
        return {"shape": self.shape, "background": self.background,
                "margin": self.margin, "gap": self.gap, "corner": self.corner,
                "theme": self.theme, "frame": self.frame,
                "frame_color": self.frame_color, "frame_foot": self.frame_foot,
                "shadow": self.shadow, "tilt": self.tilt,
                "sprinkle": self.sprinkle, "sprinkle_color": self.sprinkle_color,
                "text_room": self.text_room,
                "slots": [s.to_dict() for s in self.slots],
                "texts": [t.to_dict() for t in self.texts]}

    @classmethod
    def from_dict(cls, d: dict) -> "Page":
        d = d or {}
        return cls(shape=d.get("shape", "square"),
                   background=d.get("background", "#ffffff"),
                   margin=float(d.get("margin", 0.035)),
                   gap=float(d.get("gap", 0.012)),
                   corner=float(d.get("corner", 0.0)),
                   theme=d.get("theme", "clean"),
                   frame=float(d.get("frame", 0.0)),
                   frame_color=d.get("frame_color", "#ffffff"),
                   frame_foot=float(d.get("frame_foot", 0.0)),
                   shadow=float(d.get("shadow", 0.0)),
                   tilt=float(d.get("tilt", 0.0)),
                   sprinkle=d.get("sprinkle", ""),
                   sprinkle_color=d.get("sprinkle_color", "#ffffff"),
                   text_room=float(d.get("text_room", 0.0)),
                   slots=[Slot.from_dict(s) for s in d.get("slots") or []],
                   texts=[Text.from_dict(t) for t in d.get("texts") or []])


# ======================================================================
# themes: the look of a page, as a whole
# ======================================================================
# Every theme is drawn by Piklin itself - the colours, the frames, the
# shadows and the little shapes scattered between the photos - so there is
# nothing to download, nothing that expires, and nothing whose licence
# could stop us from shipping it.
THEMES: dict[str, dict] = {
    "clean": {
        "name": "Clean", "background": "#ffffff", "margin": 0.035,
        "gap": 0.012, "corner": 0.008, "text_color": "#111111",
        "family": "inter",
    },
    "gallery": {
        "name": "Gallery", "background": "#f7f6f4", "margin": 0.085,
        "gap": 0.022, "corner": 0.0, "frame": 0.004, "frame_color": "#ffffff",
        "shadow": 0.012, "text_color": "#111111", "text_weight": "medium",
        "tracking": 0.22, "family": "montserrat",
    },
    "polaroid": {
        "name": "Polaroid", "background": "#e9e4dc", "margin": 0.05,
        "gap": 0.028, "corner": 0.0, "frame": 0.012, "frame_foot": 0.03,
        "frame_color": "#fffdf8", "shadow": 0.016, "tilt": 2.6,
        "text_color": "#3a332b", "text_weight": "medium",
        "family": "caveat",
    },
    "midnight": {
        "name": "Midnight", "background": "#0e1116,#1b2430", "margin": 0.04,
        "gap": 0.008, "corner": 0.004, "text_color": "#ffffff",
        "tracking": 0.28, "family": "montserrat",
    },
    "cream": {
        "name": "Cream", "background": "#f6efe4,#efe3d2", "margin": 0.06,
        "gap": 0.018, "corner": 0.014, "shadow": 0.008,
        "text_color": "#4a3f31", "text_weight": "medium", "tracking": 0.3,
        "family": "cormorant",
    },
    "party": {
        "name": "Party", "background": "#2a1a52,#5b2a86", "margin": 0.055,
        "gap": 0.016, "corner": 0.02, "frame": 0.005, "frame_color": "#ffffff",
        "shadow": 0.012, "tilt": 1.6, "sprinkle": "dots",
        "sprinkle_color": "#ffd166", "text_color": "#ffffff",
        "family": "bebas",
    },
    "hearts": {
        "name": "Hearts", "background": "#fdeef1,#f8dbe3", "margin": 0.06,
        "gap": 0.018, "corner": 0.03, "frame": 0.006, "frame_color": "#ffffff",
        "shadow": 0.01, "sprinkle": "hearts", "sprinkle_color": "#e8879c",
        "text_color": "#8a3a4e", "text_weight": "medium",
        "family": "dancing",
    },
    "winter": {
        "name": "Winter", "background": "#0f3b33,#14524a", "margin": 0.055,
        "gap": 0.016, "corner": 0.012, "frame": 0.006, "frame_color": "#f7fbfa",
        "shadow": 0.012, "sprinkle": "snow", "sprinkle_color": "#ffffff",
        "text_color": "#ffffff", "tracking": 0.24, "family": "playfair",
    },
    "little": {
        "name": "Little One", "background": "#eef5fb,#e6eefb", "margin": 0.06,
        "gap": 0.02, "corner": 0.035, "frame": 0.006, "frame_color": "#ffffff",
        "shadow": 0.01, "sprinkle": "stars", "sprinkle_color": "#b9cdf0",
        "text_color": "#41597f", "text_weight": "medium",
        "family": "josefin",
    },
}

THEME_ORDER = ("clean", "gallery", "polaroid", "cream", "midnight",
               "party", "hearts", "winter", "little")


def apply_theme(page: Page, key: str) -> None:
    """Give the page a theme's whole look. The photos keep their places;
    only how they are dressed changes, so switching themes never loses the
    arranging someone has done."""
    theme = THEMES.get(key) or THEMES["clean"]
    page.theme = key if key in THEMES else "clean"
    page.background = theme.get("background", "#ffffff")
    page.margin = float(theme.get("margin", 0.035))
    page.gap = float(theme.get("gap", 0.012))
    page.corner = float(theme.get("corner", 0.0))
    page.frame = float(theme.get("frame", 0.0))
    page.frame_color = theme.get("frame_color", "#ffffff")
    page.frame_foot = float(theme.get("frame_foot", 0.0))
    page.shadow = float(theme.get("shadow", 0.0))
    page.tilt = float(theme.get("tilt", 0.0))
    page.sprinkle = theme.get("sprinkle", "")
    page.sprinkle_color = theme.get("sprinkle_color", "#ffffff")
    for t in page.texts:
        t.color = theme.get("text_color", "#111111")
        t.weight = theme.get("text_weight", "bold")
        t.tracking = float(theme.get("tracking", 0.08))
        # A theme brings its own lettering, unless a typeface was chosen
        # by hand: choosing one is a decision, and a theme does not undo
        # the decisions someone has already made.
        if not getattr(t, "chosen_family", False):
            t.family = theme.get("family", "inter")


# ======================================================================
# where the photographs go
# ======================================================================
def _rows_by_shape(aspects: list[float], rows: int) -> list[list[int]]:
    """Cut the photos into ``rows`` rows, keeping their order, so that the
    rows come out as even as possible.

    Evenness is measured on the widths a row would need, which is what
    decides how tall it ends up: a row of three wide landscapes is short,
    a row of three portraits is tall. Order is kept because a collage of a
    day reads as that day when its photos stay in the order they happened.
    """
    n = len(aspects)
    if rows <= 1 or n <= 1:
        return [list(range(n))]
    rows = min(rows, n)
    # Each row's height comes from the widths it has to fit, so rows whose
    # photos add up to the same width come out the same height. The cut
    # that gets closest to that is worth finding properly: walking along
    # and closing a row when it looks full leaves a row of seven stamps
    # above a row of two billboards.
    target = sum(aspects) / rows
    total = [0.0]
    for a in aspects:
        total.append(total[-1] + a)

    def cost(i: int, j: int) -> float:         # photos i..j-1 in one row
        return (total[j] - total[i] - target) ** 2

    INF = float("inf")
    # best[r][i]: the cost of covering photos i.. with r rows left.
    best = [[INF] * (n + 1) for _ in range(rows + 1)]
    cut = [[0] * (n + 1) for _ in range(rows + 1)]
    best[0][n] = 0.0
    for r in range(1, rows + 1):
        for i in range(n, -1, -1):
            # Every row takes at least one photo, and the rows still to
            # come need one each.
            for j in range(i + 1, n - (r - 1) + 1):
                if best[r - 1][j] == INF:
                    continue
                c = cost(i, j) + best[r - 1][j]
                if c < best[r][i]:
                    best[r][i] = c
                    cut[r][i] = j
    out: list[list[int]] = []
    i, r = 0, rows
    while r > 0 and i < n:
        j = cut[r][i] or n
        out.append(list(range(i, j)))
        i, r = j, r - 1
    if i < n:                                  # nothing left behind
        out[-1].extend(range(i, n))
    return [g for g in out if g]


def mosaic(aspects: list[float], page_aspect: float, gap: float = 0.012,
           rows: int | None = None) -> list[tuple[float, float, float, float]]:
    """Rows of photographs, each row full width, every photo at close to
    its own shape - the arrangement that wastes the least of a photo.

    Returns rectangles in page fractions: (x, y, w, h).
    """
    n = len(aspects)
    if n == 0:
        return []
    if n == 1:
        return [(0.0, 0.0, 1.0, 1.0)]

    def build(r: int):
        groups = _rows_by_shape(aspects, r)
        heights = []
        for g in groups:
            # Widths in a row share one height: w_i = h * a_i / A.
            usable = 1.0 - gap * (len(g) - 1)
            total_a = sum(aspects[i] for i in g) or 1.0
            heights.append(page_aspect * usable / total_a)
        return groups, heights

    # The number of rows that fills the page most nearly on its own needs
    # the least stretching afterwards, so the photos are cropped least.
    best = None
    for r in range(1, min(n, 10) + 1):
        groups, heights = build(r)
        total = sum(heights) + gap * (len(groups) - 1)
        score = abs(math.log(total)) if total > 0 else 1e9
        if best is None or score < best[0]:
            best = (score, groups, heights)
    _score, groups, heights = best

    # Fill the page exactly: the rows are scaled to the page's height, and
    # each row keeps the full width, so photos are cropped a little rather
    # than leaving a band of background at the foot of the page.
    space = 1.0 - gap * (len(groups) - 1)
    scale = space / sum(heights) if sum(heights) > 0 else 1.0
    rects: list[tuple[float, float, float, float]] = []
    by_index: dict[int, tuple[float, float, float, float]] = {}
    y = 0.0
    for g, h in zip(groups, heights):
        rh = h * scale
        usable = 1.0 - gap * (len(g) - 1)
        total_a = sum(aspects[i] for i in g) or 1.0
        x = 0.0
        for i in g:
            w = usable * aspects[i] / total_a
            by_index[i] = (x, y, w, rh)
            x += w + gap
        y += rh + gap
    rects = [by_index[i] for i in range(n)]
    return rects


def grid(n: int, page_aspect: float, gap: float = 0.012,
         columns: int | None = None) -> list[tuple[float, float, float, float]]:
    """Even squares, in reading order: the arrangement for a calendar, a
    contact sheet, a set of portraits that should all weigh the same."""
    if n <= 0:
        return []
    if columns is None:
        ideal = max(1.0, math.sqrt(n * page_aspect))
        # A grid that comes out even is worth a column either side of the
        # ideal: three rows of four read as a grid, five and five and two
        # read as a mistake.
        # One photo per row is a column, not a grid, so it is only an
        # answer when there are one or two photos to place.
        low = max(2 if n >= 3 else 1, int(math.floor(ideal)) - 1)
        high = min(n, int(math.ceil(ideal)) + 1)
        columns = min(range(low, high + 1),
                      key=lambda c: (math.ceil(n / c) * c - n, abs(c - ideal)))
    rows = math.ceil(n / columns)
    w = (1.0 - gap * (columns - 1)) / columns
    h = (1.0 - gap * (rows - 1)) / rows
    out = []
    for i in range(n):
        r, c = divmod(i, columns)
        in_row = min(columns, n - r * columns)
        # A last row that is short sits in the middle, not against the
        # left edge with a hole beside it.
        indent = (columns - in_row) * (w + gap) / 2
        out.append((indent + c * (w + gap), r * (h + gap), w, h))
    return out


def feature(n: int, page_aspect: float, gap: float = 0.012,
            share: float = 0.62) -> list[tuple[float, float, float, float]]:
    """One photograph leads and the rest follow beside it - the poster
    arrangement, where the first photo is the one you chose it for."""
    if n <= 0:
        return []
    if n == 1:
        return [(0.0, 0.0, 1.0, 1.0)]
    rest = n - 1
    if page_aspect >= 1.0:                     # lead on the left
        lead = (0.0, 0.0, share, 1.0)
        x = share + gap
        w = 1.0 - x
        h = (1.0 - gap * (rest - 1)) / rest
        out = [lead] + [(x, i * (h + gap), w, h) for i in range(rest)]
    else:                                      # lead on top
        lead = (0.0, 0.0, 1.0, share)
        y = share + gap
        h = 1.0 - y
        w = (1.0 - gap * (rest - 1)) / rest
        out = [lead] + [(i * (w + gap), y, w, h) for i in range(rest)]
    return out


LAYOUTS = ("auto", "mosaic", "grid", "feature")


def arrange(page: Page, aspects: list[float], style: str = "auto") -> None:
    """Put the page's photographs in their places, in the given style.

    The page's own margin and gap are honoured, so the same photos can be
    rearranged without touching anything else about the page.
    """
    n = len(page.slots)
    if n == 0:
        return
    aspects = [a if a and a > 0 else 1.0 for a in aspects[:n]]
    while len(aspects) < n:
        aspects.append(1.0)
    # Inside the margin the page has a shape of its own, and the band
    # kept for the words is not part of it.
    top = page.margin * page.aspect
    inner_w = 1.0 - 2 * page.margin
    inner_h = 1.0 - 2 * top - max(0.0, page.text_room)
    if inner_h <= 0.05:
        inner_h = max(0.05, 1.0 - 2 * top)
    inner_aspect = page.aspect * inner_w / inner_h if inner_h else page.aspect
    gap = page.gap / inner_w if inner_w else page.gap

    if style == "auto":
        style = "mosaic" if n > 2 else "grid"
    if style == "grid":
        rects = grid(n, inner_aspect, gap)
    elif style == "feature":
        rects = feature(n, inner_aspect, gap)
    else:
        rects = mosaic(aspects, inner_aspect, gap)

    for slot, (x, y, w, h) in zip(page.slots, rects):
        slot.x = page.margin + x * inner_w
        slot.y = top + y * inner_h
        slot.w = w * inner_w
        slot.h = h * inner_h
    if page.text_room > 0:
        # The words sit in the middle of their band, under the photos.
        band_top = top + inner_h
        for t in page.texts:
            lines = max(1, len(str(t.text).splitlines()))
            t.y = band_top + max(0.0, (page.text_room - t.size * 1.25 * lines) / 2)


def make_page(photos, shape: str = "square", style: str = "auto",
              background: str = "#ffffff", margin: float = 0.035,
              gap: float = 0.012, corner: float = 0.0) -> Page:
    """A page holding these photos, arranged.

    ``photos`` are dicts with at least ``path``; ``id``, ``width`` and
    ``height`` are used when they are there, which is what the catalog
    already knows, so no file has to be opened to lay a page out.
    """
    page = Page(shape=shape, background=background, margin=margin, gap=gap,
                corner=corner)
    aspects = []
    for p in photos:
        w, h = float(p.get("width") or 0), float(p.get("height") or 0)
        aspects.append((w / h) if w > 0 and h > 0 else 1.0)
        page.slots.append(Slot(photo_id=int(p.get("id") or 0),
                               path=str(p.get("path") or "")))
    arrange(page, aspects, style)
    return page


def focus_slots(page: Page, load=None, on_progress=None) -> int:
    """Move each photo inside its place so that the faces stay in.

    A place is rarely the shape of the photo in it, so something is
    always cropped away; cropping from the middle is what cuts the top
    of a head off. Where Piklin can see faces it shows that part of the
    photo instead. Returns how many photos were moved.
    """
    import numpy as np
    from .engine import faces as fc
    if not fc.available():
        return 0
    if load is None:
        from .imageio import load_rgb as load

    moved = 0
    total = max(1, len(page.slots))
    for i, slot in enumerate(page.slots):
        if on_progress is not None:
            on_progress(i / total)
        if not slot.path or slot.w <= 0 or slot.h <= 0:
            continue
        try:
            img = load(slot.path, max_side=640)
        except Exception:
            continue
        ih, iw = img.shape[:2]
        if not iw or not ih:
            continue
        try:
            found = fc.detect(img)
        except Exception:
            continue
        if not found:
            continue
        # The part of the photo the place will show, as a fraction.
        photo_aspect = iw / ih
        slot_aspect = (slot.w * page.aspect) / slot.h if slot.h else photo_aspect
        show_w = min(1.0, slot_aspect / photo_aspect)
        show_h = min(1.0, photo_aspect / slot_aspect)
        if show_w > 0.995 and show_h > 0.995:
            continue                            # nothing is being cut off
        xs = [f.center[0] for f in found]
        ys = [f.center[1] for f in found]
        # A little above the faces' middle: heads look right with air
        # over them, and this is where a group photo's bodies are.
        cx = float(np.clip((min(xs) + max(xs)) / 2, 0.0, 1.0))
        cy = float(np.clip((min(ys) + max(ys)) / 2 - 0.05, 0.0, 1.0))
        # cx/cy are read as "which part of the oversized photo to keep",
        # so the wanted middle maps onto the room there is to slide.
        room_x = 1.0 - show_w
        room_y = 1.0 - show_h
        new_cx = float(np.clip((cx - show_w / 2) / room_x, 0.0, 1.0)) if room_x > 1e-6 else 0.5
        new_cy = float(np.clip((cy - show_h / 2) / room_y, 0.0, 1.0)) if room_y > 1e-6 else 0.5
        if abs(new_cx - slot.cx) > 0.01 or abs(new_cy - slot.cy) > 0.01:
            slot.cx, slot.cy = new_cx, new_cy
            moved += 1
    if on_progress is not None:
        on_progress(1.0)
    return moved


# ======================================================================
# drawing it
# ======================================================================
def _font_dir() -> Path | None:
    """Where the typeface Piklin carries lives, in a package and in the
    source tree alike."""
    here = Path(__file__).resolve().parent
    candidates = [here.parent / "data" / "fonts", Path("/usr/share/piklin/fonts")]
    if os.name == "nt":
        candidates.append(here.parents[1] / "data" / "fonts")
    if os.environ.get("APPDIR"):
        candidates.insert(0, Path(os.environ["APPDIR"]) / "usr/share/piklin/data/fonts")
    for c in candidates:
        if c.is_dir():
            return c
    return None


_WEIGHTS = {"regular": "Inter-Regular.ttf", "medium": "Inter-Medium.ttf",
            "semibold": "Inter-SemiBold.ttf", "bold": "Inter-Bold.ttf"}

# The typefaces Piklin carries, for the words on a page. Every one of them
# is under the SIL Open Font Licence and travels inside the app, so a
# creation opens with its own lettering on every computer - including one
# that has never had these typefaces installed - and nothing here depends
# on a font shop or a font staying free.
FAMILIES: dict[str, dict] = {
    "inter":      {"name": "Inter",              "dir": "Inter", "weights": True},
    "playfair":   {"name": "Playfair Display",   "dir": "PlayfairDisplay"},
    "lora":       {"name": "Lora",               "dir": "Lora"},
    "baskerville": {"name": "Libre Baskerville", "dir": "LibreBaskerville"},
    "cormorant":  {"name": "Cormorant Garamond", "dir": "CormorantGaramond"},
    "montserrat": {"name": "Montserrat",         "dir": "Montserrat"},
    "josefin":    {"name": "Josefin Sans",       "dir": "JosefinSans"},
    "oswald":     {"name": "Oswald",             "dir": "Oswald"},
    "bebas":      {"name": "Bebas Neue",         "dir": "BebasNeue"},
    "abril":      {"name": "Abril Fatface",      "dir": "AbrilFatface"},
    "caveat":     {"name": "Caveat",             "dir": "Caveat"},
    "dancing":    {"name": "Dancing Script",     "dir": "DancingScript"},
    "pacifico":   {"name": "Pacifico",           "dir": "Pacifico"},
    "greatvibes": {"name": "Great Vibes",        "dir": "GreatVibes"},
}

FAMILY_ORDER = ("inter", "montserrat", "josefin", "oswald", "bebas",
                "playfair", "lora", "baskerville", "cormorant", "abril",
                "caveat", "dancing", "pacifico", "greatvibes")

# What a weight is called inside a variable typeface.
_VARIATIONS = {"regular": "Regular", "medium": "Medium",
               "semibold": "SemiBold", "bold": "Bold"}


def family_name(key: str) -> str:
    return (FAMILIES.get(key) or FAMILIES["inter"])["name"]


def family_file(key: str) -> Path | None:
    """The file a family is drawn from, wherever Piklin is installed."""
    d = _font_dir()
    if d is None:
        return None
    spec = FAMILIES.get(key) or FAMILIES["inter"]
    folder = d / spec["dir"]
    if not folder.is_dir():
        return None
    files = sorted(folder.glob("*.ttf"))
    upright = [f for f in files if "italic" not in f.name.lower()]
    return (upright or files or [None])[0]


def font(weight: str, px: int, family: str = "inter"):
    """The typeface a page's words are drawn in, at this size.

    The letters are laid out by Pillow's own engine rather than the
    Raqm one it prefers. Inside Piklin, Raqm shares a process with the
    HarfBuzz that GTK draws the interface with, and the two disagree:
    Raqm then reports letter widths in the millions of pixels, and a
    title drawn from them lands a mile off the side of the page - the
    page comes out with no title on it and nothing says why.
    """
    from PIL import ImageFont
    d = _font_dir()
    candidates = []
    if family and family != "inter":
        chosen = family_file(family)
        if chosen is not None:
            candidates.append(chosen)
    if d is not None:
        name = _WEIGHTS.get(weight, _WEIGHTS["bold"])
        candidates += [d / "Inter" / name, d / name]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            f = ImageFont.truetype(str(candidate), px,
                                   layout_engine=ImageFont.Layout.BASIC)
        except (OSError, AttributeError):
            try:
                f = ImageFont.truetype(str(candidate), px)
            except OSError:
                continue
        # One file of a typeface holds every weight of it; the one asked
        # for is chosen inside the file where there is a choice.
        try:
            f.set_variation_by_name(_VARIATIONS.get(weight, "Regular"))
        except Exception:
            pass
        return f
    try:
        return ImageFont.load_default(px)
    except TypeError:                          # older Pillow
        return ImageFont.load_default()


def _fit(im, box_w: int, box_h: int, zoom: float, cx: float, cy: float):
    """The photo filling its place, cropped rather than squashed."""
    from PIL import Image
    iw, ih = im.size
    if iw <= 0 or ih <= 0 or box_w <= 0 or box_h <= 0:
        return im
    scale = max(box_w / iw, box_h / ih) * max(0.05, zoom)
    nw, nh = max(1, int(round(iw * scale))), max(1, int(round(ih * scale)))
    im = im.resize((nw, nh), Image.LANCZOS)
    left = int(round((nw - box_w) * min(1.0, max(0.0, cx))))
    top = int(round((nh - box_h) * min(1.0, max(0.0, cy))))
    left = max(0, min(left, nw - box_w))
    top = max(0, min(top, nh - box_h))
    return im.crop((left, top, left + box_w, top + box_h))


def _rounded(im, radius: int):
    from PIL import Image, ImageDraw
    if radius <= 0:
        return im, None
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, im.size[0] - 1, im.size[1] - 1),
                                           radius=radius, fill=255)
    return im, mask


def page_size(page: Page, long_side: int) -> tuple[int, int]:
    a = page.aspect
    if a >= 1.0:
        return long_side, max(1, int(round(long_side / a)))
    return max(1, int(round(long_side * a))), long_side


def _background(page: Page, W: int, H: int):
    """The page's own colour, or a wash between two of them."""
    from PIL import Image
    spec = (page.background or "#ffffff").strip()
    if "," not in spec:
        return Image.new("RGB", (W, H), spec)
    top, bottom = [c.strip() for c in spec.split(",", 1)]
    import numpy as np
    from PIL import ImageColor
    a = np.array(ImageColor.getrgb(top), dtype=np.float32)
    b = np.array(ImageColor.getrgb(bottom), dtype=np.float32)
    ramp = np.linspace(0.0, 1.0, H, dtype=np.float32)[:, None]
    rows = a[None, :] * (1 - ramp) + b[None, :] * ramp
    return Image.fromarray(np.repeat(rows[:, None, :], W, axis=1).astype(np.uint8))


def _sprinkles(canvas, page: Page, W: int, H: int) -> None:
    """The little shapes a theme scatters over the page.

    They are laid down from a seed made of the page itself, so a page
    drawn on screen and the same page written at print size have their
    confetti in exactly the same places.
    """
    kind = (page.sprinkle or "").strip()
    if not kind:
        return
    import random
    from PIL import Image, ImageDraw
    rng = random.Random(hash((page.shape, len(page.slots), kind)) & 0xffffffff)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    from PIL import ImageColor
    r, g, b = ImageColor.getrgb(page.sprinkle_color or "#ffffff")
    short = min(W, H)
    for _ in range(90):
        cx, cy = rng.uniform(0, W), rng.uniform(0, H)
        size = short * rng.uniform(0.006, 0.018)
        alpha = int(rng.uniform(70, 190))
        fill = (r, g, b, alpha)
        if kind == "hearts":
            # two circles and a triangle: a heart at any size
            half = size / 2
            draw.ellipse((cx - half, cy - half, cx, cy + half * 0.2), fill=fill)
            draw.ellipse((cx, cy - half, cx + half, cy + half * 0.2), fill=fill)
            draw.polygon([(cx - half, cy), (cx + half, cy), (cx, cy + size * 0.8)],
                         fill=fill)
        elif kind == "stars":
            pts = []
            for k in range(10):
                rad = size if k % 2 == 0 else size * 0.42
                ang = math.pi / 2 + k * math.pi / 5
                pts.append((cx + rad * math.cos(ang), cy - rad * math.sin(ang)))
            draw.polygon(pts, fill=fill)
        elif kind == "snow":
            draw.ellipse((cx - size / 2, cy - size / 2, cx + size / 2, cy + size / 2),
                         fill=(r, g, b, int(alpha * 0.8)))
        else:                                   # dots
            wide = size * rng.uniform(0.6, 1.8)
            draw.ellipse((cx - wide / 2, cy - size / 3, cx + wide / 2, cy + size / 3),
                         fill=fill)
    canvas.paste(layer, (0, 0), layer)


def _card(im, w: int, h: int, page: Page, slot: Slot, short: int, rng=None):
    """One photo dressed the way the page's theme dresses it: cropped to
    its place, framed, its corners rounded, and leaning a little when the
    theme leans them. Returns an RGBA image to be pasted."""
    from PIL import Image, ImageDraw
    frame = int(round(max(0.0, page.frame) * short))
    foot = int(round(max(0.0, page.frame_foot) * short))
    radius = int(round(max(0.0, page.corner) * short))
    # The frame grows outwards from the place the photo was given, so a
    # framed photo does not overlap the one beside it.
    inner_w = max(1, w - 2 * frame)
    inner_h = max(1, h - 2 * frame - foot)
    tile = _fit(im, inner_w, inner_h, slot.zoom, slot.cx, slot.cy)

    card = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shape = Image.new("L", (w, h), 0)
    ImageDraw.Draw(shape).rounded_rectangle((0, 0, w - 1, h - 1),
                                            radius=radius if not frame else
                                            max(radius, int(frame * 0.6)), fill=255)
    if frame or foot:
        from PIL import ImageColor
        paint = Image.new("RGBA", (w, h), ImageColor.getrgb(page.frame_color or "#ffffff") + (255,))
        card.paste(paint, (0, 0), shape)
    inner_mask = None
    if radius and not frame:
        inner_mask = Image.new("L", (inner_w, inner_h), 0)
        ImageDraw.Draw(inner_mask).rounded_rectangle(
            (0, 0, inner_w - 1, inner_h - 1), radius=radius, fill=255)
    card.paste(tile.convert("RGBA"), (frame, frame), inner_mask)
    if not frame and not foot:
        card.putalpha(shape)
    return card


def render(page: Page, long_side: int = 2000, load=None, on_progress=None,
           highlight: int | None = None, edge: bool = False):
    """Draw the page at this size, as a ``PIL.Image``.

    ``load(path, max_side)`` opens a photo; the default reads it the way
    the rest of Piklin does, orientation and all. Each photo is decoded
    only as large as its place needs, so a poster of forty photographs
    never holds forty full-size images at once.

    ``highlight`` and ``edge`` are for the workshop's screen preview only -
    the photo being worked on, and where a white page ends against a white
    window. Neither is ever written to a file.
    """
    import random
    from PIL import Image, ImageDraw, ImageFilter
    if load is None:
        from .imageio import load_pil as load
    W, H = page_size(page, long_side)
    canvas = _background(page, W, H)
    short = min(W, H)
    _sprinkles(canvas, page, W, H)
    # The lean of each photo is drawn from the page, so it stays the same
    # every time this page is drawn.
    rng = random.Random(hash((page.theme, len(page.slots))) & 0xffffffff)
    tilts = [rng.uniform(-page.tilt, page.tilt) for _ in page.slots]
    blur = max(1, int(round(page.shadow * short * 0.5)))

    total = max(1, len(page.slots))
    for i, slot in enumerate(page.slots):
        x, y = int(round(slot.x * W)), int(round(slot.y * H))
        w, h = int(round(slot.w * W)), int(round(slot.h * H))
        if w <= 0 or h <= 0:
            continue
        try:
            im = load(slot.path, max_side=int(max(w, h) * 1.35) or None)
        except Exception:
            # A photo that will not open leaves its place empty rather
            # than losing the whole page.
            im = None
        if im is not None:
            if im.mode != "RGB":
                im = im.convert("RGB")
            angle = tilts[i] if page.tilt else 0.0
            if angle:
                # A leaning photo needs more room than its place, corner
                # to corner; it is made smaller so the lean happens inside
                # the page instead of over its edge.
                rad = math.radians(abs(page.tilt))
                grow = math.cos(rad) + math.sin(rad) * max(w, h) / max(1, min(w, h))
                w = max(1, int(w / grow))
                h = max(1, int(h / grow))
            card = _card(im, w, h, page, slot, short)
            if angle:
                card = card.rotate(angle, resample=Image.BICUBIC, expand=True)
            cx, cy = x + w // 2, y + h // 2
            at = (cx - card.size[0] // 2, cy - card.size[1] // 2)
            if page.shadow > 0:
                shade = Image.new("RGBA", card.size, (0, 0, 0, 0))
                shade.putalpha(card.getchannel("A").point(lambda v: int(v * 0.42)))
                shade = shade.filter(ImageFilter.GaussianBlur(blur))
                drop = int(round(page.shadow * short * 0.35))
                canvas.paste(shade, (at[0], at[1] + drop), shade)
            canvas.paste(card, at, card)
        if on_progress is not None:
            on_progress((i + 1) / total)

    draw = ImageDraw.Draw(canvas)
    for t in page.texts:
        if not t.text:
            continue
        px = max(6, int(round(t.size * H)))
        f = font(t.weight, px, getattr(t, "family", "inter"))
        _draw_text(draw, t, f, W, H, page.background)

    if highlight is not None and 0 <= highlight < len(page.slots):
        slot = page.slots[highlight]
        pad = max(2, int(short * 0.004))
        box = (int(slot.x * W) - pad, int(slot.y * H) - pad,
               int((slot.x + slot.w) * W) + pad, int((slot.y + slot.h) * H) + pad)
        width = max(2, int(short * 0.005))
        draw.rectangle(box, outline="#ffffff", width=width + 2)
        draw.rectangle(box, outline="#1a73e8", width=width)
    if edge:
        draw.rectangle((0, 0, W - 1, H - 1), outline="#c9c9c9", width=1)
    return canvas


def _luma(color: str) -> float:
    """How light a colour reads, 0 (black) to 1 (white)."""
    from PIL import ImageColor
    try:
        r, g, b = ImageColor.getrgb(color)[:3]
    except (ValueError, AttributeError):
        return 0.0
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def readable_on(color: str, background: str) -> str:
    """``color``, unless it would disappear into ``background``.

    Words that cannot be read are worse than words in the wrong colour:
    white lettering on a white page is simply a page with no title, and
    nothing in the interface tells you that is what happened. Where the
    two are too close, the nearer of black and white to the wanted colour
    is used instead.
    """
    spec = (background or "#ffffff").split(",")
    back = max((_luma(c.strip()) for c in spec if c.strip()), default=1.0)
    front = _luma(color)
    if abs(front - back) >= 0.28:
        return color
    return "#ffffff" if back < 0.5 else "#111111"


def _draw_text(draw, t: Text, f, W: int, H: int, background: str = "#ffffff") -> None:
    lines = str(t.text).splitlines() or [""]
    colour = readable_on(t.color, background)
    tracking = t.tracking * f.size if t.tracking else 0.0
    y = t.y * H
    for line in lines:
        width = draw.textlength(line, font=f) + tracking * max(0, len(line) - 1)
        # However the width was measured, words belong on the page: a
        # measurement gone wrong must not put them outside it.
        if not (0 < width <= W * 4):
            width = min(float(W) * 0.9, max(1.0, len(line) * f.size * 0.6))
        if t.align == "left":
            x = (t.x - t.w / 2) * W
        elif t.align == "right":
            x = (t.x + t.w / 2) * W - width
        else:
            x = t.x * W - width / 2
        if tracking:
            for ch in line:
                draw.text((x, y), ch, font=f, fill=colour)
                x += draw.textlength(ch, font=f) + tracking
        else:
            draw.text((x, y), line, font=f, fill=colour)
        y += f.size * 1.25


def save(pages, dest, long_side: int | None = None, load=None,
         quality: int = 95, on_progress=None) -> Path:
    """Write one or more pages to a file, by what the name ends in.

    PDF keeps every page in one document at print resolution; PNG and
    JPEG write the first page. The file is written beside its destination
    and moved into place, so a creation is never half-written.
    """
    from PIL import Image  # noqa: F401  (kept for a clear error if missing)
    pages = list(pages) if isinstance(pages, (list, tuple)) else [pages]
    dest = Path(dest)
    ext = dest.suffix.lower()
    if long_side is None:
        long_side = 3508 if ext == ".pdf" else 2400

    done = [0]

    def step(_f=None):
        if on_progress is not None:
            on_progress(min(1.0, (done[0] + (_f or 0)) / max(1, len(pages))))

    images = []
    for p in pages:
        images.append(render(p, long_side, load, on_progress=lambda f: step(f)))
        done[0] += 1
        step()

    tmp = dest.with_name(dest.name + ".part")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    try:
        if ext == ".pdf":
            images[0].save(tmp, "PDF", resolution=float(PRINT_DPI),
                           save_all=True, append_images=images[1:])
        elif ext in (".jpg", ".jpeg"):
            images[0].save(tmp, "JPEG", quality=quality, subsampling=1,
                           optimize=True)
        else:
            images[0].save(tmp, "PNG", optimize=True)
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return dest
