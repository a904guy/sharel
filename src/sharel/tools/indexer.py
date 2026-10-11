"""Directory indexer (ShareX "Index folder" / IndexerLib): HTML, TXT, XML and JSON listings of a folder tree.

The output mirrors ShareX's Indexer{Html,Text,Xml,Json}: same element names, the same default CSS (headings
h1..h6 per depth, ``MainFolderBorder``/``FolderBorder`` boxes, ``FolderInfo``/``FileSize`` spans), the same
size strings (``ToSizeString``: "512 B", "1.50 KB", ... decimal units, or KiB/MiB with ``binary_units``) and the
same rules (folders before files in HTML/XML/JSON, files sorted by name, folders sorted by name, max depth 0 =
unlimited, a folder at ``level == max_depth`` is listed but not descended into). On Linux "hidden" means the
name starts with a dot. ShareL extension: ``collapsible=True`` wraps every folder in ``<details open>`` so a
folder can be folded by clicking its heading; the ShareX classes and look are kept.

API
---
``IndexerSettings``                              all of ShareX's IndexerSettings (snake_case) + ``custom_css``
                                                 (CSS text or a path to a .css file) + ``collapsible``.
``IndexerSettings.from_task_settings(tools)``    build from ``TaskSettingsTools`` (``indexer_*`` fields).
``scan(path, settings, cancel=None, progress=None) -> FolderInfo``  walk the tree (``progress(folders_done, 0)``
                                                 since the total is unknown up front).
``index_folder(path, settings=None, cancel=None, progress=None, now=None) -> IndexResult``
    ``.text`` (the index), ``.suggested_name`` ("Index for <folder>.html"), ``.extension``, ``.root``.
``render(folder_info, settings, now=None) -> str`` render an already-scanned tree.
``size_string(n, binary=False, decimals=2)``    ShareX ``ToSizeString``.
``DEFAULT_CSS``                                  ShareX's IndexerDefault.css.
"""
from __future__ import annotations

import datetime as _dt
import html
import json
import os
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from math import floor, log
from pathlib import Path

from ..core.enums import IndexerOutput
from ._common import ProgressFn, check_cancel

__all__ = ["DEFAULT_CSS", "FileEntry", "FolderInfo", "IndexResult", "IndexerOutput", "IndexerSettings",
           "index_folder", "render", "scan", "size_string"]

EXTENSIONS = {"Html": "html", "Txt": "txt", "Xml": "xml", "Json": "json"}

DEFAULT_CSS = """body {
    font-family: Arial, Helvetica, sans-serif;
    background-color: #F2F2F2;
}

img {
    border-style: none;
}

a:link, a:visited {
    color: #0066FF;
}

ul {
    margin: 0px 0px 8px 0px;
    list-style-type: none;
    padding-left: 10px;
}

li {
    margin-bottom: 2px;
}

h1, h2, h3, h4, h5, h6 {
    border: 1px solid #000000;
    border-top-left-radius: 5px;
    border-top-right-radius: 5px;
    padding: 4px 0px 4px 10px;
    margin: 0px 10px -10px 10px;
    color: #FFFFFF;
    font-family: Arial, Helvetica, sans-serif;
    font-size: large;
    font-weight: normal;
    text-shadow: 0px 1px 1px #000000;
}

h1 {
    background-color: #336699;
    margin: 0px 0px -10px 0px;
    font-size: x-large;
}

h2 {
    background-color: #3D7AAD;
}

h3 {
    background-color: #478FC2;
}

h4 {
    background-color: #52A3D6;
}

h5 {
    background-color: #5CB8EB;
}

h6 {
    background-color: #66CCFF;
}

.MainFolderBorder, .FolderBorder {
    border-style: solid;
    border-width: 0px 1px 1px 1px;
    border-bottom-left-radius: 5px;
    border-bottom-right-radius: 5px;
    padding: 20px 0px 0px 0px;
}

.MainFolderBorder {
    margin: 0px 0px 10px 0px;
}

.FolderBorder {
    margin: 0px 10px 10px 10px;
}

.FolderInfo {
    color: #FFFFFF;
    float: right;
    margin-right: 10px;
    display: block;
}

.FileSize {
    color: #3D7AAD;
}"""

# Added after the (default or custom) CSS when folders are collapsible: hide the disclosure triangle and keep the
# heading boxes looking like ShareX's; a folded folder gets a bottom border so its heading still looks closed.
COLLAPSIBLE_CSS = """
summary {
    list-style: none;
    cursor: pointer;
}

summary::-webkit-details-marker {
    display: none;
}

details:not([open]) > summary > h1, details:not([open]) > summary > h2, details:not([open]) > summary > h3,
details:not([open]) > summary > h4, details:not([open]) > summary > h5, details:not([open]) > summary > h6 {
    margin-bottom: 10px;
    border-radius: 5px;
}"""

_SUFFIX_DEC = ("B", "KB", "MB", "GB", "TB", "PB", "EB")
_SUFFIX_BIN = ("B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB")


def size_string(size: int, binary: bool = False, decimals: int = 2) -> str:
    base = 1024 if binary else 1000
    if size < base:
        return f"{max(size, 0)} B"
    place = min(floor(log(size, base)), len(_SUFFIX_DEC) - 1)
    num = size / base ** place
    if num >= base and place < len(_SUFFIX_DEC) - 1:  # float log rounding at exact powers
        place += 1
        num /= base
    suffix = (_SUFFIX_BIN if binary else _SUFFIX_DEC)[place]
    return f"{num:.{max(0, min(decimals, 3))}f} {suffix}"


@dataclass
class IndexerSettings:
    output: IndexerOutput = IndexerOutput.Html
    skip_hidden_folders: bool = True
    skip_hidden_files: bool = True
    skip_files: bool = False
    max_depth: int = 0  # 0 = unlimited
    show_sizes: bool = True
    add_footer: bool = True
    indentation_text: str = "|___"
    add_empty_line_after_folders: bool = False
    custom_css: str = ""  # CSS text, or a path to a .css file; empty = ShareX's default CSS
    display_path: bool = False
    display_path_limited: bool = False
    use_attribute: bool = True  # XML: name/size as attributes instead of child elements
    parseable_json: bool = True
    binary_units: bool = False
    collapsible: bool = True

    @classmethod
    def from_task_settings(cls, tools) -> IndexerSettings:
        return cls(output=IndexerOutput(tools.indexer_output), skip_hidden_folders=tools.indexer_skip_hidden,
                   skip_hidden_files=tools.indexer_skip_hidden, max_depth=tools.indexer_max_depth,
                   show_sizes=tools.indexer_show_sizes, custom_css=tools.indexer_custom_css)

    def css_text(self) -> str:
        css = self.custom_css.strip()
        if not css:
            return DEFAULT_CSS
        if "{" not in css and len(css) < 4096:
            p = Path(os.path.expanduser(css))
            if p.is_file():
                return p.read_text(encoding="utf-8")
        return css


@dataclass
class FileEntry:
    name: str
    size: int


@dataclass
class FolderInfo:
    path: str
    files: list[FileEntry] = field(default_factory=list)
    folders: list[FolderInfo] = field(default_factory=list)
    size: int = 0
    total_file_count: int = 0
    total_folder_count: int = 0

    @property
    def name(self) -> str:
        return os.path.basename(self.path.rstrip(os.sep)) or self.path

    @property
    def is_empty(self) -> bool:
        return self.total_file_count == 0 and self.total_folder_count == 0

    def update(self) -> None:
        for f in self.folders:
            f.update()
        self.folders.sort(key=lambda f: f.name)
        self.size = sum(f.size for f in self.folders) + sum(f.size for f in self.files)
        self.total_file_count = len(self.files) + sum(f.total_file_count for f in self.folders)
        self.total_folder_count = len(self.folders) + sum(f.total_folder_count for f in self.folders)


@dataclass
class IndexResult:
    text: str
    suggested_name: str
    extension: str
    root: FolderInfo


def _hidden(name: str) -> bool:
    return name.startswith(".")


def scan(path: str | os.PathLike, settings: IndexerSettings | None = None, cancel: threading.Event | None = None,
         progress: ProgressFn | None = None) -> FolderInfo:
    s = settings or IndexerSettings()
    root = os.path.abspath(os.fspath(path))
    if not os.path.isdir(root):
        raise NotADirectoryError(root)
    counter = [0]

    def walk(folder: str, level: int) -> FolderInfo:
        check_cancel(cancel)
        info = FolderInfo(folder)
        if s.max_depth == 0 or level < s.max_depth:
            try:
                entries = list(os.scandir(folder))
            except OSError:
                entries = []
            for e in entries:
                try:
                    if e.is_dir(follow_symlinks=False):
                        if s.skip_hidden_folders and _hidden(e.name):
                            continue
                        info.folders.append(walk(e.path, level + 1))
                    elif not s.skip_files and e.is_file():
                        if s.skip_hidden_files and _hidden(e.name):
                            continue
                        info.files.append(FileEntry(e.name, e.stat().st_size))
                except OSError:
                    continue
            info.files.sort(key=lambda f: f.name)
        counter[0] += 1
        if progress:
            progress(counter[0], 0)
        return info

    info = walk(root, 0)
    info.update()
    return info


# ---- HTML ---------------------------------------------------------------------------------


def _esc(s: str) -> str:
    return html.escape(s, quote=True)


def _count_text(n: int, one: str, many: str) -> str:
    return one if n == 1 else many.format(n)


def _render_html(root: FolderInfo, s: IndexerSettings, now: _dt.datetime) -> str:
    pre_trim = len(os.path.dirname(root.path.rstrip(os.sep))) + 1
    out: list[str] = []

    def folder_row(d: FolderInfo, level: int) -> str:
        info = ""
        if not d.is_empty:
            if s.show_sizes:
                info += size_string(d.size, s.binary_units) + " "
            info += "("
            if d.total_file_count > 0:
                info += _count_text(d.total_file_count, "1 file", "{0} files")
            if d.total_folder_count > 0:
                if d.total_file_count > 0:
                    info += ", "
                info += _count_text(d.total_folder_count, "1 folder", "{0} folders")
            info += ")"
            info = f' <span class="FolderInfo">{_esc(info)}</span>'
        title = (d.path[pre_trim:] if s.display_path_limited else d.path) if s.display_path else d.name
        hn = min(max(level + 1, 1), 6)
        return f"<h{hn}>{_esc(title)}{info}</h{hn}>"

    def file_row(f: FileEntry) -> str:
        row = "<li>" + _esc(f.name)
        if s.show_sizes:
            row += f' <span class="FileSize">{_esc(size_string(f.size, s.binary_units))}</span>'
        return row + "</li>"

    def walk(d: FolderInfo, level: int) -> None:
        if s.collapsible:
            out.append("<details open>")
            out.append("<summary>" + folder_row(d, level) + "</summary>")
        else:
            out.append(folder_row(d, level))
        out.append(f'<div class="{"FolderBorder" if level > 0 else "MainFolderBorder"}">')
        if d.files:
            out.append("<ul>")
            out.extend(file_row(f) for f in d.files)
            out.append("</ul>")
        for sub in d.folders:
            walk(sub, level + 1)
        out.append("</div>")
        if s.collapsible:
            out.append("</details>")

    walk(root, 0)
    css = s.css_text() + (COLLAPSIBLE_CSS if s.collapsible else "")
    parts = ["<!DOCTYPE html>", "<html>", "<head>", '<meta charset="UTF-8">',
             '<meta http-equiv="X-UA-Compatible" content="IE=edge">',
             f"<title>{_esc('Index for ' + root.name)}</title>",
             f'<style type="text/css">\n{css}\n</style>', "</head>", "<body>", "\n".join(out)]
    if s.add_footer:
        parts.append(f"<div>Generated by ShareL Directory Indexer on {now:%Y-%m-%d %H:%M:%S} UTC.</div>")
    parts += ["</body>", "</html>"]
    return "\n".join(parts)


# ---- TXT ----------------------------------------------------------------------------------


def _render_txt(root: FolderInfo, s: IndexerSettings, now: _dt.datetime) -> str:
    lines: list[str] = []

    def walk(d: FolderInfo, level: int) -> None:
        row = s.indentation_text * level + d.name
        if s.show_sizes and d.size > 0:
            row += f" [{size_string(d.size, s.binary_units)}]"
        lines.append(row)
        for sub in d.folders:
            if s.add_empty_line_after_folders:
                lines.append("")
            walk(sub, level + 1)
        if d.files:
            if s.add_empty_line_after_folders:
                lines.append("")
            for f in d.files:
                r = s.indentation_text * (level + 1) + f.name
                if s.show_sizes:
                    r += f" [{size_string(f.size, s.binary_units)}]"
                lines.append(r)

    walk(root, 0)
    text = "\n".join(lines).strip()
    if s.add_footer:
        footer = f"Generated by ShareL Directory Indexer on {now:%Y-%m-%d %H:%M:%S} UTC."
        text += "\n" + "_" * len(footer) + "\n" + footer
    return text


# ---- XML ----------------------------------------------------------------------------------


def _render_xml(root: FolderInfo, s: IndexerSettings) -> str:
    def put(el: ET.Element, key: str, value: str) -> None:
        if s.use_attribute:
            el.set(key, value)
        else:
            ET.SubElement(el, key).text = value

    def folder(d: FolderInfo) -> ET.Element:
        el = ET.Element("Folder")
        put(el, "Name", d.name)
        if s.show_sizes and not d.is_empty:
            put(el, "Size", size_string(d.size, s.binary_units))
        if d.files:
            files = ET.SubElement(el, "Files")
            for f in d.files:
                fe = ET.SubElement(files, "File")
                put(fe, "Name", f.name)
                if s.show_sizes:
                    put(fe, "Size", size_string(f.size, s.binary_units))
        if d.folders:
            subs = ET.SubElement(el, "Folders")
            for sub in d.folders:
                subs.append(folder(sub))
        return el

    tree = ET.ElementTree(folder(root))
    ET.indent(tree, space="  ")
    body = ET.tostring(tree.getroot(), encoding="unicode")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + body


# ---- JSON ---------------------------------------------------------------------------------


def _render_json(root: FolderInfo, s: IndexerSettings) -> str:
    def parseable(d: FolderInfo) -> dict:
        obj: dict = {"Name": d.name}
        if s.show_sizes:
            obj["Size"] = size_string(d.size, s.binary_units)
        if d.folders:
            obj["Folders"] = [parseable(sub) for sub in d.folders]
        if d.files:
            files = []
            for f in d.files:
                fo: dict = {"Name": f.name}
                if s.show_sizes:
                    fo["Size"] = size_string(f.size, s.binary_units)
                files.append(fo)
            obj["Files"] = files
        return obj

    def simple(d: FolderInfo) -> dict:
        return {d.name: [simple(sub) for sub in d.folders] + [f.name for f in d.files]}

    obj = parseable(root) if s.parseable_json else simple(root)
    return json.dumps(obj, indent=2, ensure_ascii=False)


def render(root: FolderInfo, settings: IndexerSettings | None = None, now: _dt.datetime | None = None) -> str:
    s = settings or IndexerSettings()
    now = now or _dt.datetime.now(_dt.UTC)
    out = IndexerOutput(s.output)
    if out == IndexerOutput.Html:
        return _render_html(root, s, now)
    if out == IndexerOutput.Txt:
        return _render_txt(root, s, now)
    if out == IndexerOutput.Xml:
        return _render_xml(root, s)
    return _render_json(root, s)


def index_folder(path: str | os.PathLike, settings: IndexerSettings | None = None,
                 cancel: threading.Event | None = None, progress: ProgressFn | None = None,
                 now: _dt.datetime | None = None) -> IndexResult:
    s = settings or IndexerSettings()
    root = scan(path, s, cancel, progress)
    text = render(root, s, now)
    ext = EXTENSIONS[IndexerOutput(s.output).value]
    return IndexResult(text, f"Index for {root.name}.{ext}", ext, root)
