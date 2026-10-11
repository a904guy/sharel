| Email | uploaders/services/share_email.py | smtplib (STARTTLS/SSL/plain; to prompt, remember last, automatic send) or mailto: link | done | tests/test_sharing.py::test_email_smtp_prompts_recipient_remembers_and_sends |
| Facebook | uploaders/services/share_templates.py | https://www.facebook.com/sharer/sharer.php?u= | done | tests/test_sharing.py::test_template_share_url[Facebook] |
| Reddit | uploaders/services/share_templates.py | https://www.reddit.com/submit?url= | done | tests/test_sharing.py::test_template_share_url[Reddit] |
| Pinterest | uploaders/services/share_templates.py | https://www.pinterest.com/pin/create/button/?url=&media= | done | tests/test_sharing.py::test_template_share_url[Pinterest] |
| Tumblr | uploaders/services/share_templates.py | https://www.tumblr.com/widgets/share/tool?posttype=link&canonicalUrl=&content= | done | tests/test_sharing.py::test_template_share_url[Tumblr] |
| LinkedIn | uploaders/services/share_templates.py | https://www.linkedin.com/sharing/share-offsite/?url= | done | tests/test_sharing.py::test_template_share_url[LinkedIn] |
| StumbleUpon | uploaders/services/share_defunct.py | register_defunct (shown unavailable with evidence) | excluded: https://techcrunch.com/2018/05/24/so-long-stumbleupon/ | tests/test_sharing.py::test_defunct_sharing_registered_with_evidence[StumbleUpon] |
| Delicious | uploaders/services/share_defunct.py | register_defunct (shown unavailable with evidence) | excluded: https://martech.org/?p=216615 | tests/test_sharing.py::test_defunct_sharing_registered_with_evidence[Delicious] |
| VK | uploaders/services/share_templates.py | https://vk.com/share.php?url= | done | tests/test_sharing.py::test_template_share_url[VK] |
| Pushbullet | uploaders/services/share_pushbullet.py | POST api.pushbullet.com/v2/pushes type=link (Access-Token; device_iden or all; GET /v2/devices) | done | tests/test_sharing.py::test_pushbullet_pushes_link_to_all_devices |
| GoogleImageSearch | uploaders/services/share_templates.py | https://lens.google.com/uploadbyurl?url= | done | tests/test_sharing.py::test_template_share_url[GoogleImageSearch] |
| BingVisualSearch | uploaders/services/share_templates.py | https://www.bing.com/images/searchbyimage?cbir=sbi&imgurl= | done | tests/test_sharing.py::test_template_share_url[BingVisualSearch] |
| CustomURLSharingService | uploaders/custom/uploader.py + ui/windows/customuploader.py | custom uploader (.sxcu) via uploaders/dispatch | done | tests/test_ui_customuploader.py::test_dispatch_to_custom_uploaders_for_every_destination_type |
