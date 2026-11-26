from bisect import bisect_left, bisect_right
import math
import re

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QPoint, QPointF, QRect, QRectF, QSizeF, Qt, QTimer, Signal, QUrl
from PySide6.QtGui import (
    QAbstractTextDocumentLayout, QColor, QFont, QGuiApplication, QKeySequence,
    QPainter, QPalette, QPen, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument, QTextImageFormat, QImageReader,
)
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from quietreader.domain.models import Settings
from quietreader.persistence.store import MAX_TERM_TEXT
from quietreader.ui.term_notes import compile_term_matcher, find_term_spans
from quietreader.ui.theme import colors
from quietreader.i18n import tr
from quietreader.ui.wheel_navigation import WheelPageAccumulator


def qt_offset(text: str, python_offset: int) -> int:
    return len(text[:python_offset].encode("utf-16-le")) // 2


def python_offset(text: str, utf16_offset: int) -> int:
    return len(text.encode("utf-16-le")[:utf16_offset * 2].decode("utf-16-le", errors="ignore"))


class _TextOffsetMap:
    """Fast cached bidirectional Python <-> UTF-16 offset mapper for a single text string."""

    __slots__ = ("_py_len", "_u16_len", "_bmp_only", "_astral_py", "_astral_mid_u16")

    def __init__(self, text: str):
        self._py_len = len(text)
        if not text or text.isascii():
            self._u16_len = self._py_len
            self._bmp_only = True
            self._astral_py = ()
            self._astral_mid_u16 = ()
            return
        u16_len = len(text.encode("utf-16-le")) // 2
        self._u16_len = u16_len
        if u16_len == self._py_len:
            self._bmp_only = True
            self._astral_py = ()
            self._astral_mid_u16 = ()
        else:
            self._bmp_only = False
            astral_py = [idx for idx, ch in enumerate(text) if ord(ch) > 0xFFFF]
            self._astral_py = astral_py
            self._astral_mid_u16 = [pos + idx + 1 for idx, pos in enumerate(astral_py)]

    def to_qt(self, python_pos: int) -> int:
        if python_pos <= 0:
            return 0
        if python_pos >= self._py_len:
            return self._u16_len
        if self._bmp_only:
            return python_pos
        return python_pos + bisect_left(self._astral_py, python_pos)

    def to_python(self, utf16_pos: int) -> int:
        if utf16_pos <= 0:
            return 0
        if utf16_pos >= self._u16_len:
            return self._py_len
        if self._bmp_only:
            return utf16_pos
        return utf16_pos - bisect_right(self._astral_mid_u16, utf16_pos)


class PageView(QWidget):
    turned = Signal(int)
    reflowed = Signal()
    link_activated = Signal(object)
    chapter_turned = Signal(int)
    exit_focus = Signal()
    focus_requested = Signal()
    resample_requested = Signal()
    visibility_requested = Signal()
    emergency_requested = Signal()
    term_hovered = Signal(object, object)
    term_hover_cleared = Signal()
    term_note_requested = Signal(str)
    term_search_requested = Signal(str)

    _MAX_SECTION_PAGE_CACHE = 64
    _REFLOW_ANCHOR_COOLDOWN_MS = 5000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._resize_reflow_timer = QTimer(self)
        self._resize_reflow_timer.setSingleShot(True)
        self._resize_reflow_timer.setInterval(160)
        self._resize_reflow_timer.timeout.connect(self.reflow)
        self._reflow_anchor_marker_timer = QTimer(self)
        self._reflow_anchor_marker_timer.setSingleShot(True)
        self._reflow_anchor_marker_timer.timeout.connect(self._clear_reflow_anchor_marker)
        self._reflow_anchor_marker_offset: int | None = None
        self._reflow_anchor_marker_rects: list[QRectF] = []
        self._reflow_anchor_marker_status: str | None = None
        self._pending_reflow_anchor: int | None = None
        self._layout_size: tuple[int, int] | None = None
        self.settings = Settings()
        self.focus_mode = False
        self.focus_background_override = ""
        self.text = ""
        self._offset_map = _TextOffsetMap("")
        self.images = {}
        self.image_sizes = {}
        self.links = []
        self.formats = []
        self.search_matches: list[tuple[int, int]] = []
        self._search_match_starts: list[int] = []
        self.active_search_match: tuple[int, int] | None = None
        self._term_notes: list[dict] = []
        self._term_pattern: re.Pattern | None = None
        self._term_lookup: dict[str, dict] = {}
        self._term_spans: list[tuple[int, int, dict]] = []
        self._term_span_starts: list[int] = []
        self._pending_hover: tuple[int, int, dict, QRect] | None = None
        self._active_hover: tuple[int, int, dict] | None = None
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(140)
        self._hover_timer.timeout.connect(self._fire_term_hover)
        self._section_page_cache: dict[tuple, tuple[int, ...]] = {}
        self.offset = 0
        self.page = 0
        self.page_count = 1
        self.page_offsets = [0]
        self.document = QTextDocument(self)
        self.selection = QTextCursor(self.document)
        self.page_height = 400
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("阅读正文")
        self.setMinimumSize(160, 140)
        self.setMouseTracking(True)
        self._wheel_page_accumulator = WheelPageAccumulator(self._emit_wheel_turn, self)

    @staticmethod
    def _decode_section_images(document: QTextDocument, images, parent=None):
        loaded = {}
        for position, payload in (images or {}).items():
            data = QBuffer(parent)
            data.setData(QByteArray(payload))
            data.open(QIODevice.OpenModeFlag.ReadOnly)
            reader = QImageReader(data)
            size = reader.size()
            if not size.isValid() or size.width() * size.height() > 32000000:
                data.deleteLater()
                raise ValueError("Unsupported or oversized book image")
            reader.setAutoTransform(True)
            budget_edge = int(math.sqrt(16000000 / max(1, len(images or {}))))
            edge = min(2000, max(64, budget_edge))
            reader.setScaledSize(size.scaled(edge, edge, Qt.AspectRatioMode.KeepAspectRatio))
            image = reader.read()
            data.deleteLater()
            if image.isNull():
                raise ValueError("Cannot decode book image")
            name = QUrl(f"quietreader-image:{position}")
            document.addResource(QTextDocument.ResourceType.ImageResource, name, image)
            loaded[position] = (name, image.size())
        return loaded

    def set_text(self, text: str, offset=0, images=None, links=None, formats=None):
        self._resize_reflow_timer.stop()
        self._clear_reflow_anchor_marker()
        self._pending_reflow_anchor = None
        self.clear_term_hover()
        self.text = text
        self._offset_map = _TextOffsetMap(text)
        self.offset = max(0, min(len(text), offset))
        previous = self.document
        self.document = QTextDocument(self)
        previous.deleteLater()
        self.document.setPlainText(text)
        self.image_sizes = {}
        self.links = list(links or [])
        self.formats = list(formats or [])
        self.search_matches = []
        self._search_match_starts = []
        self.active_search_match = None
        self.images = self._decode_section_images(self.document, images, self)
        self.selection = QTextCursor(self.document)
        self._rebuild_term_spans()
        self.reflow(anchor_offset=self.offset)

    def set_term_notes(self, records):
        """Compile a single longest-first regex and precompute section term spans."""
        self.clear_term_hover()
        self._term_notes, self._term_pattern, self._term_lookup = compile_term_matcher(records)
        self._rebuild_term_spans()
        self.update()

    def _rebuild_term_spans(self):
        spans = find_term_spans(self.text, self._term_pattern, self._term_lookup)
        self._term_spans = spans
        self._term_span_starts = [span[0] for span in spans]

    def clear_term_hover(self):
        had = self._hover_timer.isActive() or self._pending_hover is not None or self._active_hover is not None
        self._hover_timer.stop()
        self._pending_hover = None
        self._active_hover = None
        if had:
            self.term_hover_cleared.emit()

    def _has_term_hover(self) -> bool:
        return self._active_hover is not None or self._pending_hover is not None or self._hover_timer.isActive()

    def _fire_term_hover(self):
        if self._pending_hover is None or not self.isVisible():
            return
        if QApplication.mouseButtons() & Qt.MouseButton.LeftButton:
            self.clear_term_hover()
            return
        start, end, record, anchor_rect = self._pending_hover
        self._pending_hover = None
        self._active_hover = (start, end, record)
        self.term_hovered.emit(record, anchor_rect)

    def _term_span_at(self, python_pos: int):
        if not self._term_spans or python_pos < 0:
            return None
        idx = bisect_right(self._term_span_starts, python_pos) - 1
        if idx >= 0:
            start, end, record = self._term_spans[idx]
            if start <= python_pos < end:
                return start, end, record
        return None

    def _span_widget_rects(self, start_py: int, end_py: int) -> list[QRectF]:
        if not self.text or end_py <= start_py:
            return []
        q_start = self._offset_map.to_qt(start_py)
        q_end = self._offset_map.to_qt(end_py)
        if q_end <= q_start:
            return []
        pad = self.settings.padding
        page_top = self.page * self.page_height
        page_bottom = page_top + self.page_height
        layout = self.document.documentLayout()
        rects: list[QRectF] = []
        block = self.document.findBlock(q_start)
        while block.isValid() and block.position() < q_end:
            block_pos = block.position()
            block_text_len = max(0, block.length() - 1)
            local_start = max(0, q_start - block_pos)
            local_end = min(block_text_len, q_end - block_pos)
            if local_end > local_start:
                block_rect = layout.blockBoundingRect(block)
                text_layout = block.layout()
                for line_idx in range(text_layout.lineCount()):
                    line = text_layout.lineAt(line_idx)
                    line_start = line.textStart()
                    line_end = line_start + line.textLength()
                    ov_start = max(local_start, line_start)
                    ov_end = min(local_end, line_end)
                    if ov_end <= ov_start:
                        continue
                    doc_y = block_rect.top() + line.y()
                    line_h = line.height()
                    if doc_y + line_h <= page_top or doc_y >= page_bottom:
                        continue
                    x1 = line.cursorToX(ov_start)[0]
                    x2 = line.cursorToX(ov_end)[0]
                    left, right = (x1, x2) if x1 <= x2 else (x2, x1)
                    rects.append(QRectF(
                        left + pad,
                        doc_y - page_top + pad,
                        max(2.0, right - left),
                        max(1.0, line_h),
                    ))
            block = block.next()
        return rects

    def term_span_rect(self, start_py: int, end_py: int) -> QRect | None:
        """Return the widget-local bounding rect of a visible term span on the current page."""
        rects = self._span_widget_rects(start_py, end_py)
        return rects[0].toAlignedRect() if rects else None

    def _term_hit_at_point(self, point: QPointF):
        if not self.text or not self._term_spans:
            return None
        pad = self.settings.padding
        if (
            point.x() < pad
            or point.x() > self.width() - pad
            or point.y() < pad
            or point.y() > pad + self.page_height
        ):
            return None
        doc_pt = QPointF(point.x() - pad, point.y() - pad + self.page * self.page_height)
        raw_hit = self.document.documentLayout().hitTest(doc_pt, Qt.HitTestAccuracy.ExactHit)
        if raw_hit < 0:
            return None
        py_hit = self._offset_map.to_python(raw_hit)
        candidates = []
        primary = self._term_span_at(py_hit)
        if primary is not None:
            candidates.append(primary)
        if py_hit > 0:
            previous = self._term_span_at(py_hit - 1)
            if previous is not None and previous != primary:
                candidates.append(previous)
        for start, end, record in candidates:
            for rect in self._span_widget_rects(start, end):
                if rect.adjusted(-1.5, -1.5, 1.5, 1.5).contains(point):
                    return start, end, record, rect.toAlignedRect()
        return None

    def set_search_highlights(self, matches: list[tuple[int, int]], active_match: tuple[int, int] | None = None):
        self.search_matches = list(matches)
        self._search_match_starts = [item[0] for item in self.search_matches]
        self.active_search_match = active_match
        self.update()

    def clear_section_cache(self):
        self._section_page_cache.clear()

    def _active_focus_background(self) -> str:
        if not self.focus_mode:
            return ""
        return self.focus_background_override or self.settings.background_color

    def apply_settings(self, settings):
        layout_changed = any(
            getattr(self.settings, name) != getattr(settings, name)
            for name in (
                "font_family",
                "font_size",
                "line_height",
                "letter_spacing",
                "paragraph_spacing",
                "padding",
            )
        )
        pending_anchor = self._pending_reflow_anchor
        marked_anchor = self._active_reflow_anchor()
        if pending_anchor is not None:
            anchor_offset = pending_anchor
            align_offset = True
        elif marked_anchor is not None:
            anchor_offset = marked_anchor
            align_offset = True
        elif layout_changed:
            anchor_offset = self._current_page_start_offset()
            align_offset = True
        else:
            anchor_offset = self.offset
            align_offset = False
        self._resize_reflow_timer.stop()
        self.settings = settings
        self.reflow(anchor_offset=anchor_offset, align_offset=align_offset)

    def _layout_document_pages(
        self,
        document: QTextDocument,
        offset_map: _TextOffsetMap,
        loaded_images: dict,
        formats: list,
        links: list,
        width: int,
        page_height: int,
    ) -> tuple[list[int], dict]:
        font = QFont(self.settings.font_family)
        font.setPixelSize(self.settings.font_size)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, self.settings.letter_spacing)
        document.setDefaultFont(font)
        document.setDocumentMargin(0)
        cursor = QTextCursor(document)
        cursor.select(QTextCursor.SelectionType.Document)
        block = QTextBlockFormat()
        block.setLineHeight(self.settings.line_height * 100, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        block.setBottomMargin(self.settings.paragraph_spacing)
        cursor.mergeBlockFormat(block)
        document.setPageSize(QSizeF(width, page_height))
        image_sizes = {}
        for position, (name, size) in loaded_images.items():
            scaled = QSizeF(size).scaled(
                QSizeF(width, max(20, page_height - self.settings.font_size * 2)),
                Qt.AspectRatioMode.KeepAspectRatio,
            )
            fmt = QTextImageFormat()
            fmt.setName(name.toString())
            fmt.setWidth(scaled.width())
            fmt.setHeight(scaled.height())
            cursor = QTextCursor(document)
            cursor.setPosition(offset_map.to_qt(position))
            cursor.movePosition(QTextCursor.MoveOperation.NextCharacter, QTextCursor.MoveMode.KeepAnchor)
            cursor.insertImage(fmt)
            image_block = QTextBlockFormat()
            image_block.setLineHeight(100, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            image_block.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            cursor.mergeBlockFormat(image_block)
            image_sizes[position] = scaled
        c = colors(self.settings.theme, self._active_focus_background())
        if self.settings.text_color:
            c["ink"] = self.settings.text_color
        heading_scale = {"1": 1.8, "2": 1.55, "3": 1.35, "4": 1.2, "5": 1.1, "6": 1.0}
        # Apply heading sizing first so nested inline emphasis can refine it.
        for item in sorted(formats, key=lambda value: value.kind != "heading"):
            if item.end <= item.start:
                continue
            cursor = QTextCursor(document)
            cursor.setPosition(offset_map.to_qt(item.start))
            cursor.setPosition(offset_map.to_qt(item.end), QTextCursor.MoveMode.KeepAnchor)
            char_format = QTextCharFormat()
            kind = item.kind
            if kind == "bold":
                char_format.setFontWeight(QFont.Weight.Bold)
            elif kind == "italic":
                char_format.setFontItalic(True)
            elif kind == "strike":
                char_format.setFontStrikeOut(True)
            elif kind == "underline":
                char_format.setFontUnderline(True)
            elif kind == "highlight":
                char_format.setBackground(QColor(c["selected"]))
            elif kind == "code":
                code_font = cursor.charFormat().font()
                code_font.setFamily("Consolas")
                code_font.setStyleHint(QFont.StyleHint.Monospace)
                char_format.setFont(code_font)
            elif kind in ("superscript", "subscript"):
                vertical = (
                    QTextCharFormat.VerticalAlignment.AlignSuperScript
                    if kind == "superscript"
                    else QTextCharFormat.VerticalAlignment.AlignSubScript
                )
                char_format.setVerticalAlignment(vertical)
            elif kind == "heading":
                heading_font = QFont(self.settings.font_family)
                heading_font.setPixelSize(max(1, round(self.settings.font_size * heading_scale.get(item.value, 1.0))))
                heading_font.setWeight(QFont.Weight.Bold)
                char_format.setFont(heading_font)
            elif kind == "quote":
                char_format.setFontItalic(True)
                block_format = QTextBlockFormat()
                block_format.setLeftMargin(max(14, self.settings.font_size * 1.2))
                cursor.mergeBlockFormat(block_format)
            elif kind == "list_item":
                block_format = QTextBlockFormat()
                block_format.setLeftMargin(int(item.value or 0) * self.settings.font_size * 0.8)
                cursor.mergeBlockFormat(block_format)
            elif kind == "rule":
                char_format.setForeground(QColor(c["muted"]))
                block_format = QTextBlockFormat()
                block_format.setAlignment(Qt.AlignmentFlag.AlignHCenter)
                cursor.mergeBlockFormat(block_format)
            if kind in ("bold", "italic", "strike", "underline", "highlight", "code", "superscript", "subscript", "heading", "quote", "rule"):
                cursor.mergeCharFormat(char_format)
        for link in links:
            if link.end <= link.start:
                continue
            cursor = QTextCursor(document)
            cursor.setPosition(offset_map.to_qt(link.start))
            cursor.setPosition(offset_map.to_qt(link.end), QTextCursor.MoveMode.KeepAnchor)
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(c["accent"]))
            fmt.setFontUnderline(True)
            cursor.mergeCharFormat(fmt)
        page_count = max(1, document.pageCount())
        offsets = [None] * page_count
        offsets[0] = 0
        current = document.begin()
        layout = document.documentLayout()
        while current.isValid():
            block_y = layout.blockBoundingRect(current).top()
            text_layout = current.layout()
            for i in range(text_layout.lineCount()):
                line = text_layout.lineAt(i)
                page = min(page_count - 1, int((block_y + line.y() + 0.1) // page_height))
                if offsets[page] is None:
                    offsets[page] = offset_map.to_python(current.position() + line.textStart())
            current = current.next()
        # Qt can count a final margin-only page after a full-height illustration.
        while len(offsets) > 1 and offsets[-1] is None:
            offsets.pop()
        page_offsets = [
            value if value is not None else offset_map.to_python(
                max(0, layout.hitTest(QPointF(0, page * page_height + 1), Qt.HitTestAccuracy.FuzzyHit))
            )
            for page, value in enumerate(offsets)
        ]
        return page_offsets, image_sizes

    def _current_page_start_offset(self) -> int:
        if not self.page_offsets:
            return self.offset
        page = min(max(0, self.page), len(self.page_offsets) - 1)
        return self.page_offsets[page]

    def _active_reflow_anchor(self) -> int | None:
        if self._reflow_anchor_marker_timer.isActive():
            return self._reflow_anchor_marker_offset
        return None

    def _clear_reflow_anchor_marker(self):
        self._reflow_anchor_marker_timer.stop()
        self._reflow_anchor_marker_offset = None
        self._reflow_anchor_marker_rects = []
        self._reflow_anchor_marker_status = None
        self.update()

    def _show_reflow_anchor_marker(self, anchor_offset: int):
        self._reflow_anchor_marker_offset = anchor_offset
        self._reflow_anchor_marker_rects = []
        self._reflow_anchor_marker_status = None

        page_start = self.page_offsets[self.page]
        page_end = self.page_offsets[self.page + 1] if self.page + 1 < self.page_count else len(self.text)
        if not page_start <= anchor_offset < page_end:
            self._reflow_anchor_marker_status = "锚点不在本页"
        else:
            marker_start = anchor_offset
            while marker_start < page_end and self.text[marker_start].isspace():
                marker_start += 1
            if marker_start >= page_end:
                self._reflow_anchor_marker_status = "页首内容未显示"
            else:
                marker_end = marker_start
                while (
                    marker_end < page_end
                    and marker_end - marker_start < 8
                    and not self.text[marker_end].isspace()
                ):
                    marker_end += 1
                if marker_end == marker_start:
                    marker_end = min(page_end, marker_start + 1)
                self._reflow_anchor_marker_rects = self._span_widget_rects(marker_start, marker_end)
                if not self._reflow_anchor_marker_rects:
                    self._reflow_anchor_marker_status = "页首内容未显示"

        self._reflow_anchor_marker_timer.start(self._REFLOW_ANCHOR_COOLDOWN_MS)
        self.update()

    def _draw_reflow_anchor_marker(self, painter: QPainter, palette: dict):
        if self._reflow_anchor_marker_offset is None:
            return
        pad = self.settings.padding
        page_top = self.page * self.page_height
        if self._reflow_anchor_marker_rects:
            paper = QColor(palette.get("paper", "#ffffff"))
            marker_color = QColor("#9e2a2b" if paper.lightness() >= 128 else "#ffd166")
            marker_width = max(2.2, round(self.settings.font_size * 0.12, 1))
            painter.save()
            painter.setPen(QPen(marker_color, marker_width, Qt.PenStyle.SolidLine))
            for rect in self._reflow_anchor_marker_rects:
                underline_y = rect.bottom() + page_top - pad - 2.0
                painter.drawLine(
                    QPointF(rect.left() - pad, underline_y),
                    QPointF(rect.right() - pad, underline_y),
                )
            painter.restore()
            return

        available_width = max(1, self.width() - pad * 2 - 16)
        status_rect = QRectF(8, page_top + 8, min(150, available_width), 24)
        painter.save()
        painter.fillRect(status_rect, QColor("#9e2a2b"))
        painter.setPen(QColor("#ffffff"))
        status_font = QFont(self.settings.font_family)
        status_font.setPixelSize(max(10, min(13, self.settings.font_size)))
        painter.setFont(status_font)
        painter.drawText(status_rect, Qt.AlignmentFlag.AlignCenter, self._reflow_anchor_marker_status or "页首内容未显示")
        painter.restore()

    def reflow(self, anchor_offset=None, *, align_offset=False):
        self.clear_term_hover()
        marked_anchor = self._active_reflow_anchor()
        show_anchor_marker = (
            align_offset
            or self._pending_reflow_anchor is not None
            or (anchor_offset is None and marked_anchor is not None)
        )
        if anchor_offset is None:
            if self._pending_reflow_anchor is not None:
                anchor_offset = self._pending_reflow_anchor
                align_offset = True
            elif marked_anchor is not None:
                anchor_offset = marked_anchor
                align_offset = True
            else:
                anchor_offset = self.offset
        anchor_offset = max(0, min(len(self.text), int(anchor_offset)))
        self._pending_reflow_anchor = None
        padding = self.settings.padding
        width = max(80, self.width() - padding * 2)
        self.page_height = max(60, self.height() - padding * 2)
        page_offsets, self.image_sizes = self._layout_document_pages(
            self.document,
            self._offset_map,
            self.images,
            self.formats,
            self.links,
            width,
            self.page_height,
        )
        self.page_count = len(page_offsets)
        self.page_offsets = page_offsets
        self.page = min(self.page_count - 1, max(0, bisect_right(self.page_offsets, anchor_offset) - 1))
        if align_offset:
            self.offset = self.page_offsets[self.page]
        if show_anchor_marker and self.text:
            self._show_reflow_anchor_marker(anchor_offset)
        self._layout_size = (width, self.page_height)
        self.update()
        self.reflowed.emit()

    def flush_pending_reflow(self):
        padding = self.settings.padding
        layout_size = (
            max(80, self.width() - padding * 2),
            max(60, self.height() - padding * 2),
        )
        if self._resize_reflow_timer.isActive() or self._layout_size != layout_size:
            if self._pending_reflow_anchor is None:
                marked_anchor = self._active_reflow_anchor()
                self._pending_reflow_anchor = (
                    marked_anchor if marked_anchor is not None else self._current_page_start_offset()
                )
            self._resize_reflow_timer.stop()
            self.reflow()

    def page_for_section_offset(
        self,
        section,
        offset: int,
        *,
        target_width: int | None = None,
        target_height: int | None = None,
    ) -> int:
        """Compute the exact 1-based page number for (section, offset) using reflow()'s metrics."""
        padding = self.settings.padding
        raw_w = self.width() if target_width is None else target_width
        raw_h = self.height() if target_height is None else target_height
        width = max(80, raw_w - padding * 2)
        page_height = max(60, raw_h - padding * 2)
        cache_key = (
            id(section),
            section.id,
            len(section.text),
            width,
            page_height,
            self.settings.font_family,
            self.settings.font_size,
            self.settings.line_height,
            self.settings.letter_spacing,
            self.settings.paragraph_spacing,
            self.settings.padding,
        )
        page_offsets = self._section_page_cache.get(cache_key)
        if page_offsets is None:
            doc = QTextDocument()
            doc.setPlainText(section.text)
            offset_map = _TextOffsetMap(section.text)
            try:
                loaded_images = self._decode_section_images(doc, section.images)
            except ValueError:
                loaded_images = {}
            computed_offsets, _ = self._layout_document_pages(
                doc,
                offset_map,
                loaded_images,
                list(section.formats or []),
                list(section.links or []),
                width,
                page_height,
            )
            page_offsets = tuple(computed_offsets)
            if len(self._section_page_cache) >= self._MAX_SECTION_PAGE_CACHE:
                self._section_page_cache.pop(next(iter(self._section_page_cache)))
            self._section_page_cache[cache_key] = page_offsets
        clamped = max(0, min(len(section.text), int(offset)))
        page_0 = min(len(page_offsets) - 1, max(0, bisect_right(page_offsets, clamped) - 1))
        return page_0 + 1

    def go_page(self, page):
        self.clear_term_hover()
        self._clear_reflow_anchor_marker()
        self.page = max(0, min(self.page_count - 1, page))
        self.offset = self.page_offsets[self.page]
        self.selection.clearSelection()
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._resize_reflow_timer.isActive():
            marked_anchor = self._active_reflow_anchor()
            self._pending_reflow_anchor = (
                marked_anchor if marked_anchor is not None else self._current_page_start_offset()
            )
        self._resize_reflow_timer.start()

    def _emit_wheel_turn(self, direction):
        self.clear_term_hover()
        self.turned.emit(direction)

    def _term_underline_pen(self, palette: dict) -> QPen:
        is_dark_paper = QColor(palette["paper"]).lightness() < 128
        if self.focus_mode:
            if palette.get("custom_background"):
                color = QColor(palette["ink"])
                color.setAlpha(190 if is_dark_paper else 180)
            else:
                color = QColor(palette["accent"])
                color.setAlpha(220 if is_dark_paper else 210)
        else:
            color = QColor(palette["accent"])
            color.setAlpha(238 if is_dark_paper else 230)
        width = max(1.6, round(self.settings.font_size * 0.085, 1))
        pen = QPen(color, width, Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([3.0, 2.2])
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        return pen

    def _draw_term_underlines(self, painter: QPainter, palette: dict, win_start: int, win_end: int):
        if not self._term_spans:
            return
        first_term = max(0, bisect_right(self._term_span_starts, win_start) - 1)
        doc_layout = self.document.documentLayout()
        page_top = self.page * self.page_height
        page_bottom = page_top + self.page_height
        painter.save()
        painter.setPen(self._term_underline_pen(palette))
        for idx in range(first_term, len(self._term_spans)):
            t_start, t_end, _record = self._term_spans[idx]
            if t_start > win_end:
                break
            if t_end <= t_start or t_end <= win_start:
                continue
            q_start = self._offset_map.to_qt(t_start)
            q_end = self._offset_map.to_qt(t_end)
            if q_end <= q_start:
                continue
            block = self.document.findBlock(q_start)
            while block.isValid() and block.position() < q_end:
                block_pos = block.position()
                block_text_len = max(0, block.length() - 1)
                local_start = max(0, q_start - block_pos)
                local_end = min(block_text_len, q_end - block_pos)
                if local_end > local_start:
                    block_rect = doc_layout.blockBoundingRect(block)
                    text_layout = block.layout()
                    for line_idx in range(text_layout.lineCount()):
                        line = text_layout.lineAt(line_idx)
                        line_start = line.textStart()
                        line_end = line_start + line.textLength()
                        ov_start = max(local_start, line_start)
                        ov_end = min(local_end, line_end)
                        if ov_end <= ov_start:
                            continue
                        line_top = block_rect.top() + line.y()
                        line_h = line.height()
                        if line_top + line_h <= page_top or line_top >= page_bottom:
                            continue
                        x1 = line.cursorToX(ov_start)[0]
                        x2 = line.cursorToX(ov_end)[0]
                        left, right = (x1, x2) if x1 <= x2 else (x2, x1)
                        if right - left < 1.0:
                            continue
                        baseline_y = line_top + line.ascent()
                        descent = line.descent()
                        underline_y = min(line_top + line_h - 1.5, baseline_y + max(4.0, descent + 2.0))
                        painter.drawLine(QPointF(left, underline_y), QPointF(right, underline_y))
                block = block.next()
        painter.restore()

    def wheelEvent(self, event):
        self._wheel_page_accumulator.handle(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        c = colors(self.settings.theme, self._active_focus_background())
        if self.settings.text_color:
            c["ink"] = self.settings.text_color
        painter.setPen(QColor(c["ink"]))
        if not self.text:
            if self.focus_mode:
                return
            box = QRectF(self.rect()).adjusted(25, 10, -25, -10)
            painter.setFont(QFont("Microsoft YaHei", 11))
            painter.setPen(QColor(c["muted"]))
            painter.drawText(box.adjusted(0, 0, 0, -130), Qt.AlignmentFlag.AlignCenter, tr("一 段 留 给 自 己 的 时 间", self.settings.language))
            painter.setFont(QFont("Microsoft YaHei", 23))
            painter.setPen(QColor(c["ink"]))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, tr("把喧闹\n留在书页之外。", self.settings.language))
            painter.setFont(QFont("Microsoft YaHei", 10))
            painter.setPen(QColor(c["muted"]))
            painter.drawText(box.adjusted(0, 160, 0, 0), Qt.AlignmentFlag.AlignCenter, tr("添加一本 TXT、EPUB 或 PDF\n文件与进度，只保存在这台电脑。", self.settings.language))
            return
        pad = self.settings.padding
        painter.setClipRect(QRectF(pad, pad, max(1, self.width() - 2 * pad), self.page_height))
        painter.translate(pad, pad - self.page * self.page_height)
        context = QAbstractTextDocumentLayout.PaintContext()
        context.clip = QRectF(0, self.page * self.page_height, self.document.pageSize().width(), self.page_height)
        context.palette.setColor(QPalette.ColorRole.Text, QColor(c["ink"]))
        selections = []
        win_start_page = max(0, self.page - 1)
        win_start = self.page_offsets[win_start_page]
        win_end = (
            self.page_offsets[self.page + 2]
            if self.page + 2 < self.page_count
            else len(self.text)
        )
        paper_hex = c.get("paper", "").lower()
        if not self.focus_mode and self.search_matches:
            first_idx = max(0, bisect_right(self._search_match_starts, win_start) - 1)
            passive_bg = QColor(
                "#5c4c26" if paper_hex == "#1d2522"
                else ("#e5c57b" if paper_hex == "#efe4cd" else "#f2dc96")
            )
            active_bg = QColor(c["accent"])
            active_fg = QColor(c.get("on_accent", c["paper"]))
            for idx in range(first_idx, len(self.search_matches)):
                m_start, m_end = self.search_matches[idx]
                if m_start > win_end:
                    break
                if m_end <= m_start:
                    continue
                cur = QTextCursor(self.document)
                cur.setPosition(self._offset_map.to_qt(m_start))
                cur.setPosition(self._offset_map.to_qt(m_end), QTextCursor.MoveMode.KeepAnchor)
                sel = QAbstractTextDocumentLayout.Selection()
                sel.cursor = cur
                fmt = QTextCharFormat()
                if self.active_search_match == (m_start, m_end):
                    fmt.setBackground(active_bg)
                    fmt.setForeground(active_fg)
                else:
                    fmt.setBackground(passive_bg)
                sel.format = fmt
                selections.append(sel)
        if self.selection.hasSelection():
            selected = QAbstractTextDocumentLayout.Selection()
            selected.cursor = self.selection
            selected.format = QTextCharFormat()
            selected.format.setBackground(QColor(c["selected"]))
            selections.append(selected)
        if selections:
            context.selections = selections
        self.document.documentLayout().draw(painter, context)
        if self._term_spans:
            self._draw_term_underlines(painter, c, win_start, win_end)
        self._draw_reflow_anchor_marker(painter, c)

    def _hit(self, point):
        p = QPointF(point.x() - self.settings.padding, point.y() - self.settings.padding + self.page * self.page_height)
        return max(0, self.document.documentLayout().hitTest(p, Qt.HitTestAccuracy.FuzzyHit))

    def mousePressEvent(self, event):
        self.clear_term_hover()
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus()
            hit = self._hit(event.position())
            python_hit = self._offset_map.to_python(hit)
            link = next((item for item in self.links if item.start <= python_hit < item.end), None)
            if link is not None:
                self.link_activated.emit(link)
                event.accept()
                return
            self.selection.setPosition(hit)
            self.update()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.clear_term_hover()
            self.selection.setPosition(self._hit(event.position()), QTextCursor.MoveMode.KeepAnchor)
            self.update()
        else:
            hit = self._offset_map.to_python(self._hit(event.position()))
            clickable = any(link.start <= hit < link.end for link in self.links)
            self.setCursor(Qt.CursorShape.PointingHandCursor if clickable else Qt.CursorShape.ArrowCursor)
            term_hit = self._term_hit_at_point(event.position())
            if term_hit is None:
                self.clear_term_hover()
            else:
                start, end, record, anchor_rect = term_hit
                if (
                    self._active_hover is not None
                    and self._active_hover[0] == start
                    and self._active_hover[1] == end
                    and self._active_hover[2] == record
                ):
                    return
                if self._active_hover is not None:
                    self._active_hover = None
                    self.term_hover_cleared.emit()
                same_pending = (
                    self._pending_hover is not None
                    and self._pending_hover[0] == start
                    and self._pending_hover[1] == end
                    and self._pending_hover[2] == record
                )
                self._pending_hover = (start, end, record, anchor_rect)
                if not same_pending or not self._hover_timer.isActive():
                    self._hover_timer.start()

    def leaveEvent(self, event):
        self.clear_term_hover()
        super().leaveEvent(event)

    def hideEvent(self, event):
        self.clear_term_hover()
        super().hideEvent(event)

    def build_context_menu(self, pos: QPoint) -> QMenu:
        self.clear_term_hover()
        lang = self.settings.language
        menu = QMenu(self)
        action = menu.addAction(tr("复制选中文字", lang), self.copy)
        action.setEnabled(self.selection.hasSelection())
        if self.text:
            if self.selection.hasSelection():
                raw_selected = self.selection.selectedText().replace("\u2029", "\n")
                collapsed = " ".join(raw_selected.replace("\x00", "").split())
                if 1 <= len(collapsed) <= MAX_TERM_TEXT:
                    exists = collapsed.casefold() in self._term_lookup
                    label = tr("编辑词条标注…" if exists else "添加词条标注…", lang)
                    menu.addSeparator()
                    menu.addAction(label, lambda t=collapsed: self.term_note_requested.emit(t))
            else:
                term_hit = self._term_hit_at_point(QPointF(pos))
                if term_hit is not None:
                    term = term_hit[2]["term"]
                    menu.addSeparator()
                    menu.addAction(
                        tr("编辑词条标注：“{term}”", lang, term=term),
                        lambda t=term: self.term_note_requested.emit(t),
                    )
                    if not self.focus_mode:
                        menu.addAction(
                            tr("在全书搜索：“{term}”", lang, term=term),
                            lambda t=term: self.term_search_requested.emit(t),
                        )
        if self.focus_mode:
            menu.addSeparator()
            menu.addAction(tr("重新采样背景色", lang), self.resample_requested.emit)
            menu.addAction(tr("退出专注模式", lang), self.exit_focus.emit)
        return menu

    def contextMenuEvent(self, event):
        menu = self.build_context_menu(event.pos())
        menu.exec(event.globalPos())

    def copy(self):
        QGuiApplication.clipboard().setText(self.selection.selectedText().replace("\u2029", "\n"))

    def keyPressEvent(self, event):
        sequence = QKeySequence(event.keyCombination()).toString(QKeySequence.SequenceFormat.PortableText)
        if sequence == self.settings.emergency_key:
            if not event.isAutoRepeat():
                self.emergency_requested.emit()
        elif sequence == self.settings.hotkey:
            if not event.isAutoRepeat():
                self.visibility_requested.emit()
        elif event.key() == Qt.Key.Key_Escape:
            if self._has_term_hover():
                self.clear_term_hover()
                if self.focus_mode:
                    self.exit_focus.emit()
                event.accept()
                return
            if self.focus_mode:
                self.exit_focus.emit()
                event.accept()
                return
            super().keyPressEvent(event)
        elif sequence == self.settings.focus_key:
            if not event.isAutoRepeat():
                self.focus_requested.emit()
        elif sequence == self.settings.focus_resample_key:
            if not event.isAutoRepeat():
                self.resample_requested.emit()
        elif event.matches(QKeySequence.StandardKey.Copy):
            self.copy()
        elif sequence == self.settings.next_key:
            self.turned.emit(1)
        elif sequence == self.settings.previous_key:
            self.turned.emit(-1)
        elif sequence == self.settings.next_chapter_key:
            self.chapter_turned.emit(1)
        elif sequence == self.settings.previous_chapter_key:
            self.chapter_turned.emit(-1)
        else:
            super().keyPressEvent(event)
