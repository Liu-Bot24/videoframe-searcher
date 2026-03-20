from __future__ import annotations

import importlib.util
import json
import re
import shlex
import subprocess
import sys
from urllib.parse import urlparse
from pathlib import Path
from typing import Any, Callable

import requests

from videoframe_searcher.services.process_manager import ProcessManager


ProgressCallback = Callable[[str], None]
DEFAULT_DOWNLOAD_FORMAT = "bv*+ba/b"
FALLBACK_FORMAT = "bestvideo*+bestaudio/best"
ULTIMATE_FALLBACK_FORMAT = "best"
TWITTER_STATUS_PATTERN = re.compile(r"(?:twitter\.com|x\.com)/(?:[^/]+/status|i/status)/(\d+)")
FXTWITTER_API_TEMPLATE = "https://api.fxtwitter.com/i/status/{tweet_id}"
REQUEST_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)


class DownloadService:
    def __init__(self, process_manager: ProcessManager) -> None:
        self.process_manager = process_manager

    def _base_command(self, settings: dict[str, Any]) -> list[str]:
        command = [sys.executable, "-m", "yt_dlp"]
        proxy = str(settings.get("http_proxy", "")).strip()
        if proxy:
            command.extend(["--proxy", proxy])
        cookie_file = str(settings.get("cookie_file", "")).strip()
        if cookie_file:
            command.extend(["--cookies", cookie_file])
        elif settings.get("use_cookie_auth"):
            browser = str(settings.get("cookie_browser", "chrome")).strip() or "chrome"
            command.extend(["--cookies-from-browser", browser])

        if settings.get("use_impersonate", True) and importlib.util.find_spec("curl_cffi") is not None:
            command.extend(["--impersonate", "chrome"])

        command.extend(
            [
                "--extractor-retries",
                "5",
                "--retries",
                "10",
                "--fragment-retries",
                "10",
                "--concurrent-fragments",
                "4",
            ]
        )
        extra_args = str(settings.get("extra_yt_dlp_args", "")).strip()
        if extra_args:
            command.extend(shlex.split(extra_args))
        return command

    def _proxies(self, settings: dict[str, Any]) -> dict[str, str] | None:
        proxy = str(settings.get("http_proxy", "")).strip()
        if not proxy:
            return None
        return {"http": proxy, "https": proxy}

    def _extract_tweet_id(self, url: str) -> str | None:
        match = TWITTER_STATUS_PATTERN.search(url)
        return match.group(1) if match else None

    def _candidate_urls(self, url: str) -> list[str]:
        urls = [url]
        match = TWITTER_STATUS_PATTERN.search(url)
        if match:
            tweet_id = match.group(1)
            candidates = [
                f"https://x.com/i/status/{tweet_id}",
                f"https://twitter.com/i/status/{tweet_id}",
                f"https://mobile.twitter.com/i/status/{tweet_id}",
            ]
            for normalized in candidates:
                if normalized not in urls:
                    urls.append(normalized)
        return urls

    def _fetch_fxtwitter_payload(self, tweet_id: str, settings: dict[str, Any]) -> dict[str, Any]:
        endpoint = FXTWITTER_API_TEMPLATE.format(tweet_id=tweet_id)
        response = requests.get(
            endpoint,
            headers={"User-Agent": REQUEST_UA},
            proxies=self._proxies(settings),
            timeout=45,
        )
        response.raise_for_status()
        payload = response.json()
        if int(payload.get("code", 500)) != 200:
            raise RuntimeError(f"FixTweet API 返回异常：{payload.get('message', 'UNKNOWN')}")
        return payload

    def _pick_fxtwitter_video(self, payload: dict[str, Any]) -> dict[str, Any]:
        media = payload.get("tweet", {}).get("media", {}) or {}
        videos = media.get("videos") or []
        if not videos:
            raise RuntimeError("FixTweet 返回的数据中未包含视频。")

        candidates: list[dict[str, Any]] = []
        for item in videos:
            url = str(item.get("url") or "").strip()
            if url:
                candidates.append(
                    {
                        "url": url,
                        "width": int(item.get("width") or 0),
                        "height": int(item.get("height") or 0),
                        "duration": item.get("duration"),
                    }
                )
            for variant in item.get("variants") or []:
                variant_url = str(variant.get("url") or "").strip()
                if not variant_url:
                    continue
                candidates.append(
                    {
                        "url": variant_url,
                        "width": int(variant.get("width") or item.get("width") or 0),
                        "height": int(variant.get("height") or item.get("height") or 0),
                        "duration": variant.get("duration") or item.get("duration"),
                    }
                )

        if not candidates:
            raise RuntimeError("FixTweet 视频候选为空。")

        limited = [c for c in candidates if 0 < c["height"] <= 1080]
        if limited:
            limited.sort(key=lambda x: (x["height"], x["width"]), reverse=True)
            return limited[0]

        candidates.sort(key=lambda x: (x["height"], x["width"]), reverse=True)
        best = candidates[0]
        return best

    def _build_fxtwitter_metadata(self, payload: dict[str, Any], picked_video: dict[str, Any]) -> dict[str, Any]:
        tweet = payload.get("tweet", {}) or {}
        author = tweet.get("author", {}) or {}
        text = str(tweet.get("text") or "").strip()
        title = text.replace("\n", " ").strip()
        if not title:
            title = str(tweet.get("id") or "twitter_video")
        if len(title) > 80:
            title = title[:80]
        return {
            "title": title,
            "duration": picked_video.get("duration"),
            "is_live": False,
            "uploader": author.get("screen_name") or author.get("name"),
            "source": "fxtwitter_fallback",
        }

    def _download_direct_video(
        self,
        media_url: str,
        project_path: Path,
        settings: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> Path:
        parsed = urlparse(media_url)
        suffix = Path(parsed.path).suffix or ".mp4"
        target = project_path / f"video{suffix}"
        session = requests.Session()
        response = session.get(
            media_url,
            headers={"User-Agent": REQUEST_UA},
            proxies=self._proxies(settings),
            stream=True,
            timeout=120,
        )
        response.raise_for_status()
        total = int(response.headers.get("Content-Length", "0") or "0")
        downloaded = 0
        with target.open("wb") as file:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if not chunk:
                    continue
                file.write(chunk)
                downloaded += len(chunk)
                if progress_callback and total > 0:
                    progress_callback(f"备用下载进度：{downloaded * 100 // total}%")
        if progress_callback:
            progress_callback(f"备用下载完成：{target.name}")
        return target

    def fetch_twitter_fallback_metadata(
        self, url: str, settings: dict[str, Any], progress_callback: ProgressCallback | None = None
    ) -> dict[str, Any]:
        tweet_id = self._extract_tweet_id(url)
        if not tweet_id:
            raise RuntimeError("当前 URL 不是 X/Twitter 推文链接，无法使用备用解析。")
        if progress_callback:
            progress_callback("尝试使用 FixTweet API 解析推文视频...")
        payload = self._fetch_fxtwitter_payload(tweet_id, settings)
        picked = self._pick_fxtwitter_video(payload)
        metadata = self._build_fxtwitter_metadata(payload, picked)
        metadata["_fallback_media_url"] = picked["url"]
        return metadata

    def download_twitter_fallback(
        self,
        url: str,
        project_dir: str | Path,
        settings: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> Path:
        tweet_id = self._extract_tweet_id(url)
        if not tweet_id:
            raise RuntimeError("当前 URL 不是 X/Twitter 推文链接，无法使用备用下载。")
        if progress_callback:
            progress_callback("yt-dlp 下载失败，尝试 FixTweet 备用下载...")
        payload = self._fetch_fxtwitter_payload(tweet_id, settings)
        picked = self._pick_fxtwitter_video(payload)
        return self._download_direct_video(picked["url"], Path(project_dir), settings, progress_callback)

    def _friendly_error(self, error_text: str, url: str, settings: dict[str, Any]) -> str:
        lowered = error_text.lower()
        if "no video could be found in this tweet" in lowered:
            hint = (
                "未从该 X/Twitter 链接解析到视频流。请确认链接本身包含视频，"
                "若为受限内容请在设置中开启 Cookie 授权后重试。"
            )
            if settings.get("cookie_file"):
                hint += " 若已配置 Cookie 文件，请确认该文件未过期。"
            if settings.get("use_cookie_auth"):
                hint += " 若已开启 Cookie，请先关闭对应浏览器再重试。"
            return f"{hint}\n原始错误：{error_text}"
        if "could not copy" in lowered and "cookie" in lowered:
            return (
                "无法读取浏览器 Cookie。请关闭对应浏览器后重试，或切换其他浏览器。\n"
                f"原始错误：{error_text}"
            )
        if "requested format is not available" in lowered:
            return (
                "目标视频没有匹配的下载格式，已尝试多种兼容格式仍失败。\n"
                f"原始错误：{error_text}"
            )
        return error_text

    def fetch_metadata(self, url: str, settings: dict[str, Any], progress_callback: ProgressCallback | None = None) -> dict[str, Any]:
        if progress_callback:
            progress_callback("开始解析视频元数据...")
        errors: list[str] = []
        tweet_id = self._extract_tweet_id(url)
        candidates = self._candidate_urls(url)
        if tweet_id:
            candidates = candidates[:1]

        for candidate in candidates:
            command = self._base_command(settings) + [
                "--dump-single-json",
                "--no-download",
                "--no-playlist",
                candidate,
            ]
            result = self.process_manager.run(command, timeout=120)
            if result.returncode == 0:
                try:
                    metadata = json.loads(result.stdout.strip())
                except json.JSONDecodeError:
                    errors.append("无法解析 yt-dlp 返回的元数据 JSON")
                    continue
                if progress_callback:
                    progress_callback("视频元数据解析完成。")
                return metadata
            errors.append((result.stderr.strip() or result.stdout.strip() or "解析视频元数据失败").strip())

        merged_error = "\n".join(dict.fromkeys([e for e in errors if e]))
        if tweet_id:
            try:
                return self.fetch_twitter_fallback_metadata(url, settings, progress_callback)
            except Exception as fallback_error:
                merged_error = f"{merged_error}\nFixTweet 备用解析失败：{fallback_error}".strip()

        raise RuntimeError(self._friendly_error(merged_error or "解析视频元数据失败", url, settings))

    def download_video(
        self,
        url: str,
        project_dir: str | Path,
        settings: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> Path:
        project_path = Path(project_dir)
        output_template = project_path / "video.%(ext)s"
        if progress_callback:
            progress_callback("开始下载视频（完整音视频，最佳可用画质）...")

        all_errors: list[str] = []
        tweet_id = self._extract_tweet_id(url)
        target_urls = self._candidate_urls(url)
        if tweet_id:
            target_urls = target_urls[:1]

        for target_url in target_urls:
            custom_format = str(settings.get("download_format", "")).strip()
            format_candidates = [
                fmt
                for fmt in [custom_format or DEFAULT_DOWNLOAD_FORMAT, FALLBACK_FORMAT, ULTIMATE_FALLBACK_FORMAT]
                if fmt
            ]
            # 去重并保持顺序
            format_candidates = list(dict.fromkeys(format_candidates))
            for format_value in format_candidates:
                command = self._base_command(settings) + [
                    "--no-playlist",
                    "--no-part",
                    "--newline",
                ]
                if format_value:
                    command.extend(["-f", format_value])
                merge_output = str(settings.get("merge_output_format", "mp4")).strip()
                if merge_output:
                    command.extend(["--merge-output-format", merge_output])
                command.extend(["-o", str(output_template), target_url])

                process = self.process_manager.spawn(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
                assert process.stdout is not None

                output_lines: list[str] = []
                try:
                    for line in process.stdout:
                        stripped = line.strip()
                        if stripped and progress_callback:
                            progress_callback(stripped)
                        if stripped:
                            output_lines.append(stripped)
                    return_code = process.wait()
                finally:
                    self.process_manager.unregister(process)

                if return_code == 0:
                    candidates = sorted(project_path.glob("video.*"), key=lambda p: p.stat().st_mtime, reverse=True)
                    if not candidates:
                        raise RuntimeError("下载成功但未找到输出视频文件。")
                    if progress_callback:
                        progress_callback(f"视频下载完成：{candidates[0].name}")
                    return candidates[0]

                combined = "\n".join(output_lines[-40:])
                all_errors.append(
                    f"[url={target_url}][format={format_value or 'default'}] {combined or '视频下载失败，请查看日志输出。'}"
                )

        merged_error = "\n".join(dict.fromkeys([e for e in all_errors if e]))
        if tweet_id:
            try:
                return self.download_twitter_fallback(url, project_dir, settings, progress_callback)
            except Exception as fallback_error:
                merged_error = f"{merged_error}\nFixTweet 备用下载失败：{fallback_error}".strip()
        raise RuntimeError(self._friendly_error(merged_error or "视频下载失败，请查看日志输出。", url, settings))

    def update_ytdlp(self, progress_callback: ProgressCallback | None = None) -> str:
        if progress_callback:
            progress_callback("尝试执行 yt-dlp -U...")

        upgrade_cmd = [sys.executable, "-m", "yt_dlp", "-U"]
        result = self.process_manager.run(upgrade_cmd, timeout=180)
        output = "\n".join([result.stdout.strip(), result.stderr.strip()]).strip()

        if result.returncode == 0:
            return output or "yt-dlp 已是最新版本。"

        if progress_callback:
            progress_callback("yt-dlp -U 未成功，尝试使用 pip 强制升级...")

        pip_cmd = [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"]
        pip_result = self.process_manager.run(pip_cmd, timeout=240)
        pip_output = "\n".join([pip_result.stdout.strip(), pip_result.stderr.strip()]).strip()
        if pip_result.returncode != 0:
            raise RuntimeError(pip_output or "yt-dlp 强制升级失败")
        return pip_output or "yt-dlp 升级完成。"
