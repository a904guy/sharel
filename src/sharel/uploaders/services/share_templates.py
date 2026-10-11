"""URL sharing services that are just a page to open (ShareX SimpleURLSharingService subclasses).

``share_url`` returns the share page with the URL percent-encoded (``URLTemplateSharer``); the app opens
it in the browser. ShareX's formats are kept where they still work, and updated where the service moved:

* LinkedIn: ``shareArticle`` is legacy; ``sharing/share-offsite/?url=`` is the documented share URL.
* Tumblr: ``share?v=3&u=`` is gone; the share tool is ``widgets/share/tool`` (posttype=link).
* Bing: ``images/search?view=detailv2&iss=sbi&q=imgurl:`` now drops the image and lands on the Images
  home; ``images/searchbyimage?cbir=sbi&imgurl=`` opens visual search for the image URL.
* Google: "Search by image" is Google Lens, ``lens.google.com/uploadbyurl?url=``.
* Everything uses https.
"""
from __future__ import annotations

from ..base import URLTemplateSharer
from ..registry import register


@register("sharing", "Facebook", None, title="Facebook", homepage="https://www.facebook.com")
class FacebookSharer(URLTemplateSharer):
    template = "https://www.facebook.com/sharer/sharer.php?u={url}"


@register("sharing", "Reddit", None, title="Reddit", homepage="https://www.reddit.com")
class RedditSharer(URLTemplateSharer):
    template = "https://www.reddit.com/submit?url={url}"


@register("sharing", "Pinterest", None, title="Pinterest", homepage="https://www.pinterest.com")
class PinterestSharer(URLTemplateSharer):
    # The shared URL is both the pin's link and its image (ShareX shares uploaded images this way)
    template = "https://www.pinterest.com/pin/create/button/?url={url}&media={url}"


@register("sharing", "Tumblr", None, title="Tumblr", homepage="https://www.tumblr.com")
class TumblrSharer(URLTemplateSharer):
    template = "https://www.tumblr.com/widgets/share/tool?posttype=link&canonicalUrl={url}&content={url}"


@register("sharing", "LinkedIn", None, title="LinkedIn", homepage="https://www.linkedin.com")
class LinkedInSharer(URLTemplateSharer):
    template = "https://www.linkedin.com/sharing/share-offsite/?url={url}"


@register("sharing", "VK", None, title="VK", homepage="https://vk.com")
class VkSharer(URLTemplateSharer):
    template = "https://vk.com/share.php?url={url}"


@register("sharing", "GoogleImageSearch", None, title="Google Lens", homepage="https://lens.google.com")
class GoogleLensSharer(URLTemplateSharer):
    template = "https://lens.google.com/uploadbyurl?url={url}"


@register("sharing", "BingVisualSearch", None, title="Bing visual search", homepage="https://www.bing.com/visualsearch")
class BingVisualSearchSharer(URLTemplateSharer):
    template = "https://www.bing.com/images/searchbyimage?cbir=sbi&imgurl={url}"
