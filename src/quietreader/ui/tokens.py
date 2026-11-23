"""Unified design tokens for QuietReader themes and layout metrics."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

TOPBAR_HEIGHT = 44
NARROW_BREAKPOINT = 560
COMPACT_BRAND_BREAKPOINT = 500
CRUMB_MIN_WIDTH = 640
RADIUS_WINDOW = 11
RADIUS_CARD = 10
RADIUS_CONTROL = 7

UI_FONT_STACK = "'Segoe UI Variable Text', 'Microsoft YaHei UI', 'Microsoft YaHei', sans-serif"

_LIGHT_TOKENS = {
    "paper": "#f6f4ec",
    "side": "#eceee5",
    "elevated": "#faf9f4",
    "ink": "#1f3029",
    "ink_secondary": "#3d4f46",
    "muted": "#647569",
    "line": "#dde2d6",
    "line_strong": "#c8d0c0",
    "accent": "#2e5641",
    "accent_soft": "#dfe8de",
    "accent_hover": "#e6ece3",
    "button": "#e6ebe1",
    "selected": "#d6e2d3",
    "track": "#d7dfd3",
    "on_accent": "#f6f4ec",
    "danger": "#c7463b",
}

_SEPIA_TOKENS = {
    "paper": "#efe4cd",
    "side": "#e5d6b9",
    "elevated": "#f6eedd",
    "ink": "#3e3125",
    "ink_secondary": "#594838",
    "muted": "#7c6b56",
    "line": "#d7c5a1",
    "line_strong": "#c3ad84",
    "accent": "#7f5a34",
    "accent_soft": "#e2d0ae",
    "accent_hover": "#e7d8ba",
    "button": "#e6d6b5",
    "selected": "#dac59d",
    "track": "#d5c19b",
    "on_accent": "#f6eedd",
    "danger": "#b84339",
}

_DARK_TOKENS = {
    "paper": "#1d2522",
    "side": "#161d1a",
    "elevated": "#242e2a",
    "ink": "#e8e6dc",
    "ink_secondary": "#c8d1ca",
    "muted": "#95a79b",
    "line": "#313f38",
    "line_strong": "#42544b",
    "accent": "#a9c7ad",
    "accent_soft": "#283730",
    "accent_hover": "#24312b",
    "button": "#28352f",
    "selected": "#34483e",
    "track": "#33423b",
    "on_accent": "#161d1a",
    "danger": "#e06c63",
}


def resolve_palette(theme: str, background_color: str = "") -> dict[str, str]:
    """Return semantic color tokens for the active theme or custom focus background."""
    if background_color:
        red, green, blue = (int(background_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
        luminance = sum(
            weight * (channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4)
            for weight, channel in zip((0.2126, 0.7152, 0.0722), (red, green, blue))
        )
        light_text = (1.05 / (luminance + 0.05)) > ((luminance + 0.05) / 0.05)
        palette = resolve_palette("dark" if light_text else "light")
        palette["on_accent"] = palette["paper"]
        palette["paper"] = background_color
        palette["ink"] = "#f5f5f5" if light_text else "#171717"
        palette["ink_secondary"] = "#e3e8e4" if light_text else "#262626"
        palette["muted"] = "#d0d7d2" if light_text else "#3a3a3a"
        palette["custom_background"] = "1"
        return palette

    dark = theme == "dark" or (
        theme == "system"
        and QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    )
    if dark:
        return dict(_DARK_TOKENS)
    if theme == "sepia":
        return dict(_SEPIA_TOKENS)
    return dict(_LIGHT_TOKENS)
