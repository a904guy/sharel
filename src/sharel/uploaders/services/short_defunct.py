"""URL shorteners ShareX lists that no longer offer a usable API (shown as unavailable, with evidence)."""
from __future__ import annotations

from ..registry import register_defunct

register_defunct(
    "shortener", "QRnet", "https://qr.net/api/short",
    "qr.net is now a QR-code generator; the /api/short shortening endpoint ShareX used redirects to the "
    "homepage", title="qr.net")
register_defunct(
    "shortener", "TwoGP", "https://www.2.gp/api/short",
    "2.gp was relaunched (powered by Shrunken.com) without a public API; /api/short answers "
    "404 'The route api/short could not be found.'", title="2.gp")
register_defunct(
    "shortener", "FirebaseDynamicLinks", "https://firebase.google.com/support/dynamic-links-faq",
    "Google shut Firebase Dynamic Links down on 25 August 2025; links can no longer be created",
    title="Firebase Dynamic Links")
