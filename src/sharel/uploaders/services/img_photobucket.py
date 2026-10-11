"""Photobucket: the developer API ShareX used (OAuth 1.0 at api.photobucket.com) is gone.

The developer host no longer documents any API: the Wayback Machine capture of May 2026 shows only
Photobucket's generic image-viewer ad page, and the live https://developer.photobucket.com/ now answers
S3 "AccessDenied". The API's login endpoint (https://api.photobucket.com/login/request) returns 404
Not Found, and the current Photobucket app (photobucket.com/developer is an empty SPA route) has no
public upload API.
"""
from __future__ import annotations

from ..registry import register_defunct

EVIDENCE = "https://web.archive.org/web/20260516202928/http://developer.photobucket.com/"

register_defunct("image", "Photobucket", EVIDENCE,
                 "Photobucket's developer API was retired: the developer portal is gone and "
                 "api.photobucket.com/login/request returns 404", title="Photobucket")
