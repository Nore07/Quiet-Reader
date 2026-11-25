from PySide6.QtGui import QColor, QFont, QGuiApplication, QWheelEvent
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPointF, Signal
from PySide6.QtWidgets import (
    QApplication,
    QAbstractSpinBox, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFontComboBox, QFormLayout, QLabel, QMessageBox, QHBoxLayout, QPushButton,
    QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from quietreader.domain.models import Settings
from quietreader.desktop.color_picker import DesktopColorPicker
from quietreader.i18n import tr, install_qt_translator
from quietreader.keys import BINDING_FIELDS, binding_error
from quietreader.ui.key_capture import KeyBindingButton
from quietreader.ui.theme import colors, stylesheet


GLOBAL_HOTKEY_DESCRIPTION = (
    "开启后，窗口显示时，即使正在使用其他应用，也能使用「显示 / 隐藏」和「紧急退出」快捷键。"
    "窗口隐藏或最小化后，只有「显示 / 隐藏」快捷键仍然生效，可用它唤回窗口。"
)


class _PreferencesWheelFilter(QObject):
    """Wheel over a closed editor scrolls the page instead of changing a value."""
    def __init__(self, viewport, parent):
        super().__init__(parent)
        self.viewport = viewport

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Wheel:
            if self.viewport is None:
                event.accept()
                return True
            position = self.viewport.mapFromGlobal(event.globalPosition().toPoint())
            forwarded = QWheelEvent(QPointF(position), event.globalPosition(), event.pixelDelta(), event.angleDelta(), event.buttons(), event.modifiers(), event.phase(), event.inverted())
            QCoreApplication.sendEvent(self.viewport, forwarded)
            event.accept()
            return True
        return super().eventFilter(watched, event)


class SettingsDialog(QDialog):
    capture_started = Signal()
    capture_finished = Signal()
    layout_requested = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.resize(710, 790)
        self.original_language = settings.language
        self.base_settings = settings
        self.texts = []
        self.fields = {}
        self.background_color = settings.background_color
        self.text_color = settings.text_color
        outer = QVBoxLayout(self)
        self.title = self.label("让阅读适合你")
        self.title.setObjectName("bookTitle")
        outer.addWidget(self.title)
        outer.addWidget(self.button("阅读界面", self.open_layout))
        scroll = self.scroll = QScrollArea()
        scroll.setObjectName("preferencesScroll")
        scroll.viewport().setObjectName("preferencesViewport")
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName("preferencesContent")
        self.form = QFormLayout(content)
        self.form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.form.setVerticalSpacing(10)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        self.language = QComboBox()
        for label, code in [("跟随系统", "system"), ("简体中文", "zh_CN"), ("English", "en")]:
            self.language.addItem(label, code)
        self.language.setCurrentIndex(self.language.findData(settings.language))
        self.row("界面语言", self.language)
        self.theme = QComboBox()
        for label, code in [("跟随系统", "system"), ("纸白", "light"), ("暖纸", "sepia"), ("夜读", "dark")]:
            self.theme.addItem(label, code)
        self.theme.setCurrentIndex(self.theme.findData(settings.theme))
        self.row("配色", self.theme)
        self.color_label = QLabel()
        self.text_color_label = QLabel()
        self.picker = DesktopColorPicker(self)
        self.picker.selected.connect(self.set_background_color)
        self.picker.failed.connect(lambda message: QMessageBox.warning(self, self.t("桌面取色"), self.t(message)))
        color_row = QHBoxLayout()
        color_row.addWidget(self.color_label)
        for label, callback in [("选择颜色", self.choose_background_color), ("桌面取色", self.picker.begin), ("跟随主题", self.reset_background_color)]:
            color_row.addWidget(self.button(label, callback))
        self.row("专注模式背景色", color_row)
        text_row = QHBoxLayout()
        text_row.addWidget(self.text_color_label)
        text_row.addWidget(self.button("选择颜色", self.choose_text_color))
        text_row.addWidget(self.button("自动文字颜色", self.reset_text_color))
        self.row("文字颜色", text_row)
        self.focus_adjust_key = QComboBox()
        self.focus_adjust_key.addItem("Alt", "Alt")
        self.focus_adjust_key.addItem("Ctrl", "Ctrl")
        self.focus_adjust_key.addItem("Shift", "Shift")
        self.focus_adjust_key.setCurrentIndex(self.focus_adjust_key.findData(settings.focus_adjust_key))
        self.fields["focus_adjust_key"] = self.focus_adjust_key
        self.row("专注模式窗口调整键", self.focus_adjust_key)
        self.note("专注模式下按住此键，窗口内拖动可移动；靠近边缘拖动可缩放。默认使用 Alt。")
        for key, label, low, high, step in [
            ("opacity", "专注模式背景不透明度", 0.0, 1.0, 0.05),
        ]:
            field = QDoubleSpinBox() if isinstance(step, float) else QSpinBox()
            field.setRange(low, high)
            field.setSingleStep(step)
            field.setValue(getattr(settings, key))
            self.row(label, field)
            self.fields[key] = field
        self.note("设为 0 即全透明，文字保持不透明；背景色、透明度和桌面取色仅在专注模式生效。\n取色时窗口会暂时消失，单击选择，Esc / 右键取消。\n取色结果仅保存在本机；背景变化后可重新取色。")
        for key, label in [("always_on_top", "窗口始终置顶"), ("close_to_tray", "关闭窗口时隐藏至托盘"), ("global_hotkey", "启用全局显示 / 隐藏和紧急退出快捷键")]:
            field = QCheckBox()
            self.texts.append((field, label))
            field.setChecked(getattr(settings, key))
            self.fields[key] = field
            self.form.addRow(field)
        for key, label in [
            ("next_key", "下一页 / 下一句（阅读与摸鱼模式）"),
            ("previous_key", "上一页 / 上一句（阅读与摸鱼模式）"),
            ("next_chapter_key", "下一章（正文聚焦时）"),
            ("previous_chapter_key", "上一章（正文聚焦时）"),
            ("focus_key", "切换专注模式"),
            ("focus_opacity_key", "切换专注模式透明度"),
            ("focus_resample_key", "重新采样专注背景色"),
            ("hotkey", "显示 / 隐藏快捷键"),
            ("emergency_key", "紧急退出快捷键"),
            ("moyu_key", "切换摸鱼模式"),
            ("moyu_lines_key", "切换摸鱼模式单行 / 两行"),
        ]:
            field = KeyBindingButton(getattr(settings, key), settings.language)
            field.capture_started.connect(self.capture_started.emit)
            field.capture_finished.connect(self.capture_finished.emit)
            self.fields[key] = field
            self.row(label, field)
        self.hotkey = self.fields["hotkey"]
        self.note("点击修改，然后按下一个按键或组合键。Esc 取消录入。\nEsc 固定退出专注模式；添加书籍、配置和复制的快捷键保留。")
        self.note("PageUp / PageDown 在正文翻页、在摸鱼模式逐句翻页；Ctrl+PageUp / Ctrl+PageDown 切换章节。")
        self.note("专注模式仅显示正文，F10（可修改）或 Esc 退出，也可从托盘或正文右键菜单退出。")
        self.note("专注模式下按快捷键在 0% 和 100% 背景透明度间切换，默认 F9；按住调整键时，仅当背景为 0% 才临时提高到 1% 以接收鼠标操作，松开后恢复；其他透明度保持不变。")
        self.note("摸鱼模式默认显示一行，鼠标靠近时显示、离开后隐藏；滚轮逐句翻页，按住专注模式配置的调整键拖动可将细条移到 IDE 状态栏附近。模式热键在摸鱼模式开启期间全局生效，包括细条自动隐藏时。")
        self.note(GLOBAL_HOTKEY_DESCRIPTION)
        self.note("紧急退出会尝试保存当前位置，随后直接退出，无需等待后台导入完成。")
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.RestoreDefaults)
        for role, source in [(QDialogButtonBox.StandardButton.Save, "保存配置"), (QDialogButtonBox.StandardButton.Cancel, "取消"), (QDialogButtonBox.StandardButton.RestoreDefaults, "恢复默认")]:
            self.texts.append((self.buttons.button(role), source))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(self.reset_defaults)
        outer.addWidget(self.buttons)
        self.language.currentIndexChanged.connect(self.retranslate)
        self.theme.currentIndexChanged.connect(self._apply_theme)
        QGuiApplication.styleHints().colorSchemeChanged.connect(self._apply_theme)
        self.wheel_filter = _PreferencesWheelFilter(scroll.viewport(), self)
        for field in self.findChildren(QAbstractSpinBox) + self.findChildren(QComboBox):
            field.installEventFilter(self.wheel_filter)
            if field.lineEdit():
                field.lineEdit().installEventFilter(self.wheel_filter)
        self._apply_theme()
        self.retranslate()

    def _apply_theme(self, *args):
        # Preferences stay opaque and use the selected UI theme, independently of
        # the reader's transparency, picked desktop color and custom text color.
        self.setStyleSheet(stylesheet(colors(self.theme.currentData())))

    def t(self, source):
        return tr(source, self.language.currentData())

    def open_layout(self):
        self.reject()
        self.layout_requested.emit()

    def label(self, source):
        label = QLabel()
        label.setWordWrap(True)
        self.texts.append((label, source))
        return label

    def row(self, source, field):
        self.form.addRow(self.label(source), field)

    def button(self, source, callback):
        button = QPushButton()
        button.clicked.connect(callback)
        self.texts.append((button, source))
        return button

    def note(self, source):
        label = self.label(source)
        label.setObjectName("status")
        self.form.addRow(label)

    def retranslate(self, *args):
        install_qt_translator(QApplication.instance(), self.language.currentData())
        self.setWindowTitle(self.t("阅读配置 · QuietReader"))
        for widget, source in self.texts:
            widget.setText(self.t(source))
        self.language.setItemText(0, self.t("跟随系统"))
        for index, source in enumerate(("跟随系统", "纸白", "暖纸", "夜读")):
            self.theme.setItemText(index, self.t(source))
        for index, source in enumerate(("Alt", "Ctrl", "Shift")):
            self.focus_adjust_key.setItemText(index, self.t(source))
        for field in self.fields.values():
            if isinstance(field, KeyBindingButton):
                field.language = self.language.currentData()
                field.refresh()
        self._update_color_label()

    def reset_defaults(self):
        defaults = Settings()
        self.base_settings = defaults
        self.theme.setCurrentIndex(0)
        self.language.setCurrentIndex(0)
        self.reset_background_color()
        self.reset_text_color()
        for key, field in self.fields.items():
            value = getattr(defaults, key)
            if isinstance(field, QCheckBox):
                field.setChecked(value)
            elif isinstance(field, KeyBindingButton):
                field.set_sequence(value)
            elif isinstance(field, QComboBox):
                field.setCurrentIndex(field.findData(value))
            else:
                field.setValue(value)

    def values(self):
        data = self.base_settings.to_dict()
        data.update(theme=self.theme.currentData(), language=self.language.currentData(), background_color=self.background_color, text_color=self.text_color)
        for key, field in self.fields.items():
            if isinstance(field, QCheckBox):
                data[key] = field.isChecked()
            elif isinstance(field, KeyBindingButton):
                data[key] = field.sequence
            elif isinstance(field, QComboBox):
                data[key] = field.currentData()
            else:
                data[key] = field.value()
        return Settings.from_dict(data)

    def _update_color_label(self):
        self.color_label.setText(self.background_color.upper() or self.t("主题色"))
        self.text_color_label.setText(self.text_color.upper() or self.t("自动文字颜色"))

    def set_background_color(self, color):
        if color.isValid():
            self.background_color = color.name()
            self._update_color_label()

    def set_text_color(self, color):
        if color.isValid():
            self.text_color = color.name()
            self._update_color_label()

    def choose_background_color(self):
        self.set_background_color(QColorDialog.getColor(QColor(self.background_color or "#f5f3eb"), self, self.t("选择专注模式背景色"), QColorDialog.ColorDialogOption.DontUseNativeDialog))

    def choose_text_color(self):
        self.set_text_color(QColorDialog.getColor(QColor(self.text_color or "#283c33"), self, self.t("选择文字颜色"), QColorDialog.ColorDialogOption.DontUseNativeDialog))

    def reset_background_color(self):
        self.background_color = ""
        self._update_color_label()

    def reset_text_color(self):
        self.text_color = ""
        self._update_color_label()

    def done(self, result):
        self.picker.cancel()
        if result != QDialog.DialogCode.Accepted:
            install_qt_translator(QApplication.instance(), self.original_language)
        super().done(result)

    def accept(self):
        error = binding_error({key: self.fields[key].sequence for key in BINDING_FIELDS})
        if error:
            QMessageBox.warning(self, self.t("快捷键冲突"), self.t(error))
            return
        super().accept()
