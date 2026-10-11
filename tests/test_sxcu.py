""".sxcu import/export: ShareX's JSON shape, real-world examples, and pre-v13 upgrades."""
from __future__ import annotations

import json

import pytest

from sharel.uploaders.custom import sxcu
from sharel.uploaders.custom.model import CustomUploaderItem


def make(**kw) -> CustomUploaderItem:
    base = {"version": "18.0.0", "name": "My Host", "destination_type": ["ImageUploader", "FileUploader"],
            "request_method": "POST", "request_url": "https://example.com/upload.php", "body": "MultipartFormData",
            "file_form_name": "file", "url": "{json:url}"}
    base.update(kw)
    return CustomUploaderItem(**base)


ROUND_TRIP_ITEMS = [
    make(parameters={"api_key": "k", "private": "true"}, headers={"Authorization": "Basic {base64:u:p}"},
         arguments={"title": "{filename}", "album": "x"}, thumbnail_url="{json:thumb}",
         deletion_url="https://example.com/d/{json:del}", error_message="{json:error}"),
    make(name="", destination_type=["TextUploader"], body="JSON", file_form_name="", request_method="PUT",
         data='{"text": "{input}", "name": "{filename}"}', url="{json:data.link}"),
    make(destination_type=["TextUploader"], body="XML", file_form_name="", data="<paste>{input}</paste>",
         url="{xml:/r/url}"),
    make(destination_type=["URLShortener", "URLSharingService"], body="None", request_method="GET",
         file_form_name="", parameters={"url": "{input}"}, url=""),
    make(destination_type=["TextUploader"], body="FormURLEncoded", file_form_name="", arguments={"c": "{input}"},
         request_method="PATCH"),
    make(body="Binary", file_form_name="", request_method="PUT", request_url="https://s.example/{filename}",
         headers={"X-Key": "abc"}, url="{responseurl}"),
    make(destination_type=[], request_method="DELETE", body="None", file_form_name="", url="", name="Odd"),
]


@pytest.mark.parametrize("item", ROUND_TRIP_ITEMS, ids=lambda it: f"{it.body}-{it.request_method}")
def test_round_trip_export_then_import_is_equal(item):
    assert sxcu.loads(sxcu.dumps(item)) == item


def test_export_key_order_formatting_and_omissions():
    item = ROUND_TRIP_ITEMS[0]
    text = sxcu.dumps(item)
    assert "\r\n" in text and "\n" not in text.replace("\r\n", "")
    assert text.startswith('{\r\n  "Version": "18.0.0",\r\n  "Name": "My Host",')
    obj = json.loads(text)
    assert list(obj) == ["Version", "Name", "DestinationType", "RequestMethod", "RequestURL", "Parameters",
                         "Headers", "Body", "Arguments", "FileFormName", "URL", "ThumbnailURL", "DeletionURL",
                         "ErrorMessage"]
    assert obj["DestinationType"] == "ImageUploader, FileUploader"
    assert obj["Body"] == "MultipartFormData"


def test_export_leaves_out_what_sharex_leaves_out():
    item = make(name="example.com", destination_type=["FileUploader", "ImageUploader"], body="None",
                arguments={"ignored": "1"}, file_form_name="ignored", data="ignored", url="", parameters={})
    obj = sxcu.item_to_dict(item)
    # Name equal to the request URL's host, Body None, Arguments/FileFormName/Data for the wrong body,
    # empty strings and empty dicts are all omitted; RequestMethod is always written.
    assert obj == {"Version": "18.0.0", "DestinationType": "ImageUploader, FileUploader", "RequestMethod": "POST",
                   "RequestURL": "https://example.com/upload.php"}
    # The host comparison ignores "www.".
    assert "Name" not in sxcu.item_to_dict(make(name="host.example", request_url="https://www.host.example/x"))
    assert sxcu.item_to_dict(make(name="Other", request_url="https://www.host.example/x"))["Name"] == "Other"


def test_export_data_only_for_json_and_xml():
    assert "Data" in sxcu.item_to_dict(make(body="JSON", data="{}"))
    assert "Data" in sxcu.item_to_dict(make(body="XML", data="<a/>"))
    assert "Data" not in sxcu.item_to_dict(make(body="Binary", data="{}"))
    assert "Arguments" in sxcu.item_to_dict(make(body="FormURLEncoded", arguments={"a": "b"}))
    assert "FileFormName" not in sxcu.item_to_dict(make(body="FormURLEncoded"))


def test_dump_and_load_files(tmp_path):
    p = sxcu.dump(ROUND_TRIP_ITEMS[1], tmp_path / "x.sxcu")
    assert p.read_bytes().startswith(b"{\r\n")
    assert sxcu.load(p) == ROUND_TRIP_ITEMS[1]
    paths = sxcu.dump_all([make(name="A/B"), make(name="", request_url="https://www.host.example/u")], tmp_path / "o")
    assert sorted(x.name for x in paths) == ["A_B.sxcu", "host.example.sxcu"]


# ---- real-world examples ---------------------------------------------------------------------------------

IMGUR_LIKE = """{
  "Version": "16.1.0",
  "Name": "Imgur-like",
  "DestinationType": "ImageUploader",
  "RequestMethod": "POST",
  "RequestURL": "https://api.imgur-like.example/3/image",
  "Headers": {"Authorization": "Client-ID 0123456789abcde"},
  "Body": "MultipartFormData",
  "FileFormName": "image",
  "URL": "{json:data.link}",
  "DeletionURL": "https://imgur-like.example/delete/{json:data.deletehash}",
  "ErrorMessage": "{json:data.error}"
}"""

XML_API = """{
  "Version": "15.0.0",
  "DestinationType": "ImageUploader, TextUploader, FileUploader",
  "RequestMethod": "POST",
  "RequestURL": "https://xml.example/api/upload",
  "Parameters": {"format": "xml"},
  "Body": "MultipartFormData",
  "Arguments": {"key": "secret", "expire": "%y-%mo"},
  "FileFormName": "upload",
  "URL": "{xml:/rsp/image/url}",
  "ThumbnailURL": "{xml:/rsp/image/thumb}",
  "ErrorMessage": "{xml:/rsp/err/@msg}"
}"""

SHORTENER = """{
  "Version": "17.0.0",
  "DestinationType": "URLShortener",
  "RequestMethod": "GET",
  "RequestURL": "https://is.example/create.php",
  "Parameters": {"format": "simple", "url": "{input}"}
}"""

BINARY_PUT = """{
  "Version": "14.1.0",
  "Name": "WebDAV PUT",
  "DestinationType": "ImageUploader, FileUploader",
  "RequestMethod": "PUT",
  "RequestURL": "https://dav.example/files/{filename}",
  "Headers": {"Authorization": "Basic {base64:me:pw}"},
  "Body": "Binary",
  "URL": "https://dav.example/files/{filename}"
}"""

JSON_PASTE = """{
  "Version": "18.0.0",
  "DestinationType": "TextUploader",
  "RequestMethod": "POST",
  "RequestURL": "https://paste.example/api",
  "Body": "JSON",
  "Data": "{\\n  \\"content\\": \\"{input}\\",\\n  \\"title\\": \\"{filename}\\"\\n}",
  "URL": "https://paste.example/{json:key}"
}"""


def test_import_imgur_like():
    it = sxcu.loads(IMGUR_LIKE)
    assert (it.name, it.destination_type, it.request_method, it.body, it.file_form_name) == \
        ("Imgur-like", ["ImageUploader"], "POST", "MultipartFormData", "image")
    assert it.headers == {"Authorization": "Client-ID 0123456789abcde"}
    assert it.url == "{json:data.link}"
    assert it.deletion_url == "https://imgur-like.example/delete/{json:data.deletehash}"
    assert it.error_message == "{json:data.error}"
    assert it.version == "16.1.0"


def test_import_xml_api():
    it = sxcu.loads(XML_API)
    assert it.destination_type == ["ImageUploader", "TextUploader", "FileUploader"]
    assert it.parameters == {"format": "xml"}
    assert it.arguments == {"key": "secret", "expire": "%y-%mo"}
    assert (it.url, it.thumbnail_url, it.error_message) == \
        ("{xml:/rsp/image/url}", "{xml:/rsp/image/thumb}", "{xml:/rsp/err/@msg}")
    assert it.name == ""
    assert it.display_name == "xml.example"


def test_import_url_shortener_without_body_is_body_none():
    it = sxcu.loads(SHORTENER)
    assert it.body == "None"
    assert it.request_method == "GET"
    assert it.parameters == {"format": "simple", "url": "{input}"}
    assert it.url == ""


def test_import_binary_put():
    it = sxcu.loads(BINARY_PUT)
    assert (it.request_method, it.body, it.request_url) == ("PUT", "Binary", "https://dav.example/files/{filename}")
    assert it.file_form_name == ""


def test_import_json_body_data():
    it = sxcu.loads(JSON_PASTE)
    assert it.body == "JSON"
    assert it.data == '{\n  "content": "{input}",\n  "title": "{filename}"\n}'


def test_import_with_bom_and_case_insensitive_keys_and_numeric_enums():
    text = '\ufeff{"version": "18.0.0", "requestmethod": 2, "REQUESTURL": "https://x.example/u", "body": 6,' \
           ' "destinationtype": 6, "url": "{response}"}'
    for data in (text, text.encode("utf-8"), text.lstrip("\ufeff").encode("utf-8-sig")):
        it = sxcu.loads(data)
        assert (it.request_method, it.request_url, it.body, it.url) == ("PUT", "https://x.example/u", "None",
                                                                        "{response}")
        assert it.destination_type == ["TextUploader", "FileUploader"]
    assert sxcu.loads('{"Version":"18.0.0","Body":5,"RequestURL":"https://x"}').body == "Binary"


def test_wrongly_typed_properties_are_skipped():
    it = sxcu.loads('{"Version":"18.0.0","RequestURL":"https://x","RequestMethod":"FLY","Headers":[1],'
                    '"DestinationType":"ImageUploader, Bogus","URL":{"a":1},"Arguments":{"n":5,"b":true}}')
    assert it.request_method == "POST"
    assert it.headers == {}
    assert it.destination_type == []
    assert it.url == ""
    assert it.arguments == {"n": "5", "b": "True"}


def test_import_array_and_uploaders_config_list():
    items = sxcu.loads_many("[" + IMGUR_LIKE + "," + SHORTENER + "]")
    assert [i.display_name for i in items] == ["Imgur-like", "is.example"]
    cfg = json.dumps({"CustomUploadersList": [json.loads(BINARY_PUT)], "CustomImageUploaderSelected": 0})
    assert [i.name for i in sxcu.loads_many(cfg)] == ["WebDAV PUT"]


def test_invalid_files():
    for bad in ("not json", "[1, 2]", "42"):
        with pytest.raises(sxcu.SxcuError):
            sxcu.loads(bad)


def test_query_string_in_request_url_moves_into_parameters():
    it = sxcu.loads('{"Version":"18.0.0","RequestURL":"https://x.example/u.php?key=a%20b&flag&k=1&k=2&api=x",'
                    '"Parameters":{"api":"kept"}}')
    assert it.request_url == "https://x.example/u.php"
    assert it.parameters == {"api": "kept", "flag": "", "key": "a b", "k": "1,2"}


# ---- versionless files --------------------------------------------------------------------------------

def test_versionless_file_with_new_syntax_is_not_mangled():
    it = sxcu.loads('{"RequestMethod":"POST","RequestURL":"https://n.example/up","Body":"MultipartFormData",'
                    '"FileFormName":"f","URL":"{json:url}"}')
    assert it.url == "{json:url}"
    assert it.body == "MultipartFormData"
    assert it.version == sxcu.SXCU_VERSION


# ---- pre-v13 upgrades --------------------------------------------------------------------------------------

LEGACY_V12 = r"""{
  "Name": "Old host",
  "DestinationType": "ImageUploader, FileUploader",
  "RequestType": "POST",
  "RequestURL": "https://old.example/upload.php?key=$prompt:API key|abc$",
  "FileFormName": "file",
  "Arguments": {"name": "$filename$", "literal": "50\\$ {curly} a|b"},
  "ResponseType": "Text",
  "RegexList": ["href=\"(.+?)\"", "thumb=(\\w+)"],
  "URL": "$json:data.link$",
  "ThumbnailURL": "https://old.example/t/$regex:2|1$",
  "DeletionURL": "$regex:1$"
}"""


def test_legacy_v12_upgrade():
    it = sxcu.loads(LEGACY_V12)
    assert it.version == sxcu.SXCU_VERSION
    assert it.request_method == "POST"
    assert it.body == "MultipartFormData"  # POST in a pre-13 file meant a multipart upload
    assert it.request_url == "https://old.example/upload.php"
    assert it.parameters == {"key": "{prompt:API key|abc}"}
    assert it.arguments == {"name": "{filename}", "literal": r"50$ \{curly\} a\|b"}
    assert it.url == "{json:data.link}"
    # RegexList references become self-contained {regex:pattern|group} calls.
    assert it.thumbnail_url == r"https://old.example/t/{regex:thumb=(\\w+)|1}"
    assert it.deletion_url == r'{regex:href="(.+?)"}'


def test_legacy_upgrade_result_parses_to_what_the_old_syntax_meant():
    from sharel.uploaders.custom.parser import CustomUploaderSyntaxParser, ResponseInfo
    it = sxcu.loads(LEGACY_V12)
    r = ResponseInfo(200, "OK", "https://old.example/upload.php", {}, '{"data": {"link": "https://old.example/i.png"}}')
    assert CustomUploaderSyntaxParser(response=r).parse(it.url) == "https://old.example/i.png"
    r = ResponseInfo(200, "OK", "https://old.example/upload.php", {}, 'href="https://old.example/del" thumb=T1')
    p = CustomUploaderSyntaxParser(file_name="f.png", response=r, url_encode=True)
    assert p.parse(it.thumbnail_url) == "https://old.example/t/T1"
    assert p.parse(it.deletion_url) == 'href="https://old.example/del"'
    args = CustomUploaderSyntaxParser(file_name="f.png")
    assert args.parse(it.arguments["literal"]) == "50$ {curly} a|b"


def test_legacy_get_moves_arguments_to_parameters():
    it = sxcu.loads('{"RequestType":"GET","RequestURL":"https://s.example/api","Arguments":{"url":"$input$",'
                    '"fmt":"txt"}}')
    assert it.body == "None"
    assert it.arguments == {}
    assert it.parameters == {"url": "{input}", "fmt": "txt"}


@pytest.mark.parametrize(("response_type", "url", "expected"), [
    ("RedirectionURL", "", "{responseurl}"),
    ("RedirectionURL", "$response$#x", "{responseurl}#x"),
    ("LocationHeader", "", "{header:Location}"),
    (3, "https://h/$response$", "https://h/{header:Location}"),
    ("Text", "", ""),
])
def test_legacy_response_types(response_type, url, expected):
    obj = {"RequestType": "POST", "RequestURL": "https://h.example/u", "ResponseType": response_type, "URL": url}
    assert sxcu.loads(json.dumps(obj)).url == expected


def test_legacy_headers_response_type_is_explained():
    it = sxcu.loads('{"RequestType":"POST","RequestURL":"https://h.example/u","ResponseType":"Headers"}')
    assert "deprecated" in it.url and "{" not in it.url.replace("\\{", "")


def test_v13_dollar_syntax_is_migrated_but_settings_kept():
    obj = {"Version": "13.5.0", "DestinationType": "TextUploader", "RequestMethod": "POST",
           "RequestURL": "https://p.example/$random:a|b$", "Body": "JSON",
           "Data": '{"t":"$input$","n":"$FILENAME$"}', "Headers": {"A": "Basic $base64:u:p$"},
           "URL": "$xml:/r/u$", "ErrorMessage": "$json:error$"}
    it = sxcu.loads(json.dumps(obj))
    assert it.body == "JSON"
    assert it.request_url == "https://p.example/{random:a|b}"
    assert it.data == '{"t":"{input}","n":"{filename}"}'
    assert it.headers == {"A": "Basic {base64:u:p}"}
    assert (it.url, it.error_message) == ("{xml:/r/u}", "{json:error}")


def test_current_version_is_not_migrated():
    it = sxcu.loads('{"Version":"14.0.0","RequestURL":"https://c.example/u","URL":"$5 {json:a}"}')
    assert it.url == "$5 {json:a}"


def test_migrate_old_syntax_unit():
    assert sxcu.migrate_old_syntax("$a$") == "{a}"
    assert sxcu.migrate_old_syntax(r"\$x\\y") == r"$x\\y"
    assert sxcu.migrate_old_syntax("lit {x} | y") == r"lit \{x\} \| y"
    assert sxcu.migrate_old_syntax("$random:a|b$") == "{random:a|b}"
    assert sxcu.migrate_old_syntax("keep $unterminated") == "keep "
    assert sxcu.migrate_old_syntax("") == ""


def test_compare_version():
    assert sxcu.compare_version("13.7.1", "13.7.1") == 0
    assert sxcu.compare_version("13.7", "13.7.1") < 0
    assert sxcu.compare_version("14.0.0", "13.7.1") > 0
    assert sxcu.compare_version("12.3.1.0", "12.3.1") == 0
