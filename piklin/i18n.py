"""The languages Piklin speaks: English and Spanish, with room for more.

The interface follows the computer's language unless one is chosen in
Preferences. Translations are standard gettext catalogs in
``piklin/locale/<language>/LC_MESSAGES/piklin.mo``, built from the ``.po``
files in ``po/``; a language without a catalog simply shows English.

Code marks visible text with ``_("...")``. Text defined before the language
is known - tool names in a table at import time, for instance - is marked
with ``N_("...")`` and passed through ``_()`` where it is shown.
"""
from __future__ import annotations

import gettext
import json
import os
from pathlib import Path

DOMAIN = "piklin"
LOCALE_DIR = Path(__file__).parent / "locale"

# Shown in Preferences, each in its own language so anyone can find theirs.
LANGUAGES = (("", "Match the computer"), ("en", "English"), ("es", "Español"))

_translation: gettext.NullTranslations = gettext.NullTranslations()
_current = "en"


def _config_file() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "piklin" / "language.json"


def chosen_language() -> str:
    """The language picked in Preferences; "" means follow the computer."""
    try:
        value = json.loads(_config_file().read_text()).get("language", "")
    except (OSError, ValueError, AttributeError):
        return ""
    return value if value in {code for code, _name in LANGUAGES} else ""


def choose_language(code: str) -> None:
    path = _config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"language": code}))


def setup(language: str | None = None) -> str:
    """Load the translation. Call before any window is built.

    Returns the language in use ("en", "es", ...).
    """
    global _translation, _current
    if language is None:
        language = chosen_language()
    if language:
        # GTK's own dialogs (file chooser, About) follow the same choice.
        os.environ["LANGUAGE"] = language
        languages = [language]
    else:
        languages = None                      # LANGUAGE, LC_ALL, LC_MESSAGES, LANG
    _translation = gettext.translation(DOMAIN, LOCALE_DIR, languages=languages,
                                       fallback=True)
    # A catalog names its own language in its header; English has none.
    _current = (_translation.info().get("language") or "en").split("_")[0]
    return _current


def current_language() -> str:
    return _current


def _(message: str) -> str:
    return _translation.gettext(message)


def ngettext(singular: str, plural: str, n: int) -> str:
    return _translation.ngettext(singular, plural, n)


def pgettext(context: str, message: str) -> str:
    return _translation.pgettext(context, message)


def N_(message: str) -> str:
    """Mark text for translation without translating it yet."""
    return message


# -- dates ------------------------------------------------------------------
# strftime names follow the system locale, not the language chosen in
# Piklin, so month and day names come from the catalog instead.
MONTHS = (N_("January"), N_("February"), N_("March"), N_("April"), N_("May"),
          N_("June"), N_("July"), N_("August"), N_("September"), N_("October"),
          N_("November"), N_("December"))
WEEKDAYS = (N_("Monday"), N_("Tuesday"), N_("Wednesday"), N_("Thursday"),
            N_("Friday"), N_("Saturday"), N_("Sunday"))


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def month_name(month: int) -> str:
    return _(MONTHS[month - 1])


def weekday_name(weekday: int) -> str:
    """Monday is 0, as in datetime.weekday()."""
    return _(WEEKDAYS[weekday])


def month_year(d) -> str:
    """ "March 2026" """
    return _capital(_("{month} {year}").format(month=month_name(d.month), year=d.year))


def long_date(d) -> str:
    """ "Monday, 3 March 2026" """
    return _capital(_("{weekday}, {day} {month} {year}").format(
        weekday=weekday_name(d.weekday()), day=d.day,
        month=month_name(d.month), year=d.year))


def day_month(d) -> str:
    """ "3 March" """
    return _("{day} {month}").format(day=d.day, month=month_name(d.month))
