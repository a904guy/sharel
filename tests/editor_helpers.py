"""Shared helpers for the editor tests: synthetic pointer input and pixel probes."""
from __future__ import annotations

import numpy as np
from PIL import Image

from sharel.core.config import RegionCaptureOptions
from sharel.core.enums import RegionCaptureMode, ShapeType
from sharel.editor.manager import BUTTON_LEFT, Callbacks, Mod, ShapeManager

RED = (240, 60, 60)  # AnnotationOptions primary colour #FFF03C3C
WHITE = (255, 255, 255)


def blank(w: int = 300, h: int = 200, color=(255, 255, 255, 255)) -> Image.Image:
    return Image.new("RGBA", (w, h), color)


def noise(w: int = 200, h: int = 150, seed: int = 1) -> Image.Image:
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 256, (h, w, 4), dtype=np.uint8)
    arr[..., 3] = 255
    return Image.fromarray(arr, "RGBA")


def make(image=None, mode=RegionCaptureMode.Editor, tool: ShapeType | None = None, shadow: bool = False,
         callbacks: Callbacks | None = None, options: RegionCaptureOptions | None = None, **anno) -> ShapeManager:
    opts = options or RegionCaptureOptions()
    opts.annotation_options.shadow = shadow
    for k, v in anno.items():
        setattr(opts.annotation_options, k, v)
    m = ShapeManager(image if image is not None else blank(), mode, opts, callbacks or Callbacks())
    if tool is not None:
        m.current_tool = tool
    return m


def drag(m: ShapeManager, a, b, mods: int = Mod.NONE, steps: int = 5, button: int = BUTTON_LEFT):
    m.motion(*a, mods)
    m.press(a[0], a[1], button, mods)
    for i in range(1, steps + 1):
        m.motion(a[0] + (b[0] - a[0]) * i / steps, a[1] + (b[1] - a[1]) * i / steps, mods)
    return m.release(b[0], b[1], button, mods)


def click(m: ShapeManager, p, mods: int = Mod.NONE, button: int = BUTTON_LEFT):
    m.motion(*p, mods)
    r = m.press(p[0], p[1], button, mods)
    return m.release(p[0], p[1], button, mods) or r


def px(img: Image.Image, x: int, y: int) -> tuple[int, int, int]:
    return img.getpixel((x, y))[:3]


def close(c1, c2, tol: int = 40) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(c1, c2, strict=False))


def count_color(img: Image.Image, box, color, tol: int = 40) -> int:
    a = np.asarray(img.convert("RGB"), dtype=np.int16)[box[1]:box[3], box[0]:box[2]]
    return int(np.all(np.abs(a - np.array(color)) <= tol, axis=2).sum())


def state(m: ShapeManager):
    """A comparable signature of everything undo has to restore."""
    out = []
    for s in m.shapes:
        d = {"type": s.shape_type.value, "rect": s.rect.as_tuple()}
        for k in ("points", "text", "border_size", "border_color", "fill_color", "tail_position", "number",
                  "blur_radius", "corner_radius", "center_node_active"):
            if hasattr(s, k):
                v = getattr(s, k)
                d[k] = list(v) if isinstance(v, list) else v
        out.append(d)
    return out, m.canvas.size, m.canvas.tobytes()
