"""Custom self-painted Qt primitives aligned with QuietReader design tokens."""

from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QComboBox, QFrame, QHBoxLayout, QPushButton, QSlider,
)

from quietreader.ui.icons import render_icon_pixmap, vector_icon


class ThinSlider(QSlider):
    """Drop-in QSlider replacement with a thin, theme-colored track and circular thumb."""

    def __init__(self, orientation=Qt.Orientation.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self._palette = {
            "paper": "#f6f4ec",
            "accent": "#2e5641",
            "track": "#d7dfd3",
            "muted": "#647569",
            "line_strong": "#c8d0c0",
        }
        self._hovered = False
        self._dragging = False
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedHeight(20)

    def apply_palette(self, palette: dict[str, str]):
        self._palette = dict(palette)
        self.update()

    def _track_span(self):
        margin = 8.0
        width = max(1.0, self.width() - margin * 2.0)
        return margin, width

    def _value_for_x(self, x: float) -> int:
        minimum, maximum = self.minimum(), self.maximum()
        if maximum <= minimum:
            return minimum
        margin, width = self._track_span()
        ratio = max(0.0, min(1.0, (x - margin) / width))
        return round(minimum + ratio * (maximum - minimum))

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self._dragging = True
            self.setSliderDown(True)
            self.setValue(self._value_for_x(event.position().x()))
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and self.isEnabled():
            self.setValue(self._value_for_x(event.position().x()))
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.setValue(self._value_for_x(event.position().x()))
            self.setSliderDown(False)
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        margin, span = self._track_span()
        cy = self.height() / 2.0
        track_h = 3.0
        minimum, maximum = self.minimum(), self.maximum()
        ratio = 0.0 if maximum <= minimum else max(0.0, min(1.0, (self.value() - minimum) / (maximum - minimum)))
        thumb_x = margin + span * ratio

        enabled = self.isEnabled()
        track_color = QColor(self._palette.get("track", "#d7dfd3"))
        accent_color = QColor(self._palette.get("accent", "#2e5641"))
        if not enabled:
            accent_color = QColor(self._palette.get("line_strong", "#c8d0c0"))
            track_color.setAlpha(150)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_color)
        painter.drawRoundedRect(QRectF(margin, cy - track_h / 2.0, span, track_h), 1.5, 1.5)

        if ratio > 0.0 and enabled:
            painter.setBrush(accent_color)
            painter.drawRoundedRect(QRectF(margin, cy - track_h / 2.0, max(track_h, thumb_x - margin), track_h), 1.5, 1.5)

        radius = 5.5 if (self._hovered or self._dragging) and enabled else 4.5
        if enabled:
            paper_color = QColor(self._palette.get("paper", "#f6f4ec"))
            painter.setBrush(accent_color)
            painter.setPen(QPen(paper_color, 1.6))
            painter.drawEllipse(QRectF(thumb_x - radius, cy - radius, radius * 2.0, radius * 2.0))
        painter.end()


class ToggleSwitch(QAbstractButton):
    """Self-painted pill toggle switch matching QuietReader theme tokens."""

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(38, 21)
        self._hovered = False
        self._palette = {
            "accent": "#2e5641",
            "on_accent": "#f6f4ec",
            "track": "#d7dfd3",
            "elevated": "#faf9f4",
            "line_strong": "#c8d0c0",
        }

    def apply_palette(self, palette: dict[str, str]):
        self._palette = dict(palette)
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(38, 21)

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
        w, h = float(self.width()), float(self.height())
        checked = self.isChecked()
        enabled = self.isEnabled()

        bg = QColor(self._palette.get("accent" if checked else "track", "#2e5641" if checked else "#d7dfd3"))
        if not enabled:
            bg.setAlpha(120)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(QRectF(0.0, 0.0, w, h), h / 2.0, h / 2.0)

        knob_d = h - 6.0
        knob_x = (w - knob_d - 3.0) if checked else 3.0
        knob_color = QColor(self._palette.get("on_accent" if checked else "elevated", "#ffffff"))
        painter.setBrush(knob_color)
        painter.drawEllipse(QRectF(knob_x, 3.0, knob_d, knob_d))
        painter.end()


class SegmentedControl(QFrame):
    """Mutually exclusive pill selector for modes, views, and compact options."""

    currentChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("segmentedControl")
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(3, 3, 3, 3)
        self._layout.setSpacing(2)
        self._buttons: dict[str, QPushButton] = {}
        self._current_key = ""

    def add_segment(self, key: str, text: str, tooltip: str = "") -> QPushButton:
        button = QPushButton(text, self)
        button.setObjectName("segmentOption")
        button.setCheckable(True)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if tooltip:
            button.setToolTip(tooltip)
        button.clicked.connect(lambda _checked=False, target=key: self.set_current(target, emit=True))
        self._buttons[key] = button
        self._layout.addWidget(button)
        if not self._current_key:
            self.set_current(key, emit=False)
        return button

    def button(self, key: str) -> QPushButton | None:
        return self._buttons.get(key)

    def current_key(self) -> str:
        return self._current_key

    def set_current(self, key: str, emit: bool = False):
        if key not in self._buttons:
            return
        changed = key != self._current_key
        self._current_key = key
        for name, btn in self._buttons.items():
            btn.setChecked(name == key)
        if emit and changed:
            self.currentChanged.emit(key)

    def set_label(self, key: str, text: str, tooltip: str | None = None):
        button = self._buttons.get(key)
        if button is None:
            return
        button.setText(text)
        if tooltip is not None:
            button.setToolTip(tooltip)


class IconButton(QPushButton):
    """Quiet vector-icon button whose stroke color tracks theme & active states."""

    def __init__(
        self,
        icon_name: str,
        size: int = 16,
        checkable: bool = False,
        object_name: str = "iconButton",
        parent=None,
    ):
        super().__init__("", parent)
        self._icon_name = icon_name
        self._icon_size = size
        self._palette = {
            "ink": "#1f3029",
            "muted": "#647569",
            "accent": "#2e5641",
            "line_strong": "#c8d0c0",
        }
        self._hover_white = object_name == "windowClose"
        self._hovered = False
        self.setObjectName(object_name)
        self.setCheckable(checkable)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setIconSize(QSize(size, size))
        self.toggled.connect(self._refresh_icon)
        self._refresh_icon()

    def set_icon_name(self, icon_name: str):
        if self._icon_name != icon_name:
            self._icon_name = icon_name
            self._refresh_icon()

    def apply_palette(self, palette: dict[str, str]):
        self._palette = dict(palette)
        self._refresh_icon()

    def _refresh_icon(self):
        if self._hover_white and self._hovered and self.isEnabled():
            color = "#ffffff"
        elif self.isChecked():
            color = self._palette.get("accent", "#2e5641")
        elif self._hovered and self.isEnabled():
            color = self._palette.get("ink", "#1f3029")
        else:
            color = self._palette.get("muted", "#647569")
        disabled_color = self._palette.get("line_strong", "#c8d0c0")
        self.setIcon(vector_icon(self._icon_name, color, self._icon_size, disabled_color))

    def enterEvent(self, event):
        self._hovered = True
        self._refresh_icon()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._refresh_icon()
        super().leaveEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange:
            self._refresh_icon()


class FloatingPopover(QFrame):
    """In-canvas anchored floating card that dismisses on Escape or outside click."""

    dismissed = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("floatingPopover")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._anchor = None
        self._align = "right"
        self._filter_installed = False
        self.hide()

    def open_for(self, anchor_widget, align: str = "right"):
        self._anchor = anchor_widget
        self._align = align
        self.reposition()
        self.show()
        self.raise_()
        self._install_filter()

    def dismiss(self):
        was_visible = not self.isHidden()
        self._remove_filter()
        self.hide()
        if was_visible:
            self.dismissed.emit()

    def reposition(self):
        parent = self.parentWidget()
        if parent is None:
            return
        self.adjustSize()
        width = min(self.sizeHint().width(), max(180, parent.width() - 24))
        height = min(self.sizeHint().height(), max(120, parent.height() - 56))
        if self._anchor is not None and self._anchor.isVisible():
            origin = self._anchor.mapTo(parent, QPoint(0, self._anchor.height() + 6))
            if self._align == "right":
                x = origin.x() + self._anchor.width() - width
            else:
                x = origin.x()
            y = origin.y()
        else:
            x = parent.width() - width - 16
            y = 48
        x = max(12, min(x, parent.width() - width - 12))
        y = max(12, min(y, parent.height() - height - 12))
        self.setGeometry(x, y, width, height)

    def _install_filter(self):
        app = QApplication.instance()
        if app is not None and not self._filter_installed:
            app.installEventFilter(self)
            self._filter_installed = True

    def _remove_filter(self):
        app = QApplication.instance()
        if app is not None and self._filter_installed:
            app.removeEventFilter(self)
            self._filter_installed = False

    def eventFilter(self, watched, event):
        if self.isHidden():
            return False
        event_type = event.type()
        if event_type == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.dismiss()
            return True
        if event_type == QEvent.Type.MouseButtonPress:
            global_pos = event.globalPosition().toPoint()
            local_pos = self.mapFromGlobal(global_pos)
            inside_self = self.rect().contains(local_pos)
            inside_anchor = False
            if self._anchor is not None and self._anchor.isVisible():
                inside_anchor = self._anchor.rect().contains(self._anchor.mapFromGlobal(global_pos))
            # A combo box owns a separate popup window that can extend beyond the card.
            popup = QApplication.activePopupWidget()
            inside_popup = (
                popup is not None
                and self.isAncestorOf(popup.parentWidget())
                and popup.rect().contains(popup.mapFromGlobal(global_pos))
            )
            if not (inside_self or inside_anchor or inside_popup):
                self.dismiss()
        return False

    def closeEvent(self, event):
        self._remove_filter()
        super().closeEvent(event)


class QuietComboBox(QComboBox):
    """QComboBox painting a crisp vector chevron_down arrow instead of Qt's CSS border dash."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._chevron_color = "#647569"

    def apply_palette(self, palette: dict[str, str]):
        self._chevron_color = palette.get("muted", "#647569")
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pm = render_icon_pixmap("chevron_down", self._chevron_color, 12)
        painter.drawPixmap(self.width() - 19, (self.height() - 12) // 2, pm)
        painter.end()
