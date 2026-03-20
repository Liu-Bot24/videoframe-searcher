from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType


_CONFIGURED = False
_LOG_FILE: Path | None = None


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def get_log_file() -> Path:
    if _LOG_FILE is not None:
        return _LOG_FILE
    return _project_root() / "logs" / "app.log"


def configure_logging(base_dir: str | Path | None = None) -> Path:
    global _CONFIGURED, _LOG_FILE
    if _CONFIGURED and _LOG_FILE is not None:
        return _LOG_FILE

    root = Path(base_dir) if base_dir else _project_root()
    logs_dir = root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "app.log"

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    stream_handler = logging.StreamHandler(sys.stdout)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        handlers=[file_handler, stream_handler],
        force=True,
    )

    _install_exception_hooks()

    _LOG_FILE = log_file
    _CONFIGURED = True
    logging.getLogger("videoframe_searcher").info("日志已初始化：%s", log_file)
    return log_file


def _install_exception_hooks() -> None:
    def handle_exception(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: TracebackType | None,
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logging.getLogger("videoframe_searcher").error(
            "未捕获异常",
            exc_info=(exc_type, exc_value, exc_traceback),
        )
        sys.__excepthook__(exc_type, exc_value, exc_traceback)

    sys.excepthook = handle_exception

    if hasattr(threading, "excepthook"):
        def handle_thread_exception(args: threading.ExceptHookArgs) -> None:
            logging.getLogger("videoframe_searcher").error(
                "线程未捕获异常",
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )

        threading.excepthook = handle_thread_exception
