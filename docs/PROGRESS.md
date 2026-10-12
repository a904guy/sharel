# ShareL progress log

## Phase 1: skeleton (in progress)
- 2026-10-11: repo initialised; uv venv (system site packages, Python 3.14); `wl-clipboard` installed.
- Core types done and unit-tested: enums (ShareX names), dataclass JSON serializer, ApplicationConfig /
  HotkeysConfig / TaskSettings with `use_default_*` resolution, NameParser (all tokens + `{n}`),
  History, imaging (formats, size fallback, FileExistAction, thumbnails), WorkerTask pipeline
  (after-capture and after-upload flags in ShareX order) and TaskManager (threads, progress, cancel).
- Uploader framework: base classes, UploadContext (proxy, progress, cancel, prompts), registry with
  defunct-service entries, UploadersConfig with libsecret, dispatch (FileUploader redirection,
  filters, retries, secondary uploaders).
- Effects base: ImageEffect / registry / presets.
- Feature map: 506 rows, generated from `docs/featuremap/*.md` by `scripts/featuremap.py`.

## Screen recorder hardening (2026-10-11)
- Root cause of recordings of the webcam: the recorder held its session-bus connection in a local;
  GLib caches it weakly, so it closed on return, Mutter ended the ScreenCast session, and
  pipewiresrc autoconnected to the default video node (a camera). Not a regression: real
  ScreenCast recording had only run against videotestsrc before the first e2e run.
- Guards: frame-size check before recording (region at a monitor scale; RecordMonitor against the
  monitor's rect x its scale; portal monitor against its reported size), then while recording:
  session Closed, Mutter's name vanishing, 4 s stall watchdog (relies on keepalive-time), caps
  renegotiated to another size. Failure tears down, deletes the partial file, notifies.
- `on-disconnect=error` is not used: with PipeWire 1.6.2 it fires while pause drops buffers.
- Pause uses valves (drop-mode forward-sticky-events, so stop while paused finishes) plus a pad
  offset of the paused wall time; audio is cut the same way (tested with audiotestsrc).
- Portal fallback: the chosen monitor is cropped to the region (videocrop); a window share is
  recorded whole, with a note. Not exercised against a real portal dialog (the headless shell's
  portal is unusable for ScreenCast); covered by unit tests on the crop and the source check.
- Videos are written as `<name>.part` and renamed when complete.
- For the live "mine" smoke test: one static region recording of 8 s or more with the webcam
  attached (no false stall; last frame matches a screenshot of the region), plus a real click on
  a tray submenu header.
