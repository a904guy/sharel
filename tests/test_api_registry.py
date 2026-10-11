"""Every file destination in this slice is registered under its FileDestination name."""
import dataclasses
import importlib

from sharel.core.enums import FileDestination
from sharel.uploaders import registry

SERVICES = {"Dropbox": "api_dropbox", "OneDrive": "api_onedrive", "GoogleDrive": "api_googledrive",
            "Puush": "api_puush", "Box": "api_box", "Mega": "api_mega", "MediaFire": "api_mediafire",
            "Pushbullet": "api_pushbullet", "Localhostr": "api_localhostr", "ImgFish": "api_imgfish",
            "Streamable": "api_streamable", "Sul": "api_sul", "Lithiio": "api_lithiio", "YouTube": "api_youtube"}
DEFUNCT = {"Lambda", "SendSpace"}


def test_all_file_api_destinations_registered():
    names = {d.value for d in FileDestination}
    for name, module in SERVICES.items():
        assert name in names
        info = registry.get("file", name)
        assert info is not None and info.available, name
        assert info.cls.__module__ == f"sharel.uploaders.services.{module}"
        settings = info.settings_cls()  # every field has a default
        for f in dataclasses.fields(settings):
            assert "label" in f.metadata, (name, f.name)
        if info.oauth is not None:
            assert {"client_id", "client_secret", "oauth2"} <= {f.name for f in dataclasses.fields(settings)}
            mod = importlib.import_module(info.cls.__module__)
            assert callable(mod.authorize)
            assert next(f for f in dataclasses.fields(settings) if f.name == "oauth2").metadata["kind"] == "oauth"
    for name in DEFUNCT:
        assert name in names and registry.get("file", name).defunct
