"""Photobucket: the developer API ShareX used (OAuth 1.0 at api.photobucket.com) is gone.

The developer portal (https://developer.photobucket.com/) now answers S3 "AccessDenied" and the API's
login endpoints (https://api.photobucket.com/login/request) return 404 Not Found; the current
Photobucket app has no public upload API.
"""
from __future__ import annotations

from ..registry import register_defunct

EVIDENCE = "https://developer.photobucket.com/"

register_defunct("image", "Photobucket", EVIDENCE,
                 "Photobucket's developer API was retired: the developer portal is gone and "
                 "api.photobucket.com/login/request returns 404", title="Photobucket")
