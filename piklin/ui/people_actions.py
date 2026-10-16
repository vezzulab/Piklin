"""What the window does for People (see people.py and people_view.py)."""
from __future__ import annotations

import threading

from gi.repository import Adw, GLib, Gtk

from .. import people
from ..i18n import _, ngettext


class PeopleActions:
    # -- building ---------------------------------------------------------
    def _make_people_back(self):
        """After a person's photos open, the way back to all the people (the
        sidebar row is still the selected one and cannot do it)."""
        self.people_back = Gtk.Button(icon_name="go-previous-symbolic",
                                      label=_("People"), visible=False,
                                      tooltip_text=_("Back to People"))
        self.people_back.connect("clicked", lambda _b: self._back_to_people())
        return self.people_back

    def _build_people_view(self):
        from .people_view import PeopleView
        self.people_view = PeopleView(self.catalog, self.settings)
        v = self.people_view
        v.connect("open-person", lambda _v, i: self._open_person(i))
        v.connect("rename", lambda _v, i: self._rename_person(i))
        v.connect("turn", self._turn_picture)
        v.connect("choose-picture", self._choose_picture)
        v.connect("merge", lambda _v, i: self._merge_person(i))
        v.connect("ignore", lambda _v, i: self._ignore_person(i))
        v.connect("same", lambda _v, a, b, picked, shown: self._join_people(a, b, picked, shown))
        v.connect("different", lambda _v, a, b: self._keep_apart(a, b))
        v.connect("find-more", lambda _v: self._find_more_people())
        v.connect("turn-on", lambda _v: self._turn_on_people())
        v.connect("turn-off", lambda _v: self._turn_off_people())
        self.content_stack.add_named(v, "people")
        self._person_id = None

    def _choose_picture(self, _view, person_id):
        """Which photo is the person's picture: open their photos, where a
        right-click on one says so."""
        self._open_person(person_id)
        self._show_toast(_("Right-click a photo and choose “Use as Picture”."))

    def _use_as_picture(self, photo_id):
        """In a person's photos: this one is their picture. A window opens over the
        photo, with the circle on the person's face if Piklin found it, to be put
        where it should be."""
        pid = self._person_id
        if pid is None:
            return
        row = self.catalog.photo(photo_id)
        if row is None:
            return
        face = self.catalog.con.execute(
            "SELECT x, y, w, h FROM faces WHERE person_id=? AND photo_id=? AND state<2 "
            "ORDER BY w*h DESC LIMIT 1", (pid, photo_id)).fetchone()
        from .cover_dialog import CoverDialog
        try:
            dialog = CoverDialog(row["path"], photo_id, dict(face) if face else None)
        except Exception as exc:
            from ..logs import log
            log.warning("could not open the photo to frame a picture: %s", exc)
            self._show_toast(_("This photo could not be opened."))
            return

        def framed(_d, photo, cx, cy, side, rot):
            if people.set_cover_custom(self.catalog, pid, photo, cx, cy, side, rot):
                self._show_toast(_("This is now the picture of {name}.").format(
                    name=self._person_name(pid) or _("this person")))
                self.people_view.update_cover(pid)
        dialog.connect("framed", framed)
        dialog.present(self)

    def _turn_picture(self, _view, person_id, quarter_turns):
        """Turn a person's picture. Only that picture changes: the list stays
        where it is."""
        people.turn_cover(self.catalog, person_id, quarter_turns)
        self.people_view.update_cover(person_id)

    def _start_people(self):
        from ..peoplewatch import PeopleWatch
        self.people_watch = PeopleWatch(
            self.library, self.catalog, self.settings, self.autobackup, self.indexer,
            self.health_watch, self.video_watch, self._on_people_change)
        self.people_view.watch = self.people_watch

    def _on_people_change(self, st):
        if self._scope == "people":
            self.people_view.refresh()
        return False

    # -- going there -------------------------------------------------------
    def _show_people(self):
        self._scope = "people"
        self._album_id = None
        self._person_id = None
        self.people_back.set_visible(False)
        # what was drawn is kept when nothing changed: coming back from a
        # person's photos is instant and the list is where it was left
        if getattr(self.people_view, "_sig", None) is None:
            self.people_view.load()
        else:
            self.people_view.refresh()
        self._sync_content_view()
        self._on_selection_changed(self.grid)

    def _open_person(self, person_id):
        ids = people.photo_ids(self.catalog, person_id)
        self._scope = "ids"
        self._album_id = self._smart_id = None
        self._person_id = person_id
        self._select_sidebar_key("people")
        self.grid.load("ids", photo_ids=ids)
        self.content_stack.set_visible_child_name("grid")
        self._on_selection_changed(self.grid)
        self.people_back.set_visible(True)

    def _back_to_people(self):
        self._select_sidebar_key("people")
        self._show_people()

    # -- turning it on and off ---------------------------------------------
    def _turn_on_people(self):
        self.people_watch.set_enabled(True)
        self.people_view.load()
        self._show_toast(_("Looking for people. It happens in the background."))

    def _turn_off_people(self):
        dialog = Adw.AlertDialog(
            heading=_("Turn Off People?"),
            body=_("The faces Piklin found and the names you gave are deleted from this "
                   "computer. Your photos are not touched. You can turn People on again, "
                   "and the photos are looked at once more."))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("off", _("Turn Off and Delete"))
        dialog.set_response_appearance("off", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")

        def done(_d, response):
            if response == "off":
                self.people_watch.set_enabled(False)
                GLib.timeout_add(600, lambda: (self.people_view.load(), False)[1])
        dialog.connect("response", done)
        dialog.present(self)

    # -- correcting what Piklin grouped -------------------------------------
    def _person_name(self, person_id):
        row = self.catalog.con.execute("SELECT name FROM people WHERE id=?",
                                       (person_id,)).fetchone()
        return (row[0] if row else None) or ""

    def _rename_person(self, person_id):
        dialog = Adw.AlertDialog(heading=_("Name This Person"))
        entry = Gtk.Entry(text=self._person_name(person_id), activates_default=True)
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
                text = entry.get_text()
                before = len(people.list_people(self.catalog))
                people.rename(self.catalog, person_id, text)
                if len(people.list_people(self.catalog)) < before:
                    self._show_toast(_("Joined with the other {name}.").format(name=text.strip()))
                self.people_view.load()
        dialog.connect("response", done)
        dialog.present(self)

    def _merge_person(self, person_id):
        others = [p for p in people.list_people(self.catalog) if p["id"] != person_id]
        if not others:
            self._show_toast(_("There is nobody else to join."))
            return
        labels = [(p["name"] or _("Unnamed")) + " · "
                  + ngettext("{count} photo", "{count} photos", p["photos"]).format(
                      count=p["photos"]) for p in others]
        dialog = Adw.AlertDialog(
            heading=_("Same Person As…"),
            body=_("Join this person with another: their photos become one person."))
        combo = Gtk.DropDown.new_from_strings(labels)
        dialog.set_extra_child(combo)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("join", _("Join"))
        dialog.set_response_appearance("join", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_close_response("cancel")

        def done(_d, response):
            if response != "join":
                return
            other = others[combo.get_selected()]
            # the named one stays; between two named, the one chosen here
            keep, gone = ((other["id"], person_id) if other["name"] and not self._person_name(person_id)
                          else (person_id, other["id"]))
            people.merge(self.catalog, keep, gone)
            self.people_view.load()
        dialog.connect("response", done)
        dialog.present(self)

    def _find_more_people(self):
        """Look at any photo not looked at yet, then at the faces in no group, less
        strictly. In another thread: it reads every face."""
        self.people_view.busy_finding()

        def work():
            found = None
            try:
                from .. import system
                system.lower_thread_priority()
                # photos that came in and have not been looked at: now, not in an hour
                while True:
                    batch = people.pending(self.catalog, 48)
                    if not batch:
                        break
                    people.scan_photos(self.catalog, batch)
                found = people.find_more(self.catalog)
            except Exception as exc:
                from ..logs import log
                log.warning("Find More People failed: %s", exc)
            GLib.idle_add(done, found)

        def done(found):
            self.people_view.done_finding()
            if found is None:
                self._show_toast(_("Piklin could not look for more people."))
            elif found["new_people"] or found["placed"]:
                self._show_toast(_("Found {people} more people and put {faces} faces in a group.")
                                 .format(people=found["new_people"], faces=found["placed"]))
            else:
                self._show_toast(_("No more people to find. What is left is too little, or too "
                                   "turned away, to group."))
            self.people_view.load()
            return False
        threading.Thread(target=work, daemon=True, name="people-more").start()

    def _join_people(self, a, b, picked, shown):
        """"Yes": the faces picked as the same person become one person. Joined
        in another thread - it reads every face - and drawn when it is done."""
        def work():
            joined = people.join_faces(self.catalog, a, b, picked, shown)
            if joined is None:
                GLib.idle_add(self._show_toast, _("Click a face of each person first."))
            GLib.idle_add(self.people_view.load)
        threading.Thread(target=work, daemon=True, name="people-join").start()

    def _keep_apart(self, a, b):
        def work():
            people.not_same(self.catalog, a, b)
            GLib.idle_add(self.people_view.load)
        threading.Thread(target=work, daemon=True, name="people-apart").start()

    def _ignore_person(self, person_id):
        people.ignore_person(self.catalog, person_id)
        self.people_view.load()
        self._show_toast(_("Put away. It will not appear again."))

    def _not_this_person(self, photo_ids):
        """In a person's photos: this is not them. Their face in those photos is
        taken out of the person, and not put back by itself."""
        pid = self._person_id
        if pid is None or not photo_ids:
            return
        marks = ",".join("?" * len(photo_ids))
        faces = [r[0] for r in self.catalog.con.execute(
            f"SELECT id FROM faces WHERE person_id=? AND photo_id IN ({marks})",
            [pid, *photo_ids])]
        people.not_this_person(self.catalog, faces)
        self._open_person(pid) if people.photo_ids(self.catalog, pid) else self._back_to_people()
        self._show_toast(ngettext("Taken out of this person", "Taken out of this person",
                                  len(photo_ids)))
