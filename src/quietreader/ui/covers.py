"""HiDPI book cover renderer: scales embedded EPUB covers or generates typographic covers."""

from functools import lru_cache
import hashlib

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QPixmapCache

_COVER_THEMES = (
    ("#395246", "#23362d", "#eff1e8", "#a9c7ad"),  # deep pine
    ("#2f3e4d", "#1b2530", "#ece9df", "#b8cde0"),  # slate night
    ("#584c3d", "#3a3126", "#f2eadc", "#dec8a4"),  # warm walnut
    ("#46585c", "#2a383c", "#eef2f1", "#b4ccd1"),  # river mist
    ("#4d4452", "#302a35", "#eeeaf2", "#cfc0db"),  # plum ink
    ("#5d483f", "#3e2f28", "#f4ebe2", "#e2bfa8"),  # clay paper
)


def _cover_palette(seed: str):
    digest = hashlib.sha1((seed or "book").encode("utf-8")).digest()
    return _COVER_THEMES[digest[0] % len(_COVER_THEMES)]


def _rounded_clip(painter: QPainter, width: float, height: float, radius: float):
    path = QPainterPath()
    path.addRoundedRect(QRectF(0.0, 0.0, width, height), radius, radius)
    painter.setClipPath(path)


def render_cover_pixmap(
    title: str,
    author: str = "",
    fmt: str = "",
    cover_bytes: bytes = b"",
    width: int = 148,
    height: int = 196,
    radius: float = 8.0,
    scale: float = 2.0,
    mini: bool = False,
) -> QPixmap:
    """Render a crisp HiDPI rounded cover pixmap from EPUB image bytes or typographic fallback."""
    scale = float(scale)
    if cover_bytes:
        digest = hashlib.sha256(cover_bytes).hexdigest()
        cache_key = f"quietreader:cover:{digest}:{width}:{height}:{radius}:{scale}"
        cached = QPixmapCache.find(cache_key)
        if cached is not None:
            return cached
        raw = QPixmap()
        if raw.loadFromData(cover_bytes) and not raw.isNull():
            pw, ph = round(width * scale), round(height * scale)
            out = QPixmap(pw, ph)
            out.fill(Qt.GlobalColor.transparent)
            out.setDevicePixelRatio(scale)
            painter = QPainter(out)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            _rounded_clip(painter, float(width), float(height), radius)
            scaled = raw.scaled(
                pw, ph,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            sx = max(0, (scaled.width() - pw) // 2)
            sy = max(0, (scaled.height() - ph) // 2)
            painter.drawPixmap(QRectF(0, 0, width, height), scaled, QRectF(sx, sy, pw, ph))
            # Subtle spine shading + hairline inner border
            spine = QLinearGradient(0, 0, 7, 0)
            spine.setColorAt(0.0, QColor(0, 0, 0, 56))
            spine.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.fillRect(QRectF(0, 0, 7, height), spine)
            painter.setPen(QPen(QColor(255, 255, 255, 28), 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(0.5, 0.5, width - 1.0, height - 1.0), radius, radius)
            painter.end()
            QPixmapCache.insert(cache_key, out)
            return out
    return _cached_typographic_cover(title or "Untitled", author or "", fmt or "", width, height, radius, scale, mini)


@lru_cache(maxsize=256)
def _cached_typographic_cover(
    title: str,
    author: str,
    fmt: str,
    width: int,
    height: int,
    radius: float,
    scale: float,
    mini: bool,
) -> QPixmap:
    pw, ph = round(width * scale), round(height * scale)
    pixmap = QPixmap(pw, ph)
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(scale)

    top_hex, bot_hex, fg_hex, accent_hex = _cover_palette(title)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    _rounded_clip(painter, float(width), float(height), radius)

    grad = QLinearGradient(0, 0, width * 0.35, height)
    grad.setColorAt(0.0, QColor(top_hex))
    grad.setColorAt(1.0, QColor(bot_hex))
    painter.fillRect(QRectF(0, 0, width, height), grad)

    # Subtle book spine shadow
    spine = QLinearGradient(0, 0, 7, 0)
    spine.setColorAt(0.0, QColor(0, 0, 0, 62))
    spine.setColorAt(0.65, QColor(255, 255, 255, 16))
    spine.setColorAt(1.0, QColor(0, 0, 0, 0))
    painter.fillRect(QRectF(0, 0, 7, height), spine)

    accent_color = QColor(accent_hex)
    fg_color = QColor(fg_hex)

    if mini:
        # Clean minimalist botanical ridge silhouette for small 38x50 sidebar cover
        accent_soft = QColor(accent_hex)
        accent_soft.setAlpha(72)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent_soft)
        painter.drawEllipse(QRectF(width * 0.56, height * 0.16, width * 0.24, width * 0.24))
        hill = QPainterPath()
        hill.moveTo(0, height * 0.68)
        hill.quadTo(width * 0.38, height * 0.50, width, height * 0.64)
        hill.lineTo(width, height)
        hill.lineTo(0, height)
        hill.closeSubpath()
        hill_color = QColor(0, 0, 0, 58)
        painter.setBrush(hill_color)
        painter.drawPath(hill)
    else:
        # Format badge at top-right (EPUB / TXT)
        if fmt:
            badge_font = QFont("Segoe UI Variable Text", 8)
            badge_font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(badge_font)
            fm_badge = painter.fontMetrics()
            badge_text = fmt.upper()[:4]
            bw = fm_badge.horizontalAdvance(badge_text) + 12
            bh = 18
            bx = width - bw - 10
            by = 10
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 68))
            painter.drawRoundedRect(QRectF(bx, by, bw, bh), 4.0, 4.0)
            painter.setPen(QColor(255, 255, 255, 225))
            painter.drawText(QRectF(bx, by, bw, bh), Qt.AlignmentFlag.AlignCenter, badge_text)

        # Left vertical editorial accent rule + serif title block near lower half
        rule_x = 15.0
        rule_top = height * 0.52
        rule_bottom = height - 16.0
        accent_rule = QColor(accent_hex)
        accent_rule.setAlpha(140)
        painter.setPen(QPen(accent_rule, 2.0))
        painter.drawLine(int(rule_x), int(rule_top), int(rule_x), int(rule_bottom))

        title_font = QFont("Noto Serif SC")
        title_font.setPixelSize(16)
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(fg_color)
        title_rect = QRectF(23.0, rule_top - 2.0, width - 33.0, (rule_bottom - rule_top) - 18.0)
        painter.drawText(
            title_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom | Qt.TextFlag.TextWordWrap,
            title[:36],
        )

        sub_font = QFont("Segoe UI Variable Text")
        sub_font.setPixelSize(10)
        painter.setFont(sub_font)
        sub_color = QColor(fg_hex)
        sub_color.setAlpha(185)
        painter.setPen(sub_color)
        sub_text = author.strip() if author.strip() else (fmt.upper() if fmt else "")
        sub_rect = QRectF(23.0, rule_bottom - 15.0, width - 33.0, 15.0)
        painter.drawText(
            sub_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            painter.fontMetrics().elidedText(sub_text, Qt.TextElideMode.ElideRight, int(sub_rect.width())),
        )

    painter.setPen(QPen(QColor(255, 255, 255, 28), 1.0))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(QRectF(0.5, 0.5, width - 1.0, height - 1.0), radius, radius)
    painter.end()
    return pixmap
