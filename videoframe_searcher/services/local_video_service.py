from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Callable

from imageio_ffmpeg import get_ffmpeg_exe

from videoframe_searcher.services.process_manager import ProcessManager


ProgressCallback = Callable[[str], None]
_DURATION_PATTERN = re.compile(r"Duration:\s*(\d{2}):(\d{2}):(\d{2}(?:\.\d+)?)")


class LocalVideoService:
    def __init__(self, process_manager: ProcessManager) -> None:
        self.process_manager = process_manager

    def probe_duration(self, video_path: str | Path, progress_callback: ProgressCallback | None = None) -> float | None:
        path = Path(video_path)
        if not path.exists():
            raise FileNotFoundError(f"视频不存在：{path}")

        ffmpeg = get_ffmpeg_exe()
        if progress_callback:
            progress_callback("正在读取本地视频时长...")

        result = self.process_manager.run(
            [ffmpeg, "-hide_banner", "-i", str(path)],
            timeout=60,
        )
        text = "\n".join([result.stdout.strip(), result.stderr.strip()]).strip()
        match = _DURATION_PATTERN.search(text)
        if not match:
            return None

        hours, minutes, seconds = match.groups()
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)

    def copy_to_project(
        self,
        source_video: str | Path,
        project_dir: str | Path,
        progress_callback: ProgressCallback | None = None,
    ) -> Path:
        source = Path(source_video)
        if not source.exists():
            raise FileNotFoundError(f"视频不存在：{source}")

        suffix = source.suffix if source.suffix else ".mp4"
        project_path = Path(project_dir)
        target = project_path / f"video{suffix.lower()}"
        if target.exists():
            target = project_path / f"video_local_{source.stem}{suffix.lower()}"

        if progress_callback:
            progress_callback("正在导入本地视频...")
        shutil.copy2(source, target)
        return target
