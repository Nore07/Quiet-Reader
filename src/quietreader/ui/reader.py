from bisect import bisect_left, bisect_right
import math

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QPointF, QRectF, QSizeF, Qt, QTimer, Signal, QUrl
from PySide6.QtGui import (
    QAbstractTextDocumentLayout, QColor, QFont, QGuiApplication, QKeySequence,
    QPainter, QPalette, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument, QTextImageFormat, QImageReader,
)
from PySide6.QtWidgets import QMenu, QWidget

from quietreader.domain.models import Settings
from quietreader.ui.theme import colors
from quietreader.i18n import tr


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

    _MAX_SECTION_PAGE_CACHE = 64

    def __init__(self, parent=None):
        super().__init__(parent)
        self._resize_reflow_timer = QTimer(self)
        self._resize_reflow_timer.setSingleShot(True)
        self._resize_reflow_timer.setInterval(160)
        self._resize_reflow_timer.timeout.connect(self.reflow)
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
        self.reflow()

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
        self._resize_reflow_timer.stop()
        self.settings = settings
        self.reflow()

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

    def reflow(self):
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
        self.page = min(self.page_count - 1, max(0, bisect_right(self.page_offsets, self.offset) - 1))
        self.update()
        self.reflowed.emit()

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
        self.page = max(0, min(self.page_count - 1, page))
        self.offset = self.page_offsets[self.page]
        self.selection.clearSelection()
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_reflow_timer.start()

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if not delta:
            event.ignore()
            return
        self.turned.emit(-1 if delta > 0 else 1)
        event.accept()

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
        if not self.focus_mode and self.search_matches:
            win_start_page = max(0, self.page - 1)
            win_start = self.page_offsets[win_start_page]
            win_end = (
                self.page_offsets[self.page + 2]
                if self.page + 2 < self.page_count
                else len(self.text)
            )
            first_idx = max(0, bisect_right(self._search_match_starts, win_start) - 1)
            paper_hex = c.get("paper", "").lower()
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

    def _hit(self, point):
        p = QPointF(point.x() - self.settings.padding, point.y() - self.settings.padding + self.page * self.page_height)
        return max(0, self.document.documentLayout().hitTest(p, Qt.HitTestAccuracy.FuzzyHit))

    def mousePressEvent(self, event):
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
            self.selection.setPosition(self._hit(event.position()), QTextCursor.MoveMode.KeepAnchor)
            self.update()
        else:
            hit = self._offset_map.to_python(self._hit(event.position()))
            clickable = any(link.start <= hit < link.end for link in self.links)
            self.setCursor(Qt.CursorShape.PointingHandCursor if clickable else Qt.CursorShape.ArrowCursor)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        action = menu.addAction(tr("复制选中文字", self.settings.language), self.copy)
        action.setEnabled(self.selection.hasSelection())
        if self.focus_mode:
            menu.addAction(tr("重新采样背景色", self.settings.language), self.resample_requested.emit)
            menu.addAction(tr("退出专注模式", self.settings.language), self.exit_focus.emit)
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
        elif event.key() == Qt.Key.Key_Escape and self.focus_mode:
            self.exit_focus.emit()
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
