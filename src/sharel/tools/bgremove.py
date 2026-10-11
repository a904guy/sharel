"""Background remover (ShareX "Background remover") using rembg with the small ``u2netp`` model.

rembg (and onnxruntime) are optional: they are imported only when a removal is requested, so ShareL starts
without them (``pip install sharel[rembg]``). The model (~4.7 MB) is downloaded by rembg on first use into the
model directory.

API
---
``is_available() -> bool``                     rembg + onnxruntime can be imported.
``default_model_dir() -> Path``               ``$U2NET_HOME`` if set, else ``$XDG_CACHE_HOME/ShareL/rembg``
                                              (``~/.cache/ShareL/rembg``).
``remove_background(img, model="u2netp", alpha_matting=False, model_dir=None, only_mask=False) -> RGBA image``
    the subject with a transparent background (or the 8-bit mask with ``only_mask``). Sessions are cached per
    model/directory, so repeated calls don't reload the network.
``remove_background_file(src, dst=None, ...) -> Path``  writes ``<stem>-nobg.png`` next to ``src`` by default.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from PIL import Image

__all__ = ["BackgroundRemoverUnavailable", "default_model_dir", "is_available", "remove_background",
           "remove_background_file"]

DEFAULT_MODEL = "u2netp"
_sessions: dict[tuple[str, str], object] = {}
_lock = threading.Lock()


class BackgroundRemoverUnavailable(RuntimeError):
    """rembg / onnxruntime are not installed."""


def is_available() -> bool:
    try:
        import onnxruntime  # noqa: F401
        import rembg  # noqa: F401
    except ImportError:
        return False
    return True


def default_model_dir() -> Path:
    env = os.environ.get("U2NET_HOME")
    if env:
        return Path(env).expanduser()
    cache = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(cache) / "ShareL" / "rembg"


def _session(model: str, model_dir: Path):
    key = (model, str(model_dir))
    with _lock:
        if key in _sessions:
            return _sessions[key]
        try:
            from rembg import new_session
        except ImportError as e:
            raise BackgroundRemoverUnavailable("the background remover needs the 'rembg' and 'onnxruntime' "
                                               "packages") from e
        model_dir.mkdir(parents=True, exist_ok=True)
        # rembg reads U2NET_HOME when it resolves/downloads the model; point it at our directory for this call
        old = os.environ.get("U2NET_HOME")
        os.environ["U2NET_HOME"] = str(model_dir)
        try:
            sess = new_session(model)
        finally:
            if old is None:
                os.environ.pop("U2NET_HOME", None)
            else:
                os.environ["U2NET_HOME"] = old
        _sessions[key] = sess
        return sess


def remove_background(img: Image.Image, model: str = DEFAULT_MODEL, alpha_matting: bool = False,
                      model_dir: str | os.PathLike | None = None, only_mask: bool = False) -> Image.Image:
    sess = _session(model, Path(model_dir) if model_dir else default_model_dir())
    from rembg import remove

    src = img.convert("RGB") if img.mode not in ("RGB", "RGBA") else img
    out = remove(src, session=sess, alpha_matting=alpha_matting, only_mask=only_mask)
    if not isinstance(out, Image.Image):  # rembg returns the input's type; be defensive
        import io

        out = Image.open(io.BytesIO(out)) if isinstance(out, (bytes, bytearray)) else Image.fromarray(out)
    return out.convert("L") if only_mask else out.convert("RGBA")


def remove_background_file(src: str | os.PathLike, dst: str | os.PathLike | None = None,
                           model: str = DEFAULT_MODEL, alpha_matting: bool = False,
                           model_dir: str | os.PathLike | None = None) -> Path:
    s = Path(src)
    d = Path(dst) if dst else s.with_name(f"{s.stem}-nobg.png")
    with Image.open(s) as im:
        im.load()
        res = remove_background(im, model, alpha_matting, model_dir)
    d.parent.mkdir(parents=True, exist_ok=True)
    res.save(d, "PNG")
    return d
