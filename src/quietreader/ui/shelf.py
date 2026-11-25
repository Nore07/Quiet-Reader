"""Modern two-pane bookshelf with category rail, cover grid / list views, search, and drag categorization."""

import json
from math import isfinite
from pathlib import Path

from PySide6.QtCore import QEvent, QMimeData, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QFont, QFontMetrics, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QApplication, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMenu, QPushButton, QSizePolicy, QStyle,
    QStyleOptionViewItem, QStyledItemDelegate, QVBoxLayout, QWidget,
)

from quietreader.content.local import read_epub_cover
from quietreader.i18n import tr
from quietreader.library.shelf import UNCLASSIFIED
from quietreader.ui.controls import IconButton, QuietComboBox
from quietreader.ui.covers import render_cover_pixmap
from quietreader.ui.icons import render_icon_pixmap, vector_icon
from quietreader.ui.tokens import NARROW_BREAKPOINT, resolve_palette


_BOOK_IDS_MIME_TYPE = "application/x-quietreader-book-ids"
_BOOK_TITLE_ROLE = Qt.ItemDataRole.UserRole + 1
_BOOK_DETAIL_ROLE = Qt.ItemDataRole.UserRole + 2
_BOOK_PROGRESS_ROLE = Qt.ItemDataRole.UserRole + 3
_BOOK_PROGRESS_FRACTION_ROLE = Qt.ItemDataRole.UserRole + 4
_BOOK_AUTHOR_ROLE = Qt.ItemDataRole.UserRole + 5
_BOOK_FORMAT_ROLE = Qt.ItemDataRole.UserRole + 6
_BOOK_PATH_ROLE = Qt.ItemDataRole.UserRole + 7
_CATEGORY_DROP_HOVER_ROLE = Qt.ItemDataRole.UserRole + 8
_CATEGORY_COUNT_ROLE = Qt.ItemDataRole.UserRole + 9


def display_title_for_record(record):
    override = record.get("title_override")
    if isinstance(override, str) and override.strip():
        return override
    return record.get("title") or Path(record["path"]).stem


class _CoverCache:
    """In-memory cache of validated EPUB cover payloads keyed by (path, mtime_ns)."""

    def __init__(self, limit: int = 256):
        self._limit = limit
        self._entries: dict[tuple[str, int], bytes] = {}

    def get(self, file_path: str) -> bytes:
        path = Path(file_path)
        if path.suffix.lower() != ".epub":
            return b""
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            return b""
        key = (str(path), mtime)
        if key not in self._entries:
            if len(self._entries) >= self._limit:
                self._entries.pop(next(iter(self._entries)))
            self._entries[key] = read_epub_cover(path)
        return self._entries[key]


class BookItemDelegate(QStyledItemDelegate):
    """Dual-mode delegate rendering either a Cover Grid card or a compact List row with a '···' menu trigger."""

    GRID_CARD_SIZE = QSize(156, 232)
    LIST_ROW_HEIGHT = 64

    def __init__(self, cover_cache: _CoverCache, parent=None):
        super().__init__(parent)
        self._cover_cache = cover_cache
        self._mode = "grid"
        self._palette = resolve_palette("light")

    def set_mode(self, mode: str):
        self._mode = "list" if mode == "list" else "grid"

    def set_palette(self, palette: dict[str, str]):
        self._palette = dict(palette)

    def more_button_rect(self, item_rect: QRect) -> QRect:
        if self._mode == "grid":
            return QRect(item_rect.right() - 28, item_rect.bottom() - 38, 22, 22)
        return QRect(item_rect.right() - 34, item_rect.top() + (item_rect.height() - 24) // 2, 24, 24)

    def sizeHint(self, option, index):
        if self._mode == "grid":
            return self.GRID_CARD_SIZE
        return QSize(max(180, option.rect.width()), self.LIST_ROW_HEIGHT)

    def paint(self, painter, option, index):
        if self._mode == "grid":
            self._paint_grid_card(painter, option, index)
        else:
            self._paint_list_row(painter, option, index)

    def _paint_grid_card(self, painter: QPainter, option, index):
        rect = option.rect.adjusted(6, 6, -6, -6)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        title = index.data(_BOOK_TITLE_ROLE) or ""
        author = index.data(_BOOK_AUTHOR_ROLE) or ""
        fmt = index.data(_BOOK_FORMAT_ROLE) or ""
        file_path = index.data(_BOOK_PATH_ROLE) or ""
        progress_text = index.data(_BOOK_PROGRESS_ROLE) or ""
        fraction = index.data(_BOOK_PROGRESS_FRACTION_ROLE)

        cover_w = rect.width()
        cover_h = 172
        cover_rect = QRect(rect.left(), rect.top(), cover_w, cover_h)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setClipRect(option.rect)

        # Subtle card hover/selection backdrop
        if selected or hovered:
            bg = QColor(self._palette["accent_soft"] if selected else self._palette["accent_hover"])
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg)
            painter.drawRoundedRect(QRectF(option.rect.adjusted(2, 2, -2, -2)), 10.0, 10.0)

        cover_bytes = self._cover_cache.get(file_path)
        pm = render_cover_pixmap(
            title, author, fmt, cover_bytes,
            width=cover_w, height=cover_h, radius=8.0, scale=painter.device().devicePixelRatioF(), mini=False,
        )
        painter.drawPixmap(cover_rect, pm)

        if selected:
            painter.setPen(QPen(QColor(self._palette["accent"]), 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(cover_rect).adjusted(1, 1, -1, -1), 8.0, 8.0)

        # Book title (single line elided, leaving room for '···')
        title_font = QFont(option.font)
        title_font.setPixelSize(12)
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QColor(self._palette["ink"]))
        title_rect = QRect(rect.left() + 1, cover_rect.bottom() + 7, cover_w - 26, 18)
        fm_title = QFontMetrics(title_font)
        painter.drawText(
            title_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            fm_title.elidedText(title, Qt.TextElideMode.ElideRight, title_rect.width()),
        )

        # Progress bar + short status
        sub_y = title_rect.bottom() + 4
        sub_font = QFont(option.font)
        sub_font.setPixelSize(10)
        painter.setFont(sub_font)
        fm_sub = QFontMetrics(sub_font)
        if isinstance(fraction, (int, float)) and isfinite(fraction) and fraction > 0:
            bar_w = min(56, max(28, cover_w - 78))
            bar_rect = QRectF(rect.left() + 1, sub_y + 5, bar_w, 3.0)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self._palette["track"]))
            painter.drawRoundedRect(bar_rect, 1.5, 1.5)
            fill_w = max(3.0, bar_w * max(0.0, min(1.0, float(fraction))))
            painter.setBrush(QColor(self._palette["accent"]))
            painter.drawRoundedRect(QRectF(bar_rect.left(), bar_rect.top(), fill_w, 3.0), 1.5, 1.5)
            pct_rect = QRect(int(bar_rect.right()) + 6, sub_y, cover_w - int(bar_rect.right() - rect.left()) - 30, 14)
            painter.setPen(QColor(self._palette["muted"]))
            painter.drawText(
                pct_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                fm_sub.elidedText(progress_text, Qt.TextElideMode.ElideRight, pct_rect.width()),
            )
        else:
            painter.setPen(QColor(self._palette["muted"]))
            sub_rect = QRect(rect.left() + 1, sub_y, cover_w - 28, 14)
            painter.drawText(
                sub_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                fm_sub.elidedText(progress_text, Qt.TextElideMode.ElideRight, sub_rect.width()),
            )

        # Overflow '···' trigger icon
        more_rect = self.more_button_rect(option.rect)
        painter.setPen(QColor(self._palette["ink"] if (selected or hovered) else self._palette["muted"]))
        dots_font = QFont(option.font)
        dots_font.setPixelSize(13)
        dots_font.setWeight(QFont.Weight.Bold)
        painter.setFont(dots_font)
        painter.drawText(more_rect, Qt.AlignmentFlag.AlignCenter, "···")
        painter.restore()

    def _paint_list_row(self, painter: QPainter, option, index):
        style_option = QStyleOptionViewItem(option)
        self.initStyleOption(style_option, index)
        selected = bool(style_option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(style_option.state & QStyle.StateFlag.State_MouseOver)

        title = index.data(_BOOK_TITLE_ROLE) or ""
        detail = index.data(_BOOK_DETAIL_ROLE) or ""
        progress = index.data(_BOOK_PROGRESS_ROLE) or ""
        author = index.data(_BOOK_AUTHOR_ROLE) or ""
        fmt = index.data(_BOOK_FORMAT_ROLE) or ""
        file_path = index.data(_BOOK_PATH_ROLE) or ""

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setClipRect(style_option.rect)

        row_rect = style_option.rect.adjusted(2, 2, -2, -2)
        if selected or hovered:
            bg = QColor(self._palette["accent_soft"] if selected else self._palette["accent_hover"])
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg)
            painter.drawRoundedRect(QRectF(row_rect), 8.0, 8.0)

        # Subtle bottom hairline divider
        painter.setPen(QPen(QColor(self._palette["line"]), 1.0))
        painter.drawLine(
            style_option.rect.left() + 12, style_option.rect.bottom(),
            style_option.rect.right() - 12, style_option.rect.bottom(),
        )

        # Left 30x40 mini cover thumbnail
        thumb_w, thumb_h = 30, 40
        thumb_x = style_option.rect.left() + 12
        thumb_y = style_option.rect.top() + (style_option.rect.height() - thumb_h) // 2
        cover_bytes = self._cover_cache.get(file_path)
        pm = render_cover_pixmap(
            title, author, fmt, cover_bytes,
            width=thumb_w, height=thumb_h, radius=4.0, scale=painter.device().devicePixelRatioF(), mini=True,
        )
        painter.drawPixmap(QRect(thumb_x, thumb_y, thumb_w, thumb_h), pm)

        title_font = QFont(style_option.font)
        title_font.setWeight(QFont.Weight.Medium)
        detail_font = QFont(style_option.font)
        if detail_font.pointSize() > 0:
            detail_font.setPointSize(max(8, detail_font.pointSize() - 1))
        elif detail_font.pixelSize() > 0:
            detail_font.setPixelSize(max(10, detail_font.pixelSize() - 1))

        title_metrics = QFontMetrics(title_font)
        detail_metrics = QFontMetrics(detail_font)
        more_rect = self.more_button_rect(style_option.rect)
        content_left = thumb_x + thumb_w + 12
        content_right = more_rect.left() - 10
        content_width = max(40, content_right - content_left)

        text_height = title_metrics.height() + detail_metrics.height() + 2
        top = style_option.rect.top() + max(4, (style_option.rect.height() - text_height) // 2)
        title_rect = QRect(content_left, top, content_width, title_metrics.height())
        detail_rect = QRect(content_left, top + title_metrics.height() + 2, content_width, detail_metrics.height())

        painter.setFont(title_font)
        painter.setPen(QColor(self._palette["ink"]))
        painter.drawText(
            title_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            title_metrics.elidedText(title, Qt.TextElideMode.ElideRight, title_rect.width()),
        )

        painter.setFont(detail_font)
        painter.setPen(QColor(self._palette["muted"]))
        if progress:
            progress_width = detail_metrics.horizontalAdvance(progress)
            gap = detail_metrics.horizontalAdvance("  ")
            progress_rect = QRect(
                detail_rect.right() - progress_width + 1,
                detail_rect.top(),
                progress_width,
                detail_rect.height(),
            )
            detail_width = max(0, detail_rect.width() - progress_width - gap)
            detail_rect.setWidth(detail_width)
            painter.drawText(
                detail_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                detail_metrics.elidedText(detail, Qt.TextElideMode.ElideRight, detail_width),
            )
            painter.drawText(
                progress_rect,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                progress,
            )
        else:
            painter.drawText(
                detail_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                detail_metrics.elidedText(detail, Qt.TextElideMode.ElideRight, detail_rect.width()),
            )

        painter.setPen(QColor(self._palette["muted"]))
        dots_font = QFont(style_option.font)
        dots_font.setPixelSize(13)
        dots_font.setWeight(QFont.Weight.Bold)
        painter.setFont(dots_font)
        painter.drawText(more_rect, Qt.AlignmentFlag.AlignCenter, "···")
        painter.restore()


class DraggableBookListWidget(QListWidget):
    """Unified Grid/List book view supporting multi-book drag-out and card '···' menu clicks."""

    more_menu_requested = Signal(QListWidgetItem, QPoint)

    def startDrag(self, supported_actions):
        book_ids = [
            item.data(Qt.ItemDataRole.UserRole)
            for item in self.selectedItems()
            if item.data(Qt.ItemDataRole.UserRole)
        ]
        if not book_ids:
            return
        mime_data = QMimeData()
        mime_data.setData(_BOOK_IDS_MIME_TYPE, json.dumps(book_ids).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime_data)
        drag.exec(Qt.DropAction.MoveAction)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            item = self.itemAt(pos)
            delegate = self.itemDelegate()
            if item is not None and isinstance(delegate, BookItemDelegate):
                item_rect = self.visualItemRect(item)
                if delegate.more_button_rect(item_rect).contains(pos):
                    self.setCurrentItem(item)
                    self.more_menu_requested.emit(item, pos)
                    event.accept()
                    return
        super().mousePressEvent(event)


class CategoryDropItemDelegate(QStyledItemDelegate):
    """Renders category name + subtle count pill, highlighting hovered drop targets cleanly."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._palette = resolve_palette("light")

    def set_palette(self, palette: dict[str, str]):
        self._palette = dict(palette)

    def paint(self, painter, option, index):
        style_option = QStyleOptionViewItem(option)
        self.initStyleOption(style_option, index)
        hovered_drop = bool(index.data(_CATEGORY_DROP_HOVER_ROLE))
        if hovered_drop:
            style_option.state |= QStyle.StateFlag.State_Selected
            style_option.state &= ~QStyle.StateFlag.State_MouseOver

        label_text = style_option.text
        count = index.data(_CATEGORY_COUNT_ROLE)
        style_option.text = ""
        widget = style_option.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, style_option, painter, widget)

        selected = bool(style_option.state & QStyle.StateFlag.State_Selected)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = style_option.rect.adjusted(10, 0, -10, 0)

        icon_color = self._palette["accent"] if selected else self._palette["muted"]
        pm = vector_icon("folder", icon_color, 14).pixmap(14, 14)
        painter.drawPixmap(rect.left(), rect.top() + (rect.height() - 14) // 2, pm)

        count_text = str(count) if isinstance(count, int) else ""
        count_font = QFont(style_option.font)
        count_font.setPixelSize(11)
        fm_count = QFontMetrics(count_font)
        count_w = fm_count.horizontalAdvance(count_text) + 6 if count_text else 0

        text_font = QFont(style_option.font)
        text_font.setPixelSize(12)
        if selected:
            text_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(text_font)
        painter.setPen(QColor(self._palette["ink"] if selected else self._palette["ink_secondary"]))
        name_rect = QRect(rect.left() + 22, rect.top(), max(20, rect.width() - 24 - count_w), rect.height())
        painter.drawText(
            name_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            QFontMetrics(text_font).elidedText(label_text, Qt.TextElideMode.ElideRight, name_rect.width()),
        )

        if count_text:
            painter.setFont(count_font)
            painter.setPen(QColor(self._palette["muted"]))
            count_rect = QRect(rect.right() - count_w + 2, rect.top(), count_w, rect.height())
            painter.drawText(count_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, count_text)
        painter.restore()


class CategoryDropListWidget(QListWidget):
    books_dropped = Signal(list, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setDropIndicatorShown(True)
        self._delegate = CategoryDropItemDelegate(self)
        self.setItemDelegate(self._delegate)
        self._drop_hover_item = None
        self._drop_hover_original_role = None

    def apply_palette(self, palette: dict[str, str]):
        self._delegate.set_palette(palette)
        self.viewport().update()

    def clear(self):
        self._clear_drop_hover()
        super().clear()

    def _set_drop_hover(self, item):
        if item is self._drop_hover_item or (
            item is not None and self._drop_hover_item is not None
            and self.row(item) == self.row(self._drop_hover_item)
        ):
            return
        self._clear_drop_hover()
        if item is None:
            return
        self._drop_hover_item = item
        self._drop_hover_original_role = item.data(_CATEGORY_DROP_HOVER_ROLE)
        item.setData(_CATEGORY_DROP_HOVER_ROLE, True)
        self.viewport().update()

    def _clear_drop_hover(self):
        item = self._drop_hover_item
        if item is None:
            return
        item.setData(_CATEGORY_DROP_HOVER_ROLE, self._drop_hover_original_role)
        self._drop_hover_item = None
        self._drop_hover_original_role = None
        self.viewport().update()

    @staticmethod
    def _book_ids(mime_data):
        if not mime_data.hasFormat(_BOOK_IDS_MIME_TYPE):
            return []
        try:
            values = json.loads(bytes(mime_data.data(_BOOK_IDS_MIME_TYPE)).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return []
        if not isinstance(values, list):
            return []
        return list(dict.fromkeys(value for value in values if isinstance(value, str) and value))

    def _drop_target(self, event):
        book_ids = self._book_ids(event.mimeData())
        item = self.itemAt(event.position().toPoint())
        if not book_ids or item is None:
            return None
        category = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(category, str) or not category:
            return None
        return item, book_ids, category

    def dragEnterEvent(self, event):
        self._clear_drop_hover()
        if not self._book_ids(event.mimeData()):
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()

    def dragMoveEvent(self, event):
        target = self._drop_target(event)
        self._set_drop_hover(target[0] if target is not None else None)
        if target is None:
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()

    def dragLeaveEvent(self, event):
        self._clear_drop_hover()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        target = self._drop_target(event)
        self._clear_drop_hover()
        if target is None:
            event.ignore()
            return
        _item, book_ids, category = target
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()
        self.books_dropped.emit(book_ids, category)


class _RailNavButton(QAbstractButton):
    """Left-rail navigation item with crisp vector icon, label, and right-aligned count."""

    def __init__(self, icon_name: str, label: str, parent=None):
        super().__init__(parent)
        self._icon_name = icon_name
        self._label = label
        self._count = 0
        self._hovered = False
        self._palette = resolve_palette("light")
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(34)

    def set_label_and_count(self, label: str, count: int):
        self._label = label
        self._count = count
        self.setText(label)
        self.update()

    def apply_palette(self, palette: dict[str, str]):
        self._palette = dict(palette)
        self.update()

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        checked = self.isChecked()
        if checked:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self._palette["accent_soft"]))
            painter.drawRoundedRect(rect, 7.0, 7.0)
        elif self._hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self._palette["accent_hover"]))
            painter.drawRoundedRect(rect, 7.0, 7.0)

        icon_color = self._palette["accent"] if checked else self._palette["muted"]
        pm = render_icon_pixmap(self._icon_name, icon_color, 14)
        painter.drawPixmap(10, (self.height() - 14) // 2, pm)

        count_text = str(self._count)
        count_font = QFont(self.font())
        count_font.setPixelSize(11)
        fm_count = QFontMetrics(count_font)
        count_w = fm_count.horizontalAdvance(count_text) + 6

        text_font = QFont(self.font())
        text_font.setPixelSize(12)
        if checked:
            text_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(text_font)
        painter.setPen(QColor(self._palette["ink"] if checked else self._palette["ink_secondary"]))
        label_rect = QRect(32, 0, max(20, self.width() - 42 - count_w), self.height())
        painter.drawText(
            label_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            QFontMetrics(text_font).elidedText(self._label, Qt.TextElideMode.ElideRight, label_rect.width()),
        )

        painter.setFont(count_font)
        painter.setPen(QColor(self._palette["muted"]))
        count_rect = QRect(self.width() - 10 - count_w, 0, count_w, self.height())
        painter.drawText(count_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, count_text)
        painter.end()


class _ShelfTabsCompat:
    """Lightweight compatibility adapter preserving shelf.tabs.currentIndex() / setCurrentIndex()."""

    def __init__(self, panel: "ShelfPanel"):
        self._panel = panel

    def currentIndex(self) -> int:
        if self._panel._scope_kind == "recent":
            return 0
        if self._panel._scope_kind == "all":
            return 1
        return 2

    def setCurrentIndex(self, index: int):
        if index == 0:
            self._panel._select_scope("recent")
        elif index == 1:
            self._panel._select_scope("all")
        else:
            self._panel._select_scope("category", self._panel._active_category)


class ShelfPanel(QWidget):
    add_requested = Signal()
    open_requested = Signal(str)
    rename_requested = Signal(str)
    move_books_requested = Signal(list, str)
    remove_requested = Signal(str)
    create_category_requested = Signal()
    rename_category_requested = Signal(str)
    delete_category_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.language = "system"
        self.records = []
        self.categories = []
        self._palette = resolve_palette("light")
        self._cover_cache = _CoverCache()
        self._scope_kind = "all"  # "recent" | "all" | "category"
        self._active_category = UNCLASSIFIED
        self._view_mode = "grid"  # "grid" | "list"
        self._sort_mode = "recent"  # "recent" | "title" | "progress"
        self._syncing_scope = False

        # Hosted in MainWindow's unified topbar
        action_holder = QWidget(self)
        action_holder.hide()
        self.add_button = QPushButton(action_holder)
        self.add_button.setObjectName("primary")
        self.add_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.add_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.add_button.setIconSize(QSize(13, 13))
        self.add_button.clicked.connect(self.add_requested.emit)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 2, 0, 0)
        outer.setSpacing(14)

        # ---- Left slim category navigation rail ----
        self.rail = QFrame(self)
        self.rail.setObjectName("shelfRail")
        self.rail.setFixedWidth(196)
        rail_layout = QVBoxLayout(self.rail)
        rail_layout.setContentsMargins(8, 10, 8, 10)
        rail_layout.setSpacing(4)

        self.nav_recent = self._rail_nav_button("clock", "最近阅读", lambda: self._select_scope("recent"))
        self.nav_all = self._rail_nav_button("grid", "所有书籍", lambda: self._select_scope("all"))
        rail_layout.addWidget(self.nav_recent)
        rail_layout.addWidget(self.nav_all)

        cat_header = QHBoxLayout()
        cat_header.setContentsMargins(6, 12, 2, 2)
        cat_header.setSpacing(4)
        self.cat_section_label = QLabel("分类", self.rail)
        self.cat_section_label.setObjectName("railSectionLabel")
        cat_header.addWidget(self.cat_section_label)
        cat_header.addStretch(1)

        self.category_create = IconButton("plus", size=13, object_name="iconButton", parent=self.rail)
        self.category_create.setFixedSize(22, 22)
        self.category_create.clicked.connect(self.create_category_requested.emit)
        self.category_rename = QPushButton(self.rail)
        self.category_rename.setObjectName("railRenameBtn")
        self.category_rename.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.category_rename.setFixedHeight(22)
        self.category_rename.clicked.connect(self._rename_selected_category)
        self.category_delete = QPushButton(self.rail)
        self.category_delete.setObjectName("railDeleteBtn")
        self.category_delete.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.category_delete.setFixedHeight(22)
        self.category_delete.clicked.connect(self._delete_selected_category)
        cat_header.addWidget(self.category_rename)
        cat_header.addWidget(self.category_delete)
        cat_header.addWidget(self.category_create)
        rail_layout.addLayout(cat_header)

        self.category_list = CategoryDropListWidget(self.rail)
        self.category_list.setObjectName("shelfCategoryList")
        self.category_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.category_list.setAccessibleName("书籍分类")
        self.category_list.books_dropped.connect(self.move_books_requested)
        self.category_list.itemClicked.connect(self._on_category_clicked)
        rail_layout.addWidget(self.category_list, 1)
        outer.addWidget(self.rail)

        # ---- Right main bookshelf canvas ----
        main = QWidget(self)
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(10)

        toolbar = QWidget(main)
        toolbar.setObjectName("shelfToolbar")
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(0, 2, 0, 0)
        tb_layout.setSpacing(8)

        # Compact scope selector shown only on narrow (< 560px) widths when rail hides
        self.narrow_scope = QuietComboBox(toolbar)
        self.narrow_scope.setObjectName("shelfSortCombo")
        self.narrow_scope.setMinimumWidth(96)
        self.narrow_scope.currentIndexChanged.connect(self._on_narrow_scope_changed)
        self.narrow_scope.hide()
        tb_layout.addWidget(self.narrow_scope)

        self.search_input = QLineEdit(toolbar)
        self.search_input.setObjectName("shelfSearch")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setMaximumWidth(280)
        self._search_icon_action = self.search_input.addAction(
            vector_icon("search", self._palette["muted"], 14),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self.search_input.textChanged.connect(self._populate_book_view)
        tb_layout.addWidget(self.search_input, 10)

        tb_layout.addStretch(1)

        self.sort_combo = QuietComboBox(toolbar)
        self.sort_combo.setObjectName("shelfSortCombo")
        for key in ("recent", "title", "progress"):
            self.sort_combo.addItem("", key)
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        tb_layout.addWidget(self.sort_combo)

        view_box = QFrame(toolbar)
        view_box.setObjectName("viewToggleBox")
        vb_layout = QHBoxLayout(view_box)
        vb_layout.setContentsMargins(2, 2, 2, 2)
        vb_layout.setSpacing(2)
        self.grid_btn = IconButton("grid", size=14, checkable=True, object_name="iconButton", parent=view_box)
        self.grid_btn.setFixedSize(26, 24)
        self.grid_btn.clicked.connect(lambda: self.set_view_mode("grid"))
        self.list_btn = IconButton("list", size=14, checkable=True, object_name="iconButton", parent=view_box)
        self.list_btn.setFixedSize(26, 24)
        self.list_btn.clicked.connect(lambda: self.set_view_mode("list"))
        vb_layout.addWidget(self.grid_btn)
        vb_layout.addWidget(self.list_btn)
        tb_layout.addWidget(view_box)
        main_layout.addWidget(toolbar)

        # Unified Grid/List Book View
        self.book_view = DraggableBookListWidget(main)
        self.book_view.setObjectName("shelfList")
        self.book_view.setAccessibleName("所有书籍")
        self.book_view.setWordWrap(False)
        self.book_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Use one list row as the scroll step in both views.
        self.book_view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.book_view.verticalScrollBar().setSingleStep(BookItemDelegate.LIST_ROW_HEIGHT)
        self.book_view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.book_view.setDragEnabled(True)
        self.book_view.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.book_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.book_view.customContextMenuRequested.connect(
            lambda pt: self._show_book_context_menu(self.book_view, pt)
        )
        self.book_view.more_menu_requested.connect(
            lambda _item, pt: self._show_book_context_menu(self.book_view, pt)
        )
        self.book_view.itemActivated.connect(self._open_item)
        self._book_delegate = BookItemDelegate(self._cover_cache, self.book_view)
        self.book_view.setItemDelegate(self._book_delegate)
        main_layout.addWidget(self.book_view, 1)

        # Quiet shelf footer strip
        footer = QHBoxLayout()
        footer.setContentsMargins(4, 0, 4, 0)
        self.footer_count = QLabel("", main)
        self.footer_count.setObjectName("status")
        self.footer_hint = QLabel("", main)
        self.footer_hint.setObjectName("status")
        footer.addWidget(self.footer_count)
        footer.addStretch(1)
        footer.addWidget(self.footer_hint)
        main_layout.addLayout(footer)

        outer.addWidget(main, 1)

        # Compatibility aliases for MainWindow & tests
        self.recent = self.book_view
        self.all_books = self.book_view
        self.category_books = self.book_view
        self.tabs = _ShelfTabsCompat(self)

        self.set_view_mode("grid")
        self._select_scope("all")
        self.retranslate("system")

    def _rail_nav_button(self, icon_name: str, label: str, callback) -> _RailNavButton:
        btn = _RailNavButton(icon_name, label, self.rail)
        btn.setObjectName("railNavBtn")
        btn.clicked.connect(callback)
        return btn

    def apply_palette(self, palette: dict[str, str]):
        self._palette = dict(palette)
        self._book_delegate.set_palette(self._palette)
        self.category_list.apply_palette(self._palette)
        self.category_create.apply_palette(self._palette)
        self.grid_btn.apply_palette(self._palette)
        self.list_btn.apply_palette(self._palette)
        self.nav_recent.apply_palette(self._palette)
        self.nav_all.apply_palette(self._palette)
        self.sort_combo.apply_palette(self._palette)
        self.narrow_scope.apply_palette(self._palette)
        self.add_button.setIcon(vector_icon("plus", self._palette.get("on_accent", "#ffffff"), 13))
        if hasattr(self, "_search_icon_action"):
            self._search_icon_action.setIcon(vector_icon("search", self._palette["muted"], 14))
        self.book_view.viewport().update()

    def _refresh_rail_icons(self):
        self.nav_recent.update()
        self.nav_all.update()

    def set_view_mode(self, mode: str):
        self._view_mode = "list" if mode == "list" else "grid"
        self.grid_btn.setChecked(self._view_mode == "grid")
        self.list_btn.setChecked(self._view_mode == "list")
        self._book_delegate.set_mode(self._view_mode)
        if self._view_mode == "grid":
            self.book_view.setViewMode(QListWidget.ViewMode.IconMode)
            self.book_view.setFlow(QListWidget.Flow.LeftToRight)
            self.book_view.setWrapping(True)
            self.book_view.setResizeMode(QListWidget.ResizeMode.Adjust)
            self.book_view.setMovement(QListWidget.Movement.Static)
            self.book_view.setSpacing(10)
            self.book_view.setGridSize(BookItemDelegate.GRID_CARD_SIZE)
        else:
            self.book_view.setGridSize(QSize())
            self.book_view.setViewMode(QListWidget.ViewMode.ListMode)
            self.book_view.setFlow(QListWidget.Flow.TopToBottom)
            self.book_view.setWrapping(False)
            self.book_view.setResizeMode(QListWidget.ResizeMode.Adjust)
            self.book_view.setMovement(QListWidget.Movement.Static)
            self.book_view.setSpacing(2)
        self._populate_book_view()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        narrow = self.width() < NARROW_BREAKPOINT - 40
        self.rail.setVisible(not narrow)
        self.narrow_scope.setVisible(narrow)
        self.sort_combo.setVisible(self.width() >= 440)
        self.footer_hint.setVisible(self.width() >= 540)

    def retranslate(self, language):
        self.language = language
        self.add_button.setText(tr("添加书籍", language))
        self.add_button.setToolTip(tr("添加本地 TXT / EPUB / PDF 到书架 · Ctrl+O", language))
        self.add_button.setAccessibleName(tr("添加书籍", language))
        self.cat_section_label.setText(tr("分类", language))
        self.category_create.setToolTip(tr("新建分类", language))
        self.category_create.setAccessibleName(tr("新建分类", language))
        self.category_rename.setText(tr("改名", language))
        self.category_rename.setToolTip(tr("重命名分类", language))
        self.category_rename.setAccessibleName(tr("重命名分类", language))
        self.category_delete.setText(tr("删除", language))
        self.category_delete.setToolTip(tr("删除分类", language))
        self.category_delete.setAccessibleName(tr("删除分类", language))
        self.category_list.setToolTip(tr("将右侧书籍拖到分类上即可快速移动。", language))
        self.search_input.setPlaceholderText(tr("搜索书名或作者…", language))
        self.grid_btn.setToolTip(tr("网格视图", language))
        self.grid_btn.setAccessibleName(tr("网格视图", language))
        self.list_btn.setToolTip(tr("列表视图", language))
        self.list_btn.setAccessibleName(tr("列表视图", language))
        sort_labels = ("按最近阅读", "按书名", "按阅读进度")
        for idx, source in enumerate(sort_labels):
            self.sort_combo.setItemText(idx, tr(source, language))
        self.footer_hint.setText(tr("可直接拖入本地 TXT / EPUB / PDF 文件导入书架", language))
        self._refresh_lists()

    def refresh(self, records, categories, category_for_path, language=None):
        if language is not None:
            self.language = language
        self.records = list(records)
        self.categories = list(categories)
        self._category_for_path = category_for_path
        self._refresh_lists()

    def reveal_book(self, book_id):
        if not book_id:
            return False
        if self.search_input.text():
            self.search_input.clear()
        self._select_scope("all")
        for row in range(self.book_view.count()):
            item = self.book_view.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == book_id:
                self.book_view.setCurrentRow(row)
                self.book_view.scrollToItem(item)
                return True
        return False

    def select_category(self, category):
        self._select_scope("category", category)
        for row in range(self.category_list.count()):
            item = self.category_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == category:
                self.category_list.setCurrentRow(row)
                self.category_list.scrollToItem(item)
                return True
        return False

    def _select_scope(self, kind: str, category: str | None = None):
        self._syncing_scope = True
        self._scope_kind = kind
        if kind == "category" and category:
            self._active_category = category
        self.nav_recent.setChecked(kind == "recent")
        self.nav_all.setChecked(kind == "all")
        if kind != "category":
            self.category_list.clearSelection()
            self.category_list.setCurrentRow(-1)
        else:
            for row in range(self.category_list.count()):
                if self.category_list.item(row).data(Qt.ItemDataRole.UserRole) == self._active_category:
                    self.category_list.setCurrentRow(row)
                    break
        self._refresh_rail_icons()
        self._sync_narrow_scope_selection()
        self._update_category_actions()
        self._syncing_scope = False
        self._populate_book_view()

    def _on_category_clicked(self, item):
        if item is None:
            return
        cat = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(cat, str) and cat:
            self._select_scope("category", cat)

    def _on_category_row_changed(self, row: int):
        if self._syncing_scope or row < 0:
            return
        item = self.category_list.item(row)
        if item is not None:
            cat = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(cat, str) and cat:
                self._select_scope("category", cat)

    def _on_narrow_scope_changed(self, index: int):
        if self._syncing_scope or index < 0:
            return
        data = self.narrow_scope.itemData(index)
        if not isinstance(data, tuple) or len(data) != 2:
            return
        self._select_scope(data[0], data[1])

    def _on_sort_changed(self, index: int):
        key = self.sort_combo.itemData(index)
        if isinstance(key, str) and key:
            self._sort_mode = key
            self._populate_book_view()

    def _sync_narrow_scope_selection(self):
        target = (self._scope_kind, self._active_category if self._scope_kind == "category" else "")
        for idx in range(self.narrow_scope.count()):
            if self.narrow_scope.itemData(idx) == target:
                self.narrow_scope.setCurrentIndex(idx)
                return

    def _update_category_actions(self):
        can_edit = (
            self._scope_kind == "category"
            and self._active_category != UNCLASSIFIED
            and self._active_category in self.categories
        )
        for button in (self.category_rename, self.category_delete):
            button.setVisible(can_edit)
            button.setEnabled(can_edit)

    def _category_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {UNCLASSIFIED: 0}
        for name in self.categories:
            counts[name] = 0
        if hasattr(self, "_category_for_path"):
            for record in self.records:
                cat = self._category_for_path(record["path"])
                counts[cat] = counts.get(cat, 0) + 1
        return counts

    def _refresh_lists(self):
        self._syncing_scope = True
        recent_count = sum(1 for r in self.records if r.get("last_read"))
        self.nav_recent.set_label_and_count(tr("最近阅读", self.language), recent_count)
        self.nav_all.set_label_and_count(tr("所有书籍", self.language), len(self.records))

        targets = [UNCLASSIFIED, *self.categories]
        if self._scope_kind == "category" and self._active_category not in targets:
            self._active_category = UNCLASSIFIED

        counts = self._category_counts()
        self.category_list.clear()
        for name in targets:
            label = tr("未分类", self.language) if name == UNCLASSIFIED else name
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setData(_CATEGORY_COUNT_ROLE, counts.get(name, 0))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsDropEnabled)
            item.setSizeHint(QSize(0, 34))
            self.category_list.addItem(item)

        self.narrow_scope.clear()
        self.narrow_scope.addItem(tr("最近阅读", self.language), ("recent", ""))
        self.narrow_scope.addItem(tr("所有书籍", self.language), ("all", ""))
        for name in targets:
            label = tr("未分类", self.language) if name == UNCLASSIFIED else name
            self.narrow_scope.addItem(label, ("category", name))

        if self._scope_kind == "category":
            row = next(
                (i for i in range(self.category_list.count())
                 if self.category_list.item(i).data(Qt.ItemDataRole.UserRole) == self._active_category),
                0,
            )
            self.category_list.setCurrentRow(row)
        else:
            self.category_list.setCurrentRow(-1)

        self._sync_narrow_scope_selection()
        self._update_category_actions()
        self._refresh_rail_icons()
        self._syncing_scope = False
        self._populate_book_view()

    def _filtered_records(self):
        if self._scope_kind == "recent":
            rows = [r for r in self.records if r.get("last_read")]
        elif self._scope_kind == "category" and hasattr(self, "_category_for_path"):
            rows = [r for r in self.records if self._category_for_path(r["path"]) == self._active_category]
        else:
            rows = list(self.records)

        query = self.search_input.text().strip().casefold()
        if query:
            rows = [
                r for r in rows
                if query in display_title_for_record(r).casefold()
                or query in str(r.get("author", "")).casefold()
            ]

        if self._scope_kind == "recent" and self._sort_mode == "recent":
            rows.sort(key=lambda r: r.get("last_read", ""), reverse=True)
            return rows[:100]
        if self._sort_mode == "recent":
            rows.sort(key=lambda r: (r.get("last_read", ""), display_title_for_record(r).casefold()), reverse=True)
        elif self._sort_mode == "progress":
            rows.sort(
                key=lambda r: (
                    float(r["reading_progress"]) if type(r.get("reading_progress")) in (int, float) and isfinite(r["reading_progress"]) else -1.0,
                    display_title_for_record(r).casefold(),
                ),
                reverse=True,
            )
        else:
            rows.sort(key=lambda r: display_title_for_record(r).casefold())
        return rows

    def _populate_book_view(self):
        selected_id = None
        current = self.book_view.currentItem()
        if current is not None:
            selected_id = current.data(Qt.ItemDataRole.UserRole)

        rows = self._filtered_records()
        self.book_view.clear()
        restore_row = -1
        for idx, record in enumerate(rows):
            title = display_title_for_record(record)
            author = str(record.get("author", "") or "")
            file_path = record.get("path", "")
            fmt = {".epub": "EPUB", ".pdf": "PDF"}.get(Path(file_path).suffix.lower(), "TXT")
            chapter = record.get("reading_chapter")
            progress = record.get("reading_progress")
            has_progress = type(progress) in (int, float) and isfinite(progress)

            if has_progress:
                clamped = max(0.0, min(1.0, float(progress)))
                percentage = f"{clamped:.0%}"
                list_progress_label = tr("全书 {progress}", self.language, progress=percentage)
                grid_progress_label = tr("已读完", self.language) if clamped >= 0.995 else (percentage if clamped > 0 else tr("尚未开始阅读", self.language))
            else:
                clamped = 0.0
                percentage = "0%"
                list_progress_label = ""
                grid_progress_label = tr("尚未开始阅读", self.language)

            if isinstance(chapter, str) and chapter.strip():
                detail = chapter.strip()
            elif record.get("_has_saved_position") or record.get("last_read") or (has_progress and clamped > 0):
                detail = tr("打开书籍后更新进度", self.language)
            else:
                detail = tr("尚未开始阅读", self.language)
                if not has_progress:
                    list_progress_label = tr("全书 {progress}", self.language, progress=percentage)

            summary = " · ".join(part for part in (detail, list_progress_label) if part)
            item = QListWidgetItem(f"{title}\n{summary}")
            if self._view_mode == "grid":
                item.setSizeHint(BookItemDelegate.GRID_CARD_SIZE)
            else:
                item.setSizeHint(QSize(0, BookItemDelegate.LIST_ROW_HEIGHT))

            item.setData(Qt.ItemDataRole.UserRole, record.get("id", ""))
            item.setData(_BOOK_TITLE_ROLE, title)
            item.setData(_BOOK_DETAIL_ROLE, detail)
            item.setData(_BOOK_PROGRESS_ROLE, grid_progress_label if self._view_mode == "grid" else list_progress_label)
            item.setData(_BOOK_PROGRESS_FRACTION_ROLE, clamped)
            item.setData(_BOOK_AUTHOR_ROLE, author)
            item.setData(_BOOK_FORMAT_ROLE, fmt)
            item.setData(_BOOK_PATH_ROLE, file_path)
            if author:
                item.setToolTip(f"{title}\n{author}\n{summary}\n{file_path}")
            else:
                item.setToolTip(f"{title}\n{summary}\n{file_path}")
            self.book_view.addItem(item)
            if selected_id and record.get("id") == selected_id:
                restore_row = idx

        if restore_row >= 0:
            self.book_view.setCurrentRow(restore_row)

        if self._scope_kind == "recent":
            self.footer_count.setText(tr("最近阅读 · 共 {count} 本", self.language, count=len(rows)))
        elif self._scope_kind == "all":
            self.footer_count.setText(tr("所有书籍 · 共 {count} 本", self.language, count=len(rows)))
        else:
            cat_name = tr("未分类", self.language) if self._active_category == UNCLASSIFIED else self._active_category
            self.footer_count.setText(f"{cat_name} · {tr('共 {count} 本书', self.language, count=len(rows))}")

    def _open_item(self, item):
        book_id = item.data(Qt.ItemDataRole.UserRole)
        if book_id:
            self.open_requested.emit(book_id)

    def _show_book_context_menu(self, widget, point):
        item = widget.itemAt(point)
        if item is None:
            return
        book_id = item.data(Qt.ItemDataRole.UserRole)
        if not book_id:
            return
        menu = QMenu(widget)
        open_action = menu.addAction(tr("继续阅读", self.language))
        rename_action = menu.addAction(tr("修改显示书名", self.language))
        menu.addSeparator()
        move_menu = menu.addMenu(tr("移动到分类", self.language))
        record = next((row for row in self.records if row.get("id") == book_id), None)
        current_category = (
            self._category_for_path(record["path"])
            if record is not None and hasattr(self, "_category_for_path")
            else None
        )
        move_actions = {}
        for category in (UNCLASSIFIED, *self.categories):
            label = tr("未分类", self.language) if category == UNCLASSIFIED else category
            action = move_menu.addAction(label)
            action.setEnabled(category != current_category)
            move_actions[action] = category
        menu.addSeparator()
        remove_action = menu.addAction(tr("从书架移除", self.language))

        selected_action = menu.exec(widget.mapToGlobal(point))
        if selected_action == open_action:
            self.open_requested.emit(book_id)
        elif selected_action == rename_action:
            self.rename_requested.emit(book_id)
        elif selected_action in move_actions:
            selected_ids = [
                sel.data(Qt.ItemDataRole.UserRole)
                for sel in widget.selectedItems()
                if sel.data(Qt.ItemDataRole.UserRole)
            ]
            targets = selected_ids if book_id in selected_ids and len(selected_ids) > 1 else [book_id]
            self.move_books_requested.emit(targets, move_actions[selected_action])
        elif selected_action == remove_action:
            self.remove_requested.emit(book_id)

    def _rename_selected_category(self):
        if self._scope_kind == "category" and self._active_category in self.categories:
            self.rename_category_requested.emit(self._active_category)

    def _delete_selected_category(self):
        if self._scope_kind == "category" and self._active_category != UNCLASSIFIED:
            self.delete_category_requested.emit(self._active_category)
