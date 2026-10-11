"""Hash checker window (ShareX HashCheckerForm).

File mode: file path (+ Browse, drag and drop), "Compare two files" with a second path, the algorithm
(CRC32, MD5, SHA1, SHA256 default, SHA384, SHA512), Check/Stop with a progress bar, the result (upper-case
hex like ShareX) and the target hash: typed by the user ("Target"), or the second file's hash when comparing.
The target box turns green on a match and red on a mismatch (ShareX's 200,255,200 / 255,200,200), and a
label says which. Text mode (ShareL addition, ShareX 18 has it) hashes typed text live.
"""
from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk

from ...core import clipboard
from ...core.enums import HashType
from ...tools import hashcheck as hc
from ..gtkutil import open_files
from ._base import ProgressPanel, ToolWindow, button, dropdown, file_drop_target, labeled, single, text_of, text_view

HASH_TYPES = [t for t in HashType]

_CSS_DONE = False


def _install_css() -> None:
    global _CSS_DONE
    if _CSS_DONE:
        return
    from gi.repository import Gdk
    prov = Gtk.CssProvider()
    prov.load_from_string(".hash-match { background-color: rgb(200,255,200); color: black; }"
                          ".hash-mismatch { background-color: rgb(255,200,200); color: black; }")
    display = Gdk.Display.get_default()
    if display is not None:
        Gtk.StyleContext.add_provider_for_display(display, prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _CSS_DONE = True


class HashCheckWindow(ToolWindow):
    def __init__(self, app, files: list[str] | None = None) -> None:
        super().__init__(app, "Hash checker", 640, 460)
        _install_css()
        self.worker = None

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        switcher = Gtk.StackSwitcher(stack=self.stack, halign=Gtk.Align.CENTER)
        self.header.set_title_widget(switcher)

        # -- file page --
        fpage = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.file1 = Gtk.Entry(hexpand=True, placeholder_text="File path (or drop a file here)",
                               tooltip_text="File path")
        self.file1.connect("changed", lambda *_: self._update_check_button())
        b1 = button("Browse…", lambda: self._browse(self.file1), "Choose the file to hash")
        row1 = labeled("File path:", self.file1, 14)
        row1.append(b1)
        fpage.append(row1)
        self.compare = Gtk.CheckButton(label="Compare two files")
        self.compare.connect("toggled", lambda *_: self._update_compare())
        fpage.append(self.compare)
        self.file2 = Gtk.Entry(hexpand=True, placeholder_text="Second file path", tooltip_text="File path 2")
        self.file2.connect("changed", lambda *_: self._update_check_button())
        self.browse2 = button("Browse…", lambda: self._browse(self.file2), "Choose the second file")
        self.row2 = labeled("File path 2:", self.file2, 14)
        self.row2.append(self.browse2)
        fpage.append(self.row2)
        self.stack.add_titled(fpage, "file", "File")
        file_drop_target(self.file1, lambda ps: self.file1.set_text(ps[0]))
        file_drop_target(self.file2, lambda ps: self.file2.set_text(ps[0]))
        file_drop_target(fpage, self._dropped)

        # -- text page --
        self.text_input, tsw = text_view(height=90)
        self.text_input.set_tooltip_text("Text to hash")
        self.text_input.get_buffer().connect("changed", lambda *_: self._hash_text())
        tpage = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        tpage.append(Gtk.Label(label="Text (UTF-8):", xalign=0))
        tpage.append(tsw)
        self.stack.add_titled(tpage, "text", "Text")
        self.stack.connect("notify::visible-child-name", lambda *_: self._mode_changed())

        # -- common --
        self.hash_type = dropdown([t.value for t in HASH_TYPES], HASH_TYPES.index(HashType.SHA256),
                                  "Hash algorithm")
        self.hash_type.connect("notify::selected", lambda *_: self._type_changed())
        self.check_button = button("Check", self._check_clicked, "Compute the hash", css="suggested-action")
        top = labeled("Hash type:", self.hash_type, 14)
        spacer = Gtk.Box(hexpand=True)
        top.append(spacer)
        top.append(self.check_button)

        self.result = Gtk.Entry(editable=False, hexpand=True, tooltip_text="Result")
        self.result.connect("changed", lambda *_: self._update_result())
        copy = button("Copy", lambda: self._copy(self.result.get_text()), "Copy the result")
        self.result_label = Gtk.Label(label="Result:", xalign=0, width_chars=18)
        rrow = Gtk.Box(spacing=6)
        rrow.append(self.result_label)
        rrow.append(self.result)
        rrow.append(copy)
        self.target = Gtk.Entry(hexpand=True, tooltip_text="Target",
                                placeholder_text="Paste the expected hash to compare")
        self.target.connect("changed", lambda *_: self._update_result())
        self.target_label = Gtk.Label(label="Target:", xalign=0, width_chars=18)
        trow = Gtk.Box(spacing=6)
        trow.append(self.target_label)
        trow.append(self.target)
        trow.append(button("Paste", self._paste_target, "Paste the expected hash from the clipboard"))
        self.match_label = Gtk.Label(label="", xalign=0)
        self.progress = ProgressPanel()

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=12, margin_bottom=12,
                       margin_start=12, margin_end=12)
        body.append(self.stack)
        body.append(top)
        body.append(rrow)
        body.append(trow)
        body.append(self.match_label)
        body.append(self.progress)
        self.set_body(body)

        if files:
            self.file1.set_text(files[0])
            if len(files) > 1:
                self.compare.set_active(True)
                self.file2.set_text(files[1])
        self._update_compare()
        self._update_check_button()

    # -- state --

    @property
    def compare_two(self) -> bool:
        return self.compare.get_active() and self.stack.get_visible_child_name() == "file"

    @property
    def selected_type(self) -> HashType:
        return HASH_TYPES[self.hash_type.get_selected()]

    def _update_compare(self) -> None:
        on = self.compare.get_active()
        self.row2.set_sensitive(on)
        self.result_label.set_label("Result of first file:" if on else "Result:")
        self.target_label.set_label("Result of second file:" if on else "Target:")
        self.target.set_editable(not on)
        self._update_check_button()

    def _update_check_button(self) -> None:
        if self.worker is not None and self.worker.running:
            self.check_button.set_sensitive(True)
            return
        text_mode = self.stack.get_visible_child_name() == "text"
        ok = text_mode or (bool(self.file1.get_text().strip()) and
                           (not self.compare.get_active() or bool(self.file2.get_text().strip())))
        self.check_button.set_sensitive(ok)

    def _update_result(self) -> None:
        r, t = self.result.get_text().strip(), self.target.get_text().strip()
        for c in ("hash-match", "hash-mismatch"):
            self.target.remove_css_class(c)
        if r and t:
            ok = hc.hashes_match(r, t)
            self.target.add_css_class("hash-match" if ok else "hash-mismatch")
            self.match_label.set_label("✔ Hashes match" if ok else "✘ Hashes do not match")
        else:
            self.match_label.set_label("")

    @property
    def matches(self) -> bool | None:
        r, t = self.result.get_text().strip(), self.target.get_text().strip()
        return hc.hashes_match(r, t) if r and t else None

    def _mode_changed(self) -> None:
        self._update_check_button()
        if self.stack.get_visible_child_name() == "text":
            self._hash_text()

    def _type_changed(self) -> None:
        if self.stack.get_visible_child_name() == "text":
            self._hash_text()

    # -- actions --

    def _browse(self, entry: Gtk.Entry) -> None:
        open_files(self, "Choose a file", lambda ps: ps and entry.set_text(ps[0]), patterns=None, multiple=False)

    def _dropped(self, paths: list[str]) -> None:
        self.stack.set_visible_child_name("file")
        self.file1.set_text(paths[0])
        if len(paths) > 1:
            self.compare.set_active(True)
            self.file2.set_text(paths[1])

    def _copy(self, text: str) -> None:
        if text:
            clipboard.set_text(text)
            self.toast("Copied")

    def _paste_target(self) -> None:
        c = clipboard.read()
        if c.kind == "text":
            self.target.set_text(c.text.strip())

    def _hash_text(self) -> None:
        text = text_of(self.text_input)
        self.result.set_text(hc.hash_text(text, self.selected_type).upper() if text else "")

    def _check_clicked(self) -> None:
        if self.worker is not None and self.worker.running:
            self.worker.cancel()
            return
        if self.stack.get_visible_child_name() == "text":
            self._hash_text()
            return
        self.start_check()

    def start_check(self) -> None:
        p1 = self.file1.get_text().strip()
        p2 = self.file2.get_text().strip() if self.compare.get_active() else ""
        for p in (p1, p2):
            if p and not Path(p).is_file():
                self.error("File not found", p)
                return
        htype = self.selected_type
        self.result.set_text("")
        if p2:
            self.target.set_text("")

        def work(progress, cancel):
            if p2:
                cmp = hc.compare_files(p1, p2, htype, progress, cancel)
                return cmp.hash_a, cmp.hash_b
            return hc.hash_file(p1, htype, progress, cancel), None

        def done(res):
            a, b = res
            self.result.set_text(a.upper())
            if b is not None:
                self.target.set_text(b.upper())

        self.check_button.set_label("Stop")
        self.worker = self.run(work, done, panel=self.progress, status="Hashing…",
                               on_finally=self._finished)

    def _finished(self) -> None:
        self.check_button.set_label("Check")
        self._update_check_button()


def open(app, ctx) -> HashCheckWindow:  # noqa: A001 - the tool contract's name
    files = list(getattr(ctx, "files", None) or [])
    win = single("hashcheck", lambda: HashCheckWindow(app, files))
    if files and win.file1.get_text() != files[0]:
        win._dropped(files)
    return win
