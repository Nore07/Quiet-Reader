import argparse
import hashlib
from pathlib import Path
import sys

from PySide6.QtCore import QLockFile, QStandardPaths, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from quietreader.persistence.data_paths import (
    application_data_directory,
    legacy_migration_needed,
    migrate_legacy_data,
    remap_relocated_shelf_paths,
)
from quietreader.persistence.store import StateStore
from quietreader.ui.window import MainWindow
from quietreader.i18n import tr


def _instance_server_name(directory: Path) -> str:
    identity = str(directory.resolve()).casefold().encode("utf-8")
    return "QuietReader-" + hashlib.sha256(identity).hexdigest()[:24]


def _wake_running_instance(server_name: str) -> bool:
    socket = QLocalSocket()
    socket.connectToServer(server_name)
    if not socket.waitForConnected(500):
        return False
    socket.write(b"show")
    socket.flush()
    delivered = socket.waitForBytesWritten(500)
    socket.disconnectFromServer()
    return delivered


def main():
    parser = argparse.ArgumentParser(description="QuietReader 本地 TXT / EPUB 阅读器")
    parser.add_argument("file", nargs="?", help="打开的 TXT / EPUB 文件")
    parser.add_argument("--data-dir", type=Path, help="指定独立的本地用户数据目录")
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("QuietReader")
    app.setOrganizationName("QuietReader")
    app.setQuitOnLastWindowClosed(False)
    directory = args.data_dir.expanduser().resolve() if args.data_dir else application_data_directory()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        QMessageBox.critical(None, tr("无法启动"), tr("无法创建阅读器数据目录，请检查权限或使用 --data-dir 指定目录。"))
        return 1
    lock = QLockFile(str(directory / "quietreader.lock"))
    lock.setStaleLockTime(0)
    server_name = _instance_server_name(directory)
    if not lock.tryLock(100):
        if _wake_running_instance(server_name):
            return 0
        QMessageBox.information(None, tr("QuietReader 已在运行"), tr("同一数据目录的阅读器已在运行，请从托盘显示窗口。"))
        return 0
    if args.data_dir is None:
        legacy_directory = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
        if legacy_migration_needed(legacy_directory, directory):
            legacy_lock = QLockFile(str(legacy_directory / "quietreader.lock"))
            legacy_lock.setStaleLockTime(0)
            if not legacy_lock.tryLock(100):
                lock.unlock()
                QMessageBox.information(None, tr("QuietReader 正在使用旧数据目录"), tr("旧版阅读器仍在使用旧数据目录，请先关闭后再启动。"))
                return 1
            try:
                migrate_legacy_data(legacy_directory, directory)
            except OSError as error:
                lock.unlock()
                QMessageBox.critical(None, tr("无法启动"), tr("旧版数据迁移失败：{error}。旧数据仍保留在原位置。", error=str(error)))
                return 1
            finally:
                legacy_lock.unlock()

    # The file lock guarantees there is no active owner for this data
    # directory, so it is safe to clear a stale local-server endpoint.
    QLocalServer.removeServer(server_name)
    activation_server = QLocalServer(app)
    activation_server.listen(server_name)
    window_ref = {"window": None}
    pending_activation = [False]

    def accept_activation():
        while activation_server.hasPendingConnections():
            client = activation_server.nextPendingConnection()
            received = bytearray()

            def receive_command(socket=client, buffer=received):
                buffer.extend(bytes(socket.readAll()))
                if len(buffer) < 4:
                    return
                if buffer == b"show":
                    window = window_ref["window"]
                    if window is None:
                        pending_activation[0] = True
                    else:
                        window.reveal()
                socket.disconnectFromServer()
                socket.deleteLater()

            client.readyRead.connect(receive_command)
            if client.bytesAvailable():
                receive_command()

    activation_server.newConnection.connect(accept_activation)
    app._quietreader_release_lock = lock.unlock
    store = StateStore(directory)
    if not store.read_only and remap_relocated_shelf_paths(store.state, directory):
        try:
            store.save()
        except OSError:
            store.warning = "书架路径已在本次运行中修复，但未能保存；请检查数据目录权限并重启。"
    window = MainWindow(store)
    window_ref["window"] = window
    app.aboutToQuit.connect(window.shutdown)
    app.commitDataRequest.connect(lambda *_: window.save_state())
    window.show()
    QTimer.singleShot(0, window.reveal)
    QTimer.singleShot(1500, window.check_for_updates_automatically)
    if pending_activation[0]:
        QTimer.singleShot(0, window.reveal)
    QTimer.singleShot(0, lambda: window.add_book_paths([args.file], open_after=True) if args.file else window.restore_last())
    code = app.exec()
    lock.unlock()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
