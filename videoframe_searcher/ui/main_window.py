from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QThreadPool, QSize, Qt, Signal
from PySide6.QtGui import QCloseEvent, QIcon, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from videoframe_searcher.logging_config import get_log_file
from videoframe_searcher.services.bridge_runtime_service import BridgeRuntimeService
from videoframe_searcher.services.plugin_search_service import PluginSearchService
from videoframe_searcher.services.process_manager import ProcessManager
from videoframe_searcher.services.project_service import sanitize_filename
from videoframe_searcher.services.settings_service import SettingsService
from videoframe_searcher.services.worker import Worker


SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
THUMBNAIL_SIZE = QSize(180, 180)


class GalleryListWidget(QListWidget):
    files_dropped = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:
        paths = self._extract_image_paths(event.mimeData())
        if paths:
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        paths = self._extract_image_paths(event.mimeData())
        if paths:
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event) -> None:
        paths = self._extract_image_paths(event.mimeData())
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
            return
        event.ignore()

    @staticmethod
    def _extract_image_paths(mime_data) -> list[str]:
        paths: list[str] = []
        if not mime_data or not mime_data.hasUrls():
            return paths
        for url in mime_data.urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES:
                paths.append(str(path))
        return paths


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger("videoframe_searcher.ui")
        self.settings_service = SettingsService()
        self.settings = self.settings_service.load()
        self.gallery_dir = self._resolve_gallery_dir()
        self.process_manager = ProcessManager()
        self.bridge_runtime_service = BridgeRuntimeService(self.process_manager)
        self.plugin_search_service = PluginSearchService()
        self.thread_pool = QThreadPool.globalInstance()
        self._workers: set[Worker] = set()
        self._preview_path: Path | None = None
        self._selection_count = 0

        self.setWindowTitle("Image Search Gallery")
        self.resize(1360, 860)
        self._build_ui()
        self._refresh_gallery()

    def _resolve_gallery_dir(self) -> Path:
        workspace_root = Path(str(self.settings.get("workspace_root") or "")).expanduser()
        if not str(workspace_root).strip():
            workspace_root = Path(__file__).resolve().parents[2] / "workspace"
        gallery_dir = workspace_root / "image_gallery"
        gallery_dir.mkdir(parents=True, exist_ok=True)
        return gallery_dir

    def _build_ui(self) -> None:
        root = QWidget(self)
        main_layout = QVBoxLayout(root)
        main_layout.setContentsMargins(18, 18, 18, 18)
        main_layout.setSpacing(14)

        title_label = QLabel("Image Search Gallery")
        title_label.setStyleSheet("font-size: 24px; font-weight: 700;")
        subtitle_label = QLabel(
            "把图片直接拖进窗口或导入到图库，然后批量提交给浏览器插件执行以图搜图。"
        )
        subtitle_label.setStyleSheet("color: #4b5563;")

        action_row = QHBoxLayout()
        action_row.setSpacing(10)

        self.import_button = QPushButton("导入图片")
        self.import_button.clicked.connect(self._choose_images)
        self.open_folder_button = QPushButton("打开图库文件夹")
        self.open_folder_button.clicked.connect(self._open_gallery_dir)
        self.search_button = QPushButton("批量以图搜图")
        self.search_button.clicked.connect(self._search_images)
        self.refresh_button = QPushButton("刷新图库")
        self.refresh_button.clicked.connect(self._refresh_gallery)

        for button in (
            self.import_button,
            self.open_folder_button,
            self.search_button,
            self.refresh_button,
        ):
            button.setMinimumHeight(38)
            action_row.addWidget(button)
        action_row.addStretch(1)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #111827; font-weight: 600;")
        self.path_label = QLabel(str(self.gallery_dir))
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.path_label.setStyleSheet("color: #6b7280;")

        self.gallery_list = GalleryListWidget(self)
        self.gallery_list.setViewMode(QListView.ViewMode.IconMode)
        self.gallery_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.gallery_list.setMovement(QListView.Movement.Static)
        self.gallery_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.gallery_list.setSpacing(12)
        self.gallery_list.setIconSize(THUMBNAIL_SIZE)
        self.gallery_list.setWordWrap(True)
        self.gallery_list.setUniformItemSizes(False)
        self.gallery_list.itemSelectionChanged.connect(self._update_preview)
        self.gallery_list.files_dropped.connect(self._import_image_paths)
        self.gallery_list.setStyleSheet(
            "QListWidget { background: #f8fafc; border: 1px solid #dbe3ee; border-radius: 12px; padding: 10px; }"
            "QListWidget::item { border-radius: 10px; padding: 8px; }"
            "QListWidget::item:selected { background: #dbeafe; color: #0f172a; }"
        )

        gallery_frame = QFrame()
        gallery_layout = QVBoxLayout(gallery_frame)
        gallery_layout.setContentsMargins(0, 0, 0, 0)
        gallery_layout.setSpacing(8)
        gallery_layout.addWidget(self.gallery_list)

        self.preview_label = QLabel("选择一张图片查看预览")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(360, 320)
        self.preview_label.setStyleSheet(
            "background: #0f172a; color: #e5e7eb; border-radius: 12px; border: 1px solid #111827;"
        )

        self.preview_info_label = QLabel("拖拽图片到左侧区域，或点击“导入图片”。")
        self.preview_info_label.setWordWrap(True)
        self.preview_info_label.setStyleSheet("color: #4b5563;")

        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setPlaceholderText("运行日志会显示在这里。")
        self.log_box.setStyleSheet(
            "QPlainTextEdit { background: #0b1220; color: #d1d5db; border-radius: 12px; border: 1px solid #1f2937; padding: 10px; }"
        )

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)
        right_layout.addWidget(self.preview_label, 1)
        right_layout.addWidget(self.preview_info_label)
        right_layout.addWidget(self.log_box, 1)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(gallery_frame)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        main_layout.addWidget(title_label)
        main_layout.addWidget(subtitle_label)
        main_layout.addLayout(action_row)
        main_layout.addWidget(self.status_label)
        main_layout.addWidget(self.path_label)
        main_layout.addWidget(splitter, 1)

        self.setCentralWidget(root)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._render_preview()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.process_manager.kill_all()
        self.thread_pool.waitForDone(3000)
        super().closeEvent(event)

    def append_log(self, message: str) -> None:
        text = str(message or "").strip()
        if not text:
            return
        self.logger.info(text)
        self.log_box.appendPlainText(text)
        self.log_box.verticalScrollBar().setValue(self.log_box.verticalScrollBar().maximum())

    def _run_worker(
        self,
        fn,
        *,
        on_result=None,
        on_error=None,
        on_finished=None,
    ) -> None:
        worker = Worker(fn)
        self._workers.add(worker)
        worker.signals.progress.connect(self.append_log)
        if on_result:
            worker.signals.result.connect(on_result)
        if on_error:
            worker.signals.error.connect(on_error)
        else:
            worker.signals.error.connect(self._show_worker_error)

        def _cleanup() -> None:
            self._workers.discard(worker)
            if on_finished:
                on_finished()

        worker.signals.finished.connect(_cleanup)
        self.thread_pool.start(worker)

    def _show_worker_error(self, traceback_text: str) -> None:
        self.logger.error("后台任务失败：\n%s", traceback_text)
        summary = traceback_text.strip().splitlines()[-1] if traceback_text.strip() else "未知错误"
        self.append_log(summary)
        QMessageBox.critical(self, "执行失败", f"{summary}\n\n详细日志：{get_log_file()}")

    def _choose_images(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择图片",
            str(Path.home()),
            "Images (*.jpg *.jpeg *.png *.webp *.bmp *.gif)",
        )
        if files:
            self._import_image_paths(files)

    def _import_image_paths(self, raw_paths: Iterable[str]) -> None:
        imported: list[Path] = []
        skipped: list[str] = []
        for raw in raw_paths:
            source = Path(raw).expanduser()
            if not source.exists() or not source.is_file():
                skipped.append(str(source))
                continue
            if source.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
                skipped.append(source.name)
                continue
            if not QImageReader(str(source)).canRead():
                skipped.append(source.name)
                continue
            target = self._copy_image_to_gallery(source)
            imported.append(target)

        if imported:
            self.append_log(f"已导入 {len(imported)} 张图片到图库。")
            self._refresh_gallery(select_paths=imported)
        if skipped:
            preview = "\n".join(skipped[:6])
            extra = "\n..." if len(skipped) > 6 else ""
            QMessageBox.warning(self, "部分文件未导入", f"以下文件无法导入：\n{preview}{extra}")

    def _copy_image_to_gallery(self, source: Path) -> Path:
        if source.resolve().parent == self.gallery_dir.resolve():
            return source.resolve()

        safe_stem = sanitize_filename(source.stem)
        suffix = source.suffix.lower() or ".jpg"
        candidate = self.gallery_dir / f"{safe_stem}{suffix}"
        index = 2
        while candidate.exists():
            candidate = self.gallery_dir / f"{safe_stem}_{index}{suffix}"
            index += 1
        shutil.copy2(source, candidate)
        return candidate

    def _refresh_gallery(self, select_paths: Iterable[Path] | None = None) -> None:
        selected = {str(path.resolve()) for path in (select_paths or [])}
        self.gallery_list.clear()
        image_paths = self._list_gallery_images()
        for image_path in image_paths:
            item = QListWidgetItem(image_path.name)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setToolTip(str(image_path))
            item.setData(Qt.ItemDataRole.UserRole, str(image_path))
            item.setSizeHint(QSize(208, 232))
            pixmap = QPixmap(str(image_path))
            if not pixmap.isNull():
                item.setIcon(
                    QIcon(
                        pixmap.scaled(
                            THUMBNAIL_SIZE,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
                )
            self.gallery_list.addItem(item)
            if str(image_path.resolve()) in selected:
                item.setSelected(True)

        self.status_label.setText(f"图库中共有 {len(image_paths)} 张图片。未选中时，“批量以图搜图”会提交全部图片。")
        self._update_preview()

    def _list_gallery_images(self) -> list[Path]:
        images: list[Path] = []
        for path in self.gallery_dir.iterdir():
            if not path.is_file():
                continue
            if path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
                continue
            images.append(path)
        images.sort(key=lambda item: item.name.lower())
        return images

    def _selected_image_paths(self) -> list[Path]:
        paths: list[Path] = []
        for item in self.gallery_list.selectedItems():
            raw = item.data(Qt.ItemDataRole.UserRole)
            if raw:
                paths.append(Path(str(raw)))
        return paths

    def _all_image_paths(self) -> list[Path]:
        paths: list[Path] = []
        for index in range(self.gallery_list.count()):
            item = self.gallery_list.item(index)
            raw = item.data(Qt.ItemDataRole.UserRole)
            if raw:
                paths.append(Path(str(raw)))
        return paths

    def _update_preview(self) -> None:
        selected = self._selected_image_paths()
        self._selection_count = len(selected)
        self._preview_path = selected[0] if selected else None
        if not selected:
            self.preview_info_label.setText(
                "拖拽图片到左侧区域，或点击“导入图片”。\n"
                f"当前图库目录：{self.gallery_dir}"
            )
        elif len(selected) == 1:
            self.preview_info_label.setText(f"已选中：{selected[0].name}\n{selected[0]}")
        else:
            self.preview_info_label.setText(
                f"已选中 {len(selected)} 张图片。\n将优先按当前选中批量提交以图搜图。"
            )
        self._render_preview()

    def _render_preview(self) -> None:
        if self._preview_path is None:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("选择一张图片查看预览")
            return

        pixmap = QPixmap(str(self._preview_path))
        if pixmap.isNull():
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("预览加载失败")
            return

        available_size = QSize(
            max(120, self.preview_label.width() - 24),
            max(120, self.preview_label.height() - 24),
        )
        scaled = pixmap.scaled(
            available_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview_label.setText("")
        self.preview_label.setPixmap(scaled)

    def _open_gallery_dir(self) -> None:
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(self.gallery_dir))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(self.gallery_dir)])
            else:
                subprocess.Popen(["xdg-open", str(self.gallery_dir)])
        except Exception as exc:
            QMessageBox.warning(self, "打开失败", f"无法打开图库目录：{exc}")

    def _search_images(self) -> None:
        target_images = self._selected_image_paths() or self._all_image_paths()
        if not target_images:
            QMessageBox.information(self, "没有图片", "请先导入图片，或将图片拖进图库区域。")
            return

        self.search_button.setEnabled(False)
        self.append_log(f"开始提交搜图任务，共 {len(target_images)} 张图片。")

        def task(progress_callback=None):
            self.bridge_runtime_service.ensure_running(progress_callback)
            return self.plugin_search_service.queue_search_many(target_images, progress_callback)

        def on_result(result: dict) -> None:
            queued_count = int(result.get("queued_count") or 0)
            pending_count = int(result.get("pending_count") or 0)
            self.append_log(f"搜索任务已提交，共 {queued_count} 张，待处理队列={pending_count}")

        self._run_worker(
            task,
            on_result=on_result,
            on_finished=lambda: self.search_button.setEnabled(True),
        )
