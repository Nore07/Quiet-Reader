"""User-initiated, in-memory screen sampling. Images are never saved or sent."""
from collections import Counter
from dataclasses import dataclass

from PySide6.QtCore import QObject, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget
from quietreader.i18n import tr, resolve_language


@dataclass
class ScreenSample:
    image: QImage
    geometry: QRect

    def color_at(self, global_point: QPoint) -> QColor:
        if self.image.isNull() or not self.geometry.contains(global_point):
            return QColor()
        # Screen coordinates are logical pixels; captures can use a different DPR.
        point = global_point - self.geometry.topLeft()
        x = min(self.image.width() - 1, point.x() * self.image.width() // self.geometry.width())
        y = min(self.image.height() - 1, point.y() * self.image.height() // self.geometry.height())
        return self.image.pixelColor(x, y)


def capture_screens():
    samples = []
    for screen in QGuiApplication.screens():
        image = screen.grabWindow(0).toImage()
        if image.isNull():
            raise RuntimeError("screen capture unavailable")
        samples.append(ScreenSample(image, screen.geometry()))
    if not samples:
        raise RuntimeError("no screens")
    return samples


def capture_region(bounds: QRect) -> list[ScreenSample]:
    """Capture only the intersecting screen region needed around a window."""
    if not bounds.isValid() or bounds.isEmpty():
        return []
    samples = []
    for screen in QGuiApplication.screens():
        screen_rect = screen.geometry()
        clip = bounds.intersected(screen_rect)
        if clip.isEmpty():
            continue
        image = screen.grabWindow(
            0,
            clip.left() - screen_rect.left(),
            clip.top() - screen_rect.top(),
            clip.width(),
            clip.height(),
        ).toImage()
        if not image.isNull():
            samples.append(ScreenSample(image, clip))
    return samples


def extract_perimeter_color(
    samples: list[ScreenSample],
    window_rect: QRect,
    gap: int = 3,
    band: int = 3,
) -> QColor:
    """Estimate the dominant host background color from pixels strictly outside window_rect."""
    if not samples or not window_rect.isValid() or window_rect.width() < 20 or window_rect.height() < 20:
        return QColor()
    gap = max(1, int(gap))
    band = max(1, int(band))
    inset = min(10, max(0, min(window_rect.width(), window_rect.height()) // 6))
    x_start = window_rect.left() + inset
    x_end = window_rect.right() - inset
    y_start = window_rect.top() + inset
    y_end = window_rect.bottom() - inset
    if x_end < x_start or y_end < y_start:
        return QColor()

    x_step = max(2, window_rect.width() // 72)
    y_step = max(2, window_rect.height() // 72)

    points: list[QPoint] = []
    for offset in range(gap, gap + band):
        top_y = window_rect.top() - offset
        bottom_y = window_rect.bottom() + offset
        for x in range(x_start, x_end + 1, x_step):
            points.append(QPoint(x, top_y))
            points.append(QPoint(x, bottom_y))
        left_x = window_rect.left() - offset
        right_x = window_rect.right() + offset
        for y in range(y_start, y_end + 1, y_step):
            points.append(QPoint(left_x, y))
            points.append(QPoint(right_x, y))

    rgb_values: list[tuple[int, int, int]] = []
    for pt in points:
        for sample in samples:
            if sample.geometry.contains(pt):
                color = sample.color_at(pt)
                if color.isValid():
                    rgb_values.append((color.red(), color.green(), color.blue()))
                break

    if not rgb_values:
        return QColor()

    # Quantize to 8-step RGB buckets to find the dominant solid host surface,
    # then take the per-channel median across the peak cluster (ignoring text/border outliers).
    buckets = Counter((r >> 3, g >> 3, b >> 3) for r, g, b in rgb_values)
    peak_r, peak_g, peak_b = buckets.most_common(1)[0][0]
    inliers = [
        (r, g, b)
        for r, g, b in rgb_values
        if max(abs((r >> 3) - peak_r), abs((g >> 3) - peak_g), abs((b >> 3) - peak_b)) <= 1
    ]
    if not inliers:
        inliers = rgb_values

    mid = len(inliers) // 2
    red = sorted(px[0] for px in inliers)[mid]
    green = sorted(px[1] for px in inliers)[mid]
    blue = sorted(px[2] for px in inliers)[mid]
    return QColor(red, green, blue)


def sample_window_perimeter_color(
    window_rect: QRect,
    capture=None,
    gap: int = 3,
    band: int = 3,
) -> QColor:
    """Capture pixels strictly outside window_rect without hiding the window and estimate background color."""
    if not window_rect.isValid() or window_rect.width() < 20 or window_rect.height() < 20:
        return QColor()
    outer = max(1, int(gap)) + max(1, int(band))
    expanded = window_rect.adjusted(-outer, -outer, outer, outer)
    try:
        samples = capture(expanded) if callable(capture) else capture_region(expanded)
    except Exception:
        return QColor()
    if not samples:
        return QColor()
    return extract_perimeter_color(samples, window_rect, gap=gap, band=band)


class ColorOverlay(QWidget):
    chosen = Signal(QColor)
    canceled = Signal()

    def __init__(self, sample, parent):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.sample = sample
        lang_attr = getattr(parent, "language", "system")
        if callable(getattr(lang_attr, "currentData", None)):
            self.language = lang_attr.currentData()
        elif callable(getattr(lang_attr, "current_key", None)):
            self.language = lang_attr.current_key()
        elif isinstance(lang_attr, str):
            self.language = lang_attr
        else:
            self.language = "system"
        self.point = QPoint(20, 20)
        self.setGeometry(sample.geometry)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(tr("桌面取色：单击选择，Esc 或右键取消", self.language))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawImage(self.rect(), self.sample.image)
        color = self.sample.color_at(self.sample.geometry.topLeft() + self.point)
        width = min(self.width(), 350 if resolve_language(self.language) == "en" else 260)
        box = QRect(min(self.width() - width, max(0, self.point.x() + 20)), min(self.height() - 74, max(0, self.point.y() + 20)), width, 74)
        painter.fillRect(box, QColor("#f5f3eb"))
        painter.setPen(QPen(QColor("#315743"), 1))
        painter.drawRect(box)
        painter.fillRect(QRect(box.left() + 10, box.top() + 12, 26, 30), color)
        painter.drawText(box.adjusted(46, 9, -8, -30), Qt.AlignmentFlag.AlignVCenter, color.name().upper())
        painter.drawText(box.adjusted(10, 38, -8, -4), Qt.AlignmentFlag.AlignVCenter, tr("单击取色 · Esc / 右键取消", self.language))

    def mouseMoveEvent(self, event):
        self.point = event.position().toPoint()
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            color = self.sample.color_at(self.sample.geometry.topLeft() + event.position().toPoint())
            if color.isValid():
                self.chosen.emit(color)
        elif event.button() == Qt.MouseButton.RightButton:
            self.canceled.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.canceled.emit()
        else:
            super().keyPressEvent(event)


class DesktopColorPicker(QObject):
    selected = Signal(QColor)
    failed = Signal(str)

    def __init__(self, owner, capture=capture_screens):
        super().__init__(owner)
        self.owner = owner
        self.capture = capture
        self.overlays = []
        self.windows = []
        self.active = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(180)
        self.timer.timeout.connect(self._capture)

    def begin(self):
        if self.active:
            return
        self.active = True
        self.windows = []
        parent = self.owner.parentWidget()
        # Opacity hides the windows visually without ending a modal QDialog.exec().
        # Deduplicate top-level windows so embedded widgets (e.g. SettingsPage) only hide MainWindow once.
        targets = []
        for candidate in ([parent.window()] if parent else []) + [self.owner.window()]:
            if candidate is not None and candidate not in targets:
                targets.append(candidate)
        for window in targets:
            self.windows.append((window, window.windowOpacity()))
            window.setWindowOpacity(0)
        self.timer.start()

    def _capture(self):
        if not self.active:
            return
        try:
            samples = self.capture()  # Capture every screen before showing any overlay.
            if not samples:
                raise RuntimeError("no captures")
            for sample in samples:
                overlay = ColorOverlay(sample, self.owner)
                overlay.chosen.connect(self._finish)
                overlay.canceled.connect(self.cancel)
                self.overlays.append(overlay)
            for overlay in self.overlays:
                overlay.show()
            focused = next((o for o in self.overlays if o.geometry().contains(QCursor.pos())), self.overlays[0])
            focused.activateWindow()
            focused.setFocus()
        except Exception:
            self.cancel()
            self.failed.emit("无法获取桌面颜色，请使用“选择颜色”手动设置。")

    def _finish(self, color=None):
        if not self.active:
            return
        self.active = False
        self.timer.stop()
        for overlay in self.overlays:
            overlay.hide()
            overlay.sample.image = QImage()
            overlay.deleteLater()
        self.overlays.clear()
        for window, opacity in self.windows:
            window.setWindowOpacity(opacity)
        self.windows.clear()
        owner_window = self.owner.window()
        if owner_window is not None:
            owner_window.activateWindow()
            owner_window.raise_()
        if color is not None and color.isValid():
            self.selected.emit(color)

    def cancel(self):
        self._finish()
