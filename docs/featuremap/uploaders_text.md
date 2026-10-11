| Pastebin | uploaders/services/text_pastebin.py | Pastebin API api_post.php + api_login.php (user api_dev_key) | done | tests/test_uploaders_text.py::test_pastebin_login_then_paste_raw_url |
| Paste2 | uploaders/services/text_paste2.py | paste2.org form POST, URL from the redirect | done | tests/test_uploaders_text.py::test_paste2_follows_redirect_location |
| Slexy | uploaders/services/text_slexy.py | - (service shut down; slexy.org is a parked domain for sale) | excluded: https://forum.infinityfree.com/t/can-i-host-a-public-pastebin/44753 | tests/test_uploaders_text.py::test_text_services_registered |
| Paste_ee | uploaders/services/text_paste_ee.py | Paste.ee API v1 POST /v1/pastes with X-Auth-Token | done | tests/test_uploaders_text.py::test_paste_ee_v1 |
| Gist | uploaders/services/text_gist.py | GitHub REST POST /gists; personal token or OAuth App loopback flow; GHE API URL | done | tests/test_uploaders_text.py::test_gist_personal_token |
| Upaste | uploaders/services/text_upaste.py | uPaste API v2 POST /api/v2/paste with Bearer key | done | tests/test_uploaders_text.py::test_upaste_v2 |
| Hastebin | uploaders/services/text_hastebin.py | POST <server>/documents (Bearer token for hastebin.com) | done | tests/test_uploaders_text.py::test_hastebin_toptal_with_token |
| OneTimeSecret | uploaders/services/text_onetimesecret.py | OneTimeSecret API v2 /api/v2/secret/conceal (Basic auth) or /api/v2/guest/secret/conceal | done | tests/test_uploaders_text.py::test_onetimesecret_authenticated |
| Pastie | uploaders/services/text_pastie.py | pastie.org (pastie_express) POST /pastes/create, URL from the redirect | done | tests/test_uploaders_text.py::test_pastie_create_redirect |
| PrivateBin | uploaders/services/text_privatebin.py | PrivateBin v2 JSON API; client-side PBKDF2-SHA256 + AES-256-GCM + raw deflate | done | tests/test_privatebin.py::test_upload_request_shape_and_urls |
| CustomTextUploader | uploaders/custom/uploader.py + ui/windows/customuploader.py | custom uploader (.sxcu) via uploaders/dispatch | done | tests/test_ui_customuploader.py::test_dispatch_to_custom_uploaders_for_every_destination_type |
| FileUploader | uploaders/dispatch.py | destination_for redirects text tasks to the text file destination | done | tests/test_uploaders_image.py::test_dispatch_file_uploader_redirects_image_and_text_to_file_destination |
