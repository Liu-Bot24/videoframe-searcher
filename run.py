from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

from videoframe_searcher.logging_config import configure_logging


ROOT_DIR = Path(__file__).resolve().parent
REQUIREMENTS_FILE = ROOT_DIR / "requirements.txt"
BOOTSTRAP_STATE_FILE = ROOT_DIR / ".bootstrap_state.json"
LOGGER = logging.getLogger("videoframe_searcher.bootstrap")
MIN_PYTHON = (3, 11)

REQUIRED_IMPORTS = {
    "PySide6": "PySide6",
    "psutil": "psutil",
    "requests": "requests",
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


def _missing_imports() -> list[str]:
    return [m for m in REQUIRED_IMPORTS if not _is_module_available(m)]


def _ensure_supported_python_version() -> None:
    current = sys.version_info[:3]
    if current[:2] >= MIN_PYTHON:
        return
    version_text = ".".join(str(part) for part in current)
    required_text = ".".join(str(part) for part in MIN_PYTHON)
    raise RuntimeError(
        f"需要 Python {required_text}+，当前解释器为 {version_text}。"
        " macOS 请优先运行 start.command，Windows 请运行 start.bat。"
    )


def _requirements_hash() -> str:
    if not REQUIREMENTS_FILE.exists():
        return ""
    digest = hashlib.sha256()
    digest.update(REQUIREMENTS_FILE.read_bytes())
    return digest.hexdigest()


def _python_tag() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def _load_bootstrap_state() -> dict[str, Any]:
    if not BOOTSTRAP_STATE_FILE.exists():
        return {}
    try:
        return json.loads(BOOTSTRAP_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_bootstrap_state(requirements_hash: str) -> None:
    payload = {
        "requirements_hash": requirements_hash,
        "python_tag": _python_tag(),
    }
    try:
        BOOTSTRAP_STATE_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        LOGGER.warning("写入依赖状态文件失败：%s", exc)


def _ensure_pip_available() -> None:
    probe = subprocess.run(
        [sys.executable, "-m", "pip", "--version"],
        cwd=str(ROOT_DIR),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if probe.returncode == 0:
        return
    LOGGER.warning("pip 不可用，尝试执行 ensurepip ...")
    _run_checked([sys.executable, "-m", "ensurepip", "--upgrade"])


def install_missing_dependencies() -> None:
    missing_before = _missing_imports()
    requirements_hash = _requirements_hash()
    state = _load_bootstrap_state()
    should_sync = bool(missing_before)
    should_sync = should_sync or state.get("requirements_hash") != requirements_hash
    should_sync = should_sync or state.get("python_tag") != _python_tag()

    if missing_before and not REQUIREMENTS_FILE.exists():
        raise FileNotFoundError(f"缺少 requirements 文件：{REQUIREMENTS_FILE}")
    if not should_sync:
        LOGGER.info("依赖状态已是最新，跳过安装。")
        return
    if not REQUIREMENTS_FILE.exists():
        raise FileNotFoundError(f"缺少 requirements 文件：{REQUIREMENTS_FILE}")

    if missing_before:
        LOGGER.info("检测到缺失依赖：%s", ", ".join(missing_before))
    else:
        LOGGER.info("检测到依赖或 Python 版本变化，执行依赖同步。")

    print("Installing project dependencies...")
    _ensure_pip_available()
    _run_checked(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-r",
            str(REQUIREMENTS_FILE),
        ]
    )

    missing_after = _missing_imports()
    if missing_after:
        raise RuntimeError(f"依赖安装后仍缺失：{', '.join(missing_after)}")
    _write_bootstrap_state(requirements_hash)


def ensure_runtime_components() -> None:
    import PySide6.QtWidgets  # noqa: F401


def main() -> int:
    log_file = configure_logging(ROOT_DIR)
    LOGGER.info("应用启动")
    try:
        _ensure_supported_python_version()
        install_missing_dependencies()
        ensure_runtime_components()
        from videoframe_searcher.main import main as app_main

        app_main()
        return 0
    except Exception:
        LOGGER.exception("启动失败")
        print(f"[ERROR] 启动失败，详情请查看日志：{log_file}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
