"""In-canvas floating Focus mode quick-settings popover (sibling of AppearancePopover)."""

from dataclasses import replace

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from quietreader.i18n import tr
from quietreader.ui.appearance_popover import _SliderControl
from quietreader.ui.controls import (
    ColorSwatchChip, FloatingPopover, SegmentedControl, ToggleSwitch,
)
from quietreader.ui.settings import _PreferencesWheelFilter
from quietreader.ui.tokens import resolve_palette


class _FocusScrollArea(QScrollArea):
    def sizeHint(self) -> QSize:
        content = self.widget()
        return content.sizeHint() if content is not None else super().sizeHint()


class FocusPopover(FloatingPopover):
    """Floating Focus mode quick-configuration card anchored inside MainWindow."""

    settings_changed = Signal(object)
    enter_focus_requested = Signal()
    resample_requested = Signal()
    eyedropper_requested = Signal()
    more_settings_requested = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self._settings = None
        self._sampled_color = ""
        self._sampled_complex = False
        self._has_book = False
        self._syncing = False
        self._language = "system"
        self._palette = resolve_palette("light")
        self.setFixedWidth(308)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 12)
        outer.setSpacing(11)

        # Header row: title + focus shortcut badge
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        self.header_label = QLabel("专注模式", self)
        self.header_label.setObjectName("popoverHeading")
        self.shortcut_badge = QLabel("F10 进出", self)
        self.shortcut_badge.setObjectName("popoverBadge")
        head.addWidget(self.header_label)
        head.addStretch(1)
        head.addWidget(self.shortcut_badge)
        outer.addLayout(head)

        scroll = _FocusScrollArea(self)
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

        # Row 1: Auto-match surrounding background toggle
        auto_row = QHBoxLayout()
        auto_row.setContentsMargins(0, 0, 0, 0)
        auto_row.setSpacing(8)
        self.auto_label = QLabel("自动适配周边背景色", content)
        self.auto_label.setObjectName("popoverLabel")
        self.auto_toggle = ToggleSwitch(False, content)
        self.auto_toggle.toggled.connect(self._on_auto_toggled)
        auto_row.addWidget(self.auto_label)
        auto_row.addStretch(1)
        auto_row.addWidget(self.auto_toggle)
        body.addLayout(auto_row)

        # Row 2: Trigger mode segmented control (shown when auto-match is ON)
        self.trigger_wrap = QWidget(content)
        trigger_layout = QVBoxLayout(self.trigger_wrap)
        trigger_layout.setContentsMargins(0, 0, 0, 0)
        trigger_layout.setSpacing(5)
        self.trigger_label = QLabel("采样触发方式", self.trigger_wrap)
        self.trigger_label.setObjectName("popoverLabel")
        self.trigger_control = SegmentedControl(self.trigger_wrap)
        self.trigger_auto_btn = self.trigger_control.add_segment("auto", "进专注与移动后")
        self.trigger_manual_btn = self.trigger_control.add_segment("manual", "仅手动快捷键")
        self.trigger_control.currentChanged.connect(self._on_trigger_changed)
        trigger_layout.addWidget(self.trigger_label)
        trigger_layout.addWidget(self.trigger_control)
        body.addWidget(self.trigger_wrap)

        # Row 3: Active Focus background color + quick actions
        color_head = QHBoxLayout()
        color_head.setContentsMargins(0, 0, 0, 0)
        color_head.setSpacing(8)
        self.color_label = QLabel("手动专注底色", content)
        self.color_label.setObjectName("popoverLabel")
        self.color_swatch = ColorSwatchChip(content)
        self.color_swatch.clicked.connect(self._on_swatch_clicked)
        color_head.addWidget(self.color_label)
        color_head.addStretch(1)
        color_head.addWidget(self.color_swatch)
        body.addLayout(color_head)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(6)
        self.resample_button = self._action_btn("立即采样", self._on_resample_clicked, content)
        self.choose_color_button = self._action_btn("选择颜色", self._choose_bg_color, content)
        self.eyedropper_button = self._action_btn("桌面取色", self._on_eyedropper_clicked, content)
        self.reset_color_button = self._action_btn("跟随主题", self._reset_bg_color, content)
        action_row.addWidget(self.resample_button, 1)
        action_row.addWidget(self.choose_color_button, 1)
        action_row.addWidget(self.eyedropper_button, 1)
        action_row.addWidget(self.reset_color_button, 1)
        body.addLayout(action_row)

        # Row 4: Focus background opacity & complex-background opacity cap sliders
        self.opacity_slider = _SliderControl(
            "背景不透明度", 0, 100, 1, "%", 0, content, commit_on_release=True
        )
        self.opacity_slider.valueChanged.connect(self._on_opacity_changed)
        body.addWidget(self.opacity_slider)

        self.complex_opacity_slider = _SliderControl(
            "复杂背景不透明度上限", 0, 100, 1, "%", 0, content, commit_on_release=True
        )
        self.complex_opacity_slider.valueChanged.connect(self._on_complex_opacity_changed)
        body.addWidget(self.complex_opacity_slider)

        # Primary CTA pinned below scroll area so 1-click Focus entry stays visible on short windows
        self.enter_focus_button = QPushButton("进入专注模式 (F10)", self)
        self.enter_focus_button.setObjectName("primary")
        self.enter_focus_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.enter_focus_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.enter_focus_button.clicked.connect(self._on_enter_focus)
        outer.addWidget(self.enter_focus_button)

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
        self.opacity_slider.slider.installEventFilter(self._wheel_filter)
        self.complex_opacity_slider.slider.installEventFilter(self._wheel_filter)

    @staticmethod
    def _action_btn(label: str, callback, parent: QWidget) -> QPushButton:
        btn = QPushButton(label, parent)
        btn.setObjectName("popoverActionBtn")
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(callback)
        return btn

    def sync_settings(
        self,
        settings,
        palette: dict[str, str],
        sampled_color: str = "",
        has_book: bool = True,
        sampled_complex: bool = False,
    ):
        self._syncing = True
        prev_auto_vis = self.trigger_wrap.isVisible()
        prev_lang = self._language
        self._settings = replace(settings)
        self._sampled_color = sampled_color
        self._sampled_complex = bool(sampled_complex)
        self._has_book = bool(has_book)
        self._language = settings.language
        self._palette = dict(palette)

        auto_on = bool(settings.focus_auto_background)
        self.auto_toggle.setChecked(auto_on)
        self.auto_toggle.apply_palette(palette)
        self.trigger_wrap.setVisible(auto_on)
        self.trigger_control.set_current(settings.focus_sample_trigger, emit=False)
        self.complex_opacity_slider.setVisible(auto_on)

        self.resample_button.setVisible(auto_on)
        self.choose_color_button.setVisible(not auto_on)
        self.reset_color_button.setVisible(not auto_on)

        self.color_swatch.apply_palette(palette)
        if auto_on:
            chip_hex = sampled_color
            chip_label = sampled_color.upper() if sampled_color else tr("未采样", self._language)
            color_heading = "已采外沿底色 · 复杂背景" if (sampled_color and self._sampled_complex) else "已采外沿底色"
            self.color_label.setText(tr(color_heading, self._language))
        else:
            chip_hex = settings.background_color
            chip_label = settings.background_color.upper() if settings.background_color else tr("主题色", self._language)
            self.color_label.setText(tr("手动专注底色", self._language))
        self.color_swatch.set_color(chip_hex, palette.get("paper", "#f6f4ec"), chip_label)

        self.opacity_slider.set_value(round(max(0.0, min(1.0, settings.opacity)) * 100))
        self.opacity_slider.retranslate(self._language)
        self.opacity_slider.apply_palette(palette)

        self.complex_opacity_slider.set_value(round(max(0.0, min(1.0, settings.focus_complex_opacity)) * 100))
        self.complex_opacity_slider.retranslate(self._language)
        self.complex_opacity_slider.apply_palette(palette)

        self.header_label.setText(tr("专注模式", self._language))
        self.shortcut_badge.setText(tr("{key} 进出", self._language, key=settings.focus_key))
        self.auto_label.setText(tr("自动适配周边背景色", self._language))
        self.trigger_label.setText(tr("采样触发方式", self._language))
        self.trigger_auto_btn.setText(tr("进专注与移动后", self._language))
        self.trigger_manual_btn.setText(tr("仅手动快捷键", self._language))
        self.resample_button.setText(f"{tr('立即采样', self._language)} ({settings.focus_resample_key})")
        self.choose_color_button.setText(tr("选择颜色", self._language))
        self.eyedropper_button.setText(tr("桌面取色", self._language))
        self.reset_color_button.setText(tr("跟随主题", self._language))
        self.enter_focus_button.setText(tr("进入专注模式 ({key})", self._language, key=settings.focus_key))
        self.enter_focus_button.setEnabled(self._has_book)
        self.enter_focus_button.setToolTip(
            "" if self._has_book else tr("请先打开一本书籍再进入专注模式", self._language)
        )
        self.live_note.setText(tr("滑块松开后应用", self._language))
        self.more_button.setText(f"{tr('更多配置', self._language)} →")

        self._syncing = False
        if not self.isHidden() and (prev_auto_vis != auto_on or prev_lang != self._language):
            self.reposition()

    def _emit_updated(self, updated):
        self._settings = updated
        self.settings_changed.emit(updated)

    def _on_auto_toggled(self, checked: bool):
        if self._syncing or self._settings is None:
            return
        self._emit_updated(replace(self._settings, focus_auto_background=bool(checked)))

    def _on_trigger_changed(self, key: str):
        if self._syncing or self._settings is None or key not in ("auto", "manual"):
            return
        self._emit_updated(replace(self._settings, focus_sample_trigger=key))

    def _on_swatch_clicked(self):
        if self._settings is None:
            return
        if self._settings.focus_auto_background:
            self._on_resample_clicked()
        else:
            self._choose_bg_color()

    def _on_resample_clicked(self):
        self.resample_requested.emit()

    def _choose_bg_color(self):
        if self._settings is None:
            return
        color = QColorDialog.getColor(
            QColor(self._settings.background_color or self._palette.get("paper", "#f6f4ec")),
            self, tr("选择专注模式背景色", self._language),
            QColorDialog.ColorDialogOption.DontUseNativeDialog,
        )
        if color.isValid():
            self._emit_updated(
                replace(self._settings, background_color=color.name().lower(), focus_auto_background=False)
            )

    def _on_eyedropper_clicked(self):
        self.dismiss()
        self.eyedropper_requested.emit()

    def _reset_bg_color(self):
        if self._settings is None:
            return
        if self._settings.background_color:
            self._emit_updated(replace(self._settings, background_color=""))

    def _on_opacity_changed(self, value: float):
        if self._syncing or self._settings is None:
            return
        self._emit_updated(replace(self._settings, opacity=round(max(0.0, min(100.0, value)) / 100.0, 2)))

    def _on_complex_opacity_changed(self, value: float):
        if self._syncing or self._settings is None:
            return
        self._emit_updated(
            replace(self._settings, focus_complex_opacity=round(max(0.0, min(100.0, value)) / 100.0, 2))
        )

    def _on_enter_focus(self):
        if not self._has_book:
            return
        self.dismiss()
        self.enter_focus_requested.emit()

    def _open_more(self):
        self.dismiss()
        self.more_settings_requested.emit()
