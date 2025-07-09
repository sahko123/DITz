import shutil, hashlib
from pathlib import Path
import os
from PySide6.QtCore import QObject, Signal
import datetime
from utils import get_base_tokens

log = lambda m: print(f"[DITZ] {m}", flush=True)

VIDEO_EXTENSIONS = {
    ".mp4", ".m4v", ".mov", ".avi", ".wmv", ".flv", ".webm",
    ".mkv", ".mpeg", ".mpg", ".3gp", ".3g2", ".ts", ".mts",
    ".m2ts", ".vob", ".ogv", ".divx", ".rm", ".rmvb", ".asf",
    ".f4v", ".amv", ".drc", ".mxf", ".roq", ".nsv", ".yuv",
    ".bik"}

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif",
    ".webp", ".heif", ".heic", ".raw", ".cr2", ".nef", ".orf",
    ".sr2", ".arw", ".dng", ".ico", ".svg", ".jfif"
}

AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma",
    ".alac", ".aiff", ".ape", ".amr", ".opus", ".ra", ".ac3"
}


class CopyWorker(QObject):
    """
    Copies files / folders with byte-level progress.
    Emits `progress(int)` 0-100, `done()`, `error(str)`.
    """
    progress = Signal(int)
    done     = Signal()
    error    = Signal(str)

    CHUNK = 1 << 20   # 1 MiB

    def __init__(self, sources, targets, verify=False,
        folder_templates=None,
        filename_template="{stem}-{file_day}-{file_month}-{file_year}{ext}",
        custom_tokens=None):
        super().__init__()
        self.sources = [Path(p) for p in sources]
        self.targets = [Path(p) for p in targets]
        self.verify = verify
        self.filename_template = filename_template
        self._index = 1  # running counter
        self.custom_tokens = custom_tokens or {}
        self.folder_templates = folder_templates or {
        "video": "{type}/{file_year}/{month:02d}",
        "audio": "{type}/{file_year}/{month:02d}",
        "photo": "{type}/{file_year}/{month:02d}",
        "other": "misc"
        }


    # ---------- helpers ----------
    @staticmethod
    def _sha256(path):
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _size_of(p: Path):
        if p.is_file():
            return p.stat().st_size
        # walk folder / drive
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    
    def _is_excluded(self, path: Path):
        return path.name.lower() in {
        "system volume information", "$recycle.bin", "recycler",
        "pagefile.sys", "hiberfil.sys"
        }

    def find_media_files(self, root_paths):
            video_files = []
            audio_files = []
            photo_files = []
            for root in root_paths:
                for f in Path(root).rglob("*"):
                    if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS:
                        video_files.append(f)
                    elif f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS:
                        audio_files.append(f)
                    elif f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS:
                        photo_files.append(f)
            return video_files, audio_files, photo_files
    
    def _file_type(self, p: Path) -> str:
        ext = p.suffix.lower()
        if ext in VIDEO_EXTENSIONS:
            return "video"
        elif ext in AUDIO_EXTENSIONS:
            return "audio"
        elif ext in IMAGE_EXTENSIONS:
            return "photo"
        return "other"

    def _render_template(self, p: Path, root: Path) -> Path:
        """Return Path relative to dst_root according to templates."""
        self.tokens = get_base_tokens(p, self._index, file_type=self._file_type(p))
        self.tokens.update(self.custom_tokens)

        folder_template = self.folder_templates.get(self._file_type(p), "misc")
        folder_part = Path(folder_template.format(**self.tokens))
        file_part   = self.filename_template.format(**self.tokens)
        self._index += 1
        return root / folder_part / file_part

    # ---------- main ----------
    def run(self):
        try:
            # Step 1: Collect all media files
            video_files, audio_files, photo_files = self.find_media_files(self.sources)
            all_media = video_files + audio_files + photo_files

            total_bytes = sum(f.stat().st_size for f in all_media) * len(self.targets)
            if not total_bytes:
                self.progress.emit(100)
                self.done.emit()
                return

            copied = 0
            for f in all_media:
                for dst_root in self.targets:
                    target_path = self._render_template(f, dst_root)
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    copied += self._copy_file(f, target_path, total_bytes, copied)

            self.done.emit()

        except Exception as ex:
            self.error.emit(str(ex))

    # ---------- byte-level copy with progress ----------
    def _copy_file(self, src: Path, dst: Path, total_bytes, copied_so_far):
        src_size = src.stat().st_size
        log(f"Copy {src}  →  {dst}  ({src_size/1_048_576:.1f} MiB)")
        with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
            while True:
                buf = fsrc.read(self.CHUNK)
                if not buf:
                    break
                fdst.write(buf)
                copied_so_far += len(buf)
                pct = int(copied_so_far / total_bytes * 100)
                self.progress.emit(pct)

        shutil.copystat(src, dst)  # preserve times/permissions
        if self.verify:
            if self._sha256(src) != self._sha256(dst):
                raise ValueError(f"Checksum mismatch: {src}")
            log(f"✓ Verified {dst}")
        return src_size