# ShareL

ShareL is a ShareX-style screenshot, recording and upload tool for Ubuntu GNOME on Wayland. It is
Python 3.14 with GTK 4 and libadwaita, plus a small GNOME Shell extension for the things Wayland
apps can't do on their own (window lists, global hotkeys, stage capture, a tray click handler).

It follows ShareX's model closely: the same hotkey jobs, after-capture and after-upload tasks, task
settings, file-name tokens, image editor and effects, uploaders and custom uploaders (`.sxcu`),
and the same config shape. `docs/FEATURE_MAP.md` maps every ShareX feature to ShareL's module,
mechanism and proving test.

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

To remove it: `./uninstall.sh` (add `--purge` to also delete settings and history). Disable the
extension first: `gnome-extensions disable sharel@hawkins.app`.

For a system-wide package: `packaging/build-deb.sh` builds `dist/sharel_<version>_all.deb`.

## Hotkeys and the Print key

The defaults are ShareX's:

| Hotkey | Job |
| --- | --- |
| Print | Capture entire screen |
| Ctrl+Print | Capture region |
| Alt+Print | Capture active window |
| Shift+Print | Screen recording (MP4) |
| Ctrl+Shift+Print | Screen recording (GIF) |

GNOME binds Print, Shift+Print and Alt+Print to its own screenshot UI. ShareL never changes that
for you. Hotkey settings shows each clash. To give these keys to ShareL, free them yourself:

```sh
gsettings set org.gnome.shell.keybindings show-screenshot-ui "[]"
gsettings set org.gnome.shell.keybindings screenshot "[]"
gsettings set org.gnome.shell.keybindings screenshot-window "[]"
```

(`gsettings reset org.gnome.shell.keybindings <key>` restores each one.)

Hotkey backends, set under Application settings > Advanced:

- **Extension** (preferred): the shell grabs the accelerators and signals ShareL.
- **GlobalShortcuts portal**: GNOME asks you once to confirm the bindings.
- **GSettings custom keybindings**: ShareL-owned entries that run `sharel -<Job>`. This edits
  your keybindings, so it is used only when you choose it; ShareL removes its entries when you
  switch away.

## Command line

The command line mirrors ShareX's: `sharel -RectangleRegion`, `sharel -PrintScreen`,
`sharel -StartScreenRecorder`, `sharel -workflow "<hotkey name>"`, `sharel <files>` (upload),
`sharel -silent` (start in the tray), `sharel -autoclose`. Any HotkeyType name works as `-<Name>`.
A second invocation is handed to the running instance.

## Where things are

- Settings: `~/.config/ShareL/{ApplicationConfig,HotkeysConfig,UploadersConfig}.json`. Secrets
  go to the keyring when one is running.
- History: `~/.local/share/ShareL/History.json`
- Screenshots: `~/Pictures/ShareL/%y-%mo/`

## Development and tests

```sh
uv venv --python /usr/bin/python3 --system-site-packages && uv sync
uv run pytest -q                         # unit suite (sandboxed: private D-Bus, headless weston)
uv run ruff check src tests
gjs -m tests/js/test_logic.js            # the extension's pure logic
```

The unit suite never touches your session. It runs on a private D-Bus and a headless weston,
with private XDG directories and in-memory GSettings. Uploaders are tested against mocked HTTP
or local servers only.

End-to-end tests drive the real app in an isolated headless GNOME Shell (`tests/e2e/shell.sh`)
with [RootPilot](../RootPilot) for real input, AT-SPI and screenshots:

```sh
uv run pytest -q -m e2e tests/e2e/                     # all scenarios
uv run pytest -q -m e2e tests/e2e/test_e2e_record.py   # one module
```

Each module starts its own private shell: its own runtime dir, D-Bus and dconf, the ShareL and
RootPilot extensions, and Print unbound in the private dconf. A watchdog and a memory-capped
systemd scope make sure the shell never outlives the run. Run one e2e module at a time. Evidence
(screenshots, ffprobe output, OCR results) goes to `tests/e2e/artifacts/`.

Progress notes are in `docs/PROGRESS.md`. The feature map is generated:
`python3 scripts/featuremap.py build` (and `check`).

## License

GPL-3.0-or-later (see `LICENSE`). ShareL ports ShareX's behaviour and algorithms. ShareX is
GPL-3.0 as well.
