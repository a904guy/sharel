"""Scenario 15 (image file tools): every image tool in the main window's Tools menu opens from that menu
by real input and does its main job on a real file, chosen in the real file dialog (Ctrl+L, path, Enter).

Each check would fail if the action did nothing: output files are re-read and their size, format, pixels or
metadata compared with the input, and shown results are the computed values for the generated input."""
from __future__ import annotations

import shutil
import time
from pathlib import Path

import numpy as np
import pytest
from conftest import found, wait_for
from PIL import Image, ImageDraw

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

SCREEN_H = 1080  # the primary virtual monitor (tests/e2e/shell.sh)
MODELS = Path.home() / ".cache" / "sharel-test-models"  # rembg's u2netp, as the unit tests cache it


@pytest.fixture(scope="module")
def app(session):
    # Nothing leaves the machine: the only after-capture task is saving the file; no upload, no clipboard.
    session.seed_config(app={"default_task_settings": {"after_capture_job": ["SaveImageToFile"],
                                                       "after_upload_job": []}})
    if (MODELS / "models").is_dir():  # the background remover's model, so it is never downloaded
        shutil.copytree(MODELS, session.state / "cache" / "ShareL" / "rembg", dirs_exist_ok=True)
    session.start_app()
    session.baseline = {w["id"] for w in session.windows()}
    yield session
    session.stop_app()


@pytest.fixture(autouse=True)
async def final_screen(request, app):
    """Evidence (and diagnosis): the whole screen as each test ends. Then close whatever the test left
    open (newest first, so a modal dialog goes before its window): one failure must not break the next."""
    yield
    d = app.artifacts / request.node.name
    d.mkdir(parents=True, exist_ok=True)
    try:
        app.ext("Screenshot", "monitor", "uint64 0", "0", "0", "0", "0", "false", str(d / "final-screen.png"))
    except RuntimeError:
        pass
    extra = [w for w in app.windows() if w["id"] not in app.baseline and w["title"] != "ShareL"]
    if extra:
        async with app.rootpilot() as client:
            call = caller(app, client)
            for w in reversed(extra):
                try:
                    await close(call, app, w["id"])
                except Exception:  # best effort
                    pass
        time.sleep(0.5)


# -- inputs -------------------------------------------------------------------------------------------------


def folder(s, name: str) -> Path:
    d = s.state / "tools-in" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def pattern(size=(400, 300)) -> Image.Image:
    """Distinct quadrants, so crops, flips and colour changes all show in the pixels."""
    w, h = size
    img = Image.new("RGB", size, (40, 120, 200))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, w // 2 - 1, h // 2 - 1), fill=(220, 40, 40))
    d.rectangle((w // 2, h // 2, w - 1, h - 1), fill=(30, 170, 60))
    d.ellipse((w // 3, h // 3, 2 * w // 3, 2 * h // 3), fill=(250, 220, 40))
    return img


def pixels(path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"), dtype=np.int16)


# -- UI helpers ---------------------------------------------------------------------------------------------


FOCUS_TOOLS = {"click", "mouse_click", "mouse_drag", "key", "type_text"}


def caller(s, client):
    """RootPilot calls that leave focus where the action put it. By default RootPilot gives focus back to
    the window that had it (a courtesy for a human's desktop), which here would raise the main window
    over the tool window that just opened."""
    call = s.call(client)

    async def c(tool, **kw):
        if tool in FOCUS_TOOLS:
            kw.setdefault("restore_focus", False)
        return await call(tool, **kw)
    return c


def ids(s) -> set[int]:
    return {w["id"] for w in s.windows()}


def new_window(s, before: set[int], title_part: str = "", timeout: float = 20.0) -> dict:
    hit = wait_for(lambda: next((w for w in s.windows() if w["id"] not in before
                                 and title_part.lower() in w["title"].lower()), None),
                   timeout=timeout, what=f"new window {title_part!r}")
    time.sleep(1.0)  # let the map animation finish before clicking into it
    return hit


def title_of(s, wid: int) -> str:
    return next(w["title"] for w in s.windows() if w["id"] == wid)


def gone(s, wid: int, timeout: float = 15.0) -> None:
    wait_for(lambda: wid not in ids(s), timeout=timeout, what=f"window {wid} to close")


async def item(call, name: str, window: str, role: str | None = None) -> dict:
    kw = {"role": role} if role else {}
    text, data, _i, _e = await call("find", name=name, window=window, limit=10, **kw)
    hits = [it for it in (data or {}).get("data") or [] if it.get("name") == name]
    assert hits, f"{name!r} not found in {window!r}: {text[:600]}"
    return hits[0]


async def click(call, name: str, window: str, role: str | None = None) -> None:
    await call("click", ref=(await item(call, name, window, role))["ref"])


async def open_tool(s, call, label: str, title_part: str, timeout: float = 20.0) -> dict:
    """Main window → Tools → ``label`` (scrolling the menu with the wheel when the entry is off screen)."""
    main = next((w for w in s.windows() if w["title"] == "ShareL"), None)
    if main is None:
        s.sharel("-OpenMainWindow")
        main = wait_for(lambda: next((w for w in s.windows() if w["title"] == "ShareL"), None), what="main window")
    s.ext("Activate", f"uint64 {main['id']}")
    time.sleep(1.0)
    before = ids(s)
    await call("click", text="Tools", window="ShareL")
    time.sleep(0.7)
    for _ in range(12):
        it = await item(call, label, "ShareL")
        _x, y, _w, h = it.get("bounds") or (0, -1, 0, 0)
        if y >= 0 and y + h <= SCREEN_H - 8:
            break
        bx = (it.get("bounds") or (main["frame"][0] + 300,))[0] + 20
        await call("scroll", dy=4 if y + h > SCREEN_H - 8 else -4, x=bx, y=SCREEN_H // 2)
        time.sleep(0.4)
    else:
        raise AssertionError(f"menu entry {label!r} never scrolled into view")
    await call("click", ref=it["ref"])
    return new_window(s, before, title_part, timeout=timeout)


async def pick_file(s, call, before: set[int], path, title_part: str = "") -> None:
    """The file dialog that just opened: type the path into its location bar (Ctrl+L), confirm with Open."""
    dlg = new_window(s, before, title_part)
    await call("key", combo="ctrl+l", window=dlg["title"])
    time.sleep(0.4)
    await call("type_text", text=str(path), window=dlg["title"])
    time.sleep(0.4)
    await call("key", combo="Return", window=dlg["title"])  # takes the typed location: selects the file
    try:
        gone(s, dlg["id"], timeout=2.5)
    except TimeoutError:  # GTK's chooser selects the typed file on Return; its Open button confirms
        await click(call, "Open", dlg["title"], "button")
        gone(s, dlg["id"])
    time.sleep(0.5)


async def accept_save(s, call, before: set[int], title_part: str = "") -> None:
    """A save dialog that suggests a name next to the source: accept it as is."""
    dlg = new_window(s, before, title_part)
    await call("screenshot", window=dlg["title"], max_width=1000)  # evidence: the suggested name
    await click(call, "Save", dlg["title"], "button")
    gone(s, dlg["id"])
    time.sleep(0.5)


async def close(call, s, wid: int) -> None:
    """Close a tool window as a user would: Escape (ShareL's tool windows close on it), else its ×.
    (RootPilot's window_close declines while GTK still lists the finished file chooser as a dialog.)"""
    if wid not in ids(s):
        return
    await call("key", combo="Escape", window=title_of(s, wid))
    try:
        gone(s, wid, timeout=3)
    except TimeoutError:
        await call("window_close", window=title_of(s, wid))
        gone(s, wid)


async def wait_file(path: Path, timeout: float = 30.0) -> Path:
    def ready():
        if path.exists() and path.stat().st_size > 0:
            size = path.stat().st_size
            time.sleep(0.3)
            return path.stat().st_size == size
        return False
    wait_for(ready, timeout=timeout, what=f"{path.name} written")
    return path


# -- the batch tools (input list, options, run button, files next to the source) -------------------------


async def test_s15_image_resizer(app):
    s = app
    src = folder(s, "resizer") / "in.png"
    pattern().save(src)
    async with s.rootpilot() as client:
        call = caller(s, client)
        win = await open_tool(s, call, "Image resizer", "Image resizer")
        t = win["title"]
        before = ids(s)
        await click(call, "Add…", t, "button")
        await pick_file(s, call, before, src)
        # width 200 (height stays 0 = keep the aspect ratio). The middle of a GTK spin button is its "-"
        # button, so click its text near the left edge, select it, type, Tab out.
        _txt, data, _i, _e = await call("find", name="Width", window=t, limit=10)
        spins = [it for it in data["data"] if (it.get("name") or "").startswith("Width")
                 and it.get("role") == "spin button"]
        if not spins:
            snap = await call("snapshot", window=t, mode="a11y", max_nodes=800)
            (s.artifacts / "resizer-a11y.txt").write_text(str(snap[0]))
        assert spins, _txt[:800]
        bx, by, _bw, bh = spins[0]["bounds"]
        await call("mouse_click", x=bx + 12, y=by + bh / 2)
        await call("key", combo="ctrl+a", window=t)
        await call("type_text", text="200", window=t)
        await call("key", combo="Tab", window=t)
        time.sleep(0.5)
        assert not src.with_name("in_resized.png").exists()
        await click(call, "Resize", t, "button")
        out = await wait_file(src.with_name("in_resized.png"))
        await call("screenshot", window=t, max_width=1000)
        with Image.open(out) as im:
            assert im.size == (200, 150), im.size
        await close(call, s, win["id"])


async def test_s15_image_thumbnailer(app):
    s = app
    src = folder(s, "thumb") / "in.png"
    pattern().save(src)
    async with s.rootpilot() as client:
        call = caller(s, client)
        win = await open_tool(s, call, "Image thumbnailer", "Image thumbnailer")
        t = win["title"]
        before = ids(s)
        await click(call, "Add…", t, "button")
        await pick_file(s, call, before, src)
        assert not list(src.parent.glob("in_th.*"))
        await click(call, "Generate thumbnails", t, "button")
        out = wait_for(lambda: [p for p in src.parent.glob("in_th.*") if p.stat().st_size], what="thumbnail")[0]
        await wait_file(out)
        await call("screenshot", window=t, max_width=1000)
        with Image.open(out) as im:
            assert im.size == (200, 150), im.size  # the default 200 px width, height proportional
        await close(call, s, win["id"])


async def test_s15_image_converter(app):
    s = app
    src = folder(s, "convert") / "in.png"
    pattern().save(src)
    async with s.rootpilot() as client:
        call = caller(s, client)
        win = await open_tool(s, call, "Image converter", "Image converter")
        t = win["title"]
        before = ids(s)
        await click(call, "Add…", t, "button")
        await pick_file(s, call, before, src)
        await click(call, "Output format", t)  # combo row: its list pops up
        time.sleep(0.6)
        await call("click", text="JPEG", window=t)
        time.sleep(0.5)
        await click(call, "Convert", t, "button")
        out = await wait_file(src.with_suffix(".jpg"))
        await call("screenshot", window=t, max_width=1000)
        with Image.open(out) as im:
            assert im.format == "JPEG" and im.size == (400, 300)
        assert np.abs(pixels(out) - pixels(src)).mean() < 6  # the same picture, re-encoded
        await close(call, s, win["id"])


async def test_s15_image_watermark(app):
    s = app
    src = folder(s, "watermark") / "in.png"
    pattern().save(src)
    async with s.rootpilot() as client:
        call = caller(s, client)
        win = await open_tool(s, call, "Image watermark", "Image watermark")
        t = win["title"]
        before = ids(s)
        await click(call, "Add…", t, "button")
        await pick_file(s, call, before, src)
        await click(call, "Apply watermark", t, "button")
        out = await wait_file(src.with_name("in_watermarked.png"))
        await call("screenshot", window=t, max_width=1000)
        a, b = pixels(src), pixels(out)
        assert a.shape == b.shape
        # the default text watermark sits bottom right: that corner changed, the opposite one did not
        assert np.abs(a[-60:, -160:] - b[-60:, -160:]).max() > 60
        assert (a[:60, :160] == b[:60, :160]).all()
        await close(call, s, win["id"])


async def test_s15_strip_metadata(app):
    s = app
    src = folder(s, "strip") / "in.jpg"
    exif = Image.Exif()
    exif[0x013B] = "ShareL e2e artist"  # Artist
    exif[0x010F] = "ShareL camera"  # Make
    pattern().save(src, exif=exif.tobytes(), quality=95)
    with Image.open(src) as im:
        assert im.getexif().get(0x013B) == "ShareL e2e artist"
    async with s.rootpilot() as client:
        call = caller(s, client)
        win = await open_tool(s, call, "Strip metadata", "Strip metadata")
        t = win["title"]
        before = ids(s)
        await click(call, "Add…", t, "button")
        await pick_file(s, call, before, src)
        await click(call, "Strip metadata", t, "button")
        time.sleep(0.8)
        # by default the originals are overwritten, after a confirmation: its button is the upper one
        _txt, data, _i, _e = await call("find", name="Strip metadata", role="button", window=t, limit=5)
        buttons = sorted((it for it in data["data"] if it.get("name") == "Strip metadata"),
                         key=lambda it: it["bounds"][1])
        assert len(buttons) == 2, buttons
        await call("click", ref=buttons[0]["ref"])

        def stripped():
            with Image.open(src) as im:
                return len(im.getexif()) == 0
        wait_for(stripped, what="metadata stripped from the file")
        await call("screenshot", window=t, max_width=1000)
        with Image.open(src) as im:
            assert im.size == (400, 300) and im.format == "JPEG"
        await close(call, s, win["id"])


# -- viewers and analysers (results shown in the window) -----------------------------------------------------


async def test_s15_metadata_viewer(app):
    s = app
    src = folder(s, "meta") / "meta.jpg"
    exif = Image.Exif()
    exif[0x013B] = "ShareL e2e artist"
    pattern().save(src, exif=exif.tobytes())
    async with s.rootpilot() as client:
        call = caller(s, client)
        win = await open_tool(s, call, "Metadata viewer", "Metadata")
        t = win["title"]
        _t, data, _i, _e = await call("find", name="ShareL e2e artist", window=t, limit=3)
        assert not found(data, "ShareL e2e artist")
        before = ids(s)
        await click(call, "Open…", t, "button")
        await pick_file(s, call, before, src)
        t = title_of(s, win["id"])
        assert "meta.jpg" in t, t
        text, data, _i, _e = await call("find", name="ShareL e2e artist", window=t, limit=3)
        await call("screenshot", window=t, max_width=1000)
        assert found(data, "ShareL e2e artist"), text[:600]
        await close(call, s, win["id"])


async def test_s15_analyze_image(app):
    s = app
    src = folder(s, "analyze") / "solid.png"
    Image.new("RGB", (120, 80), (0x33, 0x66, 0xCC)).save(src)
    async with s.rootpilot() as client:
        call = caller(s, client)
        win = await open_tool(s, call, "Analyze image", "Analyze image")
        t = win["title"]
        before = ids(s)
        await click(call, "Open…", t, "button")
        await pick_file(s, call, before, src)
        t = title_of(s, win["id"])
        for value in ("120 × 80 px", "#3366CC"):
            text, data, _i, _e = await wait_for_find(call, value, t)
            assert found(data, value), (value, text[:600])
        await call("screenshot", window=t, max_width=1000)
        await close(call, s, win["id"])


async def wait_for_find(call, name, window, tries: int = 30):
    """find() until ``name`` shows up (results are computed off the main loop)."""
    for _ in range(tries):
        res = await call("find", name=name, window=window, limit=3)
        if found(res[1], name):
            return res
        time.sleep(0.5)
    return res


async def test_s15_image_comparer(app):
    s = app
    d = folder(s, "compare")
    a, b = d / "a.png", d / "b.png"
    img = pattern((100, 100))
    img.save(a)
    img.paste((255, 0, 255), (10, 10, 20, 20))  # 100 of 10,000 pixels changed
    img.save(b)
    async with s.rootpilot() as client:
        call = caller(s, client)
        win = await open_tool(s, call, "Image comparer", "Image comparer")
        t = win["title"]
        for label, path in (("First image…", a), ("Second image…", b)):
            before = ids(s)
            await click(call, label, t, "button")
            await pick_file(s, call, before, path)
        text, data, _i, _e = await wait_for_find(call, "Similarity: 99.00%", t)
        await call("screenshot", window=t, max_width=1000)
        assert found(data, "Similarity: 99.00%"), text[:600]
        await close(call, s, win["id"])


async def test_s15_image_viewer(app):
    s = app
    src = folder(s, "viewer") / "view.png"
    Image.new("RGB", (300, 200), (0x33, 0x66, 0xCC)).save(src)
    async with s.rootpilot() as client:
        call = caller(s, client)
        before = ids(s)
        win = await open_tool(s, call, "Image viewer", "Image viewer")
        # like ShareX, the viewer opened from the menu asks for an image straight away
        await pick_file(s, call, before | {win["id"]}, src, "Open image")
        t = title_of(s, win["id"])
        assert t.startswith("view.png"), t
        text, data, _i, _e = await wait_for_find(call, "view.png — 300 × 200 — Fit", t)
        assert found(data, "view.png — 300 × 200 — Fit"), text[:600]
        # the picture is really on screen: a good part of the window is the image's colour
        x, y, w, h = s.ext("GetWindowDetails", f"uint64 {win['id']}")["frame"]
        shot = s.artifacts / "viewer-window.png"
        s.ext("Screenshot", "area", "uint64 0", str(x), str(y), str(w), str(h), "false", str(shot))
        px = pixels(shot)
        share = (np.abs(px - np.array([0x33, 0x66, 0xCC])).max(axis=2) < 8).mean()
        expected = 300 * 200 / (w * h)  # "Fit" never enlarges: the image at its own size
        assert 0.8 * expected < share < 1.2 * expected, (share, expected)
        await close(call, s, win["id"])


# -- single-image tools with their own outputs ----------------------------------------------------------------


async def test_s15_icon_converter(app):
    s = app
    src = folder(s, "icon") / "logo.png"
    pattern((64, 64)).save(src)
    async with s.rootpilot() as client:
        call = caller(s, client)
        win = await open_tool(s, call, "Icon converter", "Icon converter")
        t = win["title"]
        before = ids(s)
        await click(call, "Browse…", t, "button")
        await pick_file(s, call, before, src)
        before = ids(s)
        await click(call, "Convert…", t, "button")
        await accept_save(s, call, before)
        out = await wait_file(src.with_suffix(".ico"))
        with Image.open(out) as im:
            assert im.format == "ICO"
            assert {(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)} <= set(im.info["sizes"])
        await close(call, s, win["id"])


async def test_s15_image_combiner(app):
    s = app
    d = folder(s, "combine")
    a, b = d / "a.png", d / "b.png"
    Image.new("RGB", (100, 60), (220, 40, 40)).save(a)
    Image.new("RGB", (80, 40), (30, 170, 60)).save(b)
    async with s.rootpilot() as client:
        call = caller(s, client)
        win = await open_tool(s, call, "Image combiner", "Image combiner")
        t = win["title"]
        for path in (a, b):
            before = ids(s)
            await click(call, "Add…", t, "button")
            await pick_file(s, call, before, path)
        shots = set(s.screenshots())
        await click(call, "Combine", t, "button")  # to the after capture tasks: here, save to file
        out = wait_for(lambda: [p for p in s.screenshots() if p not in shots], what="combined image")[0]
        await wait_file(out)
        await call("screenshot", window=t, max_width=1000)
        px = pixels(out)
        assert px.shape[:2] == (100, 100), px.shape  # vertical, no space: 100 wide, 60 + 40 high
        assert (px[30, 50] == (220, 40, 40)).all() and (px[80, 40] == (30, 170, 60)).all()
        await close(call, s, win["id"])


async def test_s15_image_splitter(app):
    s = app
    d = folder(s, "split")
    src = d / "wide.png"
    pattern((200, 100)).save(src)
    async with s.rootpilot() as client:
        call = caller(s, client)
        win = await open_tool(s, call, "Image splitter", "Image splitter")
        t = win["title"]
        await click(call, "Image file", t)
        await call("type_text", text=str(src), window=t)
        await call("key", combo="Return", window=t)
        time.sleep(0.6)
        await click(call, "Split image", t, "button")
        tiles = wait_for(lambda: (lambda ts: ts if len(ts) == 4 else None)(
            sorted(p for p in d.iterdir() if p != src and p.suffix == ".png")), what="four tiles")
        await call("screenshot", window=t, max_width=1000)
        whole = pixels(src)
        got = []
        for p in tiles:
            px = pixels(p)
            assert px.shape[:2] == (50, 100), (p.name, px.shape)
            got.append(px)
        # together they are the image: each tile is one quadrant
        quads = [whole[:50, :100], whole[:50, 100:], whole[50:, :100], whole[50:, 100:]]
        assert all(any((g == q).all() for g in got) for q in quads)
        await close(call, s, win["id"])


async def test_s15_background_remover(app):
    s = app
    if not (MODELS / "models").is_dir():
        pytest.fail(f"the u2netp model is not cached in {MODELS} (run tests/test_bgremove.py once)")
    src = folder(s, "bgremove") / "subject.png"
    img = Image.new("RGB", (320, 320), (235, 235, 235))
    d = ImageDraw.Draw(img)
    d.ellipse((70, 60, 250, 260), fill=(200, 30, 40), outline=(60, 10, 10), width=6)
    d.rectangle((130, 140, 190, 200), fill=(30, 30, 160))
    img.save(src)
    async with s.rootpilot() as client:
        call = caller(s, client)
        win = await open_tool(s, call, "Background remover", "Background remover", timeout=60)
        t = win["title"]
        before = ids(s)
        await click(call, "Browse…", t, "button")
        await pick_file(s, call, before, src)
        await click(call, "Remove background", t, "button")
        text, data, _i, _e = await wait_for_find(call, "Background removed", t)
        assert found(data, "Background removed"), text[:600]
        await click(call, "Save", t, "button")
        out = await wait_file(src.with_name("subject-nobg.png"))
        await call("screenshot", window=t, max_width=1000)
        with Image.open(out) as im:
            alpha = np.asarray(im.convert("RGBA").getchannel("A"))
        assert all(alpha[y, x] < 30 for y, x in ((5, 5), (5, 314), (314, 5), (314, 314)))  # background gone
        assert alpha[160, 160] > 200  # the subject stays
        await close(call, s, win["id"])


async def test_s15_image_beautifier(app):
    s = app
    src = folder(s, "beautify") / "shot.png"
    pattern((300, 200)).save(src)
    async with s.rootpilot() as client:
        call = caller(s, client)
        win = await open_tool(s, call, "Image beautifier", "Image beautifier")
        t = win["title"]
        before = ids(s)
        await click(call, "Open image…", t, "button")
        await pick_file(s, call, before, src)
        t = title_of(s, win["id"])
        mtime = src.stat().st_mtime_ns
        time.sleep(1.0)  # the preview renders
        await click(call, "Save", t, "button")  # ShareX: Save writes back to the opened file
        wait_for(lambda: src.stat().st_mtime_ns != mtime, what="beautified file saved")
        await wait_file(src)
        await call("screenshot", window=t, max_width=1000)
        with Image.open(src) as im:
            w, h = im.size
        assert w > 300 + 100 and h > 200 + 100, (w, h)  # margin and padding around the screenshot
        await close(call, s, win["id"])


async def test_s15_image_effects(app):
    s = app
    src = folder(s, "effects") / "pic.png"
    pattern((200, 150)).save(src)
    async with s.rootpilot() as client:
        call = caller(s, client)
        win = await open_tool(s, call, "Image effects", "Image effects")
        t = win["title"]
        await click(call, "Load image", t)
        time.sleep(0.6)
        before = ids(s)
        await call("click", text="From file…", window=t)
        await pick_file(s, call, before, src)
        # a new preset with one effect: Adjustments → Inverse
        await click(call, "New", t, "button")
        time.sleep(0.4)
        for name in ("Add effect", "Adjustments", "Inverse"):
            await call("click", text=name, window=t)
            time.sleep(0.5)
        await call("screenshot", window=t, max_width=1200)
        before = ids(s)
        await click(call, "Save image…", t, "button")
        await accept_save(s, call, before)
        out = await wait_file(src.with_name("pic-effects.png"))
        a, b = pixels(src), pixels(out)
        assert a.shape == b.shape
        assert np.abs((255 - a) - b).max() <= 2, "the saved image is not the inverse of the source"
        await close(call, s, win["id"])


async def test_s15_image_editor_from_tools(app):
    s = app
    src = folder(s, "editor") / "ed.png"
    Image.new("RGB", (400, 300), (250, 250, 250)).save(src)
    async with s.rootpilot() as client:
        call = caller(s, client)
        main = next((w for w in s.windows() if w["title"] == "ShareL"), None)
        before = ids(s)
        # the menu entry opens ShareX's startup choice inside the main window, not a new window
        main_before = await open_tool_no_window(s, call, "Image editor")
        await call("screenshot", window="ShareL", max_width=1000)  # evidence: the startup choices
        await call("click", text="Open image file…", window="ShareL")
        await pick_file(s, call, main_before, src)
        ed = new_window(s, before | ({main["id"]} if main else set()), "ShareL image editor")
        t = ed["title"]
        assert "ed.png" in t, t
        canvas = await item(call, "Image canvas", t)
        cx, cy, cw, ch = canvas["bounds"]
        assert found((await call("find", name="Zoom 100%", window=t, limit=3))[1], "Zoom 100%")
        ox, oy = cx + (cw - 400) / 2, cy + (ch - 300) / 2  # the image, centred in the canvas at 100 %
        await call("key", combo="r", window=t)
        await call("mouse_drag", from_x=ox + 120, from_y=oy + 90, to_x=ox + 280, to_y=oy + 210)
        time.sleep(0.4)
        await call("key", combo="ctrl+s", window=t)
        wait_for(lambda: (pixels(src) != 250).any(), what="the edit saved into the file")
        await call("screenshot", window=t, max_width=1000)
        px = pixels(src)
        assert px.shape[:2] == (300, 400)
        changed = (np.abs(px - 250).max(axis=2) > 40)
        ys, xs = np.nonzero(changed)
        # a rectangle outline around the middle, nothing at the edges
        assert 80 < xs.min() < 160 and 240 < xs.max() < 320 and 60 < ys.min() < 120 and 180 < ys.max() < 240
        assert not changed[:40].any() and not changed[:, :60].any()
        await call("window_close", window=t)
        gone(s, ed["id"])


async def open_tool_no_window(s, call, label: str) -> set[int]:
    """Like open_tool, for an entry that shows a dialog in the main window. Returns the window ids before it."""
    main = next((w for w in s.windows() if w["title"] == "ShareL"), None)
    if main is None:
        s.sharel("-OpenMainWindow")
        main = wait_for(lambda: next((w for w in s.windows() if w["title"] == "ShareL"), None), what="main window")
    s.ext("Activate", f"uint64 {main['id']}")
    time.sleep(1.0)
    before = ids(s)
    await call("click", text="Tools", window="ShareL")
    time.sleep(0.7)
    it = await item(call, label, "ShareL")
    await call("click", ref=it["ref"])
    time.sleep(1.0)
    text, data, _i, _e = await call("find", name="Open image file…", window="ShareL", limit=3)
    assert found(data, "Open image file…"), text[:500]
    return before
