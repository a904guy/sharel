"""Every icon the UI names exists: in the icon theme GTK uses (Adwaita and hicolor) or in ShareL's own icons
(data/icons/actions, which the app adds to the theme at startup). A name that doesn't resolve renders as the red
"missing image" placeholder, as the scrolling capture window's view switcher did."""
from __future__ import annotations

import ast
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, Gtk

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "src" / "sharel" / "ui"
NAME_FIRST = {"set_icon_name", "new_from_icon_name", "set_from_icon_name"}


def _named_icons() -> dict[str, list[str]]:
    """Icon names written in src/sharel/ui -> where: icon_name= keywords, set_icon_name()-style calls, the icon of
    add_titled_with_icon, any string ending in -symbolic, and the values of annotate.ICONS (editor toolbar)."""
    found: dict[str, list[str]] = {}

    def add(name: str, where: str) -> None:
        found.setdefault(name, []).append(where)

    for path in sorted(UI.rglob("*.py")):
        rel = path.relative_to(ROOT)
        for node in ast.walk(ast.parse(path.read_text(), str(path))):
            where = f"{rel}:{getattr(node, 'lineno', 0)}"
            if isinstance(node, ast.keyword) and node.arg == "icon_name" and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str) and node.value.value:
                add(node.value.value, where)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                args = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
                if node.func.attr in NAME_FIRST and node.args and isinstance(node.args[0], ast.Constant) \
                        and isinstance(node.args[0].value, str):
                    add(node.args[0].value, where)
                elif node.func.attr == "add_titled_with_icon" and len(node.args) == 4 and args:
                    add(args[-1], where)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.endswith("-symbolic") \
                    and " " not in node.value:
                add(node.value, where)
    from sharel.ui.annotate import ICONS
    for v in ICONS.values():
        add(v, "src/sharel/ui/annotate.py:ICONS")
    return found


def _theme() -> Gtk.IconTheme:
    Gtk.init()
    theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
    theme.add_search_path(str(ROOT / "data" / "icons" / "actions"))  # what App._add_icon_path adds
    return theme


def test_every_icon_name_in_the_ui_resolves():
    names = _named_icons()
    # the scan reads every source: view switcher pages, keyword icons and the editor toolbar mapping
    assert {"preferences-system-symbolic", "image-x-generic-symbolic"} <= set(names)  # scrolling capture tabs
    assert "view-grid-symbolic" in names  # the main window's thumbnail toggle (icon_name=)
    assert "sharel-draw-arrow-symbolic" in names and "edit-undo-symbolic" in names  # annotate.ICONS
    theme = _theme()
    missing = {n: w[:3] for n, w in names.items() if not theme.has_icon(n)}
    assert not missing, missing


def test_view_switcher_pages_have_icons():
    """Adw.ViewSwitcher shows each page's icon; a page added with add_titled() (no icon) shows the placeholder.
    A file that shows a ViewStack in an Adw.ViewSwitcher must add its pages with add_titled_with_icon."""
    offenders = []
    for path in sorted(UI.rglob("*.py")):
        text = path.read_text()
        if "Adw.ViewSwitcher(" not in text:
            continue
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_titled":
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not offenders, offenders


def test_the_editor_draws_arrow_and_line_with_shareL_icons():
    """Adwaita has no drawing-arrow icon: the arrow tool used go-next (a bare chevron) and the line tool the
    strikethrough icon. ShareL ships its own, and the app adds their directory to the icon theme."""
    from sharel.ui.annotate import ICONS
    assert ICONS["layer-shape-arrow"] == "sharel-draw-arrow-symbolic"
    assert ICONS["layer-shape-line"] == "sharel-draw-line-symbolic"
    for name in ("sharel-draw-arrow-symbolic", "sharel-draw-line-symbolic"):
        assert (ROOT / "data" / "icons" / "actions" / f"{name}.svg").is_file()
