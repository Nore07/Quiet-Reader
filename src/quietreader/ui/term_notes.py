"""In-book term matcher, hover preview card, and themed add/edit/import dialogs."""

from dataclasses import asdict, is_dataclass
import re

from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QFontMetrics, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QRadioButton, QVBoxLayout, QWidget,
)

from quietreader.i18n import tr
from quietreader.persistence.store import (
    MAX_TERM_NOTE, MAX_TERM_TEXT, normalize_term_note, normalize_term_text,
)
from quietreader.ui.theme import colors, stylesheet


def compile_term_matcher(records) -> tuple[list[dict], re.Pattern | None, dict[str, dict]]:
    """Compile a single longest-first case-insensitive regex and casefold lookup table."""
    cleaned: list[dict] = []
    seen: set[str] = set()
    for raw in (records or ()):
        item = asdict(raw) if is_dataclass(raw) else raw
        if not isinstance(item, dict):
            continue
        term = normalize_term_text(item.get("term", ""))
        note = normalize_term_note(item.get("note", ""))
        if not term or not note:
            continue
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({
            "term": term,
            "note": note,
            "created": str(item.get("created", ""))[:40],
        })
    ordered = sorted(cleaned, key=lambda r: len(r["term"]), reverse=True)
    lookup = {r["term"].casefold(): r for r in ordered}
    pattern = (
        re.compile("|".join(re.escape(r["term"]) for r in ordered), re.IGNORECASE)
        if ordered
        else None
    )
    return ordered, pattern, lookup


def find_term_spans(
    text: str,
    pattern: re.Pattern | None,
    lookup: dict[str, dict],
    *,
    excluded_mask: list[bool] | None = None,
) -> list[tuple[int, int, dict]]:
    """Find non-overlapping term spans in text, skipping masked characters (e.g. placeholders)."""
    if not text or pattern is None or not lookup:
        return []
    searchable = text
    if excluded_mask is not None and any(excluded_mask):
        searchable = "".join(
            "\x00" if (idx < len(excluded_mask) and excluded_mask[idx]) else ch
            for idx, ch in enumerate(text)
        )
    spans: list[tuple[int, int, dict]] = []
    for match in pattern.finditer(searchable):
        start = match.start()
        end = match.end()
        if end <= start:
            continue
        matched_text = match.group(0)
        record = lookup.get(matched_text.casefold()) or lookup.get(matched_text.lower())
        if record is not None:
            spans.append((start, end, record))
    return spans


class TermHoverCard(QWidget):
    """Non-activating, click-through preview card (in-surface or top-level floating)."""

    _MAX_WIDTH = 260
    _MIN_WIDTH = 140
    _PAD_X = 12
    _PAD_TOP = 10
    _PAD_BOTTOM = 11
    _SPACING = 5
    _GAP = 6
    _BORDER_PX = 1
    _TEXT_HEIGHT_BUFFER = 3

    def __init__(self, parent=None, *, floating: bool = False):
        super().__init__(parent)
        self._floating = bool(floating)
        self.setObjectName("termHoverCardHost")
        if self._floating:
            self.setWindowFlags(
                Qt.WindowType.ToolTip
                | Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.WindowDoesNotAcceptFocus
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._term = ""
        self._note = ""

        host_layout = QVBoxLayout(self)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(0)

        self.card = QFrame(self)
        self.card.setObjectName("termHoverCard")
        self.card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.card.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        host_layout.addWidget(self.card)

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(self._PAD_X, self._PAD_TOP, self._PAD_X, self._PAD_BOTTOM)
        layout.setSpacing(self._SPACING)

        self.term_label = QLabel("", self.card)
        self.term_label.setObjectName("termHoverTitle")
        self.term_label.setTextFormat(Qt.TextFormat.PlainText)
        self.term_label.setWordWrap(True)
        self.term_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.term_label)

        self.divider = QFrame(self.card)
        self.divider.setObjectName("termHoverDivider")
        self.divider.setFixedHeight(1)
        layout.addWidget(self.divider)

        self.note_label = QLabel("", self.card)
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

    def show_for(
        self,
        term: str,
        note: str,
        anchor_rect: QRect,
        bounds_rect: QRect | None = None,
        *,
        prefer_above: bool = False,
    ):
        if self._floating:
            if bounds_rect is not None and bounds_rect.isValid():
                bounds = QRect(bounds_rect)
            else:
                screen = QGuiApplication.screenAt(anchor_rect.center()) or QGuiApplication.primaryScreen()
                bounds = screen.availableGeometry() if screen is not None else QRect(0, 0, 1280, 720)
        else:
            parent = self.parentWidget()
            if parent is None:
                return
            parent_rect = QRect(0, 0, max(120, parent.width()), max(80, parent.height()))
            if bounds_rect is None or not bounds_rect.isValid():
                bounds = parent_rect
            else:
                bounds = bounds_rect.intersected(parent_rect)
                if not bounds.isValid() or bounds.width() < 80 or bounds.height() < 60:
                    bounds = parent_rect

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
        self.card.ensurePolished()
        self.term_label.ensurePolished()
        self.note_label.ensurePolished()

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

        if prefer_above:
            if full_card_h <= min(max_overall_h, space_above):
                place_below = False
                allowed_h = min(max_overall_h, space_above)
            elif full_card_h <= min(max_overall_h, space_below):
                place_below = True
                allowed_h = min(max_overall_h, space_below)
            elif max(space_above, space_below) >= chrome_h + term_h + min_note_h:
                place_below = space_below > space_above
                allowed_h = min(max_overall_h, max(space_above, space_below))
            else:
                place_below = space_below > space_above
                allowed_h = max_overall_h
        else:
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
        self._editing = bool(editing)
        self._locked_term = normalize_term_text(term)
        self.delete_requested = False
        self._enforcing_limit = False

        self.setModal(True)
        self.setWindowTitle(tr("编辑词条标注" if self._editing else "添加词条标注", language))
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
        self.term_input.setText(self._locked_term)
        if self._editing:
            lock_tip = tr("编辑已有词条时不可修改词条名称；如需更换请删除后重新添加。", language)
            self.term_input.setReadOnly(True)
            self.term_input.setToolTip(lock_tip)
            self.term_counter.setToolTip(lock_tip)
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
        if self._editing:
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
        if self._editing or self.term_input.text().strip():
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
        clean_term = self._locked_term if self._editing else normalize_term_text(self.term_input.text())
        raw_note = self.note_input.toPlainText()
        clean_note = normalize_term_note(raw_note)
        if self._editing:
            self.term_counter.setText(tr("不可修改", self._language))
        else:
            self.term_counter.setText(f"{len(clean_term)} / {MAX_TERM_TEXT}")
        self.note_counter.setText(f"{min(len(raw_note), MAX_TERM_NOTE)} / {MAX_TERM_NOTE}")
        self.save_button.setEnabled(bool(clean_term and clean_note))

    def _request_delete(self):
        self.delete_requested = True
        self.accept()

    def values(self) -> tuple[str, str]:
        return (
            self._locked_term if self._editing else normalize_term_text(self.term_input.text()),
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


class ImportTermNotesDialog(QDialog):
    """Themed modal dialog for inheriting selected term notes from another book."""

    def __init__(
        self,
        *,
        sources: list[dict],
        existing_terms: set[str],
        theme: str = "system",
        language: str = "system",
        parent=None,
    ):
        super().__init__(parent)
        self._language = language
        self._sources = list(sources or [])
        self._existing_terms = {t.casefold() for t in (existing_terms or set())}

        self.setModal(True)
        self.setWindowTitle(tr("从其他书继承词条", language))
        self.setStyleSheet(stylesheet(colors(theme)))
        self.setMinimumWidth(420)
        self.resize(470, 490)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        source_label = QLabel(tr("来源书籍", language), self)
        source_label.setObjectName("popoverLabel")
        outer.addWidget(source_label)

        self.source_combo = QComboBox(self)
        for src in self._sources:
            count = len(src.get("records") or [])
            title = src.get("title") or src.get("id") or ""
            label = tr("《{title}》 · {count} 条", language, title=title, count=count)
            self.source_combo.addItem(label, src.get("id", ""))
        self.source_combo.currentIndexChanged.connect(self._populate_terms)
        outer.addWidget(self.source_combo)

        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(6)
        self.filter_input = QLineEdit(self)
        self.filter_input.setObjectName("shelfSearch")
        self.filter_input.setClearButtonEnabled(True)
        self.filter_input.setPlaceholderText(tr("筛选词条…", language))
        self.filter_input.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.filter_input, 1)

        self.select_all_button = QPushButton(tr("全选", language), self)
        self.select_all_button.setObjectName("bookmarkAction")
        self.select_all_button.clicked.connect(lambda: self._set_visible_checked(True))
        filter_row.addWidget(self.select_all_button)

        self.select_none_button = QPushButton(tr("全不选", language), self)
        self.select_none_button.setObjectName("bookmarkAction")
        self.select_none_button.clicked.connect(lambda: self._set_visible_checked(False))
        filter_row.addWidget(self.select_none_button)
        outer.addLayout(filter_row)

        self.count_label = QLabel("", self)
        self.count_label.setObjectName("status")
        outer.addWidget(self.count_label)

        self.term_list = QListWidget(self)
        self.term_list.setObjectName("importTermList")
        self.term_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.term_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.term_list.itemChanged.connect(self._sync_counts)
        outer.addWidget(self.term_list, 1)

        strategy_label = QLabel(tr("同名词条处理", language), self)
        strategy_label.setObjectName("popoverLabel")
        outer.addWidget(strategy_label)

        self.skip_radio = QRadioButton(tr("跳过同名词条（保留当前备注）", language), self)
        self.skip_radio.setChecked(True)
        self.overwrite_radio = QRadioButton(tr("覆盖同名词条（使用来源备注）", language), self)
        outer.addWidget(self.skip_radio)
        outer.addWidget(self.overwrite_radio)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 6, 0, 0)
        actions.setSpacing(8)
        actions.addStretch(1)
        self.cancel_button = QPushButton(tr("取消", language), self)
        self.cancel_button.clicked.connect(self.reject)
        actions.addWidget(self.cancel_button)
        self.import_button = QPushButton(tr("继承所选词条", language), self)
        self.import_button.setObjectName("primary")
        self.import_button.setDefault(True)
        self.import_button.clicked.connect(self.accept)
        actions.addWidget(self.import_button)
        outer.addLayout(actions)

        self._populate_terms()

    def _current_source(self) -> dict | None:
        idx = self.source_combo.currentIndex()
        if 0 <= idx < len(self._sources):
            return self._sources[idx]
        return None

    def _populate_terms(self, *_args):
        self.term_list.blockSignals(True)
        self.term_list.clear()
        src = self._current_source()
        records = (src.get("records") or []) if src else []
        exists_tag = tr("[已有]", self._language)
        self.term_list.ensurePolished()
        metrics = QFontMetrics(self.term_list.font())
        avail_w = max(240, self.width() - 88)
        for record in records:
            term = record.get("term", "")
            note = record.get("note", "")
            if not term or not note:
                continue
            note_flat = " ".join(note.split())
            already_exists = term.casefold() in self._existing_terms
            header = f"{term}  {exists_tag}" if already_exists else term
            header_elided = metrics.elidedText(header, Qt.TextElideMode.ElideRight, avail_w)
            preview = metrics.elidedText(note_flat, Qt.TextElideMode.ElideRight, avail_w)
            item = QListWidgetItem(f"{header_elided}\n{preview}" if preview else header_elided)
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, term)
            item.setData(Qt.ItemDataRole.UserRole + 1, f"{term}\n{note}".casefold())
            item.setToolTip(f"{term}\n{note}")
            self.term_list.addItem(item)
        self.term_list.blockSignals(False)
        self._apply_filter()
        self._sync_counts()

    def _apply_filter(self, _text=""):
        query = self.filter_input.text().strip().casefold()
        for row in range(self.term_list.count()):
            item = self.term_list.item(row)
            haystack = item.data(Qt.ItemDataRole.UserRole + 1) or ""
            item.setHidden(bool(query) and query not in haystack)

    def _set_visible_checked(self, checked: bool):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.term_list.blockSignals(True)
        for row in range(self.term_list.count()):
            item = self.term_list.item(row)
            if not item.isHidden():
                item.setCheckState(state)
        self.term_list.blockSignals(False)
        self._sync_counts()

    def _sync_counts(self, *_args):
        total = self.term_list.count()
        selected = sum(
            1
            for row in range(total)
            if self.term_list.item(row).checkState() == Qt.CheckState.Checked
        )
        self.count_label.setText(
            tr("已选 {selected} / {total} 条", self._language, selected=selected, total=total)
        )
        self.import_button.setEnabled(selected > 0)

    def selection(self) -> tuple[str, list[str], bool]:
        source_id = self.source_combo.currentData() or ""
        terms = [
            self.term_list.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(self.term_list.count())
            if self.term_list.item(row).checkState() == Qt.CheckState.Checked
        ]
        return source_id, [t for t in terms if t], self.overwrite_radio.isChecked()
