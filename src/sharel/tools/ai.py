"""Analyze image with an AI provider: ShareX 18's AIOptions and its four providers.

``AIOptions`` lives in UploadersConfig under ``SETTINGS_KEY``, so its API keys are keyring secrets like
every uploader's. ``describe(img, opts)`` sends the image and the prompt and returns the provider's text:

- **OpenAI**: the Responses API (``POST {base}/v1/responses``), image as a JPEG data URI, ``store: false``.
  ``openai_custom_url`` points it at any compatible server.
- **OpenAILegacy**: Chat Completions (``POST {base}/v1/chat/completions``): what most OpenAI-compatible
  servers (local LLM runners, proxies) speak.
- **Gemini**: ``generateContent`` with the PNG inline.
- **OpenRouter**: its Chat Completions endpoint, PNG data URI, ``max_tokens`` 1024.

Nothing is sent unless the chosen provider has an API key (``opts.configured``); the Analyze image window
keeps to its local analysis then.
"""
from __future__ import annotations

import base64
import io
from dataclasses import dataclass

import requests
from PIL import Image

from ..core.enums import StrEnum
from ..uploaders.registry import setting

SETTINGS_KEY = "tool/AnalyzeImage"
OPENAI_URL = "https://api.openai.com"
GEMINI_URL = "https://generativelanguage.googleapis.com"
OPENROUTER_URL = "https://openrouter.ai/api"
DEFAULT_PROMPT = "What is in this image?"


class AIProvider(StrEnum):
    OpenAI = "OpenAI"
    Gemini = "Gemini"
    OpenRouter = "OpenRouter"
    OpenAILegacy = "OpenAILegacy"


PROVIDER_TITLES = {AIProvider.OpenAI: "OpenAI (Responses API)", AIProvider.Gemini: "Google Gemini",
                   AIProvider.OpenRouter: "OpenRouter",
                   AIProvider.OpenAILegacy: "OpenAI-compatible (Chat Completions)"}


@dataclass
class AIOptions:
    provider: AIProvider = AIProvider.OpenAI
    openai_api_key: str = setting("", secret=True, label="OpenAI API key",
                                  help="Also used by OpenAI-compatible servers")
    openai_model: str = setting("gpt-5-mini", label="OpenAI model")
    openai_custom_url: str = setting("", label="OpenAI custom URL",
                                     help="Base URL of an OpenAI-compatible server (empty: api.openai.com)")
    openai_reasoning_effort: str = setting("minimal", label="Reasoning effort",
                                           choices=["none", "minimal", "low", "medium", "high"])
    openai_verbosity: str = setting("medium", label="Verbosity", choices=["low", "medium", "high"])
    gemini_api_key: str = setting("", secret=True, label="Gemini API key")
    gemini_model: str = setting("gemini-1.5-flash-latest", label="Gemini model")
    openrouter_api_key: str = setting("", secret=True, label="OpenRouter API key")
    openrouter_model: str = setting("google/gemini-flash-1.5", label="OpenRouter model")
    input: str = setting(DEFAULT_PROMPT, label="Prompt")
    auto_copy_result: bool = setting(False, label="Copy the description to the clipboard")

    @property
    def api_key(self) -> str:
        return {AIProvider.OpenAI: self.openai_api_key, AIProvider.OpenAILegacy: self.openai_api_key,
                AIProvider.Gemini: self.gemini_api_key, AIProvider.OpenRouter: self.openrouter_api_key
                }.get(self.provider, "").strip()

    @property
    def configured(self) -> bool:
        """ShareX's HasAPIKey: the chosen provider has a key."""
        return bool(self.api_key)


class AIError(Exception):
    pass


def options(app) -> AIOptions:
    return app.store.uploaders.settings_for(SETTINGS_KEY, AIOptions)


def _encoded(img: Image.Image, fmt: str) -> str:
    buf = io.BytesIO()
    if fmt == "JPEG":
        img.convert("RGB").save(buf, "JPEG", quality=90)
    else:
        img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _combine(base: str, path: str) -> str:
    base = base.rstrip("/")
    return base if base.endswith(path) else base + path


def _check(resp: requests.Response, who: str) -> dict:
    if not 200 <= resp.status_code < 300:
        raise AIError(f"{who} API error: HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        return resp.json()
    except ValueError as e:
        raise AIError(f"{who}: the response is not JSON: {resp.text[:200]}") from e


def build_request(img: Image.Image, opts: AIOptions, prompt: str | None = None) -> tuple[str, dict, dict]:
    """(url, headers, JSON body) of the provider's analyse request."""
    prompt = prompt or opts.input or DEFAULT_PROMPT
    key = opts.api_key
    if opts.provider == AIProvider.OpenAI:
        effort = opts.openai_reasoning_effort or "minimal"
        if opts.openai_model.lower().startswith("gpt-5.") and effort == "minimal":
            effort = "none"  # gpt-5.x models take "none" where gpt-5 took "minimal"
        body = {"model": opts.openai_model, "reasoning": {"effort": effort},
                "input": [{"role": "user", "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": f"data:image/jpeg;base64,{_encoded(img, 'JPEG')}"}]}],
                "text": {"verbosity": opts.openai_verbosity or "medium"}, "store": False}
        return (_combine(opts.openai_custom_url or OPENAI_URL, "/v1/responses"),
                {"Authorization": f"Bearer {key}"}, body)
    if opts.provider == AIProvider.OpenAILegacy:
        body = {"model": opts.openai_model, "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{_encoded(img, 'JPEG')}"}}]}]}
        return (_combine(opts.openai_custom_url or OPENAI_URL, "/v1/chat/completions"),
                {"Authorization": f"Bearer {key}"}, body)
    if opts.provider == AIProvider.Gemini:
        body = {"contents": [{"parts": [{"text": prompt},
                                        {"inline_data": {"mime_type": "image/png", "data": _encoded(img, "PNG")}}]}]}
        return (f"{GEMINI_URL}/v1beta/models/{opts.gemini_model}:generateContent?key={key}", {}, body)
    if opts.provider == AIProvider.OpenRouter:
        body = {"model": opts.openrouter_model, "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_encoded(img, 'PNG')}"}}]}],
            "max_tokens": 1024}
        return f"{OPENROUTER_URL}/v1/chat/completions", {"Authorization": f"Bearer {key}"}, body
    raise AIError(f"unknown AI provider {opts.provider}")


def parse_response(provider: AIProvider, data: dict) -> str:
    try:
        if provider == AIProvider.OpenAI:
            for out in data.get("output") or []:
                for c in out.get("content") or []:
                    if c.get("text"):
                        return c["text"]
            return ""
        if provider in (AIProvider.OpenAILegacy, AIProvider.OpenRouter):
            return (data["choices"][0]["message"].get("content") or "") if data.get("choices") else ""
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as e:
        raise AIError(f"{provider.value}: unexpected response: {str(data)[:200]}") from e


def describe(img: Image.Image, opts: AIOptions, prompt: str | None = None, *, proxies: dict | None = None,
             timeout: float = 120.0, session: requests.Session | None = None) -> str:
    """The provider's description of ``img`` (raises AIError)."""
    if not opts.configured:
        raise AIError(f"no API key for {PROVIDER_TITLES[opts.provider]}")
    url, headers, body = build_request(img, opts, prompt)
    s = session or requests.Session()
    try:
        resp = s.post(url, json=body, headers=headers, proxies=proxies, timeout=timeout)
    except requests.RequestException as e:
        raise AIError(f"{opts.provider.value}: {e}") from e
    return parse_response(opts.provider, _check(resp, opts.provider.value))


def check_connection(opts: AIOptions, *, proxies: dict | None = None, timeout: float = 30.0) -> tuple[bool, str]:
    """ShareX's Test connection: list the provider's models with the key."""
    if not opts.configured:
        return False, f"Missing {PROVIDER_TITLES[opts.provider]} API key"
    key = opts.api_key
    if opts.provider in (AIProvider.OpenAI, AIProvider.OpenAILegacy):
        url, headers = _combine(opts.openai_custom_url or OPENAI_URL, "/v1/models"), {"Authorization": f"Bearer {key}"}
    elif opts.provider == AIProvider.Gemini:
        url, headers = f"{GEMINI_URL}/v1beta/models?key={key}", {}
    else:
        url, headers = f"{OPENROUTER_URL}/v1/models", {"Authorization": f"Bearer {key}"}
    try:
        resp = requests.get(url, headers=headers, proxies=proxies, timeout=timeout)
    except requests.RequestException as e:
        return False, str(e)
    if 200 <= resp.status_code < 300:
        return True, "Connection OK"
    return False, f"HTTP {resp.status_code} {resp.reason}: {resp.text[:200]}"
