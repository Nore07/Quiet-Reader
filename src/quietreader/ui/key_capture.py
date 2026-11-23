from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout

from quietreader.i18n import tr
from quietreader.keys import normalize_binding


class KeyCaptureDialog(QDialog):
    def __init__(self, language, parent=None):
        super().__init__(parent)
        self.sequence = ""
        self.setWindowTitle(tr("录入快捷键", language))
        self.setMinimumSize(360, 140)
        layout = QVBoxLayout(self)
        label = QLabel(tr("请按下一个按键或组合键\nEsc 取消", language))
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)

    def event(self, event):
        if event.type() == QEvent.Type.ShortcutOverride:
            event.accept()
            return True
        if event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self.reject()
            elif not event.isAutoRepeat():
                sequence = normalize_binding(QKeySequence(event.keyCombination()).toString(QKeySequence.SequenceFormat.PortableText))
                if sequence:
                    self.sequence = sequence
                    self.accept()
            return True
        return super().event(event)


class KeyBindingButton(QPushButton):
    capture_started = Signal()
    capture_finished = Signal()
    sequence_changed = Signal(str)

    def __init__(self, sequence, language, parent=None):
        super().__init__(parent)
        self.sequence, self.language = sequence, language
        self.clicked.connect(self.capture)
        self.refresh()

    def refresh(self):
        self.setText(tr("修改 · {key}", self.language, key=self.sequence))

    def set_sequence(self, sequence):
        self.sequence = sequence
        self.refresh()

    def capture(self):
        self.capture_started.emit()
        try:
            dialog = KeyCaptureDialog(self.language, self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.set_sequence(dialog.sequence)
                self.sequence_changed.emit(dialog.sequence)
            dialog.deleteLater()
        finally:
            self.capture_finished.emit()
