"""Settings pages generated from dataclasses: every field becomes a libadwaita row bound to it.

bool → switch, int/float → spin, str → entry (secret → password, multiline → text view),
Enum → combo, IntFlag → checklist, "#AARRGGBB" colours → colour button, kind='path'/'file'/
'folder' → chooser, nested dataclasses → an expander, OAuth2Info → status + Authorize button
(when the owner supplies ``on_authorize``). Lists of dataclasses get an add/remove editor.
"""
from __future__ import annotations

import dataclasses
import re
import typing
from collections.abc import Callable
from enum import Enum, IntFlag
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk

COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{8}$|^#[0-9A-Fa-f]{6}$")
ACRONYMS = {"url": "URL", "urls": "URLs", "ftp": "FTP", "ftps": "FTPS", "sftp": "SFTP", "api": "API", "id": "ID",
            "ocr": "OCR", "jpeg": "JPEG", "png": "PNG", "gif": "GIF", "http": "HTTP", "https": "HTTPS", "qr": "QR",
            "fps": "FPS", "crf": "CRF", "ssl": "SSL", "tls": "TLS", "smtp": "SMTP", "acl": "ACL", "s3": "S3",
            "oauth": "OAuth", "oauth2": "OAuth 2", "ttl": "TTL", "pin": "PIN", "x264": "x264", "nvenc": "NVENC",
            "vaapi": "VA-API", "qsv": "QSV", "gpu": "GPU", "ui": "UI", "kb": "KB", "mb": "MB", "dpi": "DPI"}


def humanize(name: str) -> str:
    words = name.split("_")
    out = [ACRONYMS.get(w.lower(), w) for w in words if w]
    if out:
        out[0] = out[0][0].upper() + out[0][1:]
    return " ".join(out)


def _hints(obj) -> dict[str, Any]:
    try:
        return typing.get_type_hints(type(obj))
    except Exception:
        return {}


def rgba_from_hex(s: str) -> Gdk.RGBA:
    c = Gdk.RGBA()
    s = s.strip()
    if len(s) == 9:  # #AARRGGBB
        a = int(s[1:3], 16) / 255
        c.parse("#" + s[3:])
        c.alpha = a
    else:
        c.parse(s)
    return c


def hex_from_rgba(c: Gdk.RGBA) -> str:
    a, r, g, b = (round(v * 255) for v in (c.alpha, c.red, c.green, c.blue))
    return f"#{a:02X}{r:02X}{g:02X}{b:02X}"


class Form:
    """Builds rows for one dataclass instance; ``changed()`` fires after every edit."""

    def __init__(self, obj: Any, changed: Callable[[], None] | None = None, *,
                 on_authorize: Callable[[Any, str], None] | None = None,
                 on_button: Callable[[Any, str], None] | None = None, parent: Gtk.Widget | None = None,
                 meta: dict[str, dict[str, Any]] | None = None) -> None:
        self.obj = obj
        # Per-field metadata overrides ({"field": {"label": ..., "kind": ..., "min": ...}}), applied on top of
        # the dataclass field's own metadata; passed down to nested forms and list items.
        self.meta = meta or {}
        self.changed = changed or (lambda: None)
        self.on_authorize = on_authorize
        self.on_button = on_button
        self.parent = parent

    # -- public --

    def group(self, title: str = "", description: str = "", only: list[str] | None = None,
              skip: list[str] | None = None) -> Adw.PreferencesGroup:
        g = Adw.PreferencesGroup(title=title, description=description)
        for row in self.rows(only, skip):
            g.add(row)
        return g

    def rows(self, only: list[str] | None = None, skip: list[str] | None = None) -> list[Gtk.Widget]:
        hints = _hints(self.obj)
        out = []
        for f in dataclasses.fields(self.obj):
            md = self._md(f)
            if md.get("hidden") or md.get("transient"):
                continue
            if only is not None and f.name not in only:
                continue
            if skip and f.name in skip:
                continue
            row = self.row_for(f, hints.get(f.name, type(getattr(self.obj, f.name))))
            if row is not None:
                if isinstance(row, Adw.PreferencesRow):
                    row.set_use_markup(False)  # help texts hold "<port>", "&" ... literally
                out.append(row)
        return out

    # -- rows --

    def _md(self, f: dataclasses.Field) -> dict[str, Any]:
        return {**f.metadata, **self.meta.get(f.name, {})}

    def _sub(self, obj: Any) -> Form:
        return Form(obj, self.changed, on_authorize=self.on_authorize, on_button=self.on_button, parent=self.parent,
                    meta=self.meta)

    def _label(self, f: dataclasses.Field) -> str:
        return self._md(f).get("label") or humanize(f.name)

    def _set(self, name: str, value: Any) -> None:
        setattr(self.obj, name, value)
        self.changed()

    def row_for(self, f: dataclasses.Field, tp: Any) -> Gtk.Widget | None:
        name = f.name
        value = getattr(self.obj, name)
        title = self._label(f)
        md = self._md(f)
        subtitle = md.get("help", "") or ""
        kind = md.get("kind", "") or ""
        origin = typing.get_origin(tp)

        if kind == "oauth" or type(value).__name__ == "OAuth2Info":
            return self._oauth_row(name, value, title, subtitle)
        if kind == "button":
            row = Adw.ActionRow(title=title, subtitle=subtitle)
            b = Gtk.Button(label=title, valign=Gtk.Align.CENTER)
            b.connect("clicked", lambda *_: self.on_button and self.on_button(self.obj, name))
            row.add_suffix(b)
            return row
        if isinstance(value, bool):
            row = Adw.SwitchRow(title=title, subtitle=subtitle, active=value)
            row.connect("notify::active", lambda r, _p: self._set(name, r.get_active()))
            return row
        if isinstance(value, IntFlag):
            return self._flags_row(name, value, title)
        if isinstance(value, Enum):
            return self._enum_row(name, value, title, subtitle, type(value))
        choices = md.get("choices")
        if choices:
            return self._choice_row(name, value, title, subtitle, list(choices))
        if isinstance(value, (int, float)):
            return self._spin_row(f, name, value, title, subtitle)
        if isinstance(value, str):
            if kind == "color" or (COLOR_RE.match(value) and ("color" in name or kind == "color")):
                return self._color_row(name, value, title, subtitle)
            if kind in ("path", "file", "folder"):
                return self._path_row(name, value, title, subtitle, kind)
            if md.get("multiline"):
                return self._multiline_row(name, value, title, subtitle)
            if md.get("secret"):
                row = Adw.PasswordEntryRow(title=title, text=value)
            else:
                row = Adw.EntryRow(title=title, text=value)
            if subtitle:
                row.set_tooltip_text(subtitle)
            row.connect("changed", lambda r: self._set(name, r.get_text()))
            return row
        if dataclasses.is_dataclass(value):
            exp = Adw.ExpanderRow(title=title, subtitle=subtitle)
            for r in self._sub(value).rows():
                exp.add_row(r)
            return exp
        if origin is list:
            args = typing.get_args(tp)
            if args and dataclasses.is_dataclass(args[0]):
                return self._list_row(name, value, title, args[0])
            if args and args[0] is str:
                return self._strlist_row(name, value, title, subtitle)
        if origin is dict and all(isinstance(k, str) and isinstance(v, str) for k, v in (value or {}).items()):
            return self._dict_row(name, value, title)
        return None

    def _spin_row(self, f, name, value, title, subtitle) -> Adw.SpinRow:
        is_float = isinstance(value, float)
        md = self._md(f)
        lo = md.get("min")
        hi = md.get("max")
        step = md.get("step") or (0.1 if is_float else 1)
        lo = -1_000_000 if lo is None else lo
        hi = 1_000_000_000 if hi is None else hi
        adj = Gtk.Adjustment(value=value, lower=lo, upper=hi, step_increment=step, page_increment=step * 10)
        # list-item role: Adw.SpinRow's default "presentation" role hides it from assistive tech
        row = Adw.SpinRow(title=title, subtitle=subtitle, adjustment=adj, digits=2 if is_float else 0,
                          accessible_role=Gtk.AccessibleRole.LIST_ITEM)
        row.connect("notify::value", lambda r, _p: self._set(name, float(r.get_value()) if is_float
                                                             else int(r.get_value())))
        return row

    def _enum_row(self, name, value, title, subtitle, enum_cls) -> Adw.ComboRow:
        members = list(enum_cls)
        model = Gtk.StringList.new([humanize(m.name) if m.name.islower() else m.name for m in members])
        row = Adw.ComboRow(title=title, subtitle=subtitle, model=model, selected=members.index(value))
        row.connect("notify::selected", lambda r, _p: self._set(name, members[r.get_selected()]))
        return row

    def _choice_row(self, name, value, title, subtitle, choices) -> Adw.ComboRow:
        strs = [str(c) for c in choices]
        sel = strs.index(str(value)) if str(value) in strs else 0
        row = Adw.ComboRow(title=title, subtitle=subtitle, model=Gtk.StringList.new(strs), selected=sel)

        def changed(r, _p):
            v = choices[r.get_selected()]
            cur = getattr(self.obj, name)
            if isinstance(cur, int) and not isinstance(cur, bool):
                v = int(v)
            self._set(name, v)
        row.connect("notify::selected", changed)
        return row

    def _flags_row(self, name, value, title) -> Adw.ExpanderRow:
        exp = Adw.ExpanderRow(title=title)
        flag_cls = type(value)
        for m in flag_cls:
            if not m.value:
                continue
            r = Adw.SwitchRow(title=humanize(m.name) if "_" in m.name else re.sub(r"(?<!^)(?=[A-Z])", " ", m.name),
                              active=bool(value & m))

            def toggled(row, _p, m=m):
                cur = getattr(self.obj, name)
                self._set(name, (cur | m) if row.get_active() else (cur & ~m))
            r.connect("notify::active", toggled)
            exp.add_row(r)
        return exp

    def _color_row(self, name, value, title, subtitle) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=True), valign=Gtk.Align.CENTER)
        btn.set_rgba(rgba_from_hex(value))
        btn.connect("notify::rgba", lambda b, _p: self._set(name, hex_from_rgba(b.get_rgba())))
        row.add_suffix(btn)
        row.set_activatable_widget(btn)
        return row

    def _path_row(self, name, value, title, subtitle, kind) -> Adw.EntryRow:
        from .gtkutil import open_files, select_folder
        row = Adw.EntryRow(title=title, text=value)
        row.connect("changed", lambda r: self._set(name, r.get_text()))
        btn = Gtk.Button(icon_name="document-open-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Browse…")
        btn.add_css_class("flat")
        root = lambda: row.get_root() if isinstance(row.get_root(), Gtk.Window) else None  # noqa: E731

        def browse(*_):
            if kind == "folder":
                select_folder(root(), title, lambda p: p and row.set_text(p))
            else:
                open_files(root(), title, lambda ps: ps and row.set_text(ps[0]), patterns=None, multiple=False)
        btn.connect("clicked", browse)
        row.add_suffix(btn)
        return row

    def _multiline_row(self, name, value, title, subtitle) -> Adw.ExpanderRow:
        exp = Adw.ExpanderRow(title=title, subtitle=subtitle)
        view = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=6, bottom_margin=6,
                            left_margin=6, right_margin=6)
        view.get_buffer().set_text(value)

        def changed(buf):
            self._set(name, buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False))
        view.get_buffer().connect("changed", changed)
        sw = Gtk.ScrolledWindow(min_content_height=120, child=view)
        exp.add_row(sw)
        return exp

    def _oauth_row(self, name, info, title, subtitle) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title or "Account", subtitle=_oauth_status(info))
        if self.on_authorize:
            b = Gtk.Button(label="Authorize…", valign=Gtk.Align.CENTER)
            b.add_css_class("suggested-action")

            def go(*_):
                self.on_authorize(self.obj, name)
                row.set_subtitle(_oauth_status(getattr(self.obj, name)))
            b.connect("clicked", go)
            row.add_suffix(b)
        clear = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Sign out")
        clear.add_css_class("flat")

        def sign_out(*_):
            setattr(self.obj, name, type(info)())
            row.set_subtitle(_oauth_status(getattr(self.obj, name)))
            self.changed()
        clear.connect("clicked", sign_out)
        row.add_suffix(clear)
        row._refresh = lambda: row.set_subtitle(_oauth_status(getattr(self.obj, name)))  # type: ignore[attr-defined]
        return row

    def _list_row(self, name, items, title, item_cls) -> Adw.ExpanderRow:
        exp = Adw.ExpanderRow(title=title, subtitle=f"{len(items)} item(s)")
        state = {"rows": []}

        def rebuild():
            for r in state["rows"]:
                exp.remove(r)
            state["rows"] = []
            for i, item in enumerate(items):
                label = getattr(item, "name", "") or getattr(item, "folder_path", "") or \
                    getattr(item, "host", "") or f"#{i + 1}"
                sub = Adw.ExpanderRow(title=str(label))
                for r in self._sub(item).rows():
                    sub.add_row(r)
                rm = Gtk.Button(icon_name="list-remove-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Remove")
                rm.add_css_class("flat")
                rm.connect("clicked", lambda *_a, i=i: (items.pop(i), self.changed(), rebuild()))
                sub.add_suffix(rm)
                exp.add_row(sub)
                state["rows"].append(sub)
            exp.set_subtitle(f"{len(items)} item(s)")

        add = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Add")
        add.add_css_class("flat")
        add.connect("clicked", lambda *_: (items.append(item_cls()), self.changed(), rebuild()))
        exp.add_suffix(add)
        rebuild()
        return exp

    def _strlist_row(self, name, items, title, subtitle) -> Adw.EntryRow:
        row = Adw.EntryRow(title=f"{title} (comma separated)", text=", ".join(items))
        row.connect("changed", lambda r: self._set(name, [s.strip() for s in r.get_text().split(",") if s.strip()]))
        return row

    def _dict_row(self, name, d, title) -> Adw.ExpanderRow:
        exp = Adw.ExpanderRow(title=title, subtitle=f"{len(d)} entr{'y' if len(d) == 1 else 'ies'}")
        view = Gtk.TextView(monospace=True, top_margin=6, bottom_margin=6, left_margin=6, right_margin=6)
        view.get_buffer().set_text("\n".join(f"{k}={v}" for k, v in d.items()))

        def changed(buf):
            text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
            new = {}
            for line in text.splitlines():
                if "=" in line:
                    k, _, v = line.partition("=")
                    if k.strip():
                        new[k.strip()] = v.strip()
            self._set(name, new)
        view.get_buffer().connect("changed", changed)
        exp.add_row(Gtk.ScrolledWindow(min_content_height=100, child=view))
        return exp


def _oauth_status(info) -> str:
    if getattr(info, "access_token", ""):
        import time
        exp = getattr(info, "expires_at", 0)
        if exp and exp < time.time():
            return "Signed in (token expired; it will be refreshed)" if info.refresh_token else "Token expired"
        return "Signed in"
    return "Not signed in"


def page(title: str, icon: str, groups: list[Adw.PreferencesGroup]) -> Adw.PreferencesPage:
    p = Adw.PreferencesPage(title=title, icon_name=icon)
    for g in groups:
        p.add(g)
    return p
