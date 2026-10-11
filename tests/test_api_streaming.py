"""The streaming multipart body against a real local HTTP server (not the mocked adapter)."""
import http.server
import io
import threading

from _api_helpers import multipart

from sharel.uploaders.base import UploadContext
from sharel.uploaders.services._api_common import multipart_form, progress_body, send_body


class _Recorder(http.server.BaseHTTPRequestHandler):
    received: dict = {}

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        _Recorder.received = {"headers": dict(self.headers), "body": self.rfile.read(length),
                              "chunked": self.headers.get("Transfer-Encoding")}
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    do_PUT = do_POST

    def log_message(self, *args):
        return


class _Req:
    def __init__(self, headers, body):
        self.headers, self.body = headers, body


def test_streaming_multipart_reaches_a_real_server_with_progress():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Recorder)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        data = bytes(range(256)) * 4096  # 1 MiB, larger than urllib3's send block size
        progress = []
        ctx = UploadContext(progress=lambda d, t: progress.append((d, t)))
        body, ctype = multipart_form({"k": "v é"}, "file", "big file.bin", io.BytesIO(data))
        resp = send_body(ctx, "POST", f"http://127.0.0.1:{srv.server_address[1]}/up", body, ctype)
    finally:
        srv.shutdown()
        srv.server_close()
    got = _Recorder.received
    assert resp.status_code == 200 and got["chunked"] is None
    assert int(got["headers"]["Content-Length"]) == len(got["body"]) == len(body)
    parts = multipart(_Req(got["headers"], got["body"]))
    assert parts["k"][1].decode() == "v é" and parts["file"] == ("big file.bin", data, "application/octet-stream")
    total = len(body)
    assert progress[-1] == (total, total) and len(progress) > 3


def test_raw_progress_body_is_sent_with_content_length_not_chunked():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Recorder)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        data = b"x" * 300000
        stream = io.BytesIO(b"skip" + data)
        stream.seek(4)  # only the rest of the stream is sent
        ctx = UploadContext()
        resp = ctx.put(f"http://127.0.0.1:{srv.server_address[1]}/raw", data=progress_body(ctx, stream),
                       headers={"Content-Type": "application/octet-stream"})
    finally:
        srv.shutdown()
        srv.server_close()
    got = _Recorder.received
    assert resp.status_code == 200 and got["chunked"] is None and got["body"] == data
    assert got["headers"]["Content-Length"] == str(len(data))
