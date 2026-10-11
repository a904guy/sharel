"""The custom uploader engine against mocked (``responses``) and local HTTP servers: exact request shapes
for every body type, response parsing, errors, progress and cancellation."""
from __future__ import annotations

import datetime as dt
import io
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

import pytest
import responses

from sharel.uploaders.base import UploadCancelled, UploadContext, UploaderError
from sharel.uploaders.custom import sxcu
from sharel.uploaders.custom.model import CustomUploaderItem
from sharel.uploaders.custom.uploader import BodyStream, CustomUploader, mime_type, sample_png
from sharel.uploaders.custom.uploader import test as run_test

PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4


def item(**kw) -> CustomUploaderItem:
    base = {"name": "T", "destination_type": ["ImageUploader", "FileUploader", "TextUploader"],
            "request_method": "POST", "request_url": "https://up.example/upload", "body": "MultipartFormData",
            "file_form_name": "file"}
    base.update(kw)
    return CustomUploaderItem(**base)


def parse_multipart(body: bytes, content_type: str) -> list[tuple[dict[str, str], bytes]]:
    m = re.search(r'boundary="?([^";]+)"?', content_type)
    assert m, content_type
    boundary = m.group(1).encode()
    assert body.endswith(b"--" + boundary + b"--\r\n")
    parts = []
    for chunk in body.split(b"--" + boundary)[1:-1]:
        assert chunk.startswith(b"\r\n") and chunk.endswith(b"\r\n")
        head, _, content = chunk[2:-2].partition(b"\r\n\r\n")
        headers = dict(line.split(": ", 1) for line in head.decode().split("\r\n"))
        parts.append((headers, content))
    return parts


def body_of(call) -> bytes:
    b = call.request.body
    if b is None:
        return b""
    return b if isinstance(b, bytes) else b.encode()


@pytest.fixture
def ctx():
    return UploadContext()


# ---- MultipartFormData ----------------------------------------------------------------------------------

@responses.activate
def test_multipart_image_upload_request_shape_and_response_parsing(ctx):
    responses.post("https://up.example/upload", json={"data": {"link": "https://i.example/a.png",
                                                               "thumb": "https://i.example/a_t.png",
                                                               "del": "dEl"}})
    it = item(parameters={"api_key": "k e&y", "private": ""},
              headers={"Authorization": "Basic {base64:user:pass}", "X-Name": "{filename}"},
              arguments={"title": "{filename}", "": "skipped", "album": "a b"},
              url="{json:data.link}", thumbnail_url="{json:data.thumb}",
              deletion_url="https://i.example/delete/{json:data.del}")
    res = CustomUploader(it, ctx, "ImageUploader").upload(io.BytesIO(PNG), "shot 1.png")

    assert res.is_success, res.errors
    assert (res.url, res.thumbnail_url, res.deletion_url) == \
        ("https://i.example/a.png", "https://i.example/a_t.png", "https://i.example/delete/dEl")
    req = responses.calls[0].request
    assert req.method == "POST"
    assert req.url == "https://up.example/upload?api_key=k+e%26y&private"
    assert req.headers["Authorization"] == "Basic dXNlcjpwYXNz"
    assert req.headers["X-Name"] == "shot 1.png"
    ctype = req.headers["Content-Type"]
    assert re.fullmatch(r'multipart/form-data; boundary="-{20}[0-9a-f]{32}"', ctype)
    body = body_of(responses.calls[0])
    assert req.headers["Content-Length"] == str(len(body))
    parts = parse_multipart(body, ctype)
    assert [p[0]["Content-Disposition"] for p in parts] == [
        'form-data; name="title"', 'form-data; name="album"', 'form-data; name="file"; filename="shot 1.png"']
    assert parts[0] == ({"Content-Disposition": 'form-data; name="title"'}, b"shot 1.png")
    assert parts[1][1] == b"a b"
    assert parts[2][0]["Content-Type"] == "image/png"
    assert parts[2][1] == PNG


@responses.activate
def test_multipart_quotes_in_names_are_escaped(ctx):
    responses.post("https://up.example/upload", body="https://i.example/x")
    it = item(file_form_name='fi"le')
    CustomUploader(it, ctx, "FileUploader").upload(io.BytesIO(b"data"), 'we"ird\\.bin')
    parts = parse_multipart(body_of(responses.calls[0]), responses.calls[0].request.headers["Content-Type"])
    assert parts[0][0]["Content-Disposition"] == 'form-data; name="fi\\"le"; filename="we\\"ird\\\\.bin"'
    assert parts[0][0]["Content-Type"] == "application/octet-stream"


@responses.activate
def test_empty_url_template_uses_whole_response(ctx):
    responses.post("https://up.example/upload", body="https://short.example/abc")
    res = CustomUploader(item(), ctx, "FileUploader").upload(io.BytesIO(b"x"), "a.txt")
    assert res.url == "https://short.example/abc"
    assert res.response == "https://short.example/abc"


@responses.activate
def test_text_multipart_with_file_form_name_sends_text_as_file(ctx):
    responses.post("https://up.example/upload", body="ok")
    it = item(arguments={"lang": "py", "content_copy": "{input}"})
    CustomUploader(it, ctx, "TextUploader").upload_text("print('hé')", "code.txt")
    parts = parse_multipart(body_of(responses.calls[0]), responses.calls[0].request.headers["Content-Type"])
    assert parts[1][1] == "print('hé')".encode()
    assert parts[2][0] == {"Content-Type": "text/plain",
                           "Content-Disposition": 'form-data; name="file"; filename="code.txt"'}
    assert parts[2][1] == "print('hé')".encode()


@responses.activate
def test_text_multipart_without_file_form_name_sends_arguments_only(ctx):
    responses.post("https://paste.example/new", body="https://paste.example/p/1")
    it = item(request_url="https://paste.example/new", file_form_name="", arguments={"paste": "{input}"})
    res = CustomUploader(it, ctx, "TextUploader").upload_text("hello", "x.txt")
    parts = parse_multipart(body_of(responses.calls[0]), responses.calls[0].request.headers["Content-Type"])
    assert parts == [({"Content-Disposition": 'form-data; name="paste"'}, b"hello")]
    assert res.url == "https://paste.example/p/1"


@responses.activate
def test_shortener_multipart_has_arguments_only(ctx):
    responses.post("https://s.example/api", body="https://s.example/q")
    it = item(request_url="https://s.example/api", destination_type=["URLShortener"], arguments={"long": "{input}"})
    res = CustomUploader(it, ctx, "URLShortener").shorten_url("https://example.org/a?b=c")
    parts = parse_multipart(body_of(responses.calls[0]), responses.calls[0].request.headers["Content-Type"])
    assert parts == [({"Content-Disposition": 'form-data; name="long"'}, b"https://example.org/a?b=c")]
    assert (res.url, res.shortened_url) == ("https://example.org/a?b=c", "https://s.example/q")


# ---- Binary ---------------------------------------------------------------------------------------------

@responses.activate
def test_binary_put_streams_the_raw_file_with_its_mime_type(ctx):
    responses.put("https://dav.example/files/my%20shot.png", status=201, body="")
    it = item(request_method="PUT", body="Binary", file_form_name="", request_url="dav.example/files/{filename}",
              headers={"Authorization": "Bearer t"}, url="https://dav.example/files/{filename}")
    res = CustomUploader(it, ctx, "ImageUploader").upload(io.BytesIO(PNG), "my shot.png")
    req = responses.calls[0].request
    assert req.method == "PUT"
    assert req.url == "https://dav.example/files/my%20shot.png"  # https:// added, {filename} URL-encoded
    assert req.headers["Content-Type"] == "image/png"
    assert req.headers["Content-Length"] == str(len(PNG))
    assert req.headers["Authorization"] == "Bearer t"
    assert body_of(responses.calls[0]) == PNG
    assert res.url == "https://dav.example/files/my%20shot.png"


@responses.activate
def test_binary_text_upload(ctx):
    responses.post("https://up.example/upload", body="https://t.example/1")
    CustomUploader(item(body="Binary"), ctx, "TextUploader").upload_text("raw text", "note.md")
    req = responses.calls[0].request
    assert body_of(responses.calls[0]) == b"raw text"
    assert req.headers["Content-Type"] == "text/markdown"


@responses.activate
def test_custom_content_type_header_overrides_default(ctx):
    responses.post("https://up.example/upload", body="ok")
    it = item(body="Binary", headers={"content-type": "application/x-custom"})
    CustomUploader(it, ctx, "FileUploader").upload(io.BytesIO(b"x"), "a.png")
    assert responses.calls[0].request.headers["Content-Type"] == "application/x-custom"


# ---- FormURLEncoded / JSON / XML / None --------------------------------------------------------------------

@responses.activate
def test_form_urlencoded_body(ctx):
    responses.post("https://paste.example/api", body="https://paste.example/abc")
    it = item(request_url="https://paste.example/api", body="FormURLEncoded",
              arguments={"api_paste_code": "{input}", "api_option": "paste", "flag": ""})
    res = CustomUploader(it, ctx, "TextUploader").upload_text("a b&c=d!é", "t.txt")
    req = responses.calls[0].request
    assert req.headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert body_of(responses.calls[0]) == b"api_paste_code=a+b%26c%3dd!%c3%a9&api_option=paste&flag"
    assert res.url == "https://paste.example/abc"


@responses.activate
def test_json_body_substitutes_and_escapes_input(ctx):
    responses.post("https://paste.example/json", json={"key": "k1"})
    data = '{"content": "{input}", "title": "{FileName}", "when": "%y", "raw": "{base64:x}"}'
    it = item(request_url="https://paste.example/json", body="JSON", data=data,
              url="https://paste.example/{json:key}")
    text = 'line1\n"quoted" \\ back\ttab'
    res = CustomUploader(it, ctx, "TextUploader").upload_text(text, 'my "file".txt')
    req = responses.calls[0].request
    assert req.headers["Content-Type"] == "application/json"
    sent = json.loads(body_of(responses.calls[0]))
    # {input}/{filename} are JSON-escaped, the name parser ran, other syntax is left alone (as in ShareX).
    assert sent == {"content": text, "title": 'my "file".txt', "when": str(dt.datetime.now().year),
                    "raw": "{base64:x}"}
    assert res.url == "https://paste.example/k1"


@responses.activate
def test_xml_body_substitutes_and_escapes_input(ctx):
    responses.post("https://x.example/api", body="<r><url>https://x.example/1</url></r>")
    it = item(request_url="https://x.example/api", body="XML", data="<paste><t>{input}</t></paste>",
              url="{xml:/r/url}")
    res = CustomUploader(it, ctx, "TextUploader").upload_text("<b>&'\"</b>", "a.txt")
    assert responses.calls[0].request.headers["Content-Type"] == "application/xml"
    assert body_of(responses.calls[0]) == b"<paste><t>&lt;b&gt;&amp;&apos;&quot;&lt;/b&gt;</t></paste>"
    assert res.url == "https://x.example/1"


@responses.activate
def test_shortener_get_with_parameters_and_no_body(ctx):
    responses.get("https://is.example/create.php", body="https://is.example/xyz")
    it = item(request_method="GET", request_url="https://is.example/create.php", body="None", file_form_name="",
              destination_type=["URLShortener"], parameters={"format": "simple", "url": "{input}"})
    res = CustomUploader(it, ctx, "URLShortener").shorten_url("https://example.org/a b?c=d&e")
    req = responses.calls[0].request
    assert req.method == "GET"
    assert req.body is None
    assert "Content-Type" not in req.headers
    q = urlsplit(req.url).query
    # HttpUtility.UrlEncode writes lowercase %xx; requests normalises escapes in URLs to uppercase.
    assert q == "format=simple&url=https%3A%2F%2Fexample.org%2Fa+b%3Fc%3Dd%26e"
    assert dict(parse_qsl(q))["url"] == "https://example.org/a b?c=d&e"
    assert res.shortened_url == "https://is.example/xyz"
    assert res.url == "https://example.org/a b?c=d&e"


@responses.activate
def test_shortener_json_body(ctx):
    responses.post("https://api.example/shorten", json={"link": "https://sh.example/1"}, status=201)
    it = item(request_url="https://api.example/shorten", body="JSON", data='{"long_url": "{input}"}',
              headers={"Authorization": "Bearer abc"}, url="{json:link}", destination_type=["URLShortener"])
    res = CustomUploader(it, ctx, "URLShortener").shorten_url('https://e.org/"q"')
    assert json.loads(body_of(responses.calls[0])) == {"long_url": 'https://e.org/"q"'}
    assert res.shortened_url == "https://sh.example/1"


@responses.activate
def test_url_sharing_service(ctx):
    responses.post("https://share.example/post", body="shared")
    it = item(request_url="https://share.example/post", body="FormURLEncoded", arguments={"link": "{input}"},
              destination_type=["URLSharingService"])
    up = CustomUploader(it, ctx, "URLSharingService")
    assert up.share_url("https://e.org/x") == ""
    assert body_of(responses.calls[0]) == b"link=https%3a%2f%2fe.org%2fx"
    res = up.share_url_result("https://e.org/x")
    assert res.is_url_expected is False and res.is_success


@responses.activate
def test_url_sharing_service_failure_raises(ctx):
    responses.post("https://share.example/post", status=403, json={"message": "nope"})
    it = item(request_url="https://share.example/post", body="None", error_message="{json:message}")
    with pytest.raises(UploaderError, match="nope"):
        CustomUploader(it, ctx, "URLSharingService").share_url("https://e.org/x")


@responses.activate
def test_request_methods_are_used(ctx):
    for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        responses.add(method, "https://m.example/x", body="ok")
        it = item(request_method=method, request_url="https://m.example/x", body="None", file_form_name="")
        CustomUploader(it, ctx, "TextUploader").upload_text("t", "t.txt")
    assert [c.request.method for c in responses.calls] == ["GET", "POST", "PUT", "PATCH", "DELETE"]


# ---- unsupported configurations ---------------------------------------------------------------------------

@pytest.mark.parametrize(("dest", "body"), [("ImageUploader", "JSON"), ("FileUploader", "None"),
                                            ("FileUploader", "FormURLEncoded"), ("URLShortener", "Binary")])
def test_unsupported_body_for_destination(ctx, dest, body):
    up = CustomUploader(item(body=body, data="{}"), ctx, dest)
    assert up.validate() == [f"Unsupported request format: {body}"]
    with pytest.raises(UploaderError, match="Unsupported request format"):
        if dest == "URLShortener":
            up.shorten_url("https://e.org")
        else:
            up.upload(io.BytesIO(b"x"), "a.png")


def test_missing_request_url_and_file_form_name(ctx):
    with pytest.raises(UploaderError, match="Request URL must be configured"):
        CustomUploader(item(request_url=""), ctx, "ImageUploader").upload(io.BytesIO(b"x"), "a.png")
    with pytest.raises(UploaderError, match="File form name must be configured"):
        CustomUploader(item(file_form_name=""), ctx, "ImageUploader").upload(io.BytesIO(b"x"), "a.png")
    assert CustomUploader(item(file_form_name=""), ctx, "FileUploader").validate() == \
        ["File form name must be configured."]


def test_syntax_error_in_request_is_an_uploader_error(ctx):
    with pytest.raises(UploaderError, match="Invalid function name: bogus"):
        CustomUploader(item(request_url="https://x.example/{bogus}"), ctx, "FileUploader").upload(
            io.BytesIO(b"x"), "a.png")


def test_unknown_destination_type(ctx):
    with pytest.raises(ValueError, match="destination type"):
        CustomUploader(item(), ctx, "Printer")


# ---- responses: errors, redirects, headers ----------------------------------------------------------------

@responses.activate
def test_http_4xx_parses_error_message_first(ctx):
    responses.post("https://up.example/upload", status=413, json={"error": {"message": "File too large"}})
    it = item(url="{json:data.link}", error_message="{json:error.message}")
    res = CustomUploader(it, ctx, "ImageUploader").upload(io.BytesIO(PNG), "a.png")
    assert not res.is_success
    assert res.url == ""
    assert res.errors[0] == "File too large"
    assert "413" in res.errors[1] and "File too large" in res.response


@responses.activate
def test_http_5xx_without_error_message(ctx):
    responses.post("https://up.example/upload", status=502, body="Bad gateway")
    res = CustomUploader(item(), ctx, "FileUploader").upload(io.BytesIO(b"x"), "a.png")
    assert len(res.errors) == 1
    assert "502" in res.errors[0] and "Bad gateway" in res.errors[0]


@responses.activate
def test_invalid_json_response_is_reported(ctx):
    responses.post("https://up.example/upload", body="<html>oops</html>")
    res = CustomUploader(item(url="{json:url}"), ctx, "FileUploader").upload(io.BytesIO(b"x"), "a.png")
    assert not res.is_success
    assert "expected response content is JSON" in res.errors[0]
    assert "(up.example)" in res.errors[0]


@responses.activate
def test_unparseable_response_is_reported(ctx):
    responses.post("https://up.example/upload", json={"files": [{"url": "a"}, {"url": "b"}]})
    res = CustomUploader(item(url="{json:files[*].url}"), ctx, "FileUploader").upload(io.BytesIO(b"x"), "a.png")
    assert "Unable to parse response" in res.errors[0]


@responses.activate
def test_redirect_responseurl_and_header(ctx):
    responses.post("https://up.example/upload", status=302, headers={"Location": "https://cdn.example/f/abc"})
    responses.get("https://cdn.example/f/abc", body="page", headers={"X-File-Id": "abc"})
    it = item(url="{responseurl}", thumbnail_url="https://cdn.example/t/{header:x-file-id}.jpg")
    res = CustomUploader(it, ctx, "FileUploader").upload(io.BytesIO(b"x"), "a.png")
    assert res.url == "https://cdn.example/f/abc"
    assert res.thumbnail_url == "https://cdn.example/t/abc.jpg"


@responses.activate
def test_location_header_and_filename_in_response_templates(ctx):
    responses.post("https://up.example/upload", status=201, headers={"Location": "https://h.example/1"})
    it = item(url="{header:Location}", deletion_url="https://h.example/del/{filename}")
    res = CustomUploader(it, ctx, "FileUploader").upload(io.BytesIO(b"x"), "a b.png")
    assert res.url == "https://h.example/1"
    assert res.deletion_url == "https://h.example/del/a%20b.png"


@responses.activate
def test_response_charset_and_bom_decoding(ctx):
    responses.post("https://up.example/upload", body="\ufeffhttps://ü.example".encode(),
                   content_type="text/plain")
    res = CustomUploader(item(), ctx, "FileUploader").upload(io.BytesIO(b"x"), "a.png")
    assert res.url == "https://ü.example"
    responses.replace(responses.POST, "https://up.example/upload", body="caf\xe9".encode("latin-1"),
                      content_type="text/plain; charset=ISO-8859-1")
    assert CustomUploader(item(), ctx, "FileUploader").upload(io.BytesIO(b"x"), "a.png").url == "café"


@responses.activate
def test_outputbox_url_means_no_url_expected():
    shown = []
    ctx = UploadContext(show_output=lambda title, text: shown.append((title, text)))
    responses.post("https://ai.example/v1", json={"choices": [{"message": {"content": "Hello!"}}]})
    it = item(request_url="https://ai.example/v1", body="JSON", data='{"prompt": "{input}"}',
              url="{outputbox:Result|{json:choices[0].message.content}}")
    res = CustomUploader(it, ctx, "TextUploader").upload_text("hi", "t.txt")
    assert shown == [("Result", "Hello!")]
    assert res.url == "" and res.is_url_expected is False and res.is_success


@responses.activate
def test_connection_errors_become_result_errors(ctx):
    res = CustomUploader(item(request_url="https://unregistered.example/x"), ctx, "FileUploader").upload(
        io.BytesIO(b"x"), "a.png")
    assert not res.is_success
    assert "unregistered.example" in res.errors[0]


# ---- prompts in requests --------------------------------------------------------------------------------

@responses.activate
def test_select_and_inputbox_in_request_use_ctx_callbacks():
    asked = []

    def select(title, options):
        asked.append(("select", options))
        return options[1]

    def ask(title, default):
        asked.append(("input", title, default))
        return "album42"

    ctx = UploadContext(prompt_select=select, prompt_input=ask)
    responses.post("https://two.example/upload", body="ok")
    it = item(request_url="https://{select:one.example|two.example}/upload",
              arguments={"album": "{inputbox:Album|default}"})
    CustomUploader(it, ctx, "FileUploader").upload(io.BytesIO(b"x"), "a.png")
    assert asked == [("select", ["one.example", "two.example"]), ("input", "Album", "default")]
    parts = parse_multipart(body_of(responses.calls[0]), responses.calls[0].request.headers["Content-Type"])
    assert parts[0][1] == b"album42"


# ---- progress and cancellation ------------------------------------------------------------------------------

@responses.activate
def test_progress_callback_reports_the_file_bytes():
    seen = []
    ctx = UploadContext(progress=lambda done, total: seen.append((done, total)))
    responses.post("https://up.example/upload", body="ok")
    data = PNG * 50
    CustomUploader(item(), ctx, "ImageUploader").upload(io.BytesIO(data), "a.png")
    assert seen, "no progress reported"
    assert seen[-1] == (len(data), len(data))
    assert all(t == len(data) for _, t in seen)
    assert [d for d, _ in seen] == sorted(d for d, _ in seen)


def test_cancelled_before_start_raises(ctx):
    ctx.cancel_event.set()
    with pytest.raises(UploadCancelled):
        CustomUploader(item(), ctx, "ImageUploader").upload(io.BytesIO(PNG), "a.png")
    with pytest.raises(UploadCancelled):
        CustomUploader(item(body="None", destination_type=["URLShortener"]), ctx, "URLShortener").shorten_url("x")


class _Server:
    """A local HTTP server that records what it receives."""

    def __init__(self, status=200, reply=b'{"url": "http://local/ok"}'):
        self.requests: list[tuple[str, str, dict, bytes]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _handle(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = b""
                try:
                    while len(body) < n:
                        chunk = self.rfile.read(min(65536, n - len(body)))
                        if not chunk:
                            break
                        body += chunk
                except OSError:
                    return
                outer.requests.append((self.command, self.path, dict(self.headers), body))
                if len(body) < n:
                    return
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)

            do_POST = do_PUT = do_GET = _handle

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    s = _Server()
    yield s
    s.close()


def test_streaming_upload_to_a_real_server_with_progress(server):
    seen = []
    ctx = UploadContext(progress=lambda d, t: seen.append(d))
    data = bytes(range(256)) * 2000  # 512 KB, many socket writes
    it = item(request_url=server.url + "/up", url="{json:url}", arguments={"k": "v"})
    res = CustomUploader(it, ctx, "FileUploader").upload(io.BytesIO(data), "big.bin")
    assert res.url == "http://local/ok"
    method, path, headers, body = server.requests[0]
    assert (method, path) == ("POST", "/up")
    parts = parse_multipart(body, headers["Content-Type"])
    assert parts[1][1] == data
    assert len(seen) > 3 and seen[-1] == len(data)


def test_binary_put_to_a_real_server(server):
    ctx = UploadContext()
    it = item(request_url=server.url + "/files/{filename}", body="Binary", request_method="PUT",
              url="{json:url}")
    CustomUploader(it, ctx, "FileUploader").upload(io.BytesIO(PNG), "ä.png")
    method, path, headers, body = server.requests[0]
    assert (method, path, headers["Content-Type"], body) == ("PUT", "/files/%C3%A4.png", "image/png", PNG)


def test_non_latin1_header_values_are_sent_as_utf8(server):
    it = item(request_url=server.url + "/up", body="Binary", headers={"X-Name": "{filename}"})
    res = CustomUploader(it, UploadContext(), "FileUploader").upload(io.BytesIO(b"x"), "日本.png")
    assert res.is_success
    raw = server.requests[0][2]["X-Name"]  # http.server decodes header bytes as Latin-1
    assert raw.encode("latin-1").decode("utf-8") == "日本.png"


def test_cancellation_during_upload_raises(server):
    cancel = threading.Event()
    ctx = UploadContext(cancel=cancel, progress=lambda d, t: cancel.set() if d > 0 else None)
    it = item(request_url=server.url + "/up")
    with pytest.raises(UploadCancelled):
        CustomUploader(it, ctx, "FileUploader").upload(io.BytesIO(b"z" * 300_000), "big.bin")


def test_body_stream_length_read_and_rewind():
    ctx = UploadContext()
    src = io.BytesIO(b"0123456789")
    src.seek(2)
    bs = BodyStream([b"<", ctx.progress_stream(src), b">"])
    assert len(bs) == 10
    assert bs.read(3) == b"<23"
    assert bs.tell() == 3
    assert bs.read() == b"456789>"
    assert bs.read() == b""
    bs.seek(0)
    assert bs.read() == b"<23456789>"


def test_mime_types():
    assert mime_type("a.png") == "image/png"
    assert mime_type("a.JPG") == "image/jpeg"
    assert mime_type("noext") == "application/octet-stream"


# ---- the Test button helper ------------------------------------------------------------------------------

@responses.activate
def test_test_helper_image(ctx):
    responses.post("https://up.example/upload", json={"url": "https://i.example/t.png"},
                   headers={"X-Req": "1"})
    result, summary, raw = run_test(item(url="{json:url}", arguments={"a": "b"}), "ImageUploader", ctx)
    assert result.url == "https://i.example/t.png"
    assert summary.startswith("POST https://up.example/upload\nContent-Type: multipart/form-data; boundary=")
    assert "a: b" in summary
    assert re.search(r"file: Test\.png \(image/png, \d+ bytes\)", summary)
    assert raw.startswith("HTTP 200 OK\n")
    assert "X-Req: 1" in raw and raw.endswith('{"url": "https://i.example/t.png"}')
    sent = parse_multipart(body_of(responses.calls[0]), responses.calls[0].request.headers["Content-Type"])
    assert sent[1][1] == sample_png() and sent[1][1].startswith(b"\x89PNG")


@responses.activate
def test_test_helper_text_shortener_and_sharing(ctx):
    responses.post("https://up.example/upload", body="ok")
    it = item(body="JSON", data='{"t": "{input}"}', destination_type=["TextUploader", "URLShortener",
                                                                      "URLSharingService"])
    result, summary, _ = run_test(it, "TextUploader", ctx, text="custom text")
    assert result.url == "ok"
    assert summary.endswith('{"t": "custom text"}')
    result, summary, _ = run_test(it, "URLShortener", ctx)
    assert result.shortened_url == "ok" and result.url == "https://getsharex.com"
    result, _, _ = run_test(it, "URLSharingService", ctx)
    assert result.is_success and not result.is_url_expected


def test_test_helper_reports_configuration_errors(ctx):
    result, summary, raw = run_test(item(request_url=""), "ImageUploader", ctx)
    assert result.errors == ["Request URL must be configured."]
    assert (summary, raw) == ("", "")


# ---- integration with dispatch and .sxcu --------------------------------------------------------------------

@responses.activate
def test_dispatch_creates_the_engine_from_config(ctx):
    from sharel.uploaders.config import UploadersConfig
    from sharel.uploaders.dispatch import Destination, create
    from sharel.core.serialize import to_jsonable
    # Stored the way UploadersConfig.json holds it (snake_case dicts), selected as the file uploader.
    cfg = UploadersConfig(custom_uploaders=[to_jsonable(item()), to_jsonable(item(url="{json:u}"))],
                          custom_file_uploader_selected=1)
    up = create(Destination("file", "CustomFileUploader"), cfg, ctx)
    assert isinstance(up, CustomUploader) and up.dest_type == "FileUploader"
    responses.post("https://up.example/upload", json={"u": "https://ok.example"})
    assert up.upload(io.BytesIO(b"x"), "a.png").url == "https://ok.example"


@responses.activate
def test_imported_legacy_uploader_works_end_to_end(ctx):
    legacy = ('{"RequestType":"POST","RequestURL":"https://old.example/up.php?key=K","FileFormName":"img",'
              '"Arguments":{"title":"$filename$"},"URL":"$json:data.link$","DeletionURL":"$json:data.del$"}')
    it = sxcu.loads(legacy)
    responses.post("https://old.example/up.php", json={"data": {"link": "https://o/i.png", "del": "https://o/d"}})
    res = CustomUploader(it, ctx, "ImageUploader").upload(io.BytesIO(PNG), "s.png")
    assert (res.url, res.deletion_url) == ("https://o/i.png", "https://o/d")
    assert responses.calls[0].request.url == "https://old.example/up.php?key=K"
    parts = parse_multipart(body_of(responses.calls[0]), responses.calls[0].request.headers["Content-Type"])
    assert parts[0][1] == b"s.png" and 'name="img"' in parts[1][0]["Content-Disposition"]
