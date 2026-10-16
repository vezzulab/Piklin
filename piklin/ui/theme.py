"""Whether the window is dark, for what Piklin draws itself.

The stylesheet turns over for the dark look (darkcss.py); what is drawn with
cairo or as a bare colour does not follow a stylesheet, and asks here.
"""
from __future__ import annotations

import gi

gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402


def is_dark() -> bool:
    try:
        return bool(Adw.StyleManager.get_default().get_dark())
    except Exception:
        return False


def redraw_on_change(widget) -> None:
    """Draw ``widget`` again when the computer, or the Theme setting, switches
    between the light and the dark look."""
    try:
        Adw.StyleManager.get_default().connect("notify::dark", lambda *_: widget.queue_draw())
    except Exception:
        pass


# The ink of a drawing: near-black on the light look, near-white on the dark.
LIGHT_INK = (0.114, 0.114, 0.122)
DARK_INK = (0.96, 0.96, 0.97)


def ink() -> tuple:
    return DARK_INK if is_dark() else LIGHT_INK


def paper() -> tuple:
    """What lies behind the ink: the other end of it."""
    return LIGHT_INK if is_dark() else (1.0, 1.0, 1.0)
