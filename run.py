from __future__ import annotations

import importlib.util
import logging
import subprocess
import sys
from pathlib import Path

from videoframe_searcher.logging_config import configure_logging


ROOT_DIR = Path(__file__).resolve().parent
REQUIREMENTS_FILE = ROOT_DIR / "requirements.txt"
LOGGER = logging.getLogger("videoframe_searcher.bootstrap")

REQUIRED_IMPORTS = {
    "PySide6": "PySide6",
    "yt_dlp": "yt-dlp",
    "imageio_ffmpeg": "imageio-ffmpeg",
    "psutil": "psutil",
    "requests": "requests",
    "curl_cffi": "curl-cffi",
}


def _is_module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _run_checked(command: list[str]) -> None:
    LOGGER.info("执行命令：%s", " ".join(command))
    result = subprocess.run(
        command,
        cwd=str(ROOT_DIR),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.stdout.strip():
        LOGGER.info("命令输出：\n%s", result.stdout.strip())
    if result.returncode != 0:
        LOGGER.error("命令失败：\n%s", result.stderr.strip() or result.stdout.strip())
        raise RuntimeError(f"命令执行失败：{' '.join(command)}")


def install_missing_dependencies() -> None:
    missing = [m for m in REQUIRED_IMPORTS if not _is_module_available(m)]
    if missing and REQUIREMENTS_FILE.exists():
        LOGGER.info("检测到缺失依赖：%s", ", ".join(missing))
        print("Installing project dependencies...")
        _run_checked([sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS_FILE)])
    elif missing:
        raise FileNotFoundError(f"缺少 requirements 文件：{REQUIREMENTS_FILE}")


def main() -> int:
    log_file = configure_logging(ROOT_DIR)
    LOGGER.info("应用启动")
    try:
        install_missing_dependencies()
        from videoframe_searcher.main import main as app_main

        app_main()
        return 0
    except Exception:
        LOGGER.exception("启动失败")
        print(f"[ERROR] 启动失败，详情请查看日志：{log_file}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
