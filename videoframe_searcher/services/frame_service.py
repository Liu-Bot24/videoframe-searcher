from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Callable

from imageio_ffmpeg import get_ffmpeg_exe

from videoframe_searcher.services.process_manager import ProcessManager


ProgressCallback = Callable[[str], None]
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
INDEX_PATTERN = re.compile(r"^(?P<prefix>[a-zA-Z0-9_]+)_(?P<index>\d+)(?:_[a-zA-Z0-9\-]+)?\.jpg$", re.IGNORECASE)


class FrameService:
    def __init__(self, process_manager: ProcessManager) -> None:
        self.process_manager = process_manager

    def extract_frames(
        self,
        video_path: str | Path,
        output_dir: str | Path,
        interval_seconds: int,
        clear_existing: bool,
        manual_timestamps: list[float] | None = None,
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
            self._clear_existing_images(target_dir)

        ffmpeg = get_ffmpeg_exe()
        first_frame = target_dir / self._build_frame_filename("frame", 0, 0)
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
            progress_callback(f"开始抽取首帧（{first_frame.name}）...")

        self._run_ffmpeg_command(first_frame_cmd, progress_callback)

        if progress_callback:
            progress_callback("开始按间隔抽帧...")

        self._run_ffmpeg_command(interval_command, progress_callback)
        self._append_interval_timestamps(target_dir, interval_seconds)

        manual_points = self._normalize_timestamps(manual_timestamps)
        if manual_points:
            if progress_callback:
                progress_callback(f"开始抽取手动打点截图，共 {len(manual_points)} 个时间点...")
            self.extract_manual_frames(
                video_path=source,
                output_dir=target_dir,
                timestamps_seconds=manual_points,
                clear_existing=False,
                progress_callback=progress_callback,
            )

        count = len(list(target_dir.glob("frame_*.jpg")))
        count += len(list(target_dir.glob("manual_*.jpg")))
        if progress_callback:
            progress_callback(f"抽帧完成，共生成 {count} 张图片（含首帧）。")
        return count

    def probe_frame_rate(self, video_path: str | Path) -> float | None:
        source = Path(video_path)
        if not source.exists():
            return None
        ffprobe = self._ffprobe_executable()
        if ffprobe is None:
            return None

        command = [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=avg_frame_rate,r_frame_rate",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(source),
        ]
        result = self.process_manager.run(command, timeout=30)
        if result.returncode != 0:
            return None

        for line in result.stdout.splitlines():
            value = self._ratio_to_float(line.strip())
            if value and value > 0:
                return value
        return None

    def capture_frame(
        self,
        video_path: str | Path,
        output_dir: str | Path,
        timestamp_seconds: float,
        clear_existing: bool = False,
        prefix: str = "manual",
        progress_callback: ProgressCallback | None = None,
    ) -> Path:
        source = Path(video_path)
        if not source.exists():
            raise FileNotFoundError(f"视频文件不存在：{source}")
        target_dir = Path(output_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        if clear_existing:
            self._clear_existing_images(target_dir)

        index = self._next_index(target_dir, prefix)
        target = target_dir / self._build_frame_filename(prefix, index, timestamp_seconds)
        self._capture_timestamp_frame(
            source,
            target,
            timestamp_seconds=max(0.0, float(timestamp_seconds)),
            progress_callback=progress_callback,
        )
        return target

    def extract_manual_frames(
        self,
        video_path: str | Path,
        output_dir: str | Path,
        timestamps_seconds: list[float],
        clear_existing: bool,
        progress_callback: ProgressCallback | None = None,
        prefix: str = "manual",
    ) -> int:
        source = Path(video_path)
        if not source.exists():
            raise FileNotFoundError(f"视频文件不存在：{source}")
        points = self._normalize_timestamps(timestamps_seconds)
        if not points:
            raise ValueError("未提供有效打点时间。")

        target_dir = Path(output_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        if clear_existing:
            self._clear_existing_images(target_dir)

        index = self._next_index(target_dir, prefix)
        for point in points:
            target = target_dir / self._build_frame_filename(prefix, index, point)
            self._capture_timestamp_frame(source, target, point, progress_callback)
            index += 1

        count = len(points)
        if progress_callback:
            progress_callback(f"手动打点截图完成，共生成 {count} 张。")
        return count

    def _capture_timestamp_frame(
        self,
        source: Path,
        target: Path,
        timestamp_seconds: float,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        ffmpeg = get_ffmpeg_exe()
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "info",
            "-y",
            "-ss",
            f"{timestamp_seconds:.3f}",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(target),
        ]
        if progress_callback:
            progress_callback(f"截图时间点 {timestamp_seconds:.3f}s -> {target.name}")
        self._run_ffmpeg_command(command, progress_callback)

    def _normalize_timestamps(self, timestamps: list[float] | None) -> list[float]:
        if not timestamps:
            return []
        normalized = sorted({round(max(0.0, float(value)), 3) for value in timestamps})
        return normalized

    def _timestamp_token(self, timestamp_seconds: float) -> str:
        total_seconds = max(0, int(round(float(timestamp_seconds))))
        hour, rem = divmod(total_seconds, 3600)
        minute, second = divmod(rem, 60)
        return f"t{hour:02d}-{minute:02d}-{second:02d}"

    def _build_frame_filename(self, prefix: str, index: int, timestamp_seconds: float) -> str:
        return f"{prefix}_{index:05d}_{self._timestamp_token(timestamp_seconds)}.jpg"

    def _append_interval_timestamps(self, target_dir: Path, interval_seconds: int) -> None:
        raw_pattern = re.compile(r"^frame_(?P<index>\d+)\.jpg$", re.IGNORECASE)
        for image in sorted(target_dir.glob("frame_*.jpg")):
            match = raw_pattern.match(image.name)
            if not match:
                continue
            index = int(match.group("index"))
            renamed = target_dir / self._build_frame_filename("frame", index, index * interval_seconds)
            if renamed.exists():
                renamed.unlink()
            image.rename(renamed)

    def _clear_existing_images(self, target_dir: Path) -> None:
        for image in target_dir.glob("*"):
            if image.is_file() and image.suffix.lower() in IMAGE_SUFFIXES:
                image.unlink()

    def _next_index(self, target_dir: Path, prefix: str) -> int:
        max_index = 0
        for image in target_dir.glob(f"{prefix}_*.jpg"):
            match = INDEX_PATTERN.match(image.name)
            if not match:
                continue
            if match.group("prefix").lower() != prefix.lower():
                continue
            max_index = max(max_index, int(match.group("index")))
        return max_index + 1

    def _ffprobe_executable(self) -> str | None:
        ffmpeg = Path(get_ffmpeg_exe())
        candidates = []
        if ffmpeg.name.lower().startswith("ffmpeg"):
            candidates.append(ffmpeg.with_name(ffmpeg.name.replace("ffmpeg", "ffprobe", 1)))
        candidates.append(ffmpeg.with_name("ffprobe.exe"))
        candidates.append(ffmpeg.with_name("ffprobe"))
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        return None

    def _ratio_to_float(self, text: str) -> float | None:
        value = text.strip()
        if not value:
            return None
        if "/" in value:
            numerator, denominator = value.split("/", 1)
            try:
                num = float(numerator)
                den = float(denominator)
            except ValueError:
                return None
            if den == 0:
                return None
            return num / den
        try:
            return float(value)
        except ValueError:
            return None

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
