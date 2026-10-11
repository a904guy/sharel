"""About (ShareX's AboutForm) as an Adw.AboutDialog: version, credits, license, links, debug info."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from ... import __version__
from ...core import paths

SHAREX_URL = "https://getsharex.com"
SHAREX_GITHUB = "https://github.com/ShareX/ShareX"


def open_about(app) -> Adw.AboutDialog:
    from .debuglog import basic_info, info_text
    dlg = Adw.AboutDialog(
        application_name="ShareL",
        application_icon=paths.APP_ID,
        version=__version__,
        developer_name="The ShareL developers",
        comments="Screen capture, file sharing and productivity for GNOME — a feature-for-feature "
                 "re-creation of ShareX for Linux.",
        license_type=Gtk.License.GPL_3_0,  # GPL-3.0-or-later
        copyright="ShareL: GPL-3.0-or-later. Inspired by ShareX © ShareX Team (GPL-3.0).",
        debug_info=info_text(basic_info(app)),
        debug_info_filename="ShareL-debug-info.txt",
    )
    dlg.add_credit_section("Inspired by", [f"ShareX {SHAREX_URL}", f"ShareX Team {SHAREX_GITHUB}"])
    dlg.add_acknowledgement_section("Built with", [
        "GTK https://gtk.org", "libadwaita https://gnome.pages.gitlab.gnome.org/libadwaita/",
        "PyGObject https://pygobject.gnome.org", "Pillow https://python-pillow.org", "FFmpeg https://ffmpeg.org"])
    dlg.add_link("ShareX on GitHub", SHAREX_GITHUB)
    dlg.add_link("ShareX documentation", f"{SHAREX_URL}/docs")
    dlg.add_legal_section("ShareX", "© ShareX Team", Gtk.License.GPL_3_0, None)
    dlg.present(app.active_parent())
    return dlg
