"""Minimal status-bar-like reader surface used by Moyu mode."""

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPen, QTextLayout, QTextOption
from PySide6.QtWidgets import QApplication, QMenu, QSizePolicy, QWidget

from quietreader.i18n import tr
from quietreader.ui.term_notes import compile_term_matcher, find_term_spans
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
    term_hovered = Signal(object, object)
    term_hover_cleared = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._wheel_page_accumulator = WheelPageAccumulator(self._emit_wheel_turn, self)
        self._source_sentence = ""
        self._source_offset = 0
        self._display_source_offsets = []
        self._placeholder_mask: list[bool] = []
        self._qt_to_python = [0]
        self._sentence = ""
        self._term_notes: list[dict] = []
        self._term_pattern = None
        self._term_lookup: dict[str, dict] = {}
        self._term_spans: list[tuple[int, int, dict]] = []
        self._pending_hover = None
        self._active_hover = None
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(140)
        self._hover_timer.timeout.connect(self._fire_term_hover)
        self._line_count = 1
        self._language = "system"
        self._adjust_key = "Alt"
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
        self.setToolTip(self._interaction_hint(self._language, self._adjust_key))
        self._sync_height()

    @property
    def line_count(self):
        return self._line_count

    @property
    def sentence_text(self):
        return self._sentence

    @property
    def overflow_text(self):
        overflow_start = self._overflow_start()
        if overflow_start is None:
            return ""
        return self._sentence[overflow_start:].lstrip()

    @property
    def overflow_source_offset(self):
        overflow_start = self._overflow_start()
        if overflow_start is None:
            return None
        while overflow_start < len(self._sentence) and self._sentence[overflow_start].isspace():
            overflow_start += 1
        if overflow_start < len(self._display_source_offsets):
            return self._display_source_offsets[overflow_start]
        return self._source_offset + len(self._source_sentence)

    @property
    def preferred_height(self):
        return self.SINGLE_LINE_HEIGHT if self._line_count == 1 else self.DOUBLE_LINE_HEIGHT

    @property
    def interaction_active(self):
        return self._drag_state is not None

    @staticmethod
    def _interaction_hint(language, adjust_key):
        return tr(
            "鼠标靠近时显示；离开后自动隐藏。滚轮逐句翻页；按住 {key} 并拖动可移动，按住 {key} 拖动边缘或角落可调整大小。",
            language, key=adjust_key,
        )

    def set_preferences(
        self, *, lines: int, theme: str, language: str, font_family: str,
        adjust_key: str = "Alt", background_color="",
    ):
        new_lines = 1 if lines == 1 else 2
        if new_lines != self._line_count:
            self.clear_term_hover()
        self._line_count = new_lines
        self._theme = theme
        language_changed = language != self._language
        self._language = language
        self._adjust_key = adjust_key if adjust_key in ("Alt", "Ctrl", "Shift") else "Alt"
        self._font_family = font_family
        self._background_color = background_color or ""
        self._palette = colors(theme, self._background_color)
        self.setAccessibleName(tr("摸鱼模式阅读条", language))
        if language_changed:
            self._render_sentence()
        self.setToolTip(self._interaction_hint(language, self._adjust_key))
        self._sync_height()
        self.update()

    def set_term_notes(self, records):
        self.clear_term_hover()
        self._term_notes, self._term_pattern, self._term_lookup = compile_term_matcher(records)
        self._rebuild_term_spans()
        self.update()

    def _rebuild_term_spans(self):
        self._term_spans = find_term_spans(
            self._sentence,
            self._term_pattern,
            self._term_lookup,
            excluded_mask=self._placeholder_mask,
        )

    def clear_term_hover(self):
        had = self._hover_timer.isActive() or self._pending_hover is not None or self._active_hover is not None
        self._hover_timer.stop()
        self._pending_hover = None
        self._active_hover = None
        if had:
            self.term_hover_cleared.emit()

    def _emit_wheel_turn(self, direction):
        self.clear_term_hover()
        self.turn_requested.emit(direction)

    def _fire_term_hover(self):
        if self._pending_hover is None or not self.isVisible():
            return
        if (
            self.interaction_active
            or self.adjustment_key_is_pressed()
            or (QApplication.mouseButtons() & Qt.MouseButton.LeftButton)
        ):
            self.clear_term_hover()
            return
        start, end, record, local_rect = self._pending_hover
        self._pending_hover = None
        self._active_hover = (start, end, record)
        global_rect = QRect(self.mapToGlobal(local_rect.topLeft()), local_rect.size())
        self.term_hovered.emit(record, global_rect)

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

    def set_sentence(self, sentence: str, *, source_offset: int = 0):
        self.clear_term_hover()
        self._source_sentence = sentence or ""
        self._source_offset = max(0, int(source_offset))
        self._render_sentence()

    def _render_sentence(self):
        self.clear_term_hover()
        illustration_label = tr("(此处有插图)", self._language)
        rendered = []
        source_offsets = []
        placeholder_mask = []

        def append(character, source_index, *, is_placeholder=False):
            rendered.append(character)
            source_offsets.append(source_index)
            placeholder_mask.append(is_placeholder)

        def append_space(source_index):
            if rendered and not rendered[-1].isspace():
                append(" ", source_index)

        source = self._source_sentence
        index = 0
        while index < len(source):
            character = source[index]
            absolute_index = self._source_offset + index
            if character == "\ufffc":
                append_space(absolute_index)
                for label_index, label_character in enumerate(illustration_label):
                    append(
                        label_character,
                        absolute_index if label_index == 0 else absolute_index + 1,
                        is_placeholder=True,
                    )
                if index + 1 < len(source) and not source[index + 1].isspace():
                    append_space(absolute_index + 1)
                index += 1
                continue
            if character.isspace():
                while index < len(source) and source[index].isspace():
                    index += 1
                if rendered and index < len(source):
                    append_space(self._source_offset + index)
                continue
            append(character, absolute_index)
            index += 1

        self._sentence = "".join(rendered)
        self._display_source_offsets = source_offsets
        self._placeholder_mask = placeholder_mask
        qt_to_python = [0]
        for python_index, character in enumerate(self._sentence):
            if ord(character) > 0xFFFF:
                qt_to_python.append(python_index)
            qt_to_python.append(python_index + 1)
        self._qt_to_python = qt_to_python
        self._rebuild_term_spans()
        self.update()

    def _overflow_start(self):
        return self._layout_lines()[2]

    def _python_index_for_qt(self, qt_offset):
        if not self._qt_to_python:
            return 0
        index = max(0, min(int(qt_offset), len(self._qt_to_python) - 1))
        return self._qt_to_python[index]

    def set_hotkey_tooltip(self, message: str):
        hint = self._interaction_hint(self._language, self._adjust_key)
        self.setToolTip(f"{hint}\n{message}" if message else hint)

    def _sync_height(self):
        minimum_height = self.preferred_height
        self.setMinimumHeight(minimum_height)
        self.setMaximumHeight(self.MAXIMUM_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def _layout_lines(self):
        v_pad = 2 if self._line_count > 1 else 3
        text_rect = self.rect().adjusted(12, v_pad, -12, -v_pad)
        font = QFont(self._font_family)
        font.setPixelSize(12)
        if not self._sentence:
            return font, [], None, []

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
        line_h = metrics.height()
        line_gap = 0
        if len(ranges) > 1:
            spare = text_rect.height() - len(ranges) * line_h
            if spare >= 4:
                line_gap = 4
            elif spare >= 3:
                line_gap = 3
            elif spare >= 2:
                line_gap = 2
        line_step = line_h + line_gap
        total_height = len(ranges) * line_h + max(0, len(ranges) - 1) * line_gap
        top_offset = text_rect.top() + max(0.0, (text_rect.height() - total_height) / 2.0)
        baseline = top_offset + metrics.ascent()
        drawn_lines = []
        term_segments = []
        overflow_start = None
        for row, (start, count) in enumerate(ranges):
            qt_end = start + count
            python_start = self._python_index_for_qt(start)
            python_end = self._python_index_for_qt(qt_end)
            raw_segment = self._sentence[python_start:python_end]
            leading_whitespace = len(raw_segment) - len(raw_segment.lstrip())
            segment = raw_segment.strip()
            has_more = row == len(ranges) - 1 and bool(self._sentence[python_end:].strip())
            if has_more:
                segment = metrics.elidedText(
                    segment + "…", Qt.TextElideMode.ElideRight, int(text_rect.width()),
                )
                visible_segment = segment[:-1] if segment.endswith("…") else segment
                overflow_start = python_start + leading_whitespace + len(visible_segment)
            else:
                visible_segment = segment
            line_baseline = baseline + row * line_step
            drawn_lines.append((segment, QPointF(text_rect.left(), line_baseline)))

            if self._term_spans and visible_segment:
                vis_start = python_start + leading_whitespace
                vis_end = vis_start + len(visible_segment)
                underline_y = min(
                    float(self.height()) - 1.5,
                    line_baseline + max(2.5, metrics.descent() + 1.2),
                )
                if row < len(ranges) - 1:
                    next_line_top = top_offset + (row + 1) * line_step
                    underline_y = min(underline_y, next_line_top - 1.2)
                for span_start, span_end, record in self._term_spans:
                    if span_end <= vis_start:
                        continue
                    if span_start >= vis_end:
                        break
                    ov_start = max(span_start, vis_start)
                    ov_end = min(span_end, vis_end)
                    if ov_end <= ov_start:
                        continue
                    prefix = visible_segment[:ov_start - vis_start]
                    matched = visible_segment[ov_start - vis_start:ov_end - vis_start]
                    x1 = float(text_rect.left() + metrics.horizontalAdvance(prefix))
                    x2 = float(text_rect.left() + metrics.horizontalAdvance(prefix + matched))
                    if x2 - x1 < 1.0:
                        continue
                    hit_top = line_baseline - metrics.ascent()
                    hit_rect = QRectF(x1, hit_top, max(2.0, x2 - x1), max(1.0, (underline_y + 1.5) - hit_top))
                    term_segments.append((x1, x2, underline_y, hit_rect, record, span_start, span_end))
        return font, drawn_lines, overflow_start, term_segments

    def _term_underline_pen(self) -> QPen:
        is_dark = QColor(self._palette["paper"]).lightness() < 128
        if self._background_color or self._palette.get("custom_background"):
            color = QColor(self._palette["ink"])
            color.setAlpha(195 if is_dark else 185)
        else:
            color = QColor(self._palette["accent"])
            color.setAlpha(230)
        pen = QPen(color, 1.5, Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([2.6, 2.0])
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        return pen

    def _term_hit_at_point(self, point: QPointF):
        if not self._term_spans or not self._sentence:
            return None
        _font, _drawn_lines, _overflow_start, term_segments = self._layout_lines()
        for _x1, _x2, _uy, hit_rect, record, span_start, span_end in term_segments:
            if hit_rect.adjusted(-1.5, -1.5, 1.5, 1.5).contains(point):
                return span_start, span_end, record, hit_rect.toAlignedRect()
        return None

    def term_span_rect(self, term: str) -> QRect | None:
        """Return the widget-local hit rect of the first visible occurrence of a term."""
        key = (term or "").strip().casefold()
        if not key or not self._term_spans:
            return None
        _font, _drawn_lines, _overflow_start, term_segments = self._layout_lines()
        for _x1, _x2, _uy, hit_rect, record, _span_start, _span_end in term_segments:
            if record.get("term", "").casefold() == key:
                return hit_rect.toAlignedRect()
        return None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        background = self._palette["paper"] if self._background_color else self._palette.get("side", self._palette["paper"])
        background_color = QColor(background)
        background_color.setAlpha(round(self._background_opacity * 255))
        painter.fillRect(self.rect(), background_color)
        font, drawn_lines, _overflow_start, term_segments = self._layout_lines()
        painter.setFont(font)
        painter.setPen(QColor(self._palette.get("ink_secondary", self._palette["muted"])))
        for segment, baseline in drawn_lines:
            painter.drawText(baseline, segment)
        if term_segments:
            painter.save()
            painter.setPen(self._term_underline_pen())
            for x1, x2, underline_y, _hit_rect, _record, _s, _e in term_segments:
                painter.drawLine(QPointF(x1, underline_y), QPointF(x2, underline_y))
            painter.restore()
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
        self.clear_term_hover()
        self.unsetCursor()

    def adjustment_key_is_pressed(self, event=None):
        modifier = {
            "Alt": Qt.KeyboardModifier.AltModifier,
            "Ctrl": Qt.KeyboardModifier.ControlModifier,
            "Shift": Qt.KeyboardModifier.ShiftModifier,
        }.get(self._adjust_key, Qt.KeyboardModifier.AltModifier)
        if event is not None and event.modifiers() & modifier:
            return True
        return bool(QGuiApplication.queryKeyboardModifiers() & modifier)

    def event(self, ev):
        if ev.type() == QEvent.Type.ToolTip:
            if self._term_hit_at_point(QPointF(ev.pos())) is not None:
                ev.accept()
                return True
        return super().event(ev)

    def leaveEvent(self, event):
        self.clear_term_hover()
        super().leaveEvent(event)

    def hideEvent(self, event):
        self.clear_term_hover()
        super().hideEvent(event)

    def mousePressEvent(self, event):
        self.clear_term_hover()
        if event.button() == Qt.MouseButton.LeftButton and self.adjustment_key_is_pressed(event):
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
            self.clear_term_hover()
            if not self.adjustment_key_is_pressed(event):
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
            if self.adjustment_key_is_pressed(event):
                self.clear_term_hover()
                edges = self._resize_edges_at(event.position().toPoint())
                self.setCursor(self._resize_cursor(edges) if edges else Qt.CursorShape.SizeAllCursor)
            else:
                self.unsetCursor()
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
                        super().mouseMoveEvent(event)
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
        else:
            self.clear_term_hover()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._drag_state is not None:
            self.cancel_interaction()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        self.clear_term_hover()
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
