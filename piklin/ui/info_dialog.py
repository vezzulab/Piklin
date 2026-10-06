"""Edit Info: the title, caption, keywords and date of one photo or of many.

The Info panel of the viewer does this for the photo on screen. Here it is for
the photos chosen in the grid, one or a whole group: what is left empty is left
as it is in each photo, keywords can be added to the ones a photo already has,
and a date can be set for them all or moved with their spacing kept.

Nothing is written to the photo files. What is typed lives in Piklin's catalog
and in photo-state.json, which goes with the backup.
"""
from __future__ import annotations

from datetime import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GObject, Gtk  # noqa: E402

from ..i18n import _, ngettext

DATE_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d")


def split_keywords(text: str | None) -> list[str]:
    return [w.strip() for w in (text or "").replace(";", ",").split(",") if w.strip()]


def merge_keywords(existing: str | None, added: str | None) -> str:
    """The photo's keywords with ``added`` after them, none twice (without
    regard to capitals), in the order they were first given."""
    out, seen = [], set()
    for word in split_keywords(existing) + split_keywords(added):
        if word.lower() not in seen:
            seen.add(word.lower())
            out.append(word)
    return ", ".join(out)


def parse_when(text: str) -> float | None:
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text.strip(), fmt).timestamp()
        except ValueError:
            continue
    return None


def apply_edits(catalog, ids, *, title=None, caption=None, keywords=None,
                keywords_mode="add", clear=(), when=None, date_mode="keep") -> None:
    """Make the changes. ``title``, ``caption`` and ``keywords`` are changed only
    when given (not None); the names in ``clear`` are emptied in every photo.
    ``keywords_mode`` is "add" or "replace". ``date_mode`` is "keep", "set" (every
    photo gets ``when``) or "move" (the first photo gets ``when``, the others keep
    their distance from it)."""
    ids = list(ids)
    if not ids:
        return
    fields = {}
    for name, value in (("title", title), ("caption", caption)):
        if name in clear:
            fields[name] = ""
        elif value is not None:
            fields[name] = value
    if fields:
        catalog.set_text_fields(ids, **fields)
    if "keywords" in clear:
        catalog.set_text_fields(ids, keywords="")
    elif keywords is not None:
        if keywords_mode == "replace":
            catalog.set_text_fields(ids, keywords=", ".join(split_keywords(keywords)))
        else:
            marks = ",".join("?" * len(ids))
            for row in catalog.q(f"SELECT id, keywords FROM photos WHERE id IN ({marks})", ids):
                catalog.set_text_fields([row["id"]],
                                        keywords=merge_keywords(row["keywords"], keywords))
    if when is not None and date_mode in ("set", "move"):
        catalog.set_taken_at(ids, when, shift=(date_mode == "move"))


class InfoDialog(Adw.Dialog):
    """Opens on the chosen photos; emits ``saved`` once the changes are made and
    ``place`` when somebody asks to choose their place on the map."""

    __gsignals__ = {
        "saved": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "place": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, catalog, photo_ids, can_place: bool = False):
        super().__init__(title=_("Edit Info"), content_width=540, content_height=640)
        self.catalog = catalog
        self.ids = list(photo_ids)
        self.many = len(self.ids) > 1
        marks = ",".join("?" * len(self.ids))
        rows = catalog.q(f"SELECT title, caption, keywords, taken_at FROM photos "
                         f"WHERE id IN ({marks})", self.ids)
        self._first = rows[0] if rows else None
        self._same = {k: len({(r[k] or "") for r in rows}) == 1
                      for k in ("title", "caption", "keywords")}
        self._clear: set[str] = set()
        self._rows: dict[str, Adw.EntryRow] = {}

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            title=(ngettext("{count} photo", "{count} photos", len(self.ids))
                   .format(count=f"{len(self.ids):,}")),
            description=(_("Leave a field empty to keep what each photo has.")
                         if self.many else
                         _("The photo file isn't changed. What you type is saved in "
                           "Piklin and in your backup.")))
        for key, label in (("title", _("Title")), ("caption", _("Caption")),
                           ("keywords", _("Keywords, separated by commas"))):
            row = Adw.EntryRow(title=label)
            if self._first is not None and (not self.many or self._same[key]):
                row.set_text(self._first[key] or "")
            row.connect("changed", lambda *_a: self._update())
            if self.many:
                undo = Gtk.ToggleButton(icon_name="edit-clear-symbolic",
                                        valign=Gtk.Align.CENTER, css_classes=["flat"],
                                        tooltip_text=_("Remove from every photo"))
                undo.connect("toggled", self._on_clear, key, row)
                row.add_suffix(undo)
            group.add(row)
            self._rows[key] = row
        page.add(group)

        if self.many:
            self.mode = Adw.ComboRow(
                title=_("Keywords"),
                model=Gtk.StringList.new([_("Add to each photo's keywords"),
                                          _("Replace each photo's keywords")]))
            self.mode.connect("notify::selected", lambda *_a: self._update())
            group.add(self.mode)

        when_group = Adw.PreferencesGroup(title=_("Date and time"))
        if self.many:
            self.date_mode = Adw.ComboRow(
                title=_("Date and time"),
                model=Gtk.StringList.new([_("Keep each photo's own"),
                                          _("Set every photo to this"),
                                          _("Move the group, keeping the spacing")]))
            self.date_mode.connect("notify::selected", lambda *_a: self._update())
            when_group.add(self.date_mode)
        self.when = Adw.EntryRow(title=_("YYYY-MM-DD HH:MM"))
        if not self.many and self._first is not None and self._first["taken_at"]:
            self._original_when = datetime.fromtimestamp(
                self._first["taken_at"]).strftime(DATE_FORMATS[0])
            self.when.set_text(self._original_when)
        else:
            self._original_when = ""
        self.when.connect("changed", lambda *_a: self._update())
        when_group.add(self.when)
        page.add(when_group)

        if can_place:
            place = Adw.PreferencesGroup(title=_("Location"))
            row = Adw.ActionRow(title=_("Where these photos were taken"))
            button = Gtk.Button(label=_("Choose on Map…"), valign=Gtk.Align.CENTER)
            button.connect("clicked", self._on_place)
            row.add_suffix(button)
            place.add(row)
            page.add(place)

        self.save = Gtk.Button(label=_("Save"), sensitive=False,
                               css_classes=["suggested-action"])
        self.save.connect("clicked", self._on_save)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda *_a: self.close())
        bar = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        bar.pack_start(cancel)
        bar.pack_end(self.save)
        view = Adw.ToolbarView()
        view.add_top_bar(bar)
        view.set_content(page)
        self.set_child(view)
        self.set_default_widget(self.save)
        self._update()

    # ------------------------------------------------------------------
    def _on_clear(self, button, key, row):
        if button.get_active():
            self._clear.add(key)
            row.set_text("")
        else:
            self._clear.discard(key)
        row.set_sensitive(not button.get_active())
        self._update()

    def _date_mode(self) -> str:
        if not self.many:
            return "set" if self.when.get_text().strip() != self._original_when else "keep"
        return ("keep", "set", "move")[self.date_mode.get_selected()]

    def _date_valid(self) -> bool:
        mode = self._date_mode()
        if mode == "keep":
            return True
        return parse_when(self.when.get_text()) is not None

    def _changes(self) -> dict:
        """What would be written, as keyword arguments of apply_edits."""
        out: dict = {"clear": frozenset(self._clear)}
        for key in ("title", "caption", "keywords"):
            text = self._rows[key].get_text()
            if key in self._clear:
                continue
            if self.many:
                if text.strip():
                    out[key] = text
            elif self._first is not None and text.strip() != (self._first[key] or "").strip():
                out[key] = text
        if self.many:
            out["keywords_mode"] = "replace" if self.mode.get_selected() == 1 else "add"
        mode = self._date_mode()
        if mode != "keep" and self._date_valid():
            out["when"], out["date_mode"] = parse_when(self.when.get_text()), mode
        return out

    def _update(self) -> None:
        bad = not self._date_valid()
        if bad:
            self.when.add_css_class("error")
        else:
            self.when.remove_css_class("error")
        changes = self._changes()
        any_change = bool(changes["clear"]) or any(k in changes for k in ("title", "caption", "keywords", "when"))
        self.save.set_sensitive(any_change and not bad)

    def _on_save(self, _button) -> None:
        apply_edits(self.catalog, self.ids, **self._changes())
        self.emit("saved")
        self.close()

    def _on_place(self, _button) -> None:
        self.emit("place")
        self.close()
