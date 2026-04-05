from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from videoframe_searcher.logging_config import configure_logging
from videoframe_searcher.ui.main_window import MainWindow


def _resolve_app_icon_path() -> Path:
    assets_dir = Path(__file__).resolve().parent / "assets"
    preferred = ("app_icon.ico", "app_icon.png") if sys.platform.startswith("win") else ("app_icon.png", "app_icon.ico")
    for name in preferred:
        candidate = assets_dir / name
        if candidate.exists():
            return candidate
    return assets_dir / preferred[0]


def main() -> None:
    configure_logging()
    logger = logging.getLogger("videoframe_searcher.main")
    logger.info("正在初始化 Qt 应用")
    app = QApplication(sys.argv)
    app.setApplicationName("VideoFrame Searcher")
    icon_path = _resolve_app_icon_path()
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
