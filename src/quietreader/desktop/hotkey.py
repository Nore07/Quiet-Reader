import ctypes
from ctypes import wintypes
import sys

from PySide6.QtCore import QAbstractNativeEventFilter

from quietreader.domain.models import valid_hotkey
from quietreader.keys import windows_hotkey


class GlobalHotkey(QAbstractNativeEventFilter):
    ID = 0x5152

    def __init__(self, application, callback, identifier=None):
        super().__init__()
        self.application = application
        self.callback = callback
        self.ID = self.ID if identifier is None else identifier
        self.registered = False
        application.installNativeEventFilter(self)

    def configure(self, enabled, key):
        self.unregister()
        if not enabled:
            return ""
        if sys.platform != "win32":
            return "当前平台不支持 Windows 全局快捷键。"
        if not valid_hotkey(key):
            return "全局快捷键格式无效。"
        flags, virtual_key = windows_hotkey(key)
        self.registered = bool(ctypes.windll.user32.RegisterHotKey(None, self.ID, flags | 0x4000, virtual_key))
        return "" if self.registered else "全局快捷键注册失败，可能已被占用。请在设置中修改按键后重试。"

    def unregister(self):
        if self.registered:
            ctypes.windll.user32.UnregisterHotKey(None, self.ID)
            self.registered = False

    def nativeEventFilter(self, event_type, message):
        if sys.platform == "win32" and self.registered:
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0312 and msg.wParam == self.ID:
                self.callback()
                return True, 0
        return False, 0

    def close(self):
        self.unregister()
        self.application.removeNativeEventFilter(self)
