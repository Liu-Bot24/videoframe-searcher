from __future__ import annotations

import json
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Callable

import requests


ProgressCallback = Callable[[str], None] | None


class PluginSearchService:
    def __init__(self, bridge_base_url: str = "http://127.0.0.1:38999") -> None:
        self.bridge_base_url = bridge_base_url.rstrip("/")
        self.session = requests.Session()
        self.timeout = 10

    def _emit(self, progress_callback: ProgressCallback, message: str) -> None:
        if progress_callback:
            progress_callback(message)

    def _get_json(self, path: str) -> dict:
        response = self.session.get(f"{self.bridge_base_url}{path}", timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("桥接服务返回格式错误。")
        return payload

    def _post_json(self, path: str, payload: dict) -> dict:
        response = self.session.post(f"{self.bridge_base_url}{path}", json=payload, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("桥接服务返回格式错误。")
        return data

    def _ensure_plugin_enabled(self) -> dict:
        for _ in range(7):
            try:
                status = self._get_json("/status")
            except requests.RequestException as exc:
                raise RuntimeError("无法连接本地桥接服务，请先启动主程序并检查端口 38999。") from exc
            except json.JSONDecodeError as exc:
                raise RuntimeError("桥接服务返回了非法 JSON。") from exc

            if not status.get("ok", False):
                raise RuntimeError(f"桥接服务异常：{status}")

            plugin_enabled = bool(status.get("plugin_enabled", False))
            heartbeat_recent = bool(status.get("heartbeat_recent", False))

            if heartbeat_recent and plugin_enabled:
                return status
            if heartbeat_recent and not plugin_enabled:
                raise RuntimeError("Chrome 插件处于关闭状态，请先在插件弹窗中开启。")

            time.sleep(0.4)

        raise RuntimeError(
            "插件状态尚未同步，请打开 Chrome 插件弹窗并确认已开启，再重试一次。"
        )

    def _open_google_home(self) -> None:
        url = "https://www.google.com/?hl=zh-CN"
        if sys.platform == "darwin":
            for app_name in ("Google Chrome", "Chromium", "Microsoft Edge", "Brave Browser"):
                try:
                    result = subprocess.run(
                        ["open", "-a", app_name, url],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                except OSError:
                    continue
                if result.returncode == 0:
                    return
            raise RuntimeError("macOS 未找到可用于插件联动的 Chromium 浏览器，请先安装并打开 Google Chrome。")
        if not webbrowser.open(url):
            raise RuntimeError("无法自动打开浏览器，请手动打开 Google Lens 页面后重试。")

    def queue_search_many(self, image_paths: list[str | Path], progress_callback: ProgressCallback = None) -> dict:
        targets: list[Path] = []
        for raw in image_paths:
            target = Path(raw).resolve()
            if not target.exists():
                raise FileNotFoundError(f"截图不存在：{target}")
            targets.append(target)

        if not targets:
            raise RuntimeError("没有可提交的截图。")

        self._emit(progress_callback, "检测插件状态...")
        self._ensure_plugin_enabled()

        task_ids: list[str] = []
        pending_count = 0
        total = len(targets)
        for idx, target in enumerate(targets, start=1):
            self._emit(progress_callback, f"提交搜索任务 {idx}/{total}：{target.name}")
            try:
                result = self._post_json("/queue", {"image_path": str(target)})
            except requests.RequestException as exc:
                raise RuntimeError("向桥接服务提交任务失败。") from exc
            except json.JSONDecodeError as exc:
                raise RuntimeError("桥接服务返回了非法 JSON。") from exc
            if not result.get("ok", False):
                raise RuntimeError(str(result.get("error") or "桥接服务提交失败。"))
            task_ids.append(str(result.get("task_id") or ""))
            pending_count = int(result.get("pending_count") or pending_count)

        self._open_google_home()
        self._emit(progress_callback, "已触发浏览器打开 Google Lens 页面，插件将自动执行队列搜索。")
        return {"ok": True, "queued_count": total, "pending_count": pending_count, "task_ids": task_ids}

    def queue_search(self, image_path: str | Path, progress_callback: ProgressCallback = None) -> dict:
        return self.queue_search_many([image_path], progress_callback)
