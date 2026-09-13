"""How to Use Piklin: a plain guide to what each button does.

Written for someone opening a photo app for the first time. Each row shows
the same icon as the button it explains, so the guide can be matched to
the screen at a glance. Opened from Help in the main menu, or with F1.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ..i18n import _, N_

# (group title, [(icon, title, what it does), ...])
GUIDE = (
    (N_("Getting Started"), (
        ("folder-symbolic", N_("Add your photos"),
         N_("Open the menu ⋮ at the top of the sidebar and choose Add Folder… "
            "Your photos stay where they are: Piklin only shows them.")),
        ("drive-removable-media-symbolic", N_("Bring in photos from a camera or USB drive"),
         N_("Connect it and it appears under Devices in the sidebar. Choose the "
            "photos you want and press Import.")),
        ("folder-pictures-symbolic", N_("Drag and drop"),
         N_("Drag photos from your files onto Piklin to add them, or onto an album "
            "in the sidebar to put them straight into that album.")),
    )),
    (N_("Moving Around"), (
        ("x-office-calendar-symbolic", N_("Years, Months, Days, All Photos"),
         N_("The buttons at the top change how your photos are grouped.")),
        ("zoom-in-symbolic", N_("Photo size"),
         N_("Move the slider at the top left to make the photos bigger or smaller.")),
        ("view-grid-symbolic", N_("Whole photos or squares"),
         N_("The button next to the slider shows each photo whole instead of cut "
            "to a square.")),
        ("edit-find-symbolic", N_("Search"),
         N_("Type in the search box to find photos by name, date, month, camera "
            "or keywords. Ctrl+F")),
        ("view-list-symbolic", N_("Filter"),
         N_("Show only favourites, videos, edited photos, photos with a location "
            "and more. Show All brings everything back.")),
    )),
    (N_("Choosing Photos"), (
        ("object-select-symbolic", N_("Choose a photo"),
         N_("Click a photo once. A green check shows it is chosen. Click it again "
            "to let it go.")),
        ("object-select-symbolic", N_("Choose several"),
         N_("Hold Ctrl and click each photo, or hold Shift to choose everything "
            "between two photos. Ctrl+A chooses them all.")),
        ("go-next-symbolic", N_("Open a photo"),
         N_("Double-click a photo to see it big. Press Esc to go back.")),
        ("open-menu-symbolic", N_("More options"),
         N_("Right-click a photo to see everything you can do with it.")),
    )),
    (N_("The Bar for Chosen Photos"), (
        ("starred-symbolic", N_("Favourite"),
         N_("Marks the chosen photos as favourites, so they appear in Favourites. "
            "Key: F")),
        ("user-trash-symbolic", N_("Move to Recently Deleted"),
         N_("The photos wait there for 30 days, so you can still get them back. "
            "Key: Delete")),
        ("document-save-symbolic", N_("Export"),
         N_("Saves copies of the chosen photos in a folder you pick, to share or "
            "print them. Ctrl+E")),
        ("list-add-symbolic", N_("Add to Album"),
         N_("Puts the chosen photos into an album.")),
        ("object-rotate-right-symbolic", N_("Rotate"),
         N_("Turns the chosen photos right or left. Ctrl+R turns right, "
            "Ctrl+Shift+R turns left.")),
    )),
    (N_("Looking at One Photo"), (
        ("go-previous-symbolic", N_("Back"),
         N_("Returns to your photos. Key: Esc")),
        ("pan-end-symbolic", N_("Previous and Next"),
         N_("Moves to the photo before or after this one.")),
        ("object-rotate-left-symbolic", N_("Rotate"),
         N_("Turns the photo. You can turn it back at any time.")),
        ("starred-symbolic", N_("Favourite"),
         N_("Marks this photo as a favourite. Key: F")),
        ("view-fullscreen-symbolic", N_("Full Screen"),
         N_("Shows the photo on the whole screen. Key: F11")),
        ("dialog-information-symbolic", N_("Info"),
         N_("Shows the date, camera, size and place, and lets you add a title or "
            "keywords. Ctrl+I")),
        ("document-edit-symbolic", N_("Edit"),
         N_("Opens the editing tools. Key: Enter")),
    )),
    (N_("Editing"), (
        ("document-edit-symbolic", N_("Your original is always safe"),
         N_("Edits are kept separately. Remove All Edits brings the photo back "
            "exactly as it was.")),
        ("edit-undo-symbolic", N_("Undo and redo"),
         N_("Ctrl+Z undoes the last change and Ctrl+Shift+Z brings it back.")),
        ("view-reveal-symbolic", N_("Compare with the original"),
         N_("Hold the M key to see the photo without your edits.")),
    )),
    (N_("Albums and the Sidebar"), (
        ("list-add-symbolic", N_("New album or folder"),
         N_("Press + next to Albums. A folder keeps several albums together.")),
        ("folder-symbolic", N_("Open a folder"),
         N_("Click a folder to see its albums as cards, then click one to open it.")),
        ("view-conceal-symbolic", N_("Hidden"),
         N_("Photos you hide with Ctrl+L leave your library and wait here.")),
        ("edit-copy-symbolic", N_("Duplicates"),
         N_("Finds photos that are exact copies, so you can keep just one.")),
        ("user-trash-symbolic", N_("Recently Deleted"),
         N_("Get deleted photos back within 30 days.")),
    )),
    (N_("Keeping Your Photos Safe"), (
        ("emblem-synchronizing-symbolic", N_("Backups"),
         N_("Open the menu ⋮ and choose Backups… to keep a copy on a drive, a NAS "
            "or a cloud service. New photos are copied there automatically.")),
        ("preferences-system-symbolic", N_("Preferences"),
         N_("Change the language, the look, and how photos are brought in. Ctrl+,")),
    )),
)


class HelpDialog(Adw.Dialog):
    def __init__(self):
        super().__init__(title=_("How to Use Piklin"), content_width=620,
                         content_height=720)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()
        intro = Adw.PreferencesGroup(
            description=_("What each button does, in a few words. Open this guide "
                          "at any time from Help in the menu, or press F1."))
        page.add(intro)
        for group_title, rows in GUIDE:
            group = Adw.PreferencesGroup(title=_(group_title))
            for icon, title, text in rows:
                row = Adw.ActionRow(title=_(title), subtitle=_(text),
                                    use_markup=False)
                image = Gtk.Image(icon_name=icon, pixel_size=20,
                                  valign=Gtk.Align.CENTER)
                image.add_css_class("pika-help-icon")
                row.add_prefix(image)
                group.add(row)
            page.add(group)
        view.set_content(page)
        self.set_child(view)
