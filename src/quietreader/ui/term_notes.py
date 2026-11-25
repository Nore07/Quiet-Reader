"""In-book term note hover preview card and themed add/edit dialog."""

from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from quietreader.i18n import tr
from quietreader.persistence.store import (
    MAX_TERM_NOTE, MAX_TERM_TEXT, normalize_term_note, normalize_term_text,
)
from quietreader.ui.theme import colors, stylesheet


class TermHoverCard(QFrame):
    """Non-activating, click-through floating preview card clamped inside its parent surface."""

    _MAX_WIDTH = 260
    _MIN_WIDTH = 140
    _PAD_X = 12
    _PAD_TOP = 10
    _PAD_BOTTOM = 11
    _SPACING = 5
    _GAP = 6
    _BORDER_PX = 1
    _TEXT_HEIGHT_BUFFER = 3

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("termHoverCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._term = ""
        self._note = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(self._PAD_X, self._PAD_TOP, self._PAD_X, self._PAD_BOTTOM)
        layout.setSpacing(self._SPACING)

        self.term_label = QLabel("", self)
        self.term_label.setObjectName("termHoverTitle")
        self.term_label.setTextFormat(Qt.TextFormat.PlainText)
        self.term_label.setWordWrap(True)
        self.term_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.term_label)

        self.divider = QFrame(self)
        self.divider.setObjectName("termHoverDivider")
        self.divider.setFixedHeight(1)
        layout.addWidget(self.divider)

        self.note_label = QLabel("", self)
        self.note_label.setObjectName("termHoverBody")
        self.note_label.setTextFormat(Qt.TextFormat.PlainText)
        self.note_label.setWordWrap(True)
        self.note_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.note_label)

        self.hide()

    @property
    def current_term(self) -> str:
        return self._term

    @property
    def current_note(self) -> str:
        return self._note

    @staticmethod
    def _measure_wrapped(metrics: QFontMetrics, text: str, width: int) -> QRect:
        return metrics.boundingRect(
            0, 0, max(40, width), 4000,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
            text,
        )

    @classmethod
    def _label_height_for(cls, label: QLabel, metrics: QFontMetrics, text: str, width: int) -> int:
        label.setText(text)
        hfw = label.heightForWidth(max(40, width))
        fm_h = cls._measure_wrapped(metrics, text, width).height()
        return max(metrics.height(), hfw, fm_h) + cls._TEXT_HEIGHT_BUFFER

    def _fit_note_to_height(self, metrics: QFontMetrics, note: str, width: int, max_height: int) -> tuple[str, int]:
        full_h = self._label_height_for(self.note_label, metrics, note, width)
        if full_h <= max_height:
            return note, full_h
        low, high = 1, len(note)
        best = "…"
        best_h = self._label_height_for(self.note_label, metrics, best, width)
        while low <= high:
            mid = (low + high) // 2
            candidate = note[:mid].rstrip() + "…"
            cand_h = self._label_height_for(self.note_label, metrics, candidate, width)
            if cand_h <= max_height:
                best = candidate
                best_h = cand_h
                low = mid + 1
            else:
                high = mid - 1
        return best, best_h

    def show_for(self, term: str, note: str, anchor_rect: QRect, bounds_rect: QRect | None = None):
        parent = self.parentWidget()
        if parent is None:
            return
        clean_term = (term or "").strip()
        clean_note = (note or "").strip()
        if not clean_term or not clean_note:
            self.dismiss()
            return

        self._term = clean_term
        self._note = clean_note
        self.term_label.setMinimumSize(0, 0)
        self.term_label.setMaximumSize(16777215, 16777215)
        self.note_label.setMinimumSize(0, 0)
        self.note_label.setMaximumSize(16777215, 16777215)
        self.ensurePolished()
        self.term_label.ensurePolished()
        self.note_label.ensurePolished()

        parent_rect = QRect(0, 0, max(120, parent.width()), max(80, parent.height()))
        if bounds_rect is None or not bounds_rect.isValid():
            bounds = parent_rect
        else:
            bounds = bounds_rect.intersected(parent_rect)
            if not bounds.isValid() or bounds.width() < 80 or bounds.height() < 60:
                bounds = parent_rect

        bounds_w = bounds.width()
        bounds_h = bounds.height()
        margin = 6 if bounds_w < 420 or bounds_h < 220 else 10
        avail_left = bounds.left() + margin
        avail_top = bounds.top() + margin
        avail_right = bounds.right() - margin
        avail_bottom = bounds.bottom() - margin
        avail_w = max(80, avail_right - avail_left + 1)
        avail_h = max(56, avail_bottom - avail_top + 1)

        max_card_w = max(120, min(self._MAX_WIDTH, avail_w))
        max_inner_w = max(60, max_card_w - self._PAD_X * 2 - self._BORDER_PX * 2)

        term_metrics = QFontMetrics(self.term_label.font())
        note_metrics = QFontMetrics(self.note_label.font())
        term_natural = self._measure_wrapped(term_metrics, clean_term, max_inner_w)
        note_natural = self._measure_wrapped(note_metrics, clean_note, max_inner_w)

        inner_w = min(
            max_inner_w,
            max(self._MIN_WIDTH - self._PAD_X * 2 - self._BORDER_PX * 2, max(term_natural.width(), note_natural.width()) + 6),
        )
        card_w = min(max_card_w, inner_w + self._PAD_X * 2 + self._BORDER_PX * 2)
        inner_w = max(60, card_w - self._PAD_X * 2 - self._BORDER_PX * 2)

        term_h = self._label_height_for(self.term_label, term_metrics, clean_term, inner_w)
        min_note_h = note_metrics.height() + self._TEXT_HEIGHT_BUFFER
        chrome_h = self._PAD_TOP + self._PAD_BOTTOM + self._SPACING * 2 + 1 + self._BORDER_PX * 2

        space_below = (avail_bottom + 1) - (anchor_rect.bottom() + 1 + self._GAP)
        space_above = (anchor_rect.top() - self._GAP) - avail_top
        max_overall_h = max(56, min(260, avail_h))

        full_note_h = self._label_height_for(self.note_label, note_metrics, clean_note, inner_w)
        full_card_h = chrome_h + term_h + full_note_h

        if full_card_h <= min(max_overall_h, space_below):
            place_below = True
            allowed_h = min(max_overall_h, space_below)
        elif full_card_h <= min(max_overall_h, space_above):
            place_below = False
            allowed_h = min(max_overall_h, space_above)
        elif max(space_below, space_above) >= chrome_h + term_h + min_note_h:
            place_below = space_below >= space_above
            allowed_h = min(max_overall_h, max(space_below, space_above))
        else:
            place_below = space_below >= space_above
            allowed_h = max_overall_h

        max_note_h = max(min_note_h, allowed_h - chrome_h - term_h)
        display_note, note_h = self._fit_note_to_height(note_metrics, clean_note, inner_w, max_note_h)
        card_h = min(max_overall_h, chrome_h + term_h + note_h)

        self.term_label.setText(clean_term)
        self.term_label.setFixedWidth(inner_w)
        self.term_label.setFixedHeight(term_h)
        self.note_label.setText(display_note)
        self.note_label.setFixedWidth(inner_w)
        self.note_label.setFixedHeight(note_h)

        x = anchor_rect.left() - 4
        x = max(avail_left, min(x, avail_right - card_w + 1))
        if place_below:
            y = anchor_rect.bottom() + 1 + self._GAP
        else:
            y = anchor_rect.top() - self._GAP - card_h
        y = max(avail_top, min(y, avail_bottom - card_h + 1))

        self.setGeometry(x, y, card_w, card_h)
        self.show()
        self.raise_()

    def dismiss(self):
        self._term = ""
        self._note = ""
        if not self.isHidden():
            self.hide()


class TermNoteDialog(QDialog):
    """Themed modal dialog for adding or editing a book term annotation."""

    def __init__(
        self,
        *,
        theme: str = "system",
        language: str = "system",
        term: str = "",
        note: str = "",
        editing: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._language = language
        self.delete_requested = False
        self._enforcing_limit = False

        self.setModal(True)
        self.setWindowTitle(tr("编辑词条标注" if editing else "添加词条标注", language))
        self.setStyleSheet(stylesheet(colors(theme)))
        self.setMinimumWidth(340)
        self.resize(390, 290)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        term_head = QHBoxLayout()
        term_head.setContentsMargins(0, 0, 0, 0)
        self.term_label = QLabel(tr("词条名称", language), self)
        self.term_label.setObjectName("popoverLabel")
        self.term_counter = QLabel("", self)
        self.term_counter.setObjectName("status")
        term_head.addWidget(self.term_label)
        term_head.addStretch(1)
        term_head.addWidget(self.term_counter)
        outer.addLayout(term_head)

        self.term_input = QLineEdit(self)
        self.term_input.setMaxLength(MAX_TERM_TEXT)
        self.term_input.setPlaceholderText(tr("例如：韩立、墨居仁、七玄门…", language))
        self.term_input.setText(normalize_term_text(term))
        self.term_input.textChanged.connect(self._sync_state)
        outer.addWidget(self.term_input)

        note_head = QHBoxLayout()
        note_head.setContentsMargins(0, 0, 0, 0)
        self.note_label = QLabel(tr("介绍或看法", language), self)
        self.note_label.setObjectName("popoverLabel")
        self.note_counter = QLabel("", self)
        self.note_counter.setObjectName("status")
        note_head.addWidget(self.note_label)
        note_head.addStretch(1)
        note_head.addWidget(self.note_counter)
        outer.addLayout(note_head)

        self.note_input = QPlainTextEdit(self)
        self.note_input.setPlaceholderText(tr("写下人物关系、地名背景或个人看法…", language))
        self.note_input.setPlainText(normalize_term_note(note))
        self.note_input.setTabChangesFocus(True)
        self.note_input.setMinimumHeight(112)
        self.note_input.textChanged.connect(self._on_note_changed)
        self.note_input.installEventFilter(self)
        outer.addWidget(self.note_input, 1)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 4, 0, 0)
        actions.setSpacing(8)
        self.delete_button = None
        if editing:
            self.delete_button = QPushButton(tr("删除", language), self)
            self.delete_button.setObjectName("dangerAction")
            self.delete_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.delete_button.clicked.connect(self._request_delete)
            actions.addWidget(self.delete_button)
        actions.addStretch(1)
        self.cancel_button = QPushButton(tr("取消", language), self)
        self.cancel_button.clicked.connect(self.reject)
        actions.addWidget(self.cancel_button)
        self.save_button = QPushButton(tr("保存", language), self)
        self.save_button.setObjectName("primary")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self.accept)
        actions.addWidget(self.save_button)
        outer.addLayout(actions)

        self._sync_state()
        if self.term_input.text().strip():
            self.note_input.setFocus()
            cursor = self.note_input.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            self.note_input.setTextCursor(cursor)
        else:
            self.term_input.setFocus()

    def _on_note_changed(self):
        if self._enforcing_limit:
            return
        text = self.note_input.toPlainText()
        if len(text) > MAX_TERM_NOTE:
            self._enforcing_limit = True
            try:
                cursor = self.note_input.textCursor()
                pos = min(cursor.position(), MAX_TERM_NOTE)
                self.note_input.setPlainText(text[:MAX_TERM_NOTE])
                cursor = self.note_input.textCursor()
                cursor.setPosition(pos)
                self.note_input.setTextCursor(cursor)
            finally:
                self._enforcing_limit = False
        self._sync_state()

    def _sync_state(self):
        clean_term = normalize_term_text(self.term_input.text())
        raw_note = self.note_input.toPlainText()
        clean_note = normalize_term_note(raw_note)
        self.term_counter.setText(f"{len(clean_term)} / {MAX_TERM_TEXT}")
        self.note_counter.setText(f"{min(len(raw_note), MAX_TERM_NOTE)} / {MAX_TERM_NOTE}")
        self.save_button.setEnabled(bool(clean_term and clean_note))

    def _request_delete(self):
        self.delete_requested = True
        self.accept()

    def values(self) -> tuple[str, str]:
        return (
            normalize_term_text(self.term_input.text()),
            normalize_term_note(self.note_input.toPlainText()),
        )

    def eventFilter(self, watched, event):
        if (
            watched is self.note_input
            and event.type() == QEvent.Type.KeyPress
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and (event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        ):
            if self.save_button.isEnabled():
                self.accept()
            return True
        return super().eventFilter(watched, event)
