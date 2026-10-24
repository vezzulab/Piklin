"""Alerts, all made one way.

The formula every modal of Piklin follows, so that none has to be fixed again:

  * One answer does the thing - Create, Rename, Delete, Leave, Join. It is the black
    pill (SUGGESTED; DESTRUCTIVE when it deletes). There is never more than one.
  * Every other answer is a soft grey pill, with air between the pills. "Cancel" (or
    the way out, such as "Keep Working") closes the alert.
  * A short heading, then one or two plain sentences. Anything the person fills in goes
    in ``extra`` (an entry, a drop-down), under the text.

How they look is written once, in style.css ("alerts"): it covers every
Adw.AlertDialog in the program, so a new alert only has to name its answers right.
Build new alerts with ``alert()``; tests_full checks that no alert with several
answers leaves them all grey.
"""
from __future__ import annotations

import gi

gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402

from ..i18n import _


def alert(heading: str, body: str = "", *, answer: tuple[str, str] | None = None,
          destructive: bool = False, cancel: str | None = None, extra=None,
          on_answer=None) -> Adw.AlertDialog:
    """An alert with the way out and, if it asks for something, its one answer.

    ``answer``: (id, label) of the answer that does the thing. ``cancel``: the label
    of the way out (``_("Cancel")`` if left out and there is an answer). ``on_answer``
    is called with the id of whatever was chosen. Present it with ``.present(window)``.
    """
    dialog = Adw.AlertDialog(heading=heading, body=body)
    if cancel is None and answer is not None:
        cancel = _("Cancel")
    if cancel:
        dialog.add_response("cancel", cancel)
        dialog.set_close_response("cancel")
    if answer is not None:
        key, label = answer
        dialog.add_response(key, label)
        dialog.set_response_appearance(
            key, Adw.ResponseAppearance.DESTRUCTIVE if destructive
            else Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response(key)
    if extra is not None:
        dialog.set_extra_child(extra)
    if on_answer is not None:
        dialog.connect("response", lambda _d, response: on_answer(response))
    return dialog
