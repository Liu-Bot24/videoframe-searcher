from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

import requests

from videoframe_searcher.services.process_manager import ProcessManager


ProgressCallback = Callable[[str], None] | None


class BridgeRuntimeService:
    def __init__(
        self,
        process_manager: ProcessManager,
        bridge_base_url: str = "http://127.0.0.1:38999",
        script_path: str | Path | None = None,
    ) -> None:
        self.process_manager = process_manager
        self.bridge_base_url = bridge_base_url.rstrip("/")
        root_dir = Path(__file__).resolve().parents[2]
        self.script_path = Path(script_path) if script_path else root_dir / "chrome_extension_bridge.py"
        self._process: subprocess.Popen[str] | None = None

    def _emit(self, progress_callback: ProgressCallback, message: str) -> None:
        if progress_callback:
            progress_callback(message)

    def _is_healthy(self) -> bool:
        try:
            response = requests.get(f"{self.bridge_base_url}/health", timeout=1.5)
            return response.ok
        except requests.RequestException:
            return False

    def ensure_running(self, progress_callback: ProgressCallback = None) -> str:
        if self._is_healthy():
            return "already_running"

        if not self.script_path.exists():
            raise RuntimeError(f"桥接脚本不存在：{self.script_path}")

        if not self._process or self._process.poll() is not None:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform.startswith("win") else 0
            self._emit(progress_callback, "正在自动启动 Chrome 插件桥接服务...")
            self._process = self.process_manager.spawn(
                [sys.executable, str(self.script_path)],
                cwd=str(self.script_path.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )

        deadline = time.time() + 8.0
        while time.time() < deadline:
            if self._is_healthy():
                return "started"
            time.sleep(0.25)

        raise RuntimeError("自动启动桥接服务失败，请检查端口 38999 占用或 logs/chrome_extension_bridge.log。")

