"""gettext scaffolding (ShareX is localized; ShareL ships English and is ready for translations).

    from sharel.core.i18n import _
    label = _("Capture region")

Catalogs: ``po/<lang>.po`` compiled to ``<locale dir>/<lang>/LC_MESSAGES/sharel.mo``
(``scripts/i18n.sh`` extracts ``po/sharel.pot`` and compiles the .po files).
"""
from __future__ import annotations

import gettext
import os
from pathlib import Path

DOMAIN = "sharel"


def locale_dir() -> str:
    """build/locale in a source checkout (scripts/i18n.sh), else the system locale dir."""
    checkout = Path(__file__).resolve().parents[3] / "build" / "locale"
    return str(checkout) if checkout.is_dir() else "/usr/share/locale"


_translation = gettext.translation(DOMAIN, localedir=locale_dir(), fallback=True,
                                   languages=[os.environ["SHAREL_LANG"]] if os.environ.get("SHAREL_LANG") else None)


# Functions, not bound methods: modules that ``from ..core.i18n import _`` follow set_language().
def _(message: str) -> str:
    return _translation.gettext(message)


def ngettext(singular: str, plural: str, n: int) -> str:
    return _translation.ngettext(singular, plural, n)


def N_(message: str) -> str:
    return message


CODES = {"English": "en"}  # ShareX SupportedLanguage names -> gettext language codes


def set_language(lang: str | None) -> None:
    """Switch catalogs ("Automatic"/None follows the environment). Windows built afterwards use it."""
    global _translation
    langs = None if not lang or lang == "Automatic" else [CODES.get(lang, lang)]
    _translation = gettext.translation(DOMAIN, localedir=locale_dir(), fallback=True, languages=langs)
