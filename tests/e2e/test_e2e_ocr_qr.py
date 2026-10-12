"""Scenario 9: OCR of a gnome-text-editor capture with known text; show a QR code, then decode it
from the screen."""
from __future__ import annotations

import os
import signal
import time

import pytest
from conftest import wait_for

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

KNOWN = "ShareL recognises 12345 words"
QR_TEXT = "https://example.com/sharel-qr-e2e?id=42"




@pytest.fixture(scope="module")
def app(session):
    session.start_app()
    yield session
    session.stop_app()


def clip_text(s) -> str:
    for mime in ("text/plain;charset=utf-8", "text/plain"):
        data = s.clipboard(mime)
        if data:
            return data.decode("utf-8", errors="replace")
    return ""


async def test_s9_ocr_of_text_editor(app):
    s = app
    async with s.rootpilot() as client:
        call = s.call(client)
        await call("app_launch", app="org.gnome.TextEditor", args=["--standalone"])
        ed = wait_for(lambda: next((w for w in s.windows() if w["wm_class"] == "org.gnome.TextEditor"), None),
                      what="text editor")
        s.ext("Activate", f"uint64 {ed['id']}")
        time.sleep(0.5)
        await call("type_text", text=KNOWN, window=ed["title"])
        time.sleep(0.5)
        assert KNOWN.split()[0] not in clip_text(s)
        x, y, w, _h = s.ext("GetWindowDetails", f"uint64 {ed['id']}")["frame"]
        s.sharel("-OCR")
        overlays = wait_for(lambda: [o for o in s.windows() if "region capture" in o["title"]], what="overlay")
        s.ext("Activate", f"uint64 {next(o for o in overlays if o['monitor'] == 0)['id']}")
        time.sleep(0.6)
        # the editor's first line of text, below its header bar
        await call("mouse_drag", from_x=x + 5, from_y=y + 50, to_x=x + w - 5, to_y=y + 120, restore_focus=False)
        ocr = s.wait_window("OCR", timeout=30)
        time.sleep(3.0)  # recognition runs off the main loop
        await call("screenshot", window=ocr["title"], max_width=1000)
        _t, data, _i, _e = await call("find", name="Copy all", role="button", window=ocr["title"], limit=3)
        await call("click", ref=next(it["ref"] for it in data["data"] if it.get("name") == "Copy all"))
        text = wait_for(lambda: clip_text(s) if "12345" in clip_text(s) else "", what="OCR text on clipboard")
        assert "ShareL" in text and "12345" in text, text
        (s.artifacts / "ocr.txt").write_text(text)
        await call("window_close", window=ocr["title"])
        os.kill(ed["pid"], signal.SIGTERM)


async def test_s9_qr_show_then_decode_from_screen(app):
    s = app
    s.sharel("-QRCode")
    qr = s.wait_window("QR code")
    async with s.rootpilot() as client:
        call = s.call(client)
        _t, data, _i, _e = await call("find", name="Text to encode", window=qr["title"], limit=3)
        ref = next(it["ref"] for it in data["data"] if it.get("name") == "Text to encode")
        await call("click", ref=ref)
        await call("key", combo="ctrl+a", window=qr["title"])
        await call("type_text", text=QR_TEXT, window=qr["title"])
        time.sleep(1.5)  # the code regenerates as you type
        await call("screenshot", window=qr["title"], max_width=1000)  # evidence: the QR code on screen
        assert QR_TEXT not in clip_text(s)
        s.sharel("-QRCodeDecodeFromScreen")
        res = s.wait_window("QR code scan", timeout=30)
        await call("screenshot", window=res["title"], max_width=1000)
        _t, data, _i, _e = await call("find", name="Copy all", role="button", window=res["title"], limit=3)
        await call("click", ref=next(it["ref"] for it in data["data"] if it.get("name") == "Copy all"))
        text = wait_for(lambda: clip_text(s) if QR_TEXT in clip_text(s) else "", what="decoded text on clipboard")
        assert text.strip() == QR_TEXT, text
