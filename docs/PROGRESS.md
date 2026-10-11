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
