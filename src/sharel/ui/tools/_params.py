"""Parameter editors generated from ``sharel.effects.base.param`` metadata (the Image Effects property grid).

``param_rows(obj, fields, changed)`` returns libadwaita rows for the given dataclass fields. The kinds that
:class:`sharel.ui.forms.Form` does not cover are handled here:

* ``point`` / ``size`` / ``padding`` - two or four spin buttons writing an ``int`` list
* ``flags`` - one switch per choice, written as ShareX's ``"Top, Bottom"`` text
* ``font`` - a font button converting to and from ShareX's ``FontConverter`` text
* ``gradient`` - gradient type combo + colour stops (colour, location, remove; Add stop)
* ``text`` - multi-line text view
* ``file`` - path entry with an image chooser

Everything else (bool, int, float, str, ``color``, ``choice``, ``folder``) goes through ``Form.row_for``.
"""
from __future__ import annotations

import dataclasses
import typing
from collections.abc import Callable
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Pango

from ...effects.common import GRADIENT_TYPES, GradientInfo, GradientStop, format_font, parse_font
from .. import gtkutil
from ..forms import Form, hex_from_rgba, humanize, rgba_from_hex

_AXES = {"point": ("X", "Y"), "size": ("Width", "Height"), "padding": ("Left", "Top", "Right", "Bottom")}


def param_rows(obj: Any, fields: list[dataclasses.Field], changed: Callable[[], None]) -> list[Gtk.Widget]:
    try:
        hints = typing.get_type_hints(type(obj))
    except Exception:
        hints = {}
    form = Form(obj, changed)
    rows = []
    for f in fields:
        row = param_row(obj, f, hints.get(f.name, type(getattr(obj, f.name))), changed, form)
        if row is not None:
            row.field_name = f.name  # type: ignore[attr-defined]
            rows.append(row)
    return rows


def _title(f: dataclasses.Field) -> str:
    return (f.metadata.get("label") if f.metadata else "") or humanize(f.name)


def param_row(obj: Any, f: dataclasses.Field, tp: Any, changed: Callable[[], None],
              form: Form | None = None) -> Gtk.Widget | None:
    kind = (f.metadata.get("kind") if f.metadata else "") or ""
    title = _title(f)
    if kind in _AXES:
        return _ints_row(obj, f.name, title, _AXES[kind], changed, f.metadata.get("min"), f.metadata.get("max"))
    if kind == "flags":
        return _flags_row(obj, f.name, title, list(f.metadata.get("choices") or []), changed)
    if kind == "font":
        return _font_row(obj, f.name, title, changed)
    if kind == "gradient":
        return GradientRow(obj, f.name, title, changed)
    if kind == "text":
        return _text_row(obj, f.name, title, changed)
    if kind == "file":
        return _file_row(obj, f.name, title, changed)
    return (form or Form(obj, changed)).row_for(f, tp)


def _ints_row(obj, name, title, axes, changed, lo, hi) -> Adw.ActionRow:
    row = Adw.ActionRow(title=title)
    values = list(getattr(obj, name) or [])
    values += [0] * (len(axes) - len(values))
    spins = []
    for i, axis in enumerate(axes):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        lbl = Gtk.Label(label=axis)
        lbl.add_css_class("caption")
        lbl.add_css_class("dim-label")
        adj = Gtk.Adjustment(value=values[i], lower=-100000 if lo is None else lo, upper=100000 if hi is None else hi,
                             step_increment=1, page_increment=10)
        spin = Gtk.SpinButton(adjustment=adj, numeric=True, width_chars=5, tooltip_text=f"{title} {axis}")
        spin.update_property([Gtk.AccessibleProperty.LABEL], [f"{title} {axis}"])

        def on(s, _i=i):
            cur = list(getattr(obj, name) or [])
            cur += [0] * (len(axes) - len(cur))
            cur[_i] = int(s.get_value())
            setattr(obj, name, cur)
            changed()
        spin.connect("value-changed", on)
        box.append(lbl)
        box.append(spin)
        row.add_suffix(box)
        spins.append(spin)
    row.spins = spins  # type: ignore[attr-defined]
    return row


def _flags_row(obj, name, title, choices, changed) -> Adw.ExpanderRow:
    exp = Adw.ExpanderRow(title=title, subtitle=getattr(obj, name) or "None")
    switches = {}

    def current() -> set[str]:
        return {p.strip() for p in str(getattr(obj, name) or "").split(",") if p.strip()}

    def toggled(_r, _p):
        on = [c for c in choices if switches[c].get_active()]
        setattr(obj, name, ", ".join(on))
        exp.set_subtitle(", ".join(on) or "None")
        changed()
    cur = current()
    for c in choices:
        sw = Adw.SwitchRow(title=c, active=c in cur)
        sw.connect("notify::active", toggled)
        switches[c] = sw
        exp.add_row(sw)
    exp.switches = switches  # type: ignore[attr-defined]
    return exp


def _font_row(obj, name, title, changed) -> Adw.ActionRow:
    row = Adw.ActionRow(title=title, subtitle=str(getattr(obj, name)))
    spec = parse_font(str(getattr(obj, name)))
    desc = Pango.FontDescription.new()
    desc.set_family(spec.family)
    desc.set_size(int(round(spec.size_px * 72 / 96 * Pango.SCALE)))
    desc.set_weight(Pango.Weight.BOLD if spec.bold else Pango.Weight.NORMAL)
    desc.set_style(Pango.Style.ITALIC if spec.italic else Pango.Style.NORMAL)
    btn = Gtk.FontDialogButton(dialog=Gtk.FontDialog(title=title), font_desc=desc, valign=Gtk.Align.CENTER)

    def on(b, _p):
        d = b.get_font_desc()
        if d is None:
            return
        old = parse_font(str(getattr(obj, name)))
        old.family = d.get_family() or old.family
        size = d.get_size() / Pango.SCALE
        if size > 0:
            old.size, old.unit = round(size, 2), "pt"
        old.bold = d.get_weight() >= Pango.Weight.BOLD
        old.italic = d.get_style() != Pango.Style.NORMAL
        text = format_font(old)
        setattr(obj, name, text)
        row.set_subtitle(text)
        changed()
    btn.connect("notify::font-desc", on)
    row.add_suffix(btn)
    row.set_activatable_widget(btn)
    return row


def _text_row(obj, name, title, changed) -> Adw.ExpanderRow:
    exp = Adw.ExpanderRow(title=title, subtitle=str(getattr(obj, name)).split("\n")[0][:60], expanded=True)
    view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=6, bottom_margin=6, left_margin=6,
                        right_margin=6)
    view.get_buffer().set_text(str(getattr(obj, name)))

    def on(buf):
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        setattr(obj, name, text)
        exp.set_subtitle(text.split("\n")[0][:60])
        changed()
    view.get_buffer().connect("changed", on)
    exp.add_row(Gtk.ScrolledWindow(min_content_height=80, child=view))
    exp.text_view = view  # type: ignore[attr-defined]
    return exp


def _file_row(obj, name, title, changed) -> Adw.EntryRow:
    row = Adw.EntryRow(title=title, text=str(getattr(obj, name)))
    row.connect("changed", lambda r: (setattr(obj, name, r.get_text()), changed()))
    btn = Gtk.Button(icon_name="document-open-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Browse…")
    btn.add_css_class("flat")

    def browse(*_):
        root = row.get_root()
        gtkutil.open_files(root if isinstance(root, Gtk.Window) else None, title,
                           lambda ps: ps and row.set_text(ps[0]), patterns=gtkutil.IMAGE_PATTERNS, multiple=False)
    btn.connect("clicked", browse)
    row.add_suffix(btn)
    return row


class GradientRow(Adw.ExpanderRow):
    """Gradient type + colour stops editor for a :class:`GradientInfo` field."""

    def __init__(self, obj, name: str, title: str, changed: Callable[[], None]) -> None:
        super().__init__(title=title)
        self.obj, self.name, self.changed = obj, name, changed
        if not isinstance(getattr(obj, name), GradientInfo):
            setattr(obj, name, GradientInfo())
        g = self.gradient
        types = list(GRADIENT_TYPES)
        if g.type not in types:
            types.append(g.type)
        self.type_row = Adw.ComboRow(title="Gradient type", model=Gtk.StringList.new([humanize(t) for t in types]),
                                     selected=types.index(g.type))
        self.type_row.connect("notify::selected", lambda r, _p: self._set_type(types[r.get_selected()]))
        self.add_row(self.type_row)
        add = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Add stop")
        add.update_property([Gtk.AccessibleProperty.LABEL], ["Add stop"])
        add.add_css_class("flat")
        add.connect("clicked", lambda *_: self.add_stop())
        self.add_suffix(add)
        self._rows: list[Gtk.Widget] = []
        self._rebuild()

    @property
    def gradient(self) -> GradientInfo:
        return getattr(self.obj, self.name)

    def _set_type(self, t: str) -> None:
        self.gradient.type = t
        self._subtitle()
        self.changed()

    def _subtitle(self) -> None:
        g = self.gradient
        self.set_subtitle(f"{humanize(g.type)}, {len(g.colors)} colour stop(s)")

    def add_stop(self, color: str = "", location: float | None = None) -> None:
        stops = self.gradient.colors
        if location is None:
            location = 100.0 if not stops else min(100.0, max(s.location for s in stops))
        stops.append(GradientStop(color or (stops[-1].color if stops else "#FFFFFFFF"), float(location)))
        self._rebuild()
        self.changed()

    def remove_stop(self, index: int) -> None:
        stops = self.gradient.colors
        if 0 <= index < len(stops):
            del stops[index]
            self._rebuild()
            self.changed()

    def _rebuild(self) -> None:
        for r in self._rows:
            self.remove(r)
        self._rows = []
        for i, stop in enumerate(self.gradient.colors):
            row = Adw.ActionRow(title=f"Stop {i + 1}")
            cb = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=True), valign=Gtk.Align.CENTER)
            cb.set_rgba(rgba_from_hex(stop.color))

            def on_color(b, _p, s=stop):
                s.color = hex_from_rgba(b.get_rgba())
                self.changed()
            cb.connect("notify::rgba", on_color)
            adj = Gtk.Adjustment(value=stop.location, lower=0, upper=100, step_increment=1, page_increment=10)
            spin = Gtk.SpinButton(adjustment=adj, digits=1, numeric=True, valign=Gtk.Align.CENTER,
                                  tooltip_text="Location (%)")

            def on_loc(sp, s=stop):
                s.location = float(sp.get_value())
                self.changed()
            spin.connect("value-changed", on_loc)
            rm = Gtk.Button(icon_name="list-remove-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Remove stop")
            rm.add_css_class("flat")
            rm.connect("clicked", lambda *_a, i=i: self.remove_stop(i))
            row.add_suffix(cb)
            row.add_suffix(spin)
            row.add_suffix(rm)
            self.add_row(row)
            self._rows.append(row)
        self._subtitle()
