# ShareL

ShareL is a ShareX-style screenshot, recording and upload tool for Ubuntu GNOME on Wayland. It is Python 3.14 with GTK 4 and libadwaita, plus a small GNOME Shell extension for the things Wayland apps can't do on their own (window lists, global hotkeys, stage capture, a tray click handler).

It follows ShareX's model closely: the same hotkey jobs, after-capture and after-upload tasks, task settings, file-name tokens, image editor and effects, uploaders and custom uploaders (`.sxcu`), and the same config shape. `docs/FEATURE_MAP.md` maps every ShareX feature to ShareL's module, mechanism and proving test.

![ShareL's main window: the sidebar with Capture, Upload, Workflows and Tools, and eight finished uploads shown as thumbnails](docs/screenshots/main.png)

## Screenshots

Every screenshot comes from `scripts/screenshots.py`. It runs ShareL in the isolated headless GNOME Shell of the end-to-end tests, with made-up demo images. The upload URLs come from a local stand-in host that answers with `example.com` links; nothing was uploaded anywhere.

**Task list.** The list view shows each upload's status and URL. The Capture menu is open.

![The main window in list view: eight completed uploads with example.com URLs, and the Capture menu open with Capture region, Screen recording, Scrolling capture and more](docs/screenshots/main-capture-menu.png)

**Region capture**, mid-selection: the selection's position and size, the magnifier with the pointer's coordinates, and the annotation toolbar across the top.

![Region capture overlay over the desktop: a dashed selection labelled with X, Y, W and H, a magnifier next to the pointer, the screen outside the selection dimmed](docs/screenshots/region-capture.png)

**Image editor** with a highlight, a rectangle, a pixelated area, an arrow, a text label and step numbers.

![The image editor on a mock web page: a yellow highlight on the headline, a red rectangle around the sign-up button, pixelated contact details, an arrow, a "Start here" label and numbered steps 1 to 3](docs/screenshots/editor.png)

**Screen recording** of a region in progress: a red border marks the recorded area, and the controls show the elapsed time, pause, stop and abort.

![A region recording over the main window: a red border around the thumbnails and a small control bar showing 00:04 with pause, stop and abort buttons](docs/screenshots/recording.png)

**After capture tasks.** The checked tasks run after every capture, here "Save image to file" and "Upload image to host".

![The main window's After capture tasks menu, with Save image to file and Upload image to host checked](docs/screenshots/after-capture-tasks.png)

**Task settings**, Capture page.

![Task settings window on its Capture page: show cursor, capture shadow, screenshot delay, capture backend and region capture options](docs/screenshots/task-settings.png)

**Destination settings.** Every uploader has its own settings page; Imgur is shown here.

![Destination settings: a searchable list of image, text and file uploaders on the left, and Imgur's settings on the right](docs/screenshots/destinations.png)

**Hotkey settings** with ShareX's default hotkeys. Keys that GNOME also binds are flagged as possible conflicts. The demo session runs with hotkeys switched off, so the others read "Disabled".

![Hotkey settings listing Ctrl+Print capture region, Print capture entire screen, Alt+Print capture active window, Shift+Print screen recording and Shift+Ctrl+Print GIF recording](docs/screenshots/hotkeys.png)

**Custom uploader editor** after opening a sample `.sxcu` (`sharel example-host.sxcu`) and answering Yes to "set as the active custom uploader". The host is made up, on `upload.example.com`.

![Custom uploader settings: the "Example image host" uploader with image and file uploader types, POST to https://upload.example.com/api/v1/images and an Authorization header](docs/screenshots/custom-uploader.png)

**History**, with the selected entry's details and preview.

![History window: eight image uploads with date, file name, host and example.com URL, and the details of sunset.png on the right](docs/screenshots/history.png)

**Image history**

![Image history: thumbnails of the uploaded demo images](docs/screenshots/image-history.png)

### Tools

**Color picker**

![Color picker with an orange standard colour selected, its HSB, RGB and CMYK values, and hex FF6A00](docs/screenshots/color-picker.png)

**Ruler** over a thumbnail: position, width, height, area, perimeter, distance and angle.

![Ruler overlay measuring one thumbnail, with its position, size, area, distance and angle readout above the measured box](docs/screenshots/ruler.png)

**QR code.** A URL is encoded on the left, and "Scan screen" decodes it back on the right.

![QR code window encoding https://i.example.com/KeY39pW.png, and the QR code scan window showing the same URL decoded from the screen](docs/screenshots/qr-code.png)

**OCR** of a region over a text editor.

![A text editor with a release checklist, and the OCR window showing the recognised text of the selected region](docs/screenshots/ocr.png)

**Image combiner** with three images, stacked vertically in the preview.

![Image combiner with sunset.png, weekly-users.png and autumn-palette.png listed, the orientation options and a vertical preview of the combined image](docs/screenshots/image-combiner.png)

**Hash checker**: the SHA-256 of a file.

![Hash checker showing the SHA256 of sunset.png](docs/screenshots/hash-check.png)

**Scrolling capture** of a long document in a text editor, stitched into one image.

![Scrolling capture result: the frames of a long document of numbered lines stitched into one tall image, with the frame count and size above it](docs/screenshots/scrolling-capture.png)

**Tray menu** in the top bar.

![ShareL's tray menu: Capture, Upload, Workflows, Tools, the task and settings entries, History, Show main window and Exit](docs/screenshots/tray-menu.png)

**Dark style**: the main window with Theme set to Dark.

![The main window in libadwaita's dark style with the same eight thumbnails](docs/screenshots/main-dark.png)

## Install

```sh
./install.sh --deps     # first time: apt-installs the system packages (asks for sudo)
./install.sh            # per-user install: venv in ~/.local/share/sharel, commands in ~/.local/bin
./install.sh --dev      # or: launchers that run this checkout (for development)
```

The installer never enables the extension or changes keybindings. After installing:

1. Log out and back in (GNOME Shell on Wayland only picks up new extensions at login).
2. Enable the extension: `gnome-extensions enable sharel@hawkins.app`
3. Start ShareL from the app grid, or run `sharel`.

To remove it: `./uninstall.sh` (add `--purge` to also delete settings and history). Disable the extension first: `gnome-extensions disable sharel@hawkins.app`.

For a system-wide package: `packaging/build-deb.sh` builds `dist/sharel_<version>_all.deb`.

## Hotkeys and the Print key

The defaults are ShareX's:

| Hotkey | Job |
| --- | --- |
| Print | Capture entire screen |
| Ctrl+Print | Capture region |
| Alt+Print | Capture active window |
| Shift+Print | Screen recording |
| Ctrl+Shift+Print | Screen recording (GIF) |

GNOME binds Print, Shift+Print and Alt+Print to its own screenshot UI. ShareL never changes that for you. Hotkey settings shows each clash. To give these keys to ShareL, free them yourself:

```sh
gsettings set org.gnome.shell.keybindings show-screenshot-ui "[]"
gsettings set org.gnome.shell.keybindings screenshot "[]"
gsettings set org.gnome.shell.keybindings screenshot-window "[]"
```

(`gsettings reset org.gnome.shell.keybindings <key>` restores each one.)

Hotkey backends, set under Application settings > Advanced:

- **Extension** (preferred): the shell grabs the accelerators and signals ShareL.
- **GlobalShortcuts portal**: GNOME asks you once to confirm the bindings.
- **GSettings custom keybindings**: ShareL-owned entries that run `sharel -<Job>`. This edits your keybindings, so it is used only when you choose it; ShareL removes its entries when you switch away.

## Command line

The command line mirrors ShareX's: `sharel -RectangleRegion`, `sharel -PrintScreen`, `sharel -StartScreenRecorder`, `sharel -workflow "<hotkey name>"`, `sharel <files>` (upload), `sharel -silent` (start in the tray), `sharel -autoclose`. Opening a ShareX custom uploader (`sharel uploader.sxcu`) or image effect package (`sharel effects.sxie`), or double-clicking one once installed, imports it instead of uploading it. Any HotkeyType name works as `-<Name>`. A second invocation is handed to the running instance.

## Where things are

- Settings: `~/.config/ShareL/{ApplicationConfig,HotkeysConfig,UploadersConfig}.json`. Secrets go to the keyring when one is running.
- History: `~/.local/share/ShareL/History.json`
- Screenshots: `~/Pictures/ShareL/%y-%mo/`

## Development and tests

```sh
uv venv --python /usr/bin/python3 --system-site-packages && uv sync
uv run pytest -q                         # unit suite (sandboxed: private D-Bus, headless weston)
uv run ruff check src tests
gjs -m tests/js/test_logic.js            # the extension's pure logic
```

The unit suite never touches your session. It runs on a private D-Bus and a headless weston, with private XDG directories and in-memory GSettings. Uploaders are tested against mocked HTTP or local servers only.

End-to-end tests drive the real app in an isolated headless GNOME Shell (`tests/e2e/shell.sh`) with [RootPilot](../RootPilot) for real input, AT-SPI and screenshots:

```sh
uv run pytest -q -m e2e tests/e2e/                     # all scenarios
uv run pytest -q -m e2e tests/e2e/test_e2e_record.py   # one module
```

Each module starts its own private shell: its own runtime dir, D-Bus and dconf, the ShareL and RootPilot extensions, and Print unbound in the private dconf. A watchdog and a memory-capped systemd scope make sure the shell never outlives the run. Run one e2e module at a time. Evidence (screenshots, ffprobe output, OCR results) goes to `tests/e2e/artifacts/`.

The README's screenshots are made the same way, from made-up demo content. They never touch your session. Regenerate them with:

```sh
uv run python scripts/screenshots.py            # every scene into docs/screenshots/
uv run python scripts/screenshots.py --only editor,qr-code
```

Progress notes are in `docs/PROGRESS.md`. The feature map is generated: `python3 scripts/featuremap.py build` (and `check`).

## License

GPL-3.0-or-later (see `LICENSE`). ShareL ports ShareX's behaviour and algorithms. ShareX is GPL-3.0 as well.
