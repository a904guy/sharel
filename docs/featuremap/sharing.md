| Email | src/sharel/uploaders/services/share_email.py | smtplib (STARTTLS/SSL/plain; to prompt, remember last, automatic send) or mailto: link | done | tests/test_sharing.py::test_email_smtp_prompts_recipient_remembers_and_sends |
| Facebook | src/sharel/uploaders/services/share_templates.py | https://www.facebook.com/sharer/sharer.php?u= | done | tests/test_sharing.py::test_template_share_url[Facebook] |
| Reddit | src/sharel/uploaders/services/share_templates.py | https://www.reddit.com/submit?url= | done | tests/test_sharing.py::test_template_share_url[Reddit] |
| Pinterest | src/sharel/uploaders/services/share_templates.py | https://www.pinterest.com/pin/create/button/?url=&media= | done | tests/test_sharing.py::test_template_share_url[Pinterest] |
| Tumblr | src/sharel/uploaders/services/share_templates.py | https://www.tumblr.com/widgets/share/tool?posttype=link&canonicalUrl=&content= | done | tests/test_sharing.py::test_template_share_url[Tumblr] |
| LinkedIn | src/sharel/uploaders/services/share_templates.py | https://www.linkedin.com/sharing/share-offsite/?url= | done | tests/test_sharing.py::test_template_share_url[LinkedIn] |
| StumbleUpon | - | - | excluded: https://techcrunch.com/2018/05/24/so-long-stumbleupon/ | - |
| Delicious | - | - | excluded: https://martech.org/?p=216615 | - |
| VK | src/sharel/uploaders/services/share_templates.py | https://vk.com/share.php?url= | done | tests/test_sharing.py::test_template_share_url[VK] |
| Pushbullet | src/sharel/uploaders/services/share_pushbullet.py | POST api.pushbullet.com/v2/pushes type=link (Access-Token; device_iden or all; GET /v2/devices) | done | tests/test_sharing.py::test_pushbullet_pushes_link_to_all_devices |
| GoogleImageSearch | src/sharel/uploaders/services/share_templates.py | https://lens.google.com/uploadbyurl?url= | done | tests/test_sharing.py::test_template_share_url[GoogleImageSearch] |
| BingVisualSearch | src/sharel/uploaders/services/share_templates.py | https://www.bing.com/images/searchbyimage?cbir=sbi&imgurl= | done | tests/test_sharing.py::test_template_share_url[BingVisualSearch] |
| CustomURLSharingService | uploaders/services/*.py | HTTPS API | planned | - |
