"""YouTube (ShareX YouTube.cs, YouTube Data API v3 ``videos.insert``).

Resumable upload to ``https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status``
with ``{"snippet": {title, description, categoryId}, "status": {privacyStatus, selfDeclaredMadeForKids}}``.
The title defaults to the file name without extension (YouTube allows 100 characters and no ``<``/``>``);
with "show dialog" the title, description and privacy are asked before uploading. The result is
``https://youtu.be/<id>`` (or ``https://www.youtube.com/watch?v=<id>``); ``status.uploadStatus`` of
``failed``/``rejected`` is reported as an error.

Note: videos uploaded through an API project that Google has not audited are locked to private.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePath
from typing import BinaryIO

from ..base import FileUploader, UploadContext, UploaderError, UploadResult
from ..oauth import authorize as oauth_authorize
from ..oauth import bearer
from ..registry import OAuth2Info, register, setting
from ._api_common import json_ok, mime_type, remaining_size
from ._api_google import CLIENT_HELP, google_spec, resumable_upload

UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
PRIVACY = ["public", "unlisted", "private"]
# https://developers.google.com/youtube/v3/docs/videoCategories (US list, assignable ones)
CATEGORIES = {"1": "Film & Animation", "2": "Autos & Vehicles", "10": "Music", "15": "Pets & Animals",
              "17": "Sports", "19": "Travel & Events", "20": "Gaming", "22": "People & Blogs", "23": "Comedy",
              "24": "Entertainment", "25": "News & Politics", "26": "Howto & Style", "27": "Education",
              "28": "Science & Technology", "29": "Nonprofits & Activism"}

SPEC = google_spec("https://www.googleapis.com/auth/youtube.upload",
                   "https://www.googleapis.com/auth/userinfo.profile")


@dataclass
class YouTubeSettings:
    client_id: str = setting("", label="Client ID", help=CLIENT_HELP.format(api="YouTube Data API v3"))
    client_secret: str = setting("", label="Client secret", secret=True)
    oauth2: OAuth2Info = setting(label="Account", kind="oauth", default_factory=OAuth2Info)  # noqa: RUF009
    privacy_type: str = setting("public", label="Privacy", choices=PRIVACY,
                                help="Unaudited API projects can only upload private videos")
    use_shortened_link: bool = setting(True, label="Use shortened link (youtu.be)")
    show_dialog: bool = setting(False, label="Ask for title, description and privacy before uploading")
    description: str = setting("", label="Description", multiline=True)
    category_id: str = setting("22", label="Category", choices=list(CATEGORIES),
                               help=", ".join(f"{k} = {v}" for k, v in CATEGORIES.items()))
    made_for_kids: bool = setting(False, label="Made for kids")


def authorize(settings: YouTubeSettings, ctx: UploadContext) -> OAuth2Info:
    """The settings page's Authorize button."""
    settings.oauth2 = oauth_authorize(SPEC, settings.client_id, settings.client_secret, ctx)
    ctx.save_settings()
    return settings.oauth2


def clean_title(text: str) -> str:
    """YouTube titles: at most 100 characters, no angle brackets, not empty."""
    title = text.replace("<", "").replace(">", "").strip()
    return (title or "Video")[:100]


def video_title(file_name: str) -> str:
    return clean_title(PurePath(file_name).stem)


@register("file", "YouTube", YouTubeSettings, title="YouTube", oauth=SPEC, homepage="https://www.youtube.com")
class YouTubeUploader(FileUploader):
    chunk_size = 32 * 256 * 1024

    def validate(self) -> list[str]:
        o = self.settings.oauth2
        return [] if (o.access_token or o.refresh_token) else ["YouTube is not authorized"]

    def upload(self, stream: BinaryIO, file_name: str) -> UploadResult:
        s = self.settings
        title, description = video_title(file_name), s.description
        privacy = s.privacy_type if s.privacy_type in PRIVACY else "public"
        if s.show_dialog:
            answer = self.ctx.prompt_input("YouTube video title", title)
            if answer is None:
                raise UploaderError("YouTube upload cancelled")
            title = clean_title(answer) if answer.strip() else title
            description = self.ctx.prompt_input("YouTube video description", description) or ""
            choice = self.ctx.prompt_select("YouTube video privacy", [privacy] + [p for p in PRIVACY if p != privacy])
            if choice is None:
                raise UploaderError("YouTube upload cancelled")
            privacy = choice
        metadata = {"snippet": {"title": title, "description": description, "categoryId": s.category_id or "22"},
                    "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": bool(s.made_for_kids)}}
        resp = resumable_upload(self.ctx, UPLOAD_URL, bearer(SPEC, s, self.ctx), metadata, stream,
                                remaining_size(stream), mime_type(file_name) if "." in file_name else "video/*",
                                self.chunk_size)
        video = json_ok(resp, "YouTube upload")
        status = video.get("status") or {}
        if status.get("uploadStatus") == "failed":
            raise UploaderError(f"YouTube upload failed: {status.get('failureReason')}", resp.text)
        if status.get("uploadStatus") == "rejected":
            raise UploaderError(f"YouTube upload rejected: {status.get('rejectionReason')}", resp.text)
        vid = video.get("id")
        if not vid:
            raise UploaderError("YouTube upload: no video id in the response", resp.text)
        url = f"https://youtu.be/{vid}" if s.use_shortened_link else f"https://www.youtube.com/watch?v={vid}"
        return UploadResult(url=url, thumbnail_url=f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg", response=resp.text)
