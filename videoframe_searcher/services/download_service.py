from __future__ import annotations

import importlib.util
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlparse

import requests
try:
    import unalix
except Exception:  # pragma: no cover - optional dependency
    unalix = None

from videoframe_searcher.services.process_manager import ProcessManager


ProgressCallback = Callable[[str], None]
DEFAULT_DOWNLOAD_FORMAT = "bv*+ba/b"
FALLBACK_FORMAT = "bestvideo*+bestaudio/best"
ULTIMATE_FALLBACK_FORMAT = "best"
QUALITY_HEIGHT_CHOICES = {"360", "480", "720", "1080", "1440", "2160"}
TWITTER_STATUS_PATTERN = re.compile(r"(?:twitter\.com|x\.com)/(?:[^/]+/status|i/status)/(\d+)")
FXTWITTER_API_TEMPLATE = "https://api.fxtwitter.com/i/status/{tweet_id}"
REQUEST_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)
TRACKING_PARAM_PREFIXES = (
    "utm_",
    "spm",
    "igsh",
    "mc_",
)
TRACKING_PARAM_KEYS = {
    "from",
    "source",
    "vd_source",
    "feature",
    "fbclid",
    "gclid",
    "msclkid",
    "ref",
    "ref_src",
    "tracking_id",
    "session_id",
}
CORE_QUERY_KEYS = {
    "v",
    "id",
    "vid",
    "aid",
    "bvid",
    "cid",
    "p",
    "page",
    "ep",
    "episode",
    "list",
    "index",
    "t",
    "start",
    "end",
    "time_continue",
    "h",
    "token",
    "key",
}


class DownloadService:
    def __init__(self, process_manager: ProcessManager) -> None:
        self.process_manager = process_manager

    def normalize_url(self, url: str) -> str:
        text = str(url or "").strip()
        if not text:
            return text
        parsed = urlparse(text)
        if parsed.scheme:
            return text
        if text.startswith("//"):
            return f"https:{text}"
        first = text.split("/")[0]
        if "." in first:
            return f"https://{text}"
        return text

    def _preferred_height(self, settings: dict[str, Any]) -> str | None:
        value = str(settings.get("preferred_quality", "auto")).strip().lower()
        if not value or value == "auto":
            return None
        value = value.removesuffix("p")
        if value in QUALITY_HEIGHT_CHOICES:
            return value
        return None

    def _quality_format_candidates(self, settings: dict[str, Any]) -> list[str]:
        preferred_height = self._preferred_height(settings)
        if not preferred_height:
            return []
        return [
            f"bv*[height<={preferred_height}]+ba/b[height<={preferred_height}]",
            f"bestvideo*[height<={preferred_height}]+bestaudio/best[height<={preferred_height}]",
        ]

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

    def _strategy_settings(self, settings: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        base = dict(settings)
        variants: list[tuple[str, dict[str, Any]]] = [("默认策略", base)]

        has_cookie = bool(str(base.get("cookie_file", "")).strip()) or bool(base.get("use_cookie_auth", False))
        has_impersonate = bool(base.get("use_impersonate", True))

        if has_cookie:
            no_cookie = dict(base)
            no_cookie["use_cookie_auth"] = False
            no_cookie["cookie_file"] = ""
            variants.append(("回退:禁用Cookie", no_cookie))

        if has_impersonate:
            no_impersonate = dict(base)
            no_impersonate["use_impersonate"] = False
            variants.append(("回退:禁用伪装", no_impersonate))

        if has_cookie or has_impersonate:
            compat = dict(base)
            compat["use_cookie_auth"] = False
            compat["cookie_file"] = ""
            compat["use_impersonate"] = False
            variants.append(("回退:兼容模式", compat))

        deduped: list[tuple[str, dict[str, Any]]] = []
        seen: set[tuple[Any, ...]] = set()
        for name, variant in variants:
            key = (
                bool(variant.get("use_cookie_auth")),
                str(variant.get("cookie_file", "")).strip(),
                bool(variant.get("use_impersonate", True)),
                str(variant.get("cookie_browser", "")).strip(),
                str(variant.get("http_proxy", "")).strip(),
                str(variant.get("extra_yt_dlp_args", "")).strip(),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append((name, variant))
        return deduped

    def _normalize_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        result = dict(metadata)
        entry = None
        entries = result.get("entries")
        if isinstance(entries, list):
            for item in entries:
                if isinstance(item, dict):
                    entry = item
                    break

        if entry:
            if not result.get("title") and entry.get("title"):
                result["title"] = entry.get("title")
            if result.get("duration") in (None, "", 0) and entry.get("duration"):
                result["duration"] = entry.get("duration")
            if not result.get("is_live") and entry.get("is_live") is not None:
                result["is_live"] = bool(entry.get("is_live"))

        thumb_url = str(result.get("thumbnail_url") or result.get("thumbnail") or "").strip()
        if not thumb_url:
            thumbnails = result.get("thumbnails")
            if isinstance(thumbnails, list):
                for item in reversed(thumbnails):
                    if not isinstance(item, dict):
                        continue
                    url = str(item.get("url") or "").strip()
                    if url:
                        thumb_url = url
                        break
        if not thumb_url and entry:
            thumb_url = str(entry.get("thumbnail_url") or entry.get("thumbnail") or "").strip()
            if not thumb_url:
                entry_thumbnails = entry.get("thumbnails")
                if isinstance(entry_thumbnails, list):
                    for item in reversed(entry_thumbnails):
                        if not isinstance(item, dict):
                            continue
                        url = str(item.get("url") or "").strip()
                        if url:
                            thumb_url = url
                            break
        if thumb_url.startswith("//"):
            thumb_url = f"https:{thumb_url}"
        if thumb_url:
            result["thumbnail_url"] = thumb_url
            result.setdefault("thumbnail", thumb_url)
        return result

    def _proxies(self, settings: dict[str, Any]) -> dict[str, str] | None:
        proxy = str(settings.get("http_proxy", "")).strip()
        if not proxy:
            return None
        return {"http": proxy, "https": proxy}

    def _extract_tweet_id(self, url: str) -> str | None:
        match = TWITTER_STATUS_PATTERN.search(url)
        return match.group(1) if match else None

    def _is_tracking_query_key(self, key: str) -> bool:
        lowered = key.lower()
        if lowered in TRACKING_PARAM_KEYS:
            return True
        return any(lowered.startswith(prefix) for prefix in TRACKING_PARAM_PREFIXES)

    def _is_core_query_key(self, key: str) -> bool:
        return key.lower() in CORE_QUERY_KEYS

    def _clear_tracking_with_unalix(self, url: str) -> str:
        if unalix is None:
            return url
        try:
            cleaned = str(unalix.clear_url(url=url) or "").strip()
        except Exception:
            return url
        return cleaned or url

    def _build_url(self, parsed, *, query_items: list[tuple[str, str]] | None = None, drop_fragment: bool = True) -> str:
        query = parsed.query
        if query_items is not None:
            query = urlencode(query_items, doseq=True)
        fragment = "" if drop_fragment else parsed.fragment
        return parsed._replace(query=query, fragment=fragment).geturl()

    def _host_variant(self, parsed):
        hostname = parsed.hostname or ""
        if not hostname:
            return None

        alt_hostname = ""
        if hostname.startswith("www."):
            alt_hostname = hostname[4:]
        elif hostname.count(".") == 1:
            alt_hostname = f"www.{hostname}"
        if not alt_hostname:
            return None

        userinfo = ""
        if parsed.username:
            userinfo = parsed.username
            if parsed.password:
                userinfo += f":{parsed.password}"
            userinfo += "@"
        port_part = f":{parsed.port}" if parsed.port else ""
        return parsed._replace(netloc=f"{userinfo}{alt_hostname}{port_part}")

    def _parsed_variants(self, url: str) -> list[Any]:
        parsed = urlparse(url)
        variants = [parsed]

        if parsed.path and parsed.path not in {"/"} and parsed.path.endswith("/"):
            variants.append(parsed._replace(path=parsed.path.rstrip("/")))

        all_variants = list(variants)
        for candidate in variants:
            host_swapped = self._host_variant(candidate)
            if host_swapped is not None:
                all_variants.append(host_swapped)

        deduped: list[Any] = []
        seen: set[tuple[str, str, str, str, str, str]] = set()
        for candidate in all_variants:
            key = (
                candidate.scheme,
                candidate.netloc,
                candidate.path,
                candidate.params,
                candidate.query,
                candidate.fragment,
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
        return deduped

    def _generic_candidate_urls(self, url: str) -> list[str]:
        candidates: list[str] = []
        seed_urls = [url]
        unalix_cleaned = self._clear_tracking_with_unalix(url)
        if unalix_cleaned != url:
            seed_urls.insert(0, unalix_cleaned)

        parsed_variants: list[Any] = []
        for seed in seed_urls:
            parsed_variants.extend(self._parsed_variants(seed))

        dedup_parsed: list[Any] = []
        seen_parsed: set[tuple[str, str, str, str, str, str]] = set()
        for parsed in parsed_variants:
            key = (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                parsed.query,
                parsed.fragment,
            )
            if key in seen_parsed:
                continue
            seen_parsed.add(key)
            dedup_parsed.append(parsed)

        for parsed in dedup_parsed:
            query_items = parse_qsl(parsed.query, keep_blank_values=False)

            candidates.append(self._build_url(parsed, drop_fragment=True))

            if query_items:
                without_tracking = [(k, v) for k, v in query_items if not self._is_tracking_query_key(k)]
                if without_tracking and len(without_tracking) != len(query_items):
                    candidates.append(self._build_url(parsed, query_items=without_tracking, drop_fragment=True))

                core_only = [(k, v) for k, v in query_items if self._is_core_query_key(k)]
                if core_only:
                    candidates.append(self._build_url(parsed, query_items=core_only, drop_fragment=True))

                candidates.append(self._build_url(parsed, query_items=[], drop_fragment=True))
            elif parsed.fragment:
                candidates.append(self._build_url(parsed, drop_fragment=True))

        deduped: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            text = candidate.strip()
            if not text or text in seen:
                continue
            seen.add(text)
            deduped.append(text)
        return deduped

    def _candidate_urls(self, url: str) -> list[str]:
        urls: list[str] = self._generic_candidate_urls(url)
        if not urls:
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
        media = tweet.get("media", {}) or {}
        text = str(tweet.get("text") or "").strip()
        title = text.replace("\n", " ").strip()
        if not title:
            title = str(tweet.get("id") or "twitter_video")
        if len(title) > 80:
            title = title[:80]

        thumb_url = ""
        thumbnail_candidates: list[str] = []
        for candidate in [
            media.get("thumbnail_url"),
            media.get("poster"),
            tweet.get("thumbnail_url"),
            tweet.get("photo"),
            tweet.get("image"),
        ]:
            if candidate:
                thumbnail_candidates.append(str(candidate))
        for photo in media.get("photos") or []:
            if not isinstance(photo, dict):
                continue
            for key in ["url", "media_url", "media_url_https"]:
                value = photo.get(key)
                if value:
                    thumbnail_candidates.append(str(value))
        for video in media.get("videos") or []:
            if not isinstance(video, dict):
                continue
            for key in ["thumbnail_url", "poster", "image", "cover_url"]:
                value = video.get(key)
                if value:
                    thumbnail_candidates.append(str(value))
        for candidate in thumbnail_candidates:
            text_candidate = candidate.strip()
            if not text_candidate:
                continue
            if text_candidate.startswith("//"):
                text_candidate = f"https:{text_candidate}"
            thumb_url = text_candidate
            break

        return {
            "title": title,
            "duration": picked_video.get("duration"),
            "is_live": False,
            "uploader": author.get("screen_name") or author.get("name"),
            "source": "fxtwitter_fallback",
            "thumbnail_url": thumb_url,
            "thumbnail": thumb_url,
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
        normalized_url = self.normalize_url(url)
        tweet_id = self._extract_tweet_id(normalized_url)
        if not tweet_id:
            raise RuntimeError("当前 URL 不是 X/Twitter 推文链接，无法使用备用解析。")
        if progress_callback:
            progress_callback("尝试使用 FixTweet API 解析推文视频...")
        payload = self._fetch_fxtwitter_payload(tweet_id, settings)
        picked = self._pick_fxtwitter_video(payload)
        metadata = self._normalize_metadata(self._build_fxtwitter_metadata(payload, picked))
        metadata["_fallback_media_url"] = picked["url"]
        return metadata

    def download_twitter_fallback(
        self,
        url: str,
        project_dir: str | Path,
        settings: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> Path:
        normalized_url = self.normalize_url(url)
        tweet_id = self._extract_tweet_id(normalized_url)
        if not tweet_id:
            raise RuntimeError("当前 URL 不是 X/Twitter 推文链接，无法使用备用下载。")
        if progress_callback:
            progress_callback("yt-dlp 下载失败，尝试 FixTweet 备用下载...")
        payload = self._fetch_fxtwitter_payload(tweet_id, settings)
        picked = self._pick_fxtwitter_video(payload)
        return self._download_direct_video(picked["url"], Path(project_dir), settings, progress_callback)

    def _friendly_error(self, error_text: str, url: str, settings: dict[str, Any]) -> str:
        lowered = error_text.lower()
        if "http error 403" in lowered:
            return (
                "目标站点返回 403（访问受限）。已自动尝试多组 URL 候选与兼容策略仍失败。\n"
                "请重试标准链接、检查代理/网络策略，或提供有效 Cookie 文件。\n"
                f"原始错误：{error_text}"
            )
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
                "无法读取浏览器 Cookie。已自动尝试兼容模式（禁用 Cookie/伪装）仍失败；"
                "请关闭对应浏览器后重试，或改用 Cookie 文件。\n"
                f"原始错误：{error_text}"
            )
        if "requested format is not available" in lowered:
            return (
                "目标视频没有匹配的下载格式，已尝试多种兼容格式仍失败。\n"
                f"原始错误：{error_text}"
            )
        return error_text

    def fetch_metadata(self, url: str, settings: dict[str, Any], progress_callback: ProgressCallback | None = None) -> dict[str, Any]:
        normalized_url = self.normalize_url(url)
        if progress_callback:
            progress_callback("开始解析视频元数据...")
        errors: list[str] = []
        tweet_id = self._extract_tweet_id(normalized_url)
        candidates = self._candidate_urls(normalized_url)
        strategies = self._strategy_settings(settings)
        total_attempts = len(candidates) * len(strategies) * 2
        attempt = 0

        for candidate in candidates:
            for strategy_name, strategy_settings in strategies:
                for use_generic_extractor in (False, True):
                    attempt += 1
                    strategy_label = strategy_name
                    if use_generic_extractor:
                        strategy_label = f"{strategy_name}/通用提取器"

                    if progress_callback and total_attempts > 1:
                        progress_callback(f"元数据解析尝试 ({attempt}/{total_attempts})：{strategy_label}")

                    command = self._base_command(strategy_settings) + [
                        "--dump-single-json",
                        "--no-download",
                        "--no-playlist",
                    ]
                    if use_generic_extractor:
                        command.extend(["--ies", "generic,default"])
                    command.append(candidate)

                    result = self.process_manager.run(command, timeout=120)
                    if result.returncode == 0:
                        try:
                            metadata = json.loads(result.stdout.strip())
                        except json.JSONDecodeError:
                            errors.append(f"[strategy={strategy_label}] 无法解析 yt-dlp 返回的元数据 JSON")
                            continue
                        if progress_callback:
                            progress_callback("视频元数据解析完成。")
                        return self._normalize_metadata(metadata)

                    error_text = (result.stderr.strip() or result.stdout.strip() or "解析视频元数据失败").strip()
                    errors.append(f"[url={candidate}][strategy={strategy_label}] {error_text}")

        merged_error = "\n".join(dict.fromkeys([e for e in errors if e]))
        if tweet_id:
            try:
                return self.fetch_twitter_fallback_metadata(normalized_url, settings, progress_callback)
            except Exception as fallback_error:
                merged_error = f"{merged_error}\nFixTweet 备用解析失败：{fallback_error}".strip()

        raise RuntimeError(self._friendly_error(merged_error or "解析视频元数据失败", normalized_url, settings))

    def download_video(
        self,
        url: str,
        project_dir: str | Path,
        settings: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> Path:
        normalized_url = self.normalize_url(url)
        project_path = Path(project_dir)
        output_template = project_path / "video.%(ext)s"
        preferred_height = self._preferred_height(settings)
        if progress_callback:
            if preferred_height:
                progress_callback(f"开始下载...（画质优先 <= {preferred_height}p）")
            else:
                progress_callback("开始下载...")

        all_errors: list[str] = []
        tweet_id = self._extract_tweet_id(normalized_url)
        target_urls = self._candidate_urls(normalized_url)
        strategy_variants = self._strategy_settings(settings)
        for target_url in target_urls:
            custom_format = str(settings.get("download_format", "")).strip()
            quality_formats = self._quality_format_candidates(settings)
            has_custom_override = bool(custom_format) and custom_format != DEFAULT_DOWNLOAD_FORMAT
            primary_formats = [custom_format] if has_custom_override else [*quality_formats, custom_format or DEFAULT_DOWNLOAD_FORMAT]
            format_candidates = [
                fmt
                for fmt in [*primary_formats, FALLBACK_FORMAT, ULTIMATE_FALLBACK_FORMAT]
                if fmt
            ]
            # 去重并保持顺序
            format_candidates = list(dict.fromkeys(format_candidates))
            for strategy_name, strategy_settings in strategy_variants:
                for format_value in format_candidates:
                    use_generic_extractor = format_value == ULTIMATE_FALLBACK_FORMAT
                    strategy_label = strategy_name
                    if use_generic_extractor:
                        strategy_label = f"{strategy_name}/通用提取器"

                    command = self._base_command(strategy_settings) + [
                        "--no-playlist",
                        "--no-part",
                        "--newline",
                    ]
                    if use_generic_extractor:
                        command.extend(["--ies", "generic,default"])
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
                        f"[url={target_url}][strategy={strategy_label}][format={format_value or 'default'}] "
                        f"{combined or '视频下载失败，请查看日志输出。'}"
                    )

        merged_error = "\n".join(dict.fromkeys([e for e in all_errors if e]))
        if tweet_id:
            try:
                return self.download_twitter_fallback(normalized_url, project_dir, settings, progress_callback)
            except Exception as fallback_error:
                merged_error = f"{merged_error}\nFixTweet 备用下载失败：{fallback_error}".strip()
        raise RuntimeError(self._friendly_error(merged_error or "视频下载失败，请查看日志输出。", normalized_url, settings))

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
