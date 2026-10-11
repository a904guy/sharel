"""Image effect base class, registry and presets (ShareX.ImageEffectsLib).

An effect is a dataclass whose fields are its parameters, named after ShareX's properties in
snake_case (ShareX ``Value`` -> ``value``, ``UseGradient`` -> ``use_gradient``)::

    @effect("Adjustments")
    @dataclass
    class Brightness(ImageEffect):
        value: int = param(0, label="Value", min=-100, max=100)

        def apply(self, img):
            ...

``apply`` receives and returns an RGBA PIL image (it may return a different size). Field metadata
(``label``, ``min``, ``max``, ``step``, ``kind`` = 'color' | 'file' | 'font' | 'text' | 'choice',
``choices``) drives the generated parameter editor in the Image Effects window.

Presets serialize to ``{"name": ..., "effects": [{"type": "Brightness", "enabled": true, ...}]}``;
:mod:`sharel.effects.sxie` converts to and from ShareX's ``.sxie`` packages.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, ClassVar

from PIL import Image

from ..core.serialize import from_jsonable, to_jsonable

CATEGORIES = ("Adjustments", "Drawings", "Filters", "Manipulations")

EFFECTS: dict[str, type[ImageEffect]] = {}


def param(default: Any = dataclasses.MISSING, *, label: str = "", min: float | None = None,
          max: float | None = None, step: float | None = None, kind: str = "", choices: list[str] | None = None,
          help: str = "", default_factory: Any = dataclasses.MISSING) -> Any:
    md = {"label": label, "min": min, "max": max, "step": step, "kind": kind, "choices": choices, "help": help}
    if default_factory is not dataclasses.MISSING:
        return field(default_factory=default_factory, metadata=md)
    return field(default=default, metadata=md)


@dataclass
class ImageEffect:
    category: ClassVar[str] = ""
    display_name: ClassVar[str] = ""
    enabled: bool = param(True, label="Enabled")

    def apply(self, img: Image.Image) -> Image.Image:
        raise NotImplementedError

    def __call__(self, img: Image.Image) -> Image.Image:
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        return self.apply(img) if self.enabled else img

    @property
    def type_name(self) -> str:
        return type(self).__name__

    def params(self) -> list[dataclasses.Field]:
        return [f for f in dataclasses.fields(self) if f.name != "enabled"]

    def to_jsonable(self) -> dict:
        d = to_jsonable(self)
        return {"type": self.type_name, **d}

    @staticmethod
    def from_jsonable(d: dict) -> ImageEffect:
        cls = EFFECTS.get(d.get("type", ""))
        if cls is None:
            raise KeyError(f"unknown image effect {d.get('type')!r}")
        return from_jsonable(cls, {k: v for k, v in d.items() if k != "type"})

    def describe(self) -> str:
        return self.display_name or self.type_name


def effect(category: str, display_name: str = ""):
    if category not in CATEGORIES:
        raise ValueError(category)

    def deco(cls):
        cls.category = category
        cls.display_name = display_name or cls.__name__
        EFFECTS[cls.__name__] = cls
        return cls
    return deco


def load_all() -> dict[str, type[ImageEffect]]:
    import importlib
    for mod in ("adjustments", "drawings", "filters", "manipulations"):
        importlib.import_module(f"{__package__}.{mod}")
    return EFFECTS


@dataclass
class ImageEffectPreset:
    name: str = "Preset"
    effects: list[ImageEffect] = field(default_factory=list)

    def apply(self, img: Image.Image) -> Image.Image:
        out = img.convert("RGBA") if img.mode != "RGBA" else img.copy()
        for e in self.effects:
            out = e(out)
        return out

    def to_jsonable(self) -> dict:
        return {"name": self.name, "effects": [e.to_jsonable() for e in self.effects]}

    @staticmethod
    def from_jsonable(d: dict) -> ImageEffectPreset:
        load_all()
        effects = []
        for e in d.get("effects", []):
            try:
                effects.append(ImageEffect.from_jsonable(e))
            except KeyError:
                continue
        return ImageEffectPreset(d.get("name", "Preset"), effects)
