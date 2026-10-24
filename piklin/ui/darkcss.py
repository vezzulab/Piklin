"""The dark appearance, made from the light one.

Piklin's look is one palette of near-neutral colours: white walls, black text,
grey hairlines. The dark look is that palette turned over - what was white is
a deep grey, what was black is nearly white - so every rule in style.css, and
every rule added to it later, has a dark counterpart without being written
twice. Colours that are not neutral (green, red) stay as they are, and a few
surfaces that must be dark in either look (the photo stage, the video
controls) keep their colours.

``to_dark(css)`` returns the dark stylesheet for a light one.
"""
from __future__ import annotations

import colorsys
import re

# What the light palette's greys become. White is not black but a deep grey,
# and black is not white but a soft white, as the dark look of a photo
# library is: the photographs are the brightest thing on the screen.
DEEP = 0.105           # lightness of what was white
SOFT = 0.965           # lightness of what was black

# Named colours given their own dark value rather than the turned-over one.
NAMED = {
    "pika_wall":           "#1c1c1e",
    "pika_sidebar":        "#1c1c1e",
    "pika_hairline":       "#3a3a3e",
    "pika_hairline_soft":  "#2c2c30",
    "pika_text":           "#f5f5f7",
    "pika_text_dim":       "#9a9aa1",
    "pika_accent":         "#f2f2f5",
    "window_bg_color":     "#1c1c1e",
    "window_fg_color":     "#f5f5f7",
    "view_bg_color":       "#1c1c1e",
    "view_fg_color":       "#f5f5f7",
    "headerbar_bg_color":  "#1c1c1e",
    "headerbar_fg_color":  "#f5f5f7",
    "headerbar_border_color": "#3a3a3e",
    "headerbar_backdrop_color": "#1e1e20",
    "sidebar_bg_color":    "#1c1c1e",
    "sidebar_fg_color":    "#f5f5f7",
    "sidebar_backdrop_color": "#1c1c1e",
    "sidebar_border_color": "#3a3a3e",
    "secondary_sidebar_bg_color": "#1c1c1e",
    "card_bg_color":       "#26262a",
    "card_fg_color":       "#f5f5f7",
    "dialog_bg_color":     "#232326",
    "dialog_fg_color":     "#f5f5f7",
    "popover_bg_color":    "#2a2a2e",
    "popover_fg_color":    "#f5f5f7",
    "accent_bg_color":     "#f2f2f5",
    "accent_fg_color":     "#1c1c1e",
    "accent_color":        "#f2f2f5",
    "borders":             "#3a3a3e",
    "destructive_bg_color": "#3a3a3e",
    "destructive_fg_color": "#f5f5f7",
    "destructive_color":   "#f5f5f7",
    "error_color":         "#ff8a80",
    "error_bg_color":      "#3a3a3e",
    "error_fg_color":      "#f5f5f7",
    "warning_color":       "#f5f5f7",
    "warning_bg_color":    "#3a3a3e",
    "focus_border_color":  "#f2f2f5",
    "selected_bg_color":   "#f2f2f5",
    "selected_fg_color":   "#1c1c1e",
}

# Rules whose selector names these keep their light-look colours: they sit on
# photographs or video, which are dark or bright whatever the window is.
KEEP = re.compile(
    r"pika-player|pika-viewer-stage|pika-stage|pika-video-badge|pika-live-badge|"
    r"pika-badge|pika-select-check|pika-drag-count|pika-timeline|pika-scrubber|"
    r"pika-map-(?:count|badge)|pika-tile-overlay|pika-copy-stop|pika-fullscreen|dimming|"
    r"pika-welcome-btn")        # the greeting lies on a cream picture in either look: its button stays black

_HEX = re.compile(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
_ALPHA = re.compile(r"alpha\(\s*(#[0-9a-fA-F]{3,8})\s*,")


def _parse(color: str):
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    r, g, b = (int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return r, g, b


def _hex(r, g, b) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(v * 255))) for v in (r, g, b))


# The greys the stylesheet uses most are set by hand, so a rule that names one
# and a rule that names the palette's colour for it meet without a seam.
LITERAL = {
    "#ffffff": "#1c1c1e", "#fff": "#1c1c1e",
    "#000000": "#f5f5f7", "#000": "#f5f5f7",
    "#f2f2f4": "#2a2a2e", "#f4f4f6": "#2a2a2e", "#f0f0f2": "#2a2a2e",
    "#f7f7f8": "#242428", "#fbfbfc": "#202023", "#fafafa": "#202023",
    "#dcdce0": "#3a3a3e", "#ebebee": "#2c2c30", "#e0e0e5": "#3a3a3e",
    "#86868b": "#9a9aa1", "#8e8e93": "#9a9aa1",
}


def turn(color: str) -> str:
    """The dark counterpart of a colour: a neutral grey is turned over, with a
    hint of its own tint kept; anything with a colour of its own stays."""
    if color.lower() in LITERAL:
        return LITERAL[color.lower()]
    try:
        r, g, b = _parse(color)
    except ValueError:
        return color
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    if max(r, g, b) - min(r, g, b) > 0.09:          # not a grey: green, red, blue...
        return color
    return _hex(*colorsys.hls_to_rgb(h, SOFT - (SOFT - DEEP) * l, min(s, 0.06)))


def _convert_value(value: str, prop: str) -> str:
    # a shadow is darkness under something, in either look
    if prop in ("box-shadow", "text-shadow", "-gtk-icon-shadow"):
        return _shadow(value)
    # one pass over every colour, those inside alpha() too
    return _HEX.sub(lambda m: turn(m.group(0)) if len(m.group(0)) in (4, 7) else m.group(0), value)


def _shadow(value: str) -> str:
    """Black shadows stay black, and a little stronger on a dark ground; a
    shadow that is really a hairline (no blur, drawn in a grey) turns over."""
    def one(part: str) -> str:
        nums = re.findall(r"(?<![\w.#-])-?\d+(?:\.\d+)?(?:px)?", part.split("alpha(")[0])
        ring = len(nums) >= 4 and nums[2].rstrip("px") == "0" and nums[3].rstrip("px") not in ("0",)
        if ring or "inset" in part:
            return _ALPHA.sub(lambda m: f"alpha({turn(m.group(1))},", part)
        return re.sub(r"alpha\(\s*#000000\s*,\s*([\d.]+)\s*\)",
                      lambda m: f"alpha(#000000, {min(1.0, float(m.group(1)) * 1.8):.2f})", part)
    return ",".join(one(p) for p in re.split(r",(?![^()]*\))", value))


def to_dark(css: str) -> str:
    """The dark stylesheet for the light ``css``."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    # named colours first: ``@define-color name value;``
    def define(m):
        name, value = m.group(1), m.group(2)
        if name in NAMED:
            return f"@define-color {name} {NAMED[name]};"
        return f"@define-color {name} {_convert_value(value, '')};"
    css = re.sub(r"@define-color\s+([A-Za-z0-9_]+)\s+([^;]+);", define, css)

    out = []
    pos = 0
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        out.append(css[pos:m.start()])
        pos = m.end()
        selector, body = m.group(1), m.group(2)
        if KEEP.search(selector):
            out.append(f"{selector}{{{body}}}")
            continue
        decls = []
        for decl in body.split(";"):
            if ":" not in decl:
                decls.append(decl)
                continue
            prop, value = decl.split(":", 1)
            decls.append(f"{prop}:{_convert_value(value, prop.strip().lower())}")
        out.append(f"{selector}{{{';'.join(decls)}}}")
    out.append(css[pos:])
    return "".join(out)
