from __future__ import annotations

import logging
import math
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

from PySide6.QtCore import QThreadPool, Qt, QSize
from PySide6.QtGui import QAction, QCloseEvent, QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QMenu,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from videoframe_searcher.logging_config import get_log_file
from videoframe_searcher.services.download_service import DownloadService
from videoframe_searcher.services.bridge_runtime_service import BridgeRuntimeService
from videoframe_searcher.services.frame_service import FrameService
from videoframe_searcher.services.local_video_service import LocalVideoService
from videoframe_searcher.services.plugin_search_service import PluginSearchService
from videoframe_searcher.services.process_manager import ProcessManager
from videoframe_searcher.services.project_service import ProjectService
from videoframe_searcher.services.settings_service import SettingsService
from videoframe_searcher.services.worker import Worker


def _duration_text(seconds: Any) -> str:
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return "未知"
    hour, rem = divmod(total, 3600)
    minute, sec = divmod(rem, 60)
    if hour:
        return f"{hour:02d}:{minute:02d}:{sec:02d}"
    return f"{minute:02d}:{sec:02d}"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("VideoFrame Searcher")
        self.resize(1440, 900)
        self.logger = logging.getLogger("videoframe_searcher.ui")

        self.settings_service = SettingsService()
        self.settings = self.settings_service.load()

        self.process_manager = ProcessManager()
        self.bridge_runtime_service = BridgeRuntimeService(self.process_manager)
        self.project_service = ProjectService(self.settings["workspace_root"])
        self.download_service = DownloadService(self.process_manager)
        self.frame_service = FrameService(self.process_manager)
        self.local_video_service = LocalVideoService(self.process_manager)
        self.plugin_search_service = PluginSearchService()

        self.thread_pool = QThreadPool.globalInstance()
        self._workers: set[Worker] = set()

        self.latest_metadata: dict[str, Any] | None = None
        self.latest_metadata_url: str = ""
        self.current_project_path: Path | None = None
        self.current_metadata: dict[str, Any] = {}
        self.current_images: list[str] = []
        self.selected_images: set[str] = set()
        self.current_page = 0
        self.page_size = 60

        self._build_ui()
        self._load_settings_into_form()
        self.refresh_history()
        self._ensure_bridge_running_on_startup()
        self.append_log("应用已启动。")
        self.append_log(f"日志文件：{get_log_file()}")

    def _ensure_bridge_running_on_startup(self) -> None:
        try:
            state = self.bridge_runtime_service.ensure_running()
        except Exception as exc:
            self.append_log(f"桥接服务启动失败：{exc}")
            return
        if state == "started":
            self.append_log("桥接服务已自动启动（端口 38999）。")
        else:
            self.append_log("桥接服务已在线。")

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root_layout.addWidget(splitter)
        self.setCentralWidget(root)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("历史项目"))
        self.history_list = QListWidget()
        self.history_list.itemClicked.connect(self._on_history_item_clicked)
        self.history_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.history_list.customContextMenuRequested.connect(self._on_history_context_menu)
        left_layout.addWidget(self.history_list, 1)

        history_btn_row = QHBoxLayout()
        refresh_button = QPushButton("刷新历史")
        refresh_button.clicked.connect(self.refresh_history)
        history_btn_row.addWidget(refresh_button)
        delete_button = QPushButton("删除项目")
        delete_button.clicked.connect(self._on_delete_selected_project)
        history_btn_row.addWidget(delete_button)
        left_layout.addLayout(history_btn_row)
        splitter.addWidget(left_panel)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.tabs = QTabWidget()
        right_layout.addWidget(self.tabs, 1)

        self.download_tab = self._build_download_tab()
        self.gallery_tab = self._build_gallery_tab()
        self.settings_tab = self._build_settings_tab()

        self.tabs.addTab(self.download_tab, "视频工作台")
        self.tabs.addTab(self.gallery_tab, "截图画廊")
        self.tabs.addTab(self.settings_tab, "设置")

        right_layout.addWidget(QLabel("运行日志"))
        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        right_layout.addWidget(self.log_output, 0)
        splitter.addWidget(right_panel)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)

        refresh_action = QAction("刷新历史", self)
        refresh_action.triggered.connect(self.refresh_history)
        self.addAction(refresh_action)

    def _build_download_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        form = QGroupBox("下载参数")
        form_layout = QGridLayout(form)
        form_layout.addWidget(QLabel("视频 URL"), 0, 0)
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("粘贴视频链接后先解析元数据")
        form_layout.addWidget(self.url_input, 0, 1, 1, 3)

        self.parse_button = QPushButton("解析元数据")
        self.parse_button.clicked.connect(self._on_parse_metadata)
        form_layout.addWidget(self.parse_button, 1, 1)

        self.download_button = QPushButton("开始下载视频")
        self.download_button.clicked.connect(self._on_download_video)
        form_layout.addWidget(self.download_button, 1, 2, 1, 2)

        layout.addWidget(form)

        meta_group = QGroupBox("视频信息")
        meta_layout = QVBoxLayout(meta_group)
        self.metadata_label = QLabel("尚未解析。")
        self.metadata_label.setWordWrap(True)
        meta_layout.addWidget(self.metadata_label)
        layout.addWidget(meta_group)

        box = QGroupBox("抽帧参数")
        box_layout = QGridLayout(box)
        box_layout.addWidget(QLabel("抽帧间隔(秒)"), 0, 0)
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 3600)
        self.interval_spin.setValue(5)
        box_layout.addWidget(self.interval_spin, 0, 1)

        self.clear_checkbox = QCheckBox("重新截图时，清除该视频原有截图")
        box_layout.addWidget(self.clear_checkbox, 1, 0, 1, 2)

        self.extract_button = QPushButton("开始抽帧")
        self.extract_button.clicked.connect(self._on_extract_frames)
        box_layout.addWidget(self.extract_button, 2, 0, 1, 2)

        self.upload_button = QPushButton("上传本地视频")
        self.upload_button.clicked.connect(self._on_import_local_video)
        box_layout.addWidget(self.upload_button, 3, 0, 1, 2)

        self.delete_video_button = QPushButton("删除原始视频，仅保留截图")
        self.delete_video_button.clicked.connect(self._on_delete_video)
        box_layout.addWidget(self.delete_video_button, 4, 0, 1, 2)

        layout.addWidget(box)
        self.frame_hint_label = QLabel("未加载项目。")
        self.frame_hint_label.setWordWrap(True)
        layout.addWidget(self.frame_hint_label)
        layout.addStretch(1)
        return tab

    def _build_gallery_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        tools_layout = QHBoxLayout()
        select_page_btn = QPushButton("全选当前页")
        select_page_btn.clicked.connect(self._select_current_page)
        tools_layout.addWidget(select_page_btn)

        clear_btn = QPushButton("清空选择")
        clear_btn.clicked.connect(self._clear_selection)
        tools_layout.addWidget(clear_btn)

        open_folder_btn = QPushButton("打开截图文件夹")
        open_folder_btn.clicked.connect(self._on_open_screenshot_folder)
        tools_layout.addWidget(open_folder_btn)
        tools_layout.addStretch(1)
        self.gallery_info_label = QLabel("总计 0 张，已选 0 张")
        tools_layout.addWidget(self.gallery_info_label)

        self.search_button = QPushButton("以图搜图")
        self.search_button.clicked.connect(self._on_search_selected_image)
        self.search_button.setMinimumHeight(40)
        self.search_button.setMinimumWidth(116)
        self.search_button.setMaximumWidth(132)
        self.search_button.setStyleSheet(
            "QPushButton {"
            "background-color: #0ea5e9;"
            "color: white;"
            "font-weight: 700;"
            "border-radius: 10px;"
            "padding: 8px 16px;"
            "}"
            "QPushButton:hover { background-color: #0284c7; }"
            "QPushButton:disabled { background-color: #7dd3fc; color: #e5e7eb; }"
        )
        tools_layout.addWidget(self.search_button)
        layout.addLayout(tools_layout)

        page_layout = QHBoxLayout()
        self.prev_page_btn = QPushButton("上一页")
        self.prev_page_btn.clicked.connect(self._prev_page)
        self.next_page_btn = QPushButton("下一页")
        self.next_page_btn.clicked.connect(self._next_page)
        self.page_label = QLabel("页码 0 / 0")
        page_layout.addWidget(self.prev_page_btn)
        page_layout.addWidget(self.next_page_btn)
        page_layout.addWidget(self.page_label)
        page_layout.addStretch(1)
        layout.addLayout(page_layout)

        self.gallery_scroll = QScrollArea()
        self.gallery_scroll.setWidgetResizable(True)
        self.gallery_container = QWidget()
        self.gallery_grid = QGridLayout(self.gallery_container)
        self.gallery_grid.setContentsMargins(8, 8, 8, 8)
        self.gallery_grid.setSpacing(12)
        self.gallery_scroll.setWidget(self.gallery_container)
        layout.addWidget(self.gallery_scroll, 1)
        return tab

    def _build_settings_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        box = QGroupBox("基础设置")
        box_layout = QGridLayout(box)
        box_layout.addWidget(QLabel("工作区目录"), 0, 0)
        self.workspace_input = QLineEdit()
        box_layout.addWidget(self.workspace_input, 0, 1)
        browse_btn = QPushButton("浏览")
        browse_btn.clicked.connect(self._on_choose_workspace)
        box_layout.addWidget(browse_btn, 0, 2)

        self.cookie_checkbox = QCheckBox("开启本地浏览器 Cookie 授权 (yt-dlp)")
        box_layout.addWidget(self.cookie_checkbox, 1, 0, 1, 3)

        box_layout.addWidget(QLabel("Cookie 浏览器"), 2, 0)
        self.cookie_browser_combo = QComboBox()
        self.cookie_browser_combo.addItems(["chrome", "edge", "firefox", "brave", "chromium"])
        box_layout.addWidget(self.cookie_browser_combo, 2, 1, 1, 2)

        box_layout.addWidget(QLabel("Cookie 文件路径"), 3, 0)
        self.cookie_file_input = QLineEdit()
        self.cookie_file_input.setPlaceholderText("可选：Netscape cookies.txt 路径")
        box_layout.addWidget(self.cookie_file_input, 3, 1)
        cookie_file_btn = QPushButton("浏览")
        cookie_file_btn.clicked.connect(self._on_choose_cookie_file)
        box_layout.addWidget(cookie_file_btn, 3, 2)

        box_layout.addWidget(QLabel("网络代理 (HTTP Proxy)"), 4, 0)
        self.proxy_input = QLineEdit()
        self.proxy_input.setPlaceholderText("示例: http://127.0.0.1:7890")
        box_layout.addWidget(self.proxy_input, 4, 1, 1, 2)

        box_layout.addWidget(QLabel("下载格式"), 5, 0)
        self.download_format_input = QLineEdit()
        self.download_format_input.setPlaceholderText("示例: bv*+ba/b 或 best")
        box_layout.addWidget(self.download_format_input, 5, 1, 1, 2)

        box_layout.addWidget(QLabel("合并封装格式"), 6, 0)
        self.merge_output_combo = QComboBox()
        self.merge_output_combo.addItems(["mp4", "mkv", "webm", "avi", "flv"])
        box_layout.addWidget(self.merge_output_combo, 6, 1, 1, 2)

        box_layout.addWidget(QLabel("高级参数(可选)"), 7, 0)
        self.extra_args_input = QLineEdit()
        self.extra_args_input.setPlaceholderText("示例: --downloader aria2c --downloader-args \"aria2c:-x 16 -k 1M\"")
        box_layout.addWidget(self.extra_args_input, 7, 1, 1, 2)

        self.impersonate_checkbox = QCheckBox("启用浏览器伪装请求 (yt-dlp --impersonate chrome)")
        box_layout.addWidget(self.impersonate_checkbox, 8, 0, 1, 3)

        save_btn = QPushButton("保存设置")
        save_btn.clicked.connect(self._on_save_settings)
        box_layout.addWidget(save_btn, 9, 0, 1, 3)

        update_btn = QPushButton("强制更新下载核心 (yt-dlp)")
        update_btn.clicked.connect(self._on_update_ytdlp)
        box_layout.addWidget(update_btn, 10, 0, 1, 3)

        layout.addWidget(box)
        layout.addStretch(1)
        return tab

    def _run_worker(
        self,
        task,
        on_result=None,
        on_finished=None,
        on_error=None,
    ) -> None:
        worker = Worker(task)
        self._workers.add(worker)

        worker.signals.progress.connect(self.append_log)
        worker.signals.result.connect(lambda result: on_result(result) if on_result else None)
        worker.signals.error.connect(lambda err: self._handle_worker_error(err, on_error))
        worker.signals.finished.connect(lambda: self._workers.discard(worker))
        if on_finished:
            worker.signals.finished.connect(on_finished)

        self.thread_pool.start(worker)

    def _handle_worker_error(self, traceback_text: str, on_error=None) -> None:
        self.logger.error("后台任务失败：\n%s", traceback_text)
        self.append_log(traceback_text)
        if on_error:
            on_error(traceback_text)
            return
        summary = traceback_text.splitlines()[-1] if traceback_text else "未知错误"
        QMessageBox.critical(self, "执行失败", f"{summary}\n\n详细日志：{get_log_file()}")

    def append_log(self, message: str) -> None:
        self.logger.info(message)
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_output.appendPlainText(f"[{stamp}] {message}")

    def refresh_history(self) -> None:
        projects = self.project_service.list_projects()
        self.history_list.clear()
        for project in projects:
            created_at = project.get("created_at", "")
            title = project.get("title") or project.get("name")
            item = QListWidgetItem(f"{created_at} | {title}")
            item.setData(Qt.ItemDataRole.UserRole, project["path"])
            self.history_list.addItem(item)
        self.append_log(f"历史项目已刷新，共 {len(projects)} 个。")

    def _on_history_item_clicked(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        self.load_project(Path(path))

    def _on_history_context_menu(self, pos) -> None:
        item = self.history_list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        delete_action = menu.addAction("删除项目")
        selected = menu.exec(self.history_list.mapToGlobal(pos))
        if selected == delete_action:
            self._delete_project_item(item)

    def _on_delete_selected_project(self) -> None:
        item = self.history_list.currentItem()
        if item is None:
            QMessageBox.warning(self, "未选择项目", "请先从历史项目中选择一个项目。")
            return
        self._delete_project_item(item)

    def _delete_project_item(self, item: QListWidgetItem) -> None:
        raw_path = item.data(Qt.ItemDataRole.UserRole)
        if not raw_path:
            QMessageBox.warning(self, "无效项目", "该项目路径无效，无法删除。")
            return

        project_path = Path(raw_path)
        confirm = QMessageBox.question(
            self,
            "确认删除项目",
            f"将永久删除项目及其全部文件：\n{project_path.name}\n\n是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        try:
            deleted = self.project_service.delete_project(project_path)
        except Exception as exc:
            self.logger.exception("删除项目失败：%s", project_path)
            QMessageBox.critical(self, "删除失败", f"删除项目失败：{exc}")
            return

        if not deleted:
            QMessageBox.warning(self, "删除失败", "项目不存在或路径不在工作区中。")
            return

        if self.current_project_path and self.current_project_path.resolve() == project_path.resolve():
            self._clear_current_project()
        self.refresh_history()
        self.append_log(f"项目已删除：{project_path.name}")

    def _clear_current_project(self) -> None:
        self.current_project_path = None
        self.current_metadata = {}
        self.current_images = []
        self.selected_images.clear()
        self.current_page = 0
        self._update_frame_hint()
        self._render_gallery_page()

    def load_project(self, path: Path) -> None:
        loaded = self.project_service.load_project(path)
        self.current_project_path = Path(loaded["path"])
        self.current_metadata = loaded["metadata"]
        self.current_images = loaded["screenshots"]
        self.selected_images = {img for img in self.selected_images if img in self.current_images}
        self.current_page = 0
        self._update_frame_hint()
        self._update_selection_labels()
        self._render_gallery_page()
        self.tabs.setCurrentWidget(self.gallery_tab)
        self.append_log(f"已加载项目：{self.current_project_path.name}")

    def _update_frame_hint(self) -> None:
        if not self.current_project_path:
            self.frame_hint_label.setText("未加载项目。")
            return
        duration = _duration_text(self.current_metadata.get("duration"))
        video_path = self.current_metadata.get("video_path") or "(未下载或已删除)"
        self.frame_hint_label.setText(
            f"当前项目: {self.current_project_path.name}\n视频时长: {duration}\n原始视频: {video_path}"
        )

    def _on_parse_metadata(self) -> None:
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "缺少 URL", "请先输入视频链接。")
            return

        self.parse_button.setEnabled(False)
        self.metadata_label.setText("正在解析，请稍候...")

        def task(progress_callback):
            return self.download_service.fetch_metadata(url, self.settings, progress_callback)

        def on_result(metadata: dict[str, Any]) -> None:
            self.latest_metadata = metadata
            self.latest_metadata_url = url
            title = metadata.get("title", "未知标题")
            duration = _duration_text(metadata.get("duration"))
            is_live = bool(metadata.get("is_live"))
            self.metadata_label.setText(
                f"标题: {title}\n时长: {duration}\n直播: {'是' if is_live else '否'}\nURL: {url}"
            )
            self.download_button.setEnabled(True)
            if is_live:
                QMessageBox.information(self, "直播提醒", "检测到直播链接，将按 yt-dlp 默认能力尝试下载。")

        self._run_worker(task, on_result=on_result, on_finished=lambda: self.parse_button.setEnabled(True))

    def _on_download_video(self) -> None:
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "缺少 URL", "请先输入视频链接。")
            return

        self.download_button.setEnabled(False)

        def task(progress_callback):
            metadata: dict[str, Any] | None = (
                self.latest_metadata if self.latest_metadata_url == url and self.latest_metadata else None
            )
            if metadata is None:
                try:
                    metadata = self.download_service.fetch_metadata(url, self.settings, progress_callback)
                except Exception as metadata_error:
                    progress_callback(f"元数据解析失败，改为直接下载模式：{metadata_error}")
                    parsed = urlparse(url)
                    fallback_title = parsed.path.strip("/").split("/")[-1] or parsed.netloc or "untitled_video"
                    metadata = {"title": fallback_title, "duration": None, "is_live": False}

            title = str(metadata.get("title") or "untitled_video")
            project_dir = self.project_service.create_project(title, url, metadata)
            video_path = self.download_service.download_video(url, project_dir, self.settings, progress_callback)
            self.project_service.update_video_path(project_dir, video_path)
            return str(project_dir)

        def on_result(project_dir: str) -> None:
            self.refresh_history()
            self.load_project(Path(project_dir))
            QMessageBox.information(self, "下载完成", f"视频已下载到项目：{Path(project_dir).name}")

        self._run_worker(task, on_result=on_result, on_finished=lambda: self.download_button.setEnabled(True))

    def _on_import_local_video(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择本地视频",
            "",
            "Video Files (*.mp4 *.mkv *.mov *.avi *.flv *.webm *.m4v);;All Files (*.*)",
        )
        if not file_path:
            return

        source = Path(file_path)
        self.upload_button.setEnabled(False)

        def task(progress_callback):
            duration = self.local_video_service.probe_duration(source, progress_callback)
            metadata = {
                "title": source.stem,
                "duration": duration,
                "is_live": False,
            }
            project_dir = self.project_service.create_project(source.stem, str(source), metadata)
            copied_video = self.local_video_service.copy_to_project(source, project_dir, progress_callback)
            self.project_service.update_video_path(project_dir, copied_video)
            return {"project_dir": str(project_dir), "duration": duration}

        def on_result(payload: dict[str, Any]) -> None:
            duration = _duration_text(payload.get("duration"))
            project_dir = Path(payload["project_dir"])
            self.metadata_label.setText(
                f"标题: {source.stem}\n时长: {duration}\n直播: 否\n来源: 本地上传\n文件: {source}"
            )
            self.refresh_history()
            self.load_project(project_dir)
            QMessageBox.information(self, "上传完成", f"本地视频导入完成：{project_dir.name}")

        self._run_worker(task, on_result=on_result, on_finished=lambda: self.upload_button.setEnabled(True))

    def _on_extract_frames(self) -> None:
        if not self.current_project_path:
            QMessageBox.warning(self, "未选择项目", "请先从左侧历史项目中选择一个项目。")
            return
        video_path = self.current_metadata.get("video_path", "")
        if not video_path or not Path(video_path).exists():
            QMessageBox.warning(self, "缺少视频", "当前项目没有可用原始视频文件。")
            return

        interval = int(self.interval_spin.value())
        duration = self.current_metadata.get("duration")
        if duration:
            estimate = int(float(duration) / interval)
            if estimate > 1000:
                choice = QMessageBox.question(
                    self,
                    "容量预警",
                    f"预计将生成约 {estimate} 张截图，是否继续？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if choice != QMessageBox.StandardButton.Yes:
                    return

        self.extract_button.setEnabled(False)
        output_dir = self.current_project_path / "screenshots"
        clear_existing = self.clear_checkbox.isChecked()

        def task(progress_callback):
            return self.frame_service.extract_frames(
                video_path=video_path,
                output_dir=output_dir,
                interval_seconds=interval,
                clear_existing=clear_existing,
                progress_callback=progress_callback,
            )

        def on_result(count: int) -> None:
            self.append_log(f"当前项目抽帧完成，共 {count} 张。")
            self.load_project(self.current_project_path or Path())
            QMessageBox.information(self, "抽帧完成", f"已生成 {count} 张截图。")

        self._run_worker(task, on_result=on_result, on_finished=lambda: self.extract_button.setEnabled(True))

    def _on_delete_video(self) -> None:
        if not self.current_project_path:
            QMessageBox.warning(self, "未选择项目", "请先选择项目。")
            return

        confirm = QMessageBox.question(
            self,
            "确认删除",
            "删除后将仅保留截图，是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        deleted = self.project_service.delete_video(self.current_project_path)
        if deleted:
            self.append_log("原始视频已删除。")
            self.load_project(self.current_project_path)
        else:
            QMessageBox.information(self, "无操作", "未找到可删除的原始视频。")

    def _render_gallery_page(self) -> None:
        while self.gallery_grid.count():
            item = self.gallery_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        total = len(self.current_images)
        total_pages = math.ceil(total / self.page_size) if total else 0
        if total_pages:
            self.current_page = max(0, min(self.current_page, total_pages - 1))
        else:
            self.current_page = 0

        page_images = self._current_page_images()
        for index, image_path in enumerate(page_images):
            row = index // 4
            col = index % 4
            self.gallery_grid.addWidget(self._build_thumb(image_path), row, col)

        self.page_label.setText(f"页码 {self.current_page + 1 if total_pages else 0} / {total_pages}")
        self.prev_page_btn.setEnabled(self.current_page > 0)
        self.next_page_btn.setEnabled(total_pages > 0 and self.current_page < total_pages - 1)
        self._update_selection_labels()

    def _build_thumb(self, image_path: str) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)

        button = QToolButton()
        button.setCheckable(True)
        button.setChecked(image_path in self.selected_images)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        button.setText(Path(image_path).name)
        button.setIconSize(QSize(220, 124))

        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            fallback = QPixmap(220, 124)
            fallback.fill(Qt.GlobalColor.lightGray)
            button.setIcon(QIcon(fallback))
        else:
            thumb = pixmap.scaled(220, 124, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            button.setIcon(QIcon(thumb))

        button.toggled.connect(lambda checked, p=image_path: self._toggle_selected(p, checked))
        layout.addWidget(button)
        return wrapper

    def _toggle_selected(self, image_path: str, checked: bool) -> None:
        if checked:
            self.selected_images.add(image_path)
        else:
            self.selected_images.discard(image_path)
        self._update_selection_labels()

    def _update_selection_labels(self) -> None:
        total = len(self.current_images)
        selected = len(self.selected_images)
        self.gallery_info_label.setText(f"总计 {total} 张，已选 {selected} 张")

    def _current_page_images(self) -> list[str]:
        start = self.current_page * self.page_size
        end = start + self.page_size
        return self.current_images[start:end]

    def _select_current_page(self) -> None:
        for image in self._current_page_images():
            self.selected_images.add(image)
        self._render_gallery_page()

    def _clear_selection(self) -> None:
        self.selected_images.clear()
        self._render_gallery_page()

    def _on_open_screenshot_folder(self) -> None:
        if not self.current_project_path:
            QMessageBox.warning(self, "未选择项目", "请先从左侧历史项目中选择一个项目。")
            return
        screenshot_dir = self.current_project_path / "screenshots"
        if not screenshot_dir.exists():
            QMessageBox.warning(self, "目录不存在", "当前项目没有截图目录。")
            return

        try:
            if sys.platform.startswith("win"):
                os.startfile(str(screenshot_dir))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(screenshot_dir)])
            else:
                subprocess.Popen(["xdg-open", str(screenshot_dir)])
            self.append_log(f"已打开截图文件夹：{screenshot_dir}")
        except Exception as exc:
            QMessageBox.critical(self, "打开失败", f"无法打开截图文件夹：{exc}")

    def _on_search_selected_image(self) -> None:
        if not self.current_project_path:
            QMessageBox.warning(self, "未选择项目", "请先从左侧历史项目中选择一个项目。")
            return
        if not self.current_images:
            QMessageBox.warning(self, "无截图", "当前项目没有可搜索的截图。")
            return

        if len(self.selected_images) < 1:
            QMessageBox.warning(self, "未选择截图", "请先在画廊中选择至少 1 张截图。")
            return

        target_images = sorted(self.selected_images)
        self.search_button.setEnabled(False)
        self.append_log(f"开始提交插件搜索任务，共 {len(target_images)} 张截图。")

        def task(progress_callback):
            self.bridge_runtime_service.ensure_running(progress_callback)
            return self.plugin_search_service.queue_search_many(target_images, progress_callback)

        def on_result(result: dict[str, Any]) -> None:
            queued_count = int(result.get("queued_count") or 0)
            pending_count = result.get("pending_count", 0)
            self.append_log(
                f"搜索任务已提交，共 {queued_count} 张，待处理队列={pending_count}"
            )

        self._run_worker(task, on_result=on_result, on_finished=lambda: self.search_button.setEnabled(True))

    def _prev_page(self) -> None:
        if self.current_page > 0:
            self.current_page -= 1
            self._render_gallery_page()

    def _next_page(self) -> None:
        total_pages = math.ceil(len(self.current_images) / self.page_size) if self.current_images else 0
        if self.current_page + 1 < total_pages:
            self.current_page += 1
            self._render_gallery_page()

    def _load_settings_into_form(self) -> None:
        self.workspace_input.setText(str(self.settings.get("workspace_root", "")))
        self.cookie_checkbox.setChecked(bool(self.settings.get("use_cookie_auth", False)))
        browser = str(self.settings.get("cookie_browser", "chrome"))
        index = self.cookie_browser_combo.findText(browser)
        self.cookie_browser_combo.setCurrentIndex(index if index >= 0 else 0)
        self.cookie_file_input.setText(str(self.settings.get("cookie_file", "")))
        self.proxy_input.setText(str(self.settings.get("http_proxy", "")))
        self.download_format_input.setText(str(self.settings.get("download_format", "bv*+ba/b")))
        merge_output = str(self.settings.get("merge_output_format", "mp4"))
        merge_index = self.merge_output_combo.findText(merge_output)
        self.merge_output_combo.setCurrentIndex(merge_index if merge_index >= 0 else 0)
        self.extra_args_input.setText(str(self.settings.get("extra_yt_dlp_args", "")))
        self.impersonate_checkbox.setChecked(bool(self.settings.get("use_impersonate", True)))

    def _on_save_settings(self) -> None:
        workspace = self.workspace_input.text().strip()
        if not workspace:
            QMessageBox.warning(self, "路径为空", "工作区目录不能为空。")
            return

        self.settings = {
            "workspace_root": workspace,
            "use_cookie_auth": self.cookie_checkbox.isChecked(),
            "cookie_browser": self.cookie_browser_combo.currentText().strip(),
            "cookie_file": self.cookie_file_input.text().strip(),
            "http_proxy": self.proxy_input.text().strip(),
            "download_format": self.download_format_input.text().strip() or "bv*+ba/b",
            "merge_output_format": self.merge_output_combo.currentText().strip() or "mp4",
            "extra_yt_dlp_args": self.extra_args_input.text().strip(),
            "use_impersonate": self.impersonate_checkbox.isChecked(),
        }
        self.settings_service.save(self.settings)
        self.project_service.set_workspace_root(workspace)
        self.refresh_history()
        self.append_log("设置已保存。")
        QMessageBox.information(self, "保存成功", "设置已生效。")

    def _on_choose_workspace(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "选择工作区目录")
        if selected:
            self.workspace_input.setText(selected)

    def _on_choose_cookie_file(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "选择 Cookie 文件",
            "",
            "Text Files (*.txt *.cookie);;All Files (*.*)",
        )
        if selected:
            self.cookie_file_input.setText(selected)

    def _on_update_ytdlp(self) -> None:
        def task(progress_callback):
            return self.download_service.update_ytdlp(progress_callback)

        def on_result(output: str) -> None:
            self.append_log(output)
            QMessageBox.information(self, "更新完成", "yt-dlp 已更新。")

        self._run_worker(task, on_result=on_result)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.append_log("正在终止后台进程...")
        self.process_manager.kill_all()
        super().closeEvent(event)
