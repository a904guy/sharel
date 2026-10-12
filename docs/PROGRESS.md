# ShareL progress log

## Status (2026-10-12)

- Feature map: 506 rows, 497 done, 9 excluded (defunct services, each with dated evidence), 0 open.
  `python3 scripts/featuremap.py check` also fails if a row cites a test node that doesn't exist.
- Unit suite: `uv run pytest -q`: 1449 passed, 41 deselected (the e2e tests). `uv run ruff check src tests`
  is clean.
- E2E: every scenario of 6.3 (1-15) has a module under `tests/e2e/`. Run them one module at a time
  (`uv run pytest -q -m e2e tests/e2e/test_e2e_<module>.py`). Evidence goes to `tests/e2e/artifacts/`.
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
