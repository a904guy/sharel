#!/usr/bin/env python3
"""Regenerate the README screenshots: docs/screenshots/<scene>.png.

Everything happens in the isolated headless GNOME Shell of the end-to-end tests (tests/e2e/shell.sh,
1920x1080 primary monitor), driven through RootPilot like the e2e tests. The real desktop is never
touched. ShareL runs from this checkout with throwaway XDG dirs (a fresh temp dir), in-memory
GSettings, hotkeys off and no keyring, and only sees made-up demo content:

- demo images drawn here (a chart, a code snippet, a landscape, a web page mock-up, a palette);
- a local HTTP "image host" on 127.0.0.1 that answers every upload with an example.com URL. Nothing
  leaves the machine;
- a sample custom uploader (.sxcu) for example.com, which is shown but never used.

    uv run python scripts/screenshots.py                    # every scene
    uv run python scripts/screenshots.py --only editor,qr   # some of them
    uv run python scripts/screenshots.py --list
    uv run python scripts/screenshots.py --hold             # set up, then wait (explore with RootPilot)

Run one headless shell at a time (the e2e tests start their own): --lock FILE takes an exclusive
flock on FILE for the whole run.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
import http.server
import io
import json
import math
import os
import random
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
E2E = ROOT / "tests" / "e2e"
OUT = ROOT / "docs" / "screenshots"
sys.path[:0] = [str(E2E), str(ROOT / "src")]

import conftest  # the e2e harness: Session, shell.sh, the RootPilot client
from conftest import Session, real_env, wait_for

SHARE = Path("/usr/share/fonts/truetype")
FONT = SHARE / "ubuntu" / "Ubuntu[wdth,wght].ttf"
FONT_BOLD = SHARE / "dejavu" / "DejaVuSans-Bold.ttf"
MONO = SHARE / "dejavu" / "DejaVuSansMono.ttf"
SCREEN_W, SCREEN_H = 1920, 1080
MAX_BYTES = 400_000
QUANTIZE_ABOVE = 150_000


def font(size: int, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    path = MONO if mono else FONT_BOLD if bold else FONT
    if not path.exists():
        path = SHARE / "dejavu" / ("DejaVuSansMono.ttf" if mono else "DejaVuSans-Bold.ttf" if bold
                                   else "DejaVuSans.ttf")
    return ImageFont.truetype(str(path), size)


def say(*a) -> None:
    print(*a, flush=True)


# ---- demo content ---------------------------------------------------------------------------------------

def vgradient(w: int, h: int, top: tuple, bottom: tuple) -> Image.Image:
    col = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(1, h - 1)
        col.putpixel((0, y), tuple(int(a + (b - a) * t) for a, b in zip(top, bottom, strict=True)))
    return col.resize((w, h))


def wallpaper(w: int = SCREEN_W, h: int = SCREEN_H) -> Image.Image:
    """A calm blue-grey gradient with two soft shapes (compresses well, nothing to read)."""
    im = vgradient(w, h, (62, 84, 120), (28, 36, 58))
    glow = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(glow)
    d.ellipse((w * 0.55, -h * 0.35, w * 1.25, h * 0.75), fill=70)
    d.ellipse((-w * 0.2, h * 0.55, w * 0.45, h * 1.4), fill=45)
    glow = glow.filter(ImageFilter.GaussianBlur(120))
    return Image.composite(Image.new("RGB", (w, h), (120, 150, 200)), im, glow)


def chart() -> Image.Image:
    im = Image.new("RGB", (900, 560), (250, 250, 252))
    d = ImageDraw.Draw(im)
    d.text((40, 30), "Weekly active users", font=font(34, bold=True), fill=(36, 41, 51))
    d.text((40, 78), "Demo data · last 12 weeks", font=font(20), fill=(110, 118, 130))
    vals = [32, 38, 35, 44, 51, 49, 58, 62, 60, 71, 77, 84]
    x0, y0, bw, gap, top = 70, 500, 50, 20, 140
    for i in range(0, 101, 25):
        y = y0 - (y0 - top) * i / 100
        d.line((60, y, 860, y), fill=(226, 229, 234), width=1)
        d.text((20, y - 10), str(i), font=font(16), fill=(140, 146, 156))
    for i, v in enumerate(vals):
        x = x0 + i * (bw + gap)
        y = y0 - (y0 - top) * v / 100
        d.rounded_rectangle((x, y, x + bw, y0), radius=8, fill=(53, 132, 228) if i < 11 else (230, 97, 0))
    return im


CODE = [
    ("def ", "upload", "(path: Path, host: Uploader) -> str:"),
    ("    ", "", '"""Send one file and return its public URL."""'),
    ("    ", "data", " = path.read_bytes()"),
    ("    ", "result", " = host.upload(data, name=path.name)"),
    ("    if ", "result.is_error", ":"),
    ("        raise ", "UploadError", "(result.errors)"),
    ("    return ", "result.url", ""),
]


def code() -> Image.Image:
    im = Image.new("RGB", (900, 400), (30, 32, 40))
    d = ImageDraw.Draw(im)
    for i, c in enumerate(((237, 106, 94), (245, 191, 79), (98, 197, 84))):
        d.ellipse((24 + i * 26, 22, 40 + i * 26, 38), fill=c)
    d.text((110, 18), "uploader.py", font=font(18, mono=True), fill=(150, 156, 170))
    f = font(22, mono=True)
    for n, (kw, name, rest) in enumerate(CODE):
        y = 80 + n * 40
        d.text((24, y), f"{n + 1:2d}", font=f, fill=(90, 96, 110))
        x = 80
        for part, colour in ((kw, (199, 146, 234)), (name, (130, 170, 255)), (rest, (220, 223, 228))):
            d.text((x, y), part, font=f, fill=colour)
            x += d.textlength(part, font=f)
    return im


def landscape() -> Image.Image:
    w, h = 960, 600
    im = vgradient(w, h, (255, 183, 120), (120, 160, 220))
    d = ImageDraw.Draw(im)
    d.ellipse((620, 120, 760, 260), fill=(255, 236, 190))
    rnd = random.Random(7)
    for layer, (base, colour) in enumerate(((330, (92, 110, 150)), (400, (64, 86, 120)), (470, (40, 58, 86)))):
        pts = [(0, h)]
        for x in range(0, w + 40, 40):
            pts.append((x, base + 60 * math.sin(x / (140 + layer * 40) + layer) + rnd.randint(-12, 12)))
        pts.append((w, h))
        d.polygon(pts, fill=colour)
    return im


LUMEN_PURPLE = (98, 70, 234)


def mockup() -> Image.Image:
    """A made-up web page ("Lumen Notes") to annotate in the editor."""
    w, h = 1000, 640
    im = Image.new("RGB", (w, h), (246, 247, 249))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, w, 64), fill=(255, 255, 255))
    d.line((0, 64, w, 64), fill=(225, 228, 233))
    d.rounded_rectangle((28, 16, 60, 48), radius=8, fill=(98, 70, 234))
    d.text((74, 18), "Lumen Notes", font=font(24, bold=True), fill=(36, 41, 51))
    for i, label in enumerate(("Notes", "Shared", "Archive")):
        d.text((560 + i * 110, 22), label, font=font(19), fill=(90, 96, 110))
    d.rounded_rectangle((890, 14, 976, 50), radius=18, fill=(98, 70, 234))
    d.text((905, 21), "Sign in", font=font(18), fill=(255, 255, 255))
    d.text((60, 120), "Write it down,", font=font(46, bold=True), fill=(36, 41, 51))
    d.text((60, 178), "find it later.", font=font(46, bold=True), fill=(98, 70, 234))
    d.text((60, 254), "Quick notes with tags, search and sync.", font=font(22), fill=(100, 106, 118))
    d.rounded_rectangle((60, 310, 320, 366), radius=12, fill=(98, 70, 234))
    d.text((92, 324), "Create a free account", font=font(20), fill=(255, 255, 255))
    d.text((60, 400), "Email: demo@example.com", font=font(20), fill=(90, 96, 110))
    d.text((60, 432), "Order #A-1042 · Card •••• 4242", font=font(20), fill=(90, 96, 110))
    card = (560, 120, 940, 560)
    d.rounded_rectangle(card, radius=16, fill=(255, 255, 255), outline=(225, 228, 233))
    d.text((590, 146), "Today", font=font(24, bold=True), fill=(36, 41, 51))
    for i, (t, c) in enumerate((("Release notes draft", (230, 97, 0)), ("Team retro: actions", (53, 132, 228)),
                                ("Groceries", (46, 194, 126)), ("Book ideas", (192, 28, 40)))):
        y = 200 + i * 84
        d.rounded_rectangle((590, y, 910, y + 64), radius=10, fill=(248, 248, 251))
        d.ellipse((606, y + 24, 622, y + 40), fill=c)
        d.text((638, y + 19), t, font=font(20), fill=(60, 66, 78))
    return im


def palette() -> Image.Image:
    cols = [(28, 113, 216), (38, 162, 105), (229, 165, 10), (198, 70, 0), (165, 29, 45), (97, 53, 131),
            (99, 69, 44), (61, 56, 70)]
    im = Image.new("RGB", (880, 300), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.text((30, 24), "Palette · Autumn", font=font(28, bold=True), fill=(36, 41, 51))
    for i, c in enumerate(cols):
        x = 30 + i * 105
        d.rounded_rectangle((x, 90, x + 90, 210), radius=12, fill=c)
        d.text((x + 4, 222), "#{:02X}{:02X}{:02X}".format(*c), font=font(17, mono=True), fill=(90, 96, 110))
    return im


def kanban() -> Image.Image:
    im = Image.new("RGB", (960, 600), (240, 242, 246))
    d = ImageDraw.Draw(im)
    d.text((32, 24), "Sprint board", font=font(30, bold=True), fill=(36, 41, 51))
    cols = (("To do", ["Tray icon tooltip", "GIF frame limit", "Docs: hotkeys"]),
            ("In progress", ["Scrolling capture", "OCR languages"]),
            ("Done", ["Region capture", "Custom uploaders", "Image editor", "History search"]))
    for i, (title, cards) in enumerate(cols):
        x = 32 + i * 308
        d.rounded_rectangle((x, 84, x + 288, 576), radius=14, fill=(226, 230, 237))
        d.text((x + 16, 98), title, font=font(20, bold=True), fill=(70, 76, 88))
        for j, card in enumerate(cards):
            y = 140 + j * 96
            d.rounded_rectangle((x + 12, y, x + 276, y + 80), radius=10, fill=(255, 255, 255))
            d.rectangle((x + 12, y + 10, x + 17, y + 70), fill=((53, 132, 228), (230, 97, 0), (46, 194, 126))[i])
            d.text((x + 30, y + 26), card, font=font(19), fill=(60, 66, 78))
    return im


def banner() -> Image.Image:
    w, h = 960, 480
    im = vgradient(w, h, (98, 70, 234), (28, 113, 216))
    d = ImageDraw.Draw(im)
    for i in range(6):
        d.ellipse((620 + i * 30 - 160, -120 + i * 70, 620 + i * 30 + 160, 200 + i * 70), outline=(255, 255, 255),
                  width=2)
    d.text((60, 150), "Version 2.0", font=font(64, bold=True), fill=(255, 255, 255))
    d.text((64, 240), "Faster captures, a new editor and dark mode", font=font(26), fill=(225, 230, 255))
    d.rounded_rectangle((64, 310, 300, 368), radius=29, fill=(255, 255, 255))
    d.text((96, 324), "What's new", font=font(22, bold=True), fill=(98, 70, 234))
    return im


def terminal() -> Image.Image:
    im = Image.new("RGB", (900, 420), (23, 25, 31))
    d = ImageDraw.Draw(im)
    f = font(20, mono=True)
    lines = [("$ ", "make test", (220, 223, 228)), ("", "collected 214 items", (150, 156, 170)),
             ("", "tests/test_capture.py ........................  [ 11%]", (150, 156, 170)),
             ("", "tests/test_editor.py ..........................  [ 24%]", (150, 156, 170)),
             ("", "tests/test_upload.py ..........................  [ 37%]", (150, 156, 170)),
             ("", "tests/test_tools.py ..................................  [100%]", (150, 156, 170)),
             ("", "214 passed in 12.31s", (98, 197, 84)), ("$ ", "", (220, 223, 228))]
    for i, (prompt, text, colour) in enumerate(lines):
        y = 28 + i * 44
        d.text((28, y), prompt, font=f, fill=(98, 197, 84))
        d.text((28 + d.textlength(prompt, font=f), y), text, font=f, fill=colour)
    return im


DEMO_IMAGES = {"weekly-users.png": chart, "uploader-snippet.png": code, "sunset.png": landscape,
               "lumen-notes.png": mockup, "autumn-palette.png": palette, "sprint-board.png": kanban,
               "release-banner.png": banner, "test-run.png": terminal}

DOC = """Release checklist

Capture a region, a window or the whole screen
Annotate with arrows, text and step numbers
Blur anything private before sharing
Upload and copy the link automatically
Pin, compare and combine images
Record the screen as a video or a GIF
"""

SCROLL_DOC = "\n".join(f"Line {i:03d}: the quick brown fox jumps over the lazy dog" for i in range(1, 81)) + "\n"

SAMPLE_SXCU = {
    "Version": "18.0.0",
    "Name": "Example image host",
    "DestinationType": "ImageUploader, FileUploader",
    "RequestMethod": "POST",
    "RequestURL": "https://upload.example.com/api/v1/images",
    "Headers": {"Authorization": "Bearer {input:API token}"},
    "Body": "MultipartFormData",
    "Arguments": {"album": "screenshots", "expires": "30d"},
    "FileFormName": "file",
    "URL": "{json:data.link}",
    "ThumbnailURL": "{json:data.thumb}",
    "DeletionURL": "{json:data.delete_url}",
    "ErrorMessage": "{json:error.message}",
}


class DemoHost(http.server.ThreadingHTTPServer):
    """A local stand-in for an image host: every multipart POST gets an example.com link back."""

    def __init__(self) -> None:
        self.n = 0
        self.rnd = random.Random(2024)
        super().__init__(("127.0.0.1", 0), _HostHandler)
        self.url = f"http://127.0.0.1:{self.server_address[1]}"

    def next_link(self) -> dict:
        self.n += 1
        slug = "".join(self.rnd.choice("abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(7))
        return {"link": f"https://i.example.com/{slug}.png", "thumb": f"https://i.example.com/{slug}_t.png",
                "delete_url": f"https://i.example.com/delete/{slug}"}


class _HostHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        time.sleep(0.4)  # a visible moment of progress
        out = json.dumps({"data": self.server.next_link()}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *_a):
        pass


# ---- the session ----------------------------------------------------------------------------------------

class Demo:
    """The headless shell, ShareL in it, the demo host, and screenshot helpers."""

    def __init__(self, state: Path, out: Path) -> None:
        self.state = state
        self.out = out
        self.demo = state / "samples"
        self.host: DemoHost | None = None
        self.s: Session | None = None
        self.call = None
        self.saved: list[Path] = []

    # -- setup --

    def start_shell(self) -> None:
        r = subprocess.run([str(E2E / "shell.sh"), "start", str(self.state), str(os.getpid())], capture_output=True,
                           text=True, timeout=90, env=real_env())
        if r.returncode != 0:
            raise RuntimeError(f"cannot start the headless shell: {r.stdout[-800:]} {r.stderr[-800:]}")
        conftest.ARTIFACTS = self.state / "artifacts"
        self.s = Session(self.state, "screenshots")
        conftest.CURRENT.append(self.s)
        bus = self.s.env.get("DBUS_SESSION_BUS_ADDRESS", "")
        # Every setting below goes to the test shell's private dconf, never the real session's.
        assert bus and bus != os.environ.get("DBUS_SESSION_BUS_ADDRESS"), "not the private test bus"
        assert bus == (self.state / "bus.addr").read_text().strip()

    def private_gsettings(self, schema: str, key: str, value: str) -> None:
        assert self.s.env["DBUS_SESSION_BUS_ADDRESS"] != os.environ.get("DBUS_SESSION_BUS_ADDRESS")
        subprocess.run(["gsettings", "set", schema, key, value], env=self.s.env, check=True, timeout=20)

    def seed(self, dark: bool = False) -> None:
        self.demo.mkdir(parents=True, exist_ok=True)
        wp = self.state / "wallpaper.png"
        if not wp.exists():
            wallpaper().save(wp)
            for name, make in DEMO_IMAGES.items():
                make().save(self.demo / name)
            (self.demo / "release-checklist.txt").write_text(DOC)
            (self.demo / "long-document.txt").write_text(SCROLL_DOC)
            (self.demo / "example-host.sxcu").write_text(json.dumps(SAMPLE_SXCU, indent=2))
        for key in ("picture-uri", "picture-uri-dark"):
            self.private_gsettings("org.gnome.desktop.background", key, wp.as_uri())
        self.private_gsettings("org.gnome.desktop.interface", "color-scheme", "prefer-dark" if dark else "default")
        # each text-editor scene opens just its own document (no tabs restored from the previous one)
        self.private_gsettings("org.gnome.TextEditor", "restore-session", "false")
        if self.host is None:
            self.host = DemoHost()
            threading.Thread(target=self.host.serve_forever, daemon=True).start()
        from sharel.core.serialize import to_jsonable
        from sharel.uploaders.custom.model import CustomUploaderItem
        local = CustomUploaderItem(name="Demo host (local)", destination_type=["ImageUploader", "FileUploader"],
                                   request_method="POST", request_url=f"{self.host.url}/upload",
                                   body="MultipartFormData",
                                   file_form_name="file", url="{json:data.link}", thumbnail_url="{json:data.thumb}",
                                   deletion_url="{json:data.delete_url}")
        # the only uploader: the sample .sxcu is opened in its own scene (and never used)
        uploaders = {"custom_uploaders": [to_jsonable(local)],
                     "custom_image_uploader_selected": 0, "custom_file_uploader_selected": 0}
        quiet = {"play_sound_after_capture": False, "play_sound_after_upload": False,
                 "play_sound_after_action": False, "show_toast_notification_after_task_completed": False,
                 "toast_window_duration": 1.0}
        self.s.seed_config(app={
            "theme_mode": "Dark" if dark else "Light",
            "default_task_settings": {
                "after_capture_job": ["SaveImageToFile", "UploadImageToHost"],
                "after_upload_job": ["CopyURLToClipboard"],
                "image_destination": "CustomImageUploader", "file_destination": "CustomFileUploader",
                "image_file_destination": "CustomFileUploader", "general_settings": quiet},
        }, uploaders=uploaders)

    def start_app(self) -> None:
        s = self.s
        env = {**s.env, "GSETTINGS_BACKEND": "memory", "SHAREL_DISABLE_HOTKEYS": "1", "SHAREL_NO_KEYRING": "1",
               "LANG": "en_US.UTF-8", "LANGUAGE": "en_US", "SHAREL_EXECUTABLE": str(ROOT / ".venv" / "bin" / "sharel")}
        log = open(self.state / "sharel.log", "a")  # noqa: SIM115
        s.app_proc = subprocess.Popen([str(ROOT / ".venv" / "bin" / "sharel"), "-silent"], env=env, stdout=log,
                                      stderr=subprocess.STDOUT, cwd=str(ROOT), start_new_session=True)
        wait_for(self.app_running, timeout=30, what="ShareL on the test bus")
        time.sleep(2.0)

    def app_running(self) -> bool:
        try:
            out = self.s.gdbus("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus.NameHasOwner",
                               "app.sharel.ShareL")
        except RuntimeError:
            return False
        return "true" in out

    def stop(self) -> None:
        if self.s is not None:
            with contextlib.suppress(Exception):
                self.s.stop_app()
        subprocess.run([str(E2E / "shell.sh"), "stop", str(self.state)], timeout=60, env=real_env())
        if self.host is not None:
            self.host.shutdown()

    # -- windows --

    def windows(self, pid_only: bool = True) -> list[dict]:
        pid = self.s.app_proc.pid if self.s.app_proc else None
        return [w for w in self.s.windows() if not pid_only or w["pid"] == pid]

    def window(self, title_part: str, timeout: float = 20.0) -> dict:
        return wait_for(lambda: next((w for w in self.windows() if title_part.lower() in w["title"].lower()), None),
                        timeout=timeout, what=f"ShareL window {title_part!r}")

    def frame(self, w: dict) -> list[int]:
        return self.s.ext("GetWindowDetails", f"uint64 {w['id']}")["frame"]

    def place(self, w: dict, x: int, y: int, width: int, height: int) -> dict:
        """Move and resize a window (again, until it takes: a window that has just mapped may not)."""
        for _ in range(10):
            self.s.ext("MoveResize", f"uint64 {w['id']}", str(x), str(y), str(width), str(height))
            time.sleep(0.8)
            if self.frame(w) == [x, y, width, height]:
                break
        else:
            say(f"  note: {w['title']!r} is at {self.frame(w)}, not {[x, y, width, height]}")
        self.s.ext("Activate", f"uint64 {w['id']}")
        time.sleep(0.6)
        return {**w, "frame": self.frame(w)}

    # -- screenshots --

    async def grab(self, rect, name: str) -> Path:
        x, y, w, h = (int(round(v)) for v in rect)
        x, y = max(0, x), max(0, y)
        w, h = min(w, SCREEN_W - x), min(h, SCREEN_H - y)
        text, _d, images, err = await self.call("screenshot", region=[x, y, w, h], max_edge=4000)
        assert not err and images, text[:400]
        im = Image.open(io.BytesIO(images[0])).convert("RGB")
        assert im.size == (w, h), f"{name}: got {im.size}, wanted {(w, h)} (downscaled?)"
        return self.save(im, name)

    async def grab_window(self, w: dict, name: str, pad: tuple[int, int, int, int] = (0, 0, 0, 0)) -> Path:
        # the pointer onto the other monitor: no hover highlight or tooltip in the shot
        await self.call("mouse_move", x=SCREEN_W + 800, y=850, restore_pointer=False)
        time.sleep(0.8)
        x, y, fw, fh = self.frame(w)
        left, top, right, bottom = pad
        try:
            return await self.grab((x - left, y - top, fw + left + right, fh + top + bottom), name)
        finally:  # back to the primary monitor's far corner: new windows open on the pointer's monitor
            await self.call("mouse_move", x=SCREEN_W - 4, y=SCREEN_H - 4, restore_pointer=False)

    def save(self, im: Image.Image, name: str) -> Path:
        p = self.out / f"{name}.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        im.save(p, optimize=True)
        how = "lossless"
        if p.stat().st_size > QUANTIZE_ABOVE:
            # oxipng and pngquant aren't assumed: PIL's 256-colour palette, used only when every solid
            # colour survives it (see palette_error). Shots with many colours (gradients, thumbnails) stay lossless.
            q = im.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
            worst = palette_error(im, q)
            if worst <= 12 or p.stat().st_size > MAX_BYTES:
                buf = io.BytesIO()
                q.save(buf, "PNG", optimize=True)
                if len(buf.getvalue()) < p.stat().st_size:
                    p.write_bytes(buf.getvalue())
                    how = f"256 colours, worst solid-colour error {worst}"
        say(f"  {p.relative_to(ROOT)}: {im.size[0]}x{im.size[1]}, {p.stat().st_size // 1024} KB ({how})")
        self.saved.append(p)
        return p


    async def locate(self, rect, colour: tuple, at: tuple[int, int]) -> tuple[int, int]:
        """Global position of an image drawn 1:1 inside ``rect``, from a marker: the top-left-most pixels of
        ``colour``, which sit at ``at`` in the image."""
        import numpy as np
        x, y, w, h = rect
        _t, _d, images, err = await self.call("screenshot", region=[x, y, w, h], max_edge=4000)
        assert not err and images
        shot = np.asarray(Image.open(io.BytesIO(images[0])).convert("RGB")).astype(int)
        ys, xs = np.nonzero((np.abs(shot - np.array(colour)) <= 3).all(axis=2))
        assert len(xs), f"marker colour {colour} not on screen"
        return x + int(xs.min()) - at[0], y + int(ys.min()) - at[1]

    # -- input through RootPilot --

    async def find(self, name: str, window: str, role: str | None = None) -> dict:
        kw = {"role": role} if role else {}
        text, data, _i, _e = await self.call("find", name=name, window=window, limit=20, **kw)
        hits = [it for it in (data or {}).get("data") or [] if it.get("name") == name]
        if role:
            hits = [it for it in hits if it.get("role") == role] or hits
        assert hits, f"{name!r} not found in {window!r}: {text[:500]}"
        return hits[0]

    async def click(self, name: str, window: str, role: str | None = None) -> None:
        await self.call("click", ref=(await self.find(name, window, role))["ref"], restore_focus=False)
        time.sleep(0.5)

    async def key(self, combo: str, window: str | None = None) -> None:
        kw = {"window": window} if window else {}
        await self.call("key", combo=combo, restore_focus=False, **kw)
        time.sleep(0.25)

    async def type(self, text: str, window: str) -> None:
        await self.call("type_text", text=text, window=window, restore_focus=False)
        time.sleep(0.3)

    async def drag(self, a, b, hold_ms: int = 150) -> None:
        await self.call("mouse_drag", from_x=a[0], from_y=a[1], to_x=b[0], to_y=b[1], hold_ms=hold_ms,
                        restore_focus=False, restore_pointer=False)
        time.sleep(0.4)

    async def fill(self, name: str, value: str, window: str) -> None:
        await self.click(name, window, "text")
        await self.key("ctrl+a", window)
        await self.type(value, window)

    def main_window(self) -> dict:
        self.s.sharel("-OpenMainWindow")
        return wait_for(lambda: next((w for w in self.windows() if w["title"] == "ShareL"), None),
                        what="main window")

    async def overlay(self, monitor: int = 0) -> dict:
        ov = wait_for(lambda: next((w for w in self.windows() if "region capture" in w["title"]
                                    and w["monitor"] == monitor), None), what="region overlay")
        self.s.ext("Activate", f"uint64 {ov['id']}")
        time.sleep(0.8)
        return ov

    async def editor_text_window(self, path: Path, x: int, y: int, w: int, h: int) -> dict:
        """gnome-text-editor with a demo document, for the OCR and scrolling scenes."""
        # its saved session (in the test shell's private XDG dirs) would reopen the previous scene's document
        for d in self.state.glob("*/org.gnome.TextEditor"):
            shutil.rmtree(d, ignore_errors=True)
        await self.call("app_launch", app="org.gnome.TextEditor", args=["--standalone", str(path)])
        ed = wait_for(lambda: next((w for w in self.s.windows() if w["wm_class"] == "org.gnome.TextEditor"
                                    and path.stem in w["title"]), None), what="text editor")
        self.s.ext("MoveResize", f"uint64 {ed['id']}", str(x), str(y), str(w), str(h))
        time.sleep(1.5)
        return ed


def palette_error(im: Image.Image, q: Image.Image, min_pixels: int = 40) -> int:
    """The largest channel error, after quantizing, of any colour that covers at least ``min_pixels`` pixels.
    (The mean error hides it when a small solid dot changes colour; anti-aliasing pixels may drift.)"""
    import numpy as np
    a = np.asarray(im.convert("RGB"), np.int32).reshape(-1, 3)
    b = np.asarray(q.convert("RGB"), np.int32).reshape(-1, 3)
    _u, inv, counts = np.unique((a[:, 0] << 16) | (a[:, 1] << 8) | a[:, 2], return_inverse=True,
                                return_counts=True)
    worst = np.zeros(len(counts), np.int32)
    np.maximum.at(worst, inv.ravel(), np.abs(a - b).max(axis=1))
    return int(worst[counts >= min_pixels].max(initial=0))


SCENES: dict = {}


def scene(name: str):
    def deco(fn):
        SCENES[name] = fn
        return fn
    return deco


def union(*rects, pad: int = 0) -> tuple[int, int, int, int]:
    x0 = min(r[0] for r in rects) - pad
    y0 = min(r[1] for r in rects) - pad
    x1 = max(r[0] + r[2] for r in rects) + pad
    y1 = max(r[1] + r[3] for r in rects) + pad
    return x0, y0, x1 - x0, y1 - y0


async def thumbnail_view(d: Demo, on: bool) -> None:
    it = await d.find("Thumbnail view", "ShareL", "toggle button")
    if ("pressed" in it.get("states", [])) != on:
        await d.call("click", ref=it["ref"], restore_focus=False)
        time.sleep(0.8)


# ---- scenes ----------------------------------------------------------------------------------------------

MAIN_RECT = (300, 150, 1320, 760)
MENU_RECT = (300, 250, 1320, 760)  # lower: the menus reach above the window


@scene("main")
async def _main(d: Demo) -> None:
    """Hero: the main window, thumbnail view of the finished tasks."""
    w = d.place(d.main_window(), *MAIN_RECT)
    await thumbnail_view(d, True)
    await d.grab_window(w, "main")


@scene("main-menu")
async def _main_menu(d: Demo) -> None:
    """List view (file, status, URL) with the Capture menu open."""
    w = d.place(d.main_window(), *MENU_RECT)
    await thumbnail_view(d, False)
    await d.call("click", text="Capture", window="ShareL", restore_focus=False)
    time.sleep(1.0)
    await d.grab_window(w, "main-capture-menu", pad=(0, 200, 0, 0))
    await d.key("Escape", "ShareL")
    await thumbnail_view(d, True)


@scene("image-history")
async def _image_history(d: Demo) -> None:
    d.s.sharel("-OpenImageHistory")
    w = d.place(d.window("Image history"), 260, 240, 1400, 420)
    time.sleep(1.5)
    await d.grab_window(w, "image-history")


@scene("region")
async def _region(d: Demo) -> None:
    """Region capture mid-selection: the selection with its size, the magnifier and the toolbar."""
    w = d.place(d.main_window(), *MAIN_RECT)
    await thumbnail_view(d, True)
    d.s.sharel("-RectangleRegion")
    await d.overlay(0)
    x, y = w["frame"][0], w["frame"][1]
    # from above the sunset to inside the weekly-users chart: the moving corner (the pointer) is on the chart's
    # bars, so the magnifier shows them
    a, b = (x + 555, y + 240), (x + 1150, y + 380)

    async def shoot():
        await asyncio.sleep(4.3)  # the 3 s press, then the pointer has moved to the far corner
        # the overlay's toolbar and the screen around the window; not the top bar's tray, where GNOME
        # shows its screen-sharing indicator for RootPilot's input session
        await d.grab((240, 0, 1440, 990), "region-capture")

    # The drag holds the button at its end while the screenshot is taken; releasing it takes the capture.
    await asyncio.gather(d.call("mouse_drag", from_x=a[0], from_y=a[1], to_x=b[0], to_y=b[1], hold_ms=3000,
                                restore_focus=False, restore_pointer=False), shoot())
    time.sleep(2.0)


@scene("editor")
async def _editor(d: Demo) -> None:
    """The image editor with an arrow, a rectangle, text, step numbers, a pixelated area and a highlight."""
    src = d.demo / "lumen-notes.png"
    d.s.sharel("-ImageEditor", str(src))
    w = d.window("image editor")
    d.place(w, 120, 50, 1680, 990)
    t = w["title"]
    time.sleep(1.0)
    ox, oy = await d.locate(d.frame(w), LUMEN_PURPLE, (28, 16))  # the image, centred at 100 %: its logo

    def at(px, py):
        return ox + px, oy + py

    await d.key("h", t)  # highlight the headline's second line
    await d.drag(at(54, 176), at(372, 236))
    await d.key("r", t)  # a rectangle around the call to action
    await d.drag(at(48, 298), at(332, 378))
    await d.key("p", t)  # pixelate the private details
    await d.drag(at(54, 394), at(420, 462))
    await d.key("a", t)  # an arrow pointing at the button
    await d.drag(at(470, 560), at(344, 372))
    await d.key("t", t)  # text with a background, next to the arrow
    await d.drag(at(380, 560), at(600, 610))
    time.sleep(0.8)  # an in-window dialog: its text view has the focus
    await d.type("Start here", t)
    await d.click("OK", t, "button")
    await d.key("i", t)  # step numbers on the three things to look at
    for px, py in ((30, 150), (36, 320), (548, 128)):
        await d.call("mouse_click", x=at(px, py)[0], y=at(px, py)[1], restore_focus=False, restore_pointer=False)
        time.sleep(0.4)
    await d.key("m", t)  # select tool, then click the empty canvas: no shape selected
    await d.call("mouse_click", x=ox - 60, y=oy + 300, restore_focus=False, restore_pointer=False)
    # off the canvas (onto the header bar), so the editor's magnifier goes away
    fx, fy, fw, _fh = d.frame(w)
    await d.call("mouse_move", x=fx + fw // 2 + 200, y=fy + 22, restore_pointer=False)
    time.sleep(0.8)
    await d.grab_window(w, "editor")


@scene("recording")
async def _recording(d: Demo) -> None:
    """An active region recording: the region's border and the recording controls."""
    w = d.place(d.main_window(), *MAIN_RECT)
    await thumbnail_view(d, True)
    before = set(d.s.pictures.rglob("*"))
    d.s.sharel("-ScreenRecorder")
    await d.overlay(0)
    x, y = w["frame"][0], w["frame"][1]
    rect = (x + 312, y + 48, 1000, 380)  # the thumbnails
    await d.drag((rect[0], rect[1]), (rect[0] + rect[2], rect[1] + rect[3]))
    ctl = d.window("ShareL recording", timeout=20)
    time.sleep(4.0)  # a few seconds in: the timer shows it running
    await d.grab(union(d.frame(w), d.frame(ctl), pad=0), "recording")
    d.s.sharel("-AbortScreenRecording")
    wait_for(lambda: not any("ShareL recording" in x["title"] for x in d.windows()), what="recording aborted")
    time.sleep(1.0)
    assert not [p for p in d.s.pictures.rglob("*") if p not in before and p.suffix in (".mp4", ".part")]


async def open_from_main(d: Demo, button: str, item: str | None, title: str) -> dict:
    w = d.place(d.main_window(), *MAIN_RECT)
    await d.call("click", text=button, window="ShareL", restore_focus=False)
    time.sleep(0.8)
    if item:
        await d.click(item, "ShareL")
    win = d.window(title)
    time.sleep(1.0)
    assert win["id"] != w["id"]
    return win


@scene("after-capture")
async def _after_capture(d: Demo) -> None:
    """The After capture tasks menu: what runs after every capture (here: save, then upload)."""
    w = d.place(d.main_window(), *MENU_RECT)
    await thumbnail_view(d, False)
    await d.call("click", text="After capture tasks", window="ShareL", restore_focus=False)
    time.sleep(1.0)
    await d.grab_window(w, "after-capture-tasks", pad=(0, 200, 0, 0))
    await d.key("Escape", "ShareL")


@scene("history")
async def _history(d: Demo) -> None:
    d.s.sharel("-OpenHistory")
    w = wait_for(lambda: next((x for x in d.windows() if x["title"].startswith("ShareL - History")), None),
                 what="History window")  # "ShareL - History (Total: 5 - Image: 5)"
    w = d.place(w, 220, 120, 1480, 760)
    await d.call("click", text="sunset.png", window=w["title"], restore_focus=False)  # its details on the right
    time.sleep(1.5)
    await d.grab_window(w, "history")


@scene("task-settings")
async def _task_settings(d: Demo) -> None:
    w = await open_from_main(d, "Task settings…", None, "Task settings")
    d.place(w, 360, 100, 1200, 860)
    await d.click("Capture", w["title"])
    time.sleep(0.8)
    await d.grab_window(w, "task-settings")


@scene("destinations")
async def _destinations(d: Demo) -> None:
    w = await open_from_main(d, "Destinations", "Destination settings…", "Destination settings")
    d.place(w, 300, 110, 1320, 840)
    await d.click("Imgur", w["title"])
    time.sleep(0.8)
    await d.grab_window(w, "destinations")


@scene("hotkeys")
async def _hotkeys(d: Demo) -> None:
    w = await open_from_main(d, "Hotkey settings…", None, "Hotkey settings")
    d.place(w, 360, 120, 1200, 820)
    await d.grab_window(w, "hotkeys")


@scene("custom-uploader")
async def _custom_uploader(d: Demo) -> None:
    """The sample .sxcu opened with `sharel example-host.sxcu`, as a double-click on it does: ShareL imports it,
    asks whether to make it the active custom uploader (Yes) and shows it in Custom uploader settings. Nothing is
    uploaded in this scene, and the next one starts from a fresh config."""
    d.s.sharel(str(d.demo / "example-host.sxcu"))
    w = d.window("Custom uploader settings")
    time.sleep(1.0)
    await d.click("Yes", w["title"], "button")
    w = d.place(w, 220, 60, 1480, 960)
    # ShareL's "Custom uploader added" notification: GNOME keeps the banner up while the user is idle, so
    # some pointer movement (on the other monitor) lets it time out
    for i in range(16):
        await d.call("mouse_move", x=SCREEN_W + 600 + (i % 2) * 200, y=700, restore_pointer=False)
        time.sleep(0.5)
    time.sleep(1.0)
    await d.grab_window(w, "custom-uploader")


@scene("color-picker")
async def _color_picker(d: Demo) -> None:
    d.s.sharel("-ColorPicker")
    w = d.window("Color picker")
    d.place(w, 560, 160, 894, 614)
    await d.click("#FF6A00FF", w["title"], "table cell")
    time.sleep(0.6)
    await d.grab_window(w, "color-picker")


@scene("ruler")
async def _ruler(d: Demo) -> None:
    w = d.place(d.main_window(), *MAIN_RECT)
    await thumbnail_view(d, True)
    d.s.sharel("-Ruler")
    await d.overlay(0)
    x, y = w["frame"][0], w["frame"][1]
    a, b = (x + 820, y + 78), (x + 1055, y + 224)  # the sunset thumbnail
    await d.drag(a, b)
    await d.call("mouse_move", x=a[0] - 150, y=b[1] + 40, restore_pointer=False)  # the magnifier, clear of it
    time.sleep(0.8)
    await d.grab(union(d.frame(w), pad=30), "ruler")  # the whole window: box, readout and magnifier inside it
    await d.key("Escape")


@scene("qr-code")
async def _qr(d: Demo) -> None:
    """Encode a URL, then decode it back from the screen."""
    d.s.sharel("-QRCode")
    w = d.window("QR code")
    d.place(w, 40, 120, 1060, 760)
    await d.fill("Text to encode", "https://i.example.com/KeY39pW.png", w["title"])
    time.sleep(1.5)
    d.s.sharel("-QRCodeDecodeFromScreen")
    res = d.window("QR code scan", timeout=30)
    d.place(res, 1130, 300, 760, 480)
    await d.grab(union(d.frame(w), d.frame(res), pad=24), "qr-code")


@scene("ocr")
async def _ocr(d: Demo) -> None:
    ed = await d.editor_text_window(d.demo / "release-checklist.txt", 60, 160, 860, 560)
    x, y, ew, _h = d.frame(ed)
    d.s.sharel("-OCR")
    await d.overlay(0)
    await d.drag((x + 10, y + 56), (x + ew - 30, y + 400))
    w = d.window("OCR", timeout=30)
    d.place(w, 960, 120, 900, 640)
    time.sleep(4.0)  # recognition runs off the main loop
    await d.grab(union(d.frame(ed), d.frame(w), pad=24), "ocr")
    os.kill(ed["pid"], signal.SIGTERM)


@scene("image-combiner")
async def _combiner(d: Demo) -> None:
    import test_e2e_tools_image as ti
    d.s.sharel("-ImageCombiner")
    w = d.window("Image combiner")
    d.place(w, 360, 140, 1200, 800)
    for name in ("sunset.png", "weekly-users.png", "autumn-palette.png"):
        before = ti.ids(d.s)
        await d.click("Add…", w["title"], "button")
        await ti.pick_file(d.s, d.call, before, d.demo / name)
    time.sleep(1.0)
    await d.grab_window(w, "image-combiner")


@scene("hash-check")
async def _hash(d: Demo) -> None:
    d.s.sharel("-HashCheck")
    w = d.window("Hash checker")
    d.place(w, 460, 200, 1000, 640)
    await d.fill("File path:", str(d.demo / "sunset.png"), w["title"])
    await d.key("Tab", w["title"])
    await d.click("Check", w["title"], "button")
    time.sleep(2.0)
    await d.grab_window(w, "hash-check")


@scene("scrolling-capture")
async def _scrolling(d: Demo) -> None:
    ed = await d.editor_text_window(d.demo / "long-document.txt", 120, 60, 760, 560)
    x, y, ew, eh = d.frame(ed)
    d.s.sharel("-ScrollingCapture")
    await d.overlay(0)
    await d.drag((x + 8, y + 50), (x + ew - 40, y + eh - 26))
    sw = d.window("scrolling capture")
    time.sleep(1.0)
    await d.click("Start capture", sw["title"], "button")
    wait_for(lambda: not any("scrolling capture" in w["title"] for w in d.windows()), timeout=10,
             what="scrolling capture running")
    sw = d.window("scrolling capture", timeout=120)
    d.place(sw, 400, 40, 1120, 1000)
    time.sleep(1.5)
    await d.grab_window(sw, "scrolling-capture")
    os.kill(ed["pid"], signal.SIGTERM)


@scene("tray")
async def _tray(d: Demo) -> None:
    await d.call("mouse_move", x=900, y=700, restore_pointer=False)
    time.sleep(1.0)
    rect = d.s.ext("GetTrayIconRect", "app.sharel.ShareL")
    assert rect["found"], rect
    x, _y, _w, _h = rect["rect"]
    d.s.sharel("-ToggleTrayMenu")
    time.sleep(1.2)
    # the menu hangs from the top bar; the crop starts below the bar (GNOME's screen-sharing indicator
    # for RootPilot's input session sits next to the tray icon)
    await d.grab((x - 130, 34, 300, 900), "tray-menu")
    await d.key("Escape")


@scene("main-dark")
async def _main_dark(d: Demo) -> None:
    """Bonus: the main window in libadwaita's dark style (ShareL's Theme: Dark)."""
    await fresh(d, dark=True)
    w = d.place(d.main_window(), *MAIN_RECT)
    await thumbnail_view(d, True)
    await d.grab_window(w, "main-dark")


STATE = Path("/tmp/sharel-demo")  # not under $HOME or $TMPDIR: some scenes show paths, and they mustn't name the user
MARK = ".sharel-screenshots"


def make_state() -> Path:
    if STATE.exists():  # a previous run's leftovers (it was killed); never anything else
        if not (STATE / MARK).exists():
            raise SystemExit(f"{STATE} exists and isn't this script's; move it away first")
        subprocess.run([str(E2E / "shell.sh"), "stop", str(STATE)], timeout=60, env=real_env())
        shutil.rmtree(STATE)
    STATE.mkdir(mode=0o700)
    (STATE / MARK).touch()
    return STATE


async def run(args) -> int:
    state = make_state()
    out = Path(args.out).resolve()
    demo = Demo(state, out)
    names = list(SCENES) if not args.only else [n.strip() for n in args.only.split(",")]
    unknown = [n for n in names if n not in SCENES]
    if unknown:
        raise SystemExit(f"unknown scene(s): {unknown}; known: {list(SCENES)}")
    try:
        say(f"state: {state}")
        demo.start_shell()
        demo.seed()
        demo.start_app()
        async with demo.s.rootpilot() as client:
            demo.call = demo.s.client_mod.Call(client, image_dir=None)
            await demo.call("mouse_move", x=SCREEN_W + 800, y=850)  # out of the way, on the second monitor
            populate(demo)
            if args.hold:
                say(f"holding: source {state}/env to drive it; Ctrl-C or SIGTERM ends it")
                while True:
                    await asyncio.sleep(3600)
            failed = []
            for i, n in enumerate(names):
                say(f"scene {n}")
                try:
                    if i:
                        await fresh(demo)
                    await SCENES[n](demo)
                except Exception as e:
                    failed.append(n)
                    say(f"  FAILED: {type(e).__name__}: {str(e)[:1500]}")
                    with contextlib.suppress(Exception):
                        say(demo.s.log_tail(15))
        total = sum(p.stat().st_size for p in out.glob("*.png"))
        say(f"done: {len(names) - len(failed)}/{len(names)} scenes; {total // 1024} KB in {out}")
        if failed:
            say(f"failed: {failed}")
        return 1 if failed else 0
    finally:
        demo.stop()
        shutil.rmtree(state, ignore_errors=True)


def populate(demo: Demo) -> None:
    """Some finished tasks for the main window and history: the demo images, uploaded to the demo host."""
    files = [str(demo.demo / n) for n in DEMO_IMAGES]
    for i, f in enumerate(files, 1):  # one at a time, so the list order is the same on every run
        demo.s.sharel(f)
        wait_for(lambda i=i: len([h for h in demo.s.history() if "example.com" in h.get("url", "")]) >= i,
                 timeout=30, what=f"demo upload {i}")
    time.sleep(1.0)


async def fresh(demo: Demo, dark: bool = False) -> None:
    """A clean slate for the next scene: ShareL restarted (its windows gone), other apps closed, the
    history back to the demo uploads, which are run again so the main window lists them."""
    demo.s.stop_app()
    for w in demo.s.windows():
        if w["wm_class"] == "org.gnome.TextEditor":
            with contextlib.suppress(ProcessLookupError):
                os.kill(w["pid"], signal.SIGTERM)
    wait_for(lambda: not demo.s.windows(), timeout=15, what="every window closed")
    (demo.s.data_dir / "History.json").unlink(missing_ok=True)
    demo.seed(dark=dark)
    demo.start_app()
    await demo.call("mouse_move", x=SCREEN_W - 4, y=SCREEN_H - 4, restore_pointer=False)  # new windows: monitor 0
    populate(demo)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", help="comma-separated scene names")
    ap.add_argument("--list", action="store_true", help="list the scenes")
    ap.add_argument("--hold", action="store_true", help="set everything up, then wait (for exploring)")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--lock", help="flock this file for the whole run (one headless shell at a time)")
    args = ap.parse_args()
    if args.list:
        print("\n".join(SCENES))
        return 0
    lock = None
    if args.lock:
        lock = open(args.lock, "a")  # noqa: SIM115
        fcntl.flock(lock, fcntl.LOCK_EX)
    def _on_term(signum, _frame):  # run the cleanup (ShareL, the shell, the temp dir) on SIGTERM too
        raise SystemExit(f"terminated by signal {signum}")
    signal.signal(signal.SIGTERM, _on_term)
    try:
        return asyncio.run(run(args))
    finally:
        if lock:
            lock.close()


if __name__ == "__main__":
    sys.exit(main())
