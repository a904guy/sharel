"""Analyze image with an AI provider (ShareX 18's AIOptions), against a local HTTP server only."""
from __future__ import annotations

import base64
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image
from uihelpers import run_app

from sharel.tools import ai
from sharel.tools.ai import AIOptions, AIProvider
from sharel.uploaders import config as ucfg

REPLIES = {
    "/v1/responses": {"id": "r1", "output": [{"type": "reasoning", "content": []},
                                             {"type": "message", "content": [{"type": "output_text",
                                                                              "text": "A red square."}]}]},
    "/v1/chat/completions": {"choices": [{"message": {"role": "assistant", "content": "A red square (chat)."}}]},
    "/api/v1/chat/completions": {"choices": [{"message": {"content": "A red square (router)."}}]},
    "/v1beta/models/gemini-test:generateContent": {"candidates": [{"content": {"parts": [{"text": "Gemini: red."}]}}]},
    "/v1/models": {"data": [{"id": "m"}]},
}


class Server:
    def __init__(self, status: int = 200) -> None:
        self.requests: list[dict] = []
        srv = self

        class H(BaseHTTPRequestHandler):
            def _reply(self, body: bytes | None):
                path = self.path.split("?")[0]
                srv.requests.append({"method": self.command, "path": self.path, "headers": dict(self.headers),
                                     "body": json.loads(body) if body else None})
                if srv.status != 200:
                    self.send_response(srv.status)
                    self.end_headers()
                    self.wfile.write(b'{"error": {"message": "invalid api key"}}')
                    return
                out = json.dumps(REPLIES[path]).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def do_POST(self):
                self._reply(self.rfile.read(int(self.headers["Content-Length"])))

            def do_GET(self):
                self._reply(None)

            def log_message(self, *a):
                pass

        self.status = status
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server(monkeypatch):
    s = Server()
    monkeypatch.setattr(ai, "OPENAI_URL", s.url + "/nowhere")  # the custom URL must win over it
    monkeypatch.setattr(ai, "GEMINI_URL", s.url)
    monkeypatch.setattr(ai, "OPENROUTER_URL", s.url + "/api")
    yield s
    s.close()


RED = Image.new("RGB", (40, 30), (255, 0, 0))


def _decoded(data_uri_or_b64: str) -> Image.Image:
    raw = data_uri_or_b64.split(",", 1)[1] if data_uri_or_b64.startswith("data:") else data_uri_or_b64
    return Image.open(io.BytesIO(base64.b64decode(raw)))


def test_defaults_match_sharex_and_nothing_is_configured():
    o = AIOptions()
    assert o.provider == AIProvider.OpenAI and o.openai_model == "gpt-5-mini"
    assert (o.openai_reasoning_effort, o.openai_verbosity) == ("minimal", "medium")
    assert o.gemini_model == "gemini-1.5-flash-latest" and o.openrouter_model == "google/gemini-flash-1.5"
    assert o.input == "What is in this image?" and not o.auto_copy_result
    assert not o.configured
    with pytest.raises(ai.AIError, match="no API key"):
        ai.describe(RED, o)
    o.openai_api_key = "   "
    assert not o.configured
    o.provider = AIProvider.Gemini
    o.openai_api_key = "k"
    assert not o.configured  # the key must belong to the chosen provider
    o.gemini_api_key = "g"
    assert o.configured


def test_openai_responses_request_and_reply(server):
    o = AIOptions(openai_api_key="sk-test", openai_custom_url=server.url, openai_verbosity="low")
    assert ai.describe(RED, o, "Describe it") == "A red square."
    (req,) = server.requests
    assert req["method"] == "POST" and req["path"] == "/v1/responses"
    assert req["headers"]["Authorization"] == "Bearer sk-test"
    b = req["body"]
    assert b["model"] == "gpt-5-mini" and b["reasoning"] == {"effort": "minimal"} and b["store"] is False
    assert b["text"] == {"verbosity": "low"}
    text, image = b["input"][0]["content"]
    assert b["input"][0]["role"] == "user" and text == {"type": "input_text", "text": "Describe it"}
    assert image["type"] == "input_image" and image["image_url"].startswith("data:image/jpeg;base64,")
    sent = _decoded(image["image_url"])
    assert sent.format == "JPEG" and sent.size == (40, 30)
    r, g, b_ = sent.convert("RGB").getpixel((20, 15))
    assert r > 240 and g < 15 and b_ < 15


def test_openai_gpt5_point_models_take_none_and_full_custom_url_is_kept(server):
    o = AIOptions(openai_api_key="k", openai_model="gpt-5.1", openai_custom_url=server.url + "/v1/responses")
    ai.describe(RED, o)
    assert server.requests[-1]["path"] == "/v1/responses"  # not /v1/responses/v1/responses
    assert server.requests[-1]["body"]["reasoning"] == {"effort": "none"}
    o.openai_reasoning_effort = "high"
    ai.describe(RED, o)
    assert server.requests[-1]["body"]["reasoning"] == {"effort": "high"}


def test_openai_compatible_chat_completions(server):
    o = AIOptions(provider=AIProvider.OpenAILegacy, openai_api_key="local", openai_model="llava",
                  openai_custom_url=server.url + "/")
    assert ai.describe(RED, o) == "A red square (chat)."
    (req,) = server.requests
    assert req["path"] == "/v1/chat/completions" and req["headers"]["Authorization"] == "Bearer local"
    msg = req["body"]["messages"][0]
    assert req["body"]["model"] == "llava" and msg["role"] == "user"
    assert msg["content"][0] == {"type": "text", "text": "What is in this image?"}
    assert _decoded(msg["content"][1]["image_url"]["url"]).size == (40, 30)


def test_gemini_and_openrouter(server):
    g = AIOptions(provider=AIProvider.Gemini, gemini_api_key="gk", gemini_model="gemini-test")
    assert ai.describe(RED, g) == "Gemini: red."
    req = server.requests[-1]
    assert req["path"] == "/v1beta/models/gemini-test:generateContent?key=gk"
    parts = req["body"]["contents"][0]["parts"]
    assert parts[0] == {"text": "What is in this image?"} and parts[1]["inline_data"]["mime_type"] == "image/png"
    assert _decoded(parts[1]["inline_data"]["data"]).format == "PNG"
    r = AIOptions(provider=AIProvider.OpenRouter, openrouter_api_key="ok")
    assert ai.describe(RED, r) == "A red square (router)."
    req = server.requests[-1]
    assert req["path"] == "/api/v1/chat/completions" and req["headers"]["Authorization"] == "Bearer ok"
    assert req["body"]["model"] == "google/gemini-flash-1.5" and req["body"]["max_tokens"] == 1024


def test_http_errors_and_connection_test(server):
    o = AIOptions(openai_api_key="bad", openai_custom_url=server.url)
    assert ai.check_connection(o) == (True, "Connection OK")
    assert server.requests[-1]["path"] == "/v1/models" and server.requests[-1]["method"] == "GET"
    server.status = 401
    with pytest.raises(ai.AIError, match=r"HTTP 401.*invalid api key"):
        ai.describe(RED, o)
    ok, msg = ai.check_connection(o)
    assert not ok and msg.startswith("HTTP 401")
    assert ai.check_connection(AIOptions()) == (False, "Missing OpenAI (Responses API) API key")


def test_api_keys_go_to_the_keyring(monkeypatch, store):
    kept = {}

    class FakeSecret:
        COLLECTION_DEFAULT = "default"

        @staticmethod
        def password_store_sync(_schema, attrs, _coll, _label, value, _cancel):
            kept[(attrs["service"], attrs["field"])] = value

        @staticmethod
        def password_lookup_sync(_schema, attrs, _cancel):
            return kept.get((attrs["service"], attrs["field"]))
    monkeypatch.setattr(ucfg, "_secret", lambda: (FakeSecret, object()))
    o = store.uploaders.settings_for(ai.SETTINGS_KEY, AIOptions)
    o.openai_api_key, o.gemini_api_key, o.openai_model = "sk-secret", "g-secret", "gpt-x"
    store.save_uploaders()
    text = (store.directory / "UploadersConfig.json").read_text()
    assert "sk-secret" not in text and "g-secret" not in text
    on_disk = json.loads(text)["services"][ai.SETTINGS_KEY]
    assert on_disk["openai_api_key"] == ucfg.SECRET_MARK and on_disk["openai_model"] == "gpt-x"
    assert kept[(ai.SETTINGS_KEY, "openai_api_key")] == "sk-secret"
    from sharel.core.config import ConfigStore
    again = ConfigStore(store.directory).uploaders.settings_for(ai.SETTINGS_KEY, AIOptions)
    assert (again.openai_api_key, again.gemini_api_key) == ("sk-secret", "g-secret")


def _wait(cond, steps=300, dt=0.05):
    for _ in range(steps):
        if cond():
            return
        yield dt
    raise AssertionError("condition not reached")


def test_window_without_a_key_sends_nothing_and_keeps_local_analysis(server):
    def body(app):
        from sharel.ui.tools import analyze
        win = analyze.open_analyze(app, RED.convert("RGBA"))
        yield from _wait(lambda: win.done.is_set() and win.ai_done.is_set())
        assert win.analysis.value("General", "Dimensions") == "40 × 30 px"
        assert win.ai_off.get_visible() and not win.ai_prompt.get_visible() and win.ai_text == ""
        win.describe()  # even asked directly
        yield 0.3
        return True
    assert run_app(body)
    assert server.requests == []


def test_window_describes_with_the_configured_provider(server, monkeypatch):
    copied = []
    monkeypatch.setattr("sharel.core.clipboard.set_text", lambda t: copied.append(t) or "t")

    def body(app):
        from sharel.ui.tools import analyze
        o = ai.options(app)
        o.provider, o.openai_api_key, o.openai_custom_url = AIProvider.OpenAILegacy, "k", server.url
        o.auto_copy_result = True
        win = analyze.open_analyze(app, RED.convert("RGBA"))
        yield from _wait(lambda: win.done.is_set() and win.ai_done.is_set())
        assert win.ai_text == "A red square (chat)." and win.ai_result.get_text() == "A red square (chat)."
        assert not win.ai_off.get_visible() and win.ai_prompt.get_visible()
        assert copied == ["A red square (chat)."]  # AutoCopyResult
        assert win.analysis.value("General", "Dimensions") == "40 × 30 px"  # local analysis still there
        # a new prompt, by hand
        win.ai_prompt.set_text("Which colour?")
        win.ai_button.emit("clicked")
        yield from _wait(lambda: len(server.requests) == 2 and win.ai_done.is_set())
        assert server.requests[-1]["body"]["messages"][0]["content"][0]["text"] == "Which colour?"
        win.copy_button.emit("clicked")
        assert copied[-1].startswith("[AI description]\nA red square (chat).\n\n[General]")
        # a provider error is shown, not raised
        server.status = 500
        win.ai_button.emit("clicked")
        yield from _wait(lambda: len(server.requests) == 3 and win.ai_done.is_set())
        assert win.ai_text == "" and "HTTP 500" in win.ai_result.get_text()
        return True
    assert run_app(body)


def test_settings_window_saves_and_tests_the_connection(server):
    def body(app):
        from sharel.ui.tools import analyze
        win = analyze.open_analyze(app, RED.convert("RGBA"))
        yield from _wait(win.ai_done.is_set)
        assert win.ai_off.get_visible()
        sw = win.open_ai_settings()
        sw.opts.openai_api_key, sw.opts.openai_custom_url = "k", server.url
        sw.save()  # what every form edit calls
        assert not win.ai_off.get_visible()  # the analyze window follows the new settings
        saved = json.loads((app.store.directory / "UploadersConfig.json").read_text())["services"][ai.SETTINGS_KEY]
        assert saved["openai_custom_url"] == server.url
        sw.test_button.emit("clicked")
        yield from _wait(sw.test_done.is_set)
        assert sw.test_ok and sw.status.get_text() == "Connection OK"
        assert server.requests[-1]["path"] == "/v1/models"
        return True
    assert run_app(body)
