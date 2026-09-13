#!/usr/bin/env python3
"""Draw the Piklin app icon at every size the .deb installs.

The camera is the same drawing as the animated mark in the sidebar
(piklin/ui/brand.py), caught as the flash fires, above the name in
Inter. Below 64 px the name cannot be read, so the small sizes show the
camera alone, larger. The icon is also what a library package
("Piklin Library.piklin") shows in the file manager.

    python3 packaging/make-icons.py        # writes data/icons/piklin-*.png
"""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cairo  # noqa: E402
import gi  # noqa: E402

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo  # noqa: E402

from piklin.app import _load_bundled_fonts  # noqa: E402
from piklin.ui.brand import _rounded, draw_camera  # noqa: E402

SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
OUT = ROOT / "data" / "icons"


def render(size: int) -> cairo.ImageSurface:
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surf)
    margin = size * 0.07
    tile = size - 2 * margin
    radius = tile * 0.23

    # a soft shadow under the tile, stacked translucent layers
    if size >= 48:
        for i, alpha in enumerate((0.035, 0.022, 0.012)):
            grow = size * 0.004 * (i + 1)
            _rounded(cr, margin - grow, margin + size * 0.006 + grow,
                     tile + 2 * grow, tile + 2 * grow, radius + grow)
            cr.set_source_rgba(0, 0, 0, alpha)
            cr.fill()

    # white tile, the faintest fall-off towards the bottom, hairline edge
    grad = cairo.LinearGradient(0, margin, 0, margin + tile)
    grad.add_color_stop_rgb(0, 1, 1, 1)
    grad.add_color_stop_rgb(1, 0.935, 0.935, 0.95)
    _rounded(cr, margin, margin, tile, tile, radius)
    cr.set_source(grad)
    cr.fill_preserve()
    cr.set_source_rgba(0, 0, 0, 0.12)
    cr.set_line_width(max(1.0, size / 256))
    cr.stroke()

    # everything inside stays on the tile
    _rounded(cr, margin, margin, tile, tile, radius)
    cr.clip()
    flash = dict(burst=0.9, lit=1.0, blink=0.0)
    if size >= 64:
        cam = tile * 0.62
        draw_camera(cr, (size - cam) / 2 + tile * 0.01, margin + tile * 0.06,
                    cam, **flash)
        layout = PangoCairo.create_layout(cr)
        font = Pango.FontDescription.from_string("Inter Bold")
        font.set_absolute_size(tile * 0.2 * Pango.SCALE)
        layout.set_font_description(font)
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_letter_spacing_new(int(-tile * 0.006 * Pango.SCALE)))
        layout.set_attributes(attrs)
        layout.set_text("Piklin", -1)
        _ink, logical = layout.get_pixel_extents()
        cr.move_to((size - logical.width) / 2, margin + tile * 0.66)
        cr.set_source_rgb(0.114, 0.114, 0.122)
        PangoCairo.show_layout(cr, layout)
    else:
        cam = tile * 0.9
        draw_camera(cr, (size - cam) / 2, (size - cam) / 2 + tile * 0.02,
                    cam, **flash)
    return surf


def main():
    if not _load_bundled_fonts():
        sys.exit("Inter could not be loaded from data/fonts")
    OUT.mkdir(parents=True, exist_ok=True)
    for size in SIZES:
        path = OUT / f"piklin-{size}.png"
        render(size).write_to_png(str(path))
        print("wrote", path.relative_to(ROOT))
    shutil.copyfile(OUT / "piklin-512.png", OUT / "piklin.png")
    print("wrote", (OUT / "piklin.png").relative_to(ROOT))


if __name__ == "__main__":
    main()
