"""People: a card for each person found in the photos.

Everything it shows was made on this computer, from the photos on it (see
people.py). A card is a face, a name the person gave and how many photos; a
click opens that person's photos. Piklin's groups are suggestions, so every
card can be named, joined to another or put away.
"""
from __future__ import annotations

import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gdk, Gtk  # noqa: E402

from .. import people
from ..i18n import _, ngettext

AVATAR = 128
SUGGEST_EVERY = 30      # seconds, while photos are being looked at
ASK_FACES = 8           # faces shown of each group in the question
ASK_SIZE = 76
ASK_PER_ROW = 4


# The pictures of faces are read by two workers, not one thread for each card:
# with two hundred people that was two hundred photos opened at once, and the
# window stood still. What was read is kept, so coming back is instant.
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="people-faces")
_TEXTURES: dict = {}
_TEXTURES_MAX = 1200
BUILD_SLICE = 0.012         # seconds of cards made in one go; the rest in the next turns of the window


def _remember(key, texture):
    if len(_TEXTURES) >= _TEXTURES_MAX:
        _TEXTURES.clear()
    _TEXTURES[key] = texture


def _texture(img):
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    return Gdk.Texture.new_from_bytes(GLib.Bytes.new(buf.getvalue()))


class PeopleView(Gtk.ScrolledWindow):
    __gsignals__ = {
        "open-person": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        "rename": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        "turn": (GObject.SignalFlags.RUN_FIRST, None, (int, int)),
        "choose-picture": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        "merge": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        "ignore": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        # the two groups, the faces picked, the faces shown
        "same": (GObject.SignalFlags.RUN_FIRST, None, (int, int, object, object)),
        "different": (GObject.SignalFlags.RUN_FIRST, None, (int, int)),
        "find-more": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "turn-on": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "turn-off": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, catalog, settings, watch=None):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                         hexpand=True)
        self.add_css_class("pika-summary")
        self.catalog = catalog
        self.settings = settings
        self.watch = watch
        self._generation = 0
        self._asked = None
        self._asked_at = 0.0
        self._cards = {}
        self._sig = None

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, margin_top=22)
        title = Gtk.Label(label=_("People"), xalign=0.0, hexpand=True, margin_start=24)
        title.add_css_class("pika-section-title")
        title.add_css_class("pika-folder-heading")
        head.append(title)
        self.more_btn = Gtk.Button(label=_("Find More People"), valign=Gtk.Align.CENTER)
        self.more_btn.set_tooltip_text(_("Look again at the faces that are in no group, a little less strictly"))
        self.more_btn.add_css_class("pill")
        self.more_btn.connect("clicked", lambda *_a: self.emit("find-more"))
        head.append(self.more_btn)
        self.off_btn = Gtk.Button(label=_("Turn Off People…"), valign=Gtk.Align.CENTER,
                                  margin_end=24)
        self.off_btn.add_css_class("flat")
        self.off_btn.connect("clicked", lambda *_a: self.emit("turn-off"))
        head.append(self.off_btn)
        self.subtitle = Gtk.Label(xalign=0.0, wrap=True, margin_top=2,
                                  margin_start=24, margin_end=24)
        self.subtitle.add_css_class("pika-dim")
        # "Is this the same person?": two groups that look alike, each shown by
        # several of its faces, and a big question - for one tap
        self.ask = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                           margin_top=16, margin_start=24, margin_end=24,
                           halign=Gtk.Align.START, visible=False)
        self.ask.add_css_class("card")
        self.ask_label = Gtk.Label(label=_("Is this the same person?"), xalign=0.0,
                                   margin_top=18, margin_start=20, margin_end=20)
        self.ask_label.add_css_class("title-2")
        self.ask.append(self.ask_label)
        hint = Gtk.Label(label=_("Click on the faces that are the same person, then click Yes."),
                         xalign=0.0, wrap=True, margin_start=20, margin_end=20)
        hint.add_css_class("pika-dim")
        self.ask.append(hint)
        faces = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=26,
                        margin_start=20, margin_end=20)
        self._ask_cols = []                 # per group: (buttons, count label)
        for i in range(2):
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                               min_children_per_line=ASK_PER_ROW,
                               max_children_per_line=ASK_PER_ROW,
                               column_spacing=6, row_spacing=6, valign=Gtk.Align.START)
            buttons = []
            for _j in range(ASK_FACES):
                av = Adw.Avatar(size=ASK_SIZE)
                av.set_icon_name("pk-user-symbolic")
                tog = Gtk.ToggleButton(child=av, visible=False)
                tog.add_css_class("flat")
                tog.add_css_class("circular")
                tog.add_css_class("pika-face-pick")
                tog.connect("toggled", self._on_pick)
                tog.face_id = None
                tog.avatar = av
                grid.append(tog)
                buttons.append(tog)
            count = Gtk.Label(xalign=0.0)
            count.add_css_class("pika-dim")
            col.append(grid)
            col.append(count)
            faces.append(col)
            self._ask_cols.append((buttons, count))
            if i == 0:
                faces.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        self.ask.append(faces)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
                      margin_start=20, margin_end=20, margin_bottom=18)
        self.ask_yes = Gtk.Button(label=_("Yes, Join"), sensitive=False)
        self.ask_yes.add_css_class("suggested-action")
        self.ask_yes.add_css_class("pill")
        no = Gtk.Button(label=_("No"))
        no.add_css_class("pill")
        everyone = Gtk.Button(label=_("Select All"), hexpand=True, halign=Gtk.Align.END)
        everyone.add_css_class("flat")
        everyone.connect("clicked", self._on_select_all)
        self.ask_yes.connect("clicked", self._on_yes)
        no.connect("clicked", self._on_no)
        row.append(self.ask_yes)
        row.append(no)
        row.append(everyone)
        self.ask.append(row)
        self.ask_pair = None
        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                                homogeneous=True, valign=Gtk.Align.START,
                                column_spacing=18, row_spacing=26,
                                min_children_per_line=1, max_children_per_line=14,
                                margin_top=18, margin_bottom=30,
                                margin_start=24, margin_end=24)
        self.empty = Adw.StatusPage(icon_name="pk-user-symbolic", vexpand=True,
                                    visible=False)
        self.turn_on = Gtk.Button(label=_("Find People"), halign=Gtk.Align.CENTER)
        self.turn_on.add_css_class("suggested-action")
        self.turn_on.add_css_class("pill")
        self.turn_on.connect("clicked", lambda *_a: self.emit("turn-on"))
        self.empty.set_child(self.turn_on)
        for w in (head, self.subtitle, self.ask, self.flow, self.empty):
            box.append(w)
        self.set_child(box)
        self._head = head

    # -- data ------------------------------------------------------------
    # Reading what to show (every face, every person) is work for another
    # thread: it took long enough on a big library to freeze the window while
    # the pictures were still coming in. The window only draws what was read.
    def refresh(self) -> None:
        """While photos are being looked at: redraw only if the people changed,
        so the cards do not flicker and their faces are not fetched again."""
        self._request(True)

    def load(self) -> None:
        self._request(False)

    def _request(self, only_if_changed: bool) -> None:
        self._generation += 1
        gen = self._generation

        def work():
            data = self._compute(forced=not only_if_changed)
            GLib.idle_add(self._render, gen, data, only_if_changed)
        threading.Thread(target=work, daemon=True, name="people-view").start()

    def _compute(self, forced: bool = True) -> dict:
        enabled = bool(self.settings.get("people_enabled"))
        if not enabled and self.catalog.scalar("SELECT COUNT(*) FROM people", (), 0):
            enabled = True          # sent by another computer: see PeopleWatch.enabled
            self.settings.set("people_enabled", True)
        data = {"enabled": enabled,
                "available": people.available()}
        if not (data["enabled"] and data["available"]):
            return data
        if forced:
            people.merge_same_names(self.catalog)       # two with one name are one person
        data["rows"] = people.list_people(self.catalog)
        # if the list comes out empty while the catalog has people, say so in the log:
        # that is a bug to find, not a library without people
        data["raw_people"] = self.catalog.scalar("SELECT COUNT(*) FROM people", (), 0)
        data["left"] = self.watch.remaining() if self.watch is not None else 0
        data["loose"] = people.leftover(self.catalog)
        # Every face is read to find the pair worth asking about: while photos are
        # being looked at (refresh, not load) that is done now and then, not
        # after every few photos.
        if (not forced) and time.monotonic() - self._asked_at < SUGGEST_EVERY and self._asked is not None:
            data["pair"] = self._asked
        else:
            found = people.suggestions(self.catalog, 1)
            data["pair"] = (found[0]["a"], found[0]["b"]) if found else None
            self._asked, self._asked_at = data["pair"], time.monotonic()
        if data["pair"]:
            data["samples"] = {pid: people.sample_faces(self.catalog, pid, ASK_FACES)
                               for pid in data["pair"]}
        return data

    def _render(self, gen, data, only_if_changed) -> bool:
        if gen != self._generation:
            return False                        # something newer was asked for
        if not (data["enabled"] and data["available"]):
            while (child := self.flow.get_first_child()) is not None:
                self.flow.remove(child)
            self._sig = None
            self._head.set_visible(False)
            self.subtitle.set_visible(False)
            self.ask.set_visible(False)
            self.flow.set_visible(False)
            self.empty.set_visible(True)
            self.turn_on.set_visible(data["available"])
            self.empty.set_title(_("Find the People in Your Photos"))
            self.empty.set_description(
                _("Piklin looks for faces and groups the ones that are alike, so you can name "
                  "them and find every photo of someone. It all happens on this computer: no "
                  "photo and no face is ever sent anywhere.")
                if data["available"] else
                _("This copy of Piklin does not have what is needed to recognise faces."))
            return False
        rows, left = data["rows"], data["left"]
        sig = [(r["id"], r["name"], r["photos"], r["cover_face"], r["cover_turn"], r["cover_custom"]) for r in rows]
        self._head.set_visible(True)
        self.off_btn.set_visible(True)
        self.subtitle.set_visible(True)
        self._set_subtitle(rows, left, data.get("loose", 0))
        self.more_btn.set_sensitive(True)
        self._show_question(data)
        if only_if_changed and sig == getattr(self, "_sig", None):
            return False
        self._sig = None          # until the list is whole: a half-made one is never taken as drawn
        # Where the list was: coming back after a change, it is not at the top
        # again, whatever caused the change
        keep = self.get_vadjustment().get_value()
        while (child := self.flow.get_first_child()) is not None:
            self.flow.remove(child)
        self._cards = {}
        self.flow.set_visible(bool(rows))
        self.empty.set_visible(not rows)
        if not rows:
            if data.get("raw_people"):
                from ..logs import log
                log.warning("People: the list is empty, but the catalog has %d people (enabled=%s, left=%s)",
                            data["raw_people"], data["enabled"], left)
            self.turn_on.set_visible(False)
            self.empty.set_title(_("Looking for People") if left else _("No People Yet"))
            self.empty.set_description(
                _("The first people appear as the photos are looked at.") if left else
                _("People appear when the same face shows up in a few photos."))
            return False
        self._build(gen, rows, 0, keep, sig)
        return False

    def _build(self, gen, rows, start, keep, sig) -> bool:
        """Make the cards for a few milliseconds at a time, so that a long list does
        not hold the window still while it is made. The next turn is asked for with
        a timer, not an idle callback: a window that is busy (thumbnails, the people
        scan, the backup) never gets to the idle ones, and the list stayed with its
        first cards."""
        if gen != self._generation:
            return False
        began = time.monotonic()
        i = start
        while i < len(rows):
            self.flow.append(self._card(rows[i], gen))
            i += 1
            if time.monotonic() - began > BUILD_SLICE:
                break
        done = i >= len(rows)
        if done:
            self._sig = sig
        # put the list back where it was, as far as it has grown
        adj = self.get_vadjustment()
        if keep:
            GLib.idle_add(lambda: adj.set_value(min(keep, max(0.0, adj.get_upper() - adj.get_page_size()))) or False)
        if not done:
            GLib.timeout_add(1, self._build, gen, rows, i, keep, sig)
        return False

    def update_cover(self, person_id: int) -> None:
        """One person's picture changed (turned): only that picture is made
        again. The list is not built again, so it stays where it is."""
        avatar = getattr(self, "_cards", {}).get(person_id)
        row = self.catalog.con.execute(
            "SELECT cover_face, cover_turn, cover_custom FROM people WHERE id=?", (person_id,)).fetchone()
        if avatar is None or row is None or not (row[0] or row[2]):
            return
        face, turn, custom = row[0] or 0, row[1], row[2]
        sig = getattr(self, "_sig", None)
        if sig:
            self._sig = [(a, b, c, face, turn, custom) if a == person_id else (a, b, c, d, e, f)
                         for a, b, c, d, e, f in sig]
        self._load_picture(avatar, face, turn, AVATAR * 2, self._generation, custom)

    def _load_picture(self, avatar, face, turn, size, gen, custom=None) -> None:
        """A face's picture into an avatar: at once if it was seen before, else by
        the workers."""
        key = (face, turn, size, custom)
        texture = _TEXTURES.get(key)
        if texture is not None:
            avatar.set_custom_image(texture)
            return

        def work():
            if gen != self._generation:
                return                          # the list was made again meanwhile
            img = people.cover_image(self.catalog, face, size, turn, custom)
            if img is not None:
                GLib.idle_add(show, _texture(img))

        def show(texture):
            _remember(key, texture)
            avatar.set_custom_image(texture)
            return False
        _pool.submit(work)

    def _show_question(self, data) -> None:
        """"Is this the same person?" for the pair read, unless it is the pair
        already on screen (what was picked stays picked)."""
        pair = data.get("pair")
        if not pair:
            self.ask.set_visible(False)
            self.ask_pair = None
            return
        self.ask.set_sensitive(True)
        if pair == self.ask_pair and self.ask.get_visible():
            return
        self.ask_pair = pair
        self.ask.set_visible(True)
        counts = {p["id"]: p["photos"] for p in data["rows"]}
        for (buttons, count), pid in zip(self._ask_cols, pair):
            count.set_text(ngettext("{count} photo", "{count} photos",
                                    counts.get(pid, 0)).format(count=f"{counts.get(pid, 0):,}"))
            faces = data["samples"].get(pid, [])
            for i, tog in enumerate(buttons):
                tog.set_active(False)
                tog.avatar.set_custom_image(None)
                tog.face_id = faces[i] if i < len(faces) else None
                tog.set_visible(i < len(faces))

            def work(face, tog, pair=pair):
                if self.ask_pair != pair:           # answered, or another pair: stop
                    return
                img = people.cover_image(self.catalog, face, ASK_SIZE * 2)
                if img is not None:
                    GLib.idle_add(show, tog, face, _texture(img), pair)

            def show(tog, face, texture, pair_now):
                _remember((face, 0, ASK_SIZE * 2, None), texture)
                if pair_now == self.ask_pair:
                    tog.avatar.set_custom_image(texture)
                return False
            for face, tog in zip(faces, buttons):
                cached = _TEXTURES.get((face, 0, ASK_SIZE * 2, None))
                if cached is not None:
                    tog.avatar.set_custom_image(cached)
                else:
                    _pool.submit(work, face, tog)
        self._on_pick()

    def _picked(self):
        """Per group: the faces picked, and the faces shown."""
        picked, shown = [[], []], [[], []]
        for k, (buttons, _count) in enumerate(self._ask_cols):
            for tog in buttons:
                if tog.face_id is not None:
                    shown[k].append(tog.face_id)
                    if tog.get_active():
                        picked[k].append(tog.face_id)
        return picked, shown

    def _on_pick(self, *_a):
        """Yes is for when a face of each group has been picked."""
        picked, _shown = self._picked()
        self.ask_yes.set_sensitive(bool(picked[0]) and bool(picked[1]))

    def _on_select_all(self, *_a):
        """Every face shown: the two groups are one person."""
        for buttons, _count in self._ask_cols:
            for tog in buttons:
                if tog.face_id is not None:
                    tog.set_active(True)

    def _on_no(self, *_a):
        if not self.ask_pair:
            return
        pair = self.ask_pair
        self.ask.set_sensitive(False)
        self.ask_pair = None
        self.emit("different", *pair)

    def _on_yes(self, *_a):
        if not self.ask_pair:
            return
        picked, shown = self._picked()
        pair = self.ask_pair
        self.ask.set_sensitive(False)           # answered: no second click, and the pictures stop
        self.ask_pair = None
        self.emit("same", pair[0], pair[1], picked[0] + picked[1], shown[0] + shown[1])

    def busy_finding(self) -> None:
        """The button was pressed: it cannot be pressed again until it is done."""
        self.more_btn.set_sensitive(False)
        self.more_btn.set_label(_("Looking…"))

    def done_finding(self) -> None:
        self.more_btn.set_label(_("Find More People"))
        self.more_btn.set_sensitive(True)

    def _set_subtitle(self, rows, left, loose: int = 0) -> None:
        if left:
            self.subtitle.set_text(
                ngettext("Looking at photos for faces: {count} left. It happens while the "
                         "computer is plugged in and idle.",
                         "Looking at photos for faces: {count} left. It happens while the "
                         "computer is plugged in and idle.", left).format(count=f"{left:,}"))
        else:
            named = sum(1 for r in rows if r["name"])
            self.subtitle.set_text(
                ngettext("{count} person", "{count} people", len(rows)).format(count=len(rows))
                + (" · " + ngettext("{n} named", "{n} named", named).format(n=named)
                   if rows else "")
                + (" · " + ngettext("{count} face is in no group yet",
                                    "{count} faces are in no group yet", loose).format(count=f"{loose:,}")
                   if loose else ""))

    def _menu_for(self, pid):
        """The menu of a person's card, made when it is opened: two hundred of
        them made with the cards were most of the time of showing the list."""
        pop = Gtk.Popover()
        items = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for label, act in ((_("Name…"), lambda: self.emit("rename", pid)),
                           (_("Choose Picture…"), lambda: self.emit("choose-picture", pid)),
                           (_("Turn Picture Left"), lambda: self.emit("turn", pid, -1)),
                           (_("Turn Picture Right"), lambda: self.emit("turn", pid, 1)),
                           (_("Same Person As…"), lambda: self.emit("merge", pid)),
                           (_("Not a Person to Keep"), lambda: self.emit("ignore", pid))):
            b = Gtk.Button(label=label)
            b.add_css_class("flat")
            b.connect("clicked", lambda _b, a=act, p=pop: (p.popdown(), a()))
            items.append(b)
        pop.set_child(items)
        return pop

    def _card(self, row, gen):
        pid = row["id"]
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER)
        avatar = Adw.Avatar(size=AVATAR, text=row["name"] or "", show_initials=bool(row["name"]))
        avatar.set_icon_name("pk-user-symbolic")
        click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        click.connect("released", lambda *_a: self.emit("open-person", pid))
        avatar.add_controller(click)
        avatar.set_cursor_from_name("pointer")
        card.append(avatar)
        name = Gtk.Label(label=row["name"] or _("Add a Name"), ellipsize=3, max_width_chars=16)
        if not row["name"]:
            name.add_css_class("pika-dim")
        else:
            name.add_css_class("heading")
        card.append(name)
        count = Gtk.Label(label=ngettext("{count} photo", "{count} photos",
                                         row["photos"]).format(count=f"{row['photos']:,}"))
        count.add_css_class("pika-dim")
        card.append(count)
        menu = Gtk.MenuButton(icon_name="view-more-symbolic", halign=Gtk.Align.CENTER,
                              tooltip_text=_("More"))
        menu.add_css_class("flat")
        menu.set_create_popup_func(lambda m, pid=pid: m.set_popover(self._menu_for(pid)))
        card.append(menu)
        self._cards[pid] = avatar
        if row["cover_face"] or row["cover_custom"]:
            self._load_picture(avatar, row["cover_face"] or 0, row["cover_turn"], AVATAR * 2, gen,
                               row["cover_custom"])
        return card
