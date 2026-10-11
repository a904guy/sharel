"""Amazon S3 and S3-compatible storage (ShareX AmazonS3.cs): AWS, Cloudflare R2, MinIO, Backblaze B2 (S3 API),
DigitalOcean Spaces, Wasabi...

Requests are signed with AWS Signature Version 4 implemented here (no SDK). Single PUT by default, or
multipart upload (CreateMultipartUpload / UploadPart / CompleteMultipartUpload, parts in parallel) when
enabled. Path-style or virtual-hosted-style addressing; buckets containing a dot always use path style.

The result URL is ``<endpoint>/<bucket>/<key>``, or ``<custom domain>/<key>`` when a custom domain (CNAME, CDN,
R2 public bucket domain) is set, like ShareX.
"""
from __future__ import annotations

import concurrent.futures
import datetime as _dt
import hashlib
import hmac
import io
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from typing import BinaryIO
from xml.etree import ElementTree

import requests

from ...core.nameparser import NameParserType
from ..base import FileUploader, UploadCancelled, UploadContext, UploaderError, UploadResult, stream_size
from ..registry import register, setting
from ._file_helpers import (combine_url, fix_prefix, is_image_file, is_text_file, is_video_file, mime_type,
                            parse_folder, parse_pattern, remove_prefixes, url_encode, url_encode_path,
                            without_extension)

DEFAULT_REGION = "us-east-1"
ALGORITHM = "AWS4-HMAC-SHA256"
UNSIGNED_PAYLOAD = "UNSIGNED-PAYLOAD"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
DEFAULT_PART_SIZE = 100 * 1024 * 1024
MINIMUM_PART_SIZE = 5 * 1024 * 1024
MAXIMUM_PART_SIZE = 5 * 1024 * 1024 * 1024
MAXIMUM_PART_COUNT = 10000

STORAGE_CLASSES = ["STANDARD", "STANDARD_IA", "ONEZONE_IA", "INTELLIGENT_TIERING", "REDUCED_REDUNDANCY",
                   "GLACIER_IR", "(none)"]

# (name, endpoint, region) — ShareX's AmazonS3.Endpoints plus common S3-compatible services.
ENDPOINTS: list[tuple[str, str, str]] = [
    ("Asia Pacific (Hong Kong)", "s3.ap-east-1.amazonaws.com", "ap-east-1"),
    ("Asia Pacific (Mumbai)", "s3.ap-south-1.amazonaws.com", "ap-south-1"),
    ("Asia Pacific (Seoul)", "s3.ap-northeast-2.amazonaws.com", "ap-northeast-2"),
    ("Asia Pacific (Singapore)", "s3.ap-southeast-1.amazonaws.com", "ap-southeast-1"),
    ("Asia Pacific (Sydney)", "s3.ap-southeast-2.amazonaws.com", "ap-southeast-2"),
    ("Asia Pacific (Tokyo)", "s3.ap-northeast-1.amazonaws.com", "ap-northeast-1"),
    ("Canada (Central)", "s3.ca-central-1.amazonaws.com", "ca-central-1"),
    ("China (Beijing)", "s3.cn-north-1.amazonaws.com.cn", "cn-north-1"),
    ("China (Ningxia)", "s3.cn-northwest-1.amazonaws.com.cn", "cn-northwest-1"),
    ("EU (Frankfurt)", "s3.eu-central-1.amazonaws.com", "eu-central-1"),
    ("EU (Ireland)", "s3.eu-west-1.amazonaws.com", "eu-west-1"),
    ("EU (London)", "s3.eu-west-2.amazonaws.com", "eu-west-2"),
    ("EU (Paris)", "s3.eu-west-3.amazonaws.com", "eu-west-3"),
    ("EU (Stockholm)", "s3.eu-north-1.amazonaws.com", "eu-north-1"),
    ("Middle East (Bahrain)", "s3.me-south-1.amazonaws.com", "me-south-1"),
    ("South America (São Paulo)", "s3.sa-east-1.amazonaws.com", "sa-east-1"),
    ("US East (N. Virginia)", "s3.amazonaws.com", "us-east-1"),
    ("US East (Ohio)", "s3.us-east-2.amazonaws.com", "us-east-2"),
    ("US West (N. California)", "s3.us-west-1.amazonaws.com", "us-west-1"),
    ("US West (Oregon)", "s3.us-west-2.amazonaws.com", "us-west-2"),
    ("Cloudflare R2", "<account-id>.r2.cloudflarestorage.com", "auto"),
    ("Backblaze B2 (S3 API)", "s3.<region>.backblazeb2.com", "<region>"),
    ("MinIO (self-hosted)", "http://localhost:9000", "us-east-1"),
    ("DreamObjects", "objects-us-east-1.dream.io", ""),
    ("DigitalOcean (Amsterdam)", "ams3.digitaloceanspaces.com", "ams3"),
    ("DigitalOcean (New York)", "nyc3.digitaloceanspaces.com", "nyc3"),
    ("DigitalOcean (San Francisco)", "sfo2.digitaloceanspaces.com", "sfo2"),
    ("DigitalOcean (Singapore)", "sgp1.digitaloceanspaces.com", "sgp1"),
    ("Wasabi", "s3.wasabisys.com", ""),
]


@dataclass
class AmazonS3Settings:
    access_key_id: str = setting("", label="Access key ID")
    secret_access_key: str = setting("", label="Secret access key", secret=True)
    endpoint: str = setting("s3.amazonaws.com", label="Endpoint",
                            help="e.g. s3.eu-west-1.amazonaws.com, <account-id>.r2.cloudflarestorage.com, "
                                 "s3.us-west-004.backblazeb2.com, http://minio.lan:9000")
    region: str = setting("", label="Region", help="Empty: taken from an AWS endpoint, else us-east-1 "
                                                   "(R2: auto)")
    use_path_style: bool = setting(False, label="Use path-style requests",
                                   help="endpoint/bucket/key instead of bucket.endpoint/key (MinIO needs this)")
    bucket: str = setting("", label="Bucket")
    object_prefix: str = setting("ShareL/%y/%mo", label="Object prefix", help="Name-parser patterns allowed")
    use_custom_cname: bool = setting(False, label="Use custom domain")
    custom_domain: str = setting("", label="Custom domain", help="e.g. cdn.example.com or https://pub-xxxx.r2.dev")
    storage_class: str = setting("STANDARD", label="Storage class", choices=STORAGE_CLASSES)
    set_public_acl: bool = setting(True, label="Set public-read ACL",
                                   help="Turn off for buckets with ACLs disabled (Object Ownership) and for R2")
    signed_payload: bool = setting(False, label="Sign payload",
                                   help="Hash the body into the signature instead of UNSIGNED-PAYLOAD")
    use_multipart_upload: bool = setting(False, label="Use multipart upload")
    multipart_concurrency: int = setting(4, label="Parallel parts (1-16)")
    remove_extension_image: bool = setting(False, label="Remove extension from image URLs")
    remove_extension_video: bool = setting(False, label="Remove extension from video URLs")
    remove_extension_text: bool = setting(False, label="Remove extension from text URLs")
    content_type: str = setting("", label="Content-Type", help="Empty: by file extension")


# ---- Signature Version 4 -------------------------------------------------------------------------

def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def signing_key(secret_key: str, date: str, region: str, service: str = "s3") -> bytes:
    k = _hmac(("AWS4" + secret_key).encode("utf-8"), date)
    k = _hmac(k, region)
    k = _hmac(k, service)
    return _hmac(k, "aws4_request")


def canonical_query_string(params: Iterable[tuple[str, str]]) -> str:
    return "&".join(f"{url_encode(k)}={url_encode(v or '')}" for k, v in sorted(params))


def sign_request(method: str, host: str, canonical_uri: str, query: str, headers: dict[str, str],
                 payload_hash: str, access_key: str, secret_key: str, region: str, service: str = "s3",
                 now: _dt.datetime | None = None) -> dict[str, str]:
    """Headers for a SigV4-signed request: ``headers`` + Host, x-amz-date, x-amz-content-sha256, Authorization.

    ``canonical_uri`` must already be URI-encoded exactly as it is sent; ``query`` is the canonical query string.
    """
    now = now or _dt.datetime.now(_dt.UTC)
    date = now.strftime("%Y%m%d")
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    all_headers = {**headers, "Host": host, "x-amz-date": stamp, "x-amz-content-sha256": payload_hash}
    canon = sorted((k.lower(), " ".join(str(v).strip().split())) for k, v in all_headers.items())
    canonical_headers = "".join(f"{k}:{v}\n" for k, v in canon)
    signed_headers = ";".join(k for k, _ in canon)
    canonical_request = "\n".join([method, canonical_uri, query, canonical_headers, signed_headers, payload_hash])
    scope = f"{date}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join([ALGORITHM, stamp, scope,
                                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()])
    signature = hmac.new(signing_key(secret_key, date, region, service), string_to_sign.encode("utf-8"),
                         hashlib.sha256).hexdigest()
    out = dict(all_headers)
    out["Authorization"] = (f"{ALGORITHM} Credential={access_key}/{scope}, SignedHeaders={signed_headers}, "
                            f"Signature={signature}")
    return out


def region_for(settings: AmazonS3Settings) -> str:
    """AmazonS3.GetRegion: the region setting, else parsed from an *.amazonaws.com endpoint, else us-east-1."""
    if settings.region.strip():
        return settings.region.strip()
    url = remove_prefixes(settings.endpoint).rstrip("/")
    idx = url.find(".amazonaws.com")
    if idx < 0:
        return DEFAULT_REGION
    service_and_region = url[:idx]
    if service_and_region.startswith("s3-"):
        service_and_region = "s3." + service_and_region[3:]
    dot = service_and_region.rfind(".")
    return service_and_region[dot + 1:] if dot >= 0 else DEFAULT_REGION


def _sha256_of(stream: BinaryIO, start: int, length: int) -> str:
    h = hashlib.sha256()
    stream.seek(start)
    remaining = length
    while remaining > 0:
        chunk = stream.read(min(1024 * 1024, remaining))
        if not chunk:
            raise UploaderError("The upload stream ended before the payload hash was calculated")
        h.update(chunk)
        remaining -= len(chunk)
    stream.seek(start)
    return h.hexdigest()


def part_size_for(length: int, default: int | None = None) -> int:
    """AmazonS3.GetMultipartPartSize: >= the default, enough for 10000 parts, rounded up to whole MiB."""
    if length <= 0:
        return 0
    required = -(-length // MAXIMUM_PART_COUNT)
    size = max(default or DEFAULT_PART_SIZE, MINIMUM_PART_SIZE, required)
    mib = 1024 * 1024
    if size % mib:
        size += mib - size % mib
    return size if size <= MAXIMUM_PART_SIZE else 0


def _error_text(resp: requests.Response) -> str:
    text = resp.text
    try:
        root = ElementTree.fromstring(text)
        code = root.findtext("Code") or ""
        message = root.findtext("Message") or ""
        if code or message:
            return f"{code}: {message}".strip(": ")
    except ElementTree.ParseError:
        pass
    return text[:300]


class _Counter:
    """A read-only body that counts bytes into a shared progress total (for parallel parts)."""

    def __init__(self, data: bytes, on_read) -> None:
        self._buf = io.BytesIO(data)
        self._len = len(data)
        self._on_read = on_read

    def read(self, size: int = -1) -> bytes:
        chunk = self._buf.read(size)
        self._on_read(len(chunk))
        return chunk

    def __len__(self) -> int:
        return self._len


class S3Client:
    """The S3 REST calls the uploader needs, signed with SigV4."""

    def __init__(self, settings: AmazonS3Settings, ctx: UploadContext) -> None:
        self.s = settings
        self.ctx = ctx
        self.region = region_for(settings)

    # addressing -----------------------------------------------------------------------------------
    def request_target(self, key: str) -> tuple[str, str, str]:
        """(scheme, host, canonical URI) — AmazonS3.CreateRequestData."""
        s = self.s
        path_style = s.use_path_style or "." in s.bucket
        endpoint = s.endpoint.strip()
        scheme = endpoint.split("://", 1)[0] + "://" if "://" in endpoint else "https://"
        host = remove_prefixes(endpoint).rstrip("/")
        if not path_style:
            host = f"{s.bucket}.{host}"
        uri = combine_url(s.bucket, key) if path_style else key
        uri = "/" + uri.lstrip("/")
        return scheme, host, url_encode_path(uri)

    def send(self, method: str, key: str, query: list[tuple[str, str]] | None = None, body=b"",
             payload_hash: str | None = None, headers: dict[str, str] | None = None) -> requests.Response:
        scheme, host, uri = self.request_target(key)
        cqs = canonical_query_string(query or [])
        if payload_hash is None:
            if isinstance(body, (bytes, bytearray)):
                payload_hash = hashlib.sha256(body).hexdigest() if self.s.signed_payload else UNSIGNED_PAYLOAD
            else:
                payload_hash = UNSIGNED_PAYLOAD
        signed = sign_request(method, host, uri, cqs, headers or {}, payload_hash, self.s.access_key_id,
                              self.s.secret_access_key, self.region)
        url = f"{scheme}{host}{uri}" + (f"?{cqs}" if cqs else "")
        return self.ctx.request(method, url, data=body, headers=signed)

    def check(self, resp: requests.Response, what: str) -> requests.Response:
        if not 200 <= resp.status_code < 300:
            raise UploaderError(f"S3 {what} failed: HTTP {resp.status_code}: {_error_text(resp)}", resp.text)
        if resp.content[:200].lstrip().startswith(b"<?xml") and b"<Error>" in resp.content[:500]:
            raise UploaderError(f"S3 {what} failed: {_error_text(resp)}", resp.text)
        return resp

    # objects -------------------------------------------------------------------------------------
    def object_headers(self, content_type: str) -> dict[str, str]:
        headers = {"Content-Type": content_type}
        if self.s.storage_class and self.s.storage_class != "(none)":
            headers["x-amz-storage-class"] = self.s.storage_class
        if self.s.set_public_acl:
            headers["x-amz-acl"] = "public-read"
        return headers

    def put_object(self, key: str, stream: BinaryIO, content_type: str) -> None:
        start = stream.tell()
        length = stream_size(stream)
        payload_hash = _sha256_of(stream, start, length) if self.s.signed_payload else UNSIGNED_PAYLOAD
        body = self.ctx.progress_stream(stream, length)
        self.check(self.send("PUT", key, body=body, payload_hash=payload_hash,
                             headers=self.object_headers(content_type)), "upload")

    def multipart(self, key: str, stream: BinaryIO, content_type: str, part_size: int | None = None) -> None:
        start = stream.tell()
        length = stream_size(stream)
        size = part_size_for(length, part_size)
        if not size:
            raise UploaderError("File is too large for an S3 multipart upload")
        resp = self.check(self.send("POST", key, [("uploads", "")], b"", headers=self.object_headers(content_type)),
                          "CreateMultipartUpload")
        upload_id = _xml_find(resp.text, "UploadId")
        if not upload_id:
            raise UploaderError("S3 CreateMultipartUpload returned no UploadId", resp.text)
        count = -(-length // size)
        lock = threading.Lock()
        done = [0]
        failed = threading.Event()

        def on_read(n: int) -> None:
            with lock:
                done[0] += n
                current = done[0]
            self.ctx.report(current, length)

        def put_part(index: int) -> tuple[int, str]:
            if failed.is_set():
                raise UploaderError("aborted")
            self.ctx.check_cancelled()
            with lock:
                stream.seek(start + index * size)
                data = stream.read(min(size, length - index * size))
            digest = hashlib.sha256(data).hexdigest() if self.s.signed_payload else UNSIGNED_PAYLOAD
            r = self.send("PUT", key, [("partNumber", str(index + 1)), ("uploadId", upload_id)],
                          body=_Counter(data, on_read), payload_hash=digest)
            self.check(r, f"UploadPart {index + 1}")
            etag = r.headers.get("ETag", "").strip()
            if not etag:
                raise UploaderError(f"S3 UploadPart {index + 1} returned no ETag")
            return index + 1, etag

        workers = max(1, min(16, self.s.multipart_concurrency, count))
        try:
            with concurrent.futures.ThreadPoolExecutor(workers, thread_name_prefix="s3-part") as pool:
                futures = [pool.submit(put_part, i) for i in range(count)]
                parts = []
                for f in futures:
                    try:
                        parts.append(f.result())
                    except BaseException:
                        failed.set()
                        raise
            xml = "<CompleteMultipartUpload>" + "".join(
                f"<Part><PartNumber>{n}</PartNumber><ETag>{_xml_escape(e)}</ETag></Part>" for n, e in sorted(parts)
            ) + "</CompleteMultipartUpload>"
            body = xml.encode("utf-8")
            self.check(self.send("POST", key, [("uploadId", upload_id)], body,
                                 headers={"Content-Type": "application/xml"}), "CompleteMultipartUpload")
        except (UploaderError, UploadCancelled):
            try:
                self.send("DELETE", key, [("uploadId", upload_id)], b"")
            except UploaderError:
                pass
            raise

    def list_objects(self, prefix: str = "", max_keys: int = 1) -> requests.Response:
        q = [("list-type", "2"), ("max-keys", str(max_keys))]
        if prefix:
            q.append(("prefix", prefix))
        return self.check(self.send("GET", "", q, b"", payload_hash=EMPTY_SHA256), "ListObjectsV2")


def _xml_find(text: str, local_name: str) -> str:
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return ""
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == local_name:
            return (el.text or "").strip()
    return ""


def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def upload_path(settings: AmazonS3Settings, file_name: str) -> str:
    """AmazonS3.GetUploadPath: '<prefix>/<name>', extension dropped when configured for that kind of file."""
    prefix = parse_folder(settings.object_prefix)
    if ((settings.remove_extension_image and is_image_file(file_name))
            or (settings.remove_extension_text and is_text_file(file_name))
            or (settings.remove_extension_video and is_video_file(file_name))):
        file_name = without_extension(file_name)
    return combine_url(prefix, file_name)


def object_base_url(settings: AmazonS3Settings) -> str:
    if settings.use_custom_cname and settings.custom_domain:
        url = parse_pattern(settings.custom_domain, NameParserType.URL)
    else:
        url = combine_url(settings.endpoint, settings.bucket)
    return fix_prefix(url)


def generate_url(settings: AmazonS3Settings, key: str) -> str:
    if not settings.endpoint or not settings.bucket:
        return ""
    return combine_url(object_base_url(settings), url_encode_path(key))


@register("file", "AmazonS3", AmazonS3Settings, title="Amazon S3 / S3-compatible", homepage="https://aws.amazon.com/s3/")
class AmazonS3Uploader(FileUploader):
    part_size: int | None = None  # override of the multipart part size (tests use the 5 MiB minimum)

    def validate(self) -> list[str]:
        s = self.settings
        missing = [n for n, v in (("access key ID", s.access_key_id), ("secret access key", s.secret_access_key),
                                  ("endpoint", s.endpoint), ("bucket", s.bucket)) if not v]
        return [f"{', '.join(missing)} must be set"] if missing else []

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        s = self.settings
        key = upload_path(s, file_name)
        content_type = s.content_type.strip() or mime_type(file_name)
        client = S3Client(s, self.ctx)
        if s.use_multipart_upload and stream_size(stream) > 0:
            client.multipart(key, stream, content_type, self.part_size)
        else:
            client.put_object(key, stream, content_type)
        return UploadResult(url=generate_url(s, key))


def test_connection(settings: AmazonS3Settings, ctx: UploadContext) -> str:
    """List one object of the bucket with the configured credentials."""
    client = S3Client(settings, ctx)
    resp = client.list_objects(parse_folder(settings.object_prefix))
    count = _xml_find(resp.text, "KeyCount") or "0"
    return f"Bucket '{settings.bucket}' is reachable (region {client.region}); listed {count} object(s)"
