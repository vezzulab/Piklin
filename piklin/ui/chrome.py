"""Where the window buttons go, which depends on the system.

On Linux they sit on the right of the content bar: minimise, maximise,
close. On a Mac people expect them at the top left - close, minimise,
zoom - so they move to the start of the sidebar's bar, and to the start of
the viewer's and editors' bars, which replace it while those are open.
"""
from __future__ import annotations

import sys

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

IS_MAC = sys.platform == "darwin"

# The modifier of shortcuts and of adding to a selection: Ctrl on Linux,
# Command on a Mac - what "<Primary>" means in an accelerator.
PRIMARY_MASK = Gdk.ModifierType.META_MASK if IS_MAC else Gdk.ModifierType.CONTROL_MASK


def key_name(keyval) -> str:
    """The key's name, with a Mac's delete key (BackSpace) read as Delete:
    on a Mac that key moves photos to Recently Deleted, as Delete does."""
    name = Gdk.keyval_name(keyval) or ""
    return "Delete" if IS_MAC and name == "BackSpace" else name

# The sidebar's bar and the content bar of the library page.
# On a Mac the bar draws no buttons of its own: add_header_controls puts the
# system's own there instead.
SIDEBAR_LAYOUT = ":"
CONTENT_LAYOUT = ":" if IS_MAC else ":minimize,maximize,close"
# The buttons take room at the start of the sidebar's bar on a Mac, so the
# sidebar is wider there to keep the brand centred beside them.
SIDEBAR_MIN_WIDTH = 230 if IS_MAC else 210
# On a Mac the brand leaves the bar to the buttons and sits at the top of
# the sidebar instead, a little smaller.
BRAND_IN_SIDEBAR = IS_MAC
BRAND_SIZE = 28 if IS_MAC else 34


def screen_size() -> tuple[int, int]:
    """The usable size of the screen the app is on, in points.

    A Mac's menu bar and Dock take part of the screen that GTK still counts
    as the monitor, so on a Mac a little more is kept free at the top and
    bottom. (0, 0) when no monitor is known yet.
    """
    from gi.repository import Gdk
    display = Gdk.Display.get_default()
    if display is None:
        return 0, 0
    monitors = display.get_monitors()
    if monitors.get_n_items() == 0:
        return 0, 0
    area = monitors.get_item(0).get_geometry()
    if IS_MAC:
        return area.width, max(0, area.height - 110)
    return area.width, area.height


def fit(width: int, height: int, share: float = 0.9) -> tuple[int, int]:
    """``width`` x ``height``, shrunk to at most ``share`` of the screen.

    A size of -1 (or 0) is left as it is: it means "as big as needed".
    """
    sw, sh = screen_size()
    if sw and width > 0:
        width = min(width, int(sw * share))
    if sh and height > 0:
        height = min(height, int(sh * share))
    return width, height


class MacWindowButtons(Gtk.Box):
    """Close, minimise and zoom as a Mac shows them, top left.

    Three ordinary buttons that call the window directly. Gtk.WindowControls
    did not answer clicks on a Mac - drawn by GTK or asked for as native
    controls, which only work in GTK's own header bar, not libadwaita's.
    Each dot is 12 px, as on a Mac, inside a 20 px target that is easy to hit.
    """

    def __init__(self):
        super().__init__(spacing=0, valign=Gtk.Align.CENTER)
        self.add_css_class("pika-mac-buttons")
        # The system's own buttons are still drawn at the corner and answer
        # nothing: hidden once the window is on screen, so these are the ones.
        self.connect("map", lambda *_: GLib.idle_add(hide_native_window_buttons))
        for name, icon, action in (
                ("close", "window-close-symbolic", self._close),
                ("minimize", "window-minimize-symbolic", self._minimize),
                ("maximize", "window-maximize-symbolic", self._zoom)):
            button = Gtk.Button(child=Gtk.Image(icon_name=icon), focusable=False)
            button.add_css_class(name)
            button.connect("clicked", action)
            self.append(button)

    def _window(self):
        root = self.get_root()
        return root if isinstance(root, Gtk.Window) else None

    def _close(self, _button):
        if (win := self._window()) is not None:
            win.close()

    def _minimize(self, _button):
        if (win := self._window()) is not None:
            win.minimize()

    def _zoom(self, _button):
        if (win := self._window()) is not None:
            win.unmaximize() if win.is_maximized() else win.maximize()


def hide_native_window_buttons() -> bool:
    """Hide the Mac's own close, minimise and zoom buttons on every window of
    the app. Returns False, so it can be used as a one-shot idle callback."""
    if not IS_MAC:
        return False
    try:
        from AppKit import (NSApplication, NSWindowCloseButton,
                            NSWindowMiniaturizeButton, NSWindowZoomButton)
    except ImportError:
        return False
    for window in NSApplication.sharedApplication().windows() or []:
        for kind in (NSWindowCloseButton, NSWindowMiniaturizeButton, NSWindowZoomButton):
            button = window.standardWindowButton_(kind)
            if button is not None:
                button.setHidden_(True)
    return False


def _mac_controls() -> Gtk.Widget:
    return MacWindowButtons()


def add_header_controls(header) -> None:
    """On a Mac, the window buttons at the start of the sidebar's bar."""
    if IS_MAC:
        # The bar's own button slots stay reserved for the system's buttons on
        # a Mac, empty, and push these away from the corner: they go.
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        header.pack_start(_mac_controls())


def add_window_controls(bar: Gtk.Box) -> Gtk.WindowControls:
    """Put the window buttons on a custom top bar, at the end it belongs."""
    if IS_MAC:
        controls = _mac_controls()
        bar.prepend(controls)
    else:
        controls = Gtk.WindowControls(side=Gtk.PackType.END)
        controls.set_decoration_layout(":minimize,maximize,close")
        bar.append(controls)
    return controls
