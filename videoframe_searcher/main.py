from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from videoframe_searcher.logging_config import configure_logging
from videoframe_searcher.ui.main_window import MainWindow


def main() -> None:
    configure_logging()
    logger = logging.getLogger("videoframe_searcher.main")
    logger.info("正在初始化 Qt 应用")
    app = QApplication(sys.argv)
    app.setApplicationName("VideoFrame Searcher")
    icon_path = Path(__file__).resolve().parent / "assets" / "app_icon.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
