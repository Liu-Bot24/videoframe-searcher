from __future__ import annotations

import logging
import math
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

import requests
from PySide6.QtCore import QThreadPool, Qt, QSize, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QFontMetrics,
    QIcon,
    QImageReader,
    QKeySequence,
    QPainter,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QStyle,
    QMenu,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from videoframe_searcher.logging_config import get_log_file
from videoframe_searcher.services.download_service import DownloadService, REQUEST_UA
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


ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets"
APP_ICON_PATH = ASSETS_DIR / ("app_icon.ico" if sys.platform.startswith("win") else "app_icon.png")
if not APP_ICON_PATH.exists():
    APP_ICON_PATH = ASSETS_DIR / ("app_icon.png" if APP_ICON_PATH.suffix == ".ico" else "app_icon.ico")
CHEVRON_DARK_ICON_PATH = ASSETS_DIR / "chevron_down_dark.png"
CHEVRON_LIGHT_ICON_PATH = ASSETS_DIR / "chevron_down_light.png"
SPIN_UP_DARK_ICON_PATH = ASSETS_DIR / "spin_up_dark.png"
SPIN_DOWN_DARK_ICON_PATH = ASSETS_DIR / "spin_down_dark.png"
SPIN_UP_LIGHT_ICON_PATH = ASSETS_DIR / "spin_up_light.png"
SPIN_DOWN_LIGHT_ICON_PATH = ASSETS_DIR / "spin_down_light.png"
CHECK_MARK_ICON_PATH = ASSETS_DIR / "check_mark_white.png"


def _qss_path(path: Path) -> str:
    return path.as_posix()


class MarqueeLabel(QLabel):
    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self._offset = 0
        self._gap = "   "
        self._timer = QTimer(self)
        self._timer.setInterval(180)
        self._timer.timeout.connect(self._advance)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setWordWrap(False)
        self.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.set_marquee_text(text)

    def set_marquee_text(self, text: str) -> None:
        self._full_text = " ".join(str(text or "").split())
        self._offset = 0
        self._render()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._render()

    def _advance(self) -> None:
        if not self._full_text:
            return
        scroll_text = self._full_text + self._gap
        if not scroll_text:
            return
        self._offset = (self._offset + 1) % len(scroll_text)
        self._render()

    def _fit_text(self, text: str, width: int, metrics: QFontMetrics) -> str:
        output = ""
        for ch in text:
            candidate = output + ch
            if metrics.horizontalAdvance(candidate) > width:
                break
            output = candidate
        return output

    def _render(self) -> None:
        if not self._full_text:
            if self._timer.isActive():
                self._timer.stop()
            super().setText("")
            return

        width = max(12, self.contentsRect().width())
        metrics = QFontMetrics(self.font())
        if metrics.horizontalAdvance(self._full_text) <= width:
            if self._timer.isActive():
                self._timer.stop()
            super().setText(self._full_text)
            return

        if not self._timer.isActive():
            self._timer.start()
        base = self._full_text + self._gap + self._full_text
        start = self._offset
        window = base[start:] + base[:start]
        super().setText(self._fit_text(window, width, metrics))


class MarkedSlider(QSlider):
    def __init__(self, orientation: Qt.Orientation, parent: QWidget | None = None) -> None:
        super().__init__(orientation, parent)
        self._markers: list[int] = []

    def set_markers(self, markers: list[int]) -> None:
        self._markers = sorted({max(0, int(value)) for value in markers})
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.maximum() > self.minimum():
            ratio = event.position().x() / max(1.0, float(self.width()))
            ratio = max(0.0, min(1.0, ratio))
            value = int(self.minimum() + ratio * (self.maximum() - self.minimum()))
            self.setValue(value)
            self.sliderMoved.emit(value)
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self._markers or self.maximum() <= self.minimum():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#EF4444"))
        left = 10
        right = max(left + 1, self.width() - 10)
        center_y = self.height() // 2
        full = self.maximum() - self.minimum()
        for marker in self._markers:
            clamped = max(self.minimum(), min(marker, self.maximum()))
            ratio = (clamped - self.minimum()) / full
            x = int(left + (right - left) * ratio)
            painter.drawEllipse(x - 3, center_y - 3, 6, 6)
        painter.end()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("VideoFrame Searcher")
        self.resize(1440, 980)
        self.logger = logging.getLogger("videoframe_searcher.ui")
        self._startup_initialized = False
        if APP_ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(APP_ICON_PATH)))

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
        self.last_download_project_path: Path | None = None
        self._thumbnail_cache: dict[str, QPixmap] = {}
        self._history_cover_queue: list[QListWidgetItem] = []
        self._history_cover_timer: QTimer | None = None
        self._gallery_last_column_count = 0
        self.manual_frame_points: list[float] = []
        self.playback_mark_points_ms: list[int] = []
        self._player_duration_ms = 0
        self._player_slider_dragging = False
        self._player_frame_duration_ms = 33
        self._active_search_worker: Worker | None = None
        self.media_player: Any | None = None
        self.player_audio_output: Any | None = None
        self.player_video_widget: Any | None = None
        self.player_placeholder_label: QLabel | None = None
        self._multimedia_prewarm_started = False

        self._build_ui()
        self._gallery_relayout_timer = QTimer(self)
        self._gallery_relayout_timer.setSingleShot(True)
        self._gallery_relayout_timer.setInterval(80)
        self._gallery_relayout_timer.timeout.connect(self._reflow_gallery_layout)
        self._update_manual_points_label()
        self._update_playback_marks_label()
        self._current_theme = "light"
        self._apply_theme()
        self._load_settings_into_form()
        self.append_log("启动中：正在初始化后台任务...")
        self.append_log("应用已启动。")
        self.append_log(f"日志文件：{get_log_file()}")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._startup_initialized:
            return
        self._startup_initialized = True
        QTimer.singleShot(0, self._deferred_startup_init)
        QTimer.singleShot(5000, self._kickoff_multimedia_prewarm)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._schedule_gallery_relayout()

    def _schedule_gallery_relayout(self) -> None:
        if not hasattr(self, "_gallery_relayout_timer"):
            return
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
        if not hasattr(self, "tabs") or not hasattr(self, "gallery_tab"):
            return
        if self.tabs.currentWidget() != self.gallery_tab and not force:
            return
        column_count = self._current_gallery_column_count()
        if not force and column_count == self._gallery_last_column_count:
            return
        self._gallery_last_column_count = column_count
        self._render_gallery_page()

    def _set_button_role(self, button: QPushButton, role: str) -> None:
        button.setProperty("role", role)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        # Keep workflow focus on input controls; avoids focus jumping when buttons are disabled during tasks.
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def _get_light_theme(self) -> str:
        style = """
            /* ===== Modern Minimal - Light Theme ===== */
            /* Tokens: AppBg=#F3F4F6 PanelBg=#FFFFFF Primary=#0F172A TextBase=#1E293B TextMuted=#64748B Border=#E2E8F0 InputBg=#F8FAFC */

            #AppRoot {
                background: #F3F4F6;
                color: #1E293B;
                font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
                font-size: 13px;
            }

            /* ===== Splitter - More spacing ===== */
            #MainSplit::handle {
                background: #E2E8F0;
                width: 8px;
                border-radius: 4px;
            }

            /* ===== Panels ===== */
            #HistoryPanel {
                background: #FFFFFF;
                border-radius: 10px;
                border: 1px solid #E2E8F0;
            }

            #ContentPanel {
                background: #FFFFFF;
                border-radius: 10px;
                border: 1px solid #E2E8F0;
            }

            /* ===== Section Titles ===== */
            #HistoryTitle, #LogTitle {
                color: #0F172A;
                font-size: 12px;
                font-weight: 600;
                letter-spacing: 0.5px;
                padding-left: 4px;
            }

            /* ===== History List ===== */
            #HistoryList {
                background: #F8FAFC;
                border: none;
                border-radius: 8px;
                padding: 8px;
                outline: none;
            }

            #HistoryList::item {
                background: #FFFFFF;
                border: none;
                border-radius: 6px;
                color: #1E293B;
                padding: 10px 12px;
                margin: 3px 2px;
            }

            #HistoryList::item:hover {
                background: #F1F5F9;
                color: #0F172A;
            }

            #HistoryList::item:selected {
                background: rgba(15, 23, 42, 0.08);
                color: #0F172A;
                border-left: 3px solid #0F172A;
            }

            #HistoryItemCard {
                border-radius: 8px;
                background: transparent;
            }

            #HistoryItemCard[selected="true"] {
                background: rgba(15, 23, 42, 0.06);
            }

            #HistoryItemThumb {
                border-radius: 6px;
                background: #E2E8F0;
                color: #475569;
                font-size: 11px;
                font-weight: 600;
            }

            #HistoryItemTime {
                color: #64748B;
                font-size: 11px;
                font-weight: 600;
            }

            #HistoryItemTitle {
                color: #0F172A;
                font-size: 12px;
                font-weight: 600;
            }

            /* ===== Tabs - Segmented Control Style ===== */
            #MainTabs {
                border: none;
                background: transparent;
            }

            #MainTabs::pane {
                border: none;
                background: transparent;
            }

            #MainTabs QTabBar {
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
                background: transparent;
                font-weight: 600;
                border-bottom: 2px solid #0F172A;
            }

            /* ===== GroupBox - Clean Panel ===== */
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
                letter-spacing: 0.3px;
                background: #FFFFFF;
            }

            /* ===== Labels ===== */
            #AppRoot QLabel {
                color: #1E293B;
                font-size: 13px;
            }

            /* ===== Input Fields ===== */
            #AppRoot QLineEdit,
            #AppRoot QComboBox,
            #AppRoot QSpinBox,
            #AppRoot QPlainTextEdit {
                background: #F8FAFC;
                border: 1px solid #E2E8F0;
                border-radius: 6px;
                padding: 8px 12px;
                min-height: 34px;
                color: #1E293B;
                selection-background-color: rgba(15, 23, 42, 0.15);
                font-size: 13px;
            }

            #AppRoot QLineEdit:focus,
            #AppRoot QComboBox:focus,
            #AppRoot QSpinBox:focus,
            #AppRoot QPlainTextEdit:focus {
                border: 2px solid #0F172A;
                background: #FFFFFF;
            }

            #AppRoot QLineEdit::placeholder {
                color: #94A3B8;
            }

            #AppRoot QComboBox {
                padding-right: 28px;
            }

            #AppRoot QComboBox::drop-down {
                border: none;
                width: 28px;
            }

            #AppRoot QComboBox::down-arrow {
                image: url("__CHEVRON_ICON__");
                width: 12px;
                height: 12px;
                margin-right: 8px;
            }

            #AppRoot QComboBox QAbstractItemView {
                background: #FFFFFF;
                color: #1E293B;
                border: 1px solid #E2E8F0;
                border-radius: 6px;
                selection-background-color: rgba(15, 23, 42, 0.08);
                selection-color: #0F172A;
                padding: 4px;
            }

            #AppRoot QSpinBox::up-button,
            #AppRoot QSpinBox::down-button {
                background: #F1F5F9;
                border: none;
                width: 22px;
                border-radius: 4px;
            }

            #AppRoot QSpinBox::up-button:hover,
            #AppRoot QSpinBox::down-button:hover {
                background: #E2E8F0;
            }

            #AppRoot QSpinBox::up-arrow {
                image: url("__SPIN_UP_ICON__");
                width: 10px;
                height: 6px;
            }

            #AppRoot QSpinBox::down-arrow {
                image: url("__SPIN_DOWN_ICON__");
                width: 10px;
                height: 6px;
            }

            /* ===== Buttons ===== */
            #AppRoot QPushButton {
                background: #FFFFFF;
                color: #1E293B;
                border: 1px solid #E2E8F0;
                border-radius: 6px;
                padding: 8px 16px;
                min-height: 34px;
                font-weight: 500;
                font-size: 13px;
            }

            #AppRoot QPushButton:hover {
                background: #F8FAFC;
                border-color: #CBD5E1;
            }

            #AppRoot QPushButton:pressed {
                background: #F1F5F9;
            }

            #AppRoot QPushButton:disabled {
                background: #F8FAFC;
                color: #94A3B8;
                border-color: #E2E8F0;
            }

            QPushButton#PlayerTransportButton {
                background: #FFFFFF;
                border: 1px solid #CBD5E1;
                border-radius: 17px;
                min-width: 34px;
                max-width: 34px;
                min-height: 34px;
                max-height: 34px;
                padding: 0;
            }

            QPushButton#PlayerTransportButton:hover {
                border-color: #0F172A;
                background: #F8FAFC;
            }

            QPushButton#PlayerTransportButton:pressed {
                background: #EEF2FF;
            }

            /* Primary Button - Solid Primary */
            #AppRoot QPushButton[role="primary"] {
                color: #FFFFFF;
                background: #0F172A;
                border: none;
                font-weight: 600;
            }

            #AppRoot QPushButton[role="primary"]:hover {
                background: #1E293B;
            }

            #AppRoot QPushButton[role="primary"]:pressed {
                background: #334155;
            }

            /* Secondary Button */
            #AppRoot QPushButton[role="secondary"] {
                background: #FFFFFF;
                color: #1E293B;
                border: 1px solid #E2E8F0;
            }

            #AppRoot QPushButton[role="secondary"]:hover {
                background: #F8FAFC;
                border-color: #0F172A;
            }

            /* Theme Toggle Button */
            QPushButton#ThemeToggleBtn {
                background: #FFFFFF;
                color: #64748B;
                border: 1px solid #E2E8F0;
                border-radius: 6px;
                padding: 6px 12px;
                font-weight: 500;
                font-size: 12px;
            }

            QPushButton#ThemeToggleBtn:hover {
                color: #0F172A;
                border-color: #0F172A;
            }

            /* Danger Button */
            #AppRoot QPushButton[role="danger"] {
                color: #EF4444;
                background: #FEE2E2;
                border: 1px solid #FECACA;
            }

            #AppRoot QPushButton[role="danger"]:hover {
                background: #FEF2F2;
                border-color: #EF4444;
            }

            QToolButton#QueueClearButton {
                background: #FFFFFF;
                color: #64748B;
                border: 1px solid #E2E8F0;
                border-radius: 6px;
                min-width: 40px;
                max-width: 40px;
                min-height: 40px;
                max-height: 40px;
                padding: 0;
                font-size: 18px;
                font-weight: 600;
            }

            QToolButton#QueueClearButton:hover {
                background: #F8FAFC;
                color: #0F172A;
                border-color: #CBD5E1;
            }

            QToolButton#QueueClearButton:disabled {
                background: #F8FAFC;
                color: #94A3B8;
                border-color: #E2E8F0;
            }

            #VideoThumb {
                background: #F1F5F9;
                border: 1px solid #E2E8F0;
                border-radius: 10px;
                color: #64748B;
                font-weight: 600;
                font-size: 12px;
            }

            #MetaInfoLabel {
                color: #1E293B;
                font-size: 12px;
                line-height: 1.45;
            }

            #MetaInfoScroll,
            #MetaInfoViewport,
            #MetaInfoContent {
                background: #FFFFFF;
                border: none;
            }

            #PlayerVideoHost {
                background: #0D1117;
                border: 1px solid #E2E8F0;
                border-radius: 10px;
            }

            #PlayerSlider {
                background: transparent;
                border: none;
            }

            #PlayerSlider::groove:horizontal {
                border: none;
                height: 0px;
            }

            #PlayerSlider::handle:horizontal {
                width: 12px;
                height: 12px;
                background: #0F172A;
                border-radius: 6px;
                margin: -4px 0;
            }

            /* ===== Gallery ===== */
            #GalleryInfoLabel, #PageLabel {
                color: #64748B;
                font-size: 12px;
                font-weight: 500;
            }

            #GalleryScroll {
                border: none;
                background: transparent;
            }

            #GalleryContainer {
                background: #FFFFFF;
                border-radius: 10px;
                border: 1px solid #E2E8F0;
            }

            /* Frame Thumbnails - Soft shadow, no border */
            QToolButton#FrameThumb {
                background: #FFFFFF;
                border: none;
                border-radius: 10px;
                color: #64748B;
                font-size: 11px;
                font-weight: 500;
                padding: 8px;
                box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
            }

            QToolButton#FrameThumb:hover {
                background: #FFFFFF;
                border: 1px solid #0F172A;
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
            }

            QToolButton#FrameThumb:checked {
                background: rgba(15, 23, 42, 0.05);
                border: 2px solid #0F172A;
                color: #0F172A;
            }

            /* ===== Log Output - Dark terminal ===== */
            #LogOutput {
                background: #1E1E1E;
                border: 1px solid #333333;
                border-radius: 6px;
                padding: 10px 12px;
                color: #9CDCFE;
                font-family: "Consolas", "Fira Code", "Cascadia Mono", monospace;
                font-size: 12px;
                line-height: 1.5;
            }

            /* ===== ScrollBars ===== */
            #AppRoot QScrollBar:vertical {
                background: transparent;
                width: 6px;
                margin: 4px 2px;
            }

            #AppRoot QScrollBar::handle:vertical {
                background: #CBD5E1;
                border-radius: 3px;
                min-height: 24px;
            }

            #AppRoot QScrollBar::handle:vertical:hover {
                background: #94A3B8;
            }

            #AppRoot QScrollBar::add-line:vertical,
            #AppRoot QScrollBar::sub-line:vertical {
                height: 0px;
            }

            #AppRoot QScrollBar::add-page:vertical,
            #AppRoot QScrollBar::sub-page:vertical {
                background: transparent;
            }

            #AppRoot QScrollBar:horizontal {
                background: transparent;
                height: 6px;
                margin: 2px 4px;
            }

            #AppRoot QScrollBar::handle:horizontal {
                background: #CBD5E1;
                border-radius: 3px;
                min-width: 24px;
            }

            #AppRoot QScrollBar::handle:horizontal:hover {
                background: #94A3B8;
            }

            #AppRoot QScrollBar::add-line:horizontal,
            #AppRoot QScrollBar::sub-line:horizontal {
                width: 0px;
            }

            /* ===== CheckBox ===== */
            #AppRoot QCheckBox {
                color: #1E293B;
                spacing: 8px;
            }

            #AppRoot QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: 1.5px solid #CBD5E1;
                background: #FFFFFF;
            }

            #AppRoot QCheckBox::indicator:hover {
                border-color: #0F172A;
            }

            #AppRoot QCheckBox::indicator:checked {
                border: 1.5px solid #0F172A;
                background: #0F172A;
                image: url("__CHECK_ICON__");
            }

            /* ===== Menu ===== */
            #AppRoot QMenu {
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 8px;
                padding: 6px;
                color: #1E293B;
            }

            #AppRoot QMenu::item {
                padding: 8px 14px;
                border-radius: 4px;
            }

            #AppRoot QMenu::item:selected {
                background: rgba(15, 23, 42, 0.06);
                color: #0F172A;
            }

            /* ===== ToolTip ===== */
            #AppRoot QToolTip {
                background: #1E293B;
                color: #FFFFFF;
                border: none;
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 12px;
            }
        """
        return (
            style.replace("__CHEVRON_ICON__", _qss_path(CHEVRON_DARK_ICON_PATH))
            .replace("__SPIN_UP_ICON__", _qss_path(SPIN_UP_DARK_ICON_PATH))
            .replace("__SPIN_DOWN_ICON__", _qss_path(SPIN_DOWN_DARK_ICON_PATH))
            .replace("__CHECK_ICON__", _qss_path(CHECK_MARK_ICON_PATH))
        )

    def _get_dark_theme(self) -> str:
        style = """
            /* ===== Modern Minimal - Dark Theme ===== */
            /* Tokens: AppBg=#0D1117 PanelBg=#161B22 Primary=#38BDF8 TextBase=#E2E8F0 TextMuted=#94A3B8 Border=#30363D InputBg=#010409 */

            #AppRoot {
                background: #0D1117;
                color: #E2E8F0;
                font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
                font-size: 13px;
            }

            /* ===== Splitter ===== */
            #MainSplit::handle {
                background: #30363D;
                width: 8px;
                border-radius: 4px;
            }

            /* ===== Panels ===== */
            #HistoryPanel {
                background: #161B22;
                border-radius: 10px;
                border: 1px solid #30363D;
            }

            #ContentPanel {
                background: #161B22;
                border-radius: 10px;
                border: 1px solid #30363D;
            }

            /* ===== Section Titles ===== */
            #HistoryTitle, #LogTitle {
                color: #E2E8F0;
                font-size: 12px;
                font-weight: 600;
                letter-spacing: 0.5px;
                padding-left: 4px;
            }

            /* ===== History List ===== */
            #HistoryList {
                background: #010409;
                border: none;
                border-radius: 8px;
                padding: 8px;
                outline: none;
            }

            #HistoryList::item {
                background: #161B22;
                border: none;
                border-radius: 6px;
                color: #E2E8F0;
                padding: 10px 12px;
                margin: 3px 2px;
            }

            #HistoryList::item:hover {
                background: #1C2128;
                color: #E2E8F0;
            }

            #HistoryList::item:selected {
                background: rgba(56, 189, 248, 0.1);
                color: #38BDF8;
                border-left: 3px solid #38BDF8;
            }

            #HistoryItemCard {
                border-radius: 8px;
                background: transparent;
            }

            #HistoryItemCard[selected="true"] {
                background: rgba(56, 189, 248, 0.08);
            }

            #HistoryItemThumb {
                border-radius: 6px;
                background: #21262D;
                color: #94A3B8;
                font-size: 11px;
                font-weight: 600;
            }

            #HistoryItemTime {
                color: #94A3B8;
                font-size: 11px;
                font-weight: 600;
            }

            #HistoryItemTitle {
                color: #E2E8F0;
                font-size: 12px;
                font-weight: 600;
            }

            /* ===== Tabs - Segmented Control Style ===== */
            #MainTabs {
                border: none;
                background: transparent;
            }

            #MainTabs::pane {
                border: none;
                background: transparent;
            }

            #MainTabs QTabBar {
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
                color: #E2E8F0;
                background: rgba(255, 255, 255, 0.05);
            }

            #MainTabs QTabBar::tab:selected {
                color: #38BDF8;
                background: transparent;
                font-weight: 600;
                border-bottom: 2px solid #38BDF8;
            }

            /* ===== GroupBox - Clean Panel ===== */
            #AppRoot QGroupBox {
                background: #161B22;
                border: 1px solid #30363D;
                border-radius: 10px;
                margin-top: 10px;
                padding: 18px 16px 16px 16px;
                font-weight: 600;
                color: #E2E8F0;
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
                letter-spacing: 0.3px;
                background: #161B22;
            }

            /* ===== Labels ===== */
            #AppRoot QLabel {
                color: #E2E8F0;
                font-size: 13px;
            }

            /* ===== Input Fields ===== */
            #AppRoot QLineEdit,
            #AppRoot QComboBox,
            #AppRoot QSpinBox,
            #AppRoot QPlainTextEdit {
                background: #010409;
                border: 1px solid #30363D;
                border-radius: 6px;
                padding: 8px 12px;
                min-height: 34px;
                color: #E2E8F0;
                selection-background-color: rgba(56, 189, 248, 0.2);
                font-size: 13px;
            }

            #AppRoot QLineEdit:focus,
            #AppRoot QComboBox:focus,
            #AppRoot QSpinBox:focus,
            #AppRoot QPlainTextEdit:focus {
                border: 2px solid #38BDF8;
                background: #010409;
            }

            #AppRoot QLineEdit::placeholder {
                color: #6E7681;
            }

            #AppRoot QComboBox {
                padding-right: 28px;
            }

            #AppRoot QComboBox::drop-down {
                border: none;
                width: 28px;
            }

            #AppRoot QComboBox::down-arrow {
                image: url("__CHEVRON_ICON__");
                width: 12px;
                height: 12px;
                margin-right: 8px;
            }

            #AppRoot QComboBox QAbstractItemView {
                background: #161B22;
                color: #E2E8F0;
                border: 1px solid #30363D;
                border-radius: 6px;
                selection-background-color: rgba(56, 189, 248, 0.15);
                selection-color: #38BDF8;
                padding: 4px;
            }

            #AppRoot QSpinBox::up-button,
            #AppRoot QSpinBox::down-button {
                background: #21262D;
                border: none;
                width: 22px;
                border-radius: 4px;
            }

            #AppRoot QSpinBox::up-button:hover,
            #AppRoot QSpinBox::down-button:hover {
                background: #30363D;
            }

            #AppRoot QSpinBox::up-arrow {
                image: url("__SPIN_UP_ICON__");
                width: 10px;
                height: 6px;
            }

            #AppRoot QSpinBox::down-arrow {
                image: url("__SPIN_DOWN_ICON__");
                width: 10px;
                height: 6px;
            }

            /* ===== Buttons ===== */
            #AppRoot QPushButton {
                background: #21262D;
                color: #E2E8F0;
                border: 1px solid #30363D;
                border-radius: 6px;
                padding: 8px 16px;
                min-height: 34px;
                font-weight: 500;
                font-size: 13px;
            }

            #AppRoot QPushButton:hover {
                background: #30363D;
                border-color: #484F58;
            }

            #AppRoot QPushButton:pressed {
                background: #161B22;
            }

            #AppRoot QPushButton:disabled {
                background: #161B22;
                color: #484F58;
                border-color: #21262D;
            }

            QPushButton#PlayerTransportButton {
                background: #21262D;
                border: 1px solid #484F58;
                border-radius: 17px;
                min-width: 34px;
                max-width: 34px;
                min-height: 34px;
                max-height: 34px;
                padding: 0;
            }

            QPushButton#PlayerTransportButton:hover {
                border-color: #38BDF8;
                background: #30363D;
            }

            QPushButton#PlayerTransportButton:pressed {
                background: #0D1117;
            }

            /* Primary Button - Soft blue glow */
            #AppRoot QPushButton[role="primary"] {
                color: #0D1117;
                background: #38BDF8;
                border: none;
                font-weight: 600;
            }

            #AppRoot QPushButton[role="primary"]:hover {
                background: #7DD3FC;
            }

            #AppRoot QPushButton[role="primary"]:pressed {
                background: #0EA5E9;
            }

            /* Secondary Button */
            #AppRoot QPushButton[role="secondary"] {
                background: #161B22;
                color: #E2E8F0;
                border: 1px solid #30363D;
            }

            #AppRoot QPushButton[role="secondary"]:hover {
                background: #21262D;
                border-color: #38BDF8;
            }

            /* Theme Toggle Button */
            QPushButton#ThemeToggleBtn {
                background: #161B22;
                color: #94A3B8;
                border: 1px solid #30363D;
                border-radius: 6px;
                padding: 6px 12px;
                font-weight: 500;
                font-size: 12px;
            }

            QPushButton#ThemeToggleBtn:hover {
                color: #38BDF8;
                border-color: #38BDF8;
            }

            /* Danger Button */
            #AppRoot QPushButton[role="danger"] {
                color: #F87171;
                background: #450A0A;
                border: 1px solid #7F1D1D;
            }

            #AppRoot QPushButton[role="danger"]:hover {
                background: #7F1D1D;
                border-color: #F87171;
            }

            QToolButton#QueueClearButton {
                background: #161B22;
                color: #94A3B8;
                border: 1px solid #30363D;
                border-radius: 6px;
                min-width: 40px;
                max-width: 40px;
                min-height: 40px;
                max-height: 40px;
                padding: 0;
                font-size: 18px;
                font-weight: 600;
            }

            QToolButton#QueueClearButton:hover {
                background: #21262D;
                color: #38BDF8;
                border-color: #38BDF8;
            }

            QToolButton#QueueClearButton:disabled {
                background: #161B22;
                color: #64748B;
                border-color: #30363D;
            }

            #VideoThumb {
                background: #010409;
                border: 1px solid #30363D;
                border-radius: 10px;
                color: #94A3B8;
                font-weight: 600;
                font-size: 12px;
            }

            #MetaInfoLabel {
                color: #E2E8F0;
                font-size: 12px;
                line-height: 1.45;
            }

            #MetaInfoScroll,
            #MetaInfoViewport,
            #MetaInfoContent {
                background: #161B22;
                border: none;
            }

            #PlayerVideoHost {
                background: #010409;
                border: 1px solid #30363D;
                border-radius: 10px;
            }

            #PlayerSlider {
                background: transparent;
                border: none;
            }

            #PlayerSlider::groove:horizontal {
                border: none;
                height: 0px;
            }

            #PlayerSlider::handle:horizontal {
                width: 12px;
                height: 12px;
                background: #38BDF8;
                border-radius: 6px;
                margin: -4px 0;
            }

            /* ===== Gallery ===== */
            #GalleryInfoLabel, #PageLabel {
                color: #94A3B8;
                font-size: 12px;
                font-weight: 500;
            }

            #GalleryScroll {
                border: none;
                background: transparent;
            }

            #GalleryContainer {
                background: #161B22;
                border-radius: 10px;
                border: 1px solid #30363D;
            }

            /* Frame Thumbnails - Border instead of shadow */
            QToolButton#FrameThumb {
                background: #161B22;
                border: 1px solid #30363D;
                border-radius: 10px;
                color: #94A3B8;
                font-size: 11px;
                font-weight: 500;
                padding: 8px;
            }

            QToolButton#FrameThumb:hover {
                background: #21262D;
                border-color: #38BDF8;
            }

            QToolButton#FrameThumb:checked {
                background: rgba(56, 189, 248, 0.08);
                border: 2px solid #38BDF8;
                color: #38BDF8;
            }

            /* ===== Log Output - Deep dark terminal ===== */
            #LogOutput {
                background: #010409;
                border: 1px solid #30363D;
                border-radius: 6px;
                padding: 10px 12px;
                color: #9CDCFE;
                font-family: "Consolas", "Fira Code", "Cascadia Mono", monospace;
                font-size: 12px;
                line-height: 1.5;
            }

            /* ===== ScrollBars ===== */
            #AppRoot QScrollBar:vertical {
                background: transparent;
                width: 6px;
                margin: 4px 2px;
            }

            #AppRoot QScrollBar::handle:vertical {
                background: #30363D;
                border-radius: 3px;
                min-height: 24px;
            }

            #AppRoot QScrollBar::handle:vertical:hover {
                background: #484F58;
            }

            #AppRoot QScrollBar::add-line:vertical,
            #AppRoot QScrollBar::sub-line:vertical {
                height: 0px;
            }

            #AppRoot QScrollBar::add-page:vertical,
            #AppRoot QScrollBar::sub-page:vertical {
                background: transparent;
            }

            #AppRoot QScrollBar:horizontal {
                background: transparent;
                height: 6px;
                margin: 2px 4px;
            }

            #AppRoot QScrollBar::handle:horizontal {
                background: #30363D;
                border-radius: 3px;
                min-width: 24px;
            }

            #AppRoot QScrollBar::handle:horizontal:hover {
                background: #484F58;
            }

            #AppRoot QScrollBar::add-line:horizontal,
            #AppRoot QScrollBar::sub-line:horizontal {
                width: 0px;
            }

            /* ===== CheckBox ===== */
            #AppRoot QCheckBox {
                color: #E2E8F0;
                spacing: 8px;
            }

            #AppRoot QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: 1.5px solid #484F58;
                background: #010409;
            }

            #AppRoot QCheckBox::indicator:hover {
                border-color: #38BDF8;
            }

            #AppRoot QCheckBox::indicator:checked {
                border: 1.5px solid #38BDF8;
                background: #38BDF8;
                image: url("__CHECK_ICON__");
            }

            /* ===== Menu ===== */
            #AppRoot QMenu {
                background: #161B22;
                border: 1px solid #30363D;
                border-radius: 8px;
                padding: 6px;
                color: #E2E8F0;
            }

            #AppRoot QMenu::item {
                padding: 8px 14px;
                border-radius: 4px;
            }

            #AppRoot QMenu::item:selected {
                background: rgba(56, 189, 248, 0.1);
                color: #38BDF8;
            }

            /* ===== ToolTip ===== */
            #AppRoot QToolTip {
                background: #21262D;
                color: #E2E8F0;
                border: 1px solid #30363D;
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 12px;
            }
        """
        return (
            style.replace("__CHEVRON_ICON__", _qss_path(CHEVRON_LIGHT_ICON_PATH))
            .replace("__SPIN_UP_ICON__", _qss_path(SPIN_UP_LIGHT_ICON_PATH))
            .replace("__SPIN_DOWN_ICON__", _qss_path(SPIN_DOWN_LIGHT_ICON_PATH))
            .replace("__CHECK_ICON__", _qss_path(CHECK_MARK_ICON_PATH))
        )

    def _apply_theme(self) -> None:
        if self._current_theme == "light":
            self.setStyleSheet(self._get_light_theme())
        else:
            self.setStyleSheet(self._get_dark_theme())

    def _toggle_theme(self) -> None:
        self._current_theme = "dark" if self._current_theme == "light" else "light"
        self._apply_theme()
        self._update_theme_toggle_button()
        theme_name = "深色" if self._current_theme == "dark" else "浅色"
        self.append_log(f"已切换至 {theme_name} 主题。")

    def _update_theme_toggle_button(self) -> None:
        if self._current_theme == "dark":
            self.theme_toggle_btn.setText("浅色模式")
        else:
            self.theme_toggle_btn.setText("深色模式")

    def _on_theme_toggle_clicked(self) -> None:
        self._toggle_theme()

    def _ensure_bridge_running_on_startup(self) -> None:
        def task(progress_callback=None):
            return self.bridge_runtime_service.ensure_running()

        def on_result(state: str) -> None:
            if state == "started":
                self.append_log("桥接服务已自动启动（端口 38999）。")
            else:
                self.append_log("桥接服务已在线。")

        self._run_worker(
            task,
            on_result=on_result,
            on_error=lambda err: self.append_log(f"桥接服务启动失败：{err.splitlines()[-1] if err else '未知错误'}"),
        )

    def _kickoff_multimedia_prewarm(self) -> None:
        if self.media_player is not None:
            return
        if self._multimedia_prewarm_started:
            return
        if self._workers:
            QTimer.singleShot(2000, self._kickoff_multimedia_prewarm)
            return
        self._multimedia_prewarm_started = True
        code = "import PySide6.QtMultimedia, PySide6.QtMultimediaWidgets"
        kwargs: dict[str, Any] = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
                subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0
            )
        try:
            subprocess.Popen([sys.executable, "-c", code], **kwargs)
        except Exception as exc:
            self.logger.debug("播放器核心后台预热失败：%s", exc)

    def _ensure_player_video_widget_ready(self) -> Any | None:
        if self.player_video_widget is not None:
            return self.player_video_widget
        try:
            from PySide6.QtMultimediaWidgets import QVideoWidget
        except Exception as exc:
            self.append_log(f"播放器界面初始化失败：{exc}")
            return None

        video_widget = QVideoWidget()
        video_widget.setMinimumHeight(320)
        video_widget.setStyleSheet("background: #000000; border-radius: 8px;")
        if hasattr(self, "player_video_host_layout"):
            while self.player_video_host_layout.count():
                item = self.player_video_host_layout.takeAt(0)
                child = item.widget()
                if child is not None:
                    child.deleteLater()
            self.player_video_host_layout.addWidget(video_widget, 1)
        self.player_placeholder_label = None
        self.player_video_widget = video_widget
        return self.player_video_widget

    def _set_player_placeholder_text(self, text: str) -> None:
        if self.player_placeholder_label is None:
            return
        self.player_placeholder_label.setText(text)

    def _ensure_player_backend_ready(self, attach_video_output: bool = True) -> Any | None:
        if self.media_player is not None:
            if attach_video_output:
                video_widget = self._ensure_player_video_widget_ready()
                if video_widget is None:
                    return None
                self.media_player.setVideoOutput(video_widget)
            return self.media_player
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

            self.player_audio_output = QAudioOutput(self)
            player = QMediaPlayer(self)
            player.setAudioOutput(self.player_audio_output)
            if attach_video_output:
                video_widget = self._ensure_player_video_widget_ready()
                if video_widget is None:
                    return None
                player.setVideoOutput(video_widget)
            self.player_audio_output.setVolume(1.0)
            player.positionChanged.connect(self._on_player_position_changed)
            player.durationChanged.connect(self._on_player_duration_changed)
            player.playbackStateChanged.connect(self._on_player_state_changed)
            player.errorOccurred.connect(lambda *_: self.append_log(f"播放器错误：{player.errorString()}"))
            self.media_player = player
        except Exception as exc:
            self.append_log(f"播放器初始化失败：{exc}")
            return None
        return self.media_player

    def _deferred_startup_init(self) -> None:
        # Run startup tasks after the first paint, and keep expensive work off the UI thread.
        self._run_startup_history_refresh()
        self._ensure_bridge_running_on_startup()

    def _run_startup_history_refresh(self) -> None:
        def task(progress_callback=None):
            return self.project_service.list_projects()

        def on_result(projects: list[dict[str, Any]]) -> None:
            self._apply_history_projects(projects, previous_path=None, load_covers=False)
            self.append_log(f"历史项目已刷新，共 {len(projects)} 个。")

        self._run_worker(
            task,
            on_result=on_result,
            on_error=lambda err: self.append_log(f"历史项目加载失败：{err.splitlines()[-1] if err else '未知错误'}"),
        )

    def _on_tab_changed(self, _index: int) -> None:
        if self.tabs.currentWidget() == self.player_tab:
            current_video = self._current_video_path()
            if self.media_player is None or self.media_player.source().isEmpty():
                if current_video is None:
                    self.player_status_label.setText("未加载视频")
                else:
                    self.player_status_label.setText(f"待播放：{current_video.name}")
                self._update_player_play_button(False)
        if self.tabs.currentWidget() == self.gallery_tab:
            self._reflow_gallery_layout(force=True)

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("AppRoot")
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(16)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("MainSplit")
        root_layout.addWidget(splitter)
        self.setCentralWidget(root)

        left_panel = QWidget()
        left_panel.setObjectName("HistoryPanel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(12)
        history_title = QLabel("历史项目")
        history_title.setObjectName("HistoryTitle")
        left_layout.addWidget(history_title)
        self.history_list = QListWidget()
        self.history_list.setObjectName("HistoryList")
        self.history_list.itemClicked.connect(self._on_history_item_clicked)
        self.history_list.currentItemChanged.connect(self._on_history_current_item_changed)
        self.history_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.history_list.customContextMenuRequested.connect(self._on_history_context_menu)
        left_layout.addWidget(self.history_list, 1)

        history_btn_row = QHBoxLayout()
        history_btn_row.setSpacing(8)
        refresh_button = QPushButton("刷新历史")
        self._set_button_role(refresh_button, "secondary")
        refresh_button.clicked.connect(self.refresh_history)
        history_btn_row.addWidget(refresh_button)
        delete_button = QPushButton("删除项目")
        self._set_button_role(delete_button, "danger")
        delete_button.clicked.connect(self._on_delete_selected_project)
        history_btn_row.addWidget(delete_button)
        left_layout.addLayout(history_btn_row)
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

        self.download_tab = self._build_download_tab()
        self.player_tab = self._build_player_tab()
        self.gallery_tab = self._build_gallery_tab()
        self.settings_tab = self._build_settings_tab()

        self.tabs.addTab(self.download_tab, "采集工作台")
        self.tabs.addTab(self.player_tab, "视频播放")
        self.tabs.addTab(self.gallery_tab, "截图画廊")
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

        refresh_action = QAction("刷新历史", self)
        refresh_action.triggered.connect(self.refresh_history)
        self.addAction(refresh_action)

    def _build_download_tab(self) -> QWidget:
        tab = QWidget()
        layout = QHBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        left_col = QWidget()
        left_layout = QVBoxLayout(left_col)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        form = QGroupBox("下载参数")
        form_layout = QGridLayout(form)
        form_layout.addWidget(QLabel("视频 URL"), 0, 0)
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("粘贴视频链接后先解析元数据")
        self.url_input.textChanged.connect(self._on_url_input_changed)
        form_layout.addWidget(self.url_input, 0, 1, 1, 3)

        self.parse_button = QPushButton("解析元数据")
        self._set_button_role(self.parse_button, "secondary")
        self.parse_button.clicked.connect(self._on_parse_metadata)
        form_layout.addWidget(self.parse_button, 1, 1)

        self.open_download_dir_button = QPushButton("打开下载文件夹")
        self._set_button_role(self.open_download_dir_button, "secondary")
        self.open_download_dir_button.clicked.connect(self._on_open_download_folder)
        form_layout.addWidget(self.open_download_dir_button, 1, 2, 1, 2)

        form_layout.addWidget(QLabel("画质优先"), 2, 0)
        self.quality_combo = QComboBox()
        self.quality_combo.addItem("自动", "auto")
        self.quality_combo.addItem("2160p", "2160")
        self.quality_combo.addItem("1440p", "1440")
        self.quality_combo.addItem("1080p", "1080")
        self.quality_combo.addItem("720p", "720")
        self.quality_combo.addItem("480p", "480")
        self.quality_combo.addItem("360p", "360")
        form_layout.addWidget(self.quality_combo, 2, 1, 1, 3)

        self.download_button = QPushButton("开始下载")
        self._set_button_role(self.download_button, "primary")
        self.download_button.clicked.connect(self._on_download_video)
        form_layout.addWidget(self.download_button, 3, 1, 1, 3)
        left_layout.addWidget(form)

        box = QGroupBox("抽帧参数")
        box_layout = QGridLayout(box)
        box_layout.addWidget(QLabel("抽帧间隔(秒)"), 0, 0)
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 3600)
        self.interval_spin.setValue(5)
        box_layout.addWidget(self.interval_spin, 0, 1)

        self.manual_points_button = QPushButton("手动抽帧设置")
        self._set_button_role(self.manual_points_button, "secondary")
        self.manual_points_button.clicked.connect(self._on_edit_manual_points)
        box_layout.addWidget(self.manual_points_button, 0, 2)

        self.clear_checkbox = QCheckBox("清除该视频原有截图")
        self.clear_checkbox.setChecked(False)
        self.clear_checkbox.toggled.connect(self._on_workbench_clear_checkbox_toggled)
        box_layout.addWidget(self.clear_checkbox, 1, 0, 1, 3)

        self.manual_points_label = QLabel("手动时间点：0 个")
        self.manual_points_label.setObjectName("GalleryInfoLabel")
        box_layout.addWidget(self.manual_points_label, 2, 0, 1, 3)

        self.extract_button = QPushButton("开始抽帧")
        self._set_button_role(self.extract_button, "primary")
        self.extract_button.clicked.connect(self._on_extract_frames)
        box_layout.addWidget(self.extract_button, 3, 0, 1, 3)

        self.upload_button = QPushButton("上传本地视频")
        self._set_button_role(self.upload_button, "secondary")
        self.upload_button.clicked.connect(self._on_import_local_video)
        box_layout.addWidget(self.upload_button, 4, 0, 1, 3)

        self.delete_video_button = QPushButton("删除原始视频，仅保留截图")
        self._set_button_role(self.delete_video_button, "danger")
        self.delete_video_button.clicked.connect(self._on_delete_video)
        box_layout.addWidget(self.delete_video_button, 5, 0, 1, 3)
        left_layout.addWidget(box)

        self.frame_hint_label = QLabel("未加载项目。")
        self.frame_hint_label.setWordWrap(True)
        left_layout.addWidget(self.frame_hint_label)
        left_layout.addStretch(1)

        right_col = QWidget()
        right_col.setMinimumWidth(360)
        right_col.setMaximumWidth(460)
        right_layout = QVBoxLayout(right_col)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

        meta_group = QGroupBox("视频信息")
        meta_layout = QVBoxLayout(meta_group)
        meta_layout.setSpacing(10)
        self.video_thumb_label = QLabel("暂无缩略图")
        self.video_thumb_label.setObjectName("VideoThumb")
        self.video_thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_thumb_label.setMinimumHeight(188)
        self.video_thumb_label.setWordWrap(True)
        meta_layout.addWidget(self.video_thumb_label)

        self.metadata_label = QLabel("尚未解析。")
        self.metadata_label.setWordWrap(True)
        self.metadata_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.metadata_label.setObjectName("MetaInfoLabel")
        meta_scroll = QScrollArea()
        meta_scroll.setWidgetResizable(True)
        meta_scroll.setObjectName("MetaInfoScroll")
        meta_scroll.setFrameShape(QFrame.Shape.NoFrame)
        meta_scroll.setMinimumHeight(170)
        meta_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        meta_scroll.viewport().setObjectName("MetaInfoViewport")
        meta_content = QWidget()
        meta_content.setObjectName("MetaInfoContent")
        meta_content_layout = QVBoxLayout(meta_content)
        meta_content_layout.setContentsMargins(0, 0, 0, 0)
        meta_content_layout.setSpacing(0)
        meta_content_layout.addWidget(self.metadata_label)
        meta_content_layout.addStretch(1)
        meta_scroll.setWidget(meta_content)
        meta_layout.addWidget(meta_scroll, 1)
        right_layout.addWidget(meta_group, 1)

        layout.addWidget(left_col, 1)
        layout.addWidget(right_col, 0)
        return tab

    def _build_player_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        player_group = QGroupBox("视频播放")
        player_layout = QVBoxLayout(player_group)
        player_layout.setSpacing(8)

        self.player_video_host = QWidget()
        self.player_video_host.setObjectName("PlayerVideoHost")
        self.player_video_host.setMinimumHeight(320)
        self.player_video_host.setStyleSheet("background: #000000; border-radius: 8px;")
        self.player_video_host_layout = QVBoxLayout(self.player_video_host)
        self.player_video_host_layout.setContentsMargins(0, 0, 0, 0)
        self.player_video_host_layout.setSpacing(0)
        self.player_placeholder_label = QLabel("未加载视频")
        self.player_placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.player_placeholder_label.setWordWrap(True)
        self.player_placeholder_label.setStyleSheet("color: #94A3B8; font-size: 12px;")
        self.player_video_host_layout.addWidget(self.player_placeholder_label, 1)
        player_layout.addWidget(self.player_video_host, 1)

        progress_row = QHBoxLayout()
        self.player_play_button = QPushButton("")
        self.player_play_button.setObjectName("PlayerTransportButton")
        self.player_play_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.player_play_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.player_play_button.setFixedSize(34, 34)
        self.player_play_button.setIconSize(QSize(18, 18))
        self.player_play_button.clicked.connect(self._toggle_player_playback)
        progress_row.addWidget(self.player_play_button, 0)

        self.player_slider = MarkedSlider(Qt.Orientation.Horizontal)
        self.player_slider.setObjectName("PlayerSlider")
        self.player_slider.setRange(0, 0)
        self.player_slider.sliderPressed.connect(self._on_player_slider_pressed)
        self.player_slider.sliderReleased.connect(self._on_player_slider_released)
        self.player_slider.sliderMoved.connect(self._on_player_slider_moved)
        progress_row.addWidget(self.player_slider, 1)
        self.player_time_label = QLabel("00:00 / 00:00")
        self.player_time_label.setMinimumWidth(220)
        progress_row.addWidget(self.player_time_label, 0)
        player_layout.addLayout(progress_row)

        status_row = QHBoxLayout()
        self.player_status_label = QLabel("未加载视频")
        self.player_status_label.setObjectName("GalleryInfoLabel")
        status_row.addWidget(self.player_status_label, 1)
        player_layout.addLayout(status_row)
        self._update_player_play_button(False)
        layout.addWidget(player_group, 1)

        action_group = QGroupBox("播放截图")
        action_layout = QGridLayout(action_group)
        self.player_clear_checkbox = QCheckBox("清除该视频原有截图（对截图当前不生效）")
        self.player_clear_checkbox.setChecked(False)
        self.player_clear_checkbox.toggled.connect(self._on_player_clear_checkbox_toggled)
        action_layout.addWidget(self.player_clear_checkbox, 0, 0, 1, 4)

        self.capture_current_button = QPushButton("截图当前")
        self._set_button_role(self.capture_current_button, "secondary")
        self.capture_current_button.clicked.connect(self._on_capture_current_frame)
        action_layout.addWidget(self.capture_current_button, 1, 0)

        self.capture_mark_button = QPushButton("截图打点")
        self._set_button_role(self.capture_mark_button, "secondary")
        self.capture_mark_button.clicked.connect(self._on_mark_current_frame)
        action_layout.addWidget(self.capture_mark_button, 1, 1)

        self.capture_batch_button = QPushButton("批量截图")
        self._set_button_role(self.capture_batch_button, "secondary")
        self.capture_batch_button.clicked.connect(self._on_batch_capture_marked_frames)
        action_layout.addWidget(self.capture_batch_button, 1, 2)

        self.open_screenshot_folder_player_button = QPushButton("打开截图文件夹")
        self._set_button_role(self.open_screenshot_folder_player_button, "secondary")
        self.open_screenshot_folder_player_button.clicked.connect(self._on_open_screenshot_folder)
        action_layout.addWidget(self.open_screenshot_folder_player_button, 1, 3)

        self.playback_marks_label = QLabel("已打点：0 个")
        self.playback_marks_label.setObjectName("GalleryInfoLabel")
        action_layout.addWidget(self.playback_marks_label, 2, 0, 1, 4)
        layout.addWidget(action_group, 0)

        self._shortcut_step_backward = QShortcut(QKeySequence(Qt.Key.Key_Left), tab)
        self._shortcut_step_backward.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._shortcut_step_backward.activated.connect(self._step_frame_backward)
        self._shortcut_step_forward = QShortcut(QKeySequence(Qt.Key.Key_Right), tab)
        self._shortcut_step_forward.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._shortcut_step_forward.activated.connect(self._step_frame_forward)

        return tab

    def _build_gallery_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        tools_layout = QHBoxLayout()
        select_page_btn = QPushButton("全选当前页")
        self._set_button_role(select_page_btn, "secondary")
        select_page_btn.clicked.connect(self._select_current_page)
        tools_layout.addWidget(select_page_btn)

        clear_btn = QPushButton("清空选择")
        self._set_button_role(clear_btn, "secondary")
        clear_btn.clicked.connect(self._clear_selection)
        tools_layout.addWidget(clear_btn)

        delete_btn = QPushButton("删除截图")
        self._set_button_role(delete_btn, "danger")
        delete_btn.clicked.connect(self._on_delete_selected_screenshots)
        tools_layout.addWidget(delete_btn)

        open_folder_btn = QPushButton("打开截图文件夹")
        self._set_button_role(open_folder_btn, "secondary")
        open_folder_btn.clicked.connect(self._on_open_screenshot_folder)
        tools_layout.addWidget(open_folder_btn)
        tools_layout.addStretch(1)
        self.gallery_info_label = QLabel("总计 0 张，已选 0 张")
        self.gallery_info_label.setObjectName("GalleryInfoLabel")
        tools_layout.addWidget(self.gallery_info_label)
        layout.addLayout(tools_layout)

        search_row = QHBoxLayout()
        self.gallery_search_hint_label = QLabel("请先安装并启动浏览器插件，再选中截图点击右侧“以图搜图”")
        self.gallery_search_hint_label.setObjectName("GalleryInfoLabel")
        self.gallery_search_hint_label.setWordWrap(False)
        search_row.addWidget(self.gallery_search_hint_label, 1)
        self.search_button = QPushButton("以图搜图")
        self._set_button_role(self.search_button, "primary")
        self.search_button.clicked.connect(self._on_search_selected_image)
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
        self._set_button_role(cookie_file_btn, "secondary")
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
        self._set_button_role(save_btn, "primary")
        save_btn.clicked.connect(self._on_save_settings)
        box_layout.addWidget(save_btn, 9, 0, 1, 3)

        update_btn = QPushButton("强制更新下载核心 (yt-dlp)")
        self._set_button_role(update_btn, "secondary")
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
    ) -> Worker:
        worker = Worker(task)
        self._workers.add(worker)

        worker.signals.progress.connect(self.append_log)
        worker.signals.result.connect(lambda result: on_result(result) if on_result else None)
        worker.signals.error.connect(lambda err: self._handle_worker_error(err, on_error))
        worker.signals.finished.connect(lambda: self._workers.discard(worker))
        if on_finished:
            worker.signals.finished.connect(on_finished)

        self.thread_pool.start(worker)
        return worker

    def _handle_worker_error(self, traceback_text: str, on_error=None) -> None:
        self.logger.error("后台任务失败：\n%s", traceback_text)
        self.append_log(traceback_text)
        if on_error:
            on_error(traceback_text)
            return
        summary = "未知错误"
        if traceback_text:
            lines = [line.strip() for line in traceback_text.splitlines() if line.strip()]
            preferred = [
                line
                for line in lines
                if ": " in line and (line.endswith("Error") or "Error:" in line or line.endswith("Exception"))
            ]
            if preferred:
                tail = preferred[-1]
                summary = tail.split(": ", 1)[1].strip() if ": " in tail else tail
            elif lines:
                summary = lines[-1]
        QMessageBox.critical(self, "执行失败", f"{summary}\n\n详细日志：{get_log_file()}")

    def append_log(self, message: str) -> None:
        self.logger.info(message)
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_output.appendPlainText(f"[{stamp}] {message}")

    def _format_created_at(self, raw: str) -> str:
        value = str(raw or "").strip()
        if not value:
            return "未知时间"
        try:
            dt = datetime.fromisoformat(value)
            return dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return value.replace("T", " ")[:16]

    def _normalize_thumbnail_url(self, value: str) -> str:
        text = str(value or "").strip()
        if text.startswith("//"):
            return f"https:{text}"
        return text

    def _thumbnail_url_from_metadata(self, metadata: dict[str, Any]) -> str:
        thumb_url = self._normalize_thumbnail_url(str(metadata.get("thumbnail_url") or metadata.get("thumbnail") or ""))
        if thumb_url:
            return thumb_url
        thumbnails = metadata.get("thumbnails")
        if isinstance(thumbnails, list):
            for entry in reversed(thumbnails):
                if not isinstance(entry, dict):
                    continue
                candidate = self._normalize_thumbnail_url(str(entry.get("url") or ""))
                if candidate:
                    return candidate
        return ""

    def _fetch_thumbnail_pixmap(self, thumbnail_url: str, timeout: int = 8) -> QPixmap | None:
        if not thumbnail_url:
            return None
        cached = self._thumbnail_cache.get(thumbnail_url)
        if cached is not None and not cached.isNull():
            return cached
        if not thumbnail_url.startswith("http://") and not thumbnail_url.startswith("https://"):
            return None
        try:
            response = requests.get(
                thumbnail_url,
                headers={"User-Agent": REQUEST_UA},
                timeout=timeout,
            )
            response.raise_for_status()
            loaded = QPixmap()
            loaded.loadFromData(response.content)
            if loaded.isNull():
                return None
            self._thumbnail_cache[thumbnail_url] = loaded
            return loaded
        except Exception:
            return None

    def _scaled_cover_pixmap(self, source: QPixmap, width: int, height: int) -> QPixmap:
        if source.isNull():
            return QPixmap()
        scaled = source.scaled(
            width,
            height,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        if scaled.isNull():
            return QPixmap()
        x = max(0, (scaled.width() - width) // 2)
        y = max(0, (scaled.height() - height) // 2)
        return scaled.copy(x, y, width, height)

    def _normalized_input_url(self) -> str:
        raw_url = self.url_input.text().strip()
        normalized_url = self.download_service.normalize_url(raw_url)
        if normalized_url and normalized_url != raw_url:
            self.url_input.blockSignals(True)
            self.url_input.setText(normalized_url)
            self.url_input.blockSignals(False)
        return normalized_url or raw_url

    def _selected_quality_value(self) -> str:
        if not hasattr(self, "quality_combo"):
            return "auto"
        value = self.quality_combo.currentData()
        text = str(value if value is not None else "auto").strip().lower()
        return text or "auto"

    def _wrap_long_text(self, value: str, width: int = 44) -> str:
        text = str(value or "").strip()
        if not text:
            return "未设置"
        if len(text) <= width:
            return text
        return "\n".join(text[i : i + width] for i in range(0, len(text), width))

    def _format_seconds_text(self, seconds: float) -> str:
        total_ms = max(0, int(round(float(seconds) * 1000)))
        hours = total_ms // 3_600_000
        minutes = (total_ms % 3_600_000) // 60_000
        secs = (total_ms % 60_000) / 1000
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"
        return f"{minutes:02d}:{secs:06.3f}"

    def _parse_timestamp_to_seconds(self, raw: str) -> float:
        text = str(raw or "").strip()
        if not text:
            raise ValueError("时间点为空")
        if ":" not in text:
            value = float(text)
            if value < 0:
                raise ValueError("时间点不能为负数")
            return value

        parts = text.split(":")
        if len(parts) == 2:
            minute_text, second_text = parts
            hour = 0
        elif len(parts) == 3:
            hour_text, minute_text, second_text = parts
            hour = int(hour_text)
        else:
            raise ValueError("时间格式错误")

        minute = int(minute_text)
        second = float(second_text)
        value = hour * 3600 + minute * 60 + second
        if value < 0:
            raise ValueError("时间点不能为负数")
        return value

    def _update_manual_points_label(self) -> None:
        points = sorted(self.manual_frame_points)
        preview = ", ".join(self._format_seconds_text(point) for point in points[:3])
        suffix = " ..." if len(points) > 3 else ""
        if preview:
            self.manual_points_label.setText(f"手动时间点：{len(points)} 个（{preview}{suffix}）")
        else:
            self.manual_points_label.setText("手动时间点：0 个")

    def _on_workbench_clear_checkbox_toggled(self, checked: bool) -> None:
        if hasattr(self, "player_clear_checkbox"):
            self.player_clear_checkbox.blockSignals(True)
            self.player_clear_checkbox.setChecked(checked)
            self.player_clear_checkbox.blockSignals(False)

    def _on_player_clear_checkbox_toggled(self, checked: bool) -> None:
        if hasattr(self, "clear_checkbox"):
            self.clear_checkbox.blockSignals(True)
            self.clear_checkbox.setChecked(checked)
            self.clear_checkbox.blockSignals(False)

    def _edit_manual_points(self, current_points: list[float], title: str) -> list[float] | None:
        default_text = "\n".join(self._format_seconds_text(point) for point in sorted(current_points))
        text, ok = QInputDialog.getMultiLineText(
            self,
            title,
            (
                "输入规则：\n"
                "1) 每行 1 个时间点，或同一行用英文逗号分隔多个时间点；\n"
                "2) 支持“秒”（示例：75.5）或“HH:MM:SS.mmm”（示例：00:01:15.500）；\n"
                "3) 保存时会自动去重并按时间升序排序；留空表示清空手动时间点。"
            ),
            default_text,
        )
        if not ok:
            return None

        tokens: list[str] = []
        for row in text.splitlines():
            parts = [part.strip() for part in row.replace("，", ",").split(",")]
            tokens.extend([part for part in parts if part])

        parsed: list[float] = []
        invalid: list[str] = []
        for token in tokens:
            try:
                parsed.append(round(self._parse_timestamp_to_seconds(token), 3))
            except Exception:
                invalid.append(token)

        if invalid:
            preview = "\n".join(invalid[:8])
            extra = "\n..." if len(invalid) > 8 else ""
            QMessageBox.warning(self, "时间格式错误", f"以下时间无法解析：\n{preview}{extra}")
            return None

        return sorted(dict.fromkeys(parsed))

    def _on_edit_manual_points(self) -> None:
        points = self._edit_manual_points(self.manual_frame_points, "手动抽帧设置")
        if points is None:
            return
        self.manual_frame_points = points
        self._update_manual_points_label()
        self.append_log(f"手动抽帧时间点已更新：{len(points)} 个")

    def _current_video_path(self) -> Path | None:
        video_raw = str(self.current_metadata.get("video_path", "")).strip()
        if not video_raw:
            return None
        path = Path(video_raw)
        if not path.exists():
            return None
        return path

    def _format_precise_time(self, ms_value: int) -> str:
        total_ms = max(0, int(ms_value))
        _total_seconds, milli = divmod(total_ms, 1000)
        return f"{milli:03d}"

    def _format_player_time_label(self, position_ms: int, duration_ms: int) -> str:
        left = _duration_text(max(0, position_ms) / 1000)
        right = _duration_text(max(0, duration_ms) / 1000)
        precise = self._format_precise_time(position_ms)
        return f"{left} / {right} · {precise}ms"

    def _update_player_play_button(self, playing: bool) -> None:
        icon_type = (
            QStyle.StandardPixmap.SP_MediaPause if playing else QStyle.StandardPixmap.SP_MediaPlay
        )
        self.player_play_button.setIcon(self.style().standardIcon(icon_type))
        self.player_play_button.setToolTip("暂停" if playing else "播放")

    def _is_player_playing(self, player: Any | None) -> bool:
        if player is None:
            return False
        state = player.playbackState()
        state_name = getattr(state, "name", str(state))
        return str(state_name).endswith("PlayingState")

    def _update_playback_marks_label(self) -> None:
        count = len(self.playback_mark_points_ms)
        preview = ", ".join(self._format_seconds_text(ms / 1000) for ms in self.playback_mark_points_ms[:3])
        suffix = " ..." if count > 3 else ""
        if preview:
            self.playback_marks_label.setText(f"已打点：{count} 个（{preview}{suffix}）")
        else:
            self.playback_marks_label.setText("已打点：0 个")
        self.player_slider.set_markers(self.playback_mark_points_ms)

    def _clear_playback_marks(self) -> None:
        self.playback_mark_points_ms = []
        self._update_playback_marks_label()

    def _load_video_into_player(self, video_path: Path | None) -> None:
        player = self._ensure_player_backend_ready()
        if player is None:
            self.player_status_label.setText("播放器不可用")
            self._update_player_play_button(False)
            return

        player.stop()
        self._player_duration_ms = 0
        self._player_slider_dragging = False
        self.player_slider.blockSignals(True)
        self.player_slider.setRange(0, 0)
        self.player_slider.setValue(0)
        self.player_slider.blockSignals(False)
        self.player_time_label.setText(self._format_player_time_label(0, 0))
        self._clear_playback_marks()

        if video_path is None or not video_path.exists():
            player.setSource(QUrl())
            self.player_status_label.setText("未加载视频")
            self._update_player_play_button(False)
            return

        player.setSource(QUrl.fromLocalFile(str(video_path)))
        fps = self.frame_service.probe_frame_rate(video_path)
        if fps and fps > 0:
            self._player_frame_duration_ms = max(1, int(round(1000 / fps)))
        else:
            self._player_frame_duration_ms = 33
        self.player_status_label.setText(f"已加载：{video_path.name}")
        self._update_player_play_button(False)

    def _set_pending_player_video(self, video_path: Path | None) -> None:
        if self.media_player is not None:
            self.media_player.stop()
            self.media_player.setSource(QUrl())
        self._player_duration_ms = 0
        self._player_slider_dragging = False
        self.player_slider.blockSignals(True)
        self.player_slider.setRange(0, 0)
        self.player_slider.setValue(0)
        self.player_slider.blockSignals(False)
        self.player_time_label.setText(self._format_player_time_label(0, 0))
        self._clear_playback_marks()
        self._set_player_placeholder_text("未加载视频")
        if video_path is None:
            self.player_status_label.setText("未加载视频")
        else:
            self.player_status_label.setText(f"待播放：{video_path.name}")
        self._update_player_play_button(False)

    def _ensure_player_source_loaded(self) -> Any | None:
        player = self._ensure_player_backend_ready(attach_video_output=True)
        if player is None:
            return None
        if player.source().isEmpty():
            self._load_video_into_player(self._current_video_path())
        return player

    def _on_player_position_changed(self, position_ms: int) -> None:
        if not self._player_slider_dragging:
            self.player_slider.blockSignals(True)
            self.player_slider.setValue(max(0, int(position_ms)))
            self.player_slider.blockSignals(False)
        self.player_time_label.setText(self._format_player_time_label(int(position_ms), self._player_duration_ms))

    def _on_player_duration_changed(self, duration_ms: int) -> None:
        self._player_duration_ms = max(0, int(duration_ms))
        self.player_slider.blockSignals(True)
        self.player_slider.setRange(0, self._player_duration_ms if self._player_duration_ms > 0 else 0)
        self.player_slider.blockSignals(False)
        current_pos = self.media_player.position() if self.media_player is not None else 0
        self.player_time_label.setText(self._format_player_time_label(current_pos, self._player_duration_ms))

    def _on_player_state_changed(self, _state) -> None:
        playing = self._is_player_playing(self.media_player)
        self._update_player_play_button(playing)

    def _on_player_slider_pressed(self) -> None:
        self._player_slider_dragging = True

    def _on_player_slider_moved(self, value: int) -> None:
        self.player_time_label.setText(self._format_player_time_label(int(value), self._player_duration_ms))

    def _on_player_slider_released(self) -> None:
        self._player_slider_dragging = False
        if self.media_player is not None:
            self.media_player.setPosition(int(self.player_slider.value()))

    def _toggle_player_playback(self) -> None:
        if self.media_player is None:
            self.player_status_label.setText("正在初始化播放器...")
            self._set_player_placeholder_text(
                "受程序架构限制，首次启动播放器需要一定时间，如发生卡顿请稍作等待..."
            )
            self.player_play_button.setEnabled(False)

            def delayed_init() -> None:
                try:
                    self._toggle_player_playback_after_init()
                finally:
                    self.player_play_button.setEnabled(True)

            QTimer.singleShot(80, delayed_init)
            return
        self._toggle_player_playback_after_init()

    def _toggle_player_playback_after_init(self) -> None:
        player = self._ensure_player_source_loaded()
        if player is None or player.source().isEmpty():
            QMessageBox.warning(self, "未加载视频", "当前项目没有可播放的视频。")
            return
        if self._is_player_playing(player):
            player.pause()
        else:
            player.play()

    def _step_player_frame(self, direction: int) -> None:
        if self.tabs.currentWidget() != self.player_tab:
            return
        player = self._ensure_player_source_loaded()
        if player is None or player.source().isEmpty():
            return
        if self._is_player_playing(player):
            player.pause()
        delta = self._player_frame_duration_ms * (1 if direction > 0 else -1)
        target = player.position() + delta
        if self._player_duration_ms > 0:
            target = max(0, min(target, self._player_duration_ms))
        player.setPosition(max(0, int(target)))

    def _step_frame_backward(self) -> None:
        self._step_player_frame(-1)

    def _step_frame_forward(self) -> None:
        self._step_player_frame(1)

    def _on_mark_current_frame(self) -> None:
        video_path = self._current_video_path()
        if video_path is None:
            QMessageBox.warning(self, "缺少视频", "当前项目没有可播放的视频。")
            return
        player = self._ensure_player_source_loaded()
        if player is None:
            QMessageBox.warning(self, "播放器不可用", "当前播放器初始化失败，请重启应用重试。")
            return
        _ = video_path
        current = max(0, int(player.position()))
        if any(abs(existing - current) <= self._player_frame_duration_ms for existing in self.playback_mark_points_ms):
            self.append_log(f"打点已存在：{self._format_seconds_text(current / 1000)}")
            return
        self.playback_mark_points_ms.append(current)
        self.playback_mark_points_ms.sort()
        self._update_playback_marks_label()
        self.append_log(f"已打点：{self._format_seconds_text(current / 1000)}")

    def _on_capture_current_frame(self) -> None:
        if not self.current_project_path:
            QMessageBox.warning(self, "未选择项目", "请先从左侧历史项目中选择一个项目。")
            return
        video_path = self._current_video_path()
        if video_path is None:
            QMessageBox.warning(self, "缺少视频", "当前项目没有可用原始视频文件。")
            return
        output_dir = self.current_project_path / "screenshots"
        clear_existing = False
        player = self._ensure_player_source_loaded()
        if player is None:
            QMessageBox.warning(self, "播放器不可用", "当前播放器初始化失败，请重启应用重试。")
            return
        timestamp = max(0.0, player.position() / 1000)
        self.capture_current_button.setEnabled(False)

        def task(progress_callback):
            path = self.frame_service.capture_frame(
                video_path=video_path,
                output_dir=output_dir,
                timestamp_seconds=timestamp,
                clear_existing=clear_existing,
                prefix="manual",
                progress_callback=progress_callback,
            )
            return str(path)

        def on_result(path: str) -> None:
            self.append_log(f"当前画面截图完成：{Path(path).name}")
            self._refresh_current_screenshots()

        self._run_worker(task, on_result=on_result, on_finished=lambda: self.capture_current_button.setEnabled(True))

    def _on_batch_capture_marked_frames(self) -> None:
        if not self.current_project_path:
            QMessageBox.warning(self, "未选择项目", "请先从左侧历史项目中选择一个项目。")
            return
        video_path = self._current_video_path()
        if video_path is None:
            QMessageBox.warning(self, "缺少视频", "当前项目没有可用原始视频文件。")
            return
        if not self.playback_mark_points_ms:
            QMessageBox.warning(self, "未打点", "请先使用“截图打点”标记至少一个时间点。")
            return

        output_dir = self.current_project_path / "screenshots"
        clear_existing = self.player_clear_checkbox.isChecked()
        points = [ms / 1000 for ms in self.playback_mark_points_ms]
        self.capture_batch_button.setEnabled(False)

        def task(progress_callback):
            return self.frame_service.extract_manual_frames(
                video_path=video_path,
                output_dir=output_dir,
                timestamps_seconds=points,
                clear_existing=clear_existing,
                progress_callback=progress_callback,
                prefix="manual",
            )

        def on_result(count: int) -> None:
            self.append_log(f"打点批量截图完成，共 {count} 张。")
            self._refresh_current_screenshots()
            QMessageBox.information(self, "批量截图完成", f"已生成 {count} 张截图。")

        self._run_worker(task, on_result=on_result, on_finished=lambda: self.capture_batch_button.setEnabled(True))

    def _refresh_current_screenshots(self) -> None:
        if not self.current_project_path:
            return
        self.current_images = self.project_service.list_screenshots(self.current_project_path)
        self.selected_images = {img for img in self.selected_images if img in self.current_images}
        total_pages = math.ceil(len(self.current_images) / self.page_size) if self.current_images else 0
        if total_pages:
            self.current_page = max(0, min(self.current_page, total_pages - 1))
        else:
            self.current_page = 0
        self._render_gallery_page()
        self.refresh_history(silent=True)

    def _resolve_project_cover(self, project_path: Path, metadata: dict[str, Any] | None = None) -> Path | None:
        if metadata:
            thumb_path_raw = str(metadata.get("thumbnail_path", "")).strip()
            if thumb_path_raw:
                thumb_path = Path(thumb_path_raw)
                if not thumb_path.is_absolute():
                    thumb_path = project_path / thumb_path
                if thumb_path.exists() and thumb_path.is_file():
                    return thumb_path

        shot_dir = project_path / "screenshots"
        if shot_dir.exists():
            images = sorted(
                [
                    p
                    for p in shot_dir.iterdir()
                    if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
                ],
                key=lambda p: p.name,
            )
            if images:
                return images[0]
        return None

    def _set_video_thumbnail(self, metadata: dict[str, Any], project_path: Path | None = None) -> None:
        thumb_pixmap: QPixmap | None = None
        if project_path:
            cover = self._resolve_project_cover(project_path, metadata)
            if cover and cover.exists():
                loaded = QPixmap(str(cover))
                if not loaded.isNull():
                    thumb_pixmap = loaded

        if thumb_pixmap is None:
            thumb_url = self._thumbnail_url_from_metadata(metadata)
            thumb_pixmap = self._fetch_thumbnail_pixmap(thumb_url)

        if thumb_pixmap is None:
            self.video_thumb_label.setPixmap(QPixmap())
            self.video_thumb_label.setText("暂无缩略图")
            return

        preview = thumb_pixmap.scaled(
            self.video_thumb_label.width() if self.video_thumb_label.width() > 120 else 320,
            self.video_thumb_label.height() if self.video_thumb_label.height() > 100 else 188,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.video_thumb_label.setText("")
        self.video_thumb_label.setPixmap(preview)

    def _update_video_info_panel(
        self,
        *,
        title: str,
        duration: Any,
        is_live: bool,
        source: str = "",
        project_path: Path | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        duration_text = _duration_text(duration)
        source_text = self._wrap_long_text(source, width=44)
        title_text = self._wrap_long_text(title, width=36)
        self.metadata_label.setText(
            f"标题:\n{title_text}\n时长: {duration_text}\n直播: {'是' if is_live else '否'}\n来源:\n{source_text}"
        )
        self._set_video_thumbnail(metadata or {}, project_path)

    def _set_history_item_selected(self, item: QListWidgetItem | None, selected: bool) -> None:
        if item is None:
            return
        widget = self.history_list.itemWidget(item)
        if widget is None:
            return
        widget.setProperty("selected", selected)
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _on_history_current_item_changed(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        self._set_history_item_selected(previous, False)
        self._set_history_item_selected(current, True)

    def _build_history_item_widget(self, project: dict[str, Any], load_cover: bool = True) -> QWidget:
        card = QWidget()
        card.setObjectName("HistoryItemCard")
        card.setProperty("selected", False)
        card.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(8, 8, 8, 8)
        card_layout.setSpacing(10)

        thumb = QLabel()
        thumb.setObjectName("HistoryItemThumb")
        thumb.setFixedSize(112, 64)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)

        if load_cover:
            self._set_history_thumb_from_project(thumb, project)
        else:
            thumb.setText("封面加载中")

        text_col = QWidget()
        text_layout = QVBoxLayout(text_col)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)

        top = QLabel(self._format_created_at(str(project.get("created_at", ""))))
        top.setObjectName("HistoryItemTime")
        title = str(project.get("title") or project.get("name") or "")
        bottom = MarqueeLabel(title)
        bottom.setObjectName("HistoryItemTitle")
        bottom.setFixedHeight(22)
        bottom.setToolTip(title)

        text_layout.addWidget(top)
        text_layout.addWidget(bottom)

        card_layout.addWidget(thumb, 0)
        card_layout.addWidget(text_col, 1)
        card.setMinimumHeight(80)
        return card

    def _set_history_thumb_from_project(self, thumb: QLabel, project: dict[str, Any]) -> None:
        project_path = Path(str(project.get("path", "")))
        pix: QPixmap | None = None
        cover = self._resolve_project_cover(project_path, project)
        if cover and cover.exists():
            cache_key = str(cover.resolve())
            cached = self._thumbnail_cache.get(cache_key)
            if cached is not None and not cached.isNull():
                pix = cached
            else:
                reader = QImageReader(str(cover))
                reader.setAutoTransform(True)
                reader.setScaledSize(QSize(thumb.width(), thumb.height()))
                loaded = QPixmap.fromImageReader(reader)
                if not loaded.isNull():
                    pix = loaded
                    self._thumbnail_cache[cache_key] = loaded
        if pix and not pix.isNull():
            thumb.setPixmap(self._scaled_cover_pixmap(pix, thumb.width(), thumb.height()))
            thumb.setText("")
        else:
            thumb.setPixmap(QPixmap())
            thumb.setText("无封面")

    def _schedule_history_cover_loading(self) -> None:
        if not self._history_cover_queue:
            return
        if self._history_cover_timer is None:
            self._history_cover_timer = QTimer(self)
            self._history_cover_timer.setInterval(12)
            self._history_cover_timer.timeout.connect(self._load_history_cover_batch)
        if not self._history_cover_timer.isActive():
            self._history_cover_timer.start()

    def _load_history_cover_batch(self) -> None:
        if not self._history_cover_queue:
            if self._history_cover_timer is not None:
                self._history_cover_timer.stop()
            return
        batch_size = 2
        for _ in range(batch_size):
            if not self._history_cover_queue:
                break
            item = self._history_cover_queue.pop(0)
            widget = self.history_list.itemWidget(item)
            if widget is None:
                continue
            thumb = widget.findChild(QLabel, "HistoryItemThumb")
            if thumb is None:
                continue
            project_data = item.data(Qt.ItemDataRole.UserRole + 1)
            if not isinstance(project_data, dict):
                continue
            self._set_history_thumb_from_project(thumb, project_data)

    def _open_project_folder(self, item: QListWidgetItem) -> None:
        raw_path = item.data(Qt.ItemDataRole.UserRole)
        if not raw_path:
            QMessageBox.warning(self, "无效项目", "该项目路径无效。")
            return
        path = Path(str(raw_path))
        if not path.exists():
            QMessageBox.warning(self, "目录不存在", "项目目录不存在。")
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(path))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
            self.append_log(f"已打开项目目录：{path}")
        except Exception as exc:
            QMessageBox.critical(self, "打开失败", f"无法打开项目目录：{exc}")

    def _on_open_download_folder(self) -> None:
        target = self.last_download_project_path
        if target is None or not target.exists():
            target = Path(str(self.settings.get("workspace_root", "")))
        if not target.exists():
            QMessageBox.warning(self, "目录不存在", "下载目录不存在。")
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(target))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
            self.append_log(f"已打开下载目录：{target}")
        except Exception as exc:
            QMessageBox.critical(self, "打开失败", f"无法打开下载目录：{exc}")

    def _on_url_input_changed(self, _text: str) -> None:
        self.download_button.setText("开始下载")
        self.latest_metadata = None
        self.latest_metadata_url = ""

    def _apply_history_projects(
        self,
        projects: list[dict[str, Any]],
        previous_path: str | None,
        load_covers: bool = False,
    ) -> None:
        self._history_cover_queue.clear()
        self.history_list.clear()
        for project in projects:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, project["path"])
            item.setData(Qt.ItemDataRole.UserRole + 1, project)
            widget = self._build_history_item_widget(project, load_cover=load_covers)
            hint = widget.sizeHint()
            item.setSizeHint(QSize(hint.width(), max(88, hint.height())))
            self.history_list.addItem(item)
            self.history_list.setItemWidget(item, widget)
            if not load_covers:
                self._history_cover_queue.append(item)
            if previous_path and str(previous_path) == str(project.get("path")):
                self.history_list.setCurrentItem(item)
                self._set_history_item_selected(item, True)
        if not load_covers:
            self._schedule_history_cover_loading()

    def refresh_history(self, silent: bool = False) -> None:
        projects = self.project_service.list_projects()
        previous_path = None
        current_item = self.history_list.currentItem()
        if current_item is not None:
            previous_path = current_item.data(Qt.ItemDataRole.UserRole)
        self._apply_history_projects(projects, previous_path, load_covers=False)
        if not silent:
            self.append_log(f"历史项目已刷新，共 {len(projects)} 个。")

    def _on_history_item_clicked(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        self.load_project(Path(path), switch_to_gallery=False)

    def _on_history_context_menu(self, pos) -> None:
        item = self.history_list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        open_action = menu.addAction("打开项目文件夹")
        menu.addSeparator()
        delete_action = menu.addAction("删除项目")
        selected = menu.exec(self.history_list.mapToGlobal(pos))
        if selected == open_action:
            self._open_project_folder(item)
        elif selected == delete_action:
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
        self.manual_frame_points = []
        self.current_page = 0
        self._update_manual_points_label()
        self._update_frame_hint()
        self.metadata_label.setText("尚未解析。")
        self.video_thumb_label.setPixmap(QPixmap())
        self.video_thumb_label.setText("暂无缩略图")
        self._set_pending_player_video(None)
        self._render_gallery_page()

    def load_project(self, path: Path, switch_to_gallery: bool = False) -> None:
        loaded = self.project_service.load_project(path)
        self.current_project_path = Path(loaded["path"])
        self.last_download_project_path = self.current_project_path
        self.current_metadata = loaded["metadata"]
        self.current_images = loaded["screenshots"]
        self.selected_images = {img for img in self.selected_images if img in self.current_images}
        self.manual_frame_points = []
        self.current_page = 0
        self._update_manual_points_label()
        self._update_frame_hint()
        self._update_video_info_panel(
            title=str(self.current_metadata.get("title") or self.current_project_path.name),
            duration=self.current_metadata.get("duration"),
            is_live=bool(self.current_metadata.get("is_live")),
            source=str(self.current_metadata.get("source_url", "")),
            project_path=self.current_project_path,
            metadata=self.current_metadata,
        )
        self._set_pending_player_video(self._current_video_path())
        self._update_selection_labels()
        self._render_gallery_page()
        if switch_to_gallery:
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
        url = self._normalized_input_url()
        if not url:
            QMessageBox.warning(self, "缺少 URL", "请先输入视频链接。")
            return

        self.parse_button.setEnabled(False)
        self.metadata_label.setText("正在解析，请稍候...")
        self.download_button.setText("开始下载")

        def task(progress_callback):
            return self.download_service.fetch_metadata(url, self.settings, progress_callback)

        def on_result(metadata: dict[str, Any]) -> None:
            self.latest_metadata = metadata
            self.latest_metadata_url = url
            title = metadata.get("title", "未知标题")
            is_live = bool(metadata.get("is_live"))
            self._update_video_info_panel(
                title=str(title),
                duration=metadata.get("duration"),
                is_live=is_live,
                source=url,
                project_path=None,
                metadata=metadata,
            )
            self.download_button.setEnabled(True)
            if is_live:
                QMessageBox.information(self, "直播提醒", "检测到直播链接，将按 yt-dlp 默认能力尝试下载。")

        self._run_worker(task, on_result=on_result, on_finished=lambda: self.parse_button.setEnabled(True))

    def _on_download_video(self) -> None:
        url = self._normalized_input_url()
        if not url:
            QMessageBox.warning(self, "缺少 URL", "请先输入视频链接。")
            return

        runtime_settings = dict(self.settings)
        runtime_settings["preferred_quality"] = self._selected_quality_value()

        self.download_button.setEnabled(False)
        self.download_button.setText("下载中...")

        def task(progress_callback):
            metadata: dict[str, Any] | None = (
                self.latest_metadata if self.latest_metadata_url == url and self.latest_metadata else None
            )
            if metadata is None:
                try:
                    metadata = self.download_service.fetch_metadata(url, runtime_settings, progress_callback)
                except Exception as metadata_error:
                    progress_callback(f"元数据解析失败，改为直接下载模式：{metadata_error}")
                    parsed = urlparse(url)
                    fallback_title = parsed.path.strip("/").split("/")[-1] or parsed.netloc or "untitled_video"
                    metadata = {"title": fallback_title, "duration": None, "is_live": False}

            title = str(metadata.get("title") or "untitled_video")
            project_dir = self.project_service.create_project(title, url, metadata)
            video_path = self.download_service.download_video(url, project_dir, runtime_settings, progress_callback)
            renamed_video_path = self.project_service.rename_video_to_title(project_dir, video_path, title)
            self.project_service.update_video_path(project_dir, renamed_video_path)
            return str(project_dir)

        def on_result(project_dir: str) -> None:
            self.refresh_history()
            project_path = Path(project_dir)
            self.last_download_project_path = project_path
            self.load_project(project_path, switch_to_gallery=False)
            self.download_button.setText("下载完成")
            QMessageBox.information(self, "下载完成", f"视频已下载到项目：{Path(project_dir).name}")

        self._run_worker(
            task,
            on_result=on_result,
            on_finished=lambda: self.download_button.setEnabled(True),
            on_error=lambda _err: self.download_button.setText("开始下载"),
        )

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
            return {"project_dir": str(project_dir), "duration": duration, "video_path": str(copied_video)}

        def on_result(payload: dict[str, Any]) -> None:
            project_dir = Path(payload["project_dir"])
            self.last_download_project_path = project_dir
            self._update_video_info_panel(
                title=source.stem,
                duration=payload.get("duration"),
                is_live=False,
                source=str(source),
                project_path=project_dir,
                metadata={"video_path": payload.get("video_path", "")},
            )
            self.refresh_history()
            self.load_project(project_dir, switch_to_gallery=False)
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
        manual_count = len(self.manual_frame_points)
        if duration:
            estimate = int(float(duration) / interval) + manual_count
            if estimate > 1000:
                choice = QMessageBox.question(
                    self,
                    "容量预警",
                    f"预计将生成约 {estimate} 张截图（含手动点位 {manual_count} 张），是否继续？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if choice != QMessageBox.StandardButton.Yes:
                    return

        self.extract_button.setEnabled(False)
        output_dir = self.current_project_path / "screenshots"
        clear_existing = self.clear_checkbox.isChecked()
        manual_points = list(self.manual_frame_points)

        def task(progress_callback):
            return self.frame_service.extract_frames(
                video_path=video_path,
                output_dir=output_dir,
                interval_seconds=interval,
                clear_existing=clear_existing,
                manual_timestamps=manual_points,
                progress_callback=progress_callback,
            )

        def on_result(count: int) -> None:
            self.append_log(f"当前项目抽帧完成，共 {count} 张。")
            self._refresh_current_screenshots()
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
            self.load_project(self.current_project_path, switch_to_gallery=False)
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

        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            fallback = QPixmap(220, 124)
            fallback.fill(Qt.GlobalColor.lightGray)
            button.setIcon(QIcon(fallback))
        else:
            thumb = pixmap.scaled(220, 124, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
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

    def _delete_screenshot_paths(self, paths: list[str]) -> None:
        targets: list[Path] = []
        for path in paths:
            text = str(path or "").strip()
            if not text:
                continue
            candidate = Path(text)
            if candidate.exists() and candidate.is_file():
                targets.append(candidate)
        if not targets:
            QMessageBox.information(self, "无可删除项", "未找到可删除的截图文件。")
            return

        confirm = QMessageBox.question(
            self,
            "确认删除截图",
            f"将删除 {len(targets)} 张截图，是否继续？",
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
            self._refresh_current_screenshots()
            self.append_log(f"已删除截图 {deleted} 张。")

        if failed:
            preview = "\n".join(failed[:6])
            extra = "\n..." if len(failed) > 6 else ""
            QMessageBox.warning(self, "部分删除失败", f"以下文件删除失败：\n{preview}{extra}")

    def _on_delete_selected_screenshots(self) -> None:
        if not self.selected_images:
            QMessageBox.warning(self, "未选择截图", "请先选择要删除的截图。")
            return
        self._delete_screenshot_paths(sorted(self.selected_images))

    def _on_thumb_context_menu(self, pos, image_path: str, button: QToolButton) -> None:
        menu = QMenu(self)
        delete_action = menu.addAction("删除截图")
        selected = menu.exec(button.mapToGlobal(pos))
        if selected == delete_action:
            self._delete_screenshot_paths([image_path])

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
            self.append_log(
                f"搜索任务已提交，共 {queued_count} 张，待处理队列={pending_count}"
            )

        def on_finished() -> None:
            self.search_button.setEnabled(True)
            self._active_search_worker = None

        self._active_search_worker = self._run_worker(task, on_result=on_result, on_finished=on_finished)

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

        self._run_worker(task, on_result=on_result, on_finished=on_finished)

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
        preferred_quality = str(self.settings.get("preferred_quality", "auto")).strip().lower() or "auto"
        quality_index = self.quality_combo.findData(preferred_quality)
        self.quality_combo.setCurrentIndex(quality_index if quality_index >= 0 else 0)

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
            "preferred_quality": self._selected_quality_value(),
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
