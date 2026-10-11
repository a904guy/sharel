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
_ = _translation.gettext
ngettext = _translation.ngettext


def N_(message: str) -> str:
    return message


def set_language(lang: str | None) -> None:
    """Switch catalogs at runtime ("Automatic"/None follows the environment)."""
    global _translation, _, ngettext
    langs = None if not lang or lang == "Automatic" else [lang]
    _translation = gettext.translation(DOMAIN, localedir=locale_dir(), fallback=True, languages=langs)
    _ = _translation.gettext
    ngettext = _translation.ngettext
