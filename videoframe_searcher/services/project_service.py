from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

import requests


INVALID_PATH_CHARS = re.compile(r"[\\/:*?\"<>|]+")
MAX_TITLE_LENGTH = 20
MAX_FILENAME_LENGTH = 80
THUMBNAIL_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
REQUEST_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)


def sanitize_title(title: str) -> str:
    cleaned = INVALID_PATH_CHARS.sub("_", title).strip()
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = cleaned[:MAX_TITLE_LENGTH]
    return cleaned or "untitled"


def sanitize_filename(title: str) -> str:
    cleaned = INVALID_PATH_CHARS.sub("_", str(title or "")).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned[:MAX_FILENAME_LENGTH].strip(" ._")
    return cleaned or "video"


class ProjectService:
    def __init__(self, workspace_root: str | Path) -> None:
        self.workspace_root = Path(workspace_root)
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    def set_workspace_root(self, workspace_root: str | Path) -> None:
        self.workspace_root = Path(workspace_root)
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    def _normalize_thumbnail_url(self, thumbnail_url: str) -> str:
        text = str(thumbnail_url or "").strip()
        if text.startswith("//"):
            return f"https:{text}"
        return text

    def _thumbnail_url_from_metadata(self, metadata: dict[str, Any]) -> str:
        thumb_url = str(metadata.get("thumbnail_url") or metadata.get("thumbnail") or "").strip()
        if not thumb_url:
            thumbnails = metadata.get("thumbnails")
            if isinstance(thumbnails, list):
                for item in reversed(thumbnails):
                    if not isinstance(item, dict):
                        continue
                    value = str(item.get("url") or "").strip()
                    if value:
                        thumb_url = value
                        break
        return self._normalize_thumbnail_url(thumb_url)

    def _thumbnail_suffix(self, thumbnail_url: str, content_type: str) -> str:
        suffix = Path(urlparse(thumbnail_url).path).suffix.lower()
        if suffix in THUMBNAIL_SUFFIXES:
            return suffix
        lowered = str(content_type or "").lower()
        if "png" in lowered:
            return ".png"
        if "webp" in lowered:
            return ".webp"
        if "gif" in lowered:
            return ".gif"
        if "bmp" in lowered:
            return ".bmp"
        return ".jpg"

    def _download_thumbnail(self, project_dir: Path, thumbnail_url: str) -> str:
        if not thumbnail_url:
            return ""
        if not thumbnail_url.startswith("http://") and not thumbnail_url.startswith("https://"):
            return ""

        try:
            response = requests.get(
                thumbnail_url,
                headers={"User-Agent": REQUEST_UA},
                timeout=25,
                stream=True,
            )
            response.raise_for_status()
            suffix = self._thumbnail_suffix(thumbnail_url, response.headers.get("Content-Type", ""))
            target = project_dir / f"thumbnail{suffix}"
            with target.open("wb") as file:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        file.write(chunk)
            if target.stat().st_size <= 0:
                target.unlink(missing_ok=True)
                return ""
            return str(target)
        except Exception:
            return ""

    def create_project(self, title: str, source_url: str, metadata: dict[str, Any]) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_title = sanitize_title(title)
        project_name = f"{timestamp}_{safe_title}"
        project_dir = self.workspace_root / project_name
        suffix = 1
        while project_dir.exists():
            suffix += 1
            project_dir = self.workspace_root / f"{project_name}_{suffix}"

        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "screenshots").mkdir(parents=True, exist_ok=True)
        thumbnail_url = self._thumbnail_url_from_metadata(metadata)
        thumbnail_path = self._download_thumbnail(project_dir, thumbnail_url)

        payload = {
            "project_name": project_dir.name,
            "title": title,
            "source_url": source_url,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "duration": metadata.get("duration"),
            "is_live": metadata.get("is_live", False),
            "thumbnail_url": thumbnail_url,
            "thumbnail_path": thumbnail_path,
            "video_path": "",
        }
        self._write_metadata(project_dir, payload)
        return project_dir

    def _metadata_file(self, project_dir: str | Path) -> Path:
        return Path(project_dir) / "project.json"

    def _write_metadata(self, project_dir: str | Path, payload: dict[str, Any]) -> None:
        metadata_file = self._metadata_file(project_dir)
        with metadata_file.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)

    def read_metadata(self, project_dir: str | Path) -> dict[str, Any]:
        metadata_file = self._metadata_file(project_dir)
        if not metadata_file.exists():
            raise FileNotFoundError(f"Missing project metadata: {metadata_file}")
        with metadata_file.open("r", encoding="utf-8") as file:
            return json.load(file)

    def update_video_path(self, project_dir: str | Path, video_path: str | Path) -> None:
        payload = self.read_metadata(project_dir)
        payload["video_path"] = str(video_path)
        self._write_metadata(project_dir, payload)

    def rename_video_to_title(self, project_dir: str | Path, video_path: str | Path, title: str) -> Path:
        source = Path(video_path)
        if not source.exists() or not source.is_file():
            return source
        target_dir = Path(project_dir)
        suffix = source.suffix or ".mp4"
        base_name = sanitize_filename(title)
        target = target_dir / f"{base_name}{suffix}"
        if target.resolve() == source.resolve():
            return source

        index = 2
        while target.exists():
            target = target_dir / f"{base_name}_{index}{suffix}"
            index += 1

        source.rename(target)
        return target

    def list_projects(self) -> list[dict[str, Any]]:
        projects: list[dict[str, Any]] = []
        for entry in self.workspace_root.iterdir():
            if not entry.is_dir():
                continue
            metadata_file = entry / "project.json"
            if not metadata_file.exists():
                continue
            try:
                metadata = self.read_metadata(entry)
            except (OSError, json.JSONDecodeError):
                continue
            projects.append(
                {
                    "path": str(entry),
                    "name": entry.name,
                    "title": metadata.get("title", ""),
                    "created_at": metadata.get("created_at", ""),
                    "duration": metadata.get("duration"),
                    "thumbnail_url": metadata.get("thumbnail_url", ""),
                    "thumbnail_path": metadata.get("thumbnail_path", ""),
                    "video_path": metadata.get("video_path", ""),
                }
            )
        projects.sort(key=lambda p: p.get("created_at", ""), reverse=True)
        return projects

    def list_screenshots(self, project_dir: str | Path) -> list[str]:
        shot_dir = Path(project_dir) / "screenshots"
        if not shot_dir.exists():
            return []
        images = [
            path
            for path in shot_dir.iterdir()
            if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        ]
        images.sort(key=lambda p: p.name)
        return [str(path) for path in images]

    def load_project(self, project_dir: str | Path) -> dict[str, Any]:
        path = Path(project_dir)
        metadata = self.read_metadata(path)
        screenshots = self.list_screenshots(path)
        return {"path": str(path), "metadata": metadata, "screenshots": screenshots}

    def delete_video(self, project_dir: str | Path) -> bool:
        payload = self.read_metadata(project_dir)
        video_path = payload.get("video_path", "")
        if not video_path:
            return False

        path = Path(video_path)
        if not path.exists():
            return False

        path.unlink()
        payload["video_path"] = ""
        self._write_metadata(project_dir, payload)
        return True

    def delete_project(self, project_dir: str | Path) -> bool:
        path = Path(project_dir).resolve()
        workspace = self.workspace_root.resolve()
        if workspace not in path.parents:
            return False
        if not path.exists() or not path.is_dir():
            return False
        shutil.rmtree(path, ignore_errors=False)
        return True
