"""New / Edit Smart Album.

A name, "Match [any|all] of the following conditions", and one row per
condition - field, relation, value - with buttons to add and remove rows.
The count of matching photos updates as the conditions change, so what the
album will hold is visible before it is saved.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gtk  # noqa: E402

from ..catalog import SMART_FIELDS, SMART_OPS, compile_rules

# Offered in this order; the rest follow alphabetically by label.
_FIELD_ORDER = ["media_type", "favorite", "edited", "taken_at", "duration",
                "camera_model", "camera_make",
                "lens", "rating", "has_location", "filename", "ext", "folder",
                "iso", "f_number", "focal_length", "width", "height", "bytes"]


def _fields():
    keys = [k for k in _FIELD_ORDER if k in SMART_FIELDS]
    keys += sorted((k for k in SMART_FIELDS if k not in keys),
                   key=lambda k: SMART_FIELDS[k][1])
    return keys


class _ConditionRow(Gtk.Box):
    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "remove": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "add": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, rule: dict | None = None):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.add_css_class("pika-smart-row")
        self._keys = _fields()
        self.field = Gtk.DropDown.new_from_strings(
            [SMART_FIELDS[k][1] for k in self._keys])
        self.op = Gtk.DropDown.new_from_strings([""])
        self.value = Gtk.Entry(hexpand=True, placeholder_text="value")
        self.remove_btn = Gtk.Button(icon_name="list-remove-symbolic",
                                     tooltip_text="Remove this condition")
        self.add_btn = Gtk.Button(icon_name="list-add-symbolic",
                                  tooltip_text="Add a condition")
        for b in (self.remove_btn, self.add_btn):
            b.add_css_class("flat")
            b.add_css_class("circular")
        for w in (self.field, self.op, self.value, self.remove_btn, self.add_btn):
            self.append(w)

        rule = rule or {}
        key = rule.get("field", self._keys[0])
        self.field.set_selected(self._keys.index(key) if key in self._keys else 0)
        self._sync_ops(rule.get("op"))
        if rule.get("value") is not None:
            self.value.set_text(str(rule["value"]))

        self.field.connect("notify::selected", self._on_field)
        self.op.connect("notify::selected", lambda *_: self.emit("changed"))
        self.value.connect("changed", lambda *_: self.emit("changed"))
        self.remove_btn.connect("clicked", lambda *_: self.emit("remove"))
        self.add_btn.connect("clicked", lambda *_: self.emit("add"))

    def _kind(self):
        return SMART_FIELDS[self._keys[self.field.get_selected()]][2]

    def _sync_ops(self, wanted=None):
        ops = SMART_OPS[self._kind()]
        self.op.set_model(Gtk.StringList.new(ops))
        self.op.set_selected(ops.index(wanted) if wanted in ops else 0)
        kind = self._kind()
        # True/false fields need no value; dates "in the last" take days.
        self.value.set_visible(kind not in ("bool", "bool_null", "media"))
        self.value.set_placeholder_text(
            {"date": "days, or YYYY-MM-DD", "number": "number"}.get(kind, "text"))

    def _on_field(self, *_):
        self._sync_ops()
        self.emit("changed")

    def rule(self) -> dict:
        key = self._keys[self.field.get_selected()]
        ops = SMART_OPS[SMART_FIELDS[key][2]]
        rule = {"field": key, "op": ops[self.op.get_selected()]}
        if self.value.get_visible():
            rule["value"] = self.value.get_text().strip()
        return rule


class SmartAlbumDialog(Adw.Dialog):
    """Create a Smart Album, or edit one when ``smart_id`` is given."""

    __gsignals__ = {
        "saved": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }

    def __init__(self, catalog, smart_id: int | None = None,
                 folder_id: int | None = None):
        super().__init__(title="Smart Album", content_width=620)
        self.catalog = catalog
        self.smart_id = smart_id
        self.folder_id = folder_id

        existing = None
        if smart_id is not None:
            existing = catalog.q1("SELECT * FROM smart_albums WHERE id=?",
                                  (smart_id,))
        rules = []
        if existing is not None:
            import json
            try:
                rules = json.loads(existing["rules"]) or []
            except ValueError:
                rules = []

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar(show_start_title_buttons=False,
                               show_end_title_buttons=False)
        toolbar.add_top_bar(header)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                       margin_top=18, margin_bottom=18,
                       margin_start=24, margin_end=24)

        name_row = Gtk.Box(spacing=10)
        name_label = Gtk.Label(label="Smart Album Name:", xalign=0)
        self.name = Gtk.Entry(hexpand=True, activates_default=True,
                              text=existing["name"] if existing else "Smart Album")
        name_row.append(name_label)
        name_row.append(self.name)
        body.append(name_row)

        match_row = Gtk.Box(spacing=8)
        match_row.append(Gtk.Label(label="Match"))
        self.match = Gtk.DropDown.new_from_strings(["all", "any"])
        if existing is not None and existing["match_mode"] == "any":
            self.match.set_selected(1)
        self.match.connect("notify::selected", lambda *_: self._update_count())
        match_row.append(self.match)
        match_row.append(Gtk.Label(label="of the following conditions:"))
        body.append(match_row)

        # The count label exists before any row: adding a row refreshes it.
        self.count = Gtk.Label(xalign=0)
        self.count.add_css_class("pika-dim")

        self.rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.append(self.rows_box)
        for rule in rules or [{"field": "favorite", "op": "is true"}]:
            self._add_row(rule)
        body.append(self.count)

        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=6)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        self.ok = Gtk.Button(label="OK")
        self.ok.add_css_class("suggested-action")
        self.ok.connect("clicked", self._on_ok)
        buttons.append(cancel)
        buttons.append(self.ok)
        body.append(buttons)
        self.set_default_widget(self.ok)

        toolbar.set_content(body)
        self.set_child(toolbar)
        self._update_count()

        def focus_name():
            self.name.grab_focus()
            return GLib.SOURCE_REMOVE
        GLib.idle_add(focus_name, priority=GLib.PRIORITY_HIGH)

    # -- rows ----------------------------------------------------------------
    def _add_row(self, rule=None, after=None):
        row = _ConditionRow(rule)
        row.connect("changed", lambda *_: self._update_count())
        row.connect("remove", self._on_remove)
        row.connect("add", lambda r: self._add_row(after=r))
        if after is None:
            self.rows_box.append(row)
        else:
            self.rows_box.insert_child_after(row, after)
        self._sync_remove_buttons()
        self._update_count()

    def _rows(self):
        out, child = [], self.rows_box.get_first_child()
        while child is not None:
            out.append(child)
            child = child.get_next_sibling()
        return out

    def _on_remove(self, row):
        if len(self._rows()) > 1:
            self.rows_box.remove(row)
        self._sync_remove_buttons()
        self._update_count()

    def _sync_remove_buttons(self):
        rows = self._rows()
        for r in rows:
            r.remove_btn.set_sensitive(len(rows) > 1)

    # -- result ----------------------------------------------------------------
    def rules(self):
        return [r.rule() for r in self._rows()]

    def match_mode(self):
        return "any" if self.match.get_selected() == 1 else "all"

    def _update_count(self):
        frag, params = compile_rules(self.rules(), self.match_mode())
        where = "p.trashed_at IS NULL AND p.hidden=0" + (f" AND {frag}" if frag else "")
        try:
            n = int(self.catalog.scalar(
                f"SELECT COUNT(*) FROM photos p WHERE {where}", params, 0))
            self.count.set_text(f"{n:,} photo" + ("s" if n != 1 else "") + " match")
        except Exception:
            self.count.set_text("Check the values of the conditions")

    def _on_ok(self, *_):
        name = self.name.get_text().strip() or "Smart Album"
        if self.smart_id is None:
            sid = self.catalog.create_smart_album(
                name, self.rules(), self.match_mode(), folder_id=self.folder_id)
        else:
            sid = self.smart_id
            self.catalog.update_smart_album(sid, name, self.rules(),
                                            self.match_mode())
        self.emit("saved", sid)
        self.close()
