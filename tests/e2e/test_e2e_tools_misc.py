"""Scenario 15 (video, file and misc tools): each Tools menu entry opens from the main window's Tools
menu (real clicks) and does its primary job, driven through its own UI with real input.

Video converter / trimmer / thumbnailer, animated GIF trimmer, hash checker, directory indexer,
clipboard viewer, colour picker, network monitor, monitor test, and the OCR / QR code entries.
Every check is on an outcome the action produces (files, exact hashes, clipboard content, screen
pixels), and was not true before it. Nothing is uploaded: every destination is a custom uploader
with none defined, and the thumbnailer's own "Upload thumbnails" switch is turned off in the UI."""
from __future__ import annotations

import hashlib
import http.server
import io
import json
import re
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

import pytest
from conftest import found, wait_for
from PIL import Image

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

NOWHERE = {"image_destination": "CustomImageUploader", "image_file_destination": "CustomFileUploader",
           "text_destination": "CustomTextUploader", "text_file_destination": "CustomFileUploader",
           "file_destination": "CustomFileUploader", "url_shortener_destination": "CustomURLShortener"}


@pytest.fixture(scope="module")
def app(session):
    session.seed_config(app={"default_task_settings": {
        "after_capture_job": ["SaveImageToFile"], "after_upload_job": [], **NOWHERE}})
    session.start_app()
    yield session
    session.stop_app()


def probe(path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                          str(path)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def clip_text(s) -> str:
    for mime in ("text/plain;charset=utf-8", "text/plain"):
        data = s.clipboard(mime)
        if data:
            return data.decode("utf-8", errors="replace")
    return ""


def set_clip_text(s, text: str) -> None:
    s.ext("SetClipboard", "text/plain;charset=utf-8", "[" + ", ".join(f"byte 0x{b:02x}" for b in text.encode()) + "]")


class UI:
    """Real-input helpers for one tool window."""

    ACTS = {"click", "key", "type_text", "mouse_drag", "mouse_click", "scroll", "window_close"}

    def __init__(self, s, call) -> None:
        self.s, self._call = s, call
        self.title = ""
        self.win: dict = {}
        self.opened: list[dict] = []

    async def call(self, tool: str, **kw):
        # RootPilot by default gives focus back to the window that had it after each action; here
        # the windows ShareL opens must keep the focus they take (that is part of what is tested).
        if tool in self.ACTS:
            kw.setdefault("restore_focus", False)
        return await self._call(tool, **kw)

    async def open(self, label: str, title: str, *, settle: float = 1.0, focus: bool = True) -> dict:
        """Main window > Tools > ``label``, by clicks; returns the tool's window."""
        s = self.s
        s.sharel("-OpenMainWindow")  # presents (raises) the main window
        s.wait_window("ShareL")
        time.sleep(1.0)  # map animation before clicking into it
        await self.call("click", text="Tools", window="ShareL")
        time.sleep(0.6)
        await self.call("click", text=label, window="ShareL")
        self.win = s.wait_window(title, timeout=20)
        self.title = self.win["title"]
        self.opened.append(self.win)
        # a window opened from the menu comes up in front, focused, without help
        if focus:
            wait_for(lambda: next((w["focus"] for w in s.windows() if w["id"] == self.win["id"]), False),
                     timeout=3, what=f"{title!r} focused after opening it from the menu")
        time.sleep(settle)
        return self.win

    async def item(self, name: str, role: str | None = None, window: str | None = None,
                   scroll: bool = True) -> dict:
        """The element, wheel-scrolled into the window's view first if it is outside it (as a user would)."""
        kw = {"role": role} if role else {}
        for _ in range(12):
            text, data, _i, _e = await self.call("find", name=name, window=window or self.title, limit=20, **kw)
            hits = [it for it in data["data"] if it.get("name") == name]
            # find's role filter is fuzzy (a label shares its entry's name): prefer the exact role
            hits = [it for it in hits if it.get("role") == role] or hits if role else hits
            assert hits, f"{name!r} ({role}) not found in {window or self.title!r}: {text[:600]}"
            it = hits[0]
            if not scroll or window or "bounds" not in it:
                return it
            fx, fy, fw, fh = self.s.ext("GetWindowDetails", f"uint64 {self.win['id']}")["frame"]
            bx, by, bw, bh = it["bounds"]
            if fy + 40 <= by and by + bh <= fy + fh - 8:
                return it
            # over the element's own column, at the window's middle height
            x = min(max(bx + min(bw, 40) / 2, fx + 10), fx + fw - 10)
            await self.call("scroll", dy=3 if by + bh > fy + fh - 8 else -3, x=x, y=fy + fh / 2)
            time.sleep(0.3)
        raise AssertionError(f"could not scroll {name!r} into view")

    async def click(self, name: str, role: str | None = "button", window: str | None = None) -> None:
        await self.call("click", ref=(await self.item(name, role, window))["ref"])

    async def fill(self, name: str, value: str, role: str = "text") -> None:
        it = await self.item(name, role)
        if role == "spin button":
            # into its text, at the left: the middle of a GTK4 spin button is its "-" button
            x, y, _w, h = it["bounds"]
            await self.call("mouse_click", x=x + 12, y=y + h / 2)
        else:
            await self.call("click", ref=it["ref"])
        await self.call("key", combo="ctrl+a", window=self.title)
        await self.call("type_text", text=value, window=self.title)
        await self.call("key", combo="Tab", window=self.title)  # commit (spin buttons, entries)
        time.sleep(0.3)

    async def read(self, name: str, role: str = "text") -> str:
        it = await self.item(name, role)
        _t, data, _i, _e = await self.call("read_text", ref=it["ref"])
        inner = data.get("data", data) if isinstance(data, dict) else {}
        text = inner.get("text", "") if isinstance(inner, dict) else ""
        if data.get("source") == "atspi-name":  # no Text interface: RootPilot gave the name back
            text = it.get("value", "")
        return text

    async def enabled(self, name: str, role: str = "button") -> bool:
        return "disabled" not in (await self.item(name, role)).get("states", [])

    async def shot(self) -> None:
        await self.call("screenshot", window=self.title, max_width=1000)

    async def close(self) -> None:
        for _ in range(4):
            self.s.ext("Activate", f"uint64 {self.win['id']}")
            _t, data, _i, _e = await self.call("window_close", window=self.title)
            res = data.get("data", data) if isinstance(data, dict) else {}
            if not (isinstance(res, dict) and res.get("dialog")):
                break
            time.sleep(1.5)  # RootPilot waits out a transient overlay (a toast) before closing
        self.s.wait_window(self.title, gone=True)

    async def cleanup(self) -> None:
        """Close whatever this test opened and left behind (after a failure), so the next test's
        clicks on the main window aren't covered by it."""
        live = {w["id"]: w for w in self.s.windows()}
        for w in self.opened:
            if w["id"] in live:
                self.s.ext("Activate", f"uint64 {w['id']}")
                await self.call("key", combo="Escape", window=w["title"])
                if w["id"] in {x["id"] for x in self.s.windows()}:
                    await self.call("window_close", window=w["title"])


def make_inputs(d: Path) -> dict[str, Path]:
    d.mkdir(parents=True, exist_ok=True)
    avi = d / "clip.avi"  # MPEG-4 Part 2 in AVI, a key frame every second (10 fps, -g 10)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
                    "-t", "4", "-c:v", "mpeg4", "-g", "10", str(avi)], check=True)
    mp4 = d / "movie.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25",
                    "-t", "6", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(mp4)], check=True)
    gif = d / "anim.gif"  # 20 frames, each its own colour (so none merge)
    frames = [Image.new("RGB", (64, 48), (i * 12, 255 - i * 12, (i * 37) % 256)) for i in range(20)]
    frames[0].save(gif, save_all=True, append_images=frames[1:], duration=100, loop=0)
    return {"avi": avi, "mp4": mp4, "gif": gif}


async def test_s15_video_tools(app, tmp_path):
    s = app
    inp = make_inputs(tmp_path / "media")
    async with s.rootpilot() as client:
        ui = UI(s, s.call(client))
        try:

            # Video converter: MPEG-4/AVI in, H.264 MP4 out
            await ui.open("Video converter", "Video converter")
            assert not await ui.enabled("Encode")
            await ui.fill("Input file path:", str(inp["avi"]))
            await ui.click("Open folder after encoding", "check box")  # no file manager popping up
            out = tmp_path / "media" / "clip-output.mp4"
            assert not out.exists()
            await ui.click("Encode")
            wait_for(lambda: out.exists() and out.stat().st_size > 0, timeout=60, what="converted video")
            time.sleep(1.0)
            await ui.shot()
            info = probe(out)
            v = next(st for st in info["streams"] if st["codec_type"] == "video")
            assert v["codec_name"] == "h264" and (v["width"], v["height"]) == (320, 240), v
            assert 3.5 <= float(info["format"]["duration"]) <= 4.5
            await ui.close()

            # Video trimmer: seconds 1..3 of the 4 s clip
            await ui.open("Video trimmer", "Video trimmer")
            await ui.fill("Input file path:", str(inp["avi"]))
            time.sleep(1.0)  # the duration is probed, which sets the end position
            await ui.fill("Start time (seconds)", "1", role="spin button")
            await ui.fill("End time (seconds)", "3", role="spin button")
            trimmed = tmp_path / "media" / "clip-trimmed.avi"
            assert not trimmed.exists()
            await ui.click("Trim")
            wait_for(lambda: trimmed.exists() and trimmed.stat().st_size > 0, timeout=60, what="trimmed video")
            time.sleep(1.0)
            await ui.shot()
            dur = float(probe(trimmed)["format"]["duration"])
            assert 1.6 <= dur <= 2.4, f"trimmed to {dur:.2f} s, expected about 2 s"
            await ui.close()

            # Video thumbnailer: the combined contact sheet next to the video, uploads off
            await ui.open("Video thumbnailer", "Video thumbnailer")
            await ui.fill("Media path:", str(inp["mp4"]))
            upload = await ui.item("Upload thumbnails", "switch")
            if "checked" in upload.get("states", []):
                await ui.click("Upload thumbnails", "switch")
                time.sleep(0.3)
            assert "checked" not in (await ui.item("Upload thumbnails", "switch")).get("states", [])
            sheet = tmp_path / "media" / "movie_Thumbnail.png"
            assert not sheet.exists()
            await ui.click("Start")
            wait_for(lambda: sheet.exists() and sheet.stat().st_size > 0, timeout=90, what="thumbnail sheet")
            time.sleep(1.5)
            await ui.shot()
            with Image.open(sheet) as im:
                assert im.format == "PNG" and im.width >= 256 and im.height >= 144, (im.format, im.size)
            await ui.close()

            # Animated GIF trimmer: frames 5..14 (inclusive) of 20
            await ui.open("Animated GIF trimmer", "Animated GIF trimmer")
            await ui.fill("Input GIF:", str(inp["gif"]))
            time.sleep(0.8)
            await ui.fill("Start frame", "5", role="spin button")
            await ui.fill("End frame", "14", role="spin button")
            out_gif = tmp_path / "media" / "anim-trimmed.gif"
            assert not out_gif.exists()
            await ui.click("Trim")
            wait_for(lambda: out_gif.exists() and out_gif.stat().st_size > 0, timeout=30, what="trimmed GIF")
            await ui.shot()
            with Image.open(out_gif) as g, Image.open(inp["gif"]) as src:
                assert g.n_frames == 10, g.n_frames
                src.seek(5)
                want = src.convert("RGB").getpixel((10, 10))
                assert g.convert("RGB").getpixel((10, 10)) == want  # starts at the original frame 5
            await ui.close()
        finally:
            await ui.cleanup()


async def test_s15_hash_and_indexer(app, tmp_path):
    s = app
    data = tmp_path / "data.bin"
    data.write_bytes(bytes(range(256)) * 4096 + b"ShareL")
    want = hashlib.sha256(data.read_bytes()).hexdigest().upper()
    tree = tmp_path / "tree"
    for rel in ("alpha/one.txt", "alpha/beta/two.txt", "gamma/three.log", "four.md"):
        (tree / rel).parent.mkdir(parents=True, exist_ok=True)
        (tree / rel).write_text(rel)
    async with s.rootpilot() as client:
        ui = UI(s, s.call(client))
        try:

            await ui.open("Hash checker", "Hash checker")
            assert await ui.read("Result") == ""
            await ui.fill("File path:", str(data))
            await ui.click("Check")
            got = ""
            for _ in range(40):
                got = await ui.read("Result")
                if got:
                    break
                time.sleep(0.25)
            await ui.shot()
            assert got == want, (got, want)
            await ui.close()

            # Directory indexer asks for the folder first (ShareX's IndexFolder): type it in the chooser
            await ui.open("Directory indexer", "Index folder", settle=1.0)
            chooser = ui.title
            await ui.call("key", combo="ctrl+l", window=chooser)
            time.sleep(0.3)
            await ui.call("type_text", text=str(tree) + "/", window=chooser)
            time.sleep(0.5)
            await ui.call("key", combo="Return", window=chooser)  # goes into the folder
            time.sleep(1.0)
            await ui.click("Select", window=chooser)  # ...and chooses it
            s.wait_window(chooser, gone=True)
            ui.win = s.wait_window("Directory indexer")
            ui.title = ui.win["title"]
            ui.opened.append(ui.win)
            time.sleep(0.5)
            await ui.shot()
            folder = await ui.read("Folder path:")
            assert str(tree) in folder, (folder, await ui.item("Folder path:", "text"))
            await ui.click("Index folder")
            for _ in range(40):
                if await ui.enabled("Copy"):
                    break
                time.sleep(0.25)
            await ui.click("Preview", "page tab")
            time.sleep(0.5)
            await ui.shot()
            before = clip_text(s)
            assert "three.log" not in before
            await ui.click("Copy")
            html = wait_for(lambda: clip_text(s) if "three.log" in clip_text(s) else "", what="index on the clipboard")
            for name in ("alpha", "beta", "gamma", "one.txt", "two.txt", "three.log", "four.md"):
                assert name in html, name
            assert "<html" in html.lower()  # the default output is HTML
            await ui.close()
        finally:
            await ui.cleanup()


async def test_s15_clipboard_viewer_and_color_picker(app):
    s = app
    probe_text = "ShareL clipboard probe 4711"
    async with s.rootpilot() as client:
        ui = UI(s, s.call(client))
        try:
            set_clip_text(s, probe_text)
            wait_for(lambda: clip_text(s) == probe_text, what="probe text on the clipboard")

            await ui.open("Clipboard viewer", "Clipboard viewer")
            heading = f"Clipboard content: Text (Length: {len(probe_text)})"
            time.sleep(1.0)  # the viewer reads the clipboard on a worker
            await ui.shot()
            _t, d, _i, _e = await ui.call("find", name=heading, window=ui.title, limit=3)
            assert found(d, heading), (await ui.call("snapshot", window=ui.title))[0][:1500]
            assert await ui.read("Clipboard text") == probe_text
            await ui.click("Formats", "page tab")
            time.sleep(0.8)
            _t, d, _i, _e = await ui.call("find", name="text/plain", window=ui.title, limit=10)
            assert any((it.get("name") or "").startswith("text/plain") for it in d["data"]), _t[:500]
            await ui.shot()
            await ui.close()

            # Colour picker: choose a standard swatch, copy it as hex
            from sharel.tools.color import Color, format_color
            await ui.open("Color picker", "Color picker")
            await ui.click("#FF6A00FF", "table cell")
            time.sleep(0.3)
            await ui.click("Copy", None)
            time.sleep(0.5)
            await ui.call("click", text="Hexadecimal")
            want = format_color(Color(255, 106, 0), "Hex")
            got = wait_for(lambda: clip_text(s) if clip_text(s) == want else "", what="hex colour on the clipboard")
            assert got != probe_text and got == want
            await ui.shot()
            await ui.close()
        finally:
            await ui.cleanup()


class _Blob(http.server.BaseHTTPRequestHandler):
    SIZE = 200 * 1024 * 1024

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(self.SIZE))
        self.end_headers()
        chunk = b"\0" * (1 << 20)
        for _ in range(self.SIZE >> 20):
            self.wfile.write(chunk)

    def log_message(self, *_a):
        pass


def _bytes(text: str) -> float:
    m = re.match(r"([\d.,]+)\s*([KMGT]?)(i?)B", text)
    assert m, text
    num = float(m.group(1).replace(",", ""))
    base = 1024 if m.group(3) else 1000
    return num * base ** "_KMGT".index(m.group(2) or "_")


async def _download_peak(ui) -> float:
    """The Download row's Peak (bytes/s); the columns are Current, Peak, Average, Total, Packets."""
    text, *_ = await ui.call("snapshot", window=ui.title, max_nodes=200)
    lines = text.splitlines()
    i = next(n for n, ln in enumerate(lines) if "■ Download" in ln)
    vals = [re.search(r'label "([^"]*)"', ln).group(1) for ln in lines[i + 1:i + 6]]
    return _bytes(vals[1].removesuffix("/s"))


async def test_s15_network_monitor_and_monitor_test(app):
    s = app
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Blob)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        async with s.rootpilot() as client:
            ui = UI(s, s.call(client))
            try:
                await ui.open("Network monitor", "Network monitor")
                await ui.click("Include loopback", "check box")
                assert "checked" in (await ui.item("Include loopback", "check box")).get("states", [])
                time.sleep(2.5)
                before = await _download_peak(ui)
                assert before < 50e6, f"peak {before:.0f} B/s before any loopback transfer"
                with urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/blob") as r:
                    while r.read(1 << 20):
                        pass
                time.sleep(2.5)
                after = await _download_peak(ui)
                await ui.shot()
                # 200 MB on loopback within a sample or two: far above the background traffic
                assert after >= 50e6, f"download peak {before:.0f} -> {after:.0f} B/s after 200 MB on loopback"
                await ui.close()

                # Monitor test: fullscreen on the monitor, arrow keys change the test, Esc leaves
                mon = s.ext("GetMonitors")[0]
                await ui.open("Monitor test", "Monitor test", settle=1.5)
                frame = s.ext("GetWindowDetails", f"uint64 {ui.win['id']}")["frame"]
                assert list(frame) == [mon["x"], mon["y"], mon["width"], mon["height"]], (frame, mon)

                def grab(name):
                    p = s.artifacts / name
                    s.ext("Screenshot", "monitor", "uint64 0", "0", "0", "0", "0", "false", str(p))
                    with Image.open(p) as im:
                        return im.convert("RGB").resize((64, 36))
                first = grab("monitortest-1.png")
                await ui.call("key", combo="Right", window=ui.title)
                time.sleep(0.8)
                second = grab("monitortest-2.png")
                diff = sum(abs(a - b) for pa, pb in zip(first.getdata(), second.getdata(), strict=True)
                           for a, b in zip(pa, pb, strict=True))
                assert diff > 64 * 36 * 10, "the test pattern did not change on the arrow key"
                await ui.call("key", combo="Escape", window=ui.title)
                s.wait_window(ui.title, gone=True)
            finally:
                await ui.cleanup()
    finally:
        srv.shutdown()


async def test_s15_ocr_and_qr_from_menu(app):
    s = app
    async with s.rootpilot() as client:
        ui = UI(s, s.call(client))
        try:
            # OCR: region over the main window's sidebar labels
            s.sharel("-OpenMainWindow")
            main = s.wait_window("ShareL")
            set_clip_text(s, "nothing yet")
            # (the region overlay's own focus is tested in test_e2e_capture's overlay test)
            await ui.open("OCR", "region capture", settle=0.6, focus=False)
            overlays = [o for o in s.windows() if "region capture" in o["title"]]
            s.ext("Activate", f"uint64 {next(o for o in overlays if o['monitor'] == 0)['id']}")
            time.sleep(0.6)
            x, y = main["frame"][:2]
            await ui.call("mouse_drag", from_x=x + 8, from_y=y + 50, to_x=x + 200, to_y=y + 200)
            ui.win = s.wait_window("OCR", timeout=30)
            ui.title = ui.win["title"]
            ui.opened.append(ui.win)
            time.sleep(3.0)
            await ui.click("Copy all")
            text = wait_for(lambda: clip_text(s) if "Workflows" in clip_text(s) else "", what="OCR of the sidebar")
            assert "Capture" in text and "Upload" in text, text
            await ui.shot()
            await ui.close()

            # QR code: encode typed text, copy the image, decode it
            qr_text = "https://example.invalid/sharel-s15"
            await ui.open("QR code", "QR code")
            # (it starts out encoding the clipboard's text, like ShareX's QR form: replace that)
            assert clip_text(s) != qr_text
            await ui.call("click", ref=(await ui.item("Text to encode", "text"))["ref"])
            await ui.call("key", combo="ctrl+a", window=ui.title)
            await ui.call("type_text", text=qr_text, window=ui.title)
            time.sleep(1.5)
            await ui.click("Copy image")
            png = wait_for(lambda: s.clipboard("image/png"), what="QR image on the clipboard")
            p = s.artifacts / "qr-from-menu.png"
            Image.open(io.BytesIO(png)).save(p)
            dec = subprocess.run(["zbarimg", "-q", "--raw", str(p)], capture_output=True, text=True)
            assert dec.stdout.strip() == qr_text, dec
            await ui.shot()
            await ui.close()
        finally:
            await ui.cleanup()
