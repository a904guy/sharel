"""Pastebin (ShareX TextUploaders/Pastebin.cs) against https://pastebin.com/doc_api.

The developer key is the user's own (ShareX ships one; get yours at https://pastebin.com/doc_api).
With a username and password, ShareL logs in (api_login.php) and keeps the returned api_user_key so
pastes belong to the account (and Private pastes are possible).
"""
from __future__ import annotations

from dataclasses import dataclass

from ..base import TextUploader, UploadContext, UploaderError, UploadResult, check_response
from ..registry import register, setting

POST_URL = "https://pastebin.com/api/api_post.php"
LOGIN_URL = "https://pastebin.com/api/api_login.php"
RAW_URL = "https://pastebin.com/raw/"

PRIVACY = {"Public": "0", "Unlisted": "1", "Private": "2"}
# ShareX PastebinExpiration names -> API values (6M and 1Y are newer API options)
EXPIRATION = {"N": "N", "M10": "10M", "H1": "1H", "D1": "1D", "W1": "1W", "W2": "2W", "M1": "1M", "M6": "6M",
              "Y1": "1Y"}
SYNTAXES = [
    "text", "4cs", "6502acme", "6502kickass", "6502tasm", "abap", "actionscript", "actionscript3", "ada", "aimms",
    "algol68", "apache", "applescript", "apt_sources", "arm", "asm", "asp", "asymptote", "autoconf", "autohotkey",
    "autoit", "avisynth", "awk", "bascomavr", "bash", "basic4gl", "dos", "bibtex", "blitzbasic", "b3d", "bmx",
    "bnf", "boo", "bf", "c", "c_winapi", "c_mac", "cil", "csharp", "cpp", "cpp-winapi", "cpp-qt", "c_loadrunner",
    "caddcl", "cadlisp", "ceylon", "cfdg", "chaiscript", "chapel", "clojure", "klonec", "klonecpp", "cmake",
    "cobol", "coffeescript", "cfm", "css", "cuesheet", "d", "dart", "dcl", "dcpu16", "dcs", "delphi", "oxygene",
    "diff", "div", "dot", "e", "ezt", "ecmascript", "eiffel", "email", "epc", "erlang", "euphoria", "fsharp",
    "falcon", "filemaker", "fo", "f1", "fortran", "freebasic", "freeswitch", "gambas", "gml", "gdb", "genero",
    "genie", "gettext", "go", "groovy", "gwbasic", "haskell", "haxe", "hicest", "hq9plus", "html4strict", "html5",
    "icon", "idl", "ini", "inno", "intercal", "io", "ispfpanel", "j", "java", "java5", "javascript", "jcl",
    "jquery", "json", "julia", "kixtart", "kotlin", "latex", "ldif", "lb", "lsl2", "lisp", "llvm", "locobasic",
    "logtalk", "lolcode", "lotusformulas", "lotusscript", "lscript", "lua", "m68k", "magiksf", "make", "mapbasic",
    "markdown", "matlab", "mirc", "mmix", "modula2", "modula3", "68000devpac", "mpasm", "mxml", "mysql", "nagios",
    "netrexx", "newlisp", "nginx", "nimrod", "nsis", "oberon2", "objeck", "objc", "ocaml-brief", "ocaml", "octave",
    "oorexx", "pf", "glsl", "oobas", "oracle11", "oracle8", "oz", "parasail", "parigp", "pascal", "pawn", "pcre",
    "per", "perl", "perl6", "php", "php-brief", "pic16", "pike", "pixelbender", "pli", "plsql", "postgresql",
    "postscript", "povray", "powershell", "powerbuilder", "proftpd", "progress", "prolog", "properties", "providex",
    "puppet", "purebasic", "pycon", "python", "pys60", "q", "qbasic", "qml", "rsplus", "racket", "rails", "rbs",
    "rebol", "reg", "rexx", "robots", "rpmspec", "ruby", "gnuplot", "rust", "sas", "scala", "scheme", "scilab",
    "scl", "sdlbasic", "smalltalk", "smarty", "spark", "sparql", "sqf", "sql", "standardml", "stonescript",
    "sclang", "swift", "systemverilog", "tsql", "tcl", "teraterm", "thinbasic", "typoscript", "unicon", "uscript",
    "upc", "urbi", "vala", "vbnet", "vbscript", "vedit", "verilog", "vhdl", "vim", "visualprolog", "vb",
    "visualfoxpro", "whitespace", "whois", "winbatch", "xbasic", "xml", "xorg_conf", "xpp", "yaml", "z80", "zxbasic"]


@dataclass
class PastebinSettings:
    api_dev_key: str = setting("", label="Developer API key", secret=True,
                               help="Your api_dev_key from https://pastebin.com/doc_api (sign in to see it).")
    username: str = setting("", label="Username", help="Optional: log in so pastes belong to your account.")
    password: str = setting("", label="Password", secret=True)
    user_key: str = setting("", label="User key", secret=True, hidden=True)
    exposure: str = setting("Unlisted", label="Exposure", choices=list(PRIVACY),
                            help="Private needs a logged-in account.")
    expiration: str = setting("N", label="Expiration", choices=list(EXPIRATION),
                              help="N = never, M10 = 10 minutes, H1 = 1 hour, D1 = 1 day, W1/W2 = 1/2 weeks, "
                                   "M1/M6 = 1/6 months, Y1 = 1 year.")
    title: str = setting("", label="Title")
    text_format: str = setting("text", label="Syntax highlighting", choices=SYNTAXES)
    folder_key: str = setting("", label="Folder key", help="Optional folder of your account (needs login).")
    raw_url: bool = setting(False, label="Use raw URL", help="https://pastebin.com/raw/<key> instead of the page.")


def login(settings: PastebinSettings, ctx: UploadContext) -> str:
    """api_login.php: username + password -> api_user_key (kept in settings)."""
    if not settings.api_dev_key:
        raise UploaderError("Enter your Pastebin developer API key first")
    if not settings.username or not settings.password:
        settings.user_key = ""
        raise UploaderError("Enter the Pastebin username and password first")
    resp = ctx.post(LOGIN_URL, data={"api_dev_key": settings.api_dev_key, "api_user_name": settings.username,
                                     "api_user_password": settings.password})
    text = check_response(resp, "Pastebin login").strip()
    if not text or text.startswith("Bad API request") or " " in text:
        settings.user_key = ""
        raise UploaderError(f"Pastebin login failed: {text}", text)
    settings.user_key = text
    ctx.save_settings()
    return text


def authorize(settings: PastebinSettings, ctx: UploadContext) -> str:
    return login(settings, ctx)


@register("text", "Pastebin", PastebinSettings, title="Pastebin", homepage="https://pastebin.com")
class PastebinUploader(TextUploader):
    settings: PastebinSettings

    def validate(self) -> list[str]:
        problems = []
        if not self.settings.api_dev_key:
            problems.append("Developer API key is required")
        if self.settings.exposure == "Private" and not (self.settings.user_key or self.settings.username):
            problems.append("Private pastes need a Pastebin login")
        return problems

    def upload_text(self, text: str, file_name: str) -> UploadResult:
        s = self.settings
        if not text:
            raise UploaderError("Nothing to upload: the text is empty")
        if s.username and s.password and not s.user_key:
            login(s, self.ctx)
        args = {
            "api_dev_key": s.api_dev_key,
            "api_option": "paste",
            "api_paste_code": text,
            "api_paste_name": s.title,
            "api_paste_format": s.text_format or "text",
            "api_paste_private": PRIVACY.get(s.exposure, "1"),
            "api_paste_expire_date": EXPIRATION.get(s.expiration, "N"),
        }
        if s.user_key:
            args["api_user_key"] = s.user_key
            if s.folder_key:
                args["api_folder_key"] = s.folder_key
        resp = self.ctx.post(POST_URL, data=args)
        body = resp.text.strip()
        if not body.startswith(("https://", "http://")):
            raise UploaderError(f"Pastebin upload failed: {body[:300]}", body)
        url = body
        if s.raw_url:
            url = RAW_URL + body.rstrip("/").rsplit("/", 1)[-1]
        return UploadResult(url=url, response=body)
