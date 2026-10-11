"""Amazon S3 destination: SigV4 checked by a real moto server (IAM auth on) and by botocore as an oracle."""
from __future__ import annotations

import datetime as _dt
import io
import json
import os

import pytest
from _fileproto_servers import free_port

from sharel.uploaders.base import UploadContext, UploaderError
from sharel.uploaders.services import file_s3
from sharel.uploaders.services.file_s3 import AmazonS3Settings, AmazonS3Uploader

BUCKET = "sharel-test"


@pytest.fixture(scope="module")
def moto_s3():
    """A moto server with an IAM user allowed s3:*; signature checking is switched on after the setup calls."""
    import boto3
    from moto import settings as moto_settings
    from moto.core.authorization import ActionAuthenticatorMixin
    from moto.server import ThreadedMotoServer

    port = free_port()
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=port, verbose=False)
    server.start()
    endpoint = f"http://127.0.0.1:{port}"
    setup = {"endpoint_url": endpoint, "region_name": "us-east-1", "aws_access_key_id": "setup",
             "aws_secret_access_key": "setup"}
    iam = boto3.client("iam", **setup)
    iam.create_user(UserName="sharel")
    iam.put_user_policy(UserName="sharel", PolicyName="all-s3", PolicyDocument=json.dumps(
        {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "s3:*", "Resource": "*"}]}))
    key = iam.create_access_key(UserName="sharel")["AccessKey"]
    boto3.client("s3", **setup).create_bucket(Bucket=BUCKET)
    old = moto_settings.INITIAL_NO_AUTH_ACTION_COUNT
    moto_settings.INITIAL_NO_AUTH_ACTION_COUNT = 0
    ActionAuthenticatorMixin.request_count = 0
    s3 = boto3.client("s3", endpoint_url=endpoint, region_name="us-east-1",
                      aws_access_key_id=key["AccessKeyId"], aws_secret_access_key=key["SecretAccessKey"])
    try:
        yield endpoint, key["AccessKeyId"], key["SecretAccessKey"], s3
    finally:
        moto_settings.INITIAL_NO_AUTH_ACTION_COUNT = old
        server.stop()


def _settings(moto_s3, **kw) -> AmazonS3Settings:
    endpoint, ak, sk, _ = moto_s3
    base = {"access_key_id": ak, "secret_access_key": sk, "endpoint": endpoint, "region": "us-east-1",
            "bucket": BUCKET, "use_path_style": True, "signed_payload": True}
    base.update(kw)
    return AmazonS3Settings(**base)


def test_put_object_signed(moto_s3):
    s3 = moto_s3[3]
    progress = []
    st = _settings(moto_s3, object_prefix="shots/%y", storage_class="STANDARD_IA")
    data = os.urandom(70_000)
    res = AmazonS3Uploader(st, UploadContext(progress=lambda d, t: progress.append((d, t)))).upload(
        io.BytesIO(data), "my shot.png")
    year = f"{_dt.datetime.now().year:04d}"
    key = f"shots/{year}/my shot.png"
    obj = s3.get_object(Bucket=BUCKET, Key=key)
    assert obj["Body"].read() == data and obj["ContentType"] == "image/png"
    assert obj.get("StorageClass") == "STANDARD_IA"
    grants = s3.get_object_acl(Bucket=BUCKET, Key=key)["Grants"]
    assert any(g["Grantee"].get("URI", "").endswith("/global/AllUsers") and g["Permission"] == "READ"
               for g in grants)
    assert res.url == f"{moto_s3[0]}/{BUCKET}/shots/{year}/my%20shot.png"
    assert progress[-1] == (len(data), len(data))


def test_wrong_secret_is_rejected_by_server(moto_s3):
    st = _settings(moto_s3, secret_access_key="not-the-secret")
    with pytest.raises(UploaderError, match="SignatureDoesNotMatch"):
        AmazonS3Uploader(st, UploadContext()).upload(io.BytesIO(b"x"), "x.txt")


def test_unsigned_payload_private_object_custom_type(moto_s3):
    s3 = moto_s3[3]
    st = _settings(moto_s3, signed_payload=False, set_public_acl=False, object_prefix="", content_type="text/x-log")
    AmazonS3Uploader(st, UploadContext()).upload(io.BytesIO(b"log line"), "a+b (1).log")
    obj = s3.get_object(Bucket=BUCKET, Key="a+b (1).log")
    assert obj["Body"].read() == b"log line" and obj["ContentType"] == "text/x-log"
    grants = s3.get_object_acl(Bucket=BUCKET, Key="a+b (1).log")["Grants"]
    assert not any("AllUsers" in g["Grantee"].get("URI", "") for g in grants)


def test_multipart_upload_parallel_parts(moto_s3):
    s3 = moto_s3[3]
    progress = []
    st = _settings(moto_s3, use_multipart_upload=True, multipart_concurrency=3, object_prefix="mp")
    up = AmazonS3Uploader(st, UploadContext(progress=lambda d, t: progress.append((d, t))))
    up.part_size = file_s3.MINIMUM_PART_SIZE
    data = os.urandom(file_s3.MINIMUM_PART_SIZE * 2 + 12345)
    res = up.upload(io.BytesIO(data), "big.bin")
    obj = s3.get_object(Bucket=BUCKET, Key="mp/big.bin")
    assert obj["Body"].read() == data
    assert obj["ETag"].strip('"').endswith("-3")  # three parts
    assert res.url.endswith(f"/{BUCKET}/mp/big.bin") and progress[-1][0] == len(data)


def test_test_connection(moto_s3):
    msg = file_s3.test_connection(_settings(moto_s3), UploadContext())
    assert f"Bucket '{BUCKET}' is reachable" in msg
    with pytest.raises(UploaderError, match="SignatureDoesNotMatch"):
        file_s3.test_connection(_settings(moto_s3, secret_access_key="bad"), UploadContext())


# ---- URL building, region, addressing ----------------------------------------------------------

def test_urls_custom_domain_and_remove_extension():
    st = AmazonS3Settings(endpoint="s3.eu-west-2.amazonaws.com", bucket="b", object_prefix="/x/",
                          remove_extension_image=True)
    key = file_s3.upload_path(st, "pic.png")
    assert key == "x/pic" and file_s3.generate_url(st, key) == "https://s3.eu-west-2.amazonaws.com/b/x/pic"
    st.use_custom_cname, st.custom_domain = True, "cdn.example.com/i"
    assert file_s3.generate_url(st, "x/a b.png") == "https://cdn.example.com/i/x/a%20b.png"
    assert file_s3.upload_path(st, "notes.txt") == "x/notes.txt"


@pytest.mark.parametrize(("endpoint", "region", "expected"), [
    ("s3.amazonaws.com", "", "us-east-1"),
    ("s3.eu-central-1.amazonaws.com", "", "eu-central-1"),
    ("https://s3-ap-south-1.amazonaws.com/", "", "ap-south-1"),
    ("abc123.r2.cloudflarestorage.com", "auto", "auto"),
    ("https://abc123.r2.cloudflarestorage.com", "", "auto"),
    ("s3.us-west-004.backblazeb2.com", "", "us-west-004"),
    ("nyc3.digitaloceanspaces.com", "", "nyc3"),
    ("s3.eu-central-1.wasabisys.com", "", "eu-central-1"),
    ("http://minio.lan:9000", "", "us-east-1"),
    ("s3.us-west-004.backblazeb2.com", "eu-x", "eu-x"),
])
def test_region(endpoint, region, expected):
    assert file_s3.region_for(AmazonS3Settings(endpoint=endpoint, region=region)) == expected


def test_addressing_styles():
    ctx = UploadContext()
    vh = file_s3.S3Client(AmazonS3Settings(endpoint="s3.amazonaws.com", bucket="bkt"), ctx)
    assert vh.request_target("a/b c.png") == ("https://", "bkt.s3.amazonaws.com", "/a/b%20c.png")
    dotted = file_s3.S3Client(AmazonS3Settings(endpoint="s3.amazonaws.com", bucket="my.bkt"), ctx)
    assert dotted.request_target("k") == ("https://", "s3.amazonaws.com", "/my.bkt/k")
    minio = file_s3.S3Client(AmazonS3Settings(endpoint="http://minio:9000/", bucket="b", use_path_style=True), ctx)
    assert minio.request_target("k") == ("http://", "minio:9000", "/b/k")


@pytest.mark.parametrize(("method", "host", "uri", "query", "payload"), [
    ("PUT", "bkt.s3.eu-west-1.amazonaws.com", "/ShareL/2026/a%20b%2B%28c%29.png", "", file_s3.UNSIGNED_PAYLOAD),
    ("POST", "acct.r2.cloudflarestorage.com", "/bkt/k.bin", "uploads=", file_s3.EMPTY_SHA256),
    ("PUT", "s3.us-west-004.backblazeb2.com", "/b/k.bin", "partNumber=2&uploadId=abc%2Fdef", "UNSIGNED-PAYLOAD"),
])
def test_signature_matches_botocore(method, host, uri, query, payload):
    """botocore's S3SigV4Auth computes the same signature for virtual-hosted, R2 and B2-style requests."""
    from botocore.auth import S3SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.credentials import Credentials

    now = _dt.datetime(2026, 5, 6, 7, 8, 9, tzinfo=_dt.UTC)
    region = "eu-west-1"
    headers = {"Content-Type": "image/png", "x-amz-acl": "public-read", "x-amz-storage-class": "STANDARD"}
    signed = file_s3.sign_request(method, host, uri, query, headers, payload, "AKID", "SECRET/KEY+", region, now=now)
    url = f"https://{host}{uri}" + (f"?{query}" if query else "")
    req = AWSRequest(method=method, url=url, data=b"",
                     headers={k: v for k, v in signed.items() if k not in ("Authorization", "Host")})
    req.context["timestamp"] = now.strftime("%Y%m%dT%H%M%SZ")
    auth = S3SigV4Auth(Credentials("AKID", "SECRET/KEY+"), "s3", region)
    canonical = auth.canonical_request(req)
    expected = auth.signature(auth.string_to_sign(req, canonical), req)
    assert signed["Authorization"].endswith(f"Signature={expected}")
    assert "SignedHeaders=content-type;host;x-amz-acl;x-amz-content-sha256;x-amz-date;x-amz-storage-class" \
        in signed["Authorization"]
