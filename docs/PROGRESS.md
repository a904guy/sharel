# ShareL progress log

## Status (2026-10-12)

- Feature map: 507 rows, 498 done, 9 excluded (defunct services, each with dated evidence), 0 open.
  `python3 scripts/featuremap.py check` also fails if a row cites a test node that doesn't exist.
- Unit suite: `uv run pytest -q`: 1450 passed, 41 deselected (the e2e tests). `uv run ruff check src tests`
  is clean.
- E2E: every scenario of 6.3 (1-15) has a module under `tests/e2e/`. Run them one module at a time
  (`uv run pytest -q -m e2e tests/e2e/test_e2e_<module>.py`). Evidence goes to `tests/e2e/artifacts/`.
  Last full pass (2026-10-12, 00:46-00:58, from main 15bee55): 41/41 passed. Per module: capture 5,
  main 2, editor 3, ocr_qr 2, record 2, upload 1, scrolling 1, tools_image 16, tools_misc 5,
  tools_screen 4. After the S5 fix, the capture module passed 8 of 9 repeated runs; the one failure came
  before logs were kept, so its cause is unknown.
- Live smoke test on "mine": not done. The session was locked from about 22:16 on 2026-10-11 through
  01:05 on 2026-10-12 (`org.gnome.ScreenSaver.GetActive` = true, RootPilot `locked: true`), so overlays
  and captures could only show the lock screen. ShareL was never started on the real session. The
  setup is ready to run once the session is unlocked: throwaway XDG dirs, in-memory GSettings, hotkeys
  disabled, save-only after-capture. A snapshot taken before and after (enabled extensions,
  keybindings, media keys, the full `dconf dump /`, autostart, `~/.local` install, ShareL dirs) shows no
  change.
- Commits are unsigned (`commit.gpgsign=false`: the signing agent refuses) and carry no AI trailer.

## Phase 1: core (2026-10-11)
- Repo initialised; uv venv (system site packages, Python 3.14); `wl-clipboard` installed.
- Core types, unit-tested: enums (ShareX names), dataclass JSON serializer, ApplicationConfig /
  HotkeysConfig / TaskSettings with `use_default_*` resolution, NameParser (all tokens and `{n}`),
  History, imaging, the WorkerTask pipeline (after-capture and after-upload flags in ShareX order) and
  TaskManager.
- Uploader framework, every ShareX uploader, shortener and sharing service (against mocked HTTP or local
  servers only), custom uploaders (`.sxcu`), image effects, the image editor and region capture.

## Phase 2: desktop integration and UI
- GNOME Shell extension (`app.sharel.Shell`): window list, pointer, stage capture, global accelerators,
  window placement, borderless/top-most, mouse highlighter, tray click handler. Fallbacks without it:
  Mutter ScreenCast and the portal for capture, the GlobalShortcuts portal for hotkeys, wl-copy for the
  clipboard.
- Main window, tray, every settings window, every tool window, the after-capture/after-upload windows.
- Packaging: `install.sh` (per-user, `--dev`, `--deps`), `uninstall.sh`, `.deb` builder, desktop entries,
  GSettings schema, Nautilus scripts, native messaging host. The installer never enables the extension
  or changes keybindings.

## E2E harness
- Each module starts its own headless gnome-shell (`tests/e2e/shell.sh`): private runtime dir, D-Bus and
  dconf, the ShareL and RootPilot extensions, Print unbound in the private dconf, a watchdog and a
  memory-capped systemd scope. Only one shell runs at a time.
- Every step asserts an outcome that would fail if the action did nothing (no tautologies).

## Screen recorder hardening
- Recordings of the webcam: the recorder held its session-bus connection in a local; GLib caches it
  weakly, so it closed, Mutter ended the ScreenCast session and pipewiresrc autoconnected to the default
  video node (a camera). The recorder now holds the connection.
- Guards: frame size checked before recording (region at a monitor scale; RecordMonitor against the
  monitor's rect times its scale; portal monitor against its reported size); while recording: session
  Closed, Mutter's name vanishing, a 4 s stall watchdog (relies on keepalive-time), caps renegotiated to
  another size. A failure tears down, deletes the partial file and notifies.
- `on-disconnect=error` is not used: with PipeWire 1.6.2 it fires while pause drops buffers.
- Pause uses valves (drop-mode forward-sticky-events, so stop while paused finishes) plus a pad offset of
  the paused wall time; audio is cut the same way.
- Portal fallback: the chosen monitor is cropped to the region (videocrop); a window share is recorded
  whole, with a note. Videos are written as `<name>.part` and renamed when complete.
- S10 compares the last decoded frame with a screenshot of the region (and a shifted control).

## Scrolling capture
- The stitcher (a port of ShareX's CombineImages) handles GTK's fractional scrolling (tolerant row
  match), small overlaps (search from the top) and blank space (a match must contain content). Tested
  on real GTK frames (`tests/data/scroll_gtk`), ±3 noise and blank overlaps; S11 checks all 120 lines of
  a long gnome-text-editor document by OCR.

## Unit sandbox
- `tests/sandbox.py`: private dbus-daemon with a private, empty service dir (nothing on it is
  D-Bus-activated; before this, a test started the real Nautilus), headless weston, private XDG dirs,
  in-memory GSettings. `tests/conftest.py` fails closed if the sandbox isn't in place.
- `tests/launchguard.py`: an autouse guard fails any test that opens an app or URL on the desktop (Gio,
  Gtk launchers, xdg-open, gio open); the `launched` fixture records launches instead of performing them.
- `tests/fakebus.py`: fake session services (FileManager1, portals, Mutter, notifications).

## Fixes found by the feature-map audit (2026-10-11/12)
- Stop uploads: a task stopped mid-request ended Completed and ran its after-upload tasks; it now ends
  Stopped.
- Task list rows never updated during an upload (bound once).
- Editor closed before its first allocation: an always-ready idle polled forever. Besides the CPU, it
  froze "Print image" for good: GTK's synchronous print renders pages from an idle below default-idle
  priority in a nested loop. Regression tests run in-process (an idle check, and print to PDF after an
  editor and a pin closed, under a hard deadline).
- i18n: `_` follows `set_language`; the configured language is applied at startup.
- Text dialog (region capture and editor): focus sat on a response button, so Space answered it and
  the rest of the typing reached the canvas as tool shortcuts (Return finished the capture). The text
  view now gets the focus and the canvas ignores keys while one of its dialogs is open. This was S5's
  intermittent failure.
- Region toolbar: the tool list's separator was drawn as a toggle button labelled `sep_tools`.
- Spin rows: `Adw.SpinRow`'s default "presentation" role hid them from assistive tech; one helper
  (`gtkutil.spin_row_widget`) makes every spin row, with a test that no code bypasses it.

## Analyze image with AI
- ShareX 18's AIOptions and its four providers (`tools/ai.py`): OpenAI Responses, OpenAI-compatible Chat
  Completions (custom URL), Gemini, OpenRouter. Keys are keyring secrets (UploadersConfig
  `tool/AnalyzeImage`). Used only when the chosen provider has a key; otherwise nothing is sent and the
  window shows the local analysis only. Tested against a local HTTP server.

## Excluded rows (defunct services)
QRnet, TwoGP, Firebase Dynamic Links, Slexy, SendSpace, Lambda, StumbleUpon, Delicious, Photobucket.
Each row in `docs/featuremap/` holds the evidence: Wayback captures plus dated probes of the old API
endpoints.

## Supervisor items (2026-10-12): all done in commit 15bee55

1. **QRnet, TwoGP: excluded with evidence** (`docs/featuremap/shorteners.md`). Both domains still resolve,
   but they now run different products, and ShareX's endpoint is gone.
   - QRnet: 2016 capture "Create Short-URL & QR-Code"; 2026-01-04 capture "QR.net - Your QR code
     generator" (https://web.archive.org/web/20260104161420/http://qr.net/). Probe 2026-10-12 04:06 UTC:
     `GET https://qr.net/api/short?longurl=...` -> `302 Location: https://qr.net`.
   - TwoGP: 2019 capture "2.gp - Simply shorten urls"; 2020-2024 captures are bare 301s; the 2026 capture
     is a relaunched log-in/register site (https://web.archive.org/web/20260101011921/http://2.gp/).
     Probe: `GET https://www.2.gp/api/short?longurl=...` -> `HTTP 404`.
2. **Photobucket: the old evidence was weak and has been replaced** (`docs/featuremap/uploaders_image.md`).
   The old link only showed an image-viewer ad page. The new row records:
   - ShareX's OAuth endpoint `api.photobucket.com/login/request` answered 405 (POST only, so it existed)
     in every capture 2008-2011, and 404 on 2024-08-11
     (https://web.archive.org/web/20240811030645/http://api.photobucket.com/login/request).
   - Today, GET and POST both answer 404 "Resource not found".
   - developer.photobucket.com has 301-redirected to the homepage since 2016-01-18.
   - A web search found no shutdown announcement.
3. **AnalyzeImage AI provider: added** (`src/sharel/tools/ai.py`). An OpenAI-compatible endpoint (custom
   URL, model, API key) plus OpenAI Responses, Gemini and OpenRouter. Keys are stored in the keyring.
   Without a key nothing is sent and only the local analysis is shown. `tests/test_ai_analyze.py` has 10
   tests against a local HTTP server, including `test_openai_compatible_chat_completions`,
   `test_api_keys_go_to_the_keyring` and `test_window_without_a_key_sends_nothing_and_keeps_local_analysis`
   (the server gets zero requests).
4. **Autostart: off by default.** `start_with_system: bool = False` (`src/sharel/core/config.py:695`).
   `tests/test_ui_settings.py::test_fresh_config_never_writes_an_autostart_entry` covers a first run,
   settings edits and an import: no autostart directory is created. The test fails with the default on.
5. **Feature map:** `python3 scripts/featuremap.py check` prints `{'done': 497, 'excluded': 9, 'open': 0}`
   and exits 0. It also fails on a cited test node that doesn't exist
   (`tests/test_packaging_data.py::test_feature_map_check_passes_and_catches_missing_tests`).

## README screenshots (2026-10-12)
- `scripts/screenshots.py` makes `docs/screenshots/*.png` (21 scenes) in the e2e headless shell,
  driven through RootPilot. It uses throwaway dirs under `/tmp/sharel-demo`, in-memory GSettings,
  hotkeys off and no keyring. Content is made-up demo images, plus a local stand-in upload host that
  answers with `example.com` URLs. Nothing is uploaded. `--only`, `--list` and `--hold` support
  iteration; `--lock` serializes it with the e2e runs. ShareL restarts before each scene, so no
  window carries over.
- PNGs are lossless unless PIL's 256-colour palette keeps every solid colour within 12 levels
  (oxipng and pngquant aren't installed).
- `test_readme_matches_the_code` checks that every screenshot the README shows exists and has alt
  text.
- Found while making them:
  - The canvas had no pointer-leave handler, so the editor's magnifier stayed painted where the
    pointer had left the canvas. Fixed (`ShapeManager.pointer_left`), with
    `tests/test_ui_capture.py::test_magnifier_goes_away_when_the_pointer_leaves_the_canvas` (fails
    without the fix).
  - `sharel x.sxcu` uploaded the file. ShareX imports it (`-CustomUploader x.sxcu`, its file
    association). Now both forms import it through the `.sxcu` reader, including when forwarded to
    the running instance, and open Custom uploader settings. ShareX's question follows: "set as the
    active custom uploader for images/files?" Yes switches the selected custom uploader and the
    default destination for each declared type; No only adds it.
  - A shared-mime-info package registers `*.sxcu`, and the desktop entry claims it. It is installed
    by `install.sh` and the `.deb`, and `uninstall.sh` removes it.
  - Tests:
    - `tests/test_ui_customuploader.py`: the Yes and No answers;
    - `tests/test_mechanisms_desktop.py::test_a_sxcu_forwarded_to_the_running_sharel_is_imported_not_uploaded`;
    - `tests/test_cli.py::test_sxcu_files_are_imported_not_uploaded`.

    None of them upload, and the plain-path test fails when the CLI routes `.sxcu` to `req.files`
    again. The custom-uploader scene opens its sample this way and answers Yes.
- Found in review of the screenshots:
  - The scrolling capture window's view switcher pages had no icons, so they drew the
    missing-image placeholder. `emblem-ok-symbolic` (hotkey "registered") didn't exist either.
    The editor's arrow tool used `go-next-symbolic`, a bare chevron, and the line tool used the
    strikethrough icon. ShareL now ships `sharel-draw-arrow-symbolic` and
    `sharel-draw-line-symbolic` (data/icons/actions; the app adds the folder to the icon theme;
    install.sh and the .deb install them). `tests/test_ui_icons.py` resolves every icon name in
    `src/sharel/ui` and the editor's mapping against the theme, and checks that view-switcher
    pages have icons.
  - Wrapped URLs and paths got hyphens inserted by Pango (`s-unset.png`, `vuVVn-FT.png`).
    `gtkutil.no_hyphens` / `text_view_no_hyphens` turn that off (word or character wrapping) in:
    - the history details;
    - QR scan results;
    - the tools' text views (OCR, QR, clipboard);
    - file-name, path and command labels.

    Covered by `tests/test_ui_history.py` (fails with the old label).
  - `.sxie` packages are opened like `.sxcu`: `sharel x.sxie` / `-ImageEffect x.sxie` (also
    forwarded) import the preset through the existing reader into the default task settings and
    Image effects. ShareX's "Would you like to enable image effects?" follows: Yes adds
    AddImageEffects. Nothing is uploaded. A MIME type is added to the same package. Tests: Yes,
    No, already enabled, forwarded, CLI.

## Unit sandbox cleanup (2026-10-12)
- Killed or timed-out runs had left dbus-daemon, weston and their `sharel-unit-*` runtime dirs behind.
  Both now start with PR_SET_PDEATHSIG, and a detached watchdog removes the sandbox dirs when the test
  process is gone. `test_a_killed_run_leaves_no_daemon_and_no_directory` SIGKILLs an inner pytest with
  its own fresh sandbox and fails without either mechanism. The leftovers (19 processes, 27 dirs, all
  sandbox-only) were removed, with a check that no test run was active.

## System-wide install and the blank tray menu (2026-10-12)
- The .deb's Depends now names every GI typelib (glib, pango, gdkpixbuf) and helper program (xdg-utils,
  fontconfig) directly. `test_deb_depends_cover_every_import` maps every import and `require_version` in
  src to a package. A plain import check of sharel.cli, app and annotate had passed even though the system
  python3 lacked qrcode and paramiko, because both are imported lazily.
- The tray menu on a real session showed submenu arrows and separators with no text. ubuntu-appindicators
  gets the layout with only `type`/`children-display` and fetches labels later with GetGroupProperties on
  the ids it holds. sharel-tray rebuilt the whole GtkMenu, with new dbusmenu ids, three times at startup
  and after every task (`send_state` always re-sent the menu). The extension could end up holding ids
  whose labels never arrived. The helper now keeps one menu and updates the items in place, so ids are
  stable. The app only sends a menu that changed, and the icon appears once the real menu is in place.
  `tests/test_tray_dbusmenu.py` runs the real helper on the sandbox bus: the ids must survive updates,
  and GetGroupProperties on them must return every label. The old code fails it (ids 219… become 1075…).
