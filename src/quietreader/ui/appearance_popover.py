"""In-canvas floating appearance & typography popover (live preview, no modal dialog)."""

from dataclasses import replace

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QFontComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from quietreader.i18n import tr
from quietreader.ui.controls import FloatingPopover, ThinSlider
from quietreader.ui.settings import _PreferencesWheelFilter
from quietreader.ui.tokens import resolve_palette


class _ThemeCard(QAbstractButton):
    """Self-painted visual 'Aa' swatch card for selecting a built-in reading theme."""

    def __init__(self, code: str, label_source: str, parent=None):
        super().__init__(parent)
        self.code = code
        self.label_source = label_source
        self._label_text = label_source
        self._active_accent = "#2e5641"
        self._hovered = False
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(52)

    def sizeHint(self) -> QSize:
        return QSize(64, 52)

    def retranslate(self, language: str):
        self._label_text = tr(self.label_source, language)
        self.setToolTip(self._label_text)
        self.setAccessibleName(self._label_text)
        self.update()

    def apply_card_style(self, active_accent: str):
        self._active_accent = active_accent
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
        rect = QRectF(1.0, 1.0, self.width() - 2.0, self.height() - 2.0)
        clip = QPainterPath()
        clip.addRoundedRect(rect, 8.0, 8.0)

        painter.save()
        painter.setClipPath(clip)
        if self.code == "light":
            bg, fg, border = "#f6f4ec", "#1f3029", "#c8d0c0"
            painter.fillRect(rect, QColor(bg))
        elif self.code == "sepia":
            bg, fg, border = "#efe4cd", "#3e3125", "#c3ad84"
            painter.fillRect(rect, QColor(bg))
        elif self.code == "dark":
            bg, fg, border = "#1d2522", "#e8e6dc", "#42544b"
            painter.fillRect(rect, QColor(bg))
        else:
            fg, border = "#1f3029", "#9aa69d"
            half = rect.width() / 2.0
            painter.fillRect(QRectF(rect.left(), rect.top(), half, rect.height()), QColor("#f6f4ec"))
            painter.fillRect(QRectF(rect.left() + half, rect.top(), half + 1.0, rect.height()), QColor("#1d2522"))
            pill_rect = QRectF(rect.center().x() - 22, rect.top() + 7, 44, rect.height() - 14)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(250, 249, 244, 232))
            painter.drawRoundedRect(pill_rect, 5.0, 5.0)
        painter.restore()

        pen_color = self._active_accent if (self.isChecked() or self._hovered) else border
        pen_width = 2.0 if self.isChecked() else (1.5 if self._hovered else 1.0)
        painter.setPen(QPen(QColor(pen_color), pen_width))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 8.0, 8.0)

        aa_font = QFont("Noto Serif SC")
        aa_font.setPixelSize(14)
        aa_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(aa_font)
        painter.setPen(QColor(fg))
        painter.drawText(QRectF(rect.left(), rect.top() + 5, rect.width(), 22), Qt.AlignmentFlag.AlignCenter, "Aa")

        lbl_font = QFont("Segoe UI Variable Text")
        lbl_font.setPixelSize(10)
        painter.setFont(lbl_font)
        painter.drawText(QRectF(rect.left(), rect.top() + 27, rect.width(), 18), Qt.AlignmentFlag.AlignCenter, self._label_text)
        painter.end()


class _SliderControl(QWidget):
    """Compact labeled row: title + tabular value readout + [-  ThinSlider  +]."""

    valueChanged = Signal(float)

    def __init__(
        self,
        label_source: str,
        low: float,
        high: float,
        step: float,
        unit: str,
        decimals: int = 0,
        parent=None,
        commit_on_release: bool = False,
    ):
        super().__init__(parent)
        self.label_source = label_source
        self.low = low
        self.high = high
        self.step = step
        self.unit = unit
        self.decimals = decimals
        self._scale = round(1.0 / step) if step < 1 else 1
        self._syncing = False
        self._commit_on_release = bool(commit_on_release)
        self._drag_start_value = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        self.title_label = QLabel(label_source, self)
        self.title_label.setObjectName("popoverLabel")
        self.value_label = QLabel("", self)
        self.value_label.setObjectName("popoverValue")
        head.addWidget(self.title_label)
        head.addStretch(1)
        head.addWidget(self.value_label)
        outer.addLayout(head)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.minus_btn = QPushButton("−", self)
        self.minus_btn.setObjectName("stepperButton")
        self.minus_btn.setFixedSize(22, 22)
        self.minus_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.minus_btn.clicked.connect(lambda: self._nudge(-1))

        self.slider = ThinSlider(Qt.Orientation.Horizontal, self)
        self.slider.setRange(self._to_int(low), self._to_int(high))
        self.slider.valueChanged.connect(self._on_slider_changed)
        self.slider.sliderPressed.connect(self._on_slider_pressed)
        self.slider.sliderReleased.connect(self._on_slider_released)

        self.plus_btn = QPushButton("+", self)
        self.plus_btn.setObjectName("stepperButton")
        self.plus_btn.setFixedSize(22, 22)
        self.plus_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.plus_btn.clicked.connect(lambda: self._nudge(1))

        row.addWidget(self.minus_btn)
        row.addWidget(self.slider, 1)
        row.addWidget(self.plus_btn)
        outer.addLayout(row)

    def _to_int(self, val: float) -> int:
        return round(val * self._scale)

    def _from_int(self, raw: int) -> float:
        value = raw / self._scale
        return round(value, self.decimals) if self.decimals > 0 else int(round(value))

    def _format(self, val: float) -> str:
        if self.decimals > 0:
            return f"{val:.{self.decimals}f}{self.unit}"
        return f"{int(round(val))}{self.unit}"

    def set_value(self, value: float):
        self._syncing = True
        clamped = max(self.low, min(self.high, value))
        self.slider.setValue(self._to_int(clamped))
        self.value_label.setText(self._format(clamped))
        self._syncing = False
        if self._commit_on_release and self.slider.isSliderDown():
            self._drag_start_value = self.slider.value()

    def value(self):
        return self._from_int(self.slider.value())

    def _nudge(self, delta: int):
        step_units = max(1, round(self.step * self._scale))
        self.slider.setValue(max(self.slider.minimum(), min(self.slider.maximum(), self.slider.value() + delta * step_units)))

    def _on_slider_pressed(self):
        if self._commit_on_release:
            self._drag_start_value = self.slider.value()

    def _on_slider_released(self):
        if not self._commit_on_release:
            return
        start_value = self._drag_start_value
        self._drag_start_value = None
        if start_value is not None and self.slider.value() != start_value:
            self.valueChanged.emit(float(self.value()))

    def _on_slider_changed(self, raw: int):
        val = self._from_int(raw)
        self.value_label.setText(self._format(val))
        if not self._syncing:
            if self._commit_on_release and self.slider.isSliderDown():
                return
            self.valueChanged.emit(float(val))

    def retranslate(self, language: str):
        self.title_label.setText(tr(self.label_source, language))

    def apply_palette(self, palette: dict[str, str]):
        self.slider.apply_palette(palette)


class _AppearanceScrollArea(QScrollArea):
    def sizeHint(self) -> QSize:
        # Let the popover fit the full body whenever the window has room.
        content = self.widget()
        return content.sizeHint() if content is not None else super().sizeHint()


class AppearancePopover(FloatingPopover):
    """Floating Readest-style appearance & typography card anchored inside MainWindow."""

    settings_changed = Signal(object)
    more_settings_requested = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self._settings = None
        self._syncing = False
        self._language = "system"
        self._palette = resolve_palette("light")
        self.setFixedWidth(308)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 12)
        outer.setSpacing(11)

        self.header_label = QLabel("排版外观", self)
        self.header_label.setObjectName("popoverHeading")
        outer.addWidget(self.header_label)

        scroll = _AppearanceScrollArea(self)
        scroll.setObjectName("appearanceScroll")
        scroll.viewport().setObjectName("appearanceViewport")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("appearanceContent")
        content.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(11)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        # Theme 4-card visual selector
        self.theme_section_label = QLabel("阅读主题", content)
        self.theme_section_label.setObjectName("popoverLabel")
        body.addWidget(self.theme_section_label)

        theme_grid = QGridLayout()
        theme_grid.setContentsMargins(0, 0, 0, 0)
        theme_grid.setHorizontalSpacing(7)
        self.theme_cards: dict[str, _ThemeCard] = {}
        for col, (code, label) in enumerate((("light", "纸白"), ("sepia", "暖纸"), ("dark", "夜读"), ("system", "系统"))):
            card = _ThemeCard(code, label, content)
            card.setMinimumWidth(48)
            card.clicked.connect(lambda _checked=False, target=code: self._pick_theme(target))
            self.theme_cards[code] = card
            theme_grid.addWidget(card, 0, col)
        body.addLayout(theme_grid)

        # Font selector
        self.font_label = QLabel("正文字体", content)
        self.font_label.setObjectName("popoverLabel")
        body.addWidget(self.font_label)
        self.font_combo = QFontComboBox(content)
        self.font_combo.setSizeAdjustPolicy(QFontComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.font_combo.currentFontChanged.connect(self._emit_change)
        body.addWidget(self.font_combo)

        # Typography sliders
        self.sliders: dict[str, _SliderControl] = {}
        specs = (
            ("font_size", "字号", 14, 42, 1, " px", 0),
            ("line_height", "行距", 1.2, 2.5, 0.1, "×", 2),
            ("letter_spacing", "字距", 0.0, 5.0, 0.1, " px", 1),
            ("paragraph_spacing", "段距", 0, 40, 1, " px", 0),
            ("padding", "页边距", 12, 72, 2, " px", 0),
        )
        for key, label, low, high, step, unit, dec in specs:
            ctrl = _SliderControl(
                label, low, high, step, unit, dec, content, commit_on_release=True
            )
            ctrl.valueChanged.connect(lambda _v: self._emit_change())
            self.sliders[key] = ctrl
            body.addWidget(ctrl)

        # Divider + footer
        sep = QFrame(self)
        sep.setObjectName("popoverDivider")
        sep.setFixedHeight(1)
        outer.addWidget(sep)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 2, 0, 0)
        self.live_note = QLabel("滑块松开后应用", self)
        self.live_note.setObjectName("status")
        self.more_button = QPushButton("更多配置 →", self)
        self.more_button.setObjectName("popoverLink")
        self.more_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.more_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more_button.clicked.connect(self._open_more)
        footer.addWidget(self.live_note)
        footer.addStretch(1)
        footer.addWidget(self.more_button)
        outer.addLayout(footer)

        self._wheel_filter = _PreferencesWheelFilter(scroll.viewport(), self)
        self.font_combo.installEventFilter(self._wheel_filter)
        if self.font_combo.lineEdit():
            self.font_combo.lineEdit().installEventFilter(self._wheel_filter)
        for control in self.sliders.values():
            control.slider.installEventFilter(self._wheel_filter)

    def sync_settings(self, settings, palette: dict[str, str]):
        self._syncing = True
        self._settings = replace(settings)
        self._language = settings.language
        self._palette = dict(palette)

        for code, card in self.theme_cards.items():
            card.setChecked(settings.theme == code)
            card.retranslate(self._language)
            card.apply_card_style(palette["accent"])

        self.font_combo.setCurrentFont(QFont(settings.font_family))
        for key, ctrl in self.sliders.items():
            ctrl.set_value(getattr(settings, key))
            ctrl.retranslate(self._language)
            ctrl.apply_palette(palette)

        self.header_label.setText(tr("排版外观", self._language))
        self.theme_section_label.setText(tr("阅读主题", self._language))
        self.font_label.setText(tr("正文字体", self._language))
        self.live_note.setText(tr("滑块松开后应用", self._language))
        self.more_button.setText(f"{tr('更多配置', self._language)} →")
        self._syncing = False

    def _pick_theme(self, code: str):
        for name, card in self.theme_cards.items():
            card.setChecked(name == code)
            card.apply_card_style(self._palette["accent"])
        self._emit_change(theme_override=code)

    def _emit_change(self, *_args, theme_override: str | None = None):
        if self._syncing or self._settings is None:
            return
        selected_theme = theme_override or next(
            (code for code, card in self.theme_cards.items() if card.isChecked()),
            self._settings.theme,
        )
        updated = replace(
            self._settings,
            theme=selected_theme,
            font_family=self.font_combo.currentFont().family(),
            font_size=int(self.sliders["font_size"].value()),
            line_height=float(self.sliders["line_height"].value()),
            letter_spacing=float(self.sliders["letter_spacing"].value()),
            paragraph_spacing=int(self.sliders["paragraph_spacing"].value()),
            padding=int(self.sliders["padding"].value()),
        )
        self._settings = updated
        self.settings_changed.emit(updated)

    def _open_more(self):
        self.dismiss()
        self.more_settings_requested.emit()
