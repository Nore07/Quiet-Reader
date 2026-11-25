"""Themed non-native local book chooser with Windows-like navigation and filtering."""

from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import re
import stat
import sys

from PySide6.QtCore import QAbstractTableModel, QDir, QItemSelectionModel, QModelIndex, QStandardPaths, QStorageInfo, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileIconProvider, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QSplitter, QStyle, QTreeView, QVBoxLayout,
)

from quietreader.i18n import tr
from quietreader.library.shelf import SUPPORTED_SUFFIXES


COMPUTER_TARGET = "__computer__"
FILE_FILTERS = (
    ("阅读文件 (*.txt *.epub *.pdf)", frozenset(SUPPORTED_SUFFIXES)),
    ("TXT (*.txt)", frozenset({".txt"})),
    ("EPUB (*.epub)", frozenset({".epub"})),
    ("PDF (*.pdf)", frozenset({".pdf"})),
)
_QUOTED_TOKENS = re.compile(r'"([^"]+)"')
_HIDDEN_WIN_ATTRS = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0) | getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 0)


@dataclass(frozen=True)
class _FileEntry:
    name: str
    path: Path
    is_dir: bool
    size: int = 0
    mtime: float = 0.0
    is_drive: bool = False


def _is_hidden_entry(name: str, stat_result) -> bool:
    if name.startswith("."):
        return True
    if sys.platform == "win32" and stat_result is not None:
        attrs = getattr(stat_result, "st_file_attributes", 0)
        if attrs & _HIDDEN_WIN_ATTRS:
            return True
    return False


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 * 1024 * 1024):.2f} GB"


def _format_mtime(mtime: float) -> str:
    if mtime <= 0:
        return ""
    try:
        return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return ""


def _parse_filename_tokens(raw: str) -> list[str]:
    text = raw.strip()
    if not text:
        return []
    if '"' in text:
        quoted = [token.strip() for token in _QUOTED_TOKENS.findall(text) if token.strip()]
        if quoted:
            return quoted
    cleaned = text.strip('"').strip("'").strip()
    return [cleaned] if cleaned else []


def _format_selected_names(paths: list[Path]) -> str:
    if not paths:
        return ""
    if len(paths) == 1:
        return paths[0].name
    return " ".join(f'"{path.name}"' for path in paths)


def _accessible_dir(candidate: Path) -> Path | None:
    try:
        resolved = candidate.expanduser().resolve()
        if not resolved.is_dir():
            return None
        with os.scandir(resolved):
            pass
        return resolved
    except (OSError, RuntimeError):
        return None


def _standard_location(location: QStandardPaths.StandardLocation) -> Path | None:
    raw = QStandardPaths.writableLocation(location)
    if not raw:
        return None
    return _accessible_dir(Path(raw))


def default_book_directory(books, shelf_manager=None, fallback_last: str = "") -> Path:
    """Resolve the initial folder from remembered browse path, book history, or standard folders."""
    if fallback_last:
        accessible = _accessible_dir(Path(fallback_last))
        if accessible is not None:
            return accessible

    shelf_root = None
    if shelf_manager is not None:
        try:
            shelf_root = shelf_manager.root.resolve()
        except (OSError, RuntimeError):
            shelf_root = None

    for record in books or ():
        if not isinstance(record, dict):
            continue
        source = record.get("source_path")
        if not isinstance(source, str) or not source:
            continue
        parent = _accessible_dir(Path(source).expanduser().parent)
        if parent is None:
            continue
        if shelf_root is not None:
            try:
                parent.relative_to(shelf_root)
                continue
            except ValueError:
                pass
        return parent

    for location in (
        QStandardPaths.StandardLocation.DesktopLocation,
        QStandardPaths.StandardLocation.DocumentsLocation,
        QStandardPaths.StandardLocation.HomeLocation,
    ):
        found = _standard_location(location)
        if found is not None:
            return found
    return Path.home()


def list_drives() -> list[tuple[str, Path]]:
    drives: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for volume in QStorageInfo.mountedVolumes():
        if not volume.isValid():
            continue
        root = volume.rootPath()
        if not root:
            continue
        path = Path(root)
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        drive_label = str(path).rstrip("\\/") or str(path)
        name = volume.displayName().strip()
        if name and name.rstrip("\\/").casefold() != drive_label.casefold():
            label = f"{name} ({drive_label})"
        else:
            label = drive_label
        drives.append((label, path))

    for info in QDir.drives():
        path = Path(info.absoluteFilePath())
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        drive_label = str(path).rstrip("\\/") or str(path)
        drives.append((drive_label, path))

    drives.sort(key=lambda item: str(item[1]).casefold())
    return drives


def scan_directory(
    directory: Path,
    allowed_suffixes: frozenset[str],
    show_hidden: bool = False,
    search_text: str = "",
) -> list[_FileEntry]:
    query = search_text.strip().casefold()
    entries: list[_FileEntry] = []
    with os.scandir(directory) as iterator:
        for item in iterator:
            name = item.name
            if query and query not in name.casefold():
                continue
            try:
                stat_result = item.stat(follow_symlinks=False)
                is_dir = stat.S_ISDIR(stat_result.st_mode)
                if not is_dir and item.is_symlink():
                    target_stat = item.stat(follow_symlinks=True)
                    is_dir = stat.S_ISDIR(target_stat.st_mode)
                    stat_result = target_stat
            except OSError:
                continue
            if not show_hidden and _is_hidden_entry(name, stat_result):
                continue
            if not is_dir and Path(name).suffix.lower() not in allowed_suffixes:
                continue
            entries.append(_FileEntry(
                name=name,
                path=Path(item.path),
                is_dir=is_dir,
                size=0 if is_dir else stat_result.st_size,
                mtime=stat_result.st_mtime,
            ))
    return entries


class _FileTableModel(QAbstractTableModel):
    def __init__(self, language: str = "system", parent=None):
        super().__init__(parent)
        self.language = language
        self.entries: list[_FileEntry] = []
        self._sort_column = 0
        self._sort_order = Qt.SortOrder.AscendingOrder
        self._icons = QFileIconProvider()

    def set_entries(self, entries: list[_FileEntry]):
        self.beginResetModel()
        self.entries = list(entries)
        self._apply_sort()
        self.endResetModel()

    def entry_at(self, row: int) -> _FileEntry | None:
        if 0 <= row < len(self.entries):
            return self.entries[row]
        return None

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.entries)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 3

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation != Qt.Orientation.Horizontal or role != Qt.ItemDataRole.DisplayRole:
            return None
        headers = ("名称", "修改日期", "大小")
        if 0 <= section < len(headers):
            return tr(headers[section], self.language)
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.entries):
            return None
        entry = self.entries[index.row()]
        column = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if column == 0:
                return entry.name
            if column == 1:
                return "" if entry.is_drive else _format_mtime(entry.mtime)
            if column == 2:
                return "" if entry.is_dir else _format_size(entry.size)
        elif role == Qt.ItemDataRole.DecorationRole and column == 0:
            if entry.is_drive:
                return self._icons.icon(QFileIconProvider.IconType.Drive)
            return self._icons.icon(
                QFileIconProvider.IconType.Folder if entry.is_dir else QFileIconProvider.IconType.File
            )
        elif role == Qt.ItemDataRole.TextAlignmentRole and column == 2:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        elif role == Qt.ItemDataRole.ToolTipRole and column == 0:
            return str(entry.path)
        return None

    def _apply_sort(self):
        reverse = self._sort_order == Qt.SortOrder.DescendingOrder
        dirs = [entry for entry in self.entries if entry.is_dir]
        files = [entry for entry in self.entries if not entry.is_dir]
        if self._sort_column == 1:
            dirs.sort(key=lambda e: (e.mtime, e.name.casefold()), reverse=reverse)
            files.sort(key=lambda e: (e.mtime, e.name.casefold()), reverse=reverse)
        elif self._sort_column == 2:
            dirs.sort(key=lambda e: e.name.casefold(), reverse=reverse)
            files.sort(key=lambda e: (e.size, e.name.casefold()), reverse=reverse)
        else:
            dirs.sort(key=lambda e: e.name.casefold(), reverse=reverse)
            files.sort(key=lambda e: e.name.casefold(), reverse=reverse)
        self.entries = dirs + files

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        self._sort_column = column
        self._sort_order = order
        self.layoutAboutToBeChanged.emit()
        persistent = self.persistentIndexList()
        tracked = [
            (str(self.entries[index.row()].path).casefold(), index.column())
            if index.isValid() and 0 <= index.row() < len(self.entries)
            else None
            for index in persistent
        ]
        self._apply_sort()
        row_by_path = {str(entry.path).casefold(): row for row, entry in enumerate(self.entries)}
        updated = []
        for item in tracked:
            if item is None:
                updated.append(QModelIndex())
                continue
            key, col = item
            new_row = row_by_path.get(key)
            updated.append(self.index(new_row, col) if new_row is not None else QModelIndex())
        self.changePersistentIndexList(persistent, updated)
        self.layoutChanged.emit()


class _FileTreeView(QTreeView):
    def __init__(self, on_enter, on_backspace, parent=None):
        super().__init__(parent)
        self._on_enter = on_enter
        self._on_backspace = on_backspace

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._on_enter()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Backspace:
            self._on_backspace()
            event.accept()
            return
        super().keyPressEvent(event)


class BookFileDialog(QDialog):
    """Themed local file dialog supporting quick places, editable path bar, search, and hidden toggle."""

    def __init__(self, start_directory: Path | str | None = None, language: str = "system", parent=None):
        super().__init__(parent)
        self.setObjectName("bookFileDialog")
        self.language = language
        self._selected_files: list[str] = []
        self._current_location: Path | str = COMPUTER_TARGET
        self._last_valid_directory: Path | None = None
        self._history: list[Path | str] = []
        self._history_index = -1
        self._syncing_places = False
        self._syncing_filename = False
        self._pending_selected_paths: list[str] = []
        self._pending_current_path: str = ""

        self.setWindowTitle(self.t("添加本地书籍"))
        self.resize(860, 560)
        self.setMinimumSize(640, 440)
        if parent is not None and not parent.windowIcon().isNull():
            self.setWindowIcon(parent.windowIcon())

        self._build_ui()
        self._populate_places()

        initial: Path | str = COMPUTER_TARGET
        if start_directory:
            accessible = _accessible_dir(Path(start_directory))
            if accessible is not None:
                initial = accessible
        if initial == COMPUTER_TARGET:
            initial = default_book_directory([])
        self.navigate(initial)

    def t(self, source: str, **values) -> str:
        return tr(source, self.language, **values)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        nav_row = QHBoxLayout()
        nav_row.setContentsMargins(0, 0, 0, 0)
        nav_row.setSpacing(6)

        self.back_button = self._nav_button("←", "后退", self.go_back)
        self.forward_button = self._nav_button("→", "前进", self.go_forward)
        self.up_button = self._nav_button("↑", "上一级", self.go_up)
        nav_row.addWidget(self.back_button)
        nav_row.addWidget(self.forward_button)
        nav_row.addWidget(self.up_button)

        self.address_edit = QLineEdit()
        self.address_edit.setObjectName("fileAddressEdit")
        self.address_edit.setPlaceholderText(self.t("输入或粘贴文件夹路径"))
        self.address_edit.setAccessibleName(self.t("地址栏"))
        self.address_edit.returnPressed.connect(self._navigate_from_address)
        nav_row.addWidget(self.address_edit, 3)

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("fileSearchEdit")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setPlaceholderText(self.t("搜索当前目录…"))
        self.search_edit.setAccessibleName(self.t("搜索当前目录…"))
        self.search_edit.textChanged.connect(lambda *_: self._refresh_listing())
        nav_row.addWidget(self.search_edit, 1)
        root.addLayout(nav_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("fileDialogSplitter")
        splitter.setChildrenCollapsible(False)

        self.places_list = QListWidget()
        self.places_list.setObjectName("fileDialogPlaces")
        self.places_list.setAccessibleName(self.t("常用位置"))
        self.places_list.setMinimumWidth(155)
        self.places_list.setMaximumWidth(260)
        self.places_list.itemClicked.connect(self._place_clicked)
        self.places_list.itemActivated.connect(self._place_clicked)
        splitter.addWidget(self.places_list)

        self.model = _FileTableModel(self.language, self)
        self.file_view = _FileTreeView(self._activate_selected_or_accept, self.go_up, self)
        self.file_view.setObjectName("fileDialogView")
        self.file_view.setAccessibleName(self.t("文件列表"))
        self.file_view.setModel(self.model)
        self.file_view.setRootIsDecorated(False)
        self.file_view.setItemsExpandable(False)
        self.file_view.setUniformRowHeights(True)
        self.file_view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.file_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.file_view.setSortingEnabled(True)
        self.file_view.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        header = self.file_view.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(1, 145)
        header.resizeSection(2, 95)
        self.file_view.doubleClicked.connect(self._item_double_clicked)
        self.file_view.selectionModel().selectionChanged.connect(self._selection_changed)
        self.model.layoutAboutToBeChanged.connect(self._capture_selection_before_layout)
        self.model.layoutChanged.connect(self._restore_selection_after_layout)
        splitter.addWidget(self.file_view)
        splitter.setSizes([185, 645])
        root.addWidget(splitter, 1)

        file_row = QHBoxLayout()
        file_row.setContentsMargins(0, 0, 0, 0)
        file_row.setSpacing(8)
        name_label = QLabel(self.t("文件名"))
        name_label.setObjectName("toolbarLabel")
        file_row.addWidget(name_label)

        self.filename_edit = QLineEdit()
        self.filename_edit.setObjectName("fileNameEdit")
        self.filename_edit.setAccessibleName(self.t("文件名"))
        self.filename_edit.returnPressed.connect(self._accept_selection)
        file_row.addWidget(self.filename_edit, 1)

        self.filter_combo = QComboBox()
        self.filter_combo.setObjectName("fileFilterCombo")
        self.filter_combo.setAccessibleName(self.t("文件类型"))
        for label, suffixes in FILE_FILTERS:
            self.filter_combo.addItem(self.t(label), suffixes)
        self.filter_combo.currentIndexChanged.connect(lambda *_: self._refresh_listing())
        file_row.addWidget(self.filter_combo)
        root.addLayout(file_row)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(10)

        self.show_hidden_checkbox = QCheckBox(self.t("显示隐藏文件夹"))
        self.show_hidden_checkbox.setChecked(False)
        self.show_hidden_checkbox.toggled.connect(lambda *_: self._refresh_listing())
        footer.addWidget(self.show_hidden_checkbox)

        self.hint_label = QLabel("")
        self.hint_label.setObjectName("status")
        footer.addWidget(self.hint_label, 1)

        self.open_button = QPushButton(self.t("打开"))
        self.open_button.setObjectName("primary")
        self.open_button.setAutoDefault(False)
        self.open_button.setDefault(False)
        self.open_button.clicked.connect(self._accept_selection)
        footer.addWidget(self.open_button)

        self.cancel_button = QPushButton(self.t("取消"))
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.setDefault(False)
        self.cancel_button.clicked.connect(self.reject)
        footer.addWidget(self.cancel_button)
        root.addLayout(footer)

        for sequence, callback in (
            ("Alt+Left", self.go_back),
            ("Alt+Right", self.go_forward),
            ("Alt+Up", self.go_up),
            ("Ctrl+L", self._focus_address),
            ("Alt+D", self._focus_address),
            ("Ctrl+F", self._focus_search),
        ):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(callback)

    def _nav_button(self, text: str, tooltip: str, callback) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("fileNavButton")
        label = self.t(tooltip)
        button.setToolTip(label)
        button.setAccessibleName(label)
        button.setFixedSize(34, 32)
        button.setAutoDefault(False)
        button.setDefault(False)
        button.clicked.connect(callback)
        return button

    def _populate_places(self):
        self.places_list.clear()
        style = self.style()
        quick_places = (
            ("桌面", QStandardPaths.StandardLocation.DesktopLocation, QStyle.StandardPixmap.SP_DesktopIcon),
            ("下载", QStandardPaths.StandardLocation.DownloadLocation, QStyle.StandardPixmap.SP_DirIcon),
            ("文档", QStandardPaths.StandardLocation.DocumentsLocation, QStyle.StandardPixmap.SP_DirIcon),
            ("主目录", QStandardPaths.StandardLocation.HomeLocation, QStyle.StandardPixmap.SP_DirHomeIcon),
        )
        added_paths: set[str] = set()
        for label, location, icon_kind in quick_places:
            path = _standard_location(location)
            if path is None:
                continue
            key = str(path).casefold()
            if key in added_paths:
                continue
            added_paths.add(key)
            item = QListWidgetItem(style.standardIcon(icon_kind), self.t(label))
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(str(path))
            self.places_list.addItem(item)

        computer = QListWidgetItem(style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon), self.t("此电脑"))
        computer.setData(Qt.ItemDataRole.UserRole, COMPUTER_TARGET)
        self.places_list.addItem(computer)

        drive_icon = style.standardIcon(QStyle.StandardPixmap.SP_DriveHDIcon)
        for label, path in list_drives():
            item = QListWidgetItem(drive_icon, label)
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(str(path))
            self.places_list.addItem(item)

    def _focus_address(self):
        self.address_edit.setFocus()
        self.address_edit.selectAll()

    def _focus_search(self):
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    def _allowed_suffixes(self) -> frozenset[str]:
        suffixes = self.filter_combo.currentData()
        return suffixes if isinstance(suffixes, frozenset) else frozenset(SUPPORTED_SUFFIXES)

    def current_directory(self) -> Path | None:
        return self._current_location if isinstance(self._current_location, Path) else None

    def last_directory(self) -> Path | None:
        current = self.current_directory()
        return current if current is not None else self._last_valid_directory

    def selected_files(self) -> list[str]:
        return list(self._selected_files)

    def navigate(self, target: Path | str, record_history: bool = True, select_path: Path | None = None) -> bool:
        if target != COMPUTER_TARGET:
            try:
                resolved = Path(target).expanduser().resolve()
                if not resolved.is_dir():
                    self.hint_label.setText(self.t("找不到路径，请检查后重试。"))
                    return False
                target = resolved
            except (OSError, RuntimeError):
                self.hint_label.setText(self.t("找不到路径，请检查后重试。"))
                return False

        if record_history:
            if self._history_index < 0 or self._history[self._history_index] != target:
                del self._history[self._history_index + 1:]
                self._history.append(target)
                self._history_index = len(self._history) - 1

        self._current_location = target
        if isinstance(target, Path):
            self._last_valid_directory = target
        self.address_edit.setText(self.t("此电脑") if target == COMPUTER_TARGET else str(target))
        if self.search_edit.text():
            self.search_edit.blockSignals(True)
            self.search_edit.clear()
            self.search_edit.blockSignals(False)
        self._sync_places_selection()
        self._update_nav_buttons()
        self._refresh_listing(select_path=select_path)
        return True

    def _refresh_listing(self, select_path: Path | None = None):
        target = self._current_location
        query = self.search_edit.text().strip()
        if target == COMPUTER_TARGET:
            drives = [
                _FileEntry(name=label, path=path, is_dir=True, is_drive=True)
                for label, path in list_drives()
                if not query or query.casefold() in label.casefold()
            ]
            self.model.set_entries(drives)
            self.hint_label.setText(self.t("{count} 个项目", count=len(drives)))
        else:
            try:
                entries = scan_directory(
                    target,
                    self._allowed_suffixes(),
                    show_hidden=self.show_hidden_checkbox.isChecked(),
                    search_text=query,
                )
            except OSError:
                self.model.set_entries([])
                self.hint_label.setText(self.t("无法打开该文件夹，可能没有访问权限。"))
                return
            self.model.set_entries(entries)
            if entries:
                self.hint_label.setText(self.t("{count} 个项目", count=len(entries)))
            elif query:
                self.hint_label.setText(self.t("没有匹配的文件或文件夹。"))
            else:
                self.hint_label.setText(self.t("当前文件夹为空。"))

        self._syncing_filename = True
        self.filename_edit.clear()
        self._syncing_filename = False

        if select_path is not None:
            wanted = str(select_path).casefold()
            for row, entry in enumerate(self.model.entries):
                if str(entry.path).casefold() == wanted:
                    index = self.model.index(row, 0)
                    self.file_view.selectionModel().select(
                        index,
                        QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows,
                    )
                    self.file_view.setCurrentIndex(index)
                    self.file_view.scrollTo(index)
                    break

    def _sync_places_selection(self):
        self._syncing_places = True
        try:
            current = self._current_location
            matched_row = -1
            for row in range(self.places_list.count()):
                data = self.places_list.item(row).data(Qt.ItemDataRole.UserRole)
                if current == COMPUTER_TARGET and data == COMPUTER_TARGET:
                    matched_row = row
                    break
                if isinstance(current, Path) and isinstance(data, Path) and str(current).casefold() == str(data).casefold():
                    matched_row = row
                    break
            self.places_list.setCurrentRow(matched_row)
        finally:
            self._syncing_places = False

    def _update_nav_buttons(self):
        self.back_button.setEnabled(self._history_index > 0)
        self.forward_button.setEnabled(self._history_index + 1 < len(self._history))
        self.up_button.setEnabled(self._current_location != COMPUTER_TARGET)

    def go_back(self):
        if self._history_index > 0:
            self._history_index -= 1
            self.navigate(self._history[self._history_index], record_history=False)

    def go_forward(self):
        if self._history_index + 1 < len(self._history):
            self._history_index += 1
            self.navigate(self._history[self._history_index], record_history=False)

    def go_up(self):
        if not isinstance(self._current_location, Path):
            return
        parent = self._current_location.parent
        if parent == self._current_location:
            self.navigate(COMPUTER_TARGET)
        else:
            self.navigate(parent)

    def _place_clicked(self, item: QListWidgetItem):
        if self._syncing_places or item is None:
            return
        target = item.data(Qt.ItemDataRole.UserRole)
        if target is not None:
            self.navigate(target)

    def _navigate_from_address(self):
        raw = self.address_edit.text().strip().strip('"').strip("'").strip()
        if not raw:
            return
        if raw.casefold() in {self.t("此电脑").casefold(), "this pc", "computer", "此电脑"}:
            self.navigate(COMPUTER_TARGET)
            return
        candidate = Path(raw).expanduser()
        try:
            if candidate.is_dir():
                self.navigate(candidate)
                return
            if candidate.is_file():
                resolved = candidate.resolve()
                if resolved.suffix.lower() in SUPPORTED_SUFFIXES:
                    if resolved.suffix.lower() not in self._allowed_suffixes():
                        self.filter_combo.setCurrentIndex(0)
                    if not self.show_hidden_checkbox.isChecked():
                        try:
                            if _is_hidden_entry(resolved.name, resolved.stat()):
                                self.show_hidden_checkbox.setChecked(True)
                        except OSError:
                            pass
                    self.navigate(resolved.parent, select_path=resolved)
                    return
                self.hint_label.setText(self.t("请选择 TXT、EPUB 或 PDF 文件。"))
                return
        except (OSError, RuntimeError):
            pass
        self.hint_label.setText(self.t("找不到路径，请检查后重试。"))

    def _selected_entries(self) -> list[_FileEntry]:
        rows = sorted({index.row() for index in self.file_view.selectionModel().selectedRows()})
        entries = [self.model.entry_at(row) for row in rows]
        return [entry for entry in entries if entry is not None]

    def _capture_selection_before_layout(self):
        self._pending_selected_paths = [str(entry.path).casefold() for entry in self._selected_entries()]
        current = self.file_view.currentIndex()
        entry = self.model.entry_at(current.row()) if current.isValid() else None
        self._pending_current_path = str(entry.path).casefold() if entry is not None else ""

    def _restore_selection_after_layout(self):
        selected_keys = self._pending_selected_paths
        current_key = self._pending_current_path
        self._pending_selected_paths = []
        self._pending_current_path = ""

        selection_model = self.file_view.selectionModel()
        row_by_path = {str(entry.path).casefold(): row for row, entry in enumerate(self.model.entries)}
        restored_rows = [row_by_path[key] for key in selected_keys if key in row_by_path]
        current_row = row_by_path.get(current_key)

        self._syncing_filename = True
        try:
            selection_model.clearSelection()
            for row in restored_rows:
                selection_model.select(
                    self.model.index(row, 0),
                    QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                )
            target_row = current_row if current_row is not None else (restored_rows[0] if restored_rows else None)
            if target_row is not None:
                target_index = self.model.index(target_row, 0)
                selection_model.setCurrentIndex(target_index, QItemSelectionModel.SelectionFlag.NoUpdate)
                self.file_view.scrollTo(target_index)
            else:
                selection_model.setCurrentIndex(QModelIndex(), QItemSelectionModel.SelectionFlag.NoUpdate)
        finally:
            self._syncing_filename = False

        files = [entry.path for entry in self._selected_entries() if not entry.is_dir]
        self._syncing_filename = True
        self.filename_edit.setText(_format_selected_names(files))
        self._syncing_filename = False
        if files:
            self.hint_label.setText(self.t("已选择 {count} 个文件", count=len(files)))
        elif self.model.entries:
            self.hint_label.setText(self.t("{count} 个项目", count=len(self.model.entries)))

    def _selection_changed(self, *_):
        if self._syncing_filename:
            return
        files = [entry.path for entry in self._selected_entries() if not entry.is_dir]
        self._syncing_filename = True
        self.filename_edit.setText(_format_selected_names(files))
        self._syncing_filename = False
        if files:
            self.hint_label.setText(self.t("已选择 {count} 个文件", count=len(files)))
        elif self.model.entries:
            self.hint_label.setText(self.t("{count} 个项目", count=len(self.model.entries)))

    def _item_double_clicked(self, index: QModelIndex):
        entry = self.model.entry_at(index.row())
        if entry is None:
            return
        if entry.is_dir:
            self.navigate(entry.path)
            return
        self._selected_files = [str(entry.path)]
        self.accept()

    def _activate_selected_or_accept(self):
        selected = self._selected_entries()
        if len(selected) == 1 and selected[0].is_dir:
            self.navigate(selected[0].path)
            return
        self._accept_selection()

    def _accept_selection(self):
        tokens = _parse_filename_tokens(self.filename_edit.text())
        if tokens:
            base = self.current_directory()
            resolved_files: list[str] = []
            for token in tokens:
                candidate = Path(token).expanduser()
                if not candidate.is_absolute() and base is not None:
                    candidate = base / candidate
                try:
                    if len(tokens) == 1 and candidate.is_dir():
                        self.navigate(candidate)
                        return
                    resolved = candidate.resolve(strict=True)
                except (OSError, RuntimeError):
                    self.hint_label.setText(self.t("找不到路径，请检查后重试。"))
                    return
                if resolved.is_dir():
                    self.hint_label.setText(self.t("请选择要添加的 TXT、EPUB 或 PDF 文件。"))
                    return
                if resolved.suffix.lower() not in SUPPORTED_SUFFIXES:
                    self.hint_label.setText(self.t("请选择 TXT、EPUB 或 PDF 文件。"))
                    return
                resolved_files.append(str(resolved))
            self._selected_files = resolved_files
            self.accept()
            return

        selected = self._selected_entries()
        if len(selected) == 1 and selected[0].is_dir:
            self.navigate(selected[0].path)
            return
        files = [str(entry.path) for entry in selected if not entry.is_dir]
        if files:
            self._selected_files = files
            self.accept()
            return
        self.hint_label.setText(self.t("请选择要添加的 TXT、EPUB 或 PDF 文件。"))
