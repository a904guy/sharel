"""Upload jobs (ShareX's UploadManager)."""
from __future__ import annotations

import io
import logging
from pathlib import Path

from ...core import clipboard
from ...core.jobs import JobContext

log = logging.getLogger("sharel.upload.jobs")


class UploadJobs:
    def upload_paths(self, paths: list[str], ts) -> None:
        for p in paths:
            pp = Path(p)
            if pp.is_dir():
                self.upload_folder(str(pp), ts)
            elif pp.is_file():
                self.tasks.upload_file(str(pp), ts)

    def upload_folder(self, folder: str, ts) -> None:
        """ShareX's UploadFolder: every file in the folder."""
        files = sorted(p for p in Path(folder).iterdir() if p.is_file() and not p.name.startswith("."))
        for f in files:
            self.tasks.upload_file(str(f), ts)

    def job_FileUpload(self, ctx: JobContext) -> None:
        if ctx.files:
            return self.upload_paths(ctx.files, ctx.task_settings)
        from ..gtkutil import open_files
        open_files(self.active_parent(), "Upload files", lambda ps: ps and self.upload_paths(ps, ctx.task_settings),
                   patterns=None, folder=self.store.app.file_upload_default_directory)

    def job_FolderUpload(self, ctx: JobContext) -> None:
        if ctx.files:
            for f in ctx.files:
                if Path(f).is_dir():
                    self.upload_folder(f, ctx.task_settings)
            return
        from ..gtkutil import select_folder
        select_folder(self.active_parent(), "Upload folder", lambda p: p and self.upload_folder(p, ctx.task_settings))

    def clipboard_upload(self, ts, content: clipboard.ClipboardContent | None = None) -> str:
        """ShareX's ClipboardUpload: image -> image host, files -> uploads, text -> text host,
        URL -> shorten / share / upload its contents depending on Clipboard upload options."""
        c = content or clipboard.read()
        up = ts.upload_settings
        if c.kind == "image":
            if ts.advanced_settings.process_images_during_clipboard_upload:
                self.tasks.run_image(c.image, ts)
            else:
                buf = io.BytesIO()
                c.image.save(buf, "PNG")
                from ...core.nameparser import NameParser, NameParserType
                name = NameParser(NameParserType.FileName, image_width=c.image.width,
                                  image_height=c.image.height).parse(up.name_format_pattern) + ".png"
                self.tasks.upload_data(buf.getvalue(), name, ts)
            return "image"
        if c.kind == "files":
            folders = [f for f in c.files if Path(f).is_dir()]
            if folders and up.clipboard_upload_auto_index_folder:
                for f in folders:
                    self.index_and_upload(f, ts)
                self.upload_paths([f for f in c.files if f not in folders], ts)
            else:
                self.upload_paths(c.files, ts)
            return "files"
        if c.kind == "text":
            text = c.text
            if not text.strip() and self.store.app.clipboard_upload_ignore_empty_text:
                return "empty"
            if c.is_url:
                url = text.strip()
                if up.clipboard_upload_url_contents:
                    self.tasks.download_upload(url, ts)
                    return "url-contents"
                if up.clipboard_upload_shorten_url:
                    self.tasks.shorten_url(url, ts)
                    return "shorten"
                if up.clipboard_upload_share_url:
                    self.tasks.share_url(url, ts)
                    return "share"
            self.tasks.upload_text(text, ts)
            return "text"
        self.message("Clipboard is empty", "There is nothing on the clipboard to upload.")
        return "empty"

    def index_and_upload(self, folder: str, ts) -> None:
        """IndexFolder: index the folder with the Tools settings and upload the index."""
        from ...tools.indexer import index_folder
        res = index_folder(folder, self.indexer_settings(ts))
        name = res.suggested_name if res.suggested_name.endswith(res.extension) else \
            f"{res.suggested_name}.{res.extension.lstrip('.')}"
        self.tasks.upload_data(res.text.encode("utf-8"), name, ts)

    @staticmethod
    def indexer_settings(ts):
        from ...tools.indexer import IndexerSettings
        tool = ts.tool_settings
        return IndexerSettings(output=tool.indexer_output, max_depth=tool.indexer_max_depth,
                               skip_hidden_folders=tool.indexer_skip_hidden, skip_hidden_files=tool.indexer_skip_hidden,
                               show_sizes=tool.indexer_show_sizes, custom_css=tool.indexer_custom_css)

    def job_ClipboardUpload(self, ctx: JobContext) -> None:
        self.clipboard_upload(ctx.task_settings)

    def job_ClipboardUploadWithContentViewer(self, ctx: JobContext) -> None:
        if not self.store.app.show_clipboard_content_viewer:
            return self.clipboard_upload(ctx.task_settings)
        from ..tools.clipboardviewer import open_clipboard_viewer
        open_clipboard_viewer(self, ctx, upload=True)

    def job_UploadText(self, ctx: JobContext) -> None:
        from ..windows.uploadtext import open_upload_text
        open_upload_text(self, ctx)

    def job_UploadURL(self, ctx: JobContext) -> None:
        from ..gtkutil import prompt_text
        c = clipboard.read()
        default = c.text.strip() if c.is_url else ""
        prompt_text(self.active_parent(), "Upload from URL", default,
                    lambda url: url and url.strip() and self.tasks.download_upload(url.strip(), ctx.task_settings),
                    body="The file at this URL is downloaded, then uploaded.")

    def job_DragDropUpload(self, ctx: JobContext) -> None:
        from ..windows.dropwindow import open_drop_window
        open_drop_window(self, ctx)

    def job_ShortenURL(self, ctx: JobContext) -> None:
        from ..gtkutil import prompt_text
        c = clipboard.read()
        default = c.text.strip() if c.is_url else ""
        prompt_text(self.active_parent(), "Shorten URL", default,
                    lambda url: url and url.strip() and self.tasks.shorten_url(url.strip(), ctx.task_settings))

    def job_StopUploads(self, ctx: JobContext) -> None:
        n = self.tasks.stop_all()
        self.message("Uploads stopped", f"{n} task(s) stopped.")
