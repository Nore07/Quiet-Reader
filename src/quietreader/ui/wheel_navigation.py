"""Shared high-resolution wheel handling for reader page navigation."""

from PySide6.QtCore import QObject, Qt, QTimer


class WheelPageAccumulator(QObject):
    """Turn pages after a full wheel step, discarding modified wheel input."""

    ANGLE_STEP = 120
    PIXEL_STEP = 60
    RESET_DELAY_MS = 180

    def __init__(self, turn_page, parent=None):
        super().__init__(parent)
        self._turn_page = turn_page
        self._delta = 0
        self._direction = 0
        self._source = ""
        self._reset_timer = QTimer(self)
        self._reset_timer.setSingleShot(True)
        self._reset_timer.setInterval(self.RESET_DELAY_MS)
        self._reset_timer.timeout.connect(self.reset)

    def reset(self):
        self._delta = 0
        self._direction = 0
        self._source = ""
        self._reset_timer.stop()

    def handle(self, event):
        modifiers = event.modifiers()
        if modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier):
            self.reset()
            event.accept()
            return

        angle_delta = event.angleDelta().y()
        pixel_delta = event.pixelDelta().y()
        if angle_delta:
            delta = angle_delta
            threshold = self.ANGLE_STEP
            source = "angle"
        elif pixel_delta:
            delta = pixel_delta
            threshold = self.PIXEL_STEP
            source = "pixel"
        else:
            event.ignore()
            return

        direction = 1 if delta > 0 else -1
        if self._source != source or (self._direction and self._direction != direction):
            self._delta = 0
        self._source = source
        self._direction = direction
        self._delta += delta

        while abs(self._delta) >= threshold:
            self._turn_page(-direction)
            self._delta -= direction * threshold

        if self._delta:
            self._reset_timer.start()
        else:
            self.reset()
        event.accept()
