"""Shared portable key representation and Windows registration mapping."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence

RESERVED_BINDINGS = {"Esc", "Ctrl+O", "Ctrl+,", "Ctrl+C"}
BINDING_FIELDS = (
    "next_key",
    "previous_key",
    "next_chapter_key",
    "previous_chapter_key",
    "focus_key",
    "focus_opacity_key",
    "focus_resample_key",
    "hotkey",
    "emergency_key",
    "moyu_key",
    "moyu_lines_key",
)
GLOBAL_BINDING_FIELDS = (
    "hotkey",
    "emergency_key",
    "next_key",
    "previous_key",
    "moyu_key",
    "moyu_lines_key",
)


def normalize_binding(value):
    if not isinstance(value, str) or not value or len(value) > 80:
        return ""
    sequence = QKeySequence.fromString(value, QKeySequence.SequenceFormat.PortableText)
    if sequence.count() != 1:
        return ""
    key = sequence[0].key()
    if key in (Qt.Key.Key_unknown, 0, Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta, Qt.Key.Key_AltGr):
        return ""
    return sequence.toString(QKeySequence.SequenceFormat.PortableText)


def binding_error(values):
    keys = [normalize_binding(values.get(name, "Ctrl+Alt+Q" if name == "emergency_key" else "")) for name in BINDING_FIELDS]
    if not all(keys):
        return "请为每个操作录入一个按键或组合键。"
    if len(set(keys)) != len(keys):
        return "不同操作不能使用相同的快捷键。"
    if any(key in RESERVED_BINDINGS for key in keys):
        return "Esc、Ctrl+O、Ctrl+, 和 Ctrl+C 已用于退出专注、添加书籍、配置和复制。"
    global_keys = [windows_hotkey(normalize_binding(values.get(name, ""))) for name in GLOBAL_BINDING_FIELDS]
    if any(key is None for key in global_keys):
        return "这个按键不能注册为 Windows 全局快捷键，请改用其他按键。"
    if len(set(global_keys)) != len(global_keys):
        return "不同操作不能使用相同的快捷键。"
    return ""


def windows_hotkey(value):
    normalized = normalize_binding(value)
    if not normalized:
        return None
    combination = QKeySequence(normalized)[0]
    key, modifiers = combination.key(), combination.keyboardModifiers()
    flags = 0
    for qt_modifier, win_modifier in [(Qt.KeyboardModifier.AltModifier, 1), (Qt.KeyboardModifier.ControlModifier, 2), (Qt.KeyboardModifier.ShiftModifier, 4), (Qt.KeyboardModifier.MetaModifier, 8)]:
        if modifiers & qt_modifier:
            flags |= win_modifier
    virtual = {
        Qt.Key.Key_Backspace: 8, Qt.Key.Key_Tab: 9, Qt.Key.Key_Backtab: 9,
        Qt.Key.Key_Return: 13, Qt.Key.Key_Enter: 13, Qt.Key.Key_Pause: 19,
        Qt.Key.Key_CapsLock: 20, Qt.Key.Key_Space: 32,
        Qt.Key.Key_PageUp: 33, Qt.Key.Key_PageDown: 34, Qt.Key.Key_End: 35,
        Qt.Key.Key_Home: 36, Qt.Key.Key_Left: 37, Qt.Key.Key_Up: 38,
        Qt.Key.Key_Right: 39, Qt.Key.Key_Down: 40, Qt.Key.Key_Print: 44,
        Qt.Key.Key_Insert: 45, Qt.Key.Key_Delete: 46, Qt.Key.Key_Menu: 93,
        Qt.Key.Key_NumLock: 144, Qt.Key.Key_ScrollLock: 145,
        Qt.Key.Key_VolumeMute: 173, Qt.Key.Key_VolumeDown: 174, Qt.Key.Key_VolumeUp: 175,
        Qt.Key.Key_MediaNext: 176, Qt.Key.Key_MediaPrevious: 177,
        Qt.Key.Key_MediaStop: 178, Qt.Key.Key_MediaPlay: 179, Qt.Key.Key_MediaPause: 179,
    }.get(key)
    if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F24:
        virtual = 112 + key - Qt.Key.Key_F1
    elif 48 <= key <= 57 or 65 <= key <= 90:
        virtual = key
        if modifiers & Qt.KeyboardModifier.KeypadModifier and 48 <= key <= 57:
            virtual = 96 + key - 48
    elif virtual is None and 33 <= key <= 126:
        import sys
        if sys.platform == "win32":
            import ctypes
            code = ctypes.windll.user32.VkKeyScanW(key)
            if code != -1:
                virtual = code & 255
                shift = (code >> 8) & 7
                flags |= (4 if shift & 1 else 0) | (2 if shift & 2 else 0) | (1 if shift & 4 else 0)
    if key == Qt.Key.Key_Backtab:
        flags |= 4
    return (flags, int(virtual)) if virtual is not None else None
