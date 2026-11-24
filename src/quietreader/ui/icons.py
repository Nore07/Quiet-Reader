"""Pure-QPainter crisp vector icons requiring no extra QtSvg DLLs or image assets."""

from functools import lru_cache
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


def _draw_icon(painter: QPainter, name: str):
    if name == "book":
        path = QPainterPath()
        path.moveTo(3, 5.5)
        path.lineTo(9.5, 5.5)
        path.quadTo(12, 5.5, 12, 8.5)
        path.lineTo(12, 19.5)
        path.quadTo(12, 17.5, 9.5, 17.5)
        path.lineTo(3, 17.5)
        path.closeSubpath()
        painter.drawPath(path)

        right = QPainterPath()
        right.moveTo(21, 5.5)
        right.lineTo(14.5, 5.5)
        right.quadTo(12, 5.5, 12, 8.5)
        right.lineTo(12, 19.5)
        right.quadTo(12, 17.5, 14.5, 17.5)
        right.lineTo(21, 17.5)
        right.closeSubpath()
        painter.drawPath(right)
    elif name in ("sidebar", "sidebar_collapsed"):
        painter.drawRoundedRect(QRectF(3.5, 4.5, 17, 15), 2.5, 2.5)
        painter.drawLine(QPointF(9.5, 4.5), QPointF(9.5, 19.5))
        if name == "sidebar_collapsed":
            painter.drawLine(QPointF(13.5, 9.5), QPointF(16, 12))
            painter.drawLine(QPointF(16, 12), QPointF(13.5, 14.5))
    elif name == "back":
        painter.drawLine(QPointF(14.5, 6), QPointF(8.5, 12))
        painter.drawLine(QPointF(8.5, 12), QPointF(14.5, 18))
    elif name == "chevron_down":
        painter.drawLine(QPointF(6.5, 9.5), QPointF(12, 15))
        painter.drawLine(QPointF(12, 15), QPointF(17.5, 9.5))
    elif name == "search":
        painter.drawEllipse(QRectF(4.5, 4.5, 12, 12))
        painter.drawLine(QPointF(15, 15), QPointF(20, 20))
    elif name == "pin":
        path = QPainterPath()
        path.moveTo(8.5, 4.5)
        path.lineTo(15.5, 4.5)
        path.lineTo(14.5, 10.5)
        path.lineTo(17.5, 13.5)
        path.lineTo(6.5, 13.5)
        path.lineTo(9.5, 10.5)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawLine(QPointF(12, 13.5), QPointF(12, 20))
    elif name == "focus":
        painter.drawLine(QPointF(14.5, 4.5), QPointF(19.5, 4.5))
        painter.drawLine(QPointF(19.5, 4.5), QPointF(19.5, 9.5))
        painter.drawLine(QPointF(19.5, 4.5), QPointF(13.5, 10.5))
        painter.drawLine(QPointF(9.5, 19.5), QPointF(4.5, 19.5))
        painter.drawLine(QPointF(4.5, 19.5), QPointF(4.5, 14.5))
        painter.drawLine(QPointF(4.5, 19.5), QPointF(10.5, 13.5))
    elif name == "fish":
        # Matches assets/fish-outline.svg while keeping the icon on the QPainter renderer.
        path = QPainterPath()
        path.moveTo(6, 12)
        path.cubicTo(8.2, 9.4, 11.1, 7.3, 13.8, 7.2)
        path.cubicTo(17.6, 7, 21, 9.1, 22.7, 12)
        path.cubicTo(21, 14.9, 17.6, 17, 13.8, 16.8)
        path.cubicTo(11.1, 16.7, 8.2, 14.6, 6, 12)
        path.lineTo(1.8, 7.7)
        path.lineTo(1.8, 16.3)
        path.closeSubpath()
        painter.drawPath(path)

        eye_color = painter.pen().color()
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(eye_color)
        painter.drawEllipse(QRectF(18.1, 11.1, 1.8, 1.8))
        painter.restore()
    elif name == "settings":
        painter.drawEllipse(QRectF(9, 9, 6, 6))
        painter.drawEllipse(QRectF(5.5, 5.5, 13, 13))
        for step in range(8):
            angle = step * (math.pi / 4.0)
            x1 = 12.0 + math.cos(angle) * 6.5
            y1 = 12.0 + math.sin(angle) * 6.5
            x2 = 12.0 + math.cos(angle) * 9.0
            y2 = 12.0 + math.sin(angle) * 9.0
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    elif name == "encoding":
        painter.drawLine(QPointF(8.5, 7.5), QPointF(4, 12))
        painter.drawLine(QPointF(4, 12), QPointF(8.5, 16.5))
        painter.drawLine(QPointF(15.5, 7.5), QPointF(20, 12))
        painter.drawLine(QPointF(20, 12), QPointF(15.5, 16.5))
        painter.drawLine(QPointF(13.5, 6), QPointF(10.5, 18))
    elif name == "minimize":
        painter.drawLine(QPointF(5.5, 12), QPointF(18.5, 12))
    elif name == "maximize":
        painter.drawRoundedRect(QRectF(6, 6, 12, 12), 1.8, 1.8)
    elif name == "restore":
        painter.drawRoundedRect(QRectF(5.5, 8.5, 10, 10), 1.6, 1.6)
        path = QPainterPath()
        path.moveTo(8.5, 8.5)
        path.lineTo(8.5, 6.2)
        path.quadTo(8.5, 5.5, 9.2, 5.5)
        path.lineTo(17.8, 5.5)
        path.quadTo(18.5, 5.5, 18.5, 6.2)
        path.lineTo(18.5, 14.8)
        path.quadTo(18.5, 15.5, 17.8, 15.5)
        path.lineTo(15.5, 15.5)
        painter.drawPath(path)
    elif name == "close":
        painter.drawLine(QPointF(6.5, 6.5), QPointF(17.5, 17.5))
        painter.drawLine(QPointF(17.5, 6.5), QPointF(6.5, 17.5))
    elif name == "toc":
        for y in (7, 12, 17):
            painter.drawPoint(QPointF(4.5, y))
            painter.drawLine(QPointF(8.5, y), QPointF(19.5, y))
    elif name == "bookmark":
        path = QPainterPath()
        path.moveTo(6.5, 4.5)
        path.lineTo(17.5, 4.5)
        path.lineTo(17.5, 19.5)
        path.lineTo(12, 15.5)
        path.lineTo(6.5, 19.5)
        path.closeSubpath()
        painter.drawPath(path)
    elif name == "info":
        painter.drawEllipse(QRectF(4, 4, 16, 16))
        painter.drawLine(QPointF(12, 11.5), QPointF(12, 16))
        painter.drawPoint(QPointF(12, 8.2))
    elif name == "grid":
        for x in (4.5, 13.5):
            for y in (4.5, 13.5):
                painter.drawRoundedRect(QRectF(x, y, 6, 6), 1.3, 1.3)
    elif name == "list":
        for y in (7, 12, 17):
            painter.drawLine(QPointF(4.5, y), QPointF(19.5, y))
    elif name == "plus":
        painter.drawLine(QPointF(12, 5.5), QPointF(12, 18.5))
        painter.drawLine(QPointF(5.5, 12), QPointF(18.5, 12))
    elif name == "folder":
        path = QPainterPath()
        path.moveTo(3.5, 7)
        path.quadTo(3.5, 5.5, 5, 5.5)
        path.lineTo(9.5, 5.5)
        path.lineTo(11.5, 8)
        path.lineTo(19, 8)
        path.quadTo(20.5, 8, 20.5, 9.5)
        path.lineTo(20.5, 17.5)
        path.quadTo(20.5, 19, 19, 19)
        path.lineTo(5, 19)
        path.quadTo(3.5, 19, 3.5, 17.5)
        path.closeSubpath()
        painter.drawPath(path)
    elif name == "clock":
        painter.drawEllipse(QRectF(4, 4, 16, 16))
        painter.drawLine(QPointF(12, 7.5), QPointF(12, 12))
        painter.drawLine(QPointF(12, 12), QPointF(15.2, 13.8))
    elif name == "more":
        for x in (6.5, 12, 17.5):
            painter.drawEllipse(QRectF(x - 1.1, 10.9, 2.2, 2.2))
    elif name == "prev_page":
        painter.drawLine(QPointF(14.5, 6.5), QPointF(9, 12))
        painter.drawLine(QPointF(9, 12), QPointF(14.5, 17.5))
    elif name == "next_page":
        painter.drawLine(QPointF(9.5, 6.5), QPointF(15, 12))
        painter.drawLine(QPointF(15, 12), QPointF(9.5, 17.5))
    elif name == "prev_chapter":
        for dx in (0.0, 5.2):
            painter.drawLine(QPointF(11.5 + dx, 7), QPointF(6.5 + dx, 12))
            painter.drawLine(QPointF(6.5 + dx, 12), QPointF(11.5 + dx, 17))
    elif name == "next_chapter":
        for dx in (0.0, 5.2):
            painter.drawLine(QPointF(7.3 + dx, 7), QPointF(12.3 + dx, 12))
            painter.drawLine(QPointF(12.3 + dx, 12), QPointF(7.3 + dx, 17))
    elif name == "palette":
        painter.drawEllipse(QRectF(4, 4, 16, 16))
        for cx, cy in ((9.2, 9.5), (14.5, 9.2), (9.5, 14.8)):
            painter.drawEllipse(QRectF(cx - 1.1, cy - 1.1, 2.2, 2.2))
    elif name == "typography":
        painter.drawLine(QPointF(5.5, 6.5), QPointF(18.5, 6.5))
        painter.drawLine(QPointF(12, 6.5), QPointF(12, 18.5))
        painter.drawLine(QPointF(9, 18.5), QPointF(15, 18.5))
    elif name == "keyboard":
        painter.drawRoundedRect(QRectF(3.5, 6.5, 17, 11), 2.2, 2.2)
        for x in (7, 10.5, 14, 17):
            painter.drawPoint(QPointF(x, 10.2))
        painter.drawLine(QPointF(8.2, 14.2), QPointF(15.8, 14.2))
    elif name == "storage":
        painter.drawEllipse(QRectF(5, 4.5, 14, 4.5))
        painter.drawLine(QPointF(5, 6.8), QPointF(5, 17.2))
        painter.drawLine(QPointF(19, 6.8), QPointF(19, 17.2))
        painter.drawArc(QRectF(5, 10, 14, 4.5), 180 * 16, 180 * 16)
        painter.drawArc(QRectF(5, 15, 14, 4.5), 180 * 16, 180 * 16)


@lru_cache(maxsize=256)
def render_icon_pixmap(name: str, color: str, size: int = 16, scale: int = 2) -> QPixmap:
    """Render an anti-aliased HiDPI pixmap for the requested vector icon."""
    pixel_size = max(12, int(size)) * max(1, int(scale))
    pixmap = QPixmap(pixel_size, pixel_size)
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(float(max(1, int(scale))))

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(size / 24.0, size / 24.0)
    pen = QPen(QColor(color))
    pen.setWidthF(1.8 if name == "fish" else 1.75)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    _draw_icon(painter, name)
    painter.end()
    return pixmap


def vector_icon(name: str, color: str, size: int = 16, disabled_color: str = "") -> QIcon:
    """Build a QIcon with crisp normal/disabled pixmaps."""
    icon = QIcon()
    icon.addPixmap(render_icon_pixmap(name, color, size), QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(render_icon_pixmap(name, color, size), QIcon.Mode.Active, QIcon.State.Off)
    icon.addPixmap(render_icon_pixmap(name, color, size), QIcon.Mode.Normal, QIcon.State.On)
    if disabled_color:
        icon.addPixmap(render_icon_pixmap(name, disabled_color, size), QIcon.Mode.Disabled, QIcon.State.Off)
        icon.addPixmap(render_icon_pixmap(name, disabled_color, size), QIcon.Mode.Disabled, QIcon.State.On)
    return icon
