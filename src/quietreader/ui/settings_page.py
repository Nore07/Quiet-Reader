"""Embedded in-page two-column preferences panel (no native OS modal popup)."""

from dataclasses import replace

from PySide6.QtCore import QEvent, QRect, QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QBoxLayout, QColorDialog, QFontComboBox, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QStackedWidget,
    QVBoxLayout, QWidget,
)

from quietreader import __version__
from quietreader.desktop.color_picker import DesktopColorPicker
from quietreader.domain.models import Settings
from quietreader.i18n import tr
from quietreader.keys import BINDING_FIELDS, binding_error
from quietreader.ui.appearance_popover import _SliderControl, _ThemeCard
from quietreader.ui.controls import (
    ColorSwatchChip, QuietComboBox, SegmentedControl, ThinSlider, ToggleSwitch,
)
from quietreader.ui.icons import render_icon_pixmap
from quietreader.ui.key_capture import KeyBindingButton
from quietreader.ui.settings import GLOBAL_HOTKEY_DESCRIPTION, _PreferencesWheelFilter
from quietreader.ui.tokens import resolve_palette
from quietreader.updates import PROJECT_URL, RELEASES_URL


_SECTION_SPECS = (
    ("appearance", "palette", "外观与语言", "选择阅读主题配色与界面显示语言；调整即刻生效。"),
    ("reading", "typography", "阅读与排版", "自定义正文字体、字号、行距与页边距，打造舒适版面。"),
    ("focus", "focus", "专注与窗口", "配置专注模式背景、透明度与窗口置顶、托盘等桌面行为。"),
    ("shortcuts", "keyboard", "快捷键", "点击按键即可重新录入；摸鱼模式开启期间，模式热键全局生效。"),
    ("storage", "storage", "数据与存储", "书籍、阅读进度和配置均保存在本地数据目录。"),
    ("about", "info", "关于", "查看 QuietReader 的版本和项目链接。"),
)
_SETTINGS_RAIL_MIN_WIDTH = 720
_CARD_HORIZONTAL_PADDING = 16
_MIN_ROW_TEXT_WIDTH = 160


class _SettingsNavButton(QAbstractButton):
    """Left-rail section tab painted cleanly with vector icon and subtle checked pill."""

    def __init__(self, key: str, icon_name: str, label_source: str, parent=None):
        super().__init__(parent)
        self.key = key
        self._icon_name = icon_name
        self.label_source = label_source
        self._label = label_source
        self._hovered = False
        self._palette = resolve_palette("light")
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(36)

    def retranslate(self, language: str):
        self._label = tr(self.label_source, language)
        self.setText(self._label)
        self.setAccessibleName(self._label)
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
            painter.drawRoundedRect(rect, 8.0, 8.0)
        elif self._hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self._palette["accent_hover"]))
            painter.drawRoundedRect(rect, 8.0, 8.0)

        icon_color = self._palette["accent"] if checked else self._palette["muted"]
        pm = render_icon_pixmap(self._icon_name, icon_color, 15)
        painter.drawPixmap(12, (self.height() - 15) // 2, pm)

        font = QFont(self.font())
        font.setPixelSize(12.5)
        if checked:
            font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(self._palette["ink"] if checked else self._palette["ink_secondary"]))
        text_rect = QRect(35, 0, max(20, self.width() - 44), self.height())
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            QFontMetrics(font).elidedText(self._label, Qt.TextElideMode.ElideRight, text_rect.width()),
        )
        painter.end()


_ColorSwatchChip = ColorSwatchChip


class SettingsPage(QWidget):
    """Two-column in-page settings view with live immediate saving and narrow responsiveness."""

    settings_changed = Signal(object)
    capture_started = Signal()
    capture_finished = Signal()
    notice_requested = Signal(str)
    close_requested = Signal()
    check_update_requested = Signal()

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._settings = Settings()
        self._palette = resolve_palette("light")
        self._syncing = False
        self._active_section = "appearance"
        self._responsive_rows: list[QBoxLayout] = []
        self._responsive_control_groups: list[QBoxLayout] = []
        self._translatable_labels: list[tuple[QLabel, str]] = []
        self._translatable_buttons: list[tuple[QPushButton, str]] = []
        self._themed_widgets: list[object] = []
        self.toggles: dict[str, ToggleSwitch] = {}

        # Exposed attribute for DesktopColorPicker / ColorOverlay language discovery
        self.language = "system"

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 2, 0, 0)
        outer.setSpacing(14)

        # ---- Left section rail ----
        self.rail = QFrame(self)
        self.rail.setObjectName("settingsRail")
        self.rail.setFixedWidth(204)
        rail_layout = QVBoxLayout(self.rail)
        rail_layout.setContentsMargins(8, 10, 8, 12)
        rail_layout.setSpacing(4)

        self.nav_buttons: dict[str, _SettingsNavButton] = {}
        for key, icon_name, label_src, _desc in _SECTION_SPECS:
            btn = _SettingsNavButton(key, icon_name, label_src, self.rail)
            btn.clicked.connect(lambda _checked=False, target=key: self.select_section(target))
            self.nav_buttons[key] = btn
            self._themed_widgets.append(btn)
            rail_layout.addWidget(btn)

        rail_layout.addStretch(1)

        rail_sep = QFrame(self.rail)
        rail_sep.setObjectName("settingsDivider")
        rail_sep.setFixedHeight(1)
        rail_layout.addWidget(rail_sep)

        self.rail_brand = QLabel("QuietReader · 静读", self.rail)
        self.rail_brand.setObjectName("settingsRailFoot")
        self.rail_brand.setContentsMargins(8, 6, 8, 0)
        self.rail_mode = QLabel("state.json", self.rail)
        self.rail_mode.setObjectName("settingsRailSub")
        self.rail_mode.setContentsMargins(8, 0, 8, 0)
        rail_layout.addWidget(self.rail_brand)
        rail_layout.addWidget(self.rail_mode)
        outer.addWidget(self.rail)

        # ---- Right content & persistent bottom bar ----
        right = QWidget(self)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        # Narrow-window section picker replaces the left rail.
        self.narrow_section_combo = QuietComboBox(right)
        self.narrow_section_combo.setObjectName("shelfSortCombo")
        for key, _icon, label_src, _desc in _SECTION_SPECS:
            self.narrow_section_combo.addItem(label_src, key)
        self.narrow_section_combo.currentIndexChanged.connect(self._on_narrow_section_changed)
        self.narrow_section_combo.hide()
        self._themed_widgets.append(self.narrow_section_combo)
        right_layout.addWidget(self.narrow_section_combo)

        self.scroll = QScrollArea(right)
        self.scroll.setObjectName("settingsScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setObjectName("settingsViewport")

        self.scroll_host = QWidget()
        self.scroll_host.setObjectName("settingsViewport")
        host_layout = QVBoxLayout(self.scroll_host)
        host_layout.setContentsMargins(6, 6, 12, 16)
        host_layout.setSpacing(0)

        self.section_stack = QStackedWidget(self.scroll_host)
        # Inactive sections must not force the scroll area's content wider than its viewport.
        self.section_stack.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.section_pages: dict[str, QWidget] = {}
        self._build_appearance_section()
        self._build_reading_section()
        self._build_focus_section()
        self._build_shortcuts_section()
        self._build_storage_section()
        self._build_about_section()
        host_layout.addWidget(self.section_stack, 1)
        self.scroll.setWidget(self.scroll_host)
        self.scroll.viewport().installEventFilter(self)
        right_layout.addWidget(self.scroll, 1)

        # Footer bar
        self.footer = QFrame(right)
        self.footer.setObjectName("settingsFooter")
        footer_layout = QHBoxLayout(self.footer)
        footer_layout.setContentsMargins(6, 10, 8, 2)
        footer_layout.setSpacing(10)
        self.auto_save_note = self._tr_label("所有更改实时保存，无需弹窗二次确认", "status")
        self.auto_save_note.setWordWrap(True)
        self.reset_button = self._tr_button("恢复本页默认", self.reset_current_section, "secondaryAction")
        self.reset_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        footer_layout.addWidget(self.auto_save_note, 1)
        footer_layout.addWidget(self.reset_button)
        right_layout.addWidget(self.footer)

        outer.addWidget(right, 1)

        # Desktop eyedropper (uses window deduplication in DesktopColorPicker)
        self.picker = DesktopColorPicker(self)
        self.picker.selected.connect(self._on_picked_bg_color)
        self.picker.failed.connect(lambda msg: self.notice_requested.emit(msg))

        # Route wheel events from settings controls to the ScrollArea instead of changing values.
        self._wheel_filter = _PreferencesWheelFilter(self.scroll.viewport(), self)
        self.font_combo.installEventFilter(self._wheel_filter)
        if self.font_combo.lineEdit():
            self.font_combo.lineEdit().installEventFilter(self._wheel_filter)
        self.opacity_slider.installEventFilter(self._wheel_filter)
        self.complex_opacity_slider.installEventFilter(self._wheel_filter)
        for control in self.typo_sliders.values():
            control.slider.installEventFilter(self._wheel_filter)

        self.select_section("appearance")

    # ---- Layout builder helpers ----

    def _tr_label(self, source: str, object_name: str = "") -> QLabel:
        label = QLabel(source)
        label.setTextFormat(Qt.TextFormat.PlainText)
        if object_name:
            label.setObjectName(object_name)
        self._translatable_labels.append((label, source))
        return label

    def _tr_button(self, source: str, callback, object_name: str = "") -> QPushButton:
        btn = QPushButton(source)
        if object_name:
            btn.setObjectName(object_name)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(callback)
        self._translatable_buttons.append((btn, source))
        return btn

    def _make_section_page(self, key: str, title_src: str, desc_src: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        head = QVBoxLayout()
        head.setContentsMargins(2, 0, 2, 2)
        head.setSpacing(4)
        title = self._tr_label(title_src, "settingsPageTitle")
        title.setWordWrap(True)
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        desc = self._tr_label(desc_src, "settingsPageDesc")
        desc.setWordWrap(True)
        desc.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        head.addWidget(title)
        head.addWidget(desc)
        layout.addLayout(head)

        self.section_pages[key] = page
        self.section_stack.addWidget(page)
        return page, layout

    @staticmethod
    def _make_card(parent: QWidget) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame(parent)
        card.setObjectName("settingsCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(_CARD_HORIZONTAL_PADDING, 4, _CARD_HORIZONTAL_PADDING, 4)
        layout.setSpacing(0)
        return card, layout

    def _add_card_divider(self, card_layout: QVBoxLayout):
        sep = QFrame()
        sep.setObjectName("settingsDivider")
        sep.setFixedHeight(1)
        card_layout.addWidget(sep)

    def _add_card_row(
        self,
        card_layout: QVBoxLayout,
        title_src: str,
        desc_src: str,
        control: QWidget,
        responsive: bool = False,
        add_divider_before: bool = False,
        stacked: bool = False,
    ):
        if add_divider_before:
            self._add_card_divider(card_layout)
        row_host = QWidget()
        direction = QBoxLayout.Direction.TopToBottom if stacked else QBoxLayout.Direction.LeftToRight
        row_layout = QBoxLayout(direction, row_host)
        row_layout.setContentsMargins(0, 12, 0, 12)
        row_layout.setSpacing(14)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(3)
        title_lbl = self._tr_label(title_src, "settingsRowTitle")
        title_lbl.setWordWrap(True)
        title_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        text_col.addWidget(title_lbl)
        if desc_src:
            desc_lbl = self._tr_label(desc_src, "settingsRowSub")
            desc_lbl.setWordWrap(True)
            desc_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
            text_col.addWidget(desc_lbl)

        row_layout.addLayout(text_col, 1)
        row_layout.addWidget(control, 0)
        if responsive:
            self._responsive_rows.append(row_layout)
        card_layout.addWidget(row_host)

    # ---- Section 1: Appearance & Language ----

    def _build_appearance_section(self):
        _, _, title_src, desc_src = _SECTION_SPECS[0]
        page, layout = self._make_section_page("appearance", title_src, desc_src)

        card, card_layout = self._make_card(page)
        theme_wrap = QWidget(card)
        tw_layout = QVBoxLayout(theme_wrap)
        tw_layout.setContentsMargins(0, 12, 0, 14)
        tw_layout.setSpacing(10)
        tw_layout.addWidget(self._tr_label("阅读主题", "settingsRowTitle"))

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        self.theme_cards: dict[str, _ThemeCard] = {}
        for col, (code, label) in enumerate((("light", "纸白"), ("sepia", "暖纸"), ("dark", "夜读"), ("system", "跟随系统"))):
            tcard = _ThemeCard(code, label, theme_wrap)
            tcard.setFixedHeight(60)
            tcard.clicked.connect(lambda _c=False, target=code: self._on_theme_picked(target))
            self.theme_cards[code] = tcard
            grid.addWidget(tcard, 0, col)
        tw_layout.addLayout(grid)
        card_layout.addWidget(theme_wrap)

        self.language_control = SegmentedControl(card)
        for code, label in (("system", "跟随系统"), ("zh_CN", "简体中文"), ("en", "English")):
            btn = self.language_control.add_segment(code, label)
            if code == "system":
                self._translatable_buttons.append((btn, "跟随系统"))
        self.language_control.currentChanged.connect(self._on_language_changed)
        self._add_card_row(
            card_layout,
            "界面语言",
            "切换界面语言后立即生效，不影响书籍正文内容",
            self.language_control,
            responsive=True,
            add_divider_before=True,
        )
        layout.addWidget(card)
        layout.addStretch(1)

    # ---- Section 2: Reading & Layout ----

    def _build_reading_section(self):
        _, _, title_src, desc_src = _SECTION_SPECS[1]
        page, layout = self._make_section_page("reading", title_src, desc_src)

        card1, card1_layout = self._make_card(page)
        self.font_combo = QFontComboBox(card1)
        self.font_combo.setMinimumWidth(190)
        self.font_combo.setMaximumWidth(260)
        self.font_combo.currentFontChanged.connect(self._on_font_changed)
        self._add_card_row(card1_layout, "正文字体", "", self.font_combo, responsive=True)
        layout.addWidget(card1)

        card2, card2_layout = self._make_card(page)
        self.typo_sliders: dict[str, _SliderControl] = {}
        specs = (
            ("font_size", "字号", 14, 42, 1, " px", 0),
            ("line_height", "行距", 1.2, 2.5, 0.1, "×", 2),
            ("letter_spacing", "字距", 0.0, 5.0, 0.1, " px", 1),
            ("paragraph_spacing", "段距", 0, 40, 1, " px", 0),
            ("padding", "页边距", 12, 72, 2, " px", 0),
        )
        for idx, (key, label, low, high, step, unit, dec) in enumerate(specs):
            if idx > 0:
                self._add_card_divider(card2_layout)
            wrap = QWidget(card2)
            wl = QVBoxLayout(wrap)
            wl.setContentsMargins(0, 10, 0, 10)
            ctrl = _SliderControl(label, low, high, step, unit, dec, wrap)
            ctrl.valueChanged.connect(lambda _v, field=key: self._on_typo_slider_changed(field))
            self.typo_sliders[key] = ctrl
            self._themed_widgets.append(ctrl)
            wl.addWidget(ctrl)
            card2_layout.addWidget(wrap)
        layout.addWidget(card2)
        layout.addStretch(1)

    # ---- Section 3: Focus & Window ----

    def _build_focus_section(self):
        _, _, title_src, desc_src = _SECTION_SPECS[2]
        page, layout = self._make_section_page("focus", title_src, desc_src)

        # Card 1: Focus appearance & auto-blend
        card1, c1 = self._make_card(page)

        auto_bg_toggle = ToggleSwitch(False, card1)
        auto_bg_toggle.toggled.connect(lambda checked: self._on_toggle_changed("focus_auto_background", checked))
        self.toggles["focus_auto_background"] = auto_bg_toggle
        self._themed_widgets.append(auto_bg_toggle)
        self._add_card_row(
            c1, "自动适配窗口周边背景色",
            "进入专注或拖动调整后自动采样窗口外沿主色，避免切窗时反复手动取色（无后台持续录屏）",
            auto_bg_toggle, responsive=False,
        )

        self.sample_trigger_control = SegmentedControl(card1)
        for key, label in (("auto", "进专注与移动后"), ("manual", "仅手动快捷键")):
            seg_btn = self.sample_trigger_control.add_segment(key, label)
            self._translatable_buttons.append((seg_btn, label))
        self.sample_trigger_control.currentChanged.connect(self._on_sample_trigger_changed)
        self._add_card_row(
            c1, "采样触发方式",
            "可随时按快捷键重采，也可随时关闭切回手动固定色",
            self.sample_trigger_control, responsive=True, add_divider_before=True,
        )

        bg_controls = QWidget(card1)
        bg_outer = QBoxLayout(QBoxLayout.Direction.LeftToRight, bg_controls)
        bg_outer.setContentsMargins(0, 0, 0, 0)
        bg_outer.setSpacing(6)
        bg_outer.setAlignment(Qt.AlignmentFlag.AlignLeft)
        bg_pair1 = QHBoxLayout()
        bg_pair1.setContentsMargins(0, 0, 0, 0)
        bg_pair1.setSpacing(6)
        self.bg_swatch = _ColorSwatchChip(bg_controls)
        self.bg_swatch.clicked.connect(self._choose_bg_color)
        self._themed_widgets.append(self.bg_swatch)
        self.bg_choose_btn = self._tr_button("选择颜色", self._choose_bg_color, "settingsBtn")
        self.bg_choose_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        bg_pair1.addWidget(self.bg_swatch)
        bg_pair1.addWidget(self.bg_choose_btn)
        bg_pair1.setAlignment(Qt.AlignmentFlag.AlignLeft)

        bg_pair2 = QHBoxLayout()
        bg_pair2.setContentsMargins(0, 0, 0, 0)
        bg_pair2.setSpacing(6)
        self.bg_pick_btn = self._tr_button("桌面取色", self._begin_eyedropper, "settingsBtn")
        self.bg_pick_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.bg_reset_btn = self._tr_button("跟随主题", self._reset_bg_color, "settingsBtn")
        self.bg_reset_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        bg_pair2.addWidget(self.bg_pick_btn)
        bg_pair2.addWidget(self.bg_reset_btn)
        bg_pair2.setAlignment(Qt.AlignmentFlag.AlignLeft)

        bg_outer.addLayout(bg_pair1)
        bg_outer.addLayout(bg_pair2)
        self._responsive_control_groups.append(bg_outer)
        self._add_card_row(
            c1, "专注模式背景色",
            "关闭自动适配时生效；取色时窗口暂时隐藏，结果仅保存在本机",
            bg_controls, responsive=True, add_divider_before=True,
        )

        text_controls = QWidget(card1)
        text_outer = QBoxLayout(QBoxLayout.Direction.LeftToRight, text_controls)
        text_outer.setContentsMargins(0, 0, 0, 0)
        text_outer.setSpacing(6)
        text_outer.setAlignment(Qt.AlignmentFlag.AlignLeft)
        text_pair1 = QHBoxLayout()
        text_pair1.setContentsMargins(0, 0, 0, 0)
        text_pair1.setSpacing(6)
        self.text_swatch = _ColorSwatchChip(text_controls)
        self.text_swatch.clicked.connect(self._choose_text_color)
        self._themed_widgets.append(self.text_swatch)
        text_pair1.addWidget(self.text_swatch)
        text_pair1.setAlignment(Qt.AlignmentFlag.AlignLeft)

        text_pair2 = QHBoxLayout()
        text_pair2.setContentsMargins(0, 0, 0, 0)
        text_pair2.setSpacing(6)
        self.text_choose_btn = self._tr_button("选择颜色", self._choose_text_color, "settingsBtn")
        self.text_choose_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.text_reset_btn = self._tr_button("自动文字颜色", self._reset_text_color, "settingsBtn")
        self.text_reset_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        text_pair2.addWidget(self.text_choose_btn)
        text_pair2.addWidget(self.text_reset_btn)
        text_pair2.setAlignment(Qt.AlignmentFlag.AlignLeft)

        text_outer.addLayout(text_pair1)
        text_outer.addLayout(text_pair2)
        self._responsive_control_groups.append(text_outer)
        self._add_card_row(
            c1, "文字颜色",
            "留空时根据背景亮度自动计算柔和对比度",
            text_controls, responsive=True, add_divider_before=True,
        )

        opacity_wrap = QWidget(card1)
        op_row = QHBoxLayout(opacity_wrap)
        op_row.setContentsMargins(0, 0, 0, 0)
        op_row.setSpacing(10)
        self.opacity_slider = ThinSlider(Qt.Orientation.Horizontal, opacity_wrap)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.setMinimumWidth(140)
        self.opacity_slider.setMaximumWidth(190)
        self.opacity_slider.valueChanged.connect(self._on_opacity_changed)
        self._themed_widgets.append(self.opacity_slider)
        self.opacity_value = QLabel("92%", opacity_wrap)
        self.opacity_value.setObjectName("popoverValue")
        self.opacity_value.setFixedWidth(38)
        self.opacity_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        op_row.addWidget(self.opacity_slider, 1)
        op_row.addWidget(self.opacity_value)
        self._add_card_row(
            c1, "专注模式背景不透明度",
            "设为 0% 时背景全透明、文字保持不透明；默认 F9 在 0% 与 100% 间切换",
            opacity_wrap, responsive=True, add_divider_before=True,
        )

        complex_opacity_wrap = QWidget(card1)
        cop_row = QHBoxLayout(complex_opacity_wrap)
        cop_row.setContentsMargins(0, 0, 0, 0)
        cop_row.setSpacing(10)
        self.complex_opacity_slider = ThinSlider(Qt.Orientation.Horizontal, complex_opacity_wrap)
        self.complex_opacity_slider.setRange(0, 100)
        self.complex_opacity_slider.setMinimumWidth(140)
        self.complex_opacity_slider.setMaximumWidth(190)
        self.complex_opacity_slider.valueChanged.connect(self._on_complex_opacity_changed)
        self._themed_widgets.append(self.complex_opacity_slider)
        self.complex_opacity_value = QLabel("20%", complex_opacity_wrap)
        self.complex_opacity_value.setObjectName("popoverValue")
        self.complex_opacity_value.setFixedWidth(38)
        self.complex_opacity_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        cop_row.addWidget(self.complex_opacity_slider, 1)
        cop_row.addWidget(self.complex_opacity_value)
        self._add_card_row(
            c1, "复杂背景不透明度上限",
            "开启自动适配且检测到复杂背景时，不透明度自动降至不超过此值（默认 20%）",
            complex_opacity_wrap, responsive=True, add_divider_before=True,
        )
        layout.addWidget(card1)

        # Card 2: Window behavior
        card2, c2 = self._make_card(page)

        self.adjust_key_control = SegmentedControl(card2)
        for key in ("Alt", "Ctrl", "Shift"):
            self.adjust_key_control.add_segment(key, key)
        self.adjust_key_control.currentChanged.connect(self._on_adjust_key_changed)
        self._add_card_row(
            c2, "专注模式窗口调整键",
            "专注模式下按住此键，拖动窗口内可移动、拖动边缘可缩放",
            self.adjust_key_control, responsive=True,
        )

        toggle_specs = (
            ("always_on_top", "窗口始终置顶", "让阅读窗口保持在桌面其他窗口之上"),
            ("close_to_tray", "关闭窗口时隐藏至托盘", "点击关闭按钮时最小化至系统托盘，而非退出程序"),
        )
        for field, title_s, sub_s in toggle_specs:
            sw = ToggleSwitch(False, card2)
            sw.toggled.connect(lambda checked, key=field: self._on_toggle_changed(key, checked))
            self.toggles[field] = sw
            self._themed_widgets.append(sw)
            self._add_card_row(c2, title_s, sub_s, sw, responsive=False, add_divider_before=True)
        layout.addWidget(card2)
        layout.addStretch(1)

    # ---- Section 4: Shortcuts ----

    def _build_shortcuts_section(self):
        _, _, title_src, desc_src = _SECTION_SPECS[3]
        page, layout = self._make_section_page("shortcuts", title_src, desc_src)

        card, card_layout = self._make_card(page)
        toggle = ToggleSwitch(False, card)
        toggle.toggled.connect(lambda checked: self._on_toggle_changed("global_hotkey", checked))
        self.toggles["global_hotkey"] = toggle
        self._themed_widgets.append(toggle)
        self._add_card_row(card_layout, "启用全局显示 / 隐藏和紧急退出快捷键", GLOBAL_HOTKEY_DESCRIPTION, toggle)
        layout.addWidget(card)

        self.binding_buttons: dict[str, KeyBindingButton] = {}
        groups = (
            (
                ("next_key", "下一页 / 下一句（阅读与摸鱼模式）"),
                ("previous_key", "上一页 / 上一句（阅读与摸鱼模式）"),
                ("next_chapter_key", "下一章（正文聚焦时）"),
                ("previous_chapter_key", "上一章（正文聚焦时）"),
            ),
            (
                ("focus_key", "切换专注模式"),
                ("focus_opacity_key", "切换专注模式透明度"),
                ("focus_resample_key", "重新采样专注背景色"),
                ("hotkey", "显示 / 隐藏快捷键"),
                ("emergency_key", "紧急退出快捷键"),
            ),
            (
                ("moyu_key", "切换摸鱼模式"),
                ("moyu_lines_key", "切换摸鱼模式单行 / 两行"),
            ),
        )
        for group in groups:
            card, c_layout = self._make_card(page)
            for idx, (field, label_src) in enumerate(group):
                btn = KeyBindingButton(getattr(self._settings, field), self._settings.language, card)
                btn.setObjectName("settingsKeyBtn")
                btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                btn.capture_started.connect(self.capture_started.emit)
                btn.capture_finished.connect(self.capture_finished.emit)
                btn.sequence_changed.connect(lambda seq, key=field: self._on_binding_changed(key, seq))
                self.binding_buttons[field] = btn
                self._add_card_row(c_layout, label_src, "", btn, responsive=True, add_divider_before=(idx > 0))
            layout.addWidget(card)
        layout.addStretch(1)

    # ---- Section 5: Data & Storage ----

    def _build_storage_section(self):
        _, _, title_src, desc_src = _SECTION_SPECS[4]
        page, layout = self._make_section_page("storage", title_src, desc_src)

        card, c_layout = self._make_card(page)
        self.storage_dir_value = QLabel("", card)
        self.storage_dir_value.setObjectName("settingsMonoValue")
        self.storage_dir_value.setWordWrap(True)
        self.storage_dir_value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        self.storage_dir_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._add_card_row(c_layout, "当前数据目录", "", self.storage_dir_value, stacked=True)

        self.storage_file_value = QLabel("", card)
        self.storage_file_value.setObjectName("settingsMonoValue")
        self.storage_file_value.setWordWrap(True)
        self.storage_file_value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        self.storage_file_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._add_card_row(c_layout, "配置文件", "", self.storage_file_value, stacked=True, add_divider_before=True)

        self.storage_books_value = QLabel("", card)
        self.storage_books_value.setObjectName("popoverValue")
        self._add_card_row(c_layout, "已收录书籍", "", self.storage_books_value, responsive=False, add_divider_before=True)

        self.storage_mode_value = QLabel("", card)
        self.storage_mode_value.setObjectName("settingsRowSub")
        self.storage_mode_value.setWordWrap(True)
        self._add_card_row(c_layout, "运行模式", "", self.storage_mode_value, stacked=True, add_divider_before=True)

        layout.addWidget(card)
        layout.addStretch(1)

    def _build_about_section(self):
        _, _, title, description = _SECTION_SPECS[5]
        page, layout = self._make_section_page("about", title, description)
        card, rows = self._make_card(page)
        version = QLabel(__version__, card)
        version.setObjectName("popoverValue")
        self._add_card_row(rows, "当前版本", "", version, responsive=False)

        toggle = ToggleSwitch(False, card)
        toggle.toggled.connect(lambda checked: self._on_toggle_changed("auto_check_updates", checked))
        self.toggles["auto_check_updates"] = toggle
        self._themed_widgets.append(toggle)
        self._add_card_row(rows, "自动检查更新", "开启后，启动时向 GitHub 查询公开版本，每天最多一次。",
                           toggle, add_divider_before=True)
        self.check_update_button = self._tr_button("检查更新", self.check_update_requested.emit, "settingsBtn")
        self._add_card_row(rows, "版本状态", "", self.check_update_button, add_divider_before=True)
        self.update_status_label = QLabel(card)
        self.update_status_label.setObjectName("settingsRowSub")
        self.update_status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.update_status_label.setWordWrap(True)
        rows.addWidget(self.update_status_label)
        self.release_button = self._tr_button("查看更新", self.open_update_release, "settingsBtn")
        rows.addWidget(self.release_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.set_update_status("idle")
        layout.addWidget(card)

        card, rows = self._make_card(page)
        rows.addWidget(self._tr_label("GitHub", "settingsRowTitle"))
        rows.addSpacing(6)
        links = QHBoxLayout()
        links.setSpacing(8)
        for label, url in (
            ("项目主页", PROJECT_URL),
            ("反馈问题", f"{PROJECT_URL}/issues"),
            ("发布记录", RELEASES_URL),
        ):
            button = self._tr_button(label, lambda _checked=False, link=url: QDesktopServices.openUrl(QUrl(link)), "settingsBtn")
            links.addWidget(button)
        links.addStretch(1)
        rows.addLayout(links)
        layout.addWidget(card)
        layout.addStretch(1)

    def set_update_status(self, status, release=None):
        self._update_status = status
        self._release_result = release
        self._refresh_update_status()

    def _refresh_update_status(self):
        messages = {
            "idle": "尚未检查更新。",
            "checking": "正在检查更新…",
            "available": "发现新版本 {version}。",
            "current": "当前已是最新公开版本。",
            "ahead": "当前版本高于公开发布版 {version}。",
            "error": "暂时无法检查更新，请稍后重试。",
        }
        release = self._release_result
        self.update_status_label.setText(tr(messages[self._update_status], self.language,
                                            version=release.version if release else ""))
        self.check_update_button.setEnabled(self._update_status != "checking")
        self.release_button.setVisible(self._update_status == "available")

    def open_update_release(self):
        if self._update_status == "available" and self._release_result is not None:
            QDesktopServices.openUrl(QUrl(self._release_result.url))

    # ---- Public API ----

    def select_section(self, key: str):
        if key not in self.section_pages:
            return
        self._active_section = key
        self.section_stack.setCurrentWidget(self.section_pages[key])
        for name, btn in self.nav_buttons.items():
            btn.setChecked(name == key)
        idx = self.narrow_section_combo.findData(key)
        if idx >= 0 and self.narrow_section_combo.currentIndex() != idx:
            self.narrow_section_combo.blockSignals(True)
            self.narrow_section_combo.setCurrentIndex(idx)
            self.narrow_section_combo.blockSignals(False)
        self.reset_button.setVisible(key != "storage")
        self.scroll.verticalScrollBar().setValue(0)

    def current_section(self) -> str:
        return self._active_section

    def sync_settings(self, settings: Settings, palette: dict[str, str]):
        self._syncing = True
        self._settings = replace(settings)
        self._palette = dict(palette)
        self.language = settings.language

        for code, card in self.theme_cards.items():
            card.setChecked(settings.theme == code)
            card.retranslate(settings.language)
            card.apply_card_style(palette["accent"])

        self.language_control.set_current(settings.language, emit=False)
        self.font_combo.setCurrentFont(QFont(settings.font_family))
        for key, ctrl in self.typo_sliders.items():
            ctrl.set_value(getattr(settings, key))
            ctrl.retranslate(settings.language)

        self._refresh_color_swatches()
        pct = round(max(0.0, min(1.0, settings.opacity)) * 100)
        self.opacity_slider.setValue(pct)
        self.opacity_value.setText(f"{pct}%")
        complex_pct = round(max(0.0, min(1.0, settings.focus_complex_opacity)) * 100)
        self.complex_opacity_slider.setValue(complex_pct)
        self.complex_opacity_value.setText(f"{complex_pct}%")
        self.adjust_key_control.set_current(settings.focus_adjust_key, emit=False)
        self.sample_trigger_control.set_current(settings.focus_sample_trigger, emit=False)
        for key, sw in self.toggles.items():
            sw.setChecked(bool(getattr(settings, key)))

        for key, btn in self.binding_buttons.items():
            btn.language = settings.language
            btn.set_sequence(getattr(settings, key))

        self.apply_palette(palette)
        self.retranslate(settings.language)
        self._refresh_storage_info()
        self._syncing = False

    def apply_palette(self, palette: dict[str, str]):
        self._palette = dict(palette)
        for widget in self._themed_widgets:
            widget.apply_palette(palette)
        for card in self.theme_cards.values():
            card.apply_card_style(palette["accent"])
        self._refresh_color_swatches()

    def retranslate(self, language: str):
        self.language = language
        for label, source in self._translatable_labels:
            label.setText(tr(source, language))
        for btn, source in self._translatable_buttons:
            btn.setText(tr(source, language))
        for key, btn in self.nav_buttons.items():
            btn.retranslate(language)
        for idx, (_key, _icon, label_src, _desc) in enumerate(_SECTION_SPECS):
            self.narrow_section_combo.setItemText(idx, tr(label_src, language))
        for card in self.theme_cards.values():
            card.retranslate(language)
        for ctrl in self.typo_sliders.values():
            ctrl.retranslate(language)
        for btn in self.binding_buttons.values():
            btn.language = language
            btn.refresh()
        self._refresh_color_swatches()
        self._refresh_storage_info()
        self._refresh_update_status()
        self._update_responsive_rows()

    def cancel_active_capture(self):
        self.picker.cancel()

    def reset_current_section(self):
        defaults = Settings()
        sec = self._active_section
        if sec == "appearance":
            updated = replace(self._settings, theme=defaults.theme, language=defaults.language)
        elif sec == "reading":
            updated = replace(
                self._settings,
                font_family=defaults.font_family,
                font_size=defaults.font_size,
                line_height=defaults.line_height,
                letter_spacing=defaults.letter_spacing,
                paragraph_spacing=defaults.paragraph_spacing,
                padding=defaults.padding,
            )
        elif sec == "focus":
            updated = replace(
                self._settings,
                focus_auto_background=defaults.focus_auto_background,
                focus_sample_trigger=defaults.focus_sample_trigger,
                background_color=defaults.background_color,
                text_color=defaults.text_color,
                opacity=defaults.opacity,
                focus_complex_opacity=defaults.focus_complex_opacity,
                focus_adjust_key=defaults.focus_adjust_key,
                always_on_top=defaults.always_on_top,
                close_to_tray=defaults.close_to_tray,
            )
        elif sec == "shortcuts":
            updated = replace(
                self._settings,
                global_hotkey=defaults.global_hotkey,
                moyu_lines=defaults.moyu_lines,
                **{key: getattr(defaults, key) for key in BINDING_FIELDS},
            )
        elif sec == "about":
            updated = replace(self._settings, auto_check_updates=defaults.auto_check_updates)
        else:
            return
        self._commit(updated)
        self.notice_requested.emit("配置已恢复默认。")

    # ---- Responsive narrow layout ----

    def resizeEvent(self, event):
        super().resizeEvent(event)
        narrow = self.width() < _SETTINGS_RAIL_MIN_WIDTH
        self.rail.setVisible(not narrow)
        self.narrow_section_combo.setVisible(narrow)

    def eventFilter(self, watched, event):
        if watched is self.scroll.viewport() and event.type() == QEvent.Type.Resize:
            self._update_responsive_rows()
        return super().eventFilter(watched, event)

    def _update_responsive_rows(self):
        margins = self.scroll_host.layout().contentsMargins()
        # Keep the breakpoint stable when the vertical scrollbar appears.
        viewport_width = self.scroll.maximumViewportSize().width() - self.scroll.verticalScrollBar().sizeHint().width()
        row_width = viewport_width - margins.left() - margins.right() - 2 * _CARD_HORIZONTAL_PADDING
        horizontal = QBoxLayout.Direction.LeftToRight
        vertical = QBoxLayout.Direction.TopToBottom

        for group in self._responsive_control_groups:
            item_width = sum(group.itemAt(index).minimumSize().width() for index in range(group.count()))
            group_width = item_width + group.spacing() * (group.count() - 1)
            direction = horizontal if group_width <= row_width else vertical
            if group.direction() != direction:
                group.setDirection(direction)

        for row in self._responsive_rows:
            control_width = row.itemAt(1).sizeHint().width()
            required_width = _MIN_ROW_TEXT_WIDTH + row.spacing() + control_width
            direction = horizontal if required_width <= row_width else vertical
            if row.direction() != direction:
                row.setDirection(direction)

    # ---- Private slots ----

    def _on_narrow_section_changed(self, index: int):
        key = self.narrow_section_combo.itemData(index)
        if isinstance(key, str) and key in self.section_pages:
            self.select_section(key)

    def _commit(self, updated: Settings):
        self._settings = updated
        self.settings_changed.emit(updated)

    def _on_theme_picked(self, code: str):
        if self._syncing:
            return
        self._commit(replace(self._settings, theme=code))

    def _on_language_changed(self, code: str):
        if self._syncing:
            return
        self._commit(replace(self._settings, language=code))

    def _on_font_changed(self, font: QFont):
        if self._syncing:
            return
        family = font.family()
        if family and family != self._settings.font_family:
            self._commit(replace(self._settings, font_family=family))

    def _on_typo_slider_changed(self, field: str):
        if self._syncing:
            return
        ctrl = self.typo_sliders[field]
        val = ctrl.value()
        self._commit(replace(self._settings, **{field: val}))

    def _refresh_color_swatches(self):
        bg = self._settings.background_color
        self.bg_swatch.set_color(
            bg, self._palette.get("paper", "#f6f4ec"),
            bg.upper() if bg else tr("主题色", self.language),
        )
        fg = self._settings.text_color
        self.text_swatch.set_color(
            fg, self._palette.get("ink", "#1f3029"),
            fg.upper() if fg else tr("自动文字颜色", self.language),
        )

    def _choose_bg_color(self):
        color = QColorDialog.getColor(
            QColor(self._settings.background_color or self._palette.get("paper", "#f6f4ec")),
            self, tr("选择专注模式背景色", self.language),
            QColorDialog.ColorDialogOption.DontUseNativeDialog,
        )
        if color.isValid():
            self._commit(replace(self._settings, background_color=color.name().lower()))

    def _begin_eyedropper(self):
        self.picker.begin()

    def _on_picked_bg_color(self, color: QColor):
        if color.isValid():
            self._commit(replace(self._settings, background_color=color.name().lower()))

    def _reset_bg_color(self):
        if self._settings.background_color:
            self._commit(replace(self._settings, background_color=""))

    def _choose_text_color(self):
        color = QColorDialog.getColor(
            QColor(self._settings.text_color or self._palette.get("ink", "#1f3029")),
            self, tr("选择文字颜色", self.language),
            QColorDialog.ColorDialogOption.DontUseNativeDialog,
        )
        if color.isValid():
            self._commit(replace(self._settings, text_color=color.name().lower()))

    def _reset_text_color(self):
        if self._settings.text_color:
            self._commit(replace(self._settings, text_color=""))

    def _on_opacity_changed(self, pct: int):
        self.opacity_value.setText(f"{pct}%")
        if self._syncing:
            return
        self._commit(replace(self._settings, opacity=round(pct / 100.0, 2)))

    def _on_complex_opacity_changed(self, pct: int):
        self.complex_opacity_value.setText(f"{pct}%")
        if self._syncing:
            return
        self._commit(replace(self._settings, focus_complex_opacity=round(pct / 100.0, 2)))

    def _on_adjust_key_changed(self, key: str):
        if self._syncing:
            return
        self._commit(replace(self._settings, focus_adjust_key=key))

    def _on_sample_trigger_changed(self, key: str):
        if self._syncing or key not in ("auto", "manual"):
            return
        self._commit(replace(self._settings, focus_sample_trigger=key))

    def _on_toggle_changed(self, field: str, checked: bool):
        if self._syncing:
            return
        self._commit(replace(self._settings, **{field: bool(checked)}))

    def _on_binding_changed(self, field: str, new_sequence: str):
        if self._syncing:
            return
        candidate = {k: getattr(self._settings, k) for k in BINDING_FIELDS}
        candidate[field] = new_sequence
        error = binding_error(candidate)
        if error:
            self.binding_buttons[field].set_sequence(getattr(self._settings, field))
            self.notice_requested.emit(error)
            return
        self._commit(replace(self._settings, **{field: new_sequence}))

    def _refresh_storage_info(self):
        data_dir = str(self.store.path.parent)
        self.storage_dir_value.setText(data_dir)
        self.storage_file_value.setText(str(self.store.path))
        count = len(self.store.state.get("books", []))
        self.storage_books_value.setText(tr("共 {count} 本", self.language, count=count))
        mode_text = (
            tr("只读保护模式（数据来自不支持的版本）", self.language)
            if self.store.read_only
            else tr("离线便携模式 · 书籍与进度仅保存在本机", self.language)
        )
        self.storage_mode_value.setText(mode_text)
        self.rail_mode.setText(self.store.path.name)
