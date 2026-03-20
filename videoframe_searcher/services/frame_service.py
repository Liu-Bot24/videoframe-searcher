from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from imageio_ffmpeg import get_ffmpeg_exe

from videoframe_searcher.services.process_manager import ProcessManager


ProgressCallback = Callable[[str], None]


class FrameService:
    def __init__(self, process_manager: ProcessManager) -> None:
        self.process_manager = process_manager

    def extract_frames(
        self,
        video_path: str | Path,
        output_dir: str | Path,
        interval_seconds: int,
        clear_existing: bool,
        progress_callback: ProgressCallback | None = None,
    ) -> int:
        if interval_seconds < 1:
            raise ValueError("抽帧间隔不能小于 1 秒")

        source = Path(video_path)
        if not source.exists():
            raise FileNotFoundError(f"视频文件不存在：{source}")

        target_dir = Path(output_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        if clear_existing:
            for image in target_dir.glob("*"):
                if image.is_file() and image.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
                    image.unlink()

        ffmpeg = get_ffmpeg_exe()
        first_frame = target_dir / "frame_00000.jpg"
        first_frame_cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "info",
            "-y",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(first_frame),
        ]
        output_pattern = target_dir / "frame_%05d.jpg"
        interval_command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "info",
            "-y",
            "-ss",
            str(interval_seconds),
            "-i",
            str(source),
            "-vf",
            f"fps=1/{interval_seconds}",
            "-start_number",
            "1",
            "-q:v",
            "2",
            str(output_pattern),
        ]

        if progress_callback:
            progress_callback("开始抽取首帧（frame_00000.jpg）...")

        self._run_ffmpeg_command(first_frame_cmd, progress_callback)

        if progress_callback:
            progress_callback("开始按间隔抽帧...")

        self._run_ffmpeg_command(interval_command, progress_callback)

        count = len(list(target_dir.glob("frame_*.jpg")))
        if progress_callback:
            progress_callback(f"抽帧完成，共生成 {count} 张图片（含首帧）。")
        return count

    def _run_ffmpeg_command(self, command: list[str], progress_callback: ProgressCallback | None = None) -> None:
        process = self.process_manager.spawn(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        assert process.stdout is not None

        try:
            for line in process.stdout:
                stripped = line.strip()
                if stripped and progress_callback:
                    progress_callback(stripped)
            return_code = process.wait()
        finally:
            self.process_manager.unregister(process)

        if return_code != 0:
            raise RuntimeError("FFmpeg 抽帧失败，请检查日志。")
