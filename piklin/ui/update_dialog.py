"""The update window: would you like to update? The version you have next
to the one on offer, what the new one brings in a few words, and Install
Now or Cancel. Installing, and how it went, are shown here too."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import updates
from ..i18n import _, current_language


def _build_label(build: str) -> str:
    return _("build {build}").format(build=build[:7]) if build else ""


class UpdateDialog(Adw.Dialog):
    def __init__(self, release, current: str, current_build: str, on_install):
        super().__init__(title=_("Update Piklin"), content_width=520,
                         content_height=600)
        self._release = release
        self._on_install = on_install
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self._stack.add_named(self._offer_page(release, current, current_build), "offer")
        self._stack.add_named(self._busy_page(release), "installing")
        self._message = Adw.StatusPage(icon_name="emblem-ok-symbolic", vexpand=True)
        self._message_buttons = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        self._message.set_child(self._message_buttons)
        self._stack.add_named(self._message, "message")
        view.set_content(self._stack)
        self.set_child(view)

    # -- pages ------------------------------------------------------------
    def _offer_page(self, release, current, current_build):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                       margin_top=4, margin_bottom=18, margin_start=24, margin_end=24)
        question = Gtk.Label(label=_("Would you like to update Piklin?"),
                             xalign=0.0, wrap=True)
        question.add_css_class("title-2")
        page.append(question)

        # the version you have, and the one you would get
        compare = Gtk.Box(spacing=12, homogeneous=True)
        same = release.version == current
        compare.append(self._version_card(_("You have"), current, current_build, False))
        compare.append(self._version_card(
            _("You would get"), release.version,
            release.build if release.build else "", True))
        page.append(compare)
        if same:
            again = Gtk.Label(label=_("The same version with fixes, published again."),
                              xalign=0.0, wrap=True)
            again.add_css_class("pika-dim")
            page.append(again)

        heading = Gtk.Label(label=_("What's new in {version}").format(version=release.version),
                            xalign=0.0)
        heading.add_css_class("heading")
        page.append(heading)
        changes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        lines = updates.highlights(release.notes, current_language())
        if not lines:
            changes.append(Gtk.Label(label=_("Improvements and fixes."), xalign=0.0))
        for kind, text in lines:
            if kind == "heading":
                head = Gtk.Label(label=text, xalign=0.0, margin_top=8)
                head.add_css_class("pika-update-group")
                changes.append(head)
            else:
                row = Gtk.Box(spacing=8)
                row.append(Gtk.Label(label="•", valign=Gtk.Align.START))
                row.append(Gtk.Label(label=text, xalign=0.0, wrap=True, hexpand=True))
                changes.append(row)
        page.append(Gtk.ScrolledWindow(vexpand=True, child=changes,
                                       hscrollbar_policy=Gtk.PolicyType.NEVER))

        later = Gtk.Label(label=_("You can update later with Update Available, in the sidebar."),
                          xalign=0.0, wrap=True)
        later.add_css_class("pika-dim")
        page.append(later)
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda *_: self.close())
        buttons.append(cancel)
        install = Gtk.Button(label=_("Install Now"))
        install.add_css_class("suggested-action")
        if updates.can_install_itself():
            install.connect("clicked", lambda *_: self._on_install())
        else:
            install.set_label(_("Download"))
            install.connect("clicked", lambda *_: (Gtk.UriLauncher.new(
                self._release.url).launch(self.get_root(), None, None, None), self.close()))
        buttons.append(install)
        page.append(buttons)
        return page

    def _version_card(self, caption, version, build, new):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        card.add_css_class("pika-version-card")
        if new:
            card.add_css_class("new")
        cap = Gtk.Label(label=caption, xalign=0.0)
        cap.add_css_class("pika-dim")
        card.append(cap)
        ver = Gtk.Label(label=f"Piklin {version}", xalign=0.0)
        ver.add_css_class("title-3")
        card.append(ver)
        if build:
            b = Gtk.Label(label=_build_label(build), xalign=0.0)
            b.add_css_class("pika-dim")
            card.append(b)
        return card

    def _busy_page(self, release):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                      valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER, vexpand=True)
        spinner = Gtk.Spinner(spinning=True, width_request=32, height_request=32)
        box.append(spinner)
        title = Gtk.Label(label=_("Installing Piklin {version}…").format(
            version=release.version))
        title.add_css_class("title-3")
        box.append(title)
        note = Gtk.Label(label=_("Please keep Piklin open. It reopens by itself "
                                 "when it is done."), wrap=True, justify=Gtk.Justification.CENTER)
        note.add_css_class("pika-dim")
        box.append(note)
        return box

    # -- states -----------------------------------------------------------
    def show_offer(self):
        self.set_can_close(True)
        self._stack.set_visible_child_name("offer")

    def show_installing(self):
        self.set_can_close(False)
        self._stack.set_visible_child_name("installing")

    def _show_message(self, icon, title, text, buttons):
        self.set_can_close(True)
        self._message.set_icon_name(icon)
        self._message.set_title(title)
        self._message.set_description(text)
        while (child := self._message_buttons.get_first_child()) is not None:
            self._message_buttons.remove(child)
        for label, callback, suggested in buttons:
            b = Gtk.Button(label=label)
            if suggested:
                b.add_css_class("suggested-action")
            b.connect("clicked", lambda *_a, cb=callback: cb())
            self._message_buttons.append(b)
        self._stack.set_visible_child_name("message")

    def show_installed(self):
        self._show_message(
            "emblem-ok-symbolic",
            _("Piklin {version} is installed").format(version=self._release.version),
            _("Piklin will reopen in a moment."), [])
        GLib.timeout_add(2500, lambda: (self.force_close(), False)[1])

    def show_failed(self):
        self._show_message(
            "dialog-warning-symbolic",
            _("The update couldn't be installed"),
            _("Try again, or download it from the website."),
            [(_("Close"), self.close, False),
             (_("Download"), lambda: Gtk.UriLauncher.new(self._release.url).launch(
                 self.get_root(), None, None, None), False),
             (_("Try Again"), self._on_install, True)])
