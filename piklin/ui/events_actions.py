"""What the window does for Events (see events.py and events_view.py)."""
from __future__ import annotations

from gi.repository import Adw, GLib, Gtk

from .. import events
from ..i18n import _


class EventsActions:
    def _make_events_back(self):
        self.events_back = Gtk.Button(icon_name="go-previous-symbolic", label=_("Events"), visible=False,
                                      tooltip_text=_("Back to Events"))
        self.events_back.connect("clicked", lambda _b: self._back_to_events())
        return self.events_back

    def _build_events_view(self):
        from .events_view import EventsView
        self.events_view = EventsView(self.catalog, self.thumbs, self.settings)
        v = self.events_view
        v.connect("open-event", lambda _v, i: self._open_event(i))
        v.connect("rename", lambda _v, i: self._rename_event(i))
        v.connect("join", lambda _v, i, later: self._join_event(i, later))
        v.connect("album", lambda _v, i: self._event_to_album(i))
        self.content_stack.add_named(v, "events")
        self._event_id = None

    def _show_events(self):
        self._scope = "events"
        self._album_id = self._smart_id = None
        self._event_id = None
        self.events_back.set_visible(False)
        self.events_view.load()
        self._sync_content_view()
        self._on_selection_changed(self.grid)

    def _open_event(self, event_id):
        ids = events.photo_ids(self.catalog, event_id)
        self._scope = "ids"
        self._album_id = self._smart_id = None
        self._person_id = None
        self._event_id = event_id
        self._select_sidebar_key("events")
        self.grid.load("ids", photo_ids=ids)
        self.content_stack.set_visible_child_name("grid")
        self._on_selection_changed(self.grid)
        self.events_back.set_visible(True)

    def _back_to_events(self):
        self._select_sidebar_key("events")
        self._show_events()

    def _event_name(self, event_id) -> str:
        ev = events.event(self.catalog, event_id)
        return events.name_of(ev) if ev is not None else ""

    def _rename_event(self, event_id):
        dialog = Adw.AlertDialog(heading=_("Name This Event"))
        entry = Gtk.Entry(text=self._event_name(event_id), activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("save", _("Save"))
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_close_response("cancel")
        dialog.set_default_response("save")
        GLib.idle_add(lambda: (entry.grab_focus(), entry.select_region(0, -1), False)[2],
                      priority=GLib.PRIORITY_HIGH)

        def done(_d, response):
            if response == "save":
                events.rename(self.catalog, event_id, entry.get_text())
                self.events_view.load(force=False)
                self.events_view._sig = None
                self.events_view.load()
        dialog.connect("response", done)
        dialog.present(self)

    def _join_event(self, event_id, later):
        other = events.neighbour(self.catalog, event_id, later)
        if other is None:
            return
        first, second = (event_id, other["id"]) if later else (other["id"], event_id)
        events.join(self.catalog, first, second)
        self.events_view._sig = None
        self.events_view.load()
        self._show_toast(_("The two events are now one."))

    def _event_to_album(self, event_id):
        album = events.to_album(self.catalog, event_id)
        if album is None:
            return
        self.refresh_sidebar()
        self._show_toast(_("Album “{name}” made from this event.").format(name=self._event_name(event_id)))

    def _start_event_here(self, item):
        """In an event's photos: a new event begins with this photo."""
        eid = getattr(self, "_event_id", None)
        row = self.catalog.photo(item.id)
        if eid is None or row is None or row["taken_at"] is None:
            return
        result = events.split(self.catalog, eid, row["taken_at"])
        if result is None:
            self._show_toast(_("This is the first photo of the event: there is nothing to split."))
            return
        self.events_view._sig = None
        self._open_event(result[1])
        self._show_toast(_("A new event begins with this photo."))
