"""ShareL's GSettings schema (app.sharel.ShareL), when it is installed; JSON config otherwise."""
from __future__ import annotations

import os

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio

SCHEMA = "app.sharel.ShareL"


def settings() -> Gio.Settings | None:
    """Gio.Settings for ShareL, or None when the schema isn't installed (source checkout)."""
    src = Gio.SettingsSchemaSource.get_default()
    if src is None:
        return None
    schema = src.lookup(SCHEMA, True)
    if schema is None:
        local = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "glib-2.0", "schemas")
        compiled = os.path.join(local, "gschemas.compiled")
        if os.path.exists(compiled):
            src = Gio.SettingsSchemaSource.new_from_directory(local, src, False)
            schema = src.lookup(SCHEMA, False)
    if schema is None:
        return None
    return Gio.Settings.new_full(schema, None, None)
