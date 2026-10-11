"""Pull one frame from a PipeWire node through GStreamer's pipewiresrc."""
from __future__ import annotations

import threading

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst
from PIL import Image

_ready = False
_lock = threading.Lock()


def gst_init() -> None:
    global _ready
    with _lock:
        if not _ready:
            Gst.init(None)
            _ready = True


def pull_frame(node_id: int, timeout_s: float = 5.0) -> Image.Image:
    gst_init()
    pipe = Gst.parse_launch(
        f"pipewiresrc path={node_id} always-copy=true ! videoconvert ! video/x-raw,format=RGBA ! "
        "appsink name=sink max-buffers=1 drop=true sync=false")
    sink = pipe.get_by_name("sink")
    pipe.set_state(Gst.State.PLAYING)
    try:
        sample = sink.emit("try-pull-sample", int(timeout_s * Gst.SECOND))
        if sample is None:
            from .backends import CaptureError
            raise CaptureError("no frame from the PipeWire stream within the timeout")
        caps = sample.get_caps().get_structure(0)
        fw, fh = caps.get_value("width"), caps.get_value("height")
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            from .backends import CaptureError
            raise CaptureError("could not map the frame buffer")
        try:
            stride = len(info.data) // fh
            img = Image.frombuffer("RGBA", (fw, fh), bytes(info.data), "raw", "RGBA", stride, 1).copy()
        finally:
            buf.unmap(info)
        # ScreenCast frames carry no meaningful alpha for the desktop: make it opaque.
        img.putalpha(255)
        return img
    finally:
        pipe.set_state(Gst.State.NULL)
