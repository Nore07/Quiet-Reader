"""Minimal status-bar-like reader surface used by Moyu mode."""

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QTextLayout, QTextOption
from PySide6.QtWidgets import QMenu, QWidget

from quietreader.i18n import tr
from quietreader.ui.theme import colors


_SENTENCE_ENDINGS = frozenset("。！？!?…；;.")
_CLOSING_MARKS = frozenset("\"'”’」』）》】〉〕）]}")
_ABBREVIATIONS = frozenset({"mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "e.g.", "i.e.", "vs.", "etc.", "u.s."})


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Return non-whitespace sentence ranges, retaining punctuation and quotes."""
    spans = []
    length = len(text)
    start = 0
    while start < length and text[start].isspace():
        start += 1

    index = start
    while index < length:
        char = text[index]
        if char in "\r\n\u2028\u2029":
            end = index
            if text[start:end].strip():
                spans.append((start, end))
            while index < length and text[index] in "\r\n\u2028\u2029 \t":
                index += 1
            start = index
            continue

        if char in _SENTENCE_ENDINGS:
            if char == ".":
                token = text[start:index + 1].rsplit(None, 1)[-1].casefold()
                is_decimal = index > 0 and index + 1 < length and text[index - 1].isdigit() and text[index + 1].isdigit()
                is_initial = len(token) == 2 and token[0].isalpha()
                if is_decimal or is_initial or token in _ABBREVIATIONS:
                    index += 1
                    continue
            end = index + 1
            while end < length and text[end] in _SENTENCE_ENDINGS:
                end += 1
            while end < length and text[end] in _CLOSING_MARKS:
                end += 1
            if text[start:end].strip():
                spans.append((start, end))
            index = end
            while index < length and text[index].isspace():
                index += 1
            start = index
            continue

        index += 1

    if start < length and text[start:length].strip():
        spans.append((start, length))
    return spans


def sentence_index_for_offset(spans: list[tuple[int, int]], offset: int) -> int:
    if not spans:
        return -1
    for index, (start, end) in enumerate(spans):
        if start <= offset < end:
            return index
        if offset < start:
            return index
    return len(spans) - 1


class MoyuStrip(QWidget):
    """A focus-preserving, one- or two-line strip with sentence navigation."""

    turn_requested = Signal(int)
    toggle_lines_requested = Signal()
    exit_requested = Signal()
    context_menu_opened = Signal()
    context_menu_closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sentence = ""
        self._line_count = 2
        self._language = "system"
        self._theme = "system"
        self._background_color = ""
        self._palette = colors("system")
        self._font_family = "Microsoft YaHei"
        self._drag_offset = None
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setAccessibleName(tr("摸鱼模式阅读条", self._language))
        self.setToolTip(tr("鼠标靠近时显示；离开后自动隐藏。滚轮逐句翻页；按住 Alt 拖动可调整位置。", self._language))
        self._sync_height()

    @property
    def line_count(self):
        return self._line_count

    def set_preferences(
        self, *, lines: int, theme: str, language: str, font_family: str, background_color="",
    ):
        self._line_count = 1 if lines == 1 else 2
        self._theme = theme
        self._language = language
        self._font_family = font_family
        self._background_color = background_color or ""
        self._palette = colors(theme, self._background_color)
        self.setAccessibleName(tr("摸鱼模式阅读条", language))
        self.setToolTip(tr("鼠标靠近时显示；离开后自动隐藏。滚轮逐句翻页；按住 Alt 拖动可调整位置。", language))
        self._sync_height()
        self.update()

    def set_background_color(self, background_color=""):
        background_color = background_color or ""
        if background_color == self._background_color:
            return
        self._background_color = background_color
        self._palette = colors(self._theme, background_color)
        self.update()

    def set_sentence(self, sentence: str):
        self._sentence = " ".join(
            sentence.replace("\ufffc", " ").replace("\u2028", " ").replace("\u2029", " ").split()
        )
        self.update()

    def set_hotkey_tooltip(self, message: str):
        hint = tr("鼠标靠近时显示；离开后自动隐藏。滚轮逐句翻页；按住 Alt 拖动可调整位置。", self._language)
        self.setToolTip(f"{hint}\n{message}" if message else hint)

    def _sync_height(self):
        self.setFixedHeight(26 if self._line_count == 1 else 42)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        background = self._palette["paper"] if self._background_color else self._palette.get("side", self._palette["paper"])
        painter.fillRect(self.rect(), QColor(background))
        painter.setPen(QColor(self._palette["line"]))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

        text_rect = self.rect().adjusted(12, 3, -12, -3)
        font = QFont(self._font_family)
        font.setPixelSize(12)
        painter.setFont(font)
        painter.setPen(QColor(self._palette.get("ink_secondary", self._palette["muted"])))
        if not self._sentence:
            return

        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout = QTextLayout(self._sentence, font)
        layout.setTextOption(option)
        ranges = []
        layout.beginLayout()
        for _ in range(self._line_count):
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(text_rect.width())
            ranges.append((line.textStart(), line.textLength()))
        layout.endLayout()

        metrics = QFontMetrics(font)
        total_height = len(ranges) * metrics.height()
        baseline = text_rect.top() + max(0, (text_rect.height() - total_height) / 2) + metrics.ascent()
        for row, (start, count) in enumerate(ranges):
            end = start + count
            segment = self._sentence[start:end].strip()
            has_more = row == len(ranges) - 1 and bool(self._sentence[end:].strip())
            if has_more:
                segment = metrics.elidedText(
                    segment + "…", Qt.TextElideMode.ElideRight, int(text_rect.width()),
                )
            painter.drawText(QPointF(text_rect.left(), baseline + row * metrics.height()), segment)

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if not delta:
            event.ignore()
            return
        self.turn_requested.emit(-1 if delta > 0 else 1)
        event.accept()

    def mousePressEvent(self, event):
        if (
            event.button() == Qt.MouseButton.LeftButton
            and event.modifiers() & Qt.KeyboardModifier.AltModifier
        ):
            global_position = event.globalPosition().toPoint()
            self._drag_offset = global_position - self.window().frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction(tr("上一句", self._language), lambda: self.turn_requested.emit(-1))
        menu.addAction(tr("下一句", self._language), lambda: self.turn_requested.emit(1))
        menu.addAction(tr("切换单行 / 两行", self._language), self.toggle_lines_requested.emit)
        menu.addSeparator()
        menu.addAction(tr("退出摸鱼模式", self._language), self.exit_requested.emit)
        self.context_menu_opened.emit()
        try:
            menu.exec(event.globalPos())
        finally:
            self.context_menu_closed.emit()
