"""URL sharing services ShareX lists that are gone (shown as unavailable, with evidence)."""
from __future__ import annotations

from ..registry import register_defunct

register_defunct(
    "sharing", "StumbleUpon", "https://techcrunch.com/2018/05/24/so-long-stumbleupon/",
    "StumbleUpon shut down on 30 June 2018 (accounts moved to Mix); stumbleupon.com/submit now returns 404",
    title="StumbleUpon")
register_defunct(
    "sharing", "Delicious", "https://martech.org/?p=216615",
    "Delicious went read-only and stopped accepting new bookmarks in June 2017; delicious.com/save no "
    "longer works", title="Delicious")
