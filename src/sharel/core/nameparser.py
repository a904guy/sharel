"""ShareX's NameParser: file-name patterns such as ``%pn_%ra{10}`` or ``%y-%mo``.

Tokens (longest match wins, so ``%mon2`` is not read as ``%mo`` + ``n2``):

=============  ===========================================================================
``%t``         title of the active window      ``%pn``   process name of the active window
``%y``         year (2026)                     ``%yy``   year, two digits (26)
``%mo``        month (01-12)                   ``%mon``  month name     ``%mon2``  abbreviated
``%w``         weekday name                    ``%w2``   abbreviated    ``%wy`` week of year
``%d``         day (01-31)                     ``%h``    hour (12 h when ``%pm`` is used)
``%mi``        minute                          ``%s``    second         ``%ms`` millisecond
``%pm``        AM/PM                           ``%unix`` unix time
``%i``         auto-increment number, ``{n}`` zero-pads
``%ia``        auto-increment, base 36 (0-9a-z)   ``%iAa`` base 62 (0-9A-Za-z)
``%ib``        auto-increment in base ``{n}`` (2..62) using 0-9A-Za-z
``%ix``        auto-increment, hexadecimal
``%rn``        random digit                    ``%ra``   random alphanumeric
``%rna``       random non-ambiguous alphanumeric  ``%rx`` random hex digit
``%guid``      random GUID                     ``%radjective`` ``%ranimal`` ``%remoji``
``%rf``        random line from the file given in ``{path}``
``%width``     image width                     ``%height`` image height
``%un``        user name (full name)           ``%uln``  user login name
``%cn``        computer name                   ``%n``    new line (not in file names)
=============  ===========================================================================

``{n}`` after a random token repeats it n times (``%ra{10}``); after ``%i``/``%ia``/``%iAa``/
``%ix`` it pads with zeros to n digits.
"""
from __future__ import annotations

import datetime as _dt
import os
import platform
import pwd
import random
import re
import secrets
import string
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

ALNUM = string.digits + string.ascii_uppercase + string.ascii_lowercase  # 0-9A-Za-z (base-62 order)
ALNUM_LOWER = string.digits + string.ascii_lowercase
NON_AMBIGUOUS = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
HEX = "0123456789ABCDEF"

ADJECTIVES = (
    "adorable adventurous aggressive agreeable alert alive amused angry annoyed anxious arrogant ashamed "
    "attractive average beautiful better bewildered blue blushing bored brainy brave breakable bright busy calm "
    "careful cautious charming cheerful clean clear clever cloudy clumsy colorful combative comfortable concerned "
    "confused cooperative courageous crazy creepy crowded curious cute dangerous dark defiant delightful "
    "determined different difficult disgusted distinct dizzy doubtful drab dull eager easy elated elegant "
    "embarrassed enchanting encouraging energetic enthusiastic envious evil excited expensive exuberant fair "
    "faithful famous fancy fantastic fierce fine foolish fragile frail frantic friendly frightened funny gentle "
    "gifted glamorous gleaming glorious good gorgeous graceful grumpy handsome happy healthy helpful helpless "
    "hilarious homely hungry hurt important impossible inexpensive innocent inquisitive itchy jealous jittery "
    "jolly joyous kind lazy light lively lonely long lovely lucky magnificent misty modern motionless muddy "
    "mushy mysterious nasty naughty nervous nice nutty obedient obnoxious odd open outrageous outstanding "
    "panicky perfect plain pleasant poised poor powerful precious prickly proud putrid puzzled quaint real "
    "relieved repulsive rich scary selfish shiny shy silly sleepy smiling smoggy sore sparkling splendid "
    "spotless stormy strange stupid successful super talented tame tasty tender tense terrible thankful "
    "thoughtful thoughtless tired tough troubled ugliest ugly uninterested unsightly unusual upset uptight "
    "vast victorious vivacious wandering weary wicked wide wild witty worried worrisome wrong zany zealous"
).split()
ANIMALS = (
    "Aardvark Albatross Alligator Alpaca Ant Anteater Antelope Ape Armadillo Baboon Badger Barracuda Bat Bear "
    "Beaver Bee Bison Boar Buffalo Butterfly Camel Capybara Caribou Cassowary Cat Caterpillar Cattle Chamois "
    "Cheetah Chicken Chimpanzee Chinchilla Chough Clam Cobra Cockroach Cod Cormorant Coyote Crab Crane "
    "Crocodile Crow Curlew Deer Dinosaur Dog Dogfish Dolphin Donkey Dotterel Dove Dragonfly Duck Dugong Dunlin "
    "Eagle Echidna Eel Eland Elephant Elk Emu Falcon Ferret Finch Fish Flamingo Fly Fox Frog Gaur Gazelle "
    "Gerbil Giraffe Gnat Gnu Goat Goldfinch Goldfish Goose Gorilla Goshawk Grasshopper Grouse Guanaco Gull "
    "Hamster Hare Hawk Hedgehog Heron Herring Hippopotamus Hornet Horse Hummingbird Hyena Ibex Ibis Jackal "
    "Jaguar Jay Jellyfish Kangaroo Kingfisher Koala Kookabura Kouprey Kudu Lapwing Lark Lemur Leopard Lion "
    "Llama Lobster Locust Loris Louse Lyrebird Magpie Mallard Manatee Mandrill Mantis Marten Meerkat Mink Mole "
    "Mongoose Monkey Moose Mosquito Mouse Mule Narwhal Newt Nightingale Octopus Okapi Opossum Oryx Ostrich "
    "Otter Owl Oyster Panther Parrot Partridge Peafowl Pelican Penguin Pheasant Pig Pigeon Pony Porcupine "
    "Porpoise Quail Quelea Quetzal Rabbit Raccoon Rail Ram Rat Raven Reindeer Rhinoceros Rook Salamander "
    "Salmon Sandpiper Sardine Scorpion Seahorse Seal Shark Sheep Shrew Skunk Snail Snake Sparrow Spider "
    "Spoonbill Squid Squirrel Starling Stingray Stinkbug Stork Swallow Swan Tapir Tarsier Termite Tiger Toad "
    "Trout Turkey Turtle Viper Vulture Wallaby Walrus Wasp Weasel Whale Wildcat Wolf Wolverine Wombat "
    "Woodcock Woodpecker Worm Wren Yak Zebra"
).split()
EMOJIS = list("😀😃😄😁😆😅😂🤣😊😇🙂🙃😉😌😍🥰😘😗😙😚😋😛😝😜🤪🤨🧐🤓😎🥸🤩🥳😏😒😞😔😟😕🙁😣😖😫😩🥺😢😭"
              "😤😠😡🤬🤯😳🥵🥶😱😨😰😥😓🤗🤔🤭🤫🤥😶😐😑😬🙄😯😦😧😮😲🥱😴🤤😪😵🤐🥴🤢🤮🤧😷🤒🤕🤑🤠"
              "😈👿👹👺🤡💩👻💀👽👾🤖🎃😺😸😹😻😼😽🙀😿😾🐶🐱🐭🐹🐰🦊🐻🐼🐨🐯🦁🐮🐷🐸🐵🐔🐧🐦🐤🦆🦅🦉🦇🐺🐗🐴"
              "🦄🐝🐛🦋🐌🐞🐜🦟🦗🕷🦂🐢🐍🦎🦖🦕🐙🦑🦐🦞🦀🐡🐠🐟🐬🐳🐋🦈🐊🐅🐆🦓🦍🦧🐘🦛🦏🐪🐫🦒🦘🐃🐂🐄")


class NameParserType(Enum):
    Default = "Default"
    Text = "Text"
    FileName = "FileName"
    FolderPath = "FolderPath"
    FilePath = "FilePath"
    URL = "URL"


@dataclass
class WindowInfo:
    title: str = ""
    process_name: str = ""


# Longest first: the scanner tries these in order at every '%'.
TOKENS = sorted([
    "t", "pn", "y", "yy", "mo", "mon", "mon2", "w", "w2", "wy", "d", "h", "mi", "s", "ms", "pm", "unix",
    "i", "ia", "iAa", "ib", "ix", "rn", "ra", "rna", "rx", "guid", "radjective", "ranimal", "remoji", "rf",
    "width", "height", "un", "uln", "cn", "n",
], key=len, reverse=True)

RANDOM_TOKENS = {"rn", "ra", "rna", "rx", "guid", "radjective", "ranimal", "remoji"}
INVALID_FILENAME = '<>:"/\\|?*\x00'
INVALID_PATH = '<>:"|?*\x00'

_ARG = re.compile(r"\{([^{}]*)\}")


def _to_base(n: int, alphabet: str) -> str:
    if n == 0:
        return alphabet[0]
    base = len(alphabet)
    digits = []
    while n:
        n, r = divmod(n, base)
        digits.append(alphabet[r])
    return "".join(reversed(digits))


def _user_full_name() -> str:
    try:
        gecos = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0]
        return gecos or _login_name()
    except (KeyError, OSError):
        return _login_name()


def _login_name() -> str:
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return os.environ.get("USER", "user")


class NameParser:
    """Parse a pattern. Construct one per task (it remembers the window, image size, counter)."""

    def __init__(self, type: NameParserType = NameParserType.Default, *, window: WindowInfo | None = None,
                 window_provider: Callable[[], WindowInfo] | None = None, image_width: int = 0,
                 image_height: int = 0, auto_increment: int = 0, custom_time: _dt.datetime | None = None,
                 max_name_length: int = 0, max_title_length: int = 0, rng: random.Random | None = None,
                 time_zone_offset_hours: float | None = None) -> None:
        self.type = type
        self._window = window
        self._window_provider = window_provider
        self.image_width = image_width
        self.image_height = image_height
        self.auto_increment = auto_increment
        self.custom_time = custom_time
        self.max_name_length = max_name_length
        self.max_title_length = max_title_length
        self.rng = rng or random.SystemRandom()
        self.time_zone_offset_hours = time_zone_offset_hours
        self.used_auto_increment = False

    # -- context --

    @property
    def window(self) -> WindowInfo:
        if self._window is None:
            self._window = WindowInfo()
            if self._window_provider:
                try:
                    self._window = self._window_provider() or WindowInfo()
                except Exception:
                    self._window = WindowInfo()
        return self._window

    def _now(self) -> _dt.datetime:
        if self.custom_time is not None:
            return self.custom_time
        if self.time_zone_offset_hours is not None:
            tz = _dt.timezone(_dt.timedelta(hours=self.time_zone_offset_hours))
            return _dt.datetime.now(tz)
        return _dt.datetime.now()

    # -- parsing --

    def parse(self, pattern: str) -> str:
        now = self._now()
        uses_pm = "%pm" in pattern
        out: list[str] = []
        i = 0
        n = len(pattern)
        while i < n:
            c = pattern[i]
            if c != "%":
                out.append(c)
                i += 1
                continue
            token = next((t for t in TOKENS if pattern.startswith(t, i + 1)), None)
            if token is None:
                out.append(c)
                i += 1
                continue
            j = i + 1 + len(token)
            arg = None
            m = _ARG.match(pattern, j)
            if m and token in RANDOM_TOKENS | {"i", "ia", "iAa", "ib", "ix", "rf", "t", "pn"}:
                arg = m.group(1)
                j = m.end()
            out.append(self._expand(token, arg, now, uses_pm))
            i = j
        result = "".join(out)
        return self._finish(result)

    def _count(self, arg: str | None, default: int = 1) -> int:
        try:
            return max(1, int(arg)) if arg is not None else default
        except ValueError:
            return default

    def _next_increment(self) -> int:
        self.used_auto_increment = True
        self.auto_increment += 1
        return self.auto_increment

    def _expand(self, token: str, arg: str | None, now: _dt.datetime, uses_pm: bool) -> str:
        r = self.rng
        match token:
            case "t":
                title = self.window.title
                if self.max_title_length > 0:
                    title = title[: self.max_title_length]
                return title
            case "pn":
                return self.window.process_name
            case "y":
                return f"{now.year:04d}"
            case "yy":
                return f"{now.year % 100:02d}"
            case "mo":
                return f"{now.month:02d}"
            case "mon":
                return now.strftime("%B")
            case "mon2":
                return now.strftime("%b")
            case "w":
                return now.strftime("%A")
            case "w2":
                return now.strftime("%a")
            case "wy":
                return f"{now.isocalendar()[1]:02d}"
            case "d":
                return f"{now.day:02d}"
            case "h":
                h = now.hour
                if uses_pm:
                    h = h % 12 or 12
                return f"{h:02d}"
            case "mi":
                return f"{now.minute:02d}"
            case "s":
                return f"{now.second:02d}"
            case "ms":
                return f"{now.microsecond // 1000:03d}"
            case "pm":
                return "PM" if now.hour >= 12 else "AM"
            case "unix":
                return str(int(now.timestamp()))
            case "i":
                return str(self._next_increment()).zfill(self._count(arg, 0) if arg else 0)
            case "ia":
                return _to_base(self._next_increment(), ALNUM_LOWER).zfill(self._count(arg, 0) if arg else 0)
            case "iAa":
                return _to_base(self._next_increment(), ALNUM).zfill(self._count(arg, 0) if arg else 0)
            case "ix":
                return _to_base(self._next_increment(), HEX).zfill(self._count(arg, 0) if arg else 0)
            case "ib":
                base = min(62, max(2, self._count(arg, 36)))
                return _to_base(self._next_increment(), ALNUM[:base])
            case "rn":
                return "".join(r.choice(string.digits) for _ in range(self._count(arg)))
            case "ra":
                return "".join(r.choice(ALNUM) for _ in range(self._count(arg)))
            case "rna":
                return "".join(r.choice(NON_AMBIGUOUS) for _ in range(self._count(arg)))
            case "rx":
                return "".join(r.choice(HEX) for _ in range(self._count(arg)))
            case "guid":
                return "".join(str(uuid.UUID(int=r.getrandbits(128), version=4)).upper()
                               for _ in range(self._count(arg)))
            case "radjective":
                return "".join(r.choice(ADJECTIVES).capitalize() for _ in range(self._count(arg)))
            case "ranimal":
                return "".join(r.choice(ANIMALS) for _ in range(self._count(arg)))
            case "remoji":
                return "".join(r.choice(EMOJIS) for _ in range(self._count(arg)))
            case "rf":
                path = os.path.expanduser(arg or "")
                try:
                    with open(path, encoding="utf-8") as fh:
                        lines = [ln.rstrip("\r\n") for ln in fh if ln.strip()]
                    return r.choice(lines) if lines else ""
                except OSError:
                    return ""
            case "width":
                return str(self.image_width)
            case "height":
                return str(self.image_height)
            case "un":
                return _user_full_name()
            case "uln":
                return _login_name()
            case "cn":
                return platform.node()
            case "n":
                return "" if self.type in (NameParserType.FileName, NameParserType.FilePath,
                                           NameParserType.FolderPath) else "\n"
        return ""

    def _finish(self, result: str) -> str:
        if self.type == NameParserType.FileName:
            result = "".join("_" if ch in INVALID_FILENAME or ord(ch) < 32 else ch for ch in result).strip()
            result = result.strip(".") or result
        elif self.type in (NameParserType.FolderPath, NameParserType.FilePath):
            result = "".join("_" if ch in INVALID_PATH or ord(ch) < 32 else ch for ch in result)
        elif self.type == NameParserType.URL:
            from urllib.parse import quote
            result = quote(result, safe="/:?&=#%+@!$'()*,;~")
        if self.max_name_length > 0 and self.type == NameParserType.FileName and len(result) > self.max_name_length:
            result = result[: self.max_name_length]
        return result


def secure_random_string(length: int = 10) -> str:
    return "".join(secrets.choice(ALNUM) for _ in range(length))
