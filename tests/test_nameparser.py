import datetime as dt
import random
import re
import string

from sharel.core.nameparser import ADJECTIVES, ANIMALS, EMOJIS, NON_AMBIGUOUS, NameParser, NameParserType, WindowInfo

T = dt.datetime(2026, 3, 7, 15, 4, 9, 123456)


def np(**kw):
    kw.setdefault("custom_time", T)
    return NameParser(**kw)


def test_date_time_tokens():
    p = np()
    assert p.parse("%y-%yy-%mo-%d %h:%mi:%s.%ms") == "2026-26-03-07 15:04:09.123"
    assert p.parse("%mon|%mon2|%w|%w2") == "March|Mar|Saturday|Sat"
    assert p.parse("%wy") == f"{T.isocalendar()[1]:02d}"
    assert p.parse("%unix") == str(int(T.timestamp()))


def test_pm_switches_to_12_hour():
    assert np().parse("%h %pm") == "03 PM"
    assert np(custom_time=dt.datetime(2026, 1, 1, 0, 30)).parse("%h%pm") == "12AM"
    assert np().parse("%h") == "15"


def test_longest_token_wins():
    # %mon2 is not %mo + "n2"; %ms is not %mi... ; %iAa not %i + "Aa"
    assert np().parse("%mon2") == "Mar"
    assert np(auto_increment=0).parse("%iAa") == "1"


def test_window_tokens_and_provider_called_lazily():
    calls = []

    def provider():
        calls.append(1)
        return WindowInfo("Doc - Editor", "gnome-text-editor")

    p = np(window_provider=provider)
    assert p.parse("%y") == "2026" and not calls
    assert p.parse("%pn_%t") == "gnome-text-editor_Doc - Editor"
    assert len(calls) == 1


def test_random_tokens_and_repeat():
    p = np(rng=random.Random(1))
    assert re.fullmatch(r"\d{5}", p.parse("%rn{5}"))
    assert re.fullmatch(r"[0-9A-Za-z]{10}", p.parse("%ra{10}"))
    assert all(c in NON_AMBIGUOUS for c in p.parse("%rna{30}"))
    assert re.fullmatch(r"[0-9A-F]{8}", p.parse("%rx{8}"))
    assert len(p.parse("%ra")) == 1
    g = p.parse("%guid")
    assert re.fullmatch(r"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}", g) and g != p.parse("%guid")
    assert p.parse("%ranimal") in ANIMALS
    adj = p.parse("%radjective")
    assert adj[0] in string.ascii_uppercase and adj.lower() in ADJECTIVES
    emo = p.parse("%remoji{3}")
    assert len(emo) == 3 and all(c in EMOJIS for c in emo)


def test_auto_increment_variants():
    p = np(auto_increment=9)
    assert p.parse("%i") == "10"
    assert p.parse("%i{4}") == "0011"
    assert p.used_auto_increment and p.auto_increment == 11
    assert np(auto_increment=34).parse("%ia") == "z"  # 35 in base 36
    assert np(auto_increment=60).parse("%iAa") == "z"  # 61 in base 62
    assert np(auto_increment=254).parse("%ix") == "FF"
    assert np(auto_increment=4).parse("%ib{2}") == "101"
    assert np(auto_increment=6).parse("%ia{3}") == "007"


def test_image_and_system_tokens():
    p = np(image_width=640, image_height=480)
    assert p.parse("%widthx%height") == "640x480"
    assert p.parse("%uln") and p.parse("%cn") and p.parse("%un")


def test_user_and_computer_tokens_exact(monkeypatch):
    import os
    import platform
    import pwd
    me = pwd.getpwuid(os.getuid())
    full = me.pw_gecos.split(",")[0] or me.pw_name
    assert np().parse("%un") == full  # user name (full name, else login)
    assert np().parse("%uln") == me.pw_name  # user login name
    assert np().parse("%cn") == platform.node()  # computer name
    monkeypatch.setattr(platform, "node", lambda: "testhost")
    assert np().parse("%cn-%uln") == f"testhost-{me.pw_name}"


def test_newline_and_filename_sanitizing():
    assert np().parse("a%nb") == "a\nb"
    assert np(type=NameParserType.FileName).parse("a%nb") == "ab"
    w = WindowInfo('a/b:c*"d"', "p")
    assert np(type=NameParserType.FileName, window=w).parse("%t") == "a_b_c__d_"


def test_random_line_from_file(tmp_path):
    f = tmp_path / "words.txt"
    f.write_text("alpha\nbeta\n\ngamma\n")
    for _ in range(10):
        assert np().parse(f"%rf{{{f}}}") in {"alpha", "beta", "gamma"}


def test_unknown_token_kept_and_max_lengths():
    assert np().parse("100%") == "100%"
    assert np().parse("%zz") == "%zz"
    p = np(type=NameParserType.FileName, max_name_length=5)
    assert p.parse("%y%mo%d") == "20260"
    p = np(window=WindowInfo("abcdefgh", ""), max_title_length=3)
    assert p.parse("%t") == "abc"
