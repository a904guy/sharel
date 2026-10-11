"""The custom uploader syntax parser: grammar, escaping and every function (ShareX semantics)."""
from __future__ import annotations

import datetime as dt
import json
import random

import pytest

from sharel.uploaders.custom.jsonpath import JsonPathError, format_double, select_token, select_tokens
from sharel.uploaders.custom.parser import (
    CustomSyntaxError,
    CustomUploaderSyntaxParser,
    InvalidJSONResponse,
    ResponseInfo,
    append_query_string,
    create_query_string,
    escape,
    fix_prefix,
    form_encode,
    host_name,
    json_encode,
    name_parse_keep_escapes,
    syntax_test,
    url_encode,
    xml_encode,
)

IMGUR_LIKE = json.dumps({
    "status": 200,
    "success": True,
    "data": {"id": "aBc12", "link": "https://i.example.com/aBc12.png", "deletehash": "dEl99",
             "size": 1024, "ratio": 1.5, "nsfw": None, "tags": []},
    "files": [{"name": "a.png", "url": "https://e.com/a.png"}, {"name": "b.png", "url": "https://e.com/b.png"}],
    "key with space": "spaced",
})


class Prompts:
    """Records the ctx callbacks the interactive functions make."""

    def __init__(self, select=None, inp=None):
        self.calls = []
        self._select = select
        self._inp = inp

    def prompt_select(self, title, options):
        self.calls.append(("select", title, list(options)))
        return self._select(options) if self._select else options[-1]

    def prompt_input(self, title, default):
        self.calls.append(("input", title, default))
        return self._inp if self._inp is not None else default

    def show_output(self, title, text):
        self.calls.append(("output", title, text))


def resp(text=IMGUR_LIKE, url="https://api.example.com/upload", headers=None, status=200):
    return ResponseInfo(status_code=status, status_description="OK", response_url=url,
                        headers=headers or {"Content-Type": "application/json", "Location": "https://e.com/loc"},
                        response_text=text)


def parse(text, **kw):
    kw.setdefault("response", resp())
    return CustomUploaderSyntaxParser(**kw).parse(text)


# ---- grammar ------------------------------------------------------------------------------------------

def test_plain_text_passes_through():
    assert parse("https://example.com/upload.php?a=1") == "https://example.com/upload.php?a=1"
    assert parse("") == ""
    assert parse(None) == ""


def test_backslash_escapes_braces_pipe_and_backslash():
    assert parse(r"a\{b\}c\|d\\e") == r"a{b}c|d\e"
    assert parse(r"\{response\}") == "{response}"
    # Escaping any other character just yields it; a trailing lone backslash is dropped.
    assert parse(r"\a\:b") == "a:b"
    assert parse("end\\") == "end"


def test_unescaped_close_or_pipe_at_top_level_ends_the_text():
    # ShareX's parser stops at a stray '}' or '|' outside a function.
    assert parse("abc}def") == "abc"
    assert parse("abc|def") == "abc"


def test_unterminated_function_is_still_called():
    assert parse("x{response") == "x" + IMGUR_LIKE


def test_function_names_are_case_insensitive():
    assert parse("{RESPONSEURL}") == "https://api.example.com/upload"
    assert parse("{Json:data.id}") == "aBc12"


def test_unknown_and_empty_function_names_are_errors():
    with pytest.raises(CustomSyntaxError, match="Invalid function name: nope"):
        parse("{nope}")
    with pytest.raises(CustomSyntaxError, match="cannot be empty"):
        parse("a{}b")


def test_minimum_parameter_counts():
    with pytest.raises(CustomSyntaxError, match='"random" is 2'):
        parse("{random:only}")
    for fn in ("json", "xml", "regex", "header", "select", "outputbox", "base64"):
        with pytest.raises(CustomSyntaxError, match=f'"{fn}" is 1'):
            parse("{" + fn + "}")


def test_empty_parameter_after_colon_counts_as_one_parameter():
    # {base64:} has one (empty) parameter, so it passes the count check and yields nothing.
    assert parse("[{base64:}]") == "[]"


def test_nested_functions_and_results_are_not_reparsed():
    p = CustomUploaderSyntaxParser(input_text="a|b}c{d", response=resp())
    assert p.parse("{base64:{input}}") == "YXxifWN7ZA=="
    text = json.dumps({"v": "{response}|x"})
    assert parse("{json:{response}|v}", response=resp(text)) == "{response}|x"


def test_escape_helper_round_trips():
    raw = r"{a|b}\c"
    assert parse(escape(raw)) == raw


def test_response_functions_need_a_response():
    with pytest.raises(CustomSyntaxError, match="response is parsed"):
        CustomUploaderSyntaxParser().parse("{response}")
    with pytest.raises(CustomSyntaxError):
        CustomUploaderSyntaxParser().parse("{json:data.link}")


# ---- response, responseurl, header ---------------------------------------------------------------------

def test_response_and_responseurl():
    assert parse("https://e.com/{response}", response=resp("abc123")) == "https://e.com/abc123"
    assert parse("{responseurl}", response=resp(url="https://final.example/x")) == "https://final.example/x"


def test_header_lookup_is_case_insensitive_and_missing_is_empty():
    assert parse("{header:location}") == "https://e.com/loc"
    assert parse("{header:CONTENT-TYPE}") == "application/json"
    assert parse("[{header:X-Missing}]") == "[]"


# ---- json -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("path", "expected"), [
    ("data.link", "https://i.example.com/aBc12.png"),
    ("$.data.link", "https://i.example.com/aBc12.png"),
    ("files[0].url", "https://e.com/a.png"),
    ("files[1].name", "b.png"),
    ("files[-1].url", "https://e.com/b.png"),
    ("['key with space']", "spaced"),
    ("data['deletehash']", "dEl99"),
    ("files[?(@.name == 'b.png')].url", "https://e.com/b.png"),
    ("files[?(@.name != 'b.png')].url", "https://e.com/a.png"),
    ("..deletehash", "dEl99"),
    ("$..link", "https://i.example.com/aBc12.png"),
    ("files[1:2].url", "https://e.com/b.png"),
    ("files[-1:].name", "b.png"),
    ("data.size", "1024"),
    ("data.ratio", "1.5"),
    ("success", "True"),
    ("status", "200"),
])
def test_json_paths(path, expected):
    assert parse("{json:" + path + "}") == expected


def test_json_missing_paths_and_null_are_empty():
    assert parse("[{json:data.nope}]") == "[]"
    assert parse("[{json:files[5].url}]") == "[]"
    assert parse("[{json:files[-9].url}]") == "[]"
    assert parse("[{json:data.nsfw}]") == "[]"
    assert parse("[{json:nope.deeper.still}]") == "[]"


def test_json_multiple_matches_is_an_error_like_select_token():
    with pytest.raises(CustomSyntaxError, match="multiple tokens"):
        parse("{json:files[*].url}")
    with pytest.raises(CustomSyntaxError, match="multiple tokens"):
        parse("{json:..url}")


def test_json_wildcard_with_a_single_match():
    text = json.dumps({"files": [{"url": "only"}]})
    assert parse("{json:files[*].url}", response=resp(text)) == "only"


def test_json_container_cannot_be_converted_to_string():
    with pytest.raises(CustomSyntaxError, match="Can not convert Object to String"):
        parse("{json:data}")
    with pytest.raises(CustomSyntaxError, match="Can not convert Array to String"):
        parse("{json:files}")


def test_json_input_form_parses_given_text():
    # The first parameter is the JSON itself (braces escaped so the parser keeps them).
    assert parse(r'{json:\{"a":\{"b":["x","y"]\}\}|a.b[1]}') == "y"


def test_json_input_form_with_nested_response():
    assert parse("{json:{response}|data.id}") == "aBc12"


def test_json_invalid_response_raises_invalid_json():
    with pytest.raises(InvalidJSONResponse):
        parse("{json:a}", response=resp("<html>not json</html>"))


def test_json_with_bom_response():
    assert parse("{json:a}", response=resp('\ufeff{"a": "x"}')) == "x"


def test_jsonpath_evaluator_directly():
    doc = {"store": {"book": [{"title": "A", "price": 8.95, "tags": ["x"]}, {"title": "B", "price": 12.99},
                              {"title": "C", "price": 8.99, "isbn": "123"}],
                     "bicycle": {"color": "red", "price": 19.95}}}
    assert [b["title"] for b in select_tokens(doc, "$.store.book[*]")] == ["A", "B", "C"]
    assert select_tokens(doc, "$..price") == [8.95, 12.99, 8.99, 19.95]
    assert select_tokens(doc, "$.store.book[0,2].title") == ["A", "C"]
    assert select_tokens(doc, "$.store.book[::2].title") == ["A", "C"]
    assert select_tokens(doc, "$.store.book[?(@.price < 9)].title") == ["A", "C"]
    assert select_tokens(doc, "$.store.book[?(@.isbn)].title") == ["C"]
    assert select_tokens(doc, "$.store.book[?(@.price > 9 && @.price < 13)].title") == ["B"]
    assert select_tokens(doc, "$.store.book[?(@.title == 'A' || @.title == 'C')].price") == [8.95, 8.99]
    assert select_tokens(doc, "$.store.book[?(@.title =~ /^b$/i)].price") == [12.99]
    assert select_tokens(doc, "$.store['bicycle','book'][0].title") == ["A"]
    assert select_tokens(doc, "$.store['bicycle'].color") == ["red"]
    assert select_tokens(doc, "$.store.*.color") == ["red"]
    assert select_tokens(doc, "$..book[?(@.price == 8.99)].isbn") == ["123"]
    assert select_token(doc, "$.store.bicycle.color") == "red"
    assert select_token(doc, "$.store.nothing", "dflt") == "dflt"
    assert select_tokens(doc, "$") == [doc]
    with pytest.raises(JsonPathError, match="multiple tokens"):
        select_token(doc, "$..title")
    for bad in ("$.store[", "$.a[1", "$.a[x]", "$.a.", "$.a[?(@.b ==", "$['a"):
        with pytest.raises(JsonPathError):
            select_tokens(doc, bad)


def test_double_formatting_matches_dotnet():
    assert [format_double(v) for v in (1.0, 1.5, 1e15, 1e-05, 0.0001, 123456789012345.0, -2.5e-07)] == \
        ["1", "1.5", "1E+15", "1E-05", "0.0001", "123456789012345", "-2.5E-07"]


# ---- xml ------------------------------------------------------------------------------------------------

XML = """<?xml version="1.0" encoding="UTF-8"?>
<files>
    <file>
        <name>image.png</name>
        <url>https://example.com/image.png</url>
        <link href="https://example.com/thumb.png"/>
    </file>
    <file><url>https://example.com/second.png</url></file>
</files>"""


def test_xml_xpath_on_response():
    r = resp(XML)
    assert parse("{xml:/files/file[1]/url}", response=r) == "https://example.com/image.png"
    assert parse("{xml:/files/file[2]/url}", response=r) == "https://example.com/second.png"
    assert parse("{xml:/files/file[1]/link/@href}", response=r) == "https://example.com/thumb.png"
    assert parse("{xml://url}", response=r) == "https://example.com/image.png"  # first match
    assert parse("{xml:/files/file[1]/url/text()}", response=r) == "https://example.com/image.png"
    assert parse("[{xml:/files/missing}]", response=r) == "[]"


def test_xml_element_value_is_its_text_without_indentation():
    assert parse("{xml:/files/file[2]}", response=resp(XML)) == "https://example.com/second.png"


def test_xml_input_form():
    assert parse(r"{xml:<r><u>abc</u></r>|/r/u}") == "abc"
    assert parse("{xml:{response}|/files/file[1]/name}", response=resp(XML)) == "image.png"


def test_xml_namespaces():
    doc = ('<?xml version="1.0"?><r:result xmlns:r="urn:r" xmlns="urn:default">'
           '<r:url>https://ns.example/a</r:url><plain>p</plain></r:result>')
    r = resp(doc)
    # XPath 1.0: unprefixed names never match default-namespaced elements (same as .NET) ...
    assert parse("[{xml:/result/plain}]", response=r) == "[]"
    # ... so ShareX uploaders use local-name(), which works here too.
    assert parse("{xml:/*[local-name()='result']/*[local-name()='plain']}", response=r) == "p"
    # Prefixes declared on the root element can be used directly.
    assert parse("{xml:/r:result/r:url}", response=r) == "https://ns.example/a"


def test_xml_errors():
    with pytest.raises(CustomSyntaxError, match="Invalid XML"):
        parse("{xml:/a}", response=resp("not <xml"))
    with pytest.raises(CustomSyntaxError, match="XPath"):
        parse("{xml:/files[}", response=resp(XML))


# ---- regex ----------------------------------------------------------------------------------------------

HTML = '<a href="https://example.com/i/abc.png">link</a> id=42 name=cat'


def test_regex_whole_match_lookarounds_and_groups():
    r = resp(HTML)
    assert parse('{regex:(?<=href=").+?(?=")}', response=r) == "https://example.com/i/abc.png"
    assert parse('{regex:href="(.+?)"|1}', response=r) == "https://example.com/i/abc.png"
    assert parse('{regex:href="(?<url>.+?)"|url}', response=r) == "https://example.com/i/abc.png"
    assert parse(r"{regex:id=(\\d+)|1}", response=r) == "42"
    assert parse(r"{regex:id=(\\d+)|0}", response=r) == "id=42"


def test_regex_dotnet_group_numbering_puts_named_groups_last():
    r = resp(HTML)
    # .NET numbers unnamed groups first: group 1 is (\d+), group 2 is the named one.
    assert parse(r"{regex:(?<word>name)=(\\w+)|1}", response=r) == "cat"
    assert parse(r"{regex:(?<word>name)=(\\w+)|2}", response=r) == "name"
    assert parse(r"{regex:(?<word>name)=(\\w+)|word}", response=r) == "name"


def test_regex_missing_group_and_no_match_are_empty():
    r = resp(HTML)
    assert parse(r"[{regex:id=(\\d+)|7}]", response=r) == "[]"
    assert parse(r"[{regex:id=(\\d+)|nosuch}]", response=r) == "[]"
    assert parse("[{regex:zzz}]", response=r) == "[]"
    assert parse("[{regex:(a)?b|1}]", response=resp("b")) == "[]"


def test_regex_escaped_pipe_is_alternation():
    assert parse(r"{regex:(dog\|cat)|1}", response=resp(HTML)) == "cat"


def test_regex_input_form_with_three_parameters():
    assert parse(r"{regex:v=12,w=34|w=(\\d+)|1}") == "34"
    assert parse(r"{regex:{response}|id=(?<n>\\d+)|n}", response=resp(HTML)) == "42"


def test_regex_inline_options_and_anchors():
    assert parse(r"{regex:(?i)HREF=\"([^\"]+)\"|1}", response=resp(HTML)) == "https://example.com/i/abc.png"
    assert parse(r"{regex:name=(?i)CAT}", response=resp(HTML)) == "name=cat"
    assert parse(r"{regex:\\d+\\z}", response=resp("a1 b22")) == "22"
    assert parse(r"{regex:(?<w>\\w)\\k<w>}", response=resp("abccd")) == "cc"


def test_regex_invalid_pattern_is_an_error():
    with pytest.raises(CustomSyntaxError, match="Regex"):
        parse("{regex:(unclosed}", response=resp(HTML))


# ---- input, filename ------------------------------------------------------------------------------------

def test_input_and_filename_plain_and_url_encoded():
    p = CustomUploaderSyntaxParser(file_name="my file (1).png", input_text="a&b=c d/é")
    assert p.parse("{filename}|{input}") == "my file (1).png"
    assert p.parse("{input}") == "a&b=c d/é"
    p.url_encode = True
    assert p.parse("{filename}") == "my%20file%20%281%29.png"
    assert p.parse("{input}") == "a%26b%3Dc%20d%2F%C3%A9"
    assert CustomUploaderSyntaxParser().parse("[{input}{filename}]") == "[]"


# ---- random, select, inputbox, outputbox, base64 --------------------------------------------------------

def test_random_picks_within_choices():
    seen = set()
    for seed in range(60):
        seen.add(CustomUploaderSyntaxParser(rng=random.Random(seed)).parse("https://{random:a|b|c}.example.com"))
    assert seen == {"https://a.example.com", "https://b.example.com", "https://c.example.com"}


def test_random_with_nested_values():
    p = CustomUploaderSyntaxParser(input_text="X", rng=random.Random(1))
    assert p.parse("{random:{input}1|{input}2}") in ("X1", "X2")


def test_select_asks_through_ctx_with_non_empty_options():
    ctx = Prompts(select=lambda opts: opts[1])
    out = CustomUploaderSyntaxParser(ctx=ctx).parse("https://{select:one.com||two.com|three.com}/x")
    assert out == "https://two.com/x"
    assert ctx.calls == [("select", "Select", ["one.com", "two.com", "three.com"])]


def test_select_with_only_empty_options_does_not_ask():
    ctx = Prompts()
    assert CustomUploaderSyntaxParser(ctx=ctx).parse("[{select:|}]") == "[]"
    assert ctx.calls == []


def test_select_cancelled_gives_empty():
    ctx = Prompts(select=lambda _opts: None)
    assert CustomUploaderSyntaxParser(ctx=ctx).parse("[{select:a|b}]") == "[]"


def test_inputbox_title_default_and_alias():
    ctx = Prompts(inp="typed")
    p = CustomUploaderSyntaxParser(ctx=ctx)
    assert p.parse("https://{inputbox:Subdomain|i}.example.com") == "https://typed.example.com"
    assert p.parse("{inputbox}") == "typed"
    assert p.parse("{prompt:Old name}") == "typed"
    assert ctx.calls == [("input", "Subdomain", "i"), ("input", "Input", ""), ("input", "Old name", "")]


def test_inputbox_cancel_keeps_default():
    class Cancel(Prompts):
        def prompt_input(self, title, default):
            return None
    assert CustomUploaderSyntaxParser(ctx=Cancel()).parse("{inputbox:T|fallback}") == "fallback"


def test_outputbox_argument_order_is_title_then_text():
    ctx = Prompts()
    p = CustomUploaderSyntaxParser(ctx=ctx, response=resp())
    assert p.parse("[{outputbox:Result|{json:data.id}}]") == "[]"
    assert p.parse("{outputbox:just text}") == ""
    assert p.parse("{outputbox:Title|}") == ""  # empty text shows nothing
    assert ctx.calls == [("output", "Result", "aBc12"), ("output", "Output", "just text")]


def test_base64():
    assert parse("Basic {base64:user:pass}") == "Basic dXNlcjpwYXNz"
    assert parse("{base64:héllo}") == "aMOpbGxv"
    assert parse("[{base64:}]") == "[]"


# ---- the name parser pre-pass ---------------------------------------------------------------------------

def test_name_parser_runs_first_when_enabled_and_escapes_survive():
    year = str(dt.datetime.now().year)
    p = CustomUploaderSyntaxParser(use_name_parser=True)
    assert p.parse("%y") == year
    assert p.parse(r"\%y") == "%y"
    assert p.parse(r"a\\%y") == "a\\" + year
    assert CustomUploaderSyntaxParser().parse("%y") == "%y"


def test_name_parser_cannot_read_files(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret\n")
    assert name_parse_keep_escapes(f"[%rf{{{secret}}}]") == "[]"


# ---- encoders and helpers -------------------------------------------------------------------------------

def test_encoders_match_sharex_helpers():
    assert url_encode("a b/c~d_e.f-g*") == "a%20b%2Fc~d_e.f-g%2A"
    assert form_encode("a b/c!*()~é") == "a+b%2fc!*()%7e%c3%a9"
    assert json_encode('say "hi"\n\ttab\\ \x01 \u2028 é') == 'say \\"hi\\"\\n\\ttab\\\\ \\u0001 \\u2028 é'
    assert xml_encode("<a href='x'>&\"</a>") == "&lt;a href=&apos;x&apos;&gt;&amp;&quot;&lt;/a&gt;"


def test_query_string_helpers():
    assert create_query_string({"k": "v w", "flag": "", "e": "a&b"}) == "k=v+w&flag&e=a%26b"
    assert append_query_string("https://x/u", {"a": "1"}) == "https://x/u?a=1"
    assert append_query_string("https://x/u?z=0", {"a": "1"}) == "https://x/u?z=0&a=1"
    assert append_query_string("https://x/u", {}) == "https://x/u"


def test_fix_prefix_and_host_name():
    assert fix_prefix("example.com/up") == "https://example.com/up"
    assert fix_prefix("http://example.com") == "http://example.com"
    assert fix_prefix("HTTPS://x") == "HTTPS://x"
    assert fix_prefix("//cdn.example") == "//cdn.example"
    assert host_name("https://www.Example.com/upload.php") == "example.com"
    assert host_name("not a url") == "not a url"


def test_syntax_test_helper_reports_errors_as_text():
    r = resp()
    assert syntax_test("{json:data.link}", r) == "https://i.example.com/aBc12.png"
    assert syntax_test("{filename}", r) == "example.png"
    assert syntax_test("{bogus}", r).startswith("Error\nInvalid function name")
