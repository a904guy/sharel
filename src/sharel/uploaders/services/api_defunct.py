"""File destinations ShareX lists that no longer work, with the evidence that they are gone."""
from __future__ import annotations

from ..registry import register_defunct

# Lambda (lbda.net / lambda.sx): lbda.net's origin has been down behind Cloudflare (HTTP 521) since at
# least June 2026 and still is; lambda.sx now sits behind HTTP basic auth and earlier in 2026 served an
# unrelated site, so there is no upload API left.
register_defunct("file", "Lambda", "https://web.archive.org/web/20260620134940/https://lbda.net/",
                 "lbda.net is offline (Cloudflare 521, origin down) and lambda.sx no longer hosts files",
                 title="Lambda")

# SendSpace: the website still runs, but the REST API every client used (api.sendspace.com/rest) answers
# HTTP 404 "File not found." for every method, including the key-less plans.list that returned data in
# the last archived capture (https://web.archive.org/web/20241001151613/https://api.sendspace.com/rest/
# ?method=plans.list); the developer program pages (/developers, /dev_manual.html) are 404 too, so no
# application key can be obtained either.
register_defunct("file", "SendSpace", "https://api.sendspace.com/rest/?method=plans.list",
                 "SendSpace's developer API was withdrawn: every api.sendspace.com/rest call returns 404 and the "
                 "developer pages are gone", title="SendSpace")
