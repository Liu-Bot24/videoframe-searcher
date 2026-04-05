from __future__ import annotations

import logging
import math
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThreadPool, Qt, QSize, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QIcon, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QToolButton,
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


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Image Search Gallery")
        self.resize(1440, 980)

        self.logger = logging.getLogger("videoframe_searcher.ui")
        self.settings_service = SettingsService()
        self.settings = self.settings_service.load()

        self.process_manager = ProcessManager()
        self.bridge_runtime_service = BridgeRuntimeService(self.process_manager)
        self.plugin_search_service = PluginSearchService()
        self.thread_pool = QThreadPool.globalInstance()
        self._workers: set[Worker] = set()

        self.gallery_dir = self._resolve_gallery_dir()
        self.current_images: list[str] = []
        self.selected_images: set[str] = set()
        self.current_page = 0
        self.page_size = 60
        self._gallery_last_column_count = 0
        self._current_theme = "light"
        self._active_search_worker: Worker | None = None

        self._build_ui()
        self._gallery_relayout_timer = QTimer(self)
        self._gallery_relayout_timer.setSingleShot(True)
        self._gallery_relayout_timer.setInterval(80)
        self._gallery_relayout_timer.timeout.connect(self._reflow_gallery_layout)
        self._apply_theme()
        self._load_settings_into_form()
        self._refresh_gallery_images()
        self.append_log("应用已启动。")
        self.append_log(f"图库目录：{self.gallery_dir}")
        self.append_log(f"日志文件：{get_log_file()}")

    def _resolve_gallery_dir(self) -> Path:
        workspace_root = Path(str(self.settings.get("workspace_root") or "")).expanduser()
        if not str(workspace_root).strip():
            workspace_root = Path(__file__).resolve().parents[2] / "workspace"
        gallery_dir = workspace_root / "image_gallery"
        gallery_dir.mkdir(parents=True, exist_ok=True)
        return gallery_dir

    def _set_button_role(self, button: QPushButton, role: str) -> None:
        button.setProperty("role", role)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("AppRoot")
        root.setAcceptDrops(True)
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(16)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("MainSplit")
        root_layout.addWidget(splitter)
        self.setCentralWidget(root)
        self.setAcceptDrops(True)

        left_panel = QWidget()
        left_panel.setObjectName("HistoryPanel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(12)

        title = QLabel("图库工作台")
        title.setObjectName("HistoryTitle")
        left_layout.addWidget(title)

        intro = QLabel(
            "直接把图片拖进窗口，或导入图片/文件夹。\n"
            "原项目的搜图插件和桥接服务会继续复用。"
        )
        intro.setWordWrap(True)
        intro.setObjectName("GalleryInfoLabel")
        left_layout.addWidget(intro)

        path_group = QGroupBox("当前图库")
        path_layout = QVBoxLayout(path_group)
        self.gallery_path_label = QLabel(str(self.gallery_dir))
        self.gallery_path_label.setWordWrap(True)
        self.gallery_path_label.setObjectName("GalleryInfoLabel")
        path_layout.addWidget(self.gallery_path_label)
        self.gallery_summary_label = QLabel("总计 0 张图片")
        self.gallery_summary_label.setObjectName("GalleryInfoLabel")
        path_layout.addWidget(self.gallery_summary_label)
        left_layout.addWidget(path_group)

        quick_group = QGroupBox("快捷操作")
        quick_layout = QVBoxLayout(quick_group)
        quick_layout.setSpacing(8)

        self.import_images_button = QPushButton("导入图片")
        self._set_button_role(self.import_images_button, "primary")
        self.import_images_button.clicked.connect(self._on_import_images)
        quick_layout.addWidget(self.import_images_button)

        self.import_folder_button = QPushButton("导入文件夹")
        self._set_button_role(self.import_folder_button, "secondary")
        self.import_folder_button.clicked.connect(self._on_import_folder)
        quick_layout.addWidget(self.import_folder_button)

        self.open_gallery_button = QPushButton("打开图库文件夹")
        self._set_button_role(self.open_gallery_button, "secondary")
        self.open_gallery_button.clicked.connect(self._on_open_gallery_folder)
        quick_layout.addWidget(self.open_gallery_button)

        self.refresh_button = QPushButton("刷新图库")
        self._set_button_role(self.refresh_button, "secondary")
        self.refresh_button.clicked.connect(self._refresh_gallery_images)
        quick_layout.addWidget(self.refresh_button)

        left_layout.addWidget(quick_group)
        left_layout.addStretch(1)
        splitter.addWidget(left_panel)

        right_panel = QWidget()
        right_panel.setObjectName("ContentPanel")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(14, 14, 14, 14)
        right_layout.setSpacing(12)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("MainTabs")
        self.theme_toggle_btn = QPushButton("深色模式")
        self.theme_toggle_btn.setObjectName("ThemeToggleBtn")
        self._set_button_role(self.theme_toggle_btn, "secondary")
        self.theme_toggle_btn.setMaximumWidth(142)
        self.theme_toggle_btn.clicked.connect(self._on_theme_toggle_clicked)
        self.tabs.setCornerWidget(self.theme_toggle_btn, Qt.Corner.TopRightCorner)
        right_layout.addWidget(self.tabs, 5)

        self.gallery_tab = self._build_gallery_tab()
        self.settings_tab = self._build_settings_tab()
        self.tabs.addTab(self.gallery_tab, "图片画廊")
        self.tabs.addTab(self.settings_tab, "设置")
        self.tabs.currentChanged.connect(self._on_tab_changed)

        log_title = QLabel("运行日志")
        log_title.setObjectName("LogTitle")
        right_layout.addWidget(log_title)
        self.log_output = QPlainTextEdit()
        self.log_output.setObjectName("LogOutput")
        self.log_output.setReadOnly(True)
        self.log_output.setMinimumHeight(180)
        right_layout.addWidget(self.log_output, 2)

        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)
        splitter.setSizes([430, 1000])

        refresh_action = QAction("刷新图库", self)
        refresh_action.triggered.connect(self._refresh_gallery_images)
        self.addAction(refresh_action)

    def _build_gallery_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        tools_layout = QHBoxLayout()

        import_btn = QPushButton("导入图片")
        self._set_button_role(import_btn, "secondary")
        import_btn.clicked.connect(self._on_import_images)
        tools_layout.addWidget(import_btn)

        import_folder_btn = QPushButton("导入文件夹")
        self._set_button_role(import_folder_btn, "secondary")
        import_folder_btn.clicked.connect(self._on_import_folder)
        tools_layout.addWidget(import_folder_btn)

        select_all_btn = QPushButton("全选全部")
        self._set_button_role(select_all_btn, "secondary")
        select_all_btn.clicked.connect(self._select_all_images)
        tools_layout.addWidget(select_all_btn)

        select_page_btn = QPushButton("全选当前页")
        self._set_button_role(select_page_btn, "secondary")
        select_page_btn.clicked.connect(self._select_current_page)
        tools_layout.addWidget(select_page_btn)

        clear_btn = QPushButton("清空选择")
        self._set_button_role(clear_btn, "secondary")
        clear_btn.clicked.connect(self._clear_selection)
        tools_layout.addWidget(clear_btn)

        delete_btn = QPushButton("删除图片")
        self._set_button_role(delete_btn, "danger")
        delete_btn.clicked.connect(self._on_delete_selected_images)
        tools_layout.addWidget(delete_btn)

        open_folder_btn = QPushButton("打开图库文件夹")
        self._set_button_role(open_folder_btn, "secondary")
        open_folder_btn.clicked.connect(self._on_open_gallery_folder)
        tools_layout.addWidget(open_folder_btn)

        tools_layout.addStretch(1)
        self.gallery_info_label = QLabel("总计 0 张，已选 0 张")
        self.gallery_info_label.setObjectName("GalleryInfoLabel")
        tools_layout.addWidget(self.gallery_info_label)
        layout.addLayout(tools_layout)

        search_row = QHBoxLayout()
        self.gallery_search_hint_label = QLabel("请先安装并开启 Local Lens Bridge 插件，再选中图片点击右侧“以图搜图”")
        self.gallery_search_hint_label.setObjectName("GalleryInfoLabel")
        self.gallery_search_hint_label.setWordWrap(False)
        search_row.addWidget(self.gallery_search_hint_label, 1)
        self.search_button = QPushButton("以图搜图")
        self._set_button_role(self.search_button, "primary")
        self.search_button.clicked.connect(self._on_search_selected_images)
        self.search_button.setMinimumHeight(40)
        self.search_button.setMinimumWidth(108)
        self.search_button.setMaximumWidth(128)
        search_row.addWidget(self.search_button, 0)
        self.clear_queue_button = QToolButton()
        self.clear_queue_button.setObjectName("QueueClearButton")
        self.clear_queue_button.setText("🧹")
        self.clear_queue_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_queue_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.clear_queue_button.setToolTip("清空尚未开始的搜索队列，不会中断浏览器里当前已经开始的搜索。")
        self.clear_queue_button.clicked.connect(self._on_clear_search_queue)
        search_row.addWidget(self.clear_queue_button, 0)
        layout.addLayout(search_row)

        self.gallery_scroll = QScrollArea()
        self.gallery_scroll.setObjectName("GalleryScroll")
        self.gallery_scroll.setWidgetResizable(True)
        self.gallery_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.gallery_container = QWidget()
        self.gallery_container.setObjectName("GalleryContainer")
        self.gallery_grid = QGridLayout(self.gallery_container)
        self.gallery_grid.setContentsMargins(8, 8, 8, 8)
        self.gallery_grid.setSpacing(12)
        self.gallery_grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.gallery_scroll.setWidget(self.gallery_container)
        layout.addWidget(self.gallery_scroll, 1)

        page_layout = QHBoxLayout()
        self.prev_page_btn = QPushButton("上一页")
        self._set_button_role(self.prev_page_btn, "secondary")
        self.prev_page_btn.clicked.connect(self._prev_page)
        self.next_page_btn = QPushButton("下一页")
        self._set_button_role(self.next_page_btn, "secondary")
        self.next_page_btn.clicked.connect(self._next_page)
        self.page_label = QLabel("页码 0 / 0")
        self.page_label.setObjectName("PageLabel")
        page_layout.addWidget(self.prev_page_btn)
        page_layout.addWidget(self.next_page_btn)
        page_layout.addWidget(self.page_label)
        page_layout.addStretch(1)
        layout.addLayout(page_layout)
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
        self._set_button_role(browse_btn, "secondary")
        browse_btn.clicked.connect(self._on_choose_workspace)
        box_layout.addWidget(browse_btn, 0, 2)

        tip_label = QLabel(
            "图片会统一保存在 workspace/image_gallery/。\n"
            "修改工作区后，画廊目录会随之切换。"
        )
        tip_label.setObjectName("GalleryInfoLabel")
        tip_label.setWordWrap(True)
        box_layout.addWidget(tip_label, 1, 0, 1, 3)

        save_btn = QPushButton("保存设置")
        self._set_button_role(save_btn, "primary")
        save_btn.clicked.connect(self._on_save_settings)
        box_layout.addWidget(save_btn, 2, 0, 1, 3)

        plugin_box = QGroupBox("插件提示")
        plugin_layout = QVBoxLayout(plugin_box)
        plugin_label = QLabel(
            "浏览器扩展名称：Local Lens Bridge\n"
            "如果点击“以图搜图”没有反应，先打开 Chrome 插件弹窗确认它处于开启状态。"
        )
        plugin_label.setObjectName("GalleryInfoLabel")
        plugin_label.setWordWrap(True)
        plugin_layout.addWidget(plugin_label)

        layout.addWidget(box)
        layout.addWidget(plugin_box)
        layout.addStretch(1)
        return tab

    def _get_light_theme(self) -> str:
        return """
            #AppRoot {
                background: #F3F4F6;
                color: #1E293B;
                font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
                font-size: 13px;
            }
            #MainSplit::handle {
                background: #E2E8F0;
                width: 8px;
                border-radius: 4px;
            }
            #HistoryPanel, #ContentPanel {
                background: #FFFFFF;
                border-radius: 10px;
                border: 1px solid #E2E8F0;
            }
            #HistoryTitle, #LogTitle {
                color: #0F172A;
                font-size: 12px;
                font-weight: 600;
                letter-spacing: 0.5px;
                padding-left: 4px;
            }
            #MainTabs {
                border: none;
                background: transparent;
            }
            #MainTabs::pane {
                border: none;
                background: transparent;
            }
            #MainTabs QTabBar::tab {
                min-width: 112px;
                padding: 9px 16px;
                margin-right: 4px;
                margin-bottom: 12px;
                border-radius: 6px;
                border: none;
                color: #64748B;
                background: transparent;
                font-weight: 500;
                font-size: 13px;
            }
            #MainTabs QTabBar::tab:hover {
                color: #1E293B;
                background: #F1F5F9;
            }
            #MainTabs QTabBar::tab:selected {
                color: #0F172A;
                font-weight: 600;
                border-bottom: 2px solid #0F172A;
            }
            #AppRoot QGroupBox {
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 10px;
                margin-top: 10px;
                padding: 18px 16px 16px 16px;
                font-weight: 600;
                color: #0F172A;
            }
            #AppRoot QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 12px;
                top: 0px;
                padding: 0 8px;
                color: #64748B;
                font-size: 11px;
                font-weight: 600;
                background: #FFFFFF;
            }
            #AppRoot QLabel {
                color: #1E293B;
                font-size: 13px;
            }
            #AppRoot QLineEdit, #AppRoot QPlainTextEdit {
                background: #F8FAFC;
                border: 1px solid #E2E8F0;
                border-radius: 6px;
                padding: 8px 12px;
                min-height: 34px;
                color: #1E293B;
            }
            #AppRoot QLineEdit:focus, #AppRoot QPlainTextEdit:focus {
                border: 2px solid #0F172A;
                background: #FFFFFF;
            }
            #AppRoot QPushButton {
                min-height: 36px;
                padding: 0 16px;
                border-radius: 8px;
                border: 1px solid #E2E8F0;
                background: #FFFFFF;
                color: #1E293B;
                font-weight: 600;
            }
            #AppRoot QPushButton:hover {
                background: #F8FAFC;
                border-color: #CBD5E1;
            }
            #AppRoot QPushButton[role="primary"] {
                background: #0F172A;
                color: #FFFFFF;
                border: none;
            }
            #AppRoot QPushButton[role="primary"]:hover {
                background: #1E293B;
            }
            #AppRoot QPushButton[role="danger"] {
                background: #FFF1F2;
                color: #BE123C;
                border: 1px solid #FDA4AF;
            }
            #AppRoot QPushButton[role="danger"]:hover {
                background: #FFE4E6;
            }
            #QueueClearButton {
                min-width: 40px;
                max-width: 40px;
                min-height: 40px;
                border-radius: 8px;
                border: 1px solid #E2E8F0;
                background: #FFFFFF;
                color: #64748B;
                font-size: 18px;
                font-weight: 600;
            }
            #QueueClearButton:hover {
                background: #F8FAFC;
                border-color: #CBD5E1;
                color: #0F172A;
            }
            #ThemeToggleBtn {
                min-width: 110px;
            }
            #LogOutput {
                background: #F8FAFC;
                border: 1px solid #E2E8F0;
                border-radius: 10px;
                padding: 10px;
                color: #334155;
            }
            #GalleryInfoLabel, #PageLabel {
                color: #64748B;
                font-size: 12px;
                font-weight: 600;
            }
            #GalleryScroll {
                border: none;
                background: transparent;
            }
            #GalleryContainer {
                background: #F8FAFC;
                border: 1px solid #E2E8F0;
                border-radius: 10px;
            }
            #FrameThumb {
                min-width: 220px;
                max-width: 220px;
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 10px;
                padding: 10px 10px 12px 10px;
                color: #334155;
                font-size: 12px;
                font-weight: 600;
                text-align: center;
            }
            #FrameThumb:hover {
                background: #F8FAFC;
                border-color: #CBD5E1;
            }
            #FrameThumb:checked {
                background: #EEF2FF;
                border: 2px solid #0F172A;
                color: #0F172A;
            }
        """

    def _get_dark_theme(self) -> str:
        return """
            #AppRoot {
                background: #0F172A;
                color: #E2E8F0;
                font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
                font-size: 13px;
            }
            #MainSplit::handle {
                background: #1E293B;
                width: 8px;
                border-radius: 4px;
            }
            #HistoryPanel, #ContentPanel {
                background: #111827;
                border-radius: 10px;
                border: 1px solid #1F2937;
            }
            #HistoryTitle, #LogTitle {
                color: #F8FAFC;
                font-size: 12px;
                font-weight: 600;
                padding-left: 4px;
            }
            #MainTabs {
                border: none;
                background: transparent;
            }
            #MainTabs::pane {
                border: none;
                background: transparent;
            }
            #MainTabs QTabBar::tab {
                min-width: 112px;
                padding: 9px 16px;
                margin-right: 4px;
                margin-bottom: 12px;
                border-radius: 6px;
                border: none;
                color: #94A3B8;
                background: transparent;
                font-weight: 500;
                font-size: 13px;
            }
            #MainTabs QTabBar::tab:hover {
                color: #F8FAFC;
                background: #1F2937;
            }
            #MainTabs QTabBar::tab:selected {
                color: #F8FAFC;
                font-weight: 600;
                border-bottom: 2px solid #F8FAFC;
            }
            #AppRoot QGroupBox {
                background: #111827;
                border: 1px solid #1F2937;
                border-radius: 10px;
                margin-top: 10px;
                padding: 18px 16px 16px 16px;
                font-weight: 600;
                color: #F8FAFC;
            }
            #AppRoot QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 12px;
                top: 0px;
                padding: 0 8px;
                color: #94A3B8;
                font-size: 11px;
                font-weight: 600;
                background: #111827;
            }
            #AppRoot QLabel {
                color: #E2E8F0;
                font-size: 13px;
            }
            #AppRoot QLineEdit, #AppRoot QPlainTextEdit {
                background: #0F172A;
                border: 1px solid #1F2937;
                border-radius: 6px;
                padding: 8px 12px;
                min-height: 34px;
                color: #E2E8F0;
            }
            #AppRoot QLineEdit:focus, #AppRoot QPlainTextEdit:focus {
                border: 2px solid #CBD5E1;
                background: #111827;
            }
            #AppRoot QPushButton {
                min-height: 36px;
                padding: 0 16px;
                border-radius: 8px;
                border: 1px solid #334155;
                background: #1F2937;
                color: #E2E8F0;
                font-weight: 600;
            }
            #AppRoot QPushButton:hover {
                background: #334155;
            }
            #AppRoot QPushButton[role="primary"] {
                background: #F8FAFC;
                color: #0F172A;
                border: none;
            }
            #AppRoot QPushButton[role="primary"]:hover {
                background: #E2E8F0;
            }
            #AppRoot QPushButton[role="danger"] {
                background: #3F1D2E;
                color: #FDA4AF;
                border: 1px solid #7F1D1D;
            }
            #AppRoot QPushButton[role="danger"]:hover {
                background: #4C1D2B;
            }
            #QueueClearButton {
                min-width: 40px;
                max-width: 40px;
                min-height: 40px;
                border-radius: 8px;
                border: 1px solid #334155;
                background: #1F2937;
                color: #CBD5E1;
                font-size: 18px;
                font-weight: 600;
            }
            #QueueClearButton:hover {
                background: #334155;
                border-color: #475569;
                color: #F8FAFC;
            }
            #LogOutput {
                background: #0B1220;
                border: 1px solid #1F2937;
                border-radius: 10px;
                padding: 10px;
                color: #CBD5E1;
            }
            #GalleryInfoLabel, #PageLabel {
                color: #94A3B8;
                font-size: 12px;
                font-weight: 600;
            }
            #GalleryScroll {
                border: none;
                background: transparent;
            }
            #GalleryContainer {
                background: #0F172A;
                border: 1px solid #1F2937;
                border-radius: 10px;
            }
            #FrameThumb {
                min-width: 220px;
                max-width: 220px;
                background: #111827;
                border: 1px solid #1F2937;
                border-radius: 10px;
                padding: 10px 10px 12px 10px;
                color: #CBD5E1;
                font-size: 12px;
                font-weight: 600;
                text-align: center;
            }
            #FrameThumb:hover {
                background: #1F2937;
            }
            #FrameThumb:checked {
                background: #1E293B;
                border: 2px solid #F8FAFC;
                color: #F8FAFC;
            }
        """

    def _apply_theme(self) -> None:
        if self._current_theme == "dark":
            self.setStyleSheet(self._get_dark_theme())
            self.theme_toggle_btn.setText("浅色模式")
        else:
            self.setStyleSheet(self._get_light_theme())
            self.theme_toggle_btn.setText("深色模式")

    def _on_theme_toggle_clicked(self) -> None:
        self._current_theme = "dark" if self._current_theme == "light" else "light"
        self._apply_theme()

    def _on_tab_changed(self, _index: int) -> None:
        if self.tabs.currentWidget() == self.gallery_tab:
            self._reflow_gallery_layout(force=True)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._schedule_gallery_relayout()

    def dragEnterEvent(self, event) -> None:
        if self._extract_paths_from_mime_data(event.mimeData()):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        if self._extract_paths_from_mime_data(event.mimeData()):
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event) -> None:
        paths = self._extract_paths_from_mime_data(event.mimeData())
        if paths:
            self._import_paths(paths)
            event.acceptProposedAction()
            return
        event.ignore()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.process_manager.kill_all()
        self.thread_pool.waitForDone(3000)
        super().closeEvent(event)

    def _schedule_gallery_relayout(self) -> None:
        if hasattr(self, "_gallery_relayout_timer"):
            self._gallery_relayout_timer.start()

    def _current_gallery_column_count(self) -> int:
        if not hasattr(self, "gallery_scroll") or self.gallery_scroll.viewport() is None:
            return 4
        available_width = max(0, self.gallery_scroll.viewport().width())
        thumb_width = 220
        cell_padding = 24
        if available_width <= 0:
            return 4
        return max(1, available_width // (thumb_width + cell_padding))

    def _reflow_gallery_layout(self, force: bool = False) -> None:
        if self.tabs.currentWidget() != self.gallery_tab and not force:
            return
        column_count = self._current_gallery_column_count()
        if not force and column_count == self._gallery_last_column_count:
            return
        self._gallery_last_column_count = column_count
        self._render_gallery_page()

    def _run_worker(self, task, on_result=None, on_finished=None, on_error=None) -> Worker:
        worker = Worker(task)
        self._workers.add(worker)
        worker.signals.progress.connect(self.append_log)
        if on_result:
            worker.signals.result.connect(on_result)
        worker.signals.error.connect(lambda err: self._handle_worker_error(err, on_error))

        def _cleanup() -> None:
            self._workers.discard(worker)
            if on_finished:
                on_finished()

        worker.signals.finished.connect(_cleanup)
        self.thread_pool.start(worker)
        return worker

    def _handle_worker_error(self, traceback_text: str, on_error=None) -> None:
        self.logger.error("后台任务失败：\n%s", traceback_text)
        if on_error:
            on_error(traceback_text)
            return

        summary = "未知错误"
        if traceback_text:
            lines = [line.strip() for line in traceback_text.splitlines() if line.strip()]
            if lines:
                summary = lines[-1]
                if ": " in summary:
                    summary = summary.split(": ", 1)[1]

        soft_errors = (
            "插件状态尚未同步",
            "Chrome 插件处于关闭状态",
            "无法连接本地桥接服务",
            "未找到可用于插件联动的 Chromium 浏览器",
        )
        if any(token in summary for token in soft_errors):
            self.gallery_search_hint_label.setText(summary)
            self.append_log(summary)
            return

        self.append_log(summary)
        QMessageBox.critical(self, "执行失败", f"{summary}\n\n详细日志：{get_log_file()}")

    def append_log(self, message: str) -> None:
        text = str(message or "").strip()
        if not text:
            return
        self.logger.info(text)
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_output.appendPlainText(f"[{stamp}] {text}")

    def _extract_paths_from_mime_data(self, mime_data) -> list[str]:
        paths: list[str] = []
        if not mime_data or not mime_data.hasUrls():
            return paths
        for url in mime_data.urls():
            if not url.isLocalFile():
                continue
            candidate = Path(url.toLocalFile())
            if candidate.is_file() and candidate.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES:
                paths.append(str(candidate))
        return paths

    def _load_settings_into_form(self) -> None:
        self.workspace_input.setText(str(self.settings.get("workspace_root", "")))

    def _on_choose_workspace(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择工作区目录", str(Path.home()))
        if directory:
            self.workspace_input.setText(directory)

    def _on_save_settings(self) -> None:
        workspace = self.workspace_input.text().strip()
        if not workspace:
            QMessageBox.warning(self, "缺少目录", "请先填写工作区目录。")
            return
        self.settings["workspace_root"] = workspace
        self.settings_service.save(self.settings)
        self.gallery_dir = self._resolve_gallery_dir()
        self.gallery_path_label.setText(str(self.gallery_dir))
        self._refresh_gallery_images()
        self.append_log(f"工作区已更新：{workspace}")

    def _list_gallery_images(self) -> list[str]:
        self.gallery_dir.mkdir(parents=True, exist_ok=True)
        images: list[str] = []
        for path in self.gallery_dir.iterdir():
            if not path.is_file():
                continue
            if path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
                continue
            images.append(str(path))
        images.sort(key=lambda item: Path(item).name.lower())
        return images

    def _refresh_gallery_images(self) -> None:
        self.current_images = self._list_gallery_images()
        self.selected_images = {path for path in self.selected_images if path in self.current_images}
        self.current_page = 0
        self.gallery_path_label.setText(str(self.gallery_dir))
        self.gallery_summary_label.setText(f"总计 {len(self.current_images)} 张图片")
        self._render_gallery_page()

    def _copy_image_to_gallery(self, source: Path) -> Path:
        if source.resolve().parent == self.gallery_dir.resolve():
            return source.resolve()
        safe_stem = sanitize_filename(source.stem)
        suffix = source.suffix.lower() or ".jpg"
        target = self.gallery_dir / f"{safe_stem}{suffix}"
        index = 2
        while target.exists():
            target = self.gallery_dir / f"{safe_stem}_{index}{suffix}"
            index += 1
        shutil.copy2(source, target)
        return target

    def _import_paths(self, raw_paths: list[str]) -> None:
        imported: list[str] = []
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
            imported.append(str(target))

        if imported:
            self.selected_images = set(imported)
            self.current_images = self._list_gallery_images()
            self.current_page = 0
            self.gallery_summary_label.setText(f"总计 {len(self.current_images)} 张图片")
            self._render_gallery_page()
            self.append_log(f"已导入 {len(imported)} 张图片。")
        if skipped:
            preview = "\n".join(skipped[:6])
            extra = "\n..." if len(skipped) > 6 else ""
            QMessageBox.warning(self, "部分文件未导入", f"以下文件无法导入：\n{preview}{extra}")

    def _on_import_images(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择图片",
            str(Path.home()),
            "Images (*.jpg *.jpeg *.png *.webp *.bmp *.gif)",
        )
        if files:
            self._import_paths(files)

    def _on_import_folder(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择图片文件夹", str(Path.home()))
        if not directory:
            return
        paths = [
            str(path)
            for path in Path(directory).iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
        ]
        if not paths:
            QMessageBox.information(self, "没有图片", "这个文件夹里没有可导入的图片。")
            return
        self._import_paths(paths)

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
        column_count = self._current_gallery_column_count()
        self._gallery_last_column_count = column_count
        for index, image_path in enumerate(page_images):
            row = index // column_count
            col = index % column_count
            self.gallery_grid.addWidget(self._build_thumb(image_path), row, col)

        self.page_label.setText(f"页码 {self.current_page + 1 if total_pages else 0} / {total_pages}")
        self.prev_page_btn.setEnabled(self.current_page > 0)
        self.next_page_btn.setEnabled(total_pages > 0 and self.current_page < total_pages - 1)
        self._update_selection_labels()

    def _build_cover_pixmap(self, source: QPixmap, width: int, height: int) -> QPixmap:
        if source.isNull():
            fallback = QPixmap(width, height)
            fallback.fill(Qt.GlobalColor.lightGray)
            return fallback
        scaled = source.scaled(
            width,
            height,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        if scaled.isNull():
            fallback = QPixmap(width, height)
            fallback.fill(Qt.GlobalColor.lightGray)
            return fallback
        x = max(0, (scaled.width() - width) // 2)
        y = max(0, (scaled.height() - height) // 2)
        return scaled.copy(x, y, width, height)

    def _build_thumb(self, image_path: str) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)

        button = QToolButton()
        button.setObjectName("FrameThumb")
        button.setCheckable(True)
        button.setChecked(image_path in self.selected_images)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        button.setText(Path(image_path).name)
        button.setIconSize(QSize(220, 124))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.DelayedPopup)

        pixmap = QPixmap(image_path)
        thumb = self._build_cover_pixmap(pixmap, 220, 124)
        button.setIcon(QIcon(thumb))

        button.toggled.connect(lambda checked, p=image_path: self._toggle_selected(p, checked))
        button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        button.customContextMenuRequested.connect(
            lambda pos, p=image_path, btn=button: self._on_thumb_context_menu(pos, p, btn)
        )
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
        self.gallery_summary_label.setText(f"总计 {total} 张图片")

    def _current_page_images(self) -> list[str]:
        start = self.current_page * self.page_size
        end = start + self.page_size
        return self.current_images[start:end]

    def _select_all_images(self) -> None:
        self.selected_images = set(self.current_images)
        self._render_gallery_page()

    def _select_current_page(self) -> None:
        for image in self._current_page_images():
            self.selected_images.add(image)
        self._render_gallery_page()

    def _clear_selection(self) -> None:
        self.selected_images.clear()
        self._render_gallery_page()

    def _prev_page(self) -> None:
        if self.current_page > 0:
            self.current_page -= 1
            self._render_gallery_page()

    def _next_page(self) -> None:
        total_pages = math.ceil(len(self.current_images) / self.page_size) if self.current_images else 0
        if self.current_page + 1 < total_pages:
            self.current_page += 1
            self._render_gallery_page()

    def _on_open_gallery_folder(self) -> None:
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(self.gallery_dir))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(self.gallery_dir)])
            else:
                subprocess.Popen(["xdg-open", str(self.gallery_dir)])
            self.append_log(f"已打开图库文件夹：{self.gallery_dir}")
        except Exception as exc:
            QMessageBox.critical(self, "打开失败", f"无法打开图库文件夹：{exc}")

    def _delete_image_paths(self, paths: list[str]) -> None:
        targets: list[Path] = []
        for path in paths:
            text = str(path or "").strip()
            if not text:
                continue
            candidate = Path(text)
            if candidate.exists() and candidate.is_file():
                targets.append(candidate)
        if not targets:
            QMessageBox.information(self, "无可删除项", "未找到可删除的图片文件。")
            return

        confirm = QMessageBox.question(
            self,
            "确认删除图片",
            f"将删除 {len(targets)} 张图片，是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        deleted = 0
        failed: list[str] = []
        for target in targets:
            try:
                target.unlink()
                deleted += 1
            except Exception as exc:
                failed.append(f"{target.name}: {exc}")

        if deleted:
            for target in targets:
                self.selected_images.discard(str(target))
            self._refresh_gallery_images()
            self.append_log(f"已删除图片 {deleted} 张。")

        if failed:
            preview = "\n".join(failed[:6])
            extra = "\n..." if len(failed) > 6 else ""
            QMessageBox.warning(self, "部分删除失败", f"以下文件删除失败：\n{preview}{extra}")

    def _on_delete_selected_images(self) -> None:
        if not self.selected_images:
            QMessageBox.warning(self, "未选择图片", "请先选择要删除的图片。")
            return
        self._delete_image_paths(sorted(self.selected_images))

    def _on_thumb_context_menu(self, pos, image_path: str, button: QToolButton) -> None:
        menu = QMenu(self)
        delete_action = menu.addAction("删除图片")
        open_action = menu.addAction("在文件夹中显示")
        selected = menu.exec(button.mapToGlobal(pos))
        if selected == delete_action:
            self._delete_image_paths([image_path])
        elif selected == open_action:
            self._reveal_image(Path(image_path))

    def _reveal_image(self, path: Path) -> None:
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(path.parent))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path.parent)])
        except Exception as exc:
            QMessageBox.warning(self, "打开失败", f"无法定位图片：{exc}")

    def _on_search_selected_images(self) -> None:
        if not self.current_images:
            QMessageBox.warning(self, "无图片", "当前图库没有可搜索的图片。")
            return
        if len(self.selected_images) < 1:
            QMessageBox.warning(self, "未选择图片", "请先在画廊中选择至少 1 张图片。")
            return

        target_images = sorted(self.selected_images)
        self.search_button.setEnabled(False)
        self.gallery_search_hint_label.setText("正在检查插件状态并提交任务…")
        self.append_log(f"开始提交插件搜索任务，共 {len(target_images)} 张图片。")

        def task(progress_callback, cancel_event):
            self.bridge_runtime_service.ensure_running(progress_callback)
            return self.plugin_search_service.queue_search_many(
                target_images,
                progress_callback,
                cancel_event=cancel_event,
            )

        def on_result(result: dict[str, Any]) -> None:
            queued_count = int(result.get("queued_count") or 0)
            pending_count = result.get("pending_count", 0)
            if result.get("cancelled"):
                self.gallery_search_hint_label.setText("已停止继续排队。当前已开始的搜索不会中断。")
                self.append_log(f"已停止继续提交搜索任务，本次实际入队 {queued_count} 张，待处理队列={pending_count}")
                return
            self.gallery_search_hint_label.setText("任务已提交。浏览器会继续处理队列。")
            self.append_log(f"搜索任务已提交，共 {queued_count} 张，待处理队列={pending_count}")

        def on_finished() -> None:
            self.search_button.setEnabled(True)
            self._active_search_worker = None

        self._active_search_worker = self._run_worker(
            task,
            on_result=on_result,
            on_finished=on_finished,
        )

    def _on_clear_search_queue(self) -> None:
        self.clear_queue_button.setEnabled(False)
        self.gallery_search_hint_label.setText("正在清空排队中的搜索队列…")
        self.append_log("正在清空排队中的搜索队列。")

        had_active_submission = self._active_search_worker is not None
        if self._active_search_worker is not None:
            self._active_search_worker.cancel()

        def task(progress_callback):
            if had_active_submission:
                time.sleep(0.35)
            return self.plugin_search_service.clear_queue(progress_callback)

        def on_result(result: dict[str, Any]) -> None:
            cleared_count = int(result.get("cleared_count") or 0)
            pending_count = int(result.get("pending_count") or 0)
            if cleared_count > 0:
                self.gallery_search_hint_label.setText(
                    f"已清空排队中的搜索队列 {cleared_count} 项。当前已开始的搜索不会中断。"
                )
                self.append_log(f"已清空排队中的搜索队列 {cleared_count} 项，剩余待处理队列={pending_count}")
                return
            self.gallery_search_hint_label.setText("当前没有待清空的搜索队列。")
            self.append_log("当前没有待清空的搜索队列。")

        def on_finished() -> None:
            self.clear_queue_button.setEnabled(True)

        self._run_worker(
            task,
            on_result=on_result,
            on_finished=on_finished,
        )
