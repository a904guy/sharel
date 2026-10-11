"""Slexy: slexy.org shut down; the domain is now a Sedo parking page ("This website is for sale!")."""
from __future__ import annotations

from ..registry import register_defunct

EVIDENCE = "https://forum.infinityfree.com/t/can-i-host-a-public-pastebin/44753"

register_defunct("text", "Slexy", EVIDENCE,
                 "slexy.org shut down (reported May 2021); the domain https://slexy.org/ is now parked for sale",
                 title="Slexy")
