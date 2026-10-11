# ShareL UI contract (for anyone writing a window)

ShareL is a GTK 4 + libadwaita app (`src/sharel/app.py`, class `ShareLApp`). Windows are opened by
jobs (`src/sharel/ui/jobs/*.py`), the main window, the tray and `app.open-window::<name>`.

## What a window can use

- `app.store` — `ConfigStore` (`core/config.py`): `.app` (ApplicationConfig), `.hotkeys` (HotkeysConfig),
  `.uploaders` (UploadersConfig); `save()`, `save_app()`, `save_hotkeys()`, `save_uploaders()`,
  `task_settings_for(workflow_or_None)` (effective TaskSettings), `screenshots_folder(ts)`, `find_workflow()`.
  After editing config call the matching save and then `app.settings_changed()`.
- `app.tasks` — `TaskManager` (`core/tasks.py`): `run_image(img, ts, window=, region=)`,
  `upload_file(path, ts)`, `upload_data(bytes, name, ts)`, `upload_text(text, ts)`, `shorten_url(url, ts)`,
  `share_url(url, ts)`, `download_upload(url, ts)`; `add_listener(cb(event, TaskInfo))`.
- `app.history` — `HistoryManager` (`core/history.py`): `items()`, `search(HistoryFilter, newest_first, limit)`,
  `remove(id)`, `hosts()`, `types()`, `listeners`.
- `app.message(summary, body, urgent=False)` — desktop notification. `app.active_parent()` — a window to
  parent dialogs on (may be None). `app.run_region(RegionCaptureMode, ts, on_outcome)` — region capture
  overlay (outcome: `.image`, `.rect_logical`, `.color`, `.point_logical`, `.result.result`).
  `app.upload_paths(paths, ts)`, `app.clipboard_upload(ts, content=None)`.
- `sharel.core.clipboard` — `set_text`, `set_image(PIL)`, `set_files(paths)`, `read() -> ClipboardContent`
  (kind image|files|text|empty). `sharel.core.desktop` — `open_url`, `open_file`, `open_folder`,
  `show_in_folder`. `sharel.core.imaging` — `load(path)`, `encode_for_task`, `png_bytes`, `thumbnail`.
- `sharel.ui.gtkutil` — `texture_from_pil`, `pil_from_texture`, `picture(img, max_size)`, `open_files`,
  `select_folder`, `save_file`, `alert`, `ask`, `prompt_text`, `prompt_choice`, `human_size`, `idle(fn, *a)`
  (run on the main loop), `call_main`, `IMAGE_PATTERNS`, `VIDEO_PATTERNS`.
- `sharel.ui.forms.Form(obj, changed).group(title, only=[...], skip=[...])` — libadwaita rows generated
  from any settings dataclass (switches, spins, entries, combos, colour buttons, file choosers, flag lists,
  nested expanders, OAuth row with Authorize button via `on_authorize(obj, field_name)`, lists of dataclasses).
- `sharel.ui.placement` — `keep_above(win, position=None)`, `raise_and_focus(win)` (Wayland windows can't
  place themselves; these go through the ShareL extension when it runs, and do nothing otherwise).
- The extension client: `sharel.shell.client.shell()` → `ShellClient` or `NullShell` (`.available`).

## Conventions

- One module per window/tool. Tools: `src/sharel/ui/tools/<module>.py` with `open(app, ctx)` where `ctx` is a
  `core.jobs.JobContext` (`.task_settings`, `.files` = files given on the command line, `.workflow`). Extra
  entry points named below are part of the contract (other code already calls them).
- Use `Adw.Window` / `Adw.ApplicationWindow` with `application=app`, a header bar, a sensible default size.
  A tool opened twice re-presents its existing window. Close = destroy and forget it.
- Every control does its job. No placeholders, no "not implemented". Errors go to `gtkutil.alert`.
- Long work (hashing, video encoding, rembg, uploads) runs on a thread with progress and a Cancel button;
  results come back with `gtkutil.idle`. Never block the main loop for more than ~100 ms.
- Accessible names: buttons and fields carry a visible label or tooltip (end-to-end tests find them by name).
- Match ShareX's window and its options (read ShareX's source for the form: github.com/ShareX/ShareX).

## Testing

- Unit tests run in a sandbox (private D-Bus + headless weston; see `tests/sandbox.py`), so windows can be
  created in tests safely. Use `tests/uihelpers.py`: `run_app(body)` starts the real app (silent, no tray,
  no hotkeys, no extension) and runs `body(app)` on the main loop (a generator may `yield seconds`).
  `find`, `find_all`, `labels`, `button_with_label` search widget trees.
- Each window gets tests in `tests/test_ui_<area>.py` that open it, drive it programmatically (set entries,
  toggle switches, `button.emit("clicked")`), and assert real outcomes (files written, config changed,
  clipboard calls — monkeypatch `sharel.core.clipboard.set_text` etc.).
- `uv run pytest -q` must pass; `uv run ruff check src tests` must be clean.

## Feature map

Don't edit `docs/featuremap/*.md` directly (several people work in parallel). Write your rows to
`docs/featuremap/_updates_<yourname>.md`, one per line:
`| <fragment> | <feature exactly as in the fragment> | <module> | <mechanism> | done | <test id> |`
(fragments: `hotkeys`, `main_ui`, `effects`, `destinations_ui`, `custom_uploader`, `editor`, `mechanisms`...).
The integrator applies them with `scripts/featuremap.py apply`.
