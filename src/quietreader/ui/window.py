from bisect import bisect_right
from collections import OrderedDict
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
from html import escape as html_escape
from math import ceil
from pathlib import Path
import os
import re
import sys
from threading import Event
from time import time

from PySide6.QtCore import (
    QAbstractListModel, QEvent, QItemSelectionModel, QModelIndex, QObject, QPoint, QRect,
    QRunnable, QSize, QThreadPool, Qt, QTimer, QUrl, Signal,
)
from PySide6.QtGui import (
    QAction, QColor, QCursor, QDesktopServices, QFont, QFontMetrics, QGuiApplication,
    QIcon, QKeySequence, QPainter, QShortcut, QTextDocument,
)
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QInputDialog, QLineEdit,
    QListView, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton,
    QSizeGrip, QSizePolicy, QProgressBar, QStackedWidget, QStyle, QStyledItemDelegate,
    QSystemTrayIcon, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from quietreader.content.local import ENCODINGS, ImportError as BookImportError, load_book, read_epub_title
from quietreader.desktop.color_picker import DesktopColorPicker, sample_window_perimeter
from quietreader.desktop.geometry import visible_bounds
from quietreader.desktop.hotkey import GlobalHotkey
from quietreader.desktop.window_effects import set_window_border_hidden
from quietreader.domain.models import Bookmark, Position, Settings, TermNote, restore_position
from quietreader.ui.appearance_popover import AppearancePopover
from quietreader.ui.controls import IconButton, SegmentedControl, ThinSlider
from quietreader.ui.covers import render_cover_pixmap
from quietreader.ui.focus_popover import FocusPopover
from quietreader.ui.icons import render_icon_pixmap, vector_icon
from quietreader.ui.reader import PageView
from quietreader.ui.settings import SettingsDialog
from quietreader.ui.settings_page import SettingsPage
from quietreader.ui.term_notes import ImportTermNotesDialog, TermHoverCard, TermNoteDialog
from quietreader.ui.theme import colors, stylesheet
from quietreader.ui.tokens import (
    COMPACT_BRAND_BREAKPOINT, CRUMB_MIN_WIDTH, NARROW_BREAKPOINT, TOPBAR_HEIGHT,
)
from quietreader.i18n import tr, install_qt_translator, resolve_language
from quietreader.ui.layout_panel import LayoutPanel
from quietreader.ui.book_info import BookInfoPanel
from quietreader.ui.moyu_strip import MoyuStrip, sentence_index_for_offset, sentence_spans
from quietreader.ui.file_dialog import BookFileDialog, default_book_directory
from quietreader.ui.toast import ToastNotice
from quietreader.persistence.store import (
    MAX_BOOKMARKS, MAX_BOOKMARK_NOTE, MAX_BOOK_RECORDS, MAX_BOOK_TITLE_OVERRIDE,
    MAX_JUMP_HISTORY, MAX_JUMP_HISTORY_BOOKS,
    MAX_TERM_NOTES, MAX_TERM_NOTES_BYTES_MIB, MAX_TERM_TEXT,
    normalize_term_note, normalize_term_text,
)
from quietreader.library.shelf import SUPPORTED_SUFFIXES, ShelfError, ShelfManager, UNCLASSIFIED, book_id_for_path
from quietreader.ui.shelf import ShelfPanel, display_title_for_record
from quietreader.updates import ReleaseChecker, automatic_check_due

_SEARCH_CHAPTER_ROLE = int(Qt.ItemDataRole.UserRole) + 1
_SEARCH_PAGE_ROLE = int(Qt.ItemDataRole.UserRole) + 2
_SEARCH_SNIPPET_ROLE = int(Qt.ItemDataRole.UserRole) + 3

_SETTINGS_SLIDER_FIELDS = frozenset({
    "font_size", "line_height", "letter_spacing", "paragraph_spacing", "padding",
    "opacity", "focus_complex_opacity",
})
_READER_LAYOUT_SETTINGS_FIELDS = frozenset({
    "font_size", "line_height", "letter_spacing", "paragraph_spacing", "padding",
})


def classify_dropped_urls(urls, max_items=MAX_BOOK_RECORDS):
    """Split dropped URLs into valid local book paths and skipped unsupported entries."""
    valid_paths = []
    seen = set()
    skipped = max(0, len(urls) - max_items)
    has_local_file = False
    for url in urls[:max_items]:
        if not url.isLocalFile() or url.host():
            skipped += 1
            continue
        local_file = url.toLocalFile()
        if not local_file or local_file.startswith("\\\\"):
            skipped += 1
            continue
        has_local_file = True
        try:
            resolved = Path(local_file).resolve(strict=True)
        except (OSError, RuntimeError):
            skipped += 1
            continue
        if not resolved.is_file() or resolved.suffix.lower() not in SUPPORTED_SUFFIXES:
            skipped += 1
            continue
        key = str(resolved).casefold()
        if key in seen:
            continue
        seen.add(key)
        valid_paths.append(resolved)
    return valid_paths, skipped, has_local_file


def book_icon():
    if getattr(sys, "frozen", False):
        asset = Path(sys._MEIPASS) / "quietreader" / "assets" / "quietreader.png"
    else:
        asset = Path(__file__).resolve().parents[1] / "assets" / "quietreader.png"
    return QIcon(str(asset))


class ImportSignals(QObject):
    loaded = Signal(object)
    failed = Signal(str, bool)
    finished = Signal()


class ImportTask(QRunnable):
    def __init__(self, path, encoding):
        super().__init__()
        self.path, self.encoding = path, encoding
        self.signals = ImportSignals()

    def run(self):
        try:
            self.signals.loaded.emit(load_book(self.path, self.encoding))
        except BookImportError as error:
            self.signals.failed.emit(str(error), error.encoding_choice)
        except Exception:
            self.signals.failed.emit("导入失败，文件结构可能不受支持。请检查文件后重试。", False)
        finally:
            self.signals.finished.emit()


class BookSearchSignals(QObject):
    completed = Signal(object)
    failed = Signal(object)


class BookSearchTask(QRunnable):
    def __init__(self, generation, book_id, query, section_texts, reference, navigate, anchor, cancel_event):
        super().__init__()
        self.generation = generation
        self.book_id = book_id
        self.query = query
        self.section_texts = section_texts
        self.reference = reference
        self.navigate = navigate
        self.anchor = anchor
        self.cancel_event = cancel_event
        self.signals = BookSearchSignals()

    def run(self):
        try:
            pattern = re.compile(re.escape(self.query), re.IGNORECASE)
            matches = []
            section_ranges = {}
            chosen = None
            ref_sec, ref_off = self.reference
            for sec_idx, text in enumerate(self.section_texts):
                if self.cancel_event.is_set():
                    return
                range_start = len(matches)
                for match in pattern.finditer(text):
                    if self.cancel_event.is_set():
                        return
                    start, end = match.start(), match.end()
                    matches.append((sec_idx, start, end))
                    if chosen is None and (sec_idx, start) >= (ref_sec, ref_off):
                        chosen = len(matches) - 1
                if len(matches) > range_start:
                    section_ranges[sec_idx] = (range_start, len(matches))

            if self.cancel_event.is_set():
                return
            if matches and chosen is None:
                chosen = 0
            self.signals.completed.emit((
                self.generation, self.book_id, self.query, matches,
                section_ranges, chosen, self.navigate, self.anchor,
            ))
        except Exception:
            self.signals.failed.emit((self.generation, self.book_id, self.query))


class _JumpTargetLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__("", parent)
        self._chapter = ""
        self._page_text = ""
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def set_target(self, chapter: str, page_text: str):
        self._chapter = chapter
        self._page_text = page_text
        full_text = f"{chapter} · {page_text}" if chapter and page_text else chapter
        self.setToolTip(full_text)
        self._apply_elide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self):
        available_width = self.contentsRect().width()
        if not self._chapter:
            visible_text = ""
        elif available_width <= 0:
            visible_text = f"{self._chapter} · {self._page_text}" if self._page_text else self._chapter
        else:
            metrics = self.fontMetrics()
            suffix = f" · {self._page_text}" if self._page_text else ""
            chapter_width = available_width - metrics.horizontalAdvance(suffix)
            if chapter_width >= 26:
                chapter = metrics.elidedText(
                    self._chapter, Qt.TextElideMode.ElideRight, chapter_width,
                )
                visible_text = f"{chapter}{suffix}"
            else:
                visible_text = metrics.elidedText(
                    self._page_text or self._chapter,
                    Qt.TextElideMode.ElideRight, available_width,
                )
        if visible_text != self.text():
            self.setText(visible_text)


class DragHeader(QWidget):
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.window().windowHandle():
            self.window().windowHandle().startSystemMove()
        else:
            super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not getattr(self.window(), "focus_mode", False):
            toggle = getattr(self.window(), "toggle_maximize", None)
            if callable(toggle):
                toggle()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)


class _SearchLineEdit(QLineEdit):
    """In-book search input supporting Enter / Shift+Enter match stepping and Esc close."""

    next_requested = Signal()
    previous_requested = Signal()
    close_requested = Signal()

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.previous_requested.emit()
            else:
                self.next_requested.emit()
            event.accept()
            return
        if key == Qt.Key.Key_Escape:
            self.close_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class _SearchResultModel(QAbstractListModel):
    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self.owner = owner
        self.book = None
        self.matches: list[tuple[int, int, int]] = []
        self._entries: dict[int, tuple[str, tuple[str, str, str, bool, bool]]] = {}
        self._pages: dict[int, int | None] = {}
        self._page_queue: list[int] = []
        self._pending_pages: set[int] = set()
        self._page_task_scheduled = False
        self._section_page_counts: dict[int, int] = {}
        self._chapter_starts: dict[int, tuple[int, str]] = {}
        self._section_page_offsets: dict[int, int] = {}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.matches)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.matches):
            return None
        row = index.row()
        chapter, snippet = self._entry(row)
        page = self._pages.get(row)
        page_label = self.owner.t("第 {page} 页", page=page) if page is not None else "…"
        if role == Qt.ItemDataRole.DisplayRole:
            before, match, after, clipped_left, clipped_right = snippet
            prefix = "…" if clipped_left else ""
            suffix = "…" if clipped_right else ""
            return (
                f"{prefix}{before}{match}{after}{suffix}\n"
                f"{chapter} · {page_label}"
            )
        if role == _SEARCH_CHAPTER_ROLE:
            return chapter
        if role == _SEARCH_PAGE_ROLE:
            if row not in self._pages:
                self._schedule_page(row)
            return page
        if role == _SEARCH_SNIPPET_ROLE:
            return snippet
        if role == Qt.ItemDataRole.ToolTipRole:
            before, match, after, clipped_left, clipped_right = snippet
            prefix = "…" if clipped_left else ""
            suffix = "…" if clipped_right else ""
            return (
                f"{prefix}{before}{match}{after}{suffix}\n"
                f"{chapter} · {page_label}"
            )
        return None

    def set_matches(self, book, matches):
        self.beginResetModel()
        self.book = book
        self.matches = matches
        self._entries.clear()
        self._pages.clear()
        self._page_queue.clear()
        self._pending_pages.clear()
        self._section_page_counts.clear()
        self._chapter_starts.clear()
        self._section_page_offsets.clear()
        self.endResetModel()

    def invalidate_pages(self):
        self._entries.clear()
        self._pages.clear()
        self._page_queue.clear()
        self._pending_pages.clear()
        self._section_page_counts.clear()
        self._chapter_starts.clear()
        self._section_page_offsets.clear()
        if self.matches:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self.matches) - 1, 0),
                [int(Qt.ItemDataRole.DisplayRole), int(Qt.ItemDataRole.SizeHintRole)],
            )

    def _schedule_page(self, row: int):
        if row in self._pages or row in self._pending_pages:
            return
        self._pending_pages.add(row)
        self._page_queue.append(row)
        if not self._page_task_scheduled:
            self._page_task_scheduled = True
            QTimer.singleShot(0, self._compute_next_page)

    def _compute_next_page(self):
        self._page_task_scheduled = False
        if not self._page_queue or not self.book:
            return
        row = self._page_queue.pop(0)
        if not 0 <= row < len(self.matches):
            self._pending_pages.discard(row)
        else:
            model_index = self.index(row, 0)
            view = self.owner.search_results
            if not view.isVisible() or not view.visualRect(model_index).intersects(view.viewport().rect()):
                self._pending_pages.discard(row)
            else:
                section_index, start, _end = self.matches[row]
                section = self.book.sections[section_index]
                chapter_start, _chapter = self._chapter_start(section_index)
                try:
                    ready = self._advance_chapter_page_offset(section_index, chapter_start)
                    if ready:
                        page_offset = self._section_page_offsets[section_index]
                        self._pages[row] = page_offset + self.owner.reader.page_for_section_offset(section, start)
                        self._pending_pages.discard(row)
                        self.dataChanged.emit(
                            model_index,
                            model_index,
                            [
                                int(Qt.ItemDataRole.DisplayRole),
                                int(Qt.ItemDataRole.ToolTipRole),
                                _SEARCH_PAGE_ROLE,
                            ],
                        )
                    else:
                        # Build long chapter prefixes one section per event-loop turn.
                        self._page_queue.append(row)
                except (IndexError, KeyError, RuntimeError, ValueError):
                    self._pages[row] = None
                    self._pending_pages.discard(row)
                    self.dataChanged.emit(
                        model_index,
                        model_index,
                        [
                            int(Qt.ItemDataRole.DisplayRole),
                            int(Qt.ItemDataRole.ToolTipRole),
                            _SEARCH_PAGE_ROLE,
                        ],
                    )

        if self._page_queue:
            self._page_task_scheduled = True
            QTimer.singleShot(10, self._compute_next_page)

    def _chapter_start(self, section_index: int) -> tuple[int, str]:
        cached = self._chapter_starts.get(section_index)
        if cached is not None:
            return cached
        section = self.book.sections[section_index]
        identity = self.owner._chapter_identity(section)
        chapter_start = section_index
        while chapter_start > 0 and self.owner._chapter_identity(self.book.sections[chapter_start - 1]) == identity:
            chapter_start -= 1
        chapter = self.book.sections[chapter_start].title.strip() or self.book.title
        for index in range(chapter_start, section_index + 1):
            self._chapter_starts[index] = (chapter_start, chapter)
        return chapter_start, chapter

    def _advance_chapter_page_offset(self, section_index: int, chapter_start: int) -> bool:
        if section_index in self._section_page_offsets:
            return True
        if chapter_start not in self._section_page_offsets:
            self._section_page_offsets[chapter_start] = 0
            return section_index == chapter_start
        for index in range(chapter_start + 1, section_index + 1):
            if index in self._section_page_offsets:
                continue
            previous_offset = self._section_page_offsets.get(index - 1)
            if previous_offset is None:
                return False
            self._section_page_offsets[index] = previous_offset + self._section_page_count(index - 1)
            return index == section_index
        return section_index in self._section_page_offsets

    def _section_page_count(self, section_index: int) -> int:
        if section_index not in self._section_page_counts:
            section = self.book.sections[section_index]
            self._section_page_counts[section_index] = max(
                1,
                self.owner.reader.page_for_section_offset(section, len(section.text)),
            )
        return self._section_page_counts[section_index]

    def _entry(self, row: int) -> tuple[str, tuple[str, str, str, bool, bool]]:
        cached = self._entries.get(row)
        if cached is not None:
            return cached

        section_index, start, end = self.matches[row]
        section = self.book.sections[section_index]
        _chapter_start, chapter = self._chapter_start(section_index)
        viewport_width = max(1, self.owner.search_results.viewport().width())
        context_chars = max(20, min(88, viewport_width // 7))
        before_chars = context_chars * 2 // 5
        after_chars = context_chars - before_chars

        context_start = max(0, start - before_chars)
        context_end = min(len(section.text), end + after_chars)

        def flatten(value: str) -> str:
            value = value.replace("\ufffc", " ")
            return re.sub(r"\s+", " ", value).strip()

        snippet = (
            flatten(section.text[context_start:start]),
            flatten(section.text[start:end]),
            flatten(section.text[end:context_end]),
            context_start > 0,
            context_end < len(section.text),
        )
        entry = (chapter, snippet)
        self._entries[row] = entry
        return entry


class _SearchResultDelegate(QStyledItemDelegate):
    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self.owner = owner
        self._palette = colors(owner.settings.theme)
        self._size_cache = OrderedDict()

    def apply_palette(self, palette: dict[str, str]):
        self._palette = dict(palette)

    def _body_document(self, snippet, font):
        before, match, after, clipped_left, clipped_right = snippet
        prefix = "…" if clipped_left else ""
        suffix = "…" if clipped_right else ""
        highlight_style = (
            f"background-color:{self._palette['accent_soft']};"
            f"color:{self._palette['accent']};font-weight:700"
        )
        body_html = (
            f"{html_escape(prefix + before)}"
            f"<span style=\"{highlight_style}\">{html_escape(match)}</span>"
            f"{html_escape(after + suffix)}"
        )
        body = QTextDocument()
        body.setDocumentMargin(0)
        body.setDefaultFont(font)
        body.setHtml(f"<p style=\"margin:0;color:{self._palette['ink']}\">{body_html}</p>")
        return body

    def sizeHint(self, option, index):
        width = max(0, option.rect.width()) or self.owner.search_results.viewport().width()
        snippet = index.data(_SEARCH_SNIPPET_ROLE) or ("", "", "", False, False)
        body_font = QFont(option.font)
        body_font.setPointSizeF(10.5)
        available_body_width = max(1, width - 6 - 16)
        # Round down so the measured height never underestimates the painted text.
        body_width = max(1, available_body_width // 8 * 8)
        font_signature = (
            body_font.family(),
            body_font.pointSizeF(),
            body_font.pixelSize(),
            int(body_font.weight()),
        )
        cache_key = (index.row(), body_width, font_signature)
        cached = self._size_cache.get(cache_key)
        if cached is not None and cached[0] == snippet:
            self._size_cache.move_to_end(cache_key)
            body_height = cached[1]
        else:
            body = self._body_document(snippet, body_font)
            body.setTextWidth(body_width)
            body_height = body.size().height()
            self._size_cache[cache_key] = (snippet, body_height)
            self._size_cache.move_to_end(cache_key)
            while len(self._size_cache) > 512:
                self._size_cache.popitem(last=False)
        return QSize(width, max(64, ceil(body_height) + 46))

    def paint(self, painter, option, index):
        palette = self._palette
        card = option.rect.adjusted(3, 3, -3, -3)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        background = (
            palette["selected"] if selected
            else palette["accent_hover"] if hovered
            else palette["elevated"]
        )

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor(palette["accent"] if selected else palette["line"]))
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(card, 8, 8)

        chapter = str(index.data(_SEARCH_CHAPTER_ROLE) or "")
        page = index.data(_SEARCH_PAGE_ROLE)
        page_label = self.owner.t("第 {page} 页", page=page) if page is not None else "…"
        heading_font = QFont(option.font)
        heading_font.setPointSizeF(9.5)
        heading_font.setWeight(QFont.Weight.DemiBold)
        metrics = QFontMetrics(heading_font)
        page_width = metrics.horizontalAdvance(page_label)
        footer = card.adjusted(8, 0, -8, -7)
        footer.setTop(footer.bottom() - 17)
        page_rect = footer.adjusted(max(0, footer.width() - page_width), 0, 0, 0)
        chapter_rect = footer.adjusted(0, 0, -(page_width + 8), 0)
        painter.setFont(heading_font)
        painter.setPen(QColor(palette["muted"]))
        painter.drawText(
            chapter_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            metrics.elidedText(chapter, Qt.TextElideMode.ElideRight, max(0, chapter_rect.width())),
        )
        painter.setPen(QColor(palette["ink_secondary"]))
        painter.drawText(page_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, page_label)

        snippet = index.data(_SEARCH_SNIPPET_ROLE)
        body_font = QFont(option.font)
        body_font.setPointSizeF(10.5)
        body = self._body_document(snippet, body_font)
        body_rect = card.adjusted(8, 8, -8, 0)
        body_rect.setBottom(footer.top() - 6)
        body.setTextWidth(body_rect.width())
        painter.setPen(QColor(palette["ink"]))
        painter.save()
        painter.setClipRect(body_rect)
        painter.translate(body_rect.topLeft())
        body.drawContents(painter, body_rect.translated(-body_rect.topLeft()))
        painter.restore()
        painter.restore()


class _SidebarTabSwitcher(QWidget):
    """Foliate-style stacked sidebar content with a responsive bottom tab switcher."""

    currentChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._palette = {}
        self._buttons: list[tuple[QToolButton, str]] = []
        self._labels: list[str] = []
        self._icon_only = False
        self._icon_px = 13
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        self.stack = QStackedWidget(self)
        outer.addWidget(self.stack, 1)

        self.bar = QFrame(self)
        self.bar.setObjectName("sidebarBottomBar")
        self.bar_layout = QHBoxLayout(self.bar)
        self.bar_layout.setContentsMargins(0, 6, 0, 0)
        self.bar_layout.setSpacing(2)
        outer.addWidget(self.bar)

    def addTab(self, widget: QWidget, label: str, icon_name: str = ""):
        index = self.stack.addWidget(widget)
        btn = QToolButton(self.bar)
        btn.setObjectName("sidebarTabBtn")
        btn.setText(label)
        btn.setToolTip(label)
        btn.setAccessibleName(label)
        btn.setAutoRaise(True)
        btn.setCheckable(True)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setMinimumWidth(0)
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextUnderIcon if icon_name else Qt.ToolButtonStyle.ToolButtonTextOnly
        )
        btn.setIconSize(QSize(13, 13))
        btn.clicked.connect(lambda _c=False, idx=index: self.setCurrentIndex(idx))
        self._buttons.append((btn, icon_name))
        self._labels.append(label)
        self.bar_layout.addWidget(btn)
        if index == 0:
            self.setCurrentIndex(0)
        self._sync_button_modes()
        return index

    def setCurrentIndex(self, index: int):
        if not 0 <= index < self.stack.count():
            return
        previous = self.stack.currentIndex()
        self.stack.setCurrentIndex(index)
        for i, (btn, _) in enumerate(self._buttons):
            btn.setChecked(i == index)
        self._refresh_icons()
        if previous != index:
            self.currentChanged.emit(index)

    def currentIndex(self) -> int:
        return self.stack.currentIndex()

    def setTabVisible(self, index: int, visible: bool):
        if 0 <= index < len(self._buttons):
            self._buttons[index][0].setVisible(visible)
            if not visible and self.stack.currentIndex() == index:
                self.setCurrentIndex(0)
            self._sync_button_modes()

    def setTabText(self, index: int, text: str):
        if 0 <= index < len(self._buttons):
            self._labels[index] = text
            btn = self._buttons[index][0]
            btn.setText(text)
            btn.setToolTip(text)
            btn.setAccessibleName(text)
            self._sync_button_modes()

    def apply_palette(self, palette: dict[str, str]):
        self._palette = dict(palette)
        self._refresh_icons()
        self._sync_button_modes()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_button_modes()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_button_modes()

    def _sync_button_modes(self):
        if not self._buttons:
            return
        avail = self.bar.width() if self.bar.width() > 20 else self.width()
        if avail <= 20:
            avail = 204
        visible_indices = [i for i, (btn, _) in enumerate(self._buttons) if not btn.isHidden()]
        if not visible_indices:
            return
        needed_widths: dict[int, int] = {}
        total_labeled = 0
        for i in visible_indices:
            btn, icon_name = self._buttons[i]
            btn.ensurePolished()
            metrics = QFontMetrics(btn.font())
            label_w = metrics.horizontalAdvance(self._labels[i]) + 8
            need = max(28 if icon_name else 24, label_w)
            needed_widths[i] = need
            total_labeled += need
        spacing_total = self.bar_layout.spacing() * max(0, len(visible_indices) - 1)
        icon_only = (total_labeled + spacing_total) > avail
        icon_px = 13 if (icon_only and len(visible_indices) >= 6) else (15 if icon_only else 13)
        changed_mode = (icon_only != self._icon_only) or (icon_px != self._icon_px)
        self._icon_only = icon_only
        self._icon_px = icon_px
        for i, (btn, icon_name) in enumerate(self._buttons):
            if icon_name:
                btn.setToolButtonStyle(
                    Qt.ToolButtonStyle.ToolButtonIconOnly
                    if icon_only
                    else Qt.ToolButtonStyle.ToolButtonTextUnderIcon
                )
                btn.setIconSize(QSize(icon_px, icon_px))
                btn.setFixedHeight(30 if icon_only else 38)
            else:
                btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
                btn.setFixedHeight(30 if icon_only else 38)
            stretch = 1 if icon_only else needed_widths.get(i, 36)
            self.bar_layout.setStretchFactor(btn, max(1, stretch))
        if changed_mode:
            self._refresh_icons()

    def _refresh_icons(self):
        if not self._palette:
            return
        icon_px = self._icon_px
        for btn, icon_name in self._buttons:
            if not icon_name:
                continue
            color = self._palette.get("accent", "#2e5641") if btn.isChecked() else self._palette.get("muted", "#647569")
            btn.setIcon(vector_icon(icon_name, color, icon_px))


class WindowSurface(QWidget):
    """Surface with frameless-window resize hit zones along its outer padding."""

    _resize_margin = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)

    def _edges_at(self, position):
        window = self.window()
        if window.isMaximized() or window.isFullScreen():
            return Qt.Edge(0)
        x, y = position.x(), position.y()
        width, height = self.width(), self.height()
        edges = Qt.Edge(0)
        if x < self._resize_margin:
            edges |= Qt.Edge.LeftEdge
        elif x >= width - self._resize_margin:
            edges |= Qt.Edge.RightEdge
        if y < self._resize_margin:
            edges |= Qt.Edge.TopEdge
        elif y >= height - self._resize_margin:
            edges |= Qt.Edge.BottomEdge
        return edges

    @staticmethod
    def _cursor_for_edges(edges):
        left = bool(edges & Qt.Edge.LeftEdge)
        right = bool(edges & Qt.Edge.RightEdge)
        top = bool(edges & Qt.Edge.TopEdge)
        bottom = bool(edges & Qt.Edge.BottomEdge)
        if (top and left) or (bottom and right):
            return Qt.CursorShape.SizeFDiagCursor
        if (top and right) or (bottom and left):
            return Qt.CursorShape.SizeBDiagCursor
        if left or right:
            return Qt.CursorShape.SizeHorCursor
        if top or bottom:
            return Qt.CursorShape.SizeVerCursor
        return None

    def mouseMoveEvent(self, event):
        if not getattr(self.window(), "focus_mode", False):
            cursor = self._cursor_for_edges(self._edges_at(event.position()))
            if cursor is None:
                self.unsetCursor()
            else:
                self.setCursor(cursor)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.unsetCursor()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not getattr(self.window(), "focus_mode", False):
            edges = self._edges_at(event.position())
            handle = self.window().windowHandle()
            if edges and handle and handle.startSystemResize(edges):
                event.accept()
                return
        super().mousePressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, store, enable_system=True):
        super().__init__()
        # Configure Qt's translucent backing store before the native HWND is
        # created; changing WA_TranslucentBackground after show is not reliable.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.store = store
        self.shelf_manager = ShelfManager(store.path.parent)
        self.settings = Settings.from_dict(store.state["settings"])
        self._reader_settings_dirty = False
        self.enable_system = enable_system
        self.book = None
        self.section_index = 0
        self.loading = False
        self.pending_path = None
        self._last_browse_dir = store.state.get("last_browse_dir", "")
        self.quitting = False
        self.closed = False
        self._window_hidden = False
        self.focus_mode = False
        self.moyu_mode = False
        self._moyu_return_to_focus = False
        self._moyu_geometry = None
        self._moyu_bar_geometry = None
        self._moyu_previous_maximum_size = None
        self._moyu_was_maximized = False
        self._moyu_sentences = []
        self._moyu_sentence_index = -1
        self._moyu_offset = 0
        self._moyu_fragment_history = []
        self._moyu_preserve_external_focus = False
        self._moyu_auto_hidden = False
        self._moyu_auto_hide_armed = False
        self._moyu_menu_open = False
        self.sidebar_collapsed = False
        self._user_sidebar_collapsed = False
        self._was_narrow = False
        self._shelf_actions_in_narrow = False
        self._pre_settings_page = None
        self._themed_controls = []
        self._focus_opacity_override = None
        self._sampled_focus_color = ""
        self._sampled_focus_complex = False
        self._focus_geometry_dirty = False
        self._perimeter_capture = None
        self._focus_adjust_active = False
        self._focus_adjust_drag = None
        self._focus_system_drag_active = False
        self._focus_adjust_mouse_drag_active = False
        self._focus_cursor_override = False
        self._drag_hover_active = False
        self._drag_has_valid = False
        self._chrome_visibility = {}
        self._jump_history: list[tuple[int, int]] = []
        self._forward_history: list[tuple[int, int]] = []
        self._max_jump_history = MAX_JUMP_HISTORY
        self._search_active = False
        self._search_return_tab = 0
        self._last_sidebar_tab_index = 0
        self._sidebar_info_return_tab = 0
        self._search_matches: list[tuple[int, int, int]] = []
        self._search_section_match_ranges: dict[int, tuple[int, int]] = {}
        self._search_index = -1
        self._search_origin: tuple[int, int] | None = None
        self._search_origin_pushed = False
        self._in_search_navigation = False
        self._book_search_generation = 0
        self._book_search_cancel_event = None
        self._book_search_request = None
        self.status_source = ("离线优先。添加书籍即可开始阅读。", {})
        self._toast_status_source = None
        self.tray = None
        self.hotkey = None
        self.emergency_hotkey = None
        self.moyu_hotkeys = []
        self._shortcut_capture_active = False
        self.worker = None
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.search_pool = QThreadPool(self)
        self.search_pool.setMaxThreadCount(1)
        self.book_search_timer = QTimer(self)
        self.book_search_timer.setSingleShot(True)
        self.book_search_timer.setInterval(220)
        self.book_search_timer.timeout.connect(self._dispatch_book_search)
        self.jump_target_page_timer = QTimer(self)
        self.jump_target_page_timer.setSingleShot(True)
        self.jump_target_page_timer.setInterval(180)
        self.jump_target_page_timer.timeout.connect(self._refresh_jump_target_pages)
        self.setWindowTitle("QuietReader · 静读")
        self.setMinimumSize(380, 360)
        self.setWindowIcon(book_icon())
        self.setAcceptDrops(True)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(350)
        self.save_timer.timeout.connect(self.save_state)
        self.release_checker = ReleaseChecker(self)
        self.release_checker.finished.connect(self._update_check_finished)
        self._automatic_update_request = False
        self._focus_resample_timer = QTimer(self)
        self._focus_resample_timer.setSingleShot(True)
        self._focus_resample_timer.setInterval(140)
        self._focus_resample_timer.timeout.connect(self._run_scheduled_focus_resample)
        self._focus_resample_pending = False
        self._moyu_visibility_timer = QTimer(self)
        self._moyu_visibility_timer.setInterval(80)
        self._moyu_visibility_timer.timeout.connect(self._sync_moyu_visibility)
        self._moyu_hide_timer = QTimer(self)
        self._moyu_hide_timer.setSingleShot(True)
        self._moyu_hide_timer.setInterval(240)
        self._moyu_hide_timer.timeout.connect(self._hide_moyu_if_away)
        self._focus_picker = DesktopColorPicker(self)
        self._focus_picker.selected.connect(self._on_focus_eyedropper_selected)
        self._focus_picker.failed.connect(self.set_status)
        self._build_ui()
        app = QApplication.instance()
        if app:
            app.installEventFilter(self)
        if enable_system:
            self._setup_system()
        self.apply_settings(self.settings)
        screens = [s.availableGeometry().getRect() for s in QGuiApplication.screens()]
        self.setGeometry(*visible_bounds(store.state["window"], screens))
        QGuiApplication.styleHints().colorSchemeChanged.connect(self._system_theme_changed)
        QGuiApplication.instance().screenRemoved.connect(self._screen_removed)
        self._refresh_recent()
        if store.warning:
            self.set_status(store.warning)

    @staticmethod
    def _label(text="", name=""):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        if name:
            label.setObjectName(name)
        if text:
            label.setProperty("translation_source", text)
        return label

    def _button(self, text, callback, checkable=False):
        button = QPushButton(text)
        button.setProperty("translation_source", text)
        button.setCheckable(checkable)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(callback)
        return button

    def _icon_button(self, icon_name, callback, accessible_name, size=15, checkable=False, object_name="iconButton", fixed_size=(26, 26)):
        button = IconButton(icon_name, size=size, checkable=checkable, object_name=object_name)
        button.setToolTip(accessible_name)
        button.setProperty("translation_tip", accessible_name)
        button.setAccessibleName(accessible_name)
        button.setProperty("translation_accessible", accessible_name)
        button.setFixedSize(*fixed_size)
        button.clicked.connect(callback)
        self._themed_controls.append(button)
        return button

    def _build_ui(self):
        self.surface = WindowSurface()
        self.surface.setObjectName("surface")
        self.surface.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCentralWidget(self.surface)
        root = self.root_layout = QVBoxLayout(self.surface)
        root.setContentsMargins(12, 6, 12, 6)
        root.setSpacing(8)

        # Single-row unified top bar (merges window chrome, navigation, context actions)
        header = self.title_bar = DragHeader()
        header.setObjectName("titleBar")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        header.setFixedHeight(TOPBAR_HEIGHT)
        top = self.topbar_layout = QHBoxLayout(header)
        top.setContentsMargins(2, 4, 2, 6)
        top.setSpacing(5)

        self.brand_mark = QLabel()
        self.brand_mark.setObjectName("brandMark")
        self.brand_mark.setFixedSize(26, 26)
        self.brand_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.brand_mark.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        top.addWidget(self.brand_mark)

        self.settings_back_button = QPushButton("返回阅读")
        self.settings_back_button.setObjectName("backPill")
        self.settings_back_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.settings_back_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_back_button.clicked.connect(self.close_settings)
        self.settings_back_button.hide()
        top.addWidget(self.settings_back_button)

        self.settings_top_sep = QFrame()
        self.settings_top_sep.setObjectName("toolbarSep")
        self.settings_top_sep.setFixedSize(1, 16)
        self.settings_top_sep.hide()
        top.addWidget(self.settings_top_sep)

        self.settings_top_title = self._label("阅读器配置", "settingsTopTitle")
        self.settings_top_title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.settings_top_title.hide()
        top.addWidget(self.settings_top_title)

        brand = self.brand = self._label("静读", "brand")
        brand.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        top.addWidget(brand)

        self.eyebrow = self._label("QUIET READER", "eyebrow")
        self.eyebrow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.eyebrow.hide()

        self.page_navigation = SegmentedControl()
        self.page_navigation.setObjectName("pageNavigation")
        self.reading_button = self.page_navigation.add_segment(
            "reading", "阅读", "进入阅读页面，查看目录、书签和资料",
        )
        self.reading_button.setObjectName("pageNav")
        self.reading_button.setProperty("translation_source", "阅读")
        self.reading_button.setProperty("translation_tip", "进入阅读页面，查看目录、书签和资料")
        self.shelf_button = self.page_navigation.add_segment(
            "shelf", "书架", "浏览最近阅读、所有书籍和分类",
        )
        self.shelf_button.setObjectName("pageNav")
        self.shelf_button.setProperty("translation_source", "书架")
        self.shelf_button.setProperty("translation_tip", "浏览最近阅读、所有书籍和分类")
        self.page_navigation.currentChanged.connect(
            lambda key: self.show_reading_page() if key == "reading" else self.show_shelf_page()
        )
        top.addWidget(self.page_navigation)

        self.sidebar_toggle = self._icon_button("sidebar", self.toggle_sidebar, "收起目录栏", size=15)
        top.addWidget(self.sidebar_toggle)

        self.header_crumb = QLabel("")
        self.header_crumb.setObjectName("headerCrumb")
        self.header_crumb.setTextFormat(Qt.TextFormat.PlainText)
        self.header_crumb.setMinimumWidth(0)
        self.header_crumb.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.header_crumb.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        top.addWidget(self.header_crumb, 1)

        self.topbar_actions = QWidget()
        self.topbar_actions.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.topbar_actions_layout = QHBoxLayout(self.topbar_actions)
        self.topbar_actions_layout.setContentsMargins(0, 0, 0, 0)
        self.topbar_actions_layout.setSpacing(3)

        self.shelf_actions = QWidget()
        self.shelf_actions.setObjectName("shelfActions")
        self.shelf_actions.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.shelf_actions_layout = QHBoxLayout(self.shelf_actions)
        self.shelf_actions_layout.setContentsMargins(0, 0, 0, 0)
        self.shelf_actions_layout.setSpacing(6)
        self.topbar_actions_layout.addWidget(self.shelf_actions)

        self.encoding_button = self._icon_button(
            "encoding", self.toggle_encoding, "TXT 乱码时，手动选择编码重新读取", size=15,
        )
        self.encoding_button.hide()
        self.topbar_actions_layout.addWidget(self.encoding_button)

        self.layout_button = QPushButton("Aa")
        self.layout_button.setObjectName("typographyButton")
        self.layout_button.setCheckable(True)
        self.layout_button.setFixedSize(30, 26)
        self.layout_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.layout_button.setToolTip("排版")
        self.layout_button.setProperty("translation_tip", "排版")
        self.layout_button.setAccessibleName("排版")
        self.layout_button.setProperty("translation_accessible", "排版")
        self.layout_button.clicked.connect(self.show_layout_panel)
        self.topbar_actions_layout.addWidget(self.layout_button)

        self.pin_button = self._icon_button("pin", self.toggle_pin, "置顶", size=15, checkable=True)
        self.topbar_actions_layout.addWidget(self.pin_button)

        self.focus_button = self._icon_button("fish", self.show_focus_panel, "专注", size=20, checkable=True)
        self.topbar_actions_layout.addWidget(self.focus_button)

        self.config_button = self._icon_button("settings", self.toggle_settings, "配置", size=15, checkable=True)
        self.topbar_actions_layout.addWidget(self.config_button)

        self.topbar_sep = QFrame()
        self.topbar_sep.setObjectName("toolbarSep")
        self.topbar_sep.setFixedSize(1, 16)
        self.topbar_actions_layout.addWidget(self.topbar_sep)

        self.minimize_button = self._icon_button(
            "minimize", lambda: self.hide_or_minimize(), "隐藏 / 最小化",
            size=13, object_name="windowControl", fixed_size=(26, 26),
        )
        self.maximize_button = self._icon_button(
            "maximize", lambda: self.toggle_maximize(), "最大化窗口",
            size=13, object_name="windowControl", fixed_size=(26, 26),
        )
        self.close_button = self._icon_button(
            "close", lambda: self.close(), "关闭窗口",
            size=13, object_name="windowClose", fixed_size=(26, 26),
        )
        self.topbar_actions_layout.addWidget(self.minimize_button)
        self.topbar_actions_layout.addWidget(self.maximize_button)
        self.topbar_actions_layout.addWidget(self.close_button)
        top.addWidget(self.topbar_actions)
        root.addWidget(header)

        # Compact overflow bar used only on narrow (< 560px) shelf view so actions never clip
        self.shelf_narrow_bar = QWidget()
        self.shelf_narrow_layout = QHBoxLayout(self.shelf_narrow_bar)
        self.shelf_narrow_layout.setContentsMargins(0, 0, 0, 0)
        self.shelf_narrow_layout.setSpacing(6)
        self.shelf_narrow_bar.hide()
        root.addWidget(self.shelf_narrow_bar)
        self.hotkey_notice = self._label("", "status")
        self.hotkey_notice.setWordWrap(True)
        self.hotkey_notice.hide()
        root.addWidget(self.hotkey_notice)
        body = self.body_layout = QHBoxLayout()
        body.setSpacing(16)
        self.sidebar = QWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(232)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(10, 10, 10, 10)
        side.setSpacing(8)

        # Foliate-style compact book identity card at top of sidebar
        self.sidebar_book_card = QFrame(self.sidebar)
        self.sidebar_book_card.setObjectName("sidebarBookCard")
        card_layout = QHBoxLayout(self.sidebar_book_card)
        card_layout.setContentsMargins(2, 2, 2, 10)
        card_layout.setSpacing(10)
        self.sidebar_cover = QLabel(self.sidebar_book_card)
        self.sidebar_cover.setFixedSize(36, 48)
        self.sidebar_cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.sidebar_cover)
        card_text = QVBoxLayout()
        card_text.setContentsMargins(0, 0, 0, 0)
        card_text.setSpacing(2)
        self.sidebar_book_title = QLabel("一本书，一处安静", self.sidebar_book_card)
        self.sidebar_book_title.setObjectName("sidebarBookTitle")
        self.sidebar_book_title.setTextFormat(Qt.TextFormat.PlainText)
        self.sidebar_book_title.setWordWrap(False)
        self.sidebar_book_title.setMinimumWidth(0)
        self.sidebar_book_meta = QLabel("本地阅读 · 无需联网", self.sidebar_book_card)
        self.sidebar_book_meta.setObjectName("sidebarBookMeta")
        self.sidebar_book_meta.setTextFormat(Qt.TextFormat.PlainText)
        self.sidebar_book_meta.setMinimumWidth(0)
        card_text.addStretch(1)
        card_text.addWidget(self.sidebar_book_title)
        card_text.addWidget(self.sidebar_book_meta)
        card_text.addStretch(1)
        card_layout.addLayout(card_text, 1)
        self.sidebar_info_button = self._icon_button(
            "info", self._toggle_sidebar_info, "资料",
            size=14, checkable=True, fixed_size=(24, 24),
        )
        card_layout.addWidget(self.sidebar_info_button, 0, Qt.AlignmentFlag.AlignVCenter)
        side.addWidget(self.sidebar_book_card)

        self.reading_tabs = _SidebarTabSwitcher(self.sidebar)
        self._themed_controls.append(self.reading_tabs)
        toc_panel = QWidget()
        toc_layout = QVBoxLayout(toc_panel)
        toc_layout.setContentsMargins(2, 2, 2, 0)
        toc_layout.setSpacing(6)
        self.toc_filter = QLineEdit(toc_panel)
        self.toc_filter.setObjectName("shelfSearch")
        self.toc_filter.setMinimumWidth(0)
        self.toc_filter.setClearButtonEnabled(True)
        self.toc_filter.setPlaceholderText("筛选目录…")
        self.toc_filter.setAccessibleName("筛选目录")
        self.toc_filter.setProperty("translation_accessible", "筛选目录")
        self.toc_filter.textChanged.connect(self._filter_chapters)
        toc_layout.addWidget(self.toc_filter)
        self.chapters = QListWidget()
        self.chapters.setAccessibleName("书籍目录")
        self.chapters.itemActivated.connect(self._chapter_activated)
        self.chapters.itemClicked.connect(self._chapter_activated)
        toc_layout.addWidget(self.chapters, 1)
        self._tab_toc = self.reading_tabs.addTab(toc_panel, "目录", "toc")
        bookmark_panel = QWidget()
        bookmark_layout = QVBoxLayout(bookmark_panel)
        bookmark_layout.setContentsMargins(2, 4, 2, 4)
        bookmark_layout.setSpacing(6)
        self.bookmarks = QListWidget()
        self.bookmarks.setAccessibleName("书签与笔记")
        self.bookmarks.itemActivated.connect(self._bookmark_activated)
        self.bookmarks.itemClicked.connect(self._bookmark_activated)
        bookmark_layout.addWidget(self.bookmarks, 1)
        bookmark_actions = QHBoxLayout()
        bookmark_actions.setContentsMargins(0, 0, 0, 0)
        bookmark_actions.setSpacing(4)
        self.bookmark_add_button = self._button("添加", self.add_bookmark)
        self.bookmark_note_button = self._button("编辑", self.edit_bookmark_note)
        self.bookmark_remove_button = self._button("删除", self.remove_bookmark)
        for button, label in (
            (self.bookmark_add_button, "添加书签"),
            (self.bookmark_note_button, "编辑笔记"),
            (self.bookmark_remove_button, "删除书签"),
        ):
            button.setObjectName("bookmarkAction")
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setToolTip(label)
            button.setProperty("translation_tip", label)
            button.setAccessibleName(label)
            button.setProperty("translation_accessible", label)
            bookmark_actions.addWidget(button)
        bookmark_layout.addLayout(bookmark_actions)
        self._tab_bookmarks = self.reading_tabs.addTab(bookmark_panel, "书签", "bookmark")

        term_panel = QWidget()
        term_layout = QVBoxLayout(term_panel)
        term_layout.setContentsMargins(2, 2, 2, 4)
        term_layout.setSpacing(6)
        self.term_notes_filter = QLineEdit(term_panel)
        self.term_notes_filter.setObjectName("shelfSearch")
        self.term_notes_filter.setMinimumWidth(0)
        self.term_notes_filter.setClearButtonEnabled(True)
        self.term_notes_filter.setPlaceholderText("筛选词条…")
        self.term_notes_filter.setAccessibleName("筛选词条")
        self.term_notes_filter.setProperty("translation_accessible", "筛选词条")
        self.term_notes_filter.textChanged.connect(self._filter_term_notes)
        term_layout.addWidget(self.term_notes_filter)
        self.term_notes_list = QListWidget(term_panel)
        self.term_notes_list.setObjectName("termNotesList")
        self.term_notes_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.term_notes_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.term_notes_list.setAccessibleName("词条标注")
        self.term_notes_list.setProperty("translation_accessible", "词条标注")
        self.term_notes_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.term_notes_list.customContextMenuRequested.connect(self._show_term_note_list_menu)
        self.term_notes_list.itemDoubleClicked.connect(self._term_note_double_clicked)
        self.term_notes_list.currentRowChanged.connect(self._sync_term_note_buttons)
        term_layout.addWidget(self.term_notes_list, 1)
        term_actions = QHBoxLayout()
        term_actions.setContentsMargins(0, 0, 0, 0)
        term_actions.setSpacing(4)
        self.term_add_button = self._button("添加", self.prompt_add_term_note)
        self.term_edit_button = self._button("编辑", self.prompt_edit_selected_term_note)
        self.term_remove_button = self._button("删除", self.remove_selected_term_note)
        for button, label in (
            (self.term_add_button, "添加词条标注"),
            (self.term_edit_button, "编辑词条标注"),
            (self.term_remove_button, "删除词条标注"),
        ):
            button.setObjectName("bookmarkAction")
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setToolTip(label)
            button.setProperty("translation_tip", label)
            button.setAccessibleName(label)
            button.setProperty("translation_accessible", label)
            term_actions.addWidget(button)
        term_layout.addLayout(term_actions)
        self.term_import_button = self._button("从其他书继承词条…", self.prompt_import_term_notes)
        self.term_import_button.setObjectName("bookmarkAction")
        self.term_import_button.setMinimumWidth(0)
        self.term_import_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.term_import_button.setToolTip("从其他书继承词条")
        self.term_import_button.setProperty("translation_tip", "从其他书继承词条")
        self.term_import_button.setAccessibleName("从其他书继承词条")
        self.term_import_button.setProperty("translation_accessible", "从其他书继承词条")
        term_layout.addWidget(self.term_import_button)
        self._tab_terms = self.reading_tabs.addTab(term_panel, "词条", "term")

        self.book_info = BookInfoPanel()
        self._tab_info = self.reading_tabs.addTab(self.book_info, "资料", "info")
        self.pages = QListWidget()
        self.pages.setAccessibleName("书中页码")
        self.pages.itemActivated.connect(self._page_activated)
        self.pages.itemClicked.connect(self._page_activated)
        self._tab_pages = self.reading_tabs.addTab(self.pages, "页码", "pages")
        self.landmarks = QListWidget()
        self.landmarks.setAccessibleName("书中位置")
        self.landmarks.itemActivated.connect(self._page_activated)
        self.landmarks.itemClicked.connect(self._page_activated)
        self._tab_landmarks = self.reading_tabs.addTab(self.landmarks, "位置", "landmarks")

        self.search_panel = QWidget()
        search_panel_layout = QVBoxLayout(self.search_panel)
        search_panel_layout.setContentsMargins(2, 2, 2, 0)
        search_panel_layout.setSpacing(6)
        self.search_empty_label = QLabel("输入关键词搜索全书", self.search_panel)
        self.search_empty_label.setObjectName("searchEmptyHint")
        self.search_empty_label.setWordWrap(True)
        self.search_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        search_panel_layout.addWidget(self.search_empty_label, 1)
        self.search_results = QListView(self.search_panel)
        self.search_results.setObjectName("searchResults")
        self.search_results.setAccessibleName("全书搜索结果")
        self.search_results.setProperty("translation_accessible", "搜索结果")
        self.search_results.setUniformItemSizes(False)
        self.search_results.setLayoutMode(QListView.LayoutMode.SinglePass)
        self.search_results.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.search_results.setSpacing(4)
        self.search_results.setMouseTracking(True)
        self.search_result_model = _SearchResultModel(self, self.search_results)
        self.search_result_delegate = _SearchResultDelegate(self, self.search_results)
        self._themed_controls.append(self.search_result_delegate)
        self.search_results.setModel(self.search_result_model)
        self.search_results.setItemDelegate(self.search_result_delegate)
        self.search_results.clicked.connect(self._search_result_activated)
        self.search_results.activated.connect(self._search_result_activated)
        self.search_results.hide()
        search_panel_layout.addWidget(self.search_results, 1)
        self.search_tab_index = self.reading_tabs.addTab(self.search_panel, "搜索", "search")
        self.reading_tabs.currentChanged.connect(self._on_reading_tab_changed)
        self.reading_tabs.setTabVisible(self._tab_terms, True)
        self.reading_tabs.setTabVisible(self._tab_info, False)
        self.reading_tabs.setTabVisible(self._tab_pages, False)
        self.reading_tabs.setTabVisible(self._tab_landmarks, False)
        self.reading_tabs.setTabVisible(self.search_tab_index, False)
        side.addWidget(self.reading_tabs, 1)

        self.book_progress_label = self._label("全书进度：--", "status")
        self.book_progress_label.setAccessibleName("全书阅读进度")
        self.book_progress_label.hide()
        self.book_progress_bar = QProgressBar()
        self.book_progress_bar.setObjectName("bookProgress")
        self.book_progress_bar.setRange(0, 1000)
        self.book_progress_bar.setTextVisible(False)
        self.book_progress_bar.setFixedHeight(3)
        self.book_progress_bar.setAccessibleName("全书阅读进度")
        side.addWidget(self.book_progress_bar)
        self.sidebar_status = self._label("本地阅读 · 无需联网", "status")
        self.sidebar_status.hide()
        self._update_sidebar_toggle()
        body.addWidget(self.sidebar)

        self.main_pages = QStackedWidget()
        self.reader_page = QWidget()
        reading = self.reading_layout = QVBoxLayout(self.reader_page)
        reading.setContentsMargins(0, 0, 0, 0)
        reading.setSpacing(4)
        # Keep hidden title labels synced for breadcrumb, window title, and tests
        self.book_title = self._label("一本书，一处安静", "bookTitle")
        self.book_title.hide()
        self.chapter_title = self._label("从你的本地书籍开始", "chapterTitle")
        self.chapter_title.hide()

        self.search_bar = QFrame(self.search_panel)
        self.search_bar.setObjectName("sidebarSearchBar")
        self.search_bar.setFixedHeight(64)
        search_layout = QVBoxLayout(self.search_bar)
        search_layout.setContentsMargins(4, 4, 4, 4)
        search_layout.setSpacing(3)
        search_input_layout = QHBoxLayout()
        search_input_layout.setContentsMargins(0, 0, 0, 0)
        search_input_layout.setSpacing(4)
        self.search_icon_label = QLabel(self.search_bar)
        self.search_icon_label.setFixedSize(16, 16)
        self.search_icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        search_input_layout.addWidget(self.search_icon_label)
        self.search_input = _SearchLineEdit(self.search_bar)
        self.search_input.setObjectName("sidebarSearchInput")
        self.search_input.setMinimumWidth(40)
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setPlaceholderText("搜索全书内容…")
        self.search_input.setAccessibleName("书内搜索")
        self.search_input.setProperty("translation_accessible", "书内搜索")
        self.search_input.textChanged.connect(self._on_book_search_changed)
        self.search_input.next_requested.connect(self.find_next_match)
        self.search_input.previous_requested.connect(self.find_previous_match)
        self.search_input.close_requested.connect(self.close_book_search)
        search_input_layout.addWidget(self.search_input, 1)
        search_layout.addLayout(search_input_layout)

        search_controls_layout = QHBoxLayout()
        search_controls_layout.setContentsMargins(0, 0, 0, 0)
        search_controls_layout.setSpacing(2)
        self.search_count_label = QLabel("", self.search_bar)
        self.search_count_label.setObjectName("sidebarSearchCount")
        self.search_count_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.search_count_label.setMinimumWidth(0)
        search_controls_layout.addWidget(self.search_count_label)
        search_controls_layout.addStretch(1)
        self.search_prev_button = self._icon_button(
            "chevron_up", self.find_previous_match, "上一个匹配 (Shift+Enter)",
            size=13, fixed_size=(22, 22),
        )
        self.search_next_button = self._icon_button(
            "chevron_down", self.find_next_match, "下一个匹配 (Enter)",
            size=13, fixed_size=(22, 22),
        )
        self.search_prev_button.setEnabled(False)
        self.search_next_button.setEnabled(False)
        search_controls_layout.addWidget(self.search_prev_button)
        search_controls_layout.addWidget(self.search_next_button)
        search_layout.addLayout(search_controls_layout)
        self.search_bar.hide()
        search_panel_layout.insertWidget(0, self.search_bar)

        self.reader = PageView()
        self.reader.turned.connect(self.turn_page)
        self.reader.chapter_turned.connect(self.turn_chapter)
        self.reader.link_activated.connect(self.open_epub_link)
        self.reader.exit_focus.connect(lambda: self.toggle_focus() if self.focus_mode else None)
        self.reader.focus_requested.connect(self.toggle_focus)
        self.reader.resample_requested.connect(self.manual_resample_focus_background)
        self.reader.visibility_requested.connect(self.toggle_visible)
        self.reader.emergency_requested.connect(self.emergency_exit)
        self.reader.reflowed.connect(self._on_reader_reflowed)
        self.reader.term_hovered.connect(self._on_term_hovered)
        self.reader.term_hover_cleared.connect(self._on_term_hover_cleared)
        self.reader.term_note_requested.connect(self.open_term_note_dialog)
        self.reader.term_search_requested.connect(self.search_in_book)
        reading.addWidget(self.reader, 1)

        self.moyu_strip = MoyuStrip(self.reader_page)
        self.moyu_strip.turn_requested.connect(self.turn_moyu_sentence)
        self.moyu_strip.toggle_lines_requested.connect(self.toggle_moyu_lines)
        self.moyu_strip.exit_requested.connect(self.exit_moyu_mode)
        self.moyu_strip.context_menu_opened.connect(self._on_moyu_menu_opened)
        self.moyu_strip.context_menu_closed.connect(self._on_moyu_menu_closed)
        self.moyu_strip.term_hovered.connect(self._on_moyu_term_hovered)
        self.moyu_strip.term_hover_cleared.connect(self._on_moyu_term_hover_cleared)
        reading.addWidget(self.moyu_strip, 1)
        self.moyu_strip.hide()

        self.progress = ThinSlider(Qt.Orientation.Horizontal)
        self.progress.setRange(0, 1000)
        self.progress.setAccessibleName("本章阅读进度")
        self.progress.sliderReleased.connect(self._seek)
        self.progress.setEnabled(False)
        self._themed_controls.append(self.progress)
        reading.addWidget(self.progress)

        # Compact footer keeps chapter status and jump-back action on one line.
        self.reader_navigation = QWidget()
        self.reader_navigation.setObjectName("readerNavigation")
        self.reader_navigation_layout = QHBoxLayout(self.reader_navigation)
        navigation = self.reader_navigation_layout
        navigation.setContentsMargins(4, 0, 4, 2)
        navigation.setSpacing(4)
        self.page_label = self._label("PageUp / PageDown", "pageStatus")
        self.page_label.setMinimumWidth(0)
        self.page_label.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.page_label.setProperty("full_text", "PageUp / PageDown")
        self.page_label.setProperty("compact_text", "Pg")

        self.jump_back_button = QPushButton("↶", self.reader_navigation)
        self.jump_back_button.setObjectName("jumpBackButton")
        self.jump_back_button.setProperty("translation_source", "↶")
        self.jump_back_button.setToolTip("返回上一处阅读位置 (Alt+Left)")
        self.jump_back_button.setProperty("translation_tip", "返回上一处阅读位置 (Alt+Left)")
        self.jump_back_button.setAccessibleName("返回上一处阅读位置 (Alt+Left)")
        self.jump_back_button.setProperty("translation_accessible", "返回上一处阅读位置 (Alt+Left)")
        self.jump_back_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.jump_back_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.jump_back_button.setFixedSize(22, 20)
        self.jump_back_button.clicked.connect(self.jump_back)
        self.jump_back_button.hide()
        self.jump_back_label = _JumpTargetLabel(self.reader_navigation)
        self.jump_back_label.setObjectName("jumpTargetLabel")
        self.jump_back_label.setMaximumWidth(132)
        self.jump_back_label.hide()

        self.jump_forward_button = QPushButton("↷", self.reader_navigation)
        self.jump_forward_button.setObjectName("jumpForwardButton")
        self.jump_forward_button.setProperty("translation_source", "↷")
        self.jump_forward_button.setToolTip("跳回刚才的位置 (Alt+Right)")
        self.jump_forward_button.setProperty("translation_tip", "跳回刚才的位置 (Alt+Right)")
        self.jump_forward_button.setAccessibleName("跳回刚才的位置 (Alt+Right)")
        self.jump_forward_button.setProperty("translation_accessible", "跳回刚才的位置 (Alt+Right)")
        self.jump_forward_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.jump_forward_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.jump_forward_button.setFixedSize(22, 20)
        self.jump_forward_button.clicked.connect(self.jump_forward)
        self.jump_forward_button.hide()
        self.jump_forward_label = _JumpTargetLabel(self.reader_navigation)
        self.jump_forward_label.setObjectName("jumpTargetLabel")
        self.jump_forward_label.setMaximumWidth(132)
        self.jump_forward_label.hide()
        self.previous_chapter = self._icon_button("prev_chapter", lambda: self.turn_chapter(-1), "上一章", size=13, fixed_size=(24, 22))
        self.previous = self._icon_button("prev_page", lambda: self.turn_page(-1), "← 上一页", size=13, fixed_size=(24, 22))
        self.next = self._icon_button("next_page", lambda: self.turn_page(1), "下一页 →", size=13, fixed_size=(24, 22))
        self.next_chapter = self._icon_button("next_chapter", lambda: self.turn_chapter(1), "下一章", size=13, fixed_size=(24, 22))
        self.footer_book_progress = QLabel("", self.reader_navigation)
        self.footer_book_progress.setObjectName("pageStatus")
        self.footer_book_progress.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        navigation.addWidget(self.page_label)
        navigation.addWidget(self.jump_back_button)
        navigation.addWidget(self.jump_back_label)
        navigation.addWidget(self.jump_forward_button)
        navigation.addWidget(self.jump_forward_label)
        navigation.addStretch(1)
        navigation.addWidget(self.previous_chapter)
        navigation.addWidget(self.previous)
        navigation.addWidget(self.next)
        navigation.addWidget(self.next_chapter)
        navigation.addStretch(1)
        navigation.addWidget(self.footer_book_progress)
        reading.addWidget(self.reader_navigation)
        self.main_pages.addWidget(self.reader_page)
        self.shelf = ShelfPanel()
        self._themed_controls.append(self.shelf)
        self.open_button = self.shelf.add_button
        self.shelf_actions_layout.addWidget(self.shelf.add_button)
        self.shelf.add_requested.connect(self.choose_file)
        self.shelf.open_requested.connect(self.open_shelf_book)
        self.shelf.rename_requested.connect(self.prompt_rename_shelf_book)
        self.shelf.move_books_requested.connect(self.move_shelf_books)
        self.shelf.remove_requested.connect(self._confirm_remove_shelf_book)
        self.shelf.create_category_requested.connect(self.prompt_create_shelf_category)
        self.shelf.delete_category_requested.connect(self._confirm_delete_shelf_category)
        self.shelf.rename_category_requested.connect(self.prompt_rename_shelf_category)
        self.main_pages.addWidget(self.shelf)
        self.settings_page = SettingsPage(self.store, self)
        self.settings_page.settings_changed.connect(self.apply_settings)
        self.settings_page.capture_started.connect(lambda: self._configure_hotkeys(capture_active=True))
        self.settings_page.capture_finished.connect(lambda: self._configure_hotkeys(capture_active=False))
        self.settings_page.notice_requested.connect(self.set_status)
        self.settings_page.close_requested.connect(self.close_settings)
        self.settings_page.check_update_requested.connect(self.check_for_updates)
        self.main_pages.addWidget(self.settings_page)
        self.recent = self.shelf.recent
        body.addWidget(self.main_pages, 1)
        self._sync_page_navigation()
        root.addLayout(body, 1)
        bottom_widget = QWidget()
        bottom = QHBoxLayout(bottom_widget)
        bottom.setContentsMargins(0, 0, 0, 0)
        self.status = self._label("离线优先。添加书籍即可开始阅读。", "status")
        self.status.setWordWrap(True)
        bottom.addWidget(self.status, 1)
        self.grip = QSizeGrip(self)
        bottom.addWidget(self.grip, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)
        root.addWidget(bottom_widget)
        self.encoding_bar = QWidget()
        encoding_layout = QHBoxLayout(self.encoding_bar)
        encoding_layout.setContentsMargins(0, 0, 0, 0)
        encoding_layout.addWidget(self._label("TXT 编码"))
        self.encoding = QComboBox()
        for value in ENCODINGS:
            self.encoding.addItem("自动识别" if value == "auto" else value.upper(), value)
        encoding_layout.addWidget(self.encoding)
        encoding_layout.addWidget(self._button("按此编码重试", self.retry_encoding))
        encoding_layout.addStretch()
        self.encoding_bar.hide()
        root.addWidget(self.encoding_bar)
        self.chrome_widgets = [
            header, self.shelf_narrow_bar, self.hotkey_notice, self.sidebar,
            self.progress,
            self.reader_navigation, bottom_widget, self.encoding_bar,
        ]
        self.open_shortcut = QShortcut(QKeySequence("Ctrl+O"), self)
        self.open_shortcut.activated.connect(self.choose_file)
        self.settings_shortcut = QShortcut(QKeySequence("Ctrl+,"), self)
        self.settings_shortcut.activated.connect(self.show_settings)
        self.search_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        self.search_shortcut.activated.connect(self.open_book_search)
        self.jump_back_shortcut = QShortcut(QKeySequence("Alt+Left"), self)
        self.jump_back_shortcut.setAutoRepeat(False)
        self.jump_back_shortcut.activated.connect(self.jump_back)
        self.jump_forward_shortcut = QShortcut(QKeySequence("Alt+Right"), self)
        self.jump_forward_shortcut.setAutoRepeat(False)
        self.jump_forward_shortcut.activated.connect(self.jump_forward)
        self.focus_shortcut = QShortcut(QKeySequence(self.settings.focus_key), self)
        self.focus_shortcut.activated.connect(self.toggle_focus)
        self.focus_shortcut.setAutoRepeat(False)
        self.focus_opacity_shortcut = QShortcut(QKeySequence(self.settings.focus_opacity_key), self)
        self.focus_opacity_shortcut.activated.connect(self.toggle_focus_opacity)
        self.focus_opacity_shortcut.setAutoRepeat(False)
        self.focus_resample_shortcut = QShortcut(QKeySequence(self.settings.focus_resample_key), self)
        self.focus_resample_shortcut.activated.connect(self.manual_resample_focus_background)
        self.focus_resample_shortcut.setAutoRepeat(False)
        self.escape_shortcut = QShortcut(QKeySequence("Esc"), self)
        self.escape_shortcut.activated.connect(self._handle_escape)
        self.escape_shortcut.setEnabled(False)
        self.visibility_shortcut = QShortcut(QKeySequence(self.settings.hotkey), self)
        self.visibility_shortcut.setAutoRepeat(False)
        self.visibility_shortcut.activated.connect(self.toggle_visible)
        self.emergency_shortcut = QShortcut(QKeySequence(self.settings.emergency_key), self)
        self.emergency_shortcut.setAutoRepeat(False)
        self.emergency_shortcut.activated.connect(self.emergency_exit)
        self.moyu_shortcut = QShortcut(QKeySequence(self.settings.moyu_key), self)
        self.moyu_shortcut.setAutoRepeat(False)
        self.moyu_shortcut.activated.connect(self.toggle_moyu_mode)
        self.moyu_lines_shortcut = QShortcut(QKeySequence(self.settings.moyu_lines_key), self)
        self.moyu_lines_shortcut.setAutoRepeat(False)
        self.moyu_lines_shortcut.activated.connect(self.toggle_moyu_lines)
        self.toast = ToastNotice(self.surface)
        self.term_hover_card = TermHoverCard(self.surface)
        self.moyu_term_hover_card = TermHoverCard(None, floating=True)
        self.appearance_popover = AppearancePopover(self.surface)
        self.appearance_popover.settings_changed.connect(self.apply_settings)
        self.appearance_popover.more_settings_requested.connect(lambda: self.show_settings(section="reading"))
        self.appearance_popover.dismissed.connect(lambda: self.layout_button.setChecked(False))
        self.focus_popover = FocusPopover(self.surface)
        self.focus_popover.settings_changed.connect(self.apply_settings)
        self.focus_popover.enter_focus_requested.connect(self.toggle_focus)
        self.focus_popover.enter_moyu_requested.connect(self.toggle_moyu_mode)
        self.focus_popover.resample_requested.connect(self.manual_resample_focus_background)
        self.focus_popover.eyedropper_requested.connect(self._focus_picker.begin)
        self.focus_popover.more_settings_requested.connect(lambda: self.show_settings(section="focus"))
        self.focus_popover.dismissed.connect(lambda: self.focus_button.setChecked(self.focus_mode))
        self.main_pages.currentChanged.connect(self._main_page_changed)
        self._update_sidebar_book_card()
        self._update_progress()
        self._refresh_term_notes()

    def _setup_system(self):
        app = QApplication.instance()
        self.hotkey = GlobalHotkey(app, self.toggle_visible)
        self.emergency_hotkey = GlobalHotkey(app, self.emergency_exit, identifier=0x5153)
        self.moyu_hotkeys = [
            GlobalHotkey(app, self.toggle_moyu_mode, identifier=0x5154),
            GlobalHotkey(app, lambda: self.turn_moyu_sentence(-1), identifier=0x5155),
            GlobalHotkey(app, lambda: self.turn_moyu_sentence(1), identifier=0x5156),
            GlobalHotkey(app, self.toggle_moyu_lines, identifier=0x5157),
        ]
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(self.windowIcon(), self)
            self.tray.setToolTip("QuietReader · 静读")
            menu = QMenu(self)
            for label, callback in [("显示 / 隐藏", self.toggle_visible), ("添加书籍…", self.choose_file), ("配置…", self.show_settings)]:
                action = QAction(label, self)
                action.setProperty("translation_source", label)
                action.triggered.connect(callback)
                menu.addAction(action)
            menu.addSeparator()
            self.tray_focus_action = QAction("进入专注模式", self)
            self.tray_focus_action.setCheckable(True)
            self.tray_focus_action.triggered.connect(self.toggle_focus)
            self.tray_focus_action.setEnabled(False)
            menu.addAction(self.tray_focus_action)
            self.tray_moyu_action = QAction("进入摸鱼模式", self)
            self.tray_moyu_action.setCheckable(True)
            self.tray_moyu_action.triggered.connect(self.toggle_moyu_mode)
            self.tray_moyu_action.setEnabled(False)
            menu.addAction(self.tray_moyu_action)
            action = QAction("退出 QuietReader", self)
            action.setProperty("translation_source", "退出 QuietReader")
            action.triggered.connect(self.quit_app)
            menu.addAction(action)
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(lambda reason: self.toggle_visible() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
            self.tray.show()

    def choose_file(self):
        if self.loading:
            return
        self.reveal()
        start_directory = default_book_directory(
            self.store.state["books"],
            self.shelf_manager,
            self._last_browse_dir,
        )
        dialog = BookFileDialog(start_directory, self.settings.language, self)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        files = dialog.selected_files() if accepted else []
        remembered = Path(files[0]).parent if files else dialog.last_directory()
        if remembered is not None:
            self._last_browse_dir = str(remembered)
            self.store.state["last_browse_dir"] = self._last_browse_dir
            self.schedule_save()
        if files:
            self.add_book_paths(files)

    @staticmethod
    def _embedded_book_title(path):
        if Path(path).suffix.lower() != ".epub":
            return None
        try:
            return read_epub_title(path)
        except BookImportError:
            return None

    def _import_book_title(self, path):
        return self._embedded_book_title(path) or Path(path).stem

    def add_book_paths(self, paths, open_after=False, skipped_count=0):
        if self.loading or self.store.read_only:
            if self.store.read_only:
                self.set_status("保存数据来自不支持的版本。已保护原文件，本次不会保存更改。")
            return []
        added = []
        new_count = 0
        try:
            for source in paths:
                try:
                    source_path = Path(source).expanduser().resolve(strict=True)
                except (OSError, RuntimeError):
                    raise ShelfError("书籍文件不存在或无法访问。") from None
                source_key = str(source_path).casefold()
                existing = next((
                    record for record in self.store.state["books"]
                    if str(record.get("source_path", "")).casefold() == source_key
                ), None)
                if existing and self.shelf_manager.is_managed(existing["path"]):
                    embedded_title = self._embedded_book_title(existing["path"])
                    if embedded_title:
                        existing["title"] = embedded_title
                    added.append(existing)
                    continue
                if existing:
                    managed_path = self.shelf_manager.add_book(source_path)
                    self._remap_shelf_book(existing, managed_path)
                    existing["source_path"] = str(source_path)
                    existing["title"] = self._import_book_title(managed_path)
                    added.append(existing)
                    new_count += 1
                    continue
                existing = next((
                    record for record in self.store.state["books"]
                    if str(Path(record["path"]).expanduser().resolve()).casefold() == source_key
                ), None)
                if existing and self.shelf_manager.is_managed(existing["path"]):
                    embedded_title = self._embedded_book_title(existing["path"])
                    if embedded_title:
                        existing["title"] = embedded_title
                    added.append(existing)
                    continue
                managed_path = self.shelf_manager.add_book(source_path)
                if existing:
                    self._remap_shelf_book(existing, managed_path)
                    existing["source_path"] = str(source_path)
                    existing["title"] = self._import_book_title(managed_path)
                    added.append(existing)
                    new_count += 1
                    continue
                book_id = book_id_for_path(managed_path)
                record = {
                    "id": book_id,
                    "path": str(managed_path),
                    "title": self._import_book_title(managed_path),
                    "author": "",
                    "encoding": {".epub": "EPUB", ".pdf": "PDF"}.get(managed_path.suffix.lower(), "auto"),
                    "last_read": "",
                    "source_path": str(source_path),
                }
                current = next((item for item in self.store.state["books"] if item["id"] == book_id), None)
                if current:
                    current.update(record)
                    record = current
                else:
                    self.store.state["books"].insert(0, record)
                    self.store.state["books"] = self.store.state["books"][:MAX_BOOK_RECORDS]
                new_count += 1
                added.append(record)
        except (OSError, ShelfError) as error:
            self._refresh_shelf()
            self.save_state()
            self._set_shelf_error(error)
            return added
        self._refresh_shelf()
        self.save_state()
        if new_count and skipped_count:
            self.set_status("已添加 {count} 本书到书架，已跳过 {skipped} 个不支持的项目。", count=new_count, skipped=skipped_count)
        elif new_count:
            self.set_status("已添加 {count} 本书到书架。", count=new_count)
        elif added and skipped_count:
            self.set_status("所选书籍已在书架中，已跳过 {skipped} 个不支持的项目。", skipped=skipped_count)
        elif added:
            self.set_status("所选书籍已在书架中。")
        if open_after and added:
            record = added[0]
            encoding = record.get("encoding", "auto")
            self.load_path(record["path"], encoding if encoding in ENCODINGS else "auto")
        return added

    def open_shelf_book(self, book_id):
        record = next((item for item in self.store.state["books"] if item["id"] == book_id), None)
        if record:
            self.show_reading_page()
            encoding = record.get("encoding", "auto")
            self.load_path(record["path"], encoding if encoding in ENCODINGS else "auto")

    def prompt_rename_shelf_book(self, book_id):
        if self.store.read_only or self.loading:
            if self.store.read_only:
                self.set_status("保存数据来自不支持的版本。已保护原文件，本次不会保存更改。")
            return
        record = next((item for item in self.store.state["books"] if item["id"] == book_id), None)
        if record is None:
            return
        name, accepted = QInputDialog.getText(
            self,
            self.t("修改显示书名"),
            self.t("输入新的书名；留空可恢复软件导入时识别的原书名。"),
            QLineEdit.EchoMode.Normal,
            display_title_for_record(record),
        )
        if accepted:
            self.rename_shelf_book(book_id, name)

    def rename_shelf_book(self, book_id, name):
        if self.store.read_only or self.loading:
            if self.store.read_only:
                self.set_status("保存数据来自不支持的版本。已保护原文件，本次不会保存更改。")
            return False
        record = next((item for item in self.store.state["books"] if item["id"] == book_id), None)
        if record is None:
            return False

        previous_override = record.get("title_override")
        original_title = record.get("title") or Path(record["path"]).stem
        name = " ".join(name.split()) if isinstance(name, str) else ""
        if len(name) > MAX_BOOK_TITLE_OVERRIDE:
            self.set_status("书名不能超过 200 个字符。")
            return False
        if not name or name == original_title:
            record.pop("title_override", None)
            restored = True
        else:
            # This is a state.json display override; the managed book file stays untouched.
            record["title_override"] = name
            restored = False
        display_title = display_title_for_record(record)
        if record.get("title_override") == previous_override:
            return False

        self._refresh_shelf()
        if self.book and self.book.id == book_id:
            self.setWindowTitle(f"{display_title} · QuietReader")
            self.book_title.setText(display_title)
            self.book_info.set_book(
                self.book, self.settings.language, display_title=display_title,
            )
            self._update_header_crumb()
        if restored:
            self.set_status("已恢复原书名：{title}", title=display_title)
        else:
            self.set_status("书名显示已更新：{title}", title=display_title)
        self.save_state()
        return True

    def create_shelf_category(self, name):
        if self.store.read_only:
            self.set_status("保存数据来自不支持的版本。已保护原文件，本次不会保存更改。")
            return None
        if self.loading:
            return None
        try:
            path = self.shelf_manager.create_category(name)
        except ShelfError as error:
            self._set_shelf_error(error)
            return None
        self._refresh_shelf()
        self.save_state()
        self.set_status("已创建分类“{name}”。", name=path.name)
        return path

    def prompt_create_shelf_category(self):
        name, accepted = QInputDialog.getText(self, self.t("新建分类"), self.t("分类名称"))
        if accepted:
            self.create_shelf_category(name)

    def prompt_rename_shelf_category(self, category):
        if self.loading:
            return
        if self.store.read_only:
            self.set_status("保存数据来自不支持的版本。已保护原文件，本次不会保存更改。")
            return
        name, accepted = QInputDialog.getText(
            self, self.t("重命名分类"), self.t("新分类名称"), text=category,
        )
        if accepted:
            self.rename_shelf_category(category, name)

    def rename_shelf_category(self, category, name):
        if self.loading or self.store.read_only:
            return False
        try:
            source, target, paths = self.shelf_manager.rename_category(category, name)
        except (OSError, ShelfError) as error:
            self._set_shelf_error(error)
            return False
        if source.name == target.name:
            return True

        previous_state = deepcopy(self.store.state)
        open_book = (self.book.id, self.book.path) if self.book else None
        for record in self.store.state["books"]:
            try:
                old_path = Path(record["path"]).expanduser().resolve()
            except (OSError, RuntimeError):
                continue
            if old_path in paths:
                self._remap_shelf_book(record, paths[old_path])
        if not self.save_state():
            try:
                self.shelf_manager.rename_category(target.name, source.name)
            except (OSError, ShelfError) as error:
                self.set_status("分类改名后保存失败，无法恢复原分类：{error}", error=str(error))
            else:
                self.store.state = previous_state
                if self.book and open_book:
                    self.book.id, self.book.path = open_book
            self._refresh_shelf()
            return False

        self._refresh_shelf()
        self.shelf.select_category(target.name)
        self.set_status("分类“{old}”已更名为“{name}”。", old=source.name, name=target.name)
        return True

    def _remap_shelf_book(self, record, new_path):
        old_id = record["id"]
        new_id = book_id_for_path(new_path)
        record["id"] = new_id
        record["path"] = str(new_path)
        positions = self.store.state.setdefault("positions", {})
        bookmarks = self.store.state.setdefault("bookmarks", {})
        term_notes = self.store.state.setdefault("term_notes", {})
        for mapping in (positions, bookmarks, term_notes):
            if old_id in mapping:
                previous = mapping.pop(old_id)
                mapping.setdefault(new_id, previous)
        jump_history = self.store.state.setdefault("jump_history", {})
        if old_id in jump_history:
            previous = jump_history.pop(old_id)
            jump_history.setdefault(new_id, previous)
        forward_history = self.store.state.setdefault("forward_history", {})
        if old_id in forward_history:
            previous = forward_history.pop(old_id)
            forward_history.setdefault(new_id, previous)
        if self.store.state.get("last_book") == old_id:
            self.store.state["last_book"] = new_id
        if self.book and self.book.id == old_id:
            self.book.id = new_id
            self.book.path = str(new_path)
        return new_id

    def move_shelf_books(self, book_ids, category):
        if self.store.read_only or self.loading:
            if self.store.read_only:
                self.set_status("保存数据来自不支持的版本。已保护原文件，本次不会保存更改。")
            return
        if isinstance(book_ids, str):
            book_ids = [book_ids]
        selected_ids = list(dict.fromkeys(book_ids))
        records_by_id = {item["id"]: item for item in self.store.state["books"]}
        records = [records_by_id[book_id] for book_id in selected_ids if book_id in records_by_id]
        if not records:
            return
        try:
            self.save_state()
        except (OSError, ShelfError) as error:
            self._set_shelf_error(error)
            return

        moved_records = []
        failures = []
        for record in records:
            old_path = record["path"]
            try:
                was_managed = self.shelf_manager.is_managed(old_path)
                if was_managed and self.shelf_manager.category_for_path(old_path) == category:
                    continue
                new_path = self.shelf_manager.move_book(old_path, category)
            except (OSError, ShelfError) as error:
                failures.append(error)
                continue
            self._remap_shelf_book(record, new_path)
            if not was_managed:
                record.setdefault("source_path", old_path)
            moved_records.append(record)

        if moved_records:
            self._refresh_shelf()
            try:
                self.save_state()
            except (OSError, ShelfError) as error:
                self._set_shelf_error(error)
                return

        if failures:
            if moved_records:
                self.set_status(
                    "已将 {moved} 本书移动到“{category}”；另有 {failed} 本失败：{error}",
                    moved=len(moved_records), failed=len(failures), category=category,
                    error=str(failures[0]),
                    toast_action_text="查看分类",
                    toast_action=lambda target=category: self.show_shelf_category(target),
                )
            elif len(records) == 1:
                self._set_shelf_error(failures[0])
            else:
                self.set_status("移动 {count} 本书失败：{error}", count=len(failures), error=str(failures[0]))
            return
        if not moved_records:
            self.set_status("所选书籍已在此分类中。")
            return
        if len(records) == 1:
            record = moved_records[0]
            title = display_title_for_record(record)
            self.set_status(
                "已将《{title}》移动到“{category}”。", title=title, category=category,
                toast_action_text="查看分类",
                toast_action=lambda target=category: self.show_shelf_category(target),
            )
        else:
            self.set_status(
                "已将 {count} 本书移动到“{category}”。", count=len(moved_records), category=category,
                toast_action_text="查看分类",
                toast_action=lambda target=category: self.show_shelf_category(target),
            )

    def remove_shelf_book(self, book_id):
        if self.store.read_only or self.loading:
            if self.store.read_only:
                self.set_status("保存数据来自不支持的版本。已保护原文件，本次不会保存更改。")
            return False
        record = next((item for item in self.store.state["books"] if item["id"] == book_id), None)
        if record is None:
            return False
        if self.book and self.book.id == book_id:
            self.save_state()
        try:
            self.shelf_manager.remove_book(record["path"])
        except (OSError, ShelfError) as error:
            self._set_shelf_error(error)
            return False
        self.store.state["books"] = [item for item in self.store.state["books"] if item["id"] != book_id]
        self.store.state.get("positions", {}).pop(book_id, None)
        self.store.state.get("bookmarks", {}).pop(book_id, None)
        self.store.state.get("jump_history", {}).pop(book_id, None)
        self.store.state.get("forward_history", {}).pop(book_id, None)
        self.store.state.get("term_notes", {}).pop(book_id, None)
        if self.store.state.get("last_book") == book_id:
            self.store.state["last_book"] = ""
        if self.book and self.book.id == book_id:
            self._clear_current_book()
        self._refresh_shelf()
        self.save_state()
        self.set_status("已从书架移除《{title}》。", title=display_title_for_record(record))
        return True

    def _confirm_remove_shelf_book(self, book_id):
        record = next((item for item in self.store.state["books"] if item["id"] == book_id), None)
        if not record:
            return
        answer = QMessageBox.question(
            self, self.t("从书架移除"),
            self.t("确定从书架移除《{title}》吗？书架中的副本会被删除。", title=display_title_for_record(record)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.remove_shelf_book(book_id)

    def _confirm_delete_shelf_category(self, category):
        dialog = QMessageBox(self)
        dialog.setWindowTitle(self.t("删除分类"))
        dialog.setText(self.t("删除分类“{name}”时，如何处理其中的书籍？", name=category))
        delete_books = dialog.addButton(self.t("删除分类及书籍"), QMessageBox.ButtonRole.DestructiveRole)
        keep_books = dialog.addButton(self.t("仅删除分类，书籍移回未分类"), QMessageBox.ButtonRole.AcceptRole)
        cancel = dialog.addButton(self.t("取消"), QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(cancel)
        dialog.setEscapeButton(cancel)
        dialog.exec()
        if dialog.clickedButton() is delete_books:
            self.delete_shelf_category(category, delete_books=True)
        elif dialog.clickedButton() is keep_books:
            self.delete_shelf_category(category, delete_books=False)

    def delete_shelf_category(self, category, delete_books=False):
        if self.store.read_only or self.loading:
            if self.store.read_only:
                self.set_status("保存数据来自不支持的版本。已保护原文件，本次不会保存更改。")
            return False
        self.save_state()
        try:
            mapping = self.shelf_manager.delete_category(category, delete_books)
        except (OSError, ShelfError) as error:
            self._set_shelf_error(error)
            return False
        for record in list(self.store.state["books"]):
            old_path = Path(record["path"]).expanduser()
            try:
                resolved = old_path.resolve()
            except OSError:
                resolved = old_path
            if resolved not in mapping:
                continue
            new_path = mapping[resolved]
            if new_path is None:
                book_id = record["id"]
                self.store.state["books"].remove(record)
                self.store.state.get("positions", {}).pop(book_id, None)
                self.store.state.get("bookmarks", {}).pop(book_id, None)
                self.store.state.get("jump_history", {}).pop(book_id, None)
                self.store.state.get("forward_history", {}).pop(book_id, None)
                self.store.state.get("term_notes", {}).pop(book_id, None)
                if self.store.state.get("last_book") == book_id:
                    self.store.state["last_book"] = ""
                if self.book and self.book.id == book_id:
                    self._clear_current_book()
            else:
                self._remap_shelf_book(record, new_path)
        self._refresh_shelf()
        self.save_state()
        self.set_status("已删除分类“{name}”。", name=category)
        return True

    def _clear_current_book(self):
        if self.focus_mode:
            self.toggle_focus()
        self._hide_term_hover()
        self.close_book_search(focus_reader=False, clear_query=True)
        self._jump_history.clear()
        self._forward_history.clear()
        self.reader.clear_section_cache()
        if hasattr(self, "toc_filter"):
            self.toc_filter.blockSignals(True)
            self.toc_filter.clear()
            self.toc_filter.blockSignals(False)
        if hasattr(self, "term_notes_filter"):
            self.term_notes_filter.blockSignals(True)
            self.term_notes_filter.clear()
            self.term_notes_filter.blockSignals(False)
        self.book = None
        self.section_index = 0
        self.reader.set_term_notes([])
        self.reader.set_text("")
        self.book_title.setText(self.t("一本书，一处安静"))
        self.book_title.setProperty("translation_source", "一本书，一处安静")
        self.chapter_title.setText(self.t("从你的本地书籍开始"))
        self.chapter_title.setProperty("translation_source", "从你的本地书籍开始")
        self.setWindowTitle("QuietReader · 静读")
        self.chapters.clear()
        self.pages.clear()
        self.landmarks.clear()
        self.bookmarks.clear()
        self.term_notes_list.clear()
        self.reading_tabs.setTabVisible(self.search_tab_index, False)
        self.book_info.set_book(None, self.settings.language)
        self.progress.setValue(0)
        self.page_label.setText("PageUp / PageDown")
        self.page_label.setProperty("translation_source", None)
        self.page_label.setProperty("full_text", "PageUp / PageDown")
        self.page_label.setProperty("compact_text", "Pg")
        self.page_label.setToolTip("PageUp / PageDown")
        self.reading_tabs.setTabVisible(self._tab_pages, False)
        self.reading_tabs.setTabVisible(self._tab_landmarks, False)
        self.encoding_button.hide()
        self.encoding_bar.hide()
        self._update_jump_navigation()
        self._update_header_crumb()
        self._update_progress()
        self._refresh_bookmarks()
        self._refresh_term_notes()

    def _refresh_shelf(self):
        if not hasattr(self, "shelf"):
            return
        positions = self.store.state.get("positions", {})
        records = [
            {**record, "_has_saved_position": record.get("id") in positions}
            for record in self.store.state["books"]
        ]
        self.shelf.refresh(
            records, self.shelf_manager.categories(),
            self.shelf_manager.category_for_path, self.settings.language,
        )

    def _recent_activated(self, item):
        book_id = item.data(Qt.ItemDataRole.UserRole)
        if book_id:
            self.open_shelf_book(book_id)

    def load_path(self, path, encoding="auto"):
        if self.loading or self.closed:
            return
        self.save_state()
        self.pending_path = Path(path)
        self.loading = True
        self.open_button.setEnabled(False)
        self.set_status("正在后台导入书籍…", notify=False)
        self.worker = ImportTask(path, encoding)
        self.worker.signals.loaded.connect(self._loaded)
        self.worker.signals.failed.connect(self._failed)
        self.worker.signals.finished.connect(self._finished)
        self.pool.start(self.worker)

    def _loaded(self, book):
        if self.closed:
            return
        prev_book_id = self.book.id if self.book else None
        self.reader.clear_section_cache()
        self._jump_history.clear()
        self._forward_history.clear()
        if prev_book_id is not None and prev_book_id != book.id:
            if hasattr(self, "toc_filter"):
                self.toc_filter.blockSignals(True)
                self.toc_filter.clear()
                self.toc_filter.blockSignals(False)
            if hasattr(self, "term_notes_filter"):
                self.term_notes_filter.blockSignals(True)
                self.term_notes_filter.clear()
                self.term_notes_filter.blockSignals(False)
            self.close_book_search(focus_reader=False, clear_query=True)
        else:
            self._invalidate_book_search_requests()
        self._hide_term_hover()
        self.book = book
        self._restore_jump_history(book)
        record = next((item for item in self.store.state["books"] if item["id"] == book.id), None)
        title_override = record.get("title_override") if record else None
        display_title = title_override if isinstance(title_override, str) and title_override.strip() else book.title
        self.show_reading_page()
        self.setWindowTitle(f"{display_title} · QuietReader")
        self.book_title.setText(display_title)
        self.book_title.setProperty("translation_source", None)
        self.book_info.set_book(book, self.settings.language, display_title=display_title)
        self.reading_tabs.setTabVisible(self._tab_info, False)
        self.reading_tabs.setTabVisible(self.search_tab_index, True)
        self._refresh_bookmarks()
        self._refresh_term_notes()
        self.chapters.clear()
        self.pages.clear()
        self.landmarks.clear()
        if book.toc:
            for entry in book.toc:
                self._add_navigation_items(self.chapters, entry)
        if not self.chapters.count():
            for index, section in enumerate(book.sections):
                item = QListWidgetItem(section.title)
                item.setData(Qt.ItemDataRole.UserRole, (index, 0))
                self.chapters.addItem(item)
        for entry in book.page_list:
            target = self._resolve_navigation_target(entry.path, entry.fragment, entry.source_offset)
            if target is not None:
                item = QListWidgetItem(entry.label)
                item.setData(Qt.ItemDataRole.UserRole, target)
                self.pages.addItem(item)
        for entry in book.landmarks:
            target = self._resolve_navigation_target(entry.path, entry.fragment, entry.source_offset)
            if target is not None:
                item = QListWidgetItem(entry.label)
                item.setData(Qt.ItemDataRole.UserRole, target)
                self.landmarks.addItem(item)
        self.reading_tabs.setTabVisible(self._tab_pages, self.pages.count() > 0)
        self.reading_tabs.setTabVisible(self._tab_landmarks, self.landmarks.count() > 0)
        self.reading_tabs.setCurrentIndex(self._tab_toc)
        raw = self.store.state["positions"].get(book.id, {})
        index, offset = restore_position(book.sections, Position(raw.get("section_id", ""), raw.get("offset", 0)))
        self._update_jump_navigation()
        self.open_section(index, offset)
        self._filter_chapters()
        if self._search_active:
            self._search_origin = (self.section_index, self.reader.offset)
            self._search_origin_pushed = False
            self._run_book_search(navigate=False)
        if record is None:
            record = {"id": book.id, "path": book.path, "title": book.title, "author": book.author, "encoding": book.encoding, "last_read": ""}
        record.update({"id": book.id, "path": book.path, "title": book.title, "author": book.author, "encoding": book.encoding, "last_read": datetime.now(timezone.utc).isoformat()})
        self.store.state["books"] = [record] + [item for item in self.store.state["books"] if item["id"] != book.id][:MAX_BOOK_RECORDS - 1]
        self.store.state["last_book"] = book.id
        self._update_progress()
        self._refresh_shelf()
        self.set_status("{encoding} · {count} 个章节 / 分段 · 进度自动保存{author}", notify=False, encoding=book.encoding, count=len(book.sections), author=f" · {book.author}" if book.author else "")
        self.encoding_bar.hide()
        self.encoding_button.setVisible(book.encoding not in ("EPUB", "PDF") and self.main_pages.currentWidget() is self.reader_page)
        self.encoding.setCurrentIndex(max(0, self.encoding.findData(book.encoding)))
        self._update_header_crumb()
        self.save_state()
        self.reader.setFocus()

    def _add_navigation_items(self, widget, entry, depth=0):
        target = self._resolve_navigation_target(entry.path, entry.fragment, entry.source_offset)
        if target is None and not entry.children:
            return
        item = QListWidgetItem(("　" * depth) + entry.label)
        item.setData(Qt.ItemDataRole.UserRole, target)
        widget.addItem(item)
        for child in entry.children:
            self._add_navigation_items(widget, child, depth + 1)

    def _resolve_navigation_target(self, path, fragment="", source_offset=-1):
        if not self.book or not path:
            return None
        candidates = [(index, section) for index, section in enumerate(self.book.sections) if section.source_path == path]
        if fragment:
            for index, section in candidates:
                if fragment in section.anchors:
                    return index, section.anchors[fragment]
            return None
        if source_offset >= 0:
            for position, (index, section) in enumerate(candidates):
                if source_offset < len(section.text) or position == len(candidates) - 1:
                    return index, min(source_offset, len(section.text))
            return None
        return (candidates[0][0], 0) if candidates else None

    def _activate_navigation_item(self, item):
        if item is None:
            return
        target = item.data(Qt.ItemDataRole.UserRole)
        if target is not None:
            self._navigate_with_jump_history(*target)
            self.reader.setFocus()

    def _page_activated(self, item):
        self._activate_navigation_item(item)

    def open_epub_link(self, link):
        target = self._resolve_navigation_target(link.path, link.fragment)
        if target is None:
            self.set_status("书内链接目标不可用。")
            return False
        self._navigate_with_jump_history(*target)
        self.reader.setFocus()
        return True

    def _failed(self, message, encoding_choice):
        if self.focus_mode:
            self.toggle_focus()
        self.set_status(message)
        self.encoding_bar.setVisible(encoding_choice)

    def _finished(self):
        self.loading = False
        self.open_button.setEnabled(True)
        self.worker = None

    def retry_encoding(self):
        if self.pending_path:
            self.load_path(self.pending_path, self.encoding.currentData())

    def restore_last(self):
        last = self.store.state.get("last_book")
        record = next((b for b in self.store.state["books"] if b["id"] == last), None)
        if record:
            encoding = record.get("encoding", "auto")
            self.load_path(record["path"], encoding if encoding in ENCODINGS else "auto")

    def open_section(self, index, offset=0):
        if not self.book or not 0 <= index < len(self.book.sections):
            return
        self.section_index = index
        section = self.book.sections[index]
        self.chapter_title.setText(section.title)
        self.chapter_title.setProperty("translation_source", None)
        self._update_header_crumb()
        self._select_toc_target(index, offset)
        self.reader.set_text(section.text, offset, section.images, section.links, section.formats)
        if self.moyu_mode:
            self._refresh_moyu_sentence(offset)
        self._sync_reader_search_highlights()
        self._update_progress()
        self.schedule_save()

    def _on_reader_reflowed(self):
        self._sync_search_bar_responsive()
        if hasattr(self, "search_result_model"):
            self.search_result_model.invalidate_pages()
            self.search_results.viewport().update()
        self._update_progress()
        self._update_jump_navigation()

    def _filter_chapters(self, _text=""):
        if not hasattr(self, "toc_filter"):
            return
        query = self.toc_filter.text().strip().casefold()
        for row in range(self.chapters.count()):
            item = self.chapters.item(row)
            matched = not query or (query in item.text().casefold())
            item.setHidden(not matched)
        current = self.chapters.currentItem()
        if current is not None and not current.isHidden():
            self.chapters.scrollToItem(current)

    def _is_search_bar_open(self) -> bool:
        return bool(
            self._search_active
            and self.book
            and self.main_pages.currentWidget() is self.reader_page
        )

    def _sync_search_bar_responsive(self):
        if not hasattr(self, "search_bar") or not hasattr(self, "search_icon_label"):
            return
        bar_w = self.search_panel.width() if self.search_panel.width() > 60 else self.search_bar.width()
        if bar_w <= 60:
            bar_w = max(150, self.sidebar.width())
        self.search_icon_label.setVisible(bar_w >= 210)
        self.search_count_label.setVisible(bar_w >= 178)

    def _toggle_sidebar_info(self, checked=False):
        info_index = self._tab_info
        current = self.reading_tabs.currentIndex()
        if checked:
            if current != info_index:
                self._sidebar_info_return_tab = current
            self.reading_tabs.setCurrentIndex(info_index)
        elif current == info_index:
            target = self._sidebar_info_return_tab
            if target == info_index or not 0 <= target < self.reading_tabs.stack.count():
                target = self._tab_toc
            self.reading_tabs.setCurrentIndex(target)

    def _on_reading_tab_changed(self, index: int):
        self.sidebar_info_button.setChecked(index == self._tab_info)
        if index == self.search_tab_index:
            if not self.book:
                self.reading_tabs.setCurrentIndex(self._last_sidebar_tab_index)
            elif not self._search_active:
                self._search_return_tab = self._last_sidebar_tab_index
                self.open_book_search(focus_input=True)
            return
        self._last_sidebar_tab_index = index
        if self._search_active:
            self._search_return_tab = index

    def _set_search_results_state(self, query: str):
        has_results = bool(self._search_matches)
        self.search_results.setVisible(has_results)
        self.search_empty_label.setVisible(not has_results)
        if not has_results:
            source = "无匹配" if query else "输入关键词搜索全书"
            self.search_empty_label.setText(self.t(source))

    def _sync_search_result_selection(self, match_index: int):
        if not 0 <= match_index < len(self._search_matches):
            return
        model_index = self.search_result_model.index(match_index, 0)
        self.search_results.selectionModel().setCurrentIndex(
            model_index, QItemSelectionModel.SelectionFlag.ClearAndSelect,
        )
        self.search_results.scrollTo(model_index)

    def _search_result_activated(self, model_index: QModelIndex):
        if model_index.isValid():
            self._go_to_search_match(model_index.row(), remember_origin=True)
            self.reader.setFocus()

    def open_book_search(self, focus_input=True, *, initial_query: str | None = None):
        if self.focus_mode or self.moyu_mode or not self.book:
            return
        self._hide_term_hover()
        if hasattr(self, "appearance_popover"):
            self.appearance_popover.dismiss()
        if hasattr(self, "focus_popover"):
            self.focus_popover.dismiss()
        if hasattr(self, "settings_page") and self.main_pages.currentWidget() is self.settings_page:
            self.settings_page.cancel_active_capture()
            self._pre_settings_page = None
        if self.main_pages.currentWidget() is not self.reader_page:
            self.show_reading_page()
        if not self._search_active:
            current_tab = self.reading_tabs.currentIndex()
            self._search_return_tab = (
                current_tab if current_tab != self.search_tab_index else self._last_sidebar_tab_index
            )
            self._search_active = True
            self._search_origin = (self.section_index, self.reader.offset)
            self._search_origin_pushed = False
            self.search_bar.show()
        elif self.reading_tabs.currentIndex() != self.search_tab_index:
            self._search_return_tab = self.reading_tabs.currentIndex()

        self.sidebar_collapsed = False
        self.reading_tabs.setCurrentIndex(self.search_tab_index)
        self._sync_page_navigation()
        self._sync_search_bar_responsive()
        anchor_override = None
        navigate_now = False
        if isinstance(initial_query, str) and initial_query.strip():
            cleaned = initial_query.strip()[:120]
            self.search_input.blockSignals(True)
            self.search_input.setText(cleaned)
            self.search_input.blockSignals(False)
            navigate_now = True
        else:
            selected = self.reader.selection.selectedText().replace("\u2029", "\n").strip()
            if selected:
                first_line = selected.splitlines()[0].strip()[:120]
                if first_line:
                    sel_start_py = self.reader._offset_map.to_python(self.reader.selection.selectionStart())
                    anchor_override = (self.section_index, sel_start_py)
                    if first_line != self.search_input.text():
                        self.search_input.blockSignals(True)
                        self.search_input.setText(first_line)
                        self.search_input.blockSignals(False)
        if self.search_input.text():
            self._run_book_search(navigate=navigate_now, anchor=anchor_override)
        else:
            self._set_search_results_state("")
        self._update_jump_navigation()
        if focus_input:
            self.search_input.setFocus()
            self.search_input.selectAll()

    def search_in_book(self, query: str):
        cleaned = (query or "").strip()
        if not cleaned or self.focus_mode or self.moyu_mode or not self.book:
            return
        self.open_book_search(focus_input=True, initial_query=cleaned)

    def close_book_search(self, checked=False, *, focus_reader=True, clear_query=False):
        self._invalidate_book_search_requests()
        was_active = self._search_active
        self._search_active = False
        self._search_matches = []
        self._search_section_match_ranges = {}
        self._search_index = -1
        self._search_origin = None
        self._search_origin_pushed = False
        self.search_result_model.set_matches(None if clear_query else self.book, [])
        self.search_results.hide()
        self.search_empty_label.setText(self.t("输入关键词搜索全书"))
        self.search_empty_label.show()
        self.search_count_label.setText("")
        self.search_prev_button.setEnabled(False)
        self.search_next_button.setEnabled(False)
        if clear_query and hasattr(self, "search_input"):
            self.search_input.blockSignals(True)
            self.search_input.clear()
            self.search_input.blockSignals(False)
        if hasattr(self, "search_bar") and not self.search_bar.isHidden():
            self.search_bar.hide()
        if (
            was_active
            and self.reading_tabs.currentIndex() == self.search_tab_index
            and 0 <= self._search_return_tab < self.reading_tabs.stack.count()
        ):
            self.reading_tabs.setCurrentIndex(self._search_return_tab)
        if was_active:
            self.sidebar_collapsed = (
                self.width() < NARROW_BREAKPOINT or self._user_sidebar_collapsed
            )
        if hasattr(self, "reader"):
            self.reader.set_search_highlights([], None)
        self._sync_page_navigation()
        self._update_jump_navigation()
        if focus_reader and was_active and hasattr(self, "reader") and self.main_pages.currentWidget() is self.reader_page:
            self.reader.setFocus()

    def _on_book_search_changed(self, _text: str):
        if not self._search_active or not self.book:
            return
        self._schedule_book_search(navigate=True, debounce=True)

    def _run_book_search(self, *, navigate: bool = True, anchor: tuple[int, int] | None = None):
        if not self.book:
            return

        self._schedule_book_search(navigate=navigate, anchor=anchor, debounce=False)

    def _invalidate_book_search_requests(self):
        self._book_search_generation += 1
        self.book_search_timer.stop()
        if self._book_search_cancel_event is not None:
            self._book_search_cancel_event.set()
        self._book_search_cancel_event = None
        self._book_search_request = None

    def _schedule_book_search(self, *, navigate: bool, anchor=None, debounce: bool):
        self._invalidate_book_search_requests()
        query = self.search_input.text()
        if not query:
            self._search_matches = []
            self._search_section_match_ranges = {}
            self._search_index = -1
            self.search_result_model.set_matches(self.book, [])
            self._set_search_results_state("")
            self.search_count_label.setText("")
            self.search_prev_button.setEnabled(False)
            self.search_next_button.setEnabled(False)
            self.reader.set_search_highlights([], None)
            return

        generation = self._book_search_generation
        self._book_search_request = (generation, self.book.id, query, navigate, anchor)
        self._search_matches = []
        self._search_section_match_ranges = {}
        self._search_index = -1
        self.search_result_model.set_matches(self.book, [])
        self.search_results.hide()
        self.search_empty_label.setText(self.t("正在搜索…"))
        self.search_empty_label.show()
        self.search_count_label.setText(self.t("搜索中…"))
        self.search_prev_button.setEnabled(False)
        self.search_next_button.setEnabled(False)
        self.reader.set_search_highlights([], None)
        if debounce:
            self.book_search_timer.start()
        else:
            self._dispatch_book_search()

    def _dispatch_book_search(self):
        request = self._book_search_request
        if request is None:
            return
        generation, book_id, query, navigate, anchor = request
        if (
            generation != self._book_search_generation
            or not self._search_active
            or not self.book
            or self.book.id != book_id
            or self.search_input.text() != query
        ):
            return

        reference = (
            anchor if anchor is not None
            else self._search_origin if self._search_origin is not None
            else (self.section_index, self.reader.offset)
        )
        section_texts = tuple(section.text for section in self.book.sections)
        cancel_event = Event()
        self._book_search_cancel_event = cancel_event
        task = BookSearchTask(
            generation, book_id, query, section_texts, reference,
            navigate, anchor, cancel_event,
        )
        task.signals.completed.connect(self._book_search_completed)
        task.signals.failed.connect(self._book_search_failed)
        self._book_search_request = None
        self.search_pool.start(task)

    def _book_search_completed(self, result):
        generation, book_id, query, matches, section_ranges, chosen, navigate, _anchor = result
        if (
            generation != self._book_search_generation
            or not self._search_active
            or not self.book
            or self.book.id != book_id
            or self.search_input.text() != query
        ):
            return

        self._book_search_cancel_event = None
        self._search_matches = matches
        self._search_section_match_ranges = section_ranges
        self.search_result_model.set_matches(self.book, matches)
        self._set_search_results_state(query)
        if not matches:
            self._search_index = -1
            self.search_count_label.setText(self.t("无匹配"))
            self.search_prev_button.setEnabled(False)
            self.search_next_button.setEnabled(False)
            self.reader.set_search_highlights([], None)
            return
        self.search_prev_button.setEnabled(True)
        self.search_next_button.setEnabled(True)
        if navigate:
            self._go_to_search_match(chosen)
        else:
            self._search_index = chosen
            self.search_count_label.setText(f"{chosen + 1} / {len(matches)}")
            self._sync_search_result_selection(chosen)
            self._sync_reader_search_highlights()

    def _book_search_failed(self, failure):
        generation, book_id, query = failure
        if (
            generation != self._book_search_generation
            or not self._search_active
            or not self.book
            or self.book.id != book_id
            or self.search_input.text() != query
        ):
            return
        self._book_search_cancel_event = None
        self.search_empty_label.setText(self.t("搜索失败"))
        self.search_empty_label.show()
        self.search_results.hide()
        self.search_count_label.setText(self.t("搜索失败"))
        self.search_prev_button.setEnabled(False)
        self.search_next_button.setEnabled(False)

    def find_next_match(self):
        if not self._search_matches:
            return
        next_idx = (self._search_index + 1) % len(self._search_matches)
        self._go_to_search_match(next_idx, remember_origin=True)

    def find_previous_match(self):
        if not self._search_matches:
            return
        prev_idx = (self._search_index - 1) % len(self._search_matches)
        self._go_to_search_match(prev_idx, remember_origin=True)

    def _go_to_search_match(self, match_index: int, *, remember_origin: bool = False):
        if not self.book or not 0 <= match_index < len(self._search_matches):
            return
        self._search_index = match_index
        sec_idx, start_off, _end_off = self._search_matches[match_index]
        self.search_count_label.setText(f"{match_index + 1} / {len(self._search_matches)}")
        self._sync_search_result_selection(match_index)
        origin = (
            (self.section_index, self.reader.offset)
            if remember_origin or self._search_origin is None
            else self._search_origin
        )
        dest = (sec_idx, start_off)
        if remember_origin:
            if not self._same_section_page(origin, dest):
                self._push_jump_origin(origin)
        elif not self._same_section_page(origin, dest):
            if not self._search_origin_pushed:
                self._push_jump_origin(origin)
                self._search_origin_pushed = True
        elif self._search_origin_pushed and self._jump_history and self._same_section_page(self._jump_history[-1], dest):
            self._jump_history.pop()
            self._persist_jump_history()
            self._search_origin_pushed = False
        self._in_search_navigation = True
        try:
            if sec_idx != self.section_index:
                self.open_section(sec_idx, start_off)
            else:
                target_page = min(
                    self.reader.page_count - 1,
                    max(0, bisect_right(self.reader.page_offsets, start_off) - 1),
                )
                if target_page != self.reader.page:
                    self.reader.go_page(target_page)
                    self._update_progress()
                    self.schedule_save()
                self._sync_reader_search_highlights()
        finally:
            self._in_search_navigation = False
        if remember_origin:
            self._search_origin = (self.section_index, self.reader.offset)
            self._search_origin_pushed = False
        self._update_jump_navigation()

    def _sync_reader_search_highlights(self):
        if not hasattr(self, "reader"):
            return
        if not self._search_active or not self._search_matches or not self.book:
            self.reader.set_search_highlights([], None)
            return
        range_start, range_end = self._search_section_match_ranges.get(self.section_index, (0, 0))
        sec_matches = [
            (start_off, end_off)
            for _sec_i, start_off, end_off in self._search_matches[range_start:range_end]
        ]
        active_range = None
        if 0 <= self._search_index < len(self._search_matches):
            act_sec, act_start, act_end = self._search_matches[self._search_index]
            if act_sec == self.section_index:
                active_range = (act_start, act_end)
        self.reader.set_search_highlights(sec_matches, active_range)

    def _same_section_page(
        self,
        pos_a: tuple[int, int],
        pos_b: tuple[int, int],
        *,
        target_height: int | None = None,
    ) -> bool:
        if not self.book or pos_a[0] != pos_b[0]:
            return False
        sec_idx = pos_a[0]
        if not 0 <= sec_idx < len(self.book.sections):
            return False
        section = self.book.sections[sec_idx]
        page_a = self.reader.page_for_section_offset(section, pos_a[1], target_height=target_height)
        page_b = self.reader.page_for_section_offset(section, pos_b[1], target_height=target_height)
        return page_a == page_b

    def _push_jump_origin(self, origin: tuple[int, int]):
        if not self.book or not 0 <= origin[0] < len(self.book.sections):
            return
        orig_sec = origin[0]
        orig_off = max(0, min(len(self.book.sections[orig_sec].text), int(origin[1])))
        self._forward_history.clear()
        self._append_jump_history_position(self._jump_history, (orig_sec, orig_off))
        self._persist_jump_history()

    def _append_jump_history_position(self, history, position):
        section_index, offset = position
        normalized = (
            section_index,
            max(0, min(len(self.book.sections[section_index].text), int(offset))),
        )
        if history and self._same_section_page(history[-1], normalized):
            history[-1] = normalized
        else:
            history.append(normalized)
            if len(history) > self._max_jump_history:
                del history[:-self._max_jump_history]

    def _restore_jump_history(self, book):
        section_indices = {section.id: index for index, section in enumerate(book.sections)}
        def restore_history(key):
            restored = []
            records = self.store.state.get(key, {}).get(book.id, [])
            for record in records[-self._max_jump_history:]:
                index = section_indices.get(record.get("section_id"))
                if index is None:
                    continue
                offset = max(0, min(len(book.sections[index].text), record.get("offset", 0)))
                restored.append((index, offset))
            return restored

        self._jump_history = restore_history("jump_history")
        self._forward_history = restore_history("forward_history")

    def _persist_jump_history(self, *, schedule=True):
        if not self.book:
            return
        for key, positions in (
            ("jump_history", self._jump_history),
            ("forward_history", self._forward_history),
        ):
            histories = self.store.state.setdefault(key, {})
            histories.pop(self.book.id, None)
            records = [
                {"section_id": self.book.sections[index].id, "offset": offset}
                for index, offset in positions[-self._max_jump_history:]
                if 0 <= index < len(self.book.sections)
            ]
            if records:
                histories[self.book.id] = records
            while len(histories) > MAX_JUMP_HISTORY_BOOKS:
                histories.pop(next(iter(histories)))
        if schedule:
            self.schedule_save()

    def _navigate_with_jump_history(self, target_section: int, target_offset: int) -> bool:
        if not self.book or not 0 <= target_section < len(self.book.sections):
            return False
        clamped_target = max(0, min(len(self.book.sections[target_section].text), int(target_offset)))
        origin = (self.section_index, self.reader.offset)
        dest = (target_section, clamped_target)
        if not self._same_section_page(origin, dest):
            self._push_jump_origin(origin)
        self.open_section(target_section, clamped_target)
        self._search_origin = (self.section_index, self.reader.offset)
        self._search_origin_pushed = False
        self._update_jump_navigation()
        return True

    def jump_back(self):
        if (
            self.focus_mode
            or not self.book
            or not self._jump_history
            or self.main_pages.currentWidget() is not self.reader_page
        ):
            return False
        target_section, target_offset = self._jump_history.pop()
        if not 0 <= target_section < len(self.book.sections):
            self._persist_jump_history()
            self._update_jump_navigation()
            return False
        current = (self.section_index, self.reader.offset)
        if not self._same_section_page(current, (target_section, target_offset)):
            self._append_jump_history_position(self._forward_history, current)
        self._persist_jump_history()
        self.open_section(target_section, target_offset)
        self._search_origin = (self.section_index, self.reader.offset)
        self._search_origin_pushed = False
        self._update_jump_navigation()
        self.reader.setFocus()
        return True

    def jump_forward(self):
        if (
            self.focus_mode
            or not self.book
            or not self._forward_history
            or self.main_pages.currentWidget() is not self.reader_page
        ):
            return False
        target_section, target_offset = self._forward_history.pop()
        if not 0 <= target_section < len(self.book.sections):
            self._persist_jump_history()
            self._update_jump_navigation()
            return False
        current = (self.section_index, self.reader.offset)
        if not self._same_section_page(current, (target_section, target_offset)):
            self._append_jump_history_position(self._jump_history, current)
        self._persist_jump_history()
        self.open_section(target_section, target_offset)
        self._search_origin = (self.section_index, self.reader.offset)
        self._search_origin_pushed = False
        self._update_jump_navigation()
        self.reader.setFocus()
        return True

    def _update_jump_navigation(self):
        if not hasattr(self, "jump_back_button"):
            return
        should_show = bool(self.book and self._jump_history and not self.focus_mode)
        forward_show = bool(self.book and self._forward_history and not self.focus_mode)
        destinations = (
            (self._jump_history, self.jump_back_button, self.jump_back_label, "返回", "返回上一处阅读位置 (Alt+Left)", should_show),
            (self._forward_history, self.jump_forward_button, self.jump_forward_label, "跳回", "跳回刚才的位置 (Alt+Right)", forward_show),
        )
        has_targets = False
        for history, button, label, action, shortcut, visible in destinations:
            if not visible or not history or not 0 <= history[-1][0] < len(self.book.sections):
                button.hide()
                label.set_target("", "")
                label.hide()
                button.setToolTip(self.t(shortcut))
                continue

            has_targets = True
            button.show()
            label.show()
            section_index, _offset = history[-1]
            chapter_name = self.book.sections[section_index].title.strip() or self.book.title
            label.set_target(chapter_name, "…")
            button.setToolTip(f"{self.t(action)}：{chapter_name} · {self.t(shortcut)}")

        if has_targets:
            self.jump_target_page_timer.start()
        else:
            self.jump_target_page_timer.stop()
        self._sync_reader_navigation_labels()

    def _refresh_jump_target_pages(self):
        if (
            not self.book
            or self.focus_mode
            or self.main_pages.currentWidget() is not self.reader_page
        ):
            return
        page_cache = {}
        destinations = (
            (self._jump_history, self.jump_back_button, self.jump_back_label, "返回", "返回上一处阅读位置 (Alt+Left)"),
            (self._forward_history, self.jump_forward_button, self.jump_forward_label, "跳回", "跳回刚才的位置 (Alt+Right)"),
        )
        for history, button, label, action, shortcut in destinations:
            if not history or button.isHidden():
                continue
            section_index, offset = history[-1]
            if not 0 <= section_index < len(self.book.sections):
                continue
            section = self.book.sections[section_index]
            chapter_name = section.title.strip() or self.book.title
            key = (section_index, offset)
            if key not in page_cache:
                page_cache[key] = self.reader.page_for_section_offset(section, offset)
            page_text = self.t("第 {page} 页", page=page_cache[key])
            label.set_target(chapter_name, page_text)
            target_text = f"{chapter_name} · {page_text}"
            button.setToolTip(f"{self.t(action)}：{target_text} · {self.t(shortcut)}")
        self._sync_reader_navigation_labels()

    def _select_toc_target(self, section_index, offset):
        best_row = -1
        best_offset = -1
        for row in range(self.chapters.count()):
            target = self.chapters.item(row).data(Qt.ItemDataRole.UserRole)
            if target and target[0] == section_index and target[1] <= offset and target[1] >= best_offset:
                best_row, best_offset = row, target[1]
        self.chapters.setCurrentRow(best_row)

    @staticmethod
    def _chapter_identity(section):
        if section.source_path:
            return section.source_path
        return section.id.rpartition(":")[0] or section.id

    def _chapter_destination(self, direction):
        if not self.book or direction not in (-1, 1):
            return None
        identity = self._chapter_identity(self.book.sections[self.section_index])
        candidate = self.section_index + direction
        while 0 <= candidate < len(self.book.sections) and self._chapter_identity(self.book.sections[candidate]) == identity:
            candidate += direction
        if not 0 <= candidate < len(self.book.sections):
            return None
        if direction < 0:
            target_identity = self._chapter_identity(self.book.sections[candidate])
            while candidate > 0 and self._chapter_identity(self.book.sections[candidate - 1]) == target_identity:
                candidate -= 1
        return candidate

    def turn_chapter(self, direction):
        target = self._chapter_destination(direction)
        if target is None:
            return
        if self._forward_history:
            self._forward_history.clear()
            self._persist_jump_history()
        self.open_section(target)
        self._update_jump_navigation()
        if self._search_active and not self._in_search_navigation:
            self._search_origin = (self.section_index, self.reader.offset)
            self._search_origin_pushed = False
        self.reader.setFocus()
        self._update_progress()
        self.schedule_save()

    def turn_page(self, direction):
        if not self.book:
            return
        self.reader.flush_pending_reflow()
        destination = self.reader.page + direction
        if destination < 0:
            if self.section_index > 0:
                self.open_section(self.section_index - 1)
                self.reader.go_page(self.reader.page_count - 1)
        elif destination >= self.reader.page_count:
            if self.section_index + 1 < len(self.book.sections):
                self.open_section(self.section_index + 1)
        else:
            self.reader.go_page(destination)
        if self._search_active and not self._in_search_navigation:
            self._search_origin = (self.section_index, self.reader.offset)
            self._search_origin_pushed = False
        self._update_progress()
        self.schedule_save()

    def _update_progress(self):
        ready = self.book is not None
        if not hasattr(self, "previous"):
            return
        self.previous.setEnabled(ready and (self.section_index > 0 or self.reader.page > 0))
        self.next.setEnabled(ready and (self.section_index + 1 < len(self.book.sections) or self.reader.page + 1 < self.reader.page_count))
        self.previous_chapter.setEnabled(ready and self._chapter_destination(-1) is not None)
        self.next_chapter.setEnabled(ready and self._chapter_destination(1) is not None)
        self.progress.setEnabled(ready)
        self.focus_button.setEnabled(True)
        if hasattr(self, "focus_popover") and not self.focus_popover.isHidden():
            self.focus_popover.sync_settings(
                self.settings, colors(self.settings.theme),
                sampled_color=self._sampled_focus_color, has_book=ready,
                sampled_complex=self._sampled_focus_complex,
            )
        if hasattr(self, "tray_focus_action"):
            self.tray_focus_action.setEnabled(ready and not self.moyu_mode)
        if hasattr(self, "tray_moyu_action"):
            self.tray_moyu_action.setEnabled(ready)
        if ready:
            identity = self._chapter_identity(self.book.sections[self.section_index])
            chapter_start = self.section_index
            while chapter_start > 0 and self._chapter_identity(self.book.sections[chapter_start - 1]) == identity:
                chapter_start -= 1
            chapter_end = self.section_index + 1
            while chapter_end < len(self.book.sections) and self._chapter_identity(self.book.sections[chapter_end]) == identity:
                chapter_end += 1
            chapter_sections = self.book.sections[chapter_start:chapter_end]
            chapter_total = sum(len(section.text) for section in chapter_sections)
            chapter_current = sum(len(section.text) for section in self.book.sections[chapter_start:self.section_index]) + self.reader.offset
            chapter_fraction = chapter_current / max(1, chapter_total)
            if self.section_index == chapter_end - 1 and self.reader.page == self.reader.page_count - 1:
                chapter_fraction = 1

            if self.book.encoding == "PDF":
                pdf_page_indices = {}
                for section in self.book.sections:
                    if section.source_path not in pdf_page_indices:
                        pdf_page_indices[section.source_path] = len(pdf_page_indices)
                page_index = pdf_page_indices.get(identity, 0)
                book_fraction = (page_index + chapter_fraction) / max(1, len(pdf_page_indices))
            else:
                total = sum(len(section.text) for section in self.book.sections)
                book_current = sum(len(section.text) for section in self.book.sections[:self.section_index]) + self.reader.offset
                book_fraction = book_current / max(1, total)
                if self.section_index == len(self.book.sections) - 1 and self.reader.page == self.reader.page_count - 1:
                    book_fraction = 1

            record = next((item for item in self.store.state["books"] if item.get("id") == self.book.id), None)
            if record is not None:
                record["reading_chapter"] = self.book.sections[chapter_start].title
                record["reading_progress"] = book_fraction

            self.page_label.setProperty("translation_source", None)
            full_label = tr("{page} / {pages} 页 · 本章 {progress}", self.settings.language, page=self.reader.page + 1, pages=self.reader.page_count, progress=f"{chapter_fraction:.0%}")
            compact_label = tr(
                "{page}/{pages} · 本章{progress}", self.settings.language,
                page=self.reader.page + 1, pages=self.reader.page_count,
                progress=f"{chapter_fraction:.0%}",
            )
            self.page_label.setProperty("full_text", full_label)
            self.page_label.setProperty("compact_text", compact_label)
            self.page_label.setToolTip(full_label)
            if not self.progress.isSliderDown():
                self.progress.setValue(round(chapter_fraction * 1000))
            progress_pct = f"{book_fraction:.0%}"
            self.book_progress_label.setText(self.t("全书进度：{progress}", progress=progress_pct))
            self.book_progress_bar.setValue(round(book_fraction * 1000))
            if hasattr(self, "footer_book_progress"):
                self.footer_book_progress.setText(self.t("全书 {progress}", progress=progress_pct))
            self._update_sidebar_book_card(progress_pct)
        else:
            self.book_progress_label.setText(self.t("全书进度：--"))
            self.book_progress_bar.setValue(0)
            if hasattr(self, "footer_book_progress"):
                self.footer_book_progress.setText("")
            self._update_sidebar_book_card("")
        self._sync_reader_navigation_labels()

    def _seek(self):
        if not self.book:
            return
        identity = self._chapter_identity(self.book.sections[self.section_index])
        chapter_start = self.section_index
        while chapter_start > 0 and self._chapter_identity(self.book.sections[chapter_start - 1]) == identity:
            chapter_start -= 1
        chapter_end = self.section_index + 1
        while chapter_end < len(self.book.sections) and self._chapter_identity(self.book.sections[chapter_end]) == identity:
            chapter_end += 1
        sections = self.book.sections[chapter_start:chapter_end]
        total = sum(len(section.text) for section in sections)
        target = round(total * self.progress.value() / 1000)
        for index, section in enumerate(sections, start=chapter_start):
            if target < len(section.text) or index == chapter_end - 1:
                self._navigate_with_jump_history(index, min(target, len(section.text)))
                break
            target -= len(section.text)
        self.reader.setFocus()

    def _chapter_activated(self, item):
        self._activate_navigation_item(item)

    def _refresh_bookmarks(self):
        self.bookmarks.clear()
        if not self.book:
            self.bookmark_note_button.setEnabled(False)
            self.bookmark_remove_button.setEnabled(False)
            return
        records = self.store.state.get("bookmarks", {}).get(self.book.id, [])
        for index, record in enumerate(records):
            title = self.t("书签名称：{label}", label=record["label"])
            note = record.get("note", "")
            item = QListWidgetItem(title + (f"\n{note}" if note else ""))
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.bookmarks.addItem(item)
        self.bookmark_note_button.setEnabled(bool(records))
        self.bookmark_remove_button.setEnabled(bool(records))

    def add_bookmark(self):
        if not self.book:
            return
        all_records = self.store.state.setdefault("bookmarks", {})
        records = all_records.setdefault(self.book.id, [])
        section = self.book.sections[self.section_index]
        offset = self.reader.offset
        for index, record in enumerate(records):
            if record.get("section_id") == section.id and record.get("offset") == offset:
                self.bookmarks.setCurrentRow(index)
                self.set_status("书签已存在。")
                return
        if sum(len(values) for values in all_records.values()) >= MAX_BOOKMARKS:
            self.set_status("书签数量已达上限。")
            return
        record = asdict(Bookmark(
            section_id=section.id,
            offset=offset,
            label=f"{section.title} · {self.t('第 {page} 页', page=self.reader.page + 1)}",
            note="",
            created=datetime.now(timezone.utc).isoformat(),
        ))
        records.insert(0, record)
        self._refresh_bookmarks()
        self.bookmarks.setCurrentRow(0)
        self.reading_tabs.setCurrentIndex(self._tab_bookmarks)
        self.set_status("书签已添加。")
        self.schedule_save()

    def set_bookmark_note(self, index, note):
        if not self.book:
            return
        records = self.store.state.get("bookmarks", {}).get(self.book.id, [])
        if not 0 <= index < len(records):
            return
        records[index]["note"] = str(note).replace("\x00", "")[:MAX_BOOKMARK_NOTE]
        self._refresh_bookmarks()
        self.bookmarks.setCurrentRow(index)
        self.schedule_save()

    def edit_bookmark_note(self):
        index = self.bookmarks.currentRow()
        if not self.book or index < 0:
            return
        records = self.store.state.get("bookmarks", {}).get(self.book.id, [])
        if index >= len(records):
            return
        note, accepted = QInputDialog.getMultiLineText(self, self.t("编辑笔记"), self.t("笔记"), records[index].get("note", ""))
        if accepted:
            self.set_bookmark_note(index, note)

    def remove_bookmark(self):
        index = self.bookmarks.currentRow()
        if not self.book or index < 0:
            return
        records = self.store.state.get("bookmarks", {}).get(self.book.id, [])
        if index < len(records):
            records.pop(index)
            self._refresh_bookmarks()
            self.set_status("书签已删除。")
            self.schedule_save()

    def _bookmark_activated(self, item):
        if not self.book or item is None:
            return
        index = item.data(Qt.ItemDataRole.UserRole)
        records = self.store.state.get("bookmarks", {}).get(self.book.id, [])
        if not isinstance(index, int) or not 0 <= index < len(records):
            return
        record = records[index]
        target_index = next((i for i, section in enumerate(self.book.sections) if section.id == record["section_id"]), None)
        if target_index is None:
            self.set_status("书签目标不可用。")
            return
        self._navigate_with_jump_history(target_index, record["offset"])
        self.reader.setFocus()

    def _sync_escape_shortcut(self):
        if not hasattr(self, "escape_shortcut"):
            return
        settings_active = hasattr(self, "settings_page") and self.main_pages.currentWidget() is self.settings_page
        hover_open = hasattr(self, "term_hover_card") and not self.term_hover_card.isHidden()
        self.escape_shortcut.setEnabled(
            self.focus_mode or self.moyu_mode or settings_active or self._is_search_bar_open() or hover_open
        )

    def _on_term_hovered(self, record, anchor_rect):
        if (
            not isinstance(record, dict)
            or not isinstance(anchor_rect, QRect)
            or not self.book
            or self.closed
            or self._window_hidden
            or getattr(self, "moyu_mode", False)
            or self._focus_adjust_active
            or not hasattr(self, "term_hover_card")
            or self.main_pages.currentWidget() is not self.reader_page
        ):
            return
        if (
            (hasattr(self, "appearance_popover") and not self.appearance_popover.isHidden())
            or (hasattr(self, "focus_popover") and not self.focus_popover.isHidden())
        ):
            return
        top_left = self.reader.mapTo(self.surface, anchor_rect.topLeft())
        surface_rect = QRect(top_left, anchor_rect.size())
        reader_bounds = QRect(self.reader.mapTo(self.surface, QPoint(0, 0)), self.reader.size())
        self.term_hover_card.show_for(
            record.get("term", ""),
            record.get("note", ""),
            surface_rect,
            bounds_rect=reader_bounds,
        )
        self._sync_escape_shortcut()

    def _on_term_hover_cleared(self):
        if hasattr(self, "term_hover_card"):
            self.term_hover_card.dismiss()
        self._sync_escape_shortcut()

    def _on_moyu_term_hovered(self, record, global_anchor_rect):
        if (
            not self.moyu_mode
            or not isinstance(record, dict)
            or not isinstance(global_anchor_rect, QRect)
            or not self.book
            or self.closed
            or self._window_hidden
            or self._moyu_auto_hidden
            or self._moyu_menu_open
            or self._focus_adjust_active
            or self.moyu_strip.interaction_active
            or not hasattr(self, "moyu_term_hover_card")
        ):
            return
        screen = (
            QGuiApplication.screenAt(global_anchor_rect.center())
            or self.screen()
            or QGuiApplication.primaryScreen()
        )
        bounds = screen.availableGeometry() if screen is not None else None
        strip_rect = self.frameGeometry()
        card_anchor = QRect(
            global_anchor_rect.left(),
            min(global_anchor_rect.top(), strip_rect.top()),
            global_anchor_rect.width(),
            max(global_anchor_rect.bottom(), strip_rect.bottom()) - min(global_anchor_rect.top(), strip_rect.top()) + 1,
        )
        self.moyu_term_hover_card.show_for(
            record.get("term", ""),
            record.get("note", ""),
            card_anchor,
            bounds_rect=bounds,
            prefer_above=True,
        )

    def _on_moyu_term_hover_cleared(self):
        if hasattr(self, "moyu_term_hover_card"):
            self.moyu_term_hover_card.dismiss()

    def _hide_term_hover(self):
        if hasattr(self, "reader"):
            self.reader.clear_term_hover()
        if hasattr(self, "moyu_strip"):
            self.moyu_strip.clear_term_hover()
        if hasattr(self, "term_hover_card"):
            self.term_hover_card.dismiss()
        if hasattr(self, "moyu_term_hover_card"):
            self.moyu_term_hover_card.dismiss()
        self._sync_escape_shortcut()

    def get_term_notes(self, book_id: str | None = None) -> list[dict]:
        target_id = book_id if book_id is not None else (self.book.id if self.book else "")
        if not target_id:
            return []
        return list(self.store.state.get("term_notes", {}).get(target_id, []))

    def _term_notes_read_only_warning(self) -> str:
        if getattr(self.store, "term_notes_read_only", False) and getattr(self.store, "term_notes_warning", ""):
            return self.store.term_notes_warning
        return "保存数据来自不支持的版本。已保护原文件，本次不会保存更改。"

    def _refresh_term_notes(self, select_term: str | None = None):
        if not hasattr(self, "term_notes_list"):
            return
        previous_term = select_term
        if previous_term is None:
            current_item = self.term_notes_list.currentItem()
            if current_item is not None:
                previous_term = current_item.data(Qt.ItemDataRole.UserRole)
        self.term_notes_list.clear()
        if not self.book:
            if hasattr(self, "reader"):
                self.reader.set_term_notes([])
            if hasattr(self, "moyu_strip"):
                self.moyu_strip.set_term_notes([])
            self.term_add_button.setEnabled(False)
            self.term_edit_button.setEnabled(False)
            self.term_remove_button.setEnabled(False)
            if hasattr(self, "term_import_button"):
                self.term_import_button.setEnabled(False)
            return
        records = self.store.state.get("term_notes", {}).get(self.book.id, [])
        self.reader.set_term_notes(records)
        if hasattr(self, "moyu_strip"):
            self.moyu_strip.set_term_notes(records)
        tip_hint = self.t("双击在全书搜索此词条")
        self.term_notes_list.ensurePolished()
        selected_row = -1
        for row_idx, record in enumerate(records):
            term = record.get("term", "")
            note = record.get("note", "")
            item = QListWidgetItem("")
            item.setData(Qt.ItemDataRole.UserRole, term)
            item.setData(Qt.ItemDataRole.UserRole + 1, " ".join(note.split()))
            item.setToolTip(f"{term}\n{note}\n\n{tip_hint}")
            self.term_notes_list.addItem(item)
            if previous_term and term.casefold() == previous_term.casefold():
                selected_row = row_idx
        self._elide_term_note_items()
        self.term_add_button.setEnabled(True)
        if hasattr(self, "term_import_button"):
            self.term_import_button.setEnabled(True)
        self._filter_term_notes()
        if selected_row >= 0 and not self.term_notes_list.item(selected_row).isHidden():
            self.term_notes_list.setCurrentRow(selected_row)
        else:
            first_visible = next(
                (r for r in range(self.term_notes_list.count()) if not self.term_notes_list.item(r).isHidden()),
                -1,
            )
            self.term_notes_list.setCurrentRow(first_visible)
        self._sync_term_note_buttons()

    def _elide_term_note_items(self):
        if not hasattr(self, "term_notes_list") or not self.term_notes_list.count():
            return
        self.term_notes_list.ensurePolished()
        metrics = QFontMetrics(self.term_notes_list.font())
        sidebar_w = self.sidebar.width() if hasattr(self, "sidebar") and self.sidebar.width() > 60 else 224
        avail_w = max(72, sidebar_w - 54)
        for row in range(self.term_notes_list.count()):
            item = self.term_notes_list.item(row)
            term = item.data(Qt.ItemDataRole.UserRole) or ""
            note_line = item.data(Qt.ItemDataRole.UserRole + 1) or ""
            term_elided = metrics.elidedText(term, Qt.TextElideMode.ElideRight, avail_w)
            preview = metrics.elidedText(note_line, Qt.TextElideMode.ElideRight, avail_w)
            item.setText(f"{term_elided}\n{preview}" if preview else term_elided)

    def _filter_term_notes(self, _text=""):
        if not hasattr(self, "term_notes_list") or not hasattr(self, "term_notes_filter"):
            return
        query = self.term_notes_filter.text().strip().casefold()
        records_by_key = {
            r.get("term", "").casefold(): r
            for r in self.get_term_notes()
        }
        for row in range(self.term_notes_list.count()):
            item = self.term_notes_list.item(row)
            term = item.data(Qt.ItemDataRole.UserRole) or ""
            record = records_by_key.get(term.casefold(), {})
            haystack = f"{term}\n{record.get('note', '')}".casefold()
            item.setHidden(bool(query) and query not in haystack)
        current = self.term_notes_list.currentItem()
        if current is None or current.isHidden():
            first_visible = next(
                (r for r in range(self.term_notes_list.count()) if not self.term_notes_list.item(r).isHidden()),
                -1,
            )
            self.term_notes_list.setCurrentRow(first_visible)
        self._sync_term_note_buttons()

    def _sync_term_note_buttons(self, *_args):
        if not hasattr(self, "term_notes_list"):
            return
        has_book = self.book is not None
        item = self.term_notes_list.currentItem()
        has_selection = has_book and item is not None and not item.isHidden()
        self.term_add_button.setEnabled(has_book)
        self.term_edit_button.setEnabled(has_selection)
        self.term_remove_button.setEnabled(has_selection)
        if hasattr(self, "term_import_button"):
            self.term_import_button.setEnabled(has_book)

    def upsert_term_note(self, term: str, note: str, *, original_term: str | None = None) -> bool:
        if not self.book:
            return False
        if self.store.read_only or getattr(self.store, "term_notes_read_only", False):
            self.set_status(self._term_notes_read_only_warning())
            return False
        clean_term = normalize_term_text(term)
        clean_note = normalize_term_note(note)
        if not clean_term or not clean_note:
            return False
        all_records = self.store.state.setdefault("term_notes", {})
        records = deepcopy(all_records.get(self.book.id, []))
        new_key = clean_term.casefold()
        orig_key = normalize_term_text(original_term).casefold() if original_term else ""
        is_new_term = False

        existing_idx = next(
            (i for i, item in enumerate(records) if item.get("term", "").casefold() == new_key),
            None,
        )
        orig_idx = (
            next((i for i, item in enumerate(records) if item.get("term", "").casefold() == orig_key), None)
            if orig_key
            else None
        )

        if orig_key:
            if orig_idx is None:
                return False
            if existing_idx is not None and existing_idx != orig_idx:
                return False
            records[orig_idx]["note"] = clean_note
            clean_term = records[orig_idx]["term"]
            status_key = "已更新词条标注：{term}"
        elif existing_idx is not None:
            records[existing_idx]["term"] = clean_term
            records[existing_idx]["note"] = clean_note
            status_key = "已更新词条标注：{term}"
        else:
            if len(records) >= MAX_TERM_NOTES:
                self.set_status(
                    "本书词条标注数量已达上限（{limit} 条）。",
                    limit=MAX_TERM_NOTES,
                    notify_in_focus=True,
                )
                return False
            record = asdict(TermNote(
                term=clean_term,
                note=clean_note,
                created=datetime.now(timezone.utc).isoformat(),
            ))
            records.insert(0, record)
            status_key = "已添加词条标注：{term}"
            is_new_term = True

        candidate_records = dict(all_records)
        candidate_records[self.book.id] = records
        if not self.store.term_notes_fit_storage_limit(candidate_records):
            message = (
                "词条标注数据将超过存储容量上限（{limit} MiB），未能新增词条。"
                if is_new_term
                else "词条标注数据将超过存储容量上限（{limit} MiB），未保存修改。"
            )
            self.set_status(
                message,
                limit=MAX_TERM_NOTES_BYTES_MIB,
                notify_in_focus=True,
            )
            return False

        all_records[self.book.id] = records

        self._refresh_term_notes(select_term=clean_term)
        self.set_status(status_key, term=clean_term)
        self.schedule_save()
        return True

    def remove_term_note(self, term: str) -> bool:
        if not self.book:
            return False
        if self.store.read_only or getattr(self.store, "term_notes_read_only", False):
            self.set_status(self._term_notes_read_only_warning())
            return False
        key = normalize_term_text(term).casefold()
        if not key:
            return False
        all_records = self.store.state.get("term_notes", {})
        records = all_records.get(self.book.id, [])
        idx = next((i for i, item in enumerate(records) if item.get("term", "").casefold() == key), None)
        if idx is None:
            return False
        removed = records.pop(idx)
        if not records:
            all_records.pop(self.book.id, None)
        self._refresh_term_notes()
        self.set_status("已删除词条标注：{term}", term=removed.get("term", term))
        self.schedule_save()
        return True

    def books_with_term_notes(self, exclude_book_id: str | None = None) -> list[dict]:
        all_notes = self.store.state.get("term_notes", {})
        if not isinstance(all_notes, dict):
            return []
        exclude = exclude_book_id if exclude_book_id is not None else (self.book.id if self.book else "")
        sources: list[dict] = []
        seen_ids: set[str] = set()
        for record in self.store.state.get("books", []):
            if not isinstance(record, dict):
                continue
            book_id = record.get("id", "")
            if not book_id or book_id == exclude or book_id in seen_ids:
                continue
            notes = all_notes.get(book_id)
            if not notes:
                continue
            seen_ids.add(book_id)
            sources.append({
                "id": book_id,
                "title": display_title_for_record(record),
                "records": deepcopy(notes),
            })
        for book_id, notes in all_notes.items():
            if not book_id or book_id == exclude or book_id in seen_ids or not notes:
                continue
            seen_ids.add(book_id)
            sources.append({
                "id": book_id,
                "title": book_id,
                "records": deepcopy(notes),
            })
        return sources

    def import_term_notes_from_book(
        self,
        source_book_id: str,
        selected_terms: list[str] | None = None,
        *,
        overwrite: bool = False,
    ) -> dict:
        summary = {
            "added": 0,
            "updated": 0,
            "skipped": 0,
            "limit_reached": False,
            "storage_limit_reached": False,
        }
        if not self.book or not source_book_id or source_book_id == self.book.id:
            return summary
        if self.store.read_only or getattr(self.store, "term_notes_read_only", False):
            self.set_status(self._term_notes_read_only_warning())
            return summary
        all_records = self.store.state.setdefault("term_notes", {})
        source_records = all_records.get(source_book_id) or []
        if not source_records:
            return summary
        allowed_keys = (
            {normalize_term_text(term).casefold() for term in selected_terms if normalize_term_text(term)}
            if selected_terms is not None
            else None
        )
        target_records = all_records.get(self.book.id, [])
        working_records = deepcopy(target_records)
        existing_by_key = {
            item.get("term", "").casefold(): item
            for item in working_records
            if isinstance(item, dict) and item.get("term")
        }
        new_entries: list[dict] = []
        timestamp = datetime.now(timezone.utc).isoformat()
        for src in source_records:
            if not isinstance(src, dict):
                continue
            term = normalize_term_text(src.get("term", ""))
            note = normalize_term_note(src.get("note", ""))
            if not term or not note:
                continue
            key = term.casefold()
            if allowed_keys is not None and key not in allowed_keys:
                continue
            existing = existing_by_key.get(key)
            if existing is not None:
                if overwrite:
                    existing["note"] = note
                    summary["updated"] += 1
                else:
                    summary["skipped"] += 1
                continue
            if len(working_records) + len(new_entries) >= MAX_TERM_NOTES:
                summary["limit_reached"] = True
                continue
            copied = asdict(TermNote(term=term, note=note, created=timestamp))
            new_entries.append(copied)
            existing_by_key[key] = copied
            summary["added"] += 1

        accepted_entries = new_entries
        if accepted_entries or summary["updated"]:
            candidate_records = dict(all_records)
            candidate_records[self.book.id] = [*accepted_entries, *working_records]
            if not self.store.term_notes_fit_storage_limit(candidate_records):
                summary["storage_limit_reached"] = True
                updated_records = dict(all_records)
                updated_records[self.book.id] = working_records
                if not self.store.term_notes_fit_storage_limit(updated_records):
                    working_records = deepcopy(target_records)
                    accepted_entries = []
                    summary["updated"] = 0
                else:
                    low, high = 0, len(accepted_entries)
                    while low < high:
                        midpoint = (low + high + 1) // 2
                        partial_records = dict(all_records)
                        partial_records[self.book.id] = [
                            *accepted_entries[:midpoint], *working_records,
                        ]
                        if self.store.term_notes_fit_storage_limit(partial_records):
                            low = midpoint
                        else:
                            high = midpoint - 1
                    accepted_entries = accepted_entries[:low]
            summary["added"] = len(accepted_entries)

        if accepted_entries or summary["updated"]:
            all_records[self.book.id] = [*accepted_entries, *working_records]
            self._refresh_term_notes()
            self.schedule_save()
        elif not target_records:
            all_records.pop(self.book.id, None)

        added = summary["added"]
        updated = summary["updated"]
        skipped = summary["skipped"]
        if summary["storage_limit_reached"]:
            if added and updated:
                self.set_status(
                    "已继承 {added} 条并覆盖 {updated} 条词条标注（达到存储上限 {limit} MiB，剩余词条未新增）。",
                    added=added, updated=updated, limit=MAX_TERM_NOTES_BYTES_MIB,
                    notify_in_focus=True,
                )
            elif added:
                self.set_status(
                    "已继承 {added} 条词条标注（达到存储上限 {limit} MiB，剩余词条未新增）。",
                    added=added, limit=MAX_TERM_NOTES_BYTES_MIB,
                    notify_in_focus=True,
                )
            elif updated:
                self.set_status(
                    "已更新 {updated} 条同名词条标注；达到存储上限 {limit} MiB，未新增词条。",
                    updated=updated, limit=MAX_TERM_NOTES_BYTES_MIB,
                    notify_in_focus=True,
                )
            else:
                self.set_status(
                    "本次继承因存储容量上限（{limit} MiB）未能写入新词条或修改。",
                    limit=MAX_TERM_NOTES_BYTES_MIB,
                    notify_in_focus=True,
                )
        elif summary["limit_reached"]:
            if added and updated:
                self.set_status(
                    "已继承 {added} 条并覆盖 {updated} 条词条标注（已达单书 {limit} 条上限）。",
                    added=added, updated=updated, limit=MAX_TERM_NOTES,
                    notify_in_focus=True,
                )
            elif added:
                self.set_status(
                    "已继承 {added} 条词条标注（已达单书 {limit} 条上限）。",
                    added=added, limit=MAX_TERM_NOTES,
                    notify_in_focus=True,
                )
            elif updated:
                self.set_status(
                    "已更新 {updated} 条同名词条标注；本书已达单书 {limit} 条上限，未新增词条。",
                    updated=updated, limit=MAX_TERM_NOTES,
                    notify_in_focus=True,
                )
            else:
                self.set_status(
                    "本书词条已达上限（{limit} 条），未能新增词条。",
                    limit=MAX_TERM_NOTES,
                    notify_in_focus=True,
                )
        elif added and updated:
            self.set_status("已继承 {added} 条词条标注，覆盖 {updated} 条同名词条。", added=added, updated=updated)
        elif added and skipped:
            self.set_status("已继承 {added} 条词条标注（跳过 {skipped} 条同名）。", added=added, skipped=skipped)
        elif added:
            self.set_status("已继承 {added} 条词条标注。", added=added)
        elif updated:
            self.set_status("已更新 {updated} 条同名词条标注。", updated=updated)
        elif skipped:
            self.set_status("所选词条已全部存在，未作修改。")
        return summary

    def prompt_import_term_notes(self):
        if not self.book or self.moyu_mode:
            return False
        if self.store.read_only or getattr(self.store, "term_notes_read_only", False):
            self.set_status(self._term_notes_read_only_warning())
            return False
        self._hide_term_hover()
        sources = self.books_with_term_notes(exclude_book_id=self.book.id)
        if not sources:
            self.set_status("书架中暂无其他包含词条标注的书籍。")
            return False
        existing_terms = {item.get("term", "").casefold() for item in self.get_term_notes()}
        dialog = ImportTermNotesDialog(
            sources=sources,
            existing_terms=existing_terms,
            theme=self.settings.theme,
            language=self.settings.language,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        source_id, selected_terms, overwrite = dialog.selection()
        if not source_id or not selected_terms:
            return False
        result = self.import_term_notes_from_book(source_id, selected_terms, overwrite=overwrite)
        return bool(result["added"] or result["updated"])

    def open_term_note_dialog(self, term: str = ""):
        if not self.book or self.moyu_mode:
            return False
        if self.store.read_only or getattr(self.store, "term_notes_read_only", False):
            self.set_status(self._term_notes_read_only_warning())
            return False
        self._hide_term_hover()
        clean_term = normalize_term_text(term)
        records = self.store.state.get("term_notes", {}).get(self.book.id, [])
        existing = next(
            (item for item in records if clean_term and item.get("term", "").casefold() == clean_term.casefold()),
            None,
        )
        dialog = TermNoteDialog(
            theme=self.settings.theme,
            language=self.settings.language,
            term=existing["term"] if existing else clean_term,
            note=existing["note"] if existing else "",
            editing=existing is not None,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        if dialog.delete_requested and existing is not None:
            return self.remove_term_note(existing["term"])
        new_term, new_note = dialog.values()
        ok = self.upsert_term_note(
            existing["term"] if existing is not None else new_term,
            new_note,
            original_term=existing["term"] if existing is not None else None,
        )
        if ok and not self.focus_mode and not self.moyu_mode:
            self.reading_tabs.setCurrentIndex(self._tab_terms)
        return ok

    def prompt_add_term_note(self):
        if not self.book:
            return
        selected = ""
        if self.reader.selection.hasSelection():
            raw = self.reader.selection.selectedText().replace("\u2029", "\n")
            collapsed = " ".join(raw.replace("\x00", "").split())
            if 1 <= len(collapsed) <= MAX_TERM_TEXT:
                selected = collapsed
        self.open_term_note_dialog(selected)

    def prompt_edit_selected_term_note(self):
        if not self.book:
            return
        item = self.term_notes_list.currentItem()
        if item is None or item.isHidden():
            return
        term = item.data(Qt.ItemDataRole.UserRole) or ""
        if term:
            self.open_term_note_dialog(term)

    def remove_selected_term_note(self):
        if not self.book:
            return
        item = self.term_notes_list.currentItem()
        if item is None or item.isHidden():
            return
        term = item.data(Qt.ItemDataRole.UserRole) or ""
        if term:
            self.remove_term_note(term)

    def _term_note_double_clicked(self, item):
        if not self.book or item is None:
            return
        term = item.data(Qt.ItemDataRole.UserRole) or ""
        if term:
            self.search_in_book(term)

    def _show_term_note_list_menu(self, pos: QPoint):
        if not self.book:
            return
        item = self.term_notes_list.itemAt(pos)
        if item is None:
            return
        self.term_notes_list.setCurrentItem(item)
        term = item.data(Qt.ItemDataRole.UserRole) or ""
        if not term:
            return
        menu = QMenu(self)
        menu.addAction(self.t("编辑词条标注：“{term}”", term=term), lambda t=term: self.open_term_note_dialog(t))
        if not self.focus_mode:
            menu.addAction(self.t("在全书搜索：“{term}”", term=term), lambda t=term: self.search_in_book(t))
        menu.addSeparator()
        menu.addAction(self.t("删除"), lambda t=term: self.remove_term_note(t))
        menu.exec(self.term_notes_list.viewport().mapToGlobal(pos))

    def _refresh_recent(self):
        self._refresh_shelf()

    def _update_sidebar_book_card(self, progress_pct: str = ""):
        if not hasattr(self, "sidebar_book_title"):
            return
        scale = self.sidebar_cover.devicePixelRatioF()
        if self.book is None:
            title = self.t("一本书，一处安静")
            meta = self.t("本地阅读 · 无需联网")
            cover = render_cover_pixmap(title, width=36, height=48, radius=5.0, scale=scale, mini=True)
        else:
            title = self.book_title.text().strip() or self.book.title
            fmt = {"EPUB": "EPUB", "PDF": "PDF"}.get(self.book.encoding, "TXT")
            parts = [part for part in (self.book.author.strip(), fmt, progress_pct) if part]
            meta = " · ".join(parts)
            cover = render_cover_pixmap(
                title, self.book.author, fmt, getattr(self.book, "cover", b""),
                width=36, height=48, radius=5.0, scale=scale, mini=True,
            )
        self.sidebar_cover.setPixmap(cover)
        title_metrics = QFontMetrics(self.sidebar_book_title.font())
        meta_metrics = QFontMetrics(self.sidebar_book_meta.font())
        avail = max(40, self.sidebar.width() - 104)
        self.sidebar_book_title.setText(title_metrics.elidedText(title, Qt.TextElideMode.ElideRight, avail))
        self.sidebar_book_title.setToolTip(title)
        self.sidebar_book_meta.setText(meta_metrics.elidedText(meta, Qt.TextElideMode.ElideRight, avail))
        self.sidebar_book_meta.setToolTip(meta)

    def _update_header_crumb(self):
        if not hasattr(self, "header_crumb"):
            return
        reading_active = hasattr(self, "main_pages") and self.main_pages.currentWidget() is self.reader_page
        show_crumb = reading_active and self.book is not None and not self.focus_mode
        if not show_crumb:
            self.header_crumb.setText("")
            self.header_crumb.setToolTip("")
            return
        title = self.book_title.text().strip()
        chapter = self.chapter_title.text().strip()
        if self.width() >= CRUMB_MIN_WIDTH:
            full = f"{title} · {chapter}" if chapter and chapter != title else title
        else:
            full = title
        available = self.header_crumb.width() - 10
        if available < 36:
            self.header_crumb.setText("")
            self.header_crumb.setToolTip(full)
            return
        metrics = QFontMetrics(self.header_crumb.font())
        self.header_crumb.setText(metrics.elidedText(full, Qt.TextElideMode.ElideRight, available))
        self.header_crumb.setToolTip(full)

    def _sync_shelf_actions_placement(self, shelf_active, is_narrow):
        if not hasattr(self, "shelf_actions") or not hasattr(self, "shelf_narrow_bar"):
            return
        # With category actions moved into the left shelf rail, shelf_actions only
        # holds "+ Add books". It fits inside the single-row 44px topbar down to
        # 380px in Chinese, and overflows cleanly below 405px in English.
        min_single_row = 405 if resolve_language(self.settings.language) == "en" else 360
        want_narrow = shelf_active and (not self.focus_mode) and self.width() < min_single_row
        if want_narrow and not self._shelf_actions_in_narrow:
            self.topbar_actions_layout.removeWidget(self.shelf_actions)
            self.shelf_narrow_layout.addWidget(self.shelf_actions)
            self.shelf_narrow_layout.addStretch(1)
            self._shelf_actions_in_narrow = True
        elif not want_narrow and self._shelf_actions_in_narrow:
            while self.shelf_narrow_layout.count():
                self.shelf_narrow_layout.takeAt(0)
            self.topbar_actions_layout.insertWidget(0, self.shelf_actions)
            self._shelf_actions_in_narrow = False
        self.shelf_narrow_bar.setVisible(want_narrow)
        self.shelf_actions.setVisible(shelf_active and not self.focus_mode)

    def _sync_page_navigation(self):
        if not hasattr(self, "main_pages"):
            return
        current = self.main_pages.currentWidget()
        reading_active = current is self.reader_page
        shelf_active = current is self.shelf
        settings_active = hasattr(self, "settings_page") and current is self.settings_page
        is_narrow = self.width() < NARROW_BREAKPOINT
        if is_narrow != self._was_narrow:
            self._was_narrow = is_narrow
            if is_narrow:
                self.sidebar_collapsed = not (
                    self._search_active and self.reading_tabs.currentIndex() == self.search_tab_index
                )
            else:
                self.sidebar_collapsed = self._user_sidebar_collapsed and not (
                    self._search_active and self.reading_tabs.currentIndex() == self.search_tab_index
                )
            self._sync_sidebar_toggle_visual()

        if hasattr(self, "settings_back_button"):
            back_source = "返回书架" if self._pre_settings_page is self.shelf else "返回阅读"
            self.settings_back_button.setText(self.t(back_source))
            self.settings_back_button.setVisible(settings_active and not self.focus_mode)
        if hasattr(self, "settings_top_sep"):
            self.settings_top_sep.setVisible(settings_active and not self.focus_mode)
        if hasattr(self, "settings_top_title"):
            self.settings_top_title.setVisible(settings_active and not self.focus_mode)
        if hasattr(self, "config_button"):
            self.config_button.setChecked(settings_active)

        if hasattr(self, "page_navigation"):
            self.page_navigation.setVisible(not settings_active and not self.focus_mode)
            if not settings_active:
                self.page_navigation.set_current("reading" if reading_active else "shelf", emit=False)
        if hasattr(self, "brand"):
            self.brand.setVisible(not settings_active and not self.focus_mode and self.width() >= COMPACT_BRAND_BREAKPOINT)
        if hasattr(self, "brand_mark"):
            self.brand_mark.setVisible(not settings_active and not self.focus_mode and self.width() >= COMPACT_BRAND_BREAKPOINT)
        self._sync_shelf_actions_placement(shelf_active, is_narrow)
        if hasattr(self, "layout_button"):
            self.layout_button.setVisible(reading_active and not self.focus_mode)
        if hasattr(self, "pin_button"):
            self.pin_button.setVisible(reading_active and not self.focus_mode)
        if hasattr(self, "focus_button"):
            self.focus_button.setVisible(reading_active and not self.focus_mode)
        if hasattr(self, "encoding_button"):
            self.encoding_button.setVisible(
                reading_active and not self.focus_mode and self.book is not None and self.book.encoding not in ("EPUB", "PDF")
            )
        if hasattr(self, "sidebar"):
            if reading_active and not self.focus_mode and not self.sidebar_collapsed:
                width = max(150, min(224, int(self.width() * 0.28)))
                if self.sidebar.width() != width:
                    self.sidebar.setFixedWidth(width)
                    self._elide_term_note_items()
                else:
                    self.sidebar.setFixedWidth(width)
            self.sidebar.setVisible(reading_active and not self.focus_mode and not self.sidebar_collapsed)
        if hasattr(self, "sidebar_toggle"):
            self.sidebar_toggle.setVisible(reading_active and not self.focus_mode)
        self._sync_escape_shortcut()
        is_ultra_narrow = self.width() < 430
        if hasattr(self, "topbar_layout"):
            self.topbar_layout.setSpacing(2 if is_ultra_narrow else 5)
        if hasattr(self, "topbar_actions_layout"):
            self.topbar_actions_layout.setSpacing(1 if is_ultra_narrow else 3)
        self._update_header_crumb()

    def _sync_sidebar_toggle_visual(self):
        if not hasattr(self, "sidebar_toggle"):
            return
        self.sidebar_toggle.set_icon_name("sidebar_collapsed" if self.sidebar_collapsed else "sidebar")
        label = "展开目录栏" if self.sidebar_collapsed else "收起目录栏"
        self.sidebar_toggle.setToolTip(self.t(label))
        self.sidebar_toggle.setAccessibleName(self.t(label))

    def _update_sidebar_toggle(self):
        self._sync_sidebar_toggle_visual()
        self._sync_page_navigation()

    def toggle_sidebar(self, checked=False):
        if self.focus_mode or self.main_pages.currentWidget() is not self.reader_page:
            return
        self.sidebar_collapsed = not self.sidebar_collapsed
        if self.width() >= NARROW_BREAKPOINT:
            self._user_sidebar_collapsed = self.sidebar_collapsed
        self._update_sidebar_toggle()

    def show_reading_page(self, checked=False):
        if self.focus_mode:
            return
        if self._reader_settings_dirty:
            self.reader.apply_settings(self.settings)
            self._reader_settings_dirty = False
        self.main_pages.setCurrentWidget(self.reader_page)
        self._sync_page_navigation()
        self._update_jump_navigation()

    def show_shelf_page(self, checked=False):
        if self.focus_mode:
            return
        self._hide_term_hover()
        if hasattr(self, "appearance_popover"):
            self.appearance_popover.dismiss()
        if hasattr(self, "focus_popover"):
            self.focus_popover.dismiss()
        self._refresh_shelf()
        self.main_pages.setCurrentWidget(self.shelf)
        self._sync_page_navigation()

    def show_shelf_category(self, category):
        if self.focus_mode:
            return
        self.show_shelf_page()
        self.shelf.select_category(category)

    def _main_page_changed(self, *_):
        if hasattr(self, "toast") and not self.toast.isHidden() and not self._window_hidden:
            self.toast.reposition(self._toast_bottom_margin())

    def toggle_maximize(self):
        if self.focus_mode:
            return
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self._update_window_controls()

    def _update_window_controls(self):
        if not hasattr(self, "maximize_button"):
            return
        restore = self.isMaximized()
        label = "还原窗口" if restore else "最大化窗口"
        self.maximize_button.set_icon_name("restore" if restore else "maximize")
        self.maximize_button.setToolTip(self.t(label))
        self.maximize_button.setAccessibleName(self.t(label))

    def toggle_main_page(self):
        """Compatibility helper for existing shortcuts and automation scripts."""
        if self.main_pages.currentWidget() is self.reader_page:
            self.show_shelf_page()
        else:
            self.show_reading_page()

    def toggle_encoding(self):
        self.encoding_bar.setVisible(not self.encoding_bar.isVisible())

    def toggle_pin(self, checked):
        values = self.settings.to_dict()
        values["always_on_top"] = checked
        self.apply_settings(Settings.from_dict(values))

    @property
    def language(self):
        return self.settings.language

    def _effective_focus_background(self) -> str:
        if self.moyu_mode and self._sampled_focus_color:
            return self._sampled_focus_color
        if self.settings.focus_auto_background and self._sampled_focus_color:
            return self._sampled_focus_color
        return self.settings.background_color

    def _effective_focus_opacity(self) -> float:
        if self._focus_opacity_override is not None:
            return self._focus_opacity_override
        opacity = self.settings.opacity
        if (self.moyu_mode or self.settings.focus_auto_background) and self._sampled_focus_complex:
            return min(opacity, self.settings.focus_complex_opacity)
        return opacity

    def _on_focus_eyedropper_selected(self, color):
        if color.isValid():
            self.apply_settings(
                replace(self.settings, background_color=color.name().lower(), focus_auto_background=False)
            )

    def resample_focus_background(self, *, notify=False, force_enable=False) -> bool:
        """Sample the dominant desktop color strictly outside the window without hiding it."""
        if self.closed or self._window_hidden:
            return False
        sample = sample_window_perimeter(self.geometry(), capture=self._perimeter_capture)
        if not sample.is_valid():
            if notify:
                self.set_status("无法采样窗口周边颜色，将保持当前配色。")
            return False
        hex_color = sample.color.name().lower()
        changed = hex_color != self._sampled_focus_color
        self._sampled_focus_color = hex_color
        self._sampled_focus_complex = sample.is_complex
        if force_enable and not self.settings.focus_auto_background:
            self.apply_settings(replace(self.settings, focus_auto_background=True))
        else:
            self._apply_window_style()
            if self.focus_mode and not self.moyu_mode and changed:
                self.reader.reflow()
        if notify:
            if sample.is_complex:
                cap_pct = round(min(self.settings.opacity, self.settings.focus_complex_opacity) * 100)
                self.set_status(
                    "已自动采样周边底色：{color}（复杂背景，不透明度上限 {opacity}%）",
                    color=hex_color.upper(), opacity=cap_pct,
                )
            else:
                self.set_status("已自动采样周边底色：{color}", color=hex_color.upper())
        return True

    def manual_resample_focus_background(self):
        if not self.focus_mode and self.main_pages.currentWidget() is not self.reader_page:
            return
        self.resample_focus_background(notify=not self.focus_mode, force_enable=True)

    def _schedule_auto_focus_resample(self, delay_ms: int = 140):
        if (
            not self.focus_mode
            or (
                not self.moyu_mode
                and (
                    not self.settings.focus_auto_background
                    or self.settings.focus_sample_trigger != "auto"
                )
            )
            or self.closed
            or self._window_hidden
        ):
            return
        self._focus_resample_timer.start(delay_ms)

    def _run_scheduled_focus_resample(self):
        if (
            not self.focus_mode
            or (
                not self.moyu_mode
                and (
                    not self.settings.focus_auto_background
                    or self.settings.focus_sample_trigger != "auto"
                )
            )
            or self.closed
            or self._window_hidden
        ):
            return
        if self._focus_adjust_active:
            self._focus_resample_pending = True
            return
        self._focus_resample_pending = False
        self.resample_focus_background(notify=False, force_enable=False)

    def show_focus_panel(self):
        if self.focus_mode:
            self.toggle_focus()
            return
        self._hide_term_hover()
        if hasattr(self, "appearance_popover"):
            self.appearance_popover.dismiss()
        if hasattr(self, "focus_popover"):
            if not self.focus_popover.isHidden():
                self.focus_popover.dismiss()
                return
            self.focus_popover.sync_settings(
                self.settings, colors(self.settings.theme),
                sampled_color=self._sampled_focus_color,
                has_book=self.book is not None,
                sampled_complex=self._sampled_focus_complex,
            )
            self.focus_button.setChecked(True)
            self.focus_popover.open_for(self.focus_button, align="right")

    def toggle_focus(self, checked=None):
        if self.moyu_mode:
            self.exit_moyu_mode()
            if self.focus_mode:
                self.toggle_focus()
            return
        if not self.book:
            return
        self._hide_term_hover()
        entering = not self.focus_mode
        if entering:
            self.show_reading_page()
            if hasattr(self, "appearance_popover"):
                self.appearance_popover.dismiss()
            if hasattr(self, "focus_popover"):
                self.focus_popover.dismiss()
            self._pre_focus_maximized = self.isMaximized()
            self._chrome_visibility = {widget: not widget.isHidden() for widget in self.chrome_widgets}
        self._focus_resample_timer.stop()
        self._focus_resample_pending = False
        self._focus_geometry_dirty = False
        self._set_focus_adjust_active(False)
        self._focus_adjust_mouse_drag_active = False
        self._drag_has_valid = False
        self._set_drag_hover_active(False)
        self._focus_opacity_override = None
        if entering and self.settings.focus_auto_background and self.settings.focus_sample_trigger == "auto":
            sample = sample_window_perimeter(self.geometry(), capture=self._perimeter_capture)
            if sample.is_valid():
                self._sampled_focus_color = sample.color.name().lower()
                self._sampled_focus_complex = sample.is_complex
        self.focus_mode = entering
        self.setAcceptDrops(not entering)
        self.reader.focus_mode = entering
        self.setMinimumSize(180 if entering else 380, 100 if entering else 360)
        self.reader.setMinimumSize(80 if entering else 160, 40 if entering else 140)
        if not entering:
            for widget, visible in self._chrome_visibility.items():
                widget.setVisible(visible)
            if hasattr(self, "search_bar"):
                self.search_bar.setVisible(bool(self._search_active and self.book))
        self.apply_settings(self.settings)
        if not entering:
            self._sync_reader_search_highlights()
            self._update_jump_navigation()
        self._focus_resample_timer.stop()
        self._focus_geometry_dirty = False
        if not entering and self._pre_focus_maximized:
            self.showMaximized()
        if not self._moyu_preserve_external_focus:
            self.raise_()
            self.activateWindow()
            self.reader.setFocus()

    def _refresh_moyu_sentence(self, offset=None):
        self._moyu_fragment_history = []
        if not self.book:
            self._moyu_sentences = []
            self._moyu_sentence_index = -1
            self._moyu_offset = 0
            self.moyu_strip.set_sentence("")
            return
        section = self.book.sections[self.section_index]
        self._moyu_sentences = sentence_spans(section.text)
        requested_offset = self.reader.offset if offset is None else max(0, int(offset))
        self._show_moyu_fragment(requested_offset)

    def _show_moyu_fragment(self, offset):
        if not self.book or not 0 <= self.section_index < len(self.book.sections):
            return False
        section = self.book.sections[self.section_index]
        requested_offset = max(0, min(len(section.text), int(offset)))
        sentence_index = sentence_index_for_offset(self._moyu_sentences, requested_offset)
        self._moyu_sentence_index = sentence_index
        if self._moyu_sentence_index < 0:
            self._moyu_offset = 0
            self.reader.offset = 0
            self.moyu_strip.set_sentence("")
            return False
        start, end = self._moyu_sentences[self._moyu_sentence_index]
        fragment_offset = requested_offset if start <= requested_offset < end else start
        self._moyu_offset = fragment_offset
        self.reader.offset = fragment_offset
        self.moyu_strip.set_sentence(
            section.text[fragment_offset:end], source_offset=fragment_offset,
        )
        return True

    def _push_moyu_fragment_history(self):
        self._moyu_fragment_history.append((self.section_index, self._moyu_offset))
        if len(self._moyu_fragment_history) > 512:
            del self._moyu_fragment_history[:-512]

    def _position_moyu_bar(self, *, default=False):
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        current = self.geometry()
        if default:
            bounds = self._constrain_moyu_geometry(self.store.state.get("moyu_window"), area)
            if bounds is None:
                width = max(180, min(500, area.width() - 16))
                height = self.moyu_strip.preferred_height
                bounds = self._constrain_moyu_geometry(
                    (area.x() + 8, area.bottom() - height + 1, width, height), area,
                )
        else:
            height = max(current.height(), self.moyu_strip.minimumHeight())
            bottom = current.y() + current.height()
            bounds = self._constrain_moyu_geometry(
                (current.x(), bottom - height, current.width(), height), area,
            )
        if bounds is None:
            return
        self.setGeometry(bounds)
        if self.moyu_mode:
            self._moyu_bar_geometry = QRect(self.geometry())

    def _constrain_moyu_geometry(self, bounds, area):
        if (
            not isinstance(bounds, (list, tuple)) or len(bounds) != 4
            or any(type(value) is not int for value in bounds)
        ):
            return None
        x, y, width, height = bounds
        if width <= 0 or height <= 0:
            return None
        width = max(180, min(width, max(180, area.width() - 16)))
        minimum_height = self.moyu_strip.minimumHeight()
        maximum_height = max(minimum_height, min(self.moyu_strip.MAXIMUM_HEIGHT, area.height()))
        height = max(minimum_height, min(height, maximum_height))
        max_x = max(area.x(), area.right() - width + 1)
        max_y = max(area.y(), area.bottom() - height + 1)
        x = max(area.x(), min(x, max_x))
        y = max(area.y(), min(y, max_y))
        return QRect(x, y, width, height)

    def _moyu_pointer_is_near(self):
        return self.geometry().adjusted(-28, -34, 28, 34).contains(QCursor.pos())

    def _sync_moyu_visibility(self, initial=False):
        if not self.moyu_mode:
            self._moyu_visibility_timer.stop()
            self._moyu_hide_timer.stop()
            return
        adjust_key_pressed = self.moyu_strip.adjustment_key_is_pressed()
        self._set_focus_adjust_active(adjust_key_pressed)
        if self.moyu_strip.interaction_active and not adjust_key_pressed:
            self.moyu_strip.cancel_interaction()
        if self.moyu_strip.interaction_active:
            self._moyu_hide_timer.stop()
            return
        if self._moyu_menu_open:
            self._moyu_hide_timer.stop()
            return
        if self._moyu_pointer_is_near():
            self._moyu_hide_timer.stop()
            self._moyu_auto_hide_armed = True
            if self._moyu_auto_hidden:
                self._moyu_auto_hidden = False
                self.show()
                self._configure_hotkeys()
                self._schedule_auto_focus_resample(140)
            return
        if initial or not self._moyu_auto_hide_armed:
            self._moyu_hide_timer.stop()
            return
        if not self._moyu_auto_hidden and not self._moyu_hide_timer.isActive():
            self._moyu_hide_timer.start()

    def _on_moyu_menu_opened(self):
        self._moyu_menu_open = True
        self._hide_term_hover()
        self._moyu_hide_timer.stop()

    def _on_moyu_menu_closed(self):
        self._moyu_menu_open = False
        self._sync_moyu_visibility()

    def _hide_moyu_if_away(self):
        if self.moyu_mode and not self.moyu_strip.interaction_active and not self._moyu_pointer_is_near():
            self._hide_term_hover()
            self._moyu_auto_hidden = True
            self.hide()

    def toggle_moyu_mode(self, checked=None):
        if self.moyu_mode:
            self.exit_moyu_mode()
            return
        if not self.book or self.loading or self.closed:
            return
        self._hide_term_hover()
        if self.isHidden() or self.isMinimized():
            self.reveal()

        self._moyu_return_to_focus = self.focus_mode
        was_maximized = self.isMaximized()
        previous_geometry = self.normalGeometry() if was_maximized else self.geometry()
        if not self.focus_mode:
            self.toggle_focus()
        self._moyu_was_maximized = was_maximized
        self._moyu_geometry = QRect(previous_geometry)
        if self.isMaximized():
            self.showNormal()

        self._hide_term_hover()
        self._set_focus_adjust_active(False)
        self._focus_resample_timer.stop()
        self._focus_geometry_dirty = False
        self.moyu_mode = True
        self._moyu_auto_hidden = False
        self._moyu_auto_hide_armed = False
        self._sampled_focus_color = ""
        self._sampled_focus_complex = False
        self._moyu_menu_open = False
        self.reader.hide()
        self.moyu_strip.show()
        self._moyu_previous_maximum_size = QSize(self.maximumSize())
        self.setMinimumSize(180, self.moyu_strip.minimumHeight())
        self.setMaximumHeight(self.moyu_strip.MAXIMUM_HEIGHT)
        self.reader.setMinimumSize(0, 0)
        self._refresh_moyu_sentence(self.reader.offset)
        self.apply_settings(self.settings)
        self._position_moyu_bar(default=True)
        self.resample_focus_background(notify=False)
        self._update_progress()
        self._moyu_visibility_timer.start()
        self._sync_moyu_visibility(initial=True)

    def exit_moyu_mode(self):
        if not self.moyu_mode:
            return
        self._hide_term_hover()
        was_auto_hidden = self._moyu_auto_hidden
        return_to_focus = self._moyu_return_to_focus
        previous_geometry = QRect(self._moyu_geometry) if self._moyu_geometry is not None else None
        was_maximized = self._moyu_was_maximized
        self.save_state()
        self._set_focus_adjust_active(False)
        self.moyu_strip.cancel_interaction()
        self._moyu_visibility_timer.stop()
        self._moyu_hide_timer.stop()
        self._moyu_auto_hidden = False
        self._moyu_auto_hide_armed = False
        self._moyu_preserve_external_focus = True
        self.moyu_mode = False
        self.moyu_strip.hide()
        self.reader.show()
        if self._moyu_previous_maximum_size is not None:
            self.setMaximumSize(self._moyu_previous_maximum_size)
            self._moyu_previous_maximum_size = None
        self.setMinimumSize(180 if self.focus_mode else 380, 100 if self.focus_mode else 360)
        self.reader.setMinimumSize(80 if self.focus_mode else 160, 40 if self.focus_mode else 140)
        if self.isMaximized():
            self.showNormal()
        self.apply_settings(self.settings)
        if previous_geometry is not None:
            self.setGeometry(previous_geometry)
        if was_maximized:
            self.showMaximized()
        self.reader.offset = self._moyu_offset
        self.reader.reflow()
        if not return_to_focus and self.focus_mode:
            self.toggle_focus()
        if was_auto_hidden:
            self.show()
        self._moyu_preserve_external_focus = False
        self._moyu_geometry = None
        self._moyu_bar_geometry = None
        self._moyu_sentences = []
        self._moyu_sentence_index = -1
        self._moyu_fragment_history = []
        self._update_progress()

    def turn_moyu_sentence(self, direction):
        if not self.moyu_mode or not self.book or direction not in (-1, 1):
            return
        self._hide_term_hover()

        if direction > 0:
            overflow_offset = self.moyu_strip.overflow_source_offset
            if 0 <= self._moyu_sentence_index < len(self._moyu_sentences):
                _sentence_start, sentence_end = self._moyu_sentences[self._moyu_sentence_index]
            else:
                sentence_end = self._moyu_offset
            if overflow_offset is not None and self._moyu_offset < overflow_offset < sentence_end:
                self._push_moyu_fragment_history()
                self._show_moyu_fragment(overflow_offset)
                self._update_progress()
                self.schedule_save()
                return
        elif self._moyu_fragment_history:
            previous_section, previous_offset = self._moyu_fragment_history.pop()
            remaining_history = self._moyu_fragment_history.copy()
            if previous_section != self.section_index:
                self.open_section(previous_section, previous_offset)
                self._moyu_fragment_history = remaining_history
            else:
                self._show_moyu_fragment(previous_offset)
            self._update_progress()
            self.schedule_save()
            return

        target = self._moyu_sentence_index + direction
        if 0 <= target < len(self._moyu_sentences):
            if direction > 0:
                self._push_moyu_fragment_history()
            self._moyu_sentence_index = target
            start, end = self._moyu_sentences[target]
            self._show_moyu_fragment(start)
            self._update_progress()
            self.schedule_save()
            return

        candidate = self.section_index + direction
        previous_history = self._moyu_fragment_history.copy()
        if direction > 0:
            previous_history.append((self.section_index, self._moyu_offset))
            if len(previous_history) > 512:
                del previous_history[:-512]
        while 0 <= candidate < len(self.book.sections):
            section = self.book.sections[candidate]
            offset = 0 if direction > 0 else len(section.text)
            self.open_section(candidate, offset)
            if self._moyu_sentences:
                self._moyu_fragment_history = previous_history
                break
            candidate += direction

    def toggle_moyu_lines(self):
        if not self.moyu_mode:
            return
        self._hide_term_hover()
        values = self.settings.to_dict()
        values["moyu_lines"] = 1 if self.settings.moyu_lines == 2 else 2
        self.apply_settings(Settings.from_dict(values))

    def toggle_focus_opacity(self):
        if not self.focus_mode:
            return
        current = self._effective_focus_opacity()
        self._focus_opacity_override = 0.0 if current > 0 else 1.0
        self._apply_window_style()

    def t(self, source, **values):
        if values.get("category") == UNCLASSIFIED:
            values["category"] = tr("未分类", self.settings.language)
        return tr(source, self.settings.language, **values)

    def _toast_status_text(self, source, values, action_text=None):
        values = dict(values)
        if source == "已将《{title}》移动到“{category}”。":
            title = values.get("title")
            if isinstance(title, str) and title:
                context = self.t(source, title="", category=values.get("category", ""))
                action_label = self.t(action_text) if action_text else None
                available_width = self.toast.available_text_width(action_label)
                metrics = QFontMetrics(self.toast.label.font())
                title_width = max(0, available_width - metrics.horizontalAdvance(context))
                values["title"] = metrics.elidedText(
                    title, Qt.TextElideMode.ElideRight, title_width,
                )
        return self.t(source, **values)

    def _toast_bottom_margin(self):
        if hasattr(self, "main_pages") and self.main_pages.currentWidget() is self.reader_page:
            anchor_widget = getattr(self, "progress", None)
            if anchor_widget is not None and not anchor_widget.isHidden():
                anchor_y = anchor_widget.mapTo(self.surface, QPoint(0, 0)).y()
                if anchor_y > 0:
                    return max(44, self.surface.height() - anchor_y + 12)
                return 96
        if hasattr(self, "status") and not self.status.isHidden():
            anchor_y = self.status.mapTo(self.surface, QPoint(0, 0)).y()
            if anchor_y > 0:
                return max(44, self.surface.height() - anchor_y + 14)
        return 44

    def set_status(
        self, source, notify=True, toast_action_text=None, toast_action=None,
        notify_in_focus=False, **values,
    ):
        self.status_source = (source, values)
        self.status.setProperty("translation_source", None)
        text = self.t(source, **values)
        self.status.setText(text)
        if (
            notify and hasattr(self, "toast")
            and (not self.focus_mode or notify_in_focus)
            and not self.closed and not self._window_hidden
        ):
            self._toast_status_source = (source, dict(values), toast_action_text, toast_action)
            self.toast.show_message(
                self._toast_status_text(source, values, toast_action_text),
                bottom_margin=self._toast_bottom_margin(),
                action_text=self.t(toast_action_text) if toast_action_text else None,
                action=toast_action,
            )

    def _set_shelf_error(self, error):
        message = str(error)
        dynamic = (
            ("无法创建分类：", "无法创建分类：{error}"),
            ("无法重命名分类：", "无法重命名分类：{error}"),
            ("复制书籍失败：", "复制书籍失败：{error}"),
            ("移动书籍失败：", "移动书籍失败：{error}"),
            ("删除书架书籍失败：", "删除书架书籍失败：{error}"),
            ("删除分类失败：", "删除分类失败：{error}"),
            ("移动分类书籍失败：", "移动分类书籍失败：{error}"),
        )
        for prefix, source in dynamic:
            if message.startswith(prefix):
                self.set_status(source, error=message[len(prefix):])
                return
        rollback_prefix = "分类移动失败，回滚也未完成："
        if message.startswith(rollback_prefix):
            details = message[len(rollback_prefix):]
            reason, separator, rollback = details.partition("; ")
            if separator:
                self.set_status("分类移动失败，回滚也未完成：{error}; {rollback}", error=reason, rollback=rollback)
                return
        self.set_status(message)

    def _retranslate(self):
        install_qt_translator(QApplication.instance(), self.settings.language)
        for widget in self.findChildren(QWidget) + self.findChildren(QAction):
            source = widget.property("translation_source")
            if source:
                widget.setText(self.t(source))
            if isinstance(widget, QWidget):
                for getter, setter, key in [("toolTip", "setToolTip", "translation_tip"), ("accessibleName", "setAccessibleName", "translation_accessible")]:
                    original = widget.property(key)
                    if original is None:
                        original = getattr(widget, getter)()
                        widget.setProperty(key, original)
                    if original:
                        getattr(widget, setter)(self.t(original))
        self.open_button.setText(self.t("添加书籍"))
        self.config_button.setToolTip(self.t("配置"))
        self.config_button.setAccessibleName(self.t("配置"))
        self.reading_button.setText(self.t("阅读"))
        self.shelf_button.setText(self.t("书架"))
        self._update_window_controls()
        for index, source in (
            (self._tab_toc, "目录"),
            (self._tab_bookmarks, "书签"),
            (self._tab_terms, "词条"),
            (self._tab_info, "资料"),
            (self._tab_pages, "页码"),
            (self._tab_landmarks, "位置"),
            (self.search_tab_index, "搜索"),
        ):
            self.reading_tabs.setTabText(index, self.t(source))
        if hasattr(self, "toc_filter"):
            self.toc_filter.setPlaceholderText(self.t("筛选目录…"))
        if hasattr(self, "term_notes_filter"):
            self.term_notes_filter.setPlaceholderText(self.t("筛选词条…"))
            self._refresh_term_notes()
        if hasattr(self, "search_input"):
            self.search_input.setPlaceholderText(self.t("搜索全书内容…"))
            self._set_search_results_state(self.search_input.text())
            if self._search_active and self.search_input.text() and not self._search_matches:
                self.search_count_label.setText(self.t("无匹配"))
        self.shelf.retranslate(self.settings.language)
        record = next((item for item in self.store.state["books"] if self.book and item["id"] == self.book.id), None)
        display_title = display_title_for_record(record) if record else (self.book.title if self.book else None)
        self.book_info.set_book(
            self.book, self.settings.language,
            display_title=display_title,
        )
        if self.book:
            self.book_title.setText(display_title)
            self.setWindowTitle(f"{display_title} · QuietReader")
        self._update_header_crumb()
        self._update_progress()
        self._update_jump_navigation()
        self.encoding.setItemText(0, self.t("自动识别"))
        focus_label = self.t("退出专注" if self.focus_mode else "专注")
        self.focus_button.setToolTip(focus_label)
        self.focus_button.setAccessibleName(focus_label)
        self._update_sidebar_toggle()
        if hasattr(self, "tray_focus_action"):
            focus_active = self.focus_mode and not self.moyu_mode
            self.tray_focus_action.setText(self.t("退出专注模式" if focus_active else "进入专注模式"))
            self.tray_focus_action.setChecked(focus_active)
            self.tray_focus_action.setEnabled(bool(self.book and not self.loading and not self.moyu_mode))
        if hasattr(self, "tray_moyu_action"):
            self.tray_moyu_action.setText(self.t("退出摸鱼模式" if self.moyu_mode else "进入摸鱼模式"))
            self.tray_moyu_action.setChecked(self.moyu_mode)
        if self.tray:
            self.tray.setToolTip("QuietReader")
        if not self.book:
            self.setWindowTitle("QuietReader")
        if hasattr(self, "appearance_popover"):
            self.appearance_popover.sync_settings(self.settings, colors(self.settings.theme))
        if hasattr(self, "focus_popover"):
            self.focus_popover.sync_settings(
                self.settings, colors(self.settings.theme),
                sampled_color=self._sampled_focus_color,
                has_book=self.book is not None,
                sampled_complex=self._sampled_focus_complex,
            )
        if hasattr(self, "settings_page"):
            self.settings_page.retranslate(self.settings.language)
        if hasattr(self, "settings_back_button"):
            back_source = "返回书架" if self._pre_settings_page is self.shelf else "返回阅读"
            self.settings_back_button.setText(self.t(back_source))
        self.set_status(self.status_source[0], notify=False, **self.status_source[1])
        if hasattr(self, "toast") and not self.toast.isHidden():
            toast_state = self._toast_status_source
            if toast_state is None:
                source, values = self.status_source
                action_text, action = None, None
            else:
                source, values, action_text, action = toast_state
            self.toast.update_text(
                self._toast_status_text(source, values, action_text),
                action_text=self.t(action_text) if action_text else None,
                action=action,
            )

    def _apply_window_style(self):
        opacity = self._effective_focus_opacity() if self.focus_mode or self.moyu_mode else 1.0
        if self.focus_mode or self.moyu_mode:
            if self._focus_adjust_active and opacity == 0 and not self.moyu_mode:
                # A fully transparent surface can miss mouse input. Make only
                # that case more reliably hit-testable while Alt is held.
                alpha = 255 * 0.01
            else:
                alpha = round(opacity * 255)
            if self.focus_mode and not self.moyu_mode:
                # Keep the full focus-window rectangle hit-testable for wheel
                # paging even when its background is visually transparent.
                alpha = max(1, alpha)
        else:
            alpha = 255
        effective_bg = self._effective_focus_background()
        background_color = effective_bg if self.focus_mode or self.moyu_mode else ""
        if hasattr(self, "reader"):
            self.reader.focus_background_override = effective_bg
        if hasattr(self, "moyu_strip"):
            self.moyu_strip.set_background_color(background_color if self.moyu_mode else "")
            self.moyu_strip.set_background_opacity(opacity if self.moyu_mode else 1.0)
            self.moyu_strip.set_adjust_outline(self.moyu_mode and self._focus_adjust_active)
        palette = colors(self.settings.theme, background_color)
        self.setStyleSheet(stylesheet(
            palette, 0 if self.moyu_mode else alpha,
            borderless=self.focus_mode or self.moyu_mode,
            resize_outline=self.focus_mode and not self.moyu_mode and self._focus_adjust_active,
            drop_highlight=self._drag_hover_active and not self.focus_mode,
        ))
        if hasattr(self, "moyu_term_hover_card"):
            moyu_card_alpha = round(opacity * 255) if self.moyu_mode else 255
            self.moyu_term_hover_card.setStyleSheet(stylesheet(palette, moyu_card_alpha, borderless=True))
        if hasattr(self, "brand_mark"):
            self.brand_mark.setPixmap(
                self.windowIcon().pixmap(QSize(22, 22), self.brand_mark.devicePixelRatioF())
            )
        if hasattr(self, "settings_back_button"):
            self.settings_back_button.setIcon(vector_icon("back", palette.get("ink_secondary", palette["muted"]), 13))
        if hasattr(self, "search_icon_label"):
            self.search_icon_label.setPixmap(render_icon_pixmap("search", palette.get("muted", "#647569"), 14))
        for control in self._themed_controls:
            control.apply_palette(palette)
        ui_palette = colors(self.settings.theme)
        if hasattr(self, "appearance_popover"):
            self.appearance_popover.sync_settings(self.settings, ui_palette)
        if hasattr(self, "focus_popover"):
            self.focus_popover.sync_settings(
                self.settings, ui_palette,
                sampled_color=self._sampled_focus_color,
                has_book=self.book is not None,
                sampled_complex=self._sampled_focus_complex,
            )
        if hasattr(self, "settings_page"):
            self.settings_page.sync_settings(self.settings, ui_palette)

    def check_for_updates_automatically(self):
        if (self.settings.auto_check_updates and
                automatic_check_due(self.store.state["last_update_check"], int(time()))):
            self.check_for_updates(automatic=True)

    def check_for_updates(self, *, automatic=False):
        if self.closed or not self.release_checker.check():
            return
        self._automatic_update_request = automatic
        self.store.state["last_update_check"] = int(time())
        self.schedule_save()
        self.settings_page.set_update_status("checking")

    def _update_check_finished(self, release):
        self.settings_page.set_update_status(release.status if release else "error", release)
        if (self._automatic_update_request and self.settings.auto_check_updates and
                release is not None and release.status == "available"):
            self.set_status(
                "发现新版本 {version}。",
                version=release.version,
                toast_action_text="查看更新",
                toast_action=lambda: QDesktopServices.openUrl(QUrl(release.url)),
            )

    def apply_settings(self, settings):
        previous_settings = self.settings
        previous_values = previous_settings.to_dict()
        previously_enabled = previous_settings.auto_check_updates
        prev_auto = previous_settings.focus_auto_background
        self.settings = Settings.from_dict(settings.to_dict())
        settings = self.settings
        updated_values = settings.to_dict()
        changed_fields = {
            key for key, value in updated_values.items()
            if previous_values.get(key) != value
        }
        # The settings page hides the reader; defer layout work until it is shown again.
        if (
            changed_fields
            and changed_fields.issubset(_SETTINGS_SLIDER_FIELDS)
            and hasattr(self, "settings_page")
            and self.main_pages.currentWidget() is self.settings_page
        ):
            if changed_fields & _READER_LAYOUT_SETTINGS_FIELDS:
                self._reader_settings_dirty = True
            self.schedule_save()
            return
        if settings.auto_check_updates and not previously_enabled:
            QTimer.singleShot(0, self.check_for_updates_automatically)
        if (
            not prev_auto
            and settings.focus_auto_background
            and settings.focus_sample_trigger == "auto"
            and not self._sampled_focus_color
            and self.isVisible()
            and not self._window_hidden
        ):
            sample = sample_window_perimeter(self.geometry(), capture=self._perimeter_capture)
            if sample.is_valid():
                self._sampled_focus_color = sample.color.name().lower()
                self._sampled_focus_complex = sample.is_complex
        was_visible = self.isVisible()
        bounds = self.geometry()
        if self.moyu_mode:
            flags = (
                Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.NoDropShadowWindowHint | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.WindowDoesNotAcceptFocus
            )
        else:
            flags = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint if self.focus_mode else Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
        if self.focus_mode and not self.moyu_mode:
            flags |= Qt.WindowType.NoDropShadowWindowHint
        if settings.always_on_top and not self.moyu_mode:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        mask = (
            Qt.WindowType.WindowType_Mask | Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.NoDropShadowWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, self.moyu_mode)
        if self.windowFlags() & mask != flags & mask:
            self.setWindowFlags(flags)
        self.setGeometry(bounds)
        self._apply_window_style()
        self.pin_button.setChecked(settings.always_on_top)
        self.reader.apply_settings(settings)
        self.moyu_strip.set_preferences(
            lines=settings.moyu_lines,
            theme=settings.theme,
            language=settings.language,
            font_family=settings.font_family,
            adjust_key=settings.focus_adjust_key,
            background_color=self._effective_focus_background() if self.moyu_mode else "",
        )
        if self.moyu_mode:
            self.setMinimumSize(180, self.moyu_strip.minimumHeight())
            self.setMaximumHeight(self.moyu_strip.MAXIMUM_HEIGHT)
        self._reader_settings_dirty = False
        self._sync_page_navigation()
        focus_popover_open = hasattr(self, "focus_popover") and not self.focus_popover.isHidden()
        self.focus_button.setChecked(self.focus_mode or focus_popover_open)
        self.focus_shortcut.setKey(QKeySequence(settings.focus_key))
        self.focus_opacity_shortcut.setKey(QKeySequence(settings.focus_opacity_key))
        self.focus_resample_shortcut.setKey(QKeySequence(settings.focus_resample_key))
        self.moyu_shortcut.setKey(QKeySequence(settings.moyu_key))
        self.moyu_lines_shortcut.setKey(QKeySequence(settings.moyu_lines_key))
        self.visibility_shortcut.setKey(QKeySequence(settings.hotkey))
        self.emergency_shortcut.setKey(QKeySequence(settings.emergency_key))
        self._sync_escape_shortcut()
        self.root_layout.setContentsMargins(*((0, 0, 0, 0) if self.focus_mode else (12, 6, 12, 6)))
        self.root_layout.setSpacing(0 if self.focus_mode else 8)
        self.body_layout.setSpacing(0 if self.focus_mode else 16)
        self.reading_layout.setSpacing(0 if self.focus_mode else 4)
        self._retranslate()
        if was_visible:
            self.show()
            if not self.moyu_mode and not self._moyu_preserve_external_focus:
                self.activateWindow()
            on_reader = self.main_pages.currentWidget() is self.reader_page
            popover_open = (
                (hasattr(self, "appearance_popover") and not self.appearance_popover.isHidden())
                or (hasattr(self, "focus_popover") and not self.focus_popover.isHidden())
            )
            search_focused = hasattr(self, "search_input") and self.search_input.hasFocus()
            if (
                on_reader and not popover_open and not search_focused
                and not self.moyu_mode and not self._moyu_preserve_external_focus
            ):
                self.reader.setFocus()
        set_window_border_hidden(self, self.focus_mode or self.moyu_mode)
        self._configure_hotkeys()
        if self.focus_mode:
            for widget in self.chrome_widgets:
                widget.hide()
            if hasattr(self, "toast"):
                self.toast.dismiss()
            if hasattr(self, "appearance_popover"):
                self.appearance_popover.dismiss()
            if hasattr(self, "focus_popover"):
                self.focus_popover.dismiss()
        if self.moyu_mode:
            self._position_moyu_bar()
        self.schedule_save()

    def _sync_reader_navigation_labels(self):
        if not hasattr(self, "reader_navigation"):
            return

        for button, source in (
            (self.previous_chapter, "上一章"),
            (self.previous, "← 上一页"),
            (self.next, "下一页 →"),
            (self.next_chapter, "下一章"),
        ):
            label = self.t(source)
            button.setToolTip(label)
            button.setAccessibleName(label)

        full_page_label = self.page_label.property("full_text") or self.page_label.text()
        compact_page_label = self.page_label.property("compact_text") or "Pg"
        self.page_label.setText(full_page_label)
        self.page_label.setToolTip(full_page_label)
        footer_w = self.footer_book_progress.sizeHint().width() if hasattr(self, "footer_book_progress") else 0
        target_controls_width = 0
        for widget in (
            self.jump_back_button, self.jump_back_label,
            self.jump_forward_button, self.jump_forward_label,
        ):
            if widget.isHidden():
                continue
            width = (
                widget.maximumWidth()
                if isinstance(widget, _JumpTargetLabel)
                else widget.sizeHint().width()
            )
            target_controls_width += width + 4
        full_width = self.page_label.sizeHint().width() + footer_w + 132 + target_controls_width
        compact = self.reader_navigation.width() < full_width
        if compact:
            self.page_label.setText(compact_page_label)
        self.reader_navigation.setProperty("compact", compact)

    def _configure_hotkeys(self, *, capture_active=None):
        if capture_active is not None:
            self._shortcut_capture_active = capture_active
        if self.closed or self.quitting:
            for hotkey in self.moyu_hotkeys:
                hotkey.unregister()
            return
        settings = self.settings
        enabled = settings.global_hotkey and not self._shortcut_capture_active
        warnings = []
        if self.hotkey:
            warning = self.hotkey.configure(enabled, settings.hotkey)
            if warning:
                warnings.append(self.t("显示 / 隐藏：{message}", message=self.t(warning)))
        if self.emergency_hotkey:
            emergency_enabled = enabled and self.isVisible() and not self.isMinimized()
            error = self.emergency_hotkey.configure(emergency_enabled, settings.emergency_key)
            if error:
                warnings.append(self.t("紧急退出：{message}", message=self.t(error)))
        moyu_visible = self.isVisible() or self._moyu_auto_hidden
        moyu_enabled = self.moyu_mode and moyu_visible and not self.isMinimized() and not self._shortcut_capture_active
        moyu_errors = []
        for hotkey, key in zip(
            self.moyu_hotkeys,
            (settings.moyu_key, settings.previous_key, settings.next_key, settings.moyu_lines_key),
        ):
            error = hotkey.configure(moyu_enabled, key)
            if error:
                moyu_errors.append(f"{key}: {self.t(error)}")
        toggle_registered = bool(self.moyu_hotkeys and self.moyu_hotkeys[0].registered)
        lines_registered = bool(len(self.moyu_hotkeys) > 3 and self.moyu_hotkeys[3].registered)
        if hasattr(self, "moyu_shortcut"):
            self.moyu_shortcut.setEnabled(
                not self._shortcut_capture_active and (not self.moyu_mode or not toggle_registered)
            )
        if hasattr(self, "moyu_lines_shortcut"):
            self.moyu_lines_shortcut.setEnabled(
                not self._shortcut_capture_active and self.moyu_mode and not lines_registered
            )
        if hasattr(self, "moyu_strip"):
            self.moyu_strip.set_hotkey_tooltip("\n".join(moyu_errors))
        if hasattr(self, "hotkey_notice"):
            self.hotkey_notice.setText("\n".join(warnings))
            self.hotkey_notice.setVisible(bool(warnings) and not self.focus_mode)

    def _handle_escape(self):
        if self.focus_mode or self.moyu_mode:
            self.toggle_focus()
        elif hasattr(self, "term_hover_card") and not self.term_hover_card.isHidden():
            self._hide_term_hover()
        elif hasattr(self, "appearance_popover") and not self.appearance_popover.isHidden():
            self.appearance_popover.dismiss()
        elif hasattr(self, "focus_popover") and not self.focus_popover.isHidden():
            self.focus_popover.dismiss()
        elif hasattr(self, "settings_page") and self.main_pages.currentWidget() is self.settings_page:
            self.close_settings()
        elif self._is_search_bar_open():
            self.close_book_search()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            if self.focus_mode or self.moyu_mode:
                self.toggle_focus()
                event.accept()
                return
            if hasattr(self, "term_hover_card") and not self.term_hover_card.isHidden():
                self._hide_term_hover()
                event.accept()
                return
            if self._is_search_bar_open():
                self.close_book_search()
                event.accept()
                return
        super().keyPressEvent(event)

    def toggle_settings(self, checked=False):
        if hasattr(self, "settings_page") and self.main_pages.currentWidget() is self.settings_page:
            self.close_settings()
        else:
            self.show_settings()

    def show_settings(self, section=None):
        if self.focus_mode:
            self.toggle_focus()
        self._hide_term_hover()
        if self.isHidden() or self.isMinimized():
            self.reveal()
        if hasattr(self, "appearance_popover"):
            self.appearance_popover.dismiss()
        if hasattr(self, "focus_popover"):
            self.focus_popover.dismiss()
        if hasattr(self, "settings_page"):
            current = self.main_pages.currentWidget()
            if current is not self.settings_page:
                self._pre_settings_page = current
            self.settings_page.sync_settings(self.settings, colors(self.settings.theme))
            if isinstance(section, str) and section:
                self.settings_page.select_section(section)
            self.main_pages.setCurrentWidget(self.settings_page)
            self._sync_page_navigation()
            return
        dialog = SettingsDialog(self.settings, self)
        dialog.capture_started.connect(lambda: self._configure_hotkeys(capture_active=True))
        dialog.capture_finished.connect(lambda: self._configure_hotkeys(capture_active=False))
        dialog.layout_requested.connect(self.show_layout_panel)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.apply_settings(dialog.values())

    def close_settings(self):
        if hasattr(self, "settings_page"):
            self.settings_page.cancel_active_capture()
        target = self._pre_settings_page
        self._pre_settings_page = None
        if target is self.shelf:
            self.show_shelf_page()
        else:
            self.show_reading_page()

    def create_layout_panel(self):
        if self.focus_mode:
            self.toggle_focus()
        panel = LayoutPanel(self.settings, self)
        area = self.screen().availableGeometry()
        x = self.frameGeometry().right() + 12
        if x + panel.width() > area.right():
            x = self.frameGeometry().left() - panel.width() - 12
        panel.move(max(area.left(), min(x, area.right() - panel.width())), max(area.top(), min(self.y(), area.bottom() - panel.height())))
        def merged(values):
            return replace(self.settings, font_family=values.font_family, **{key: getattr(values, key) for key in panel.fields})
        panel.preview.connect(lambda values: self.reader.apply_settings(merged(values)))
        panel.accepted.connect(lambda: self.apply_settings(merged(panel.values())))
        panel.rejected.connect(lambda: self.reader.apply_settings(self.settings))
        return panel

    def show_layout_panel(self):
        if self.focus_mode:
            self.toggle_focus()
        self._hide_term_hover()
        if hasattr(self, "focus_popover"):
            self.focus_popover.dismiss()
        if hasattr(self, "appearance_popover"):
            if not self.appearance_popover.isHidden():
                self.appearance_popover.dismiss()
                return
            self.appearance_popover.sync_settings(self.settings, colors(self.settings.theme))
            self.layout_button.setChecked(True)
            self.appearance_popover.open_for(self.layout_button, align="right")
            return
        if getattr(self, "layout_panel", None) and self.layout_panel.isVisible():
            self.layout_panel.raise_()
            return
        self.layout_panel = self.create_layout_panel()
        self.layout_panel.show()

    def _system_theme_changed(self, *args):
        if self.settings.theme == "system":
            self._apply_window_style()
            self.reader.update()

    def _screen_removed(self, *args):
        screens = [s.availableGeometry().getRect() for s in QGuiApplication.screens()]
        if self.moyu_mode:
            self._position_moyu_bar()
        else:
            self.setGeometry(*visible_bounds(self.geometry().getRect(), screens))

    def reveal(self):
        if self.moyu_mode:
            was_hidden = self.isHidden() or self.isMinimized()
            self._moyu_hide_timer.stop()
            self._moyu_auto_hidden = False
            self._moyu_auto_hide_armed = False
            bar_geometry = QRect(self._moyu_bar_geometry or self.geometry())
            self.showNormal()
            self.setGeometry(bar_geometry)
            if was_hidden:
                self._schedule_auto_focus_resample(140)
            return
        self._screen_removed()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def toggle_visible(self):
        if self.isVisible() and not self.isMinimized():
            self.hide_or_minimize()
        else:
            self.reveal()

    def hide_or_minimize(self):
        self._hide_term_hover()
        if self.moyu_mode:
            self._moyu_bar_geometry = QRect(self.geometry())
            self._moyu_hide_timer.stop()
            self._moyu_auto_hidden = False
            self._moyu_auto_hide_armed = False
        self.save_state()
        if self.tray and self.tray.isVisible():
            self.hide()
        else:
            self.showMinimized()

    def schedule_save(self):
        if not self.closed:
            self.save_timer.start()

    def save_state(self):
        if self.closed:
            return False
        self.store.state["settings"] = self.settings.to_dict()
        self.store.state["last_browse_dir"] = self._last_browse_dir
        if self.moyu_mode and not self.isMinimized():
            self.store.state["moyu_window"] = list(self.geometry().getRect())
        elif not self.isMinimized() and not self.isMaximized():
            bounds = self.geometry()
            self.store.state["window"] = list(bounds.getRect())
        if self.book:
            section = self.book.sections[self.section_index]
            self.store.state["positions"][self.book.id] = asdict(Position(section.id, self.reader.offset))
            self._persist_jump_history(schedule=False)
        try:
            saved = self.store.save()
        except OSError:
            self.set_status("保存失败，请检查用户数据目录的权限和可用空间。")
            return False
        if saved is False:
            self.set_status(
                "词条标注数据超过存储容量上限（{limit} MiB），未能保存。",
                limit=MAX_TERM_NOTES_BYTES_MIB,
                notify_in_focus=True,
            )
            return False
        return True

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "save_timer"):
            self.schedule_save()
        if hasattr(self, "sidebar"):
            self._sync_page_navigation()
            self._update_window_controls()
            self._sync_reader_navigation_labels()
            self._sync_search_bar_responsive()
            self._update_jump_navigation()
        if hasattr(self, "toast") and not self.toast.isHidden():
            self.toast.reposition(self._toast_bottom_margin())
        if (
            (hasattr(self, "term_hover_card") and not self.term_hover_card.isHidden())
            or (hasattr(self, "moyu_term_hover_card") and not self.moyu_term_hover_card.isHidden())
        ):
            self._hide_term_hover()
        if hasattr(self, "appearance_popover") and not self.appearance_popover.isHidden():
            self.appearance_popover.reposition()
        if hasattr(self, "focus_popover") and not self.focus_popover.isHidden():
            self.focus_popover.reposition()
        if self.moyu_mode:
            self._moyu_bar_geometry = QRect(self.geometry())
            self._schedule_auto_focus_resample(160)
        elif self.focus_mode:
            self._focus_geometry_dirty = True
            if not self._focus_adjust_active:
                self._schedule_auto_focus_resample(160)

    def showEvent(self, event):
        was_hidden = self._window_hidden
        self._window_hidden = False
        super().showEvent(event)
        if self.moyu_mode and not self._moyu_visibility_timer.isActive():
            self._moyu_auto_hidden = False
            self._moyu_auto_hide_armed = False
            self._moyu_visibility_timer.start()
            QTimer.singleShot(0, lambda: self._sync_moyu_visibility(initial=True))
        self._configure_hotkeys()
        if hasattr(self, "sidebar"):
            self._sync_page_navigation()
            self._update_window_controls()
            self._sync_reader_navigation_labels()
            self._sync_search_bar_responsive()
            self._update_jump_navigation()
        if hasattr(self, "toast") and not self.toast.isHidden():
            self.toast.reposition(self._toast_bottom_margin())
        if was_hidden and self.focus_mode:
            self._schedule_auto_focus_resample(140)

    def hideEvent(self, event):
        auto_hidden_moyu = self.moyu_mode and self._moyu_auto_hidden
        if not auto_hidden_moyu:
            self._window_hidden = True
        if hasattr(self, "_focus_resample_timer"):
            self._focus_resample_timer.stop()
        if hasattr(self, "_focus_picker"):
            self._focus_picker.cancel()
        if self.emergency_hotkey and not auto_hidden_moyu:
            self.emergency_hotkey.unregister()
        if not auto_hidden_moyu:
            for hotkey in self.moyu_hotkeys:
                hotkey.unregister()
            if self.moyu_mode:
                self._moyu_visibility_timer.stop()
                self._moyu_hide_timer.stop()
        if hasattr(self, "toast"):
            self.toast.dismiss()
        if hasattr(self, "term_hover_card"):
            self._hide_term_hover()
        if hasattr(self, "appearance_popover"):
            self.appearance_popover.dismiss()
        if hasattr(self, "focus_popover"):
            self.focus_popover.dismiss()
        if hasattr(self, "settings_page"):
            self.settings_page.cancel_active_capture()
        super().hideEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._update_window_controls()
            was_minimized = bool(event.oldState() & Qt.WindowState.WindowMinimized)
            if was_minimized and not self.isMinimized() and self.focus_mode:
                self._schedule_auto_focus_resample(140)
            self._configure_hotkeys()

    def moveEvent(self, event):
        super().moveEvent(event)
        if (
            (hasattr(self, "term_hover_card") and not self.term_hover_card.isHidden())
            or (hasattr(self, "moyu_term_hover_card") and not self.moyu_term_hover_card.isHidden())
        ):
            self._hide_term_hover()
        if hasattr(self, "save_timer"):
            self.schedule_save()
        if self.moyu_mode:
            self._moyu_bar_geometry = QRect(self.geometry())
            self._schedule_auto_focus_resample(160)
        elif self.focus_mode:
            self._focus_geometry_dirty = True
            if not self._focus_adjust_active:
                self._schedule_auto_focus_resample(160)

    def _focus_adjust_key(self):
        return {
            "Alt": Qt.Key.Key_Alt,
            "Ctrl": Qt.Key.Key_Control,
            "Shift": Qt.Key.Key_Shift,
        }.get(self.settings.focus_adjust_key, Qt.Key.Key_Alt)

    def _focus_edges_at(self, global_position):
        if self.isMaximized() or self.isFullScreen():
            return Qt.Edge(0)
        position = self.mapFromGlobal(global_position)
        margin = 9
        edges = Qt.Edge(0)
        if position.x() < margin:
            edges |= Qt.Edge.LeftEdge
        elif position.x() >= self.width() - margin:
            edges |= Qt.Edge.RightEdge
        if position.y() < margin:
            edges |= Qt.Edge.TopEdge
        elif position.y() >= self.height() - margin:
            edges |= Qt.Edge.BottomEdge
        return edges

    @staticmethod
    def _focus_cursor_for_edges(edges):
        left = bool(edges & Qt.Edge.LeftEdge)
        right = bool(edges & Qt.Edge.RightEdge)
        top = bool(edges & Qt.Edge.TopEdge)
        bottom = bool(edges & Qt.Edge.BottomEdge)
        if (top and left) or (bottom and right):
            return Qt.CursorShape.SizeFDiagCursor
        if (top and right) or (bottom and left):
            return Qt.CursorShape.SizeBDiagCursor
        if left or right:
            return Qt.CursorShape.SizeHorCursor
        if top or bottom:
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    def _set_focus_adjust_active(self, active):
        active = bool(active and (self.focus_mode or self.moyu_mode))
        if active == self._focus_adjust_active:
            return
        self._focus_adjust_active = active
        self._focus_adjust_drag = None
        self._focus_system_drag_active = False
        if active:
            self._hide_term_hover()
            if not self.moyu_mode:
                QApplication.setOverrideCursor(Qt.CursorShape.ArrowCursor)
                self._focus_cursor_override = True
        elif self._focus_cursor_override:
            QApplication.restoreOverrideCursor()
            self._focus_cursor_override = False
        self._apply_window_style()
        if not active and self.focus_mode and (self._focus_geometry_dirty or self._focus_resample_pending):
            self._focus_geometry_dirty = False
            self._focus_resample_pending = False
            self._schedule_auto_focus_resample(90)

    def _update_focus_adjust_cursor(self, global_position):
        if not self._focus_cursor_override:
            return
        cursor = self._focus_cursor_for_edges(self._focus_edges_at(global_position))
        QApplication.changeOverrideCursor(cursor)

    def _resize_focus_geometry(self, global_position):
        if not self._focus_adjust_drag:
            return
        origin, geometry, edges = self._focus_adjust_drag
        dx = global_position.x() - origin.x()
        dy = global_position.y() - origin.y()
        left, top, width, height = geometry.x(), geometry.y(), geometry.width(), geometry.height()
        minimum_width, minimum_height = self.minimumWidth(), self.minimumHeight()
        if edges & Qt.Edge.LeftEdge:
            left += dx
            width -= dx
            if width < minimum_width:
                left -= minimum_width - width
                width = minimum_width
        elif edges & Qt.Edge.RightEdge:
            width = max(minimum_width, width + dx)
        if edges & Qt.Edge.TopEdge:
            top += dy
            height -= dy
            if height < minimum_height:
                top -= minimum_height - height
                height = minimum_height
        elif edges & Qt.Edge.BottomEdge:
            height = max(minimum_height, height + dy)
        self.setGeometry(left, top, width, height)

    def _set_drag_hover_active(self, active):
        active = bool(active and not self.focus_mode)
        if active == self._drag_hover_active:
            return
        self._drag_hover_active = active
        self._apply_window_style()
        if active:
            will_open = self.book is None and self.main_pages.currentWidget() is self.reader_page
            prompt = "松开鼠标添加并打开书籍。" if will_open else "松开鼠标添加到书架。"
            self.status.setText(self.t(prompt))
        else:
            self.status.setText(self.t(self.status_source[0], **self.status_source[1]))

    def dragEnterEvent(self, event):
        if self.focus_mode or self.loading or self.closed or self.store.read_only or not event.mimeData().hasUrls():
            event.ignore()
            return
        valid_paths, _skipped, _has_local = classify_dropped_urls(event.mimeData().urls())
        if not valid_paths:
            event.ignore()
            return
        self._drag_has_valid = True
        self._set_drag_hover_active(True)
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def dragMoveEvent(self, event):
        if self.focus_mode or self.loading or self.closed or not self._drag_has_valid or not event.mimeData().hasUrls():
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def dragLeaveEvent(self, event):
        self._drag_has_valid = False
        self._set_drag_hover_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._drag_has_valid = False
        self._set_drag_hover_active(False)
        if self.focus_mode or self.loading or self.closed or not event.mimeData().hasUrls():
            event.ignore()
            return
        valid_paths, skipped, has_local = classify_dropped_urls(event.mimeData().urls())
        if not has_local:
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        if self.store.read_only:
            self.set_status("保存数据来自不支持的版本。已保护原文件，本次不会保存更改。")
            return
        if not valid_paths:
            self.set_status("拖放仅支持本地 TXT、EPUB 或 PDF 文件。")
            return
        open_after = self.book is None and self.main_pages.currentWidget() is self.reader_page
        added = self.add_book_paths(valid_paths, open_after=open_after, skipped_count=skipped)
        if added and not open_after and self.main_pages.currentWidget() is self.shelf:
            self.shelf.reveal_book(added[0]["id"])

    def eventFilter(self, watched, event):
        event_type = event.type()
        if event_type == QEvent.Type.ApplicationDeactivate or (event_type == QEvent.Type.WindowDeactivate and watched is self):
            self._hide_term_hover()
            self._set_focus_adjust_active(False)
            if hasattr(self, "moyu_strip"):
                self.moyu_strip.cancel_interaction()
            self._drag_has_valid = False
            self._set_drag_hover_active(False)
            return False
        if not self.focus_mode:
            return super().eventFilter(watched, event)

        if (
            isinstance(watched, QWidget)
            and watched.window() is self
            and not self.moyu_mode
            and event_type in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease)
            and event.key() == self._focus_adjust_key()
        ):
            self._set_focus_adjust_active(event_type == QEvent.Type.KeyPress)
            return False

        if not isinstance(watched, QWidget) or watched.window() is not self:
            return super().eventFilter(watched, event)

        if event_type == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton and self._focus_adjust_active:
            self._focus_adjust_mouse_drag_active = True
            global_position = event.globalPosition().toPoint()
            edges = self._focus_edges_at(global_position)
            handle = self.windowHandle()
            if edges and handle and handle.startSystemResize(edges):
                self._focus_system_drag_active = True
            elif not edges and handle and handle.startSystemMove():
                self._focus_system_drag_active = True
            else:
                self._focus_adjust_drag = (global_position, self.geometry(), edges)
            event.accept()
            return True

        if event_type == QEvent.Type.MouseMove:
            global_position = event.globalPosition().toPoint()
            dragging_left = bool(event.buttons() & Qt.MouseButton.LeftButton)
            if self._focus_adjust_mouse_drag_active:
                # Native system moves also send mouse events through the reader widget.
                if dragging_left and self._focus_adjust_active and self._focus_adjust_drag:
                    self._resize_focus_geometry(global_position)
                if dragging_left:
                    event.accept()
                    return True
                self._focus_adjust_mouse_drag_active = False
                self._focus_adjust_drag = None
                self._focus_system_drag_active = False
            if self._focus_adjust_active:
                if not dragging_left:
                    self._focus_system_drag_active = False
                if not self._focus_system_drag_active:
                    self._update_focus_adjust_cursor(global_position)
        elif event_type == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            was_focus_drag = self._focus_adjust_mouse_drag_active
            self._focus_adjust_mouse_drag_active = False
            self._focus_adjust_drag = None
            self._focus_system_drag_active = False
            if was_focus_drag:
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def closeEvent(self, event):
        if not self.quitting and self.settings.close_to_tray and self.tray and self.tray.isVisible():
            self.save_state()
            self.hide()
            event.ignore()
            return
        self.shutdown()
        event.accept()
        if self.enable_system:
            QApplication.instance().quit()

    def shutdown(self):
        if self.closed:
            return
        self.release_checker.stop()
        self._set_focus_adjust_active(False)
        self._moyu_visibility_timer.stop()
        self._moyu_hide_timer.stop()
        app = QApplication.instance()
        if app:
            app.removeEventFilter(self)
        self.save_state()
        self.save_timer.stop()
        if hasattr(self, "_focus_resample_timer"):
            self._focus_resample_timer.stop()
        if hasattr(self, "_focus_picker"):
            self._focus_picker.cancel()
        if hasattr(self, "toast"):
            self.toast.dismiss()
        if hasattr(self, "term_hover_card"):
            self._hide_term_hover()
        if hasattr(self, "moyu_term_hover_card"):
            self.moyu_term_hover_card.close()
        if hasattr(self, "appearance_popover"):
            self.appearance_popover.dismiss()
        if hasattr(self, "focus_popover"):
            self.focus_popover.dismiss()
        if hasattr(self, "settings_page"):
            self.settings_page.cancel_active_capture()
        self.closed = True
        self._invalidate_book_search_requests()
        self.search_pool.waitForDone()
        self.pool.waitForDone()
        if self.hotkey:
            self.hotkey.close()
        if self.emergency_hotkey:
            self.emergency_hotkey.close()
        for hotkey in self.moyu_hotkeys:
            hotkey.close()
        if self.tray:
            self.tray.hide()
        self.hide()

    def quit_app(self):
        self.quitting = True
        self.close()

    def emergency_exit(self):
        """Exit this process without a tray close or waiting for import workers."""
        self.quitting = True
        self._moyu_visibility_timer.stop()
        self._moyu_hide_timer.stop()
        try:
            self._hide_term_hover()
            self.hide()
            if self.tray:
                self.tray.hide()
            self.save_timer.stop()
            self.save_state()
        except Exception:
            # An unavailable disk must not turn emergency exit into a dialog.
            pass
        finally:
            self.closed = True
            try:
                if self.hotkey:
                    self.hotkey.close()
                if self.emergency_hotkey:
                    self.emergency_hotkey.close()
                for hotkey in self.moyu_hotkeys:
                    hotkey.close()
                release_lock = getattr(QApplication.instance(), "_quietreader_release_lock", None)
                if release_lock:
                    release_lock()
            except Exception:
                pass
            os._exit(0)
