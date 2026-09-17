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
    background: str = "#ffffff"
    margin: float = 0.035         # of the shorter side
    gap: float = 0.012
    corner: float = 0.0           # rounded photo corners, of the shorter side
    slots: list[Slot] = field(default_factory=list)
    texts: list[Text] = field(default_factory=list)

    @property
    def aspect(self) -> float:
        return shape_aspect(self.shape)

    def to_dict(self) -> dict:
        return {"shape": self.shape, "background": self.background,
                "margin": self.margin, "gap": self.gap, "corner": self.corner,
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
                   slots=[Slot.from_dict(s) for s in d.get("slots") or []],
                   texts=[Text.from_dict(t) for t in d.get("texts") or []])


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
    # Inside the margin the page has a shape of its own.
    inner_w = 1.0 - 2 * page.margin
    inner_h = 1.0 - 2 * page.margin * page.aspect
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
        slot.y = page.margin * page.aspect + y * inner_h
        slot.w = w * inner_w
        slot.h = h * inner_h


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
    if os.environ.get("APPDIR"):
        candidates.insert(0, Path(os.environ["APPDIR"]) / "usr/share/piklin/data/fonts")
    for c in candidates:
        if c.is_dir():
            return c
    return None


_WEIGHTS = {"regular": "Inter-Regular.ttf", "medium": "Inter-Medium.ttf",
            "semibold": "Inter-SemiBold.ttf", "bold": "Inter-Bold.ttf"}


def font(weight: str, px: int):
    from PIL import ImageFont
    d = _font_dir()
    name = _WEIGHTS.get(weight, _WEIGHTS["bold"])
    if d is not None:
        for candidate in (d / "Inter" / name, d / name):
            if candidate.is_file():
                try:
                    return ImageFont.truetype(str(candidate), px)
                except OSError:
                    break
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


def render(page: Page, long_side: int = 2000, load=None, on_progress=None):
    """Draw the page at this size, as a ``PIL.Image``.

    ``load(path, max_side)`` opens a photo; the default reads it the way
    the rest of Piklin does, orientation and all. Each photo is decoded
    only as large as its place needs, so a poster of forty photographs
    never holds forty full-size images at once.
    """
    from PIL import Image, ImageDraw
    if load is None:
        from .imageio import load_pil as load
    W, H = page_size(page, long_side)
    canvas = Image.new("RGB", (W, H), page.background or "#ffffff")
    short = min(W, H)
    radius = int(round(max(0.0, page.corner) * short))

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
            tile = _fit(im, w, h, slot.zoom, slot.cx, slot.cy)
            tile, mask = _rounded(tile, radius)
            canvas.paste(tile, (x, y), mask)
        if on_progress is not None:
            on_progress((i + 1) / total)

    draw = ImageDraw.Draw(canvas)
    for t in page.texts:
        if not t.text:
            continue
        px = max(6, int(round(t.size * H)))
        f = font(t.weight, px)
        _draw_text(draw, t, f, W, H)
    return canvas


def _draw_text(draw, t: Text, f, W: int, H: int) -> None:
    lines = str(t.text).splitlines() or [""]
    tracking = t.tracking * f.size if t.tracking else 0.0
    y = t.y * H
    for line in lines:
        width = draw.textlength(line, font=f) + tracking * max(0, len(line) - 1)
        if t.align == "left":
            x = (t.x - t.w / 2) * W
        elif t.align == "right":
            x = (t.x + t.w / 2) * W - width
        else:
            x = t.x * W - width / 2
        if tracking:
            for ch in line:
                draw.text((x, y), ch, font=f, fill=t.color)
                x += draw.textlength(ch, font=f) + tracking
        else:
            draw.text((x, y), line, font=f, fill=t.color)
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
