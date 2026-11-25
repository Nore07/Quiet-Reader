"""Minimal status-bar-like reader surface used by Moyu mode."""

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPen, QTextLayout, QTextOption
from PySide6.QtWidgets import QMenu, QSizePolicy, QWidget

from quietreader.i18n import tr
from quietreader.ui.theme import colors
from quietreader.ui.wheel_navigation import WheelPageAccumulator


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

    SINGLE_LINE_HEIGHT = 30
    DOUBLE_LINE_HEIGHT = 42
    MAXIMUM_HEIGHT = 88
    EDGE_MARGIN = 7

    turn_requested = Signal(int)
    toggle_lines_requested = Signal()
    exit_requested = Signal()
    context_menu_opened = Signal()
    context_menu_closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._wheel_page_accumulator = WheelPageAccumulator(self.turn_requested.emit, self)
        self._source_sentence = ""
        self._sentence = ""
        self._line_count = 1
        self._language = "system"
        self._theme = "system"
        self._background_color = ""
        self._background_opacity = 1.0
        self._adjust_outline = False
        self._palette = colors("system")
        self._font_family = "Microsoft YaHei"
        self._drag_state = None
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setAccessibleName(tr("摸鱼模式阅读条", self._language))
        self.setToolTip(self._interaction_hint(self._language))
        self._sync_height()

    @property
    def line_count(self):
        return self._line_count

    @property
    def sentence_text(self):
        return self._sentence

    @property
    def overflow_text(self):
        _font, _lines, overflow_start = self._layout_lines()
        if overflow_start is None:
            return ""
        return self._sentence[overflow_start:].lstrip()

    @property
    def preferred_height(self):
        return self.SINGLE_LINE_HEIGHT if self._line_count == 1 else self.DOUBLE_LINE_HEIGHT

    @property
    def interaction_active(self):
        return self._drag_state is not None

    @staticmethod
    def _interaction_hint(language):
        return tr(
            "鼠标靠近时显示；离开后自动隐藏。滚轮逐句翻页；按住 Alt 并拖动可移动，按住 Alt 拖动边缘或角落可调整大小。",
            language,
        )

    def set_preferences(
        self, *, lines: int, theme: str, language: str, font_family: str, background_color="",
    ):
        self._line_count = 1 if lines == 1 else 2
        self._theme = theme
        language_changed = language != self._language
        self._language = language
        self._font_family = font_family
        self._background_color = background_color or ""
        self._palette = colors(theme, self._background_color)
        self.setAccessibleName(tr("摸鱼模式阅读条", language))
        if language_changed:
            self._render_sentence()
        self.setToolTip(self._interaction_hint(language))
        self._sync_height()
        self.update()

    def set_background_color(self, background_color=""):
        background_color = background_color or ""
        if background_color == self._background_color:
            return
        self._background_color = background_color
        self._palette = colors(self._theme, background_color)
        self.update()

    def set_background_opacity(self, opacity: float):
        opacity = max(0.0, min(1.0, float(opacity)))
        if opacity == self._background_opacity:
            return
        self._background_opacity = opacity
        self.update()

    def set_adjust_outline(self, active: bool):
        active = bool(active)
        if active == self._adjust_outline:
            return
        self._adjust_outline = active
        self.update()

    def set_sentence(self, sentence: str):
        self._source_sentence = sentence
        self._render_sentence()

    def _render_sentence(self):
        illustration_label = tr("(此处有插图)", self._language)
        self._sentence = " ".join(
            self._source_sentence.replace("\ufffc", f" {illustration_label} ")
            .replace("\u2028", " ").replace("\u2029", " ").split()
        )
        self.update()

    def set_hotkey_tooltip(self, message: str):
        hint = self._interaction_hint(self._language)
        self.setToolTip(f"{hint}\n{message}" if message else hint)

    def _sync_height(self):
        minimum_height = self.preferred_height
        self.setMinimumHeight(minimum_height)
        self.setMaximumHeight(self.MAXIMUM_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def _layout_lines(self):
        text_rect = self.rect().adjusted(12, 3, -12, -3)
        font = QFont(self._font_family)
        font.setPixelSize(12)
        if not self._sentence:
            return font, [], None

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
        drawn_lines = []
        overflow_start = None
        for row, (start, count) in enumerate(ranges):
            end = start + count
            raw_segment = self._sentence[start:end]
            segment = raw_segment.strip()
            has_more = row == len(ranges) - 1 and bool(self._sentence[end:].strip())
            if has_more:
                segment = metrics.elidedText(
                    segment + "…", Qt.TextElideMode.ElideRight, int(text_rect.width()),
                )
                visible_segment = segment[:-1] if segment.endswith("…") else segment
                leading_whitespace = len(raw_segment) - len(raw_segment.lstrip())
                overflow_start = start + leading_whitespace + len(visible_segment)
            drawn_lines.append((segment, QPointF(text_rect.left(), baseline + row * metrics.height())))
        return font, drawn_lines, overflow_start

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        background = self._palette["paper"] if self._background_color else self._palette.get("side", self._palette["paper"])
        background_color = QColor(background)
        background_color.setAlpha(round(self._background_opacity * 255))
        painter.fillRect(self.rect(), background_color)
        font, drawn_lines, _overflow_start = self._layout_lines()
        painter.setFont(font)
        painter.setPen(QColor(self._palette.get("ink_secondary", self._palette["muted"])))
        for segment, baseline in drawn_lines:
            painter.drawText(baseline, segment)
        if self._adjust_outline:
            sampled_background = QColor(background)
            hue, _saturation, _value, _alpha = sampled_background.getHsv()
            if hue < 0:
                hue = QColor(self._palette.get("accent", "#315743")).hue()
            if hue < 0:
                hue = 165
            outline_value = 150 if sampled_background.lightness() > 128 else 255
            outline = QColor.fromHsv((hue + 150) % 360, 235, outline_value)
            painter.setPen(QPen(outline, 2, Qt.PenStyle.SolidLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.rect().adjusted(1, 1, -2, -2))

    def wheelEvent(self, event):
        self._wheel_page_accumulator.handle(event)

    def _resize_edges_at(self, position):
        edges = Qt.Edge(0)
        if position.x() < self.EDGE_MARGIN:
            edges |= Qt.Edge.LeftEdge
        elif position.x() >= self.width() - self.EDGE_MARGIN:
            edges |= Qt.Edge.RightEdge
        if position.y() < self.EDGE_MARGIN:
            edges |= Qt.Edge.TopEdge
        elif position.y() >= self.height() - self.EDGE_MARGIN:
            edges |= Qt.Edge.BottomEdge
        return edges

    @staticmethod
    def _resize_cursor(edges):
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

    def _resize_window(self, global_position, origin, geometry, edges):
        dx = global_position.x() - origin.x()
        dy = global_position.y() - origin.y()
        left, top = geometry.x(), geometry.y()
        width, height = geometry.width(), geometry.height()
        minimum_width = self.window().minimumWidth()
        minimum_height = self.window().minimumHeight()

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

        screen = self.window().screen()
        if screen is not None:
            area = screen.availableGeometry()
            fixed_right = geometry.x() + geometry.width() - 1
            fixed_bottom = geometry.y() + geometry.height() - 1
            if edges & Qt.Edge.LeftEdge:
                left = max(area.left(), min(left, fixed_right - minimum_width + 1))
                width = fixed_right - left + 1
            else:
                width = min(width, area.right() - left + 1)
            if edges & Qt.Edge.TopEdge:
                top = max(area.top(), min(top, fixed_bottom - minimum_height + 1))
                height = fixed_bottom - top + 1
            else:
                height = min(height, area.bottom() - top + 1)

        maximum_width = self.window().maximumWidth()
        maximum_height = self.window().maximumHeight()
        if width > maximum_width:
            if edges & Qt.Edge.LeftEdge:
                left += width - maximum_width
            width = maximum_width
        if height > maximum_height:
            if edges & Qt.Edge.TopEdge:
                top += height - maximum_height
            height = maximum_height

        self.window().setGeometry(left, top, width, height)

    def cancel_interaction(self):
        self._drag_state = None
        self.unsetCursor()

    @staticmethod
    def _alt_is_pressed(event=None):
        if event is not None and event.modifiers() & Qt.KeyboardModifier.AltModifier:
            return True
        return bool(QGuiApplication.queryKeyboardModifiers() & Qt.KeyboardModifier.AltModifier)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._alt_is_pressed(event):
            window = self.window()
            global_position = event.globalPosition().toPoint()
            edges = self._resize_edges_at(event.position().toPoint())
            if edges:
                self._drag_state = {
                    "kind": "resize",
                    "origin": global_position,
                    "geometry": window.geometry(),
                    "edges": edges,
                }
                self.setCursor(self._resize_cursor(edges))
                event.accept()
                return
            self._drag_state = {
                "kind": "move",
                "origin": global_position,
                "offset": global_position - window.frameGeometry().topLeft(),
            }
            self.setCursor(Qt.CursorShape.SizeAllCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_state is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if not self._alt_is_pressed(event):
                self.cancel_interaction()
                event.accept()
                return
            global_position = event.globalPosition().toPoint()
            if self._drag_state["kind"] == "move":
                self.window().move(global_position - self._drag_state["offset"])
            else:
                self._resize_window(
                    global_position,
                    self._drag_state["origin"],
                    self._drag_state["geometry"],
                    self._drag_state["edges"],
                )
            event.accept()
            return
        if event.buttons() == Qt.MouseButton.NoButton:
            if self._alt_is_pressed(event):
                edges = self._resize_edges_at(event.position().toPoint())
                self.setCursor(self._resize_cursor(edges) if edges else Qt.CursorShape.SizeAllCursor)
            else:
                self.unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._drag_state is not None:
            self.cancel_interaction()
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
