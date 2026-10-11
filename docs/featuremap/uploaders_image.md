| Imgur | uploaders/services/img_imgur.py | Imgur API v3 POST /3/upload; anonymous Client-ID or OAuth 2 code flow (loopback, fixed port) | done | tests/test_uploaders_image.py::test_imgur_anonymous_upload_direct_link |
| ImageShack | uploaders/services/img_imageshack.py | ImageShack API v2 POST /v2/images (api_key + auth_token from /v2/user/login) | done | tests/test_uploaders_image.py::test_imageshack_upload |
| Flickr | uploaders/services/img_flickr.py | OAuth 1.0a (uploaders/oauth1.py) + up.flickr.com/services/upload + flickr.photos.getSizes/getInfo | done | tests/test_uploaders_image.py::test_flickr_upload_direct_link |
| Photobucket | uploaders/services/img_photobucket.py | - (developer API retired; api.photobucket.com/login/request returns 404) | excluded: https://web.archive.org/web/20260516202928/http://developer.photobucket.com/ | tests/test_uploaders_image.py::test_image_services_registered |
| Chevereto | uploaders/services/img_chevereto.py | Chevereto API V1.1 POST /api/1/upload with X-API-Key | done | tests/test_uploaders_image.py::test_chevereto_upload |
| Vgyme | uploaders/services/img_vgyme.py | POST https://vgy.me/upload (optional userkey) | done | tests/test_uploaders_image.py::test_vgyme_upload |
| CustomImageUploader | uploaders/services/*.py | HTTPS API | planned | - |
| FileUploader | uploaders/dispatch.py | destination_for redirects image tasks to the image file destination | done | tests/test_uploaders_image.py::test_dispatch_file_uploader_redirects_image_and_text_to_file_destination |
