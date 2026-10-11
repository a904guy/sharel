"""Flickr (ShareX ImageUploaders/FlickrUploader.cs): OAuth 1.0a, the upload API, then
``flickr.photos.getSizes`` for the direct file URL and ``flickr.photos.getInfo`` for the photo page.

The user creates an API key at https://www.flickr.com/services/apps/create/ and authorizes it with
"write" permission through the browser (loopback ``oauth_callback``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO

from lxml import etree

from .. import oauth1
from ..base import ImageUploader, UploadContext, UploaderError, UploadResult, check_response, json_of
from ..registry import register, setting

UPLOAD_URL = "https://up.flickr.com/services/upload/"
REST_URL = "https://api.flickr.com/services/rest"
SPEC = oauth1.OAuth1Spec(
    request_token_url="https://www.flickr.com/services/oauth/request_token",
    authorize_url="https://www.flickr.com/services/oauth/authorize",
    access_token_url="https://www.flickr.com/services/oauth/access_token",
    extra_authorize_params={"perms": "write"},
)
THUMBNAIL_LABELS = ("Small", "Small 320", "Thumbnail", "Large Square", "Square")


@dataclass
class FlickrSettings:
    api_key: str = setting("", label="API key", secret=True, help="https://www.flickr.com/services/apps/create/")
    api_secret: str = setting("", label="API secret", secret=True)
    oauth_token: str = setting("", label="Access token", secret=True,
                               help="Filled by Authorize.")
    oauth_token_secret: str = setting("", label="Access token secret", secret=True, hidden=True)
    user_nsid: str = setting("", label="User ID", hidden=True)
    username: str = setting("", label="Signed in as", hidden=True)
    direct_link: bool = setting(True, label="Use direct link",
                                help="Link to the largest image file instead of the photo page.")
    title: str = setting("", label="Title", help="The title of the photo.")
    description: str = setting("", label="Description",
                               help="A description of the photo. May contain some limited HTML.")
    tags: str = setting("", label="Tags", help="A space-separated list of tags to apply to the photo.")
    is_public: str = setting("", label="Is public", choices=["", "0", "1"],
                             help="Set to 0 for no, 1 for yes. Specifies who can view the photo.")
    is_friend: str = setting("", label="Is friend", choices=["", "0", "1"],
                             help="Set to 0 for no, 1 for yes. Specifies who can view the photo.")
    is_family: str = setting("", label="Is family", choices=["", "0", "1"],
                             help="Set to 0 for no, 1 for yes. Specifies who can view the photo.")
    safety_level: str = setting("", label="Safety level", choices=["", "1", "2", "3"],
                                help="Set to 1 for Safe, 2 for Moderate, or 3 for Restricted.")
    content_type: str = setting("", label="Content type", choices=["", "1", "2", "3"],
                                help="Set to 1 for Photo, 2 for Screenshot, or 3 for Other.")
    hidden: str = setting("", label="Hidden", choices=["", "1", "2"],
                          help="Set to 1 to keep the photo in global search results, 2 to hide from public searches.")


def authorize(settings: FlickrSettings, ctx: UploadContext) -> dict[str, str]:
    """Settings page "Authorize": request token, browser approval, access token."""
    tokens = oauth1.authorize(SPEC, settings.api_key, settings.api_secret, ctx)
    settings.oauth_token = tokens["oauth_token"]
    settings.oauth_token_secret = tokens["oauth_token_secret"]
    settings.user_nsid = tokens.get("user_nsid", "")
    settings.username = tokens.get("username", "")
    ctx.save_settings()
    return tokens


def _rsp_xml(text: str) -> etree._Element:
    try:
        root = etree.fromstring(text.strip().encode("utf-8"))
    except etree.XMLSyntaxError as e:
        raise UploaderError(f"Flickr: unexpected response: {text[:300]}", text) from e
    if root.tag != "rsp":
        raise UploaderError(f"Flickr: unexpected response: {text[:300]}", text)
    if root.get("stat") != "ok":
        err = root.find("err")
        msg = err.get("msg") if err is not None else text[:300]
        raise UploaderError(f"Flickr error: {msg}", text)
    return root


@register("image", "Flickr", FlickrSettings, title="Flickr", homepage="https://www.flickr.com")
class FlickrUploader(ImageUploader):
    settings: FlickrSettings

    def validate(self) -> list[str]:
        s = self.settings
        if not s.api_key or not s.api_secret:
            return ["API key and secret are required"]
        if not s.oauth_token or not s.oauth_token_secret:
            return ["Flickr login is required: click Authorize in Destination settings"]
        return []

    def _rest(self, method: str, **params: str) -> dict:
        s = self.settings
        args = {"nojsoncallback": "1", "format": "json", "method": method, **params}
        signed = oauth1.sign_request("GET", REST_URL, s.api_key, s.api_secret, s.oauth_token,
                                     s.oauth_token_secret, params=args)
        body = json_of(self.ctx.get(REST_URL, params=signed), f"Flickr {method}")
        if body.get("stat") != "ok":
            raise UploaderError(f"Flickr {method} failed: {body.get('message', body)}", str(body))
        return body

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        s = self.settings
        args = {k: v for k, v in {
            "title": s.title, "description": s.description, "tags": s.tags, "is_public": s.is_public,
            "is_friend": s.is_friend, "is_family": s.is_family, "safety_level": s.safety_level,
            "content_type": s.content_type, "hidden": s.hidden}.items() if v}
        # The photo part is not part of the signature; every other field (incl. oauth_*) goes in the form
        signed = oauth1.sign_request("POST", UPLOAD_URL, s.api_key, s.api_secret, s.oauth_token,
                                     s.oauth_token_secret, params=args)
        resp = self.ctx.post(UPLOAD_URL, data=signed, files={"photo": (file_name, self.ctx.progress_stream(stream))})
        text = check_response(resp, "Flickr upload")
        photo_id_el = _rsp_xml(text).find("photoid")
        if photo_id_el is None or not (photo_id_el.text or "").strip():
            raise UploaderError("Flickr upload: no photo id in response", text)
        photo_id = photo_id_el.text.strip()
        result = UploadResult(response=text)

        sizes = (self._rest("flickr.photos.getSizes", photo_id=photo_id).get("sizes") or {}).get("size") or []
        if sizes:
            largest = sizes[-1]
            by_label = {sz.get("label"): sz for sz in sizes}
            thumb = next((by_label[label] for label in THUMBNAIL_LABELS if label in by_label), None)
            if thumb:
                result.thumbnail_url = thumb.get("source", "")
            if s.direct_link:
                result.url = largest.get("source", "")
        if not s.direct_link or not result.url:
            info = self._rest("flickr.photos.getInfo", photo_id=photo_id).get("photo") or {}
            urls = (info.get("urls") or {}).get("url") or []
            page = next((u.get("_content") for u in urls if u.get("type") == "photopage"), "")
            owner = (info.get("owner") or {}).get("nsid") or s.user_nsid
            result.url = page or (f"https://www.flickr.com/photos/{owner}/{photo_id}/" if owner else "")
        if not result.url:
            raise UploaderError("Flickr: could not get the photo URL", text)
        return result
