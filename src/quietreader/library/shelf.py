"""Safe filesystem operations for books managed by the local bookshelf."""

from hashlib import sha256
import os
from pathlib import Path
import re
import shutil
import stat
import uuid


BOOKSHELF_DIR = "bookshelf"
UNCLASSIFIED = "uncategorized"
SUPPORTED_SUFFIXES = {".txt", ".epub", ".pdf"}
_INVALID_CATEGORY_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_CATEGORY_NAMES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def _filesystem_detail(error):
    if isinstance(error, OSError):
        return getattr(error, "strerror", None) or "文件操作失败"
    return str(error)


class ShelfError(ValueError):
    pass


def book_id_for_path(path):
    resolved = Path(path).expanduser().resolve()
    return sha256(str(resolved).casefold().encode("utf-8")).hexdigest()[:24]


class ShelfManager:
    """Stores imported copies in one unclassified directory and flat category folders."""

    def __init__(self, data_directory):
        self.data_directory = Path(data_directory).expanduser().resolve()
        self.root = self.data_directory / BOOKSHELF_DIR

    @property
    def unclassified(self):
        return self.root / UNCLASSIFIED

    def ensure_layout(self):
        if self.root.is_symlink():
            raise ShelfError("书架目录不能是符号链接。")
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.is_dir():
            raise ShelfError("书架路径不是文件夹。")
        self.unclassified.mkdir(exist_ok=True)
        if self.unclassified.is_symlink():
            raise ShelfError("书架未分类目录不能是符号链接。")

    def is_managed(self, path):
        if self.root.is_symlink():
            return False
        try:
            resolved = Path(path).expanduser().resolve(strict=True)
            relative = resolved.relative_to(self.root.resolve())
            return (
                len(relative.parts) == 2 and resolved.is_file()
                and (relative.parts[0].casefold() == UNCLASSIFIED.casefold()
                     or any(relative.parts[0].casefold() == name.casefold() for name in self.categories()))
            )
        except (OSError, ValueError):
            return False

    def category_for_path(self, path):
        if not self.is_managed(path):
            return UNCLASSIFIED
        resolved = Path(path).expanduser().resolve()
        if resolved.parent.parent != self.root.resolve():
            return UNCLASSIFIED
        return resolved.parent.name

    def categories(self):
        if not self.root.is_dir() or self.root.is_symlink():
            return []
        result = []
        for entry in self.root.iterdir():
            if entry.name.casefold() == UNCLASSIFIED.casefold() or not entry.is_dir() or entry.is_symlink():
                continue
            try:
                if entry.resolve().parent == self.root.resolve():
                    result.append(entry.name)
            except OSError:
                continue
        return sorted(result, key=str.casefold)

    @staticmethod
    def validate_category_name(name):
        if not isinstance(name, str):
            raise ShelfError("分类名称无效。")
        name = name.strip()
        if (
            not name or len(name) > 64 or name in (".", "..")
            or name.endswith((".", " ")) or _INVALID_CATEGORY_CHARS.search(name)
            or name.casefold() in {UNCLASSIFIED.casefold(), "未分类".casefold()}
            or name.split(".", 1)[0].casefold() in _RESERVED_CATEGORY_NAMES
        ):
            raise ShelfError("分类名称为空、重复或包含文件夹不允许的字符。")
        return name

    def category_path(self, name):
        if name == UNCLASSIFIED:
            self.ensure_layout()
            return self.unclassified
        name = self.validate_category_name(name)
        if self.root.is_symlink():
            raise ShelfError("书架目录不能是符号链接。")
        path = self.root / name
        try:
            if path.resolve().parent != self.root.resolve() or path.is_symlink() or not path.is_dir():
                raise ShelfError("找不到这个书架分类。")
        except OSError:
            raise ShelfError("找不到这个书架分类。") from None
        return path

    def create_category(self, name):
        name = self.validate_category_name(name)
        self.ensure_layout()
        if any(existing.casefold() == name.casefold() for existing in self.categories()):
            raise ShelfError("这个分类已经存在。")
        path = self.root / name
        try:
            path.mkdir()
        except FileExistsError:
            raise ShelfError("这个分类已经存在。") from None
        except OSError as error:
            raise ShelfError(f"无法创建分类：{_filesystem_detail(error)}") from None
        return path

    def rename_category(self, name, new_name):
        """Rename a category directory and return its books' old and new paths."""
        if name == UNCLASSIFIED:
            raise ShelfError("未分类文件夹不能重命名。")
        new_name = self.validate_category_name(new_name)
        source = self.category_path(name).resolve()
        if source.name == new_name:
            return source, source, {}
        destination = source.with_name(new_name)
        try:
            if any(entry.name != source.name and entry.name.casefold() == new_name.casefold()
                   for entry in self.root.iterdir()):
                raise ShelfError("这个分类已经存在。")
            paths = {
                entry: destination / entry.name
                for entry in source.iterdir()
                if entry.suffix.lower() in SUPPORTED_SUFFIXES and not entry.is_symlink() and entry.is_file()
            }
            source.rename(destination)
        except FileExistsError:
            raise ShelfError("这个分类已经存在。") from None
        except OSError as error:
            raise ShelfError(f"无法重命名分类：{_filesystem_detail(error)}") from None
        return source, destination, paths

    @staticmethod
    def _unique_path(directory, filename):
        candidate = directory / filename
        if not candidate.exists():
            return candidate
        source = Path(filename)
        index = 2
        while True:
            candidate = directory / f"{source.stem} ({index}){source.suffix}"
            if not candidate.exists():
                return candidate
            index += 1

    def _managed_source(self, path):
        original = Path(path).expanduser()
        if original.is_symlink():
            raise ShelfError("书架中的书籍不能是符号链接。")
        if self.root.is_symlink():
            raise ShelfError("书架目录不能是符号链接。")
        try:
            resolved = original.resolve(strict=True)
        except (OSError, RuntimeError):
            raise ShelfError("书籍文件不存在或无法访问。") from None
        if not resolved.is_file():
            raise ShelfError("请选择 TXT、EPUB 或 PDF 文件。")
        try:
            relative = resolved.relative_to(self.root.resolve())
        except ValueError:
            return resolved, False
        if len(relative.parts) != 2 or relative.parts[0].casefold() not in {UNCLASSIFIED.casefold(), *(name.casefold() for name in self.categories())}:
            raise ShelfError("书架文件必须直接位于未分类或一个分类文件夹中。")
        if resolved.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise ShelfError("书架只支持 TXT、EPUB 和 PDF 文件。")
        return resolved, True

    def add_book(self, source):
        original = Path(source).expanduser()
        try:
            resolved = original.resolve(strict=True)
        except (OSError, RuntimeError):
            raise ShelfError("书籍文件不存在或无法访问。") from None
        if not resolved.is_file() or resolved.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise ShelfError("请选择 TXT、EPUB 或 PDF 文件。")
        if original.is_symlink():
            resolved = original.resolve(strict=True)
        if self.is_managed(resolved):
            return resolved
        self.ensure_layout()
        target = self._unique_path(self.unclassified, resolved.name)
        return self._copy_atomic(resolved, target)

    @staticmethod
    def _copy_atomic(source, destination):
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            shutil.copy2(source, temporary)
            temporary.rename(destination)
            return destination
        except OSError as error:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise ShelfError(f"复制书籍失败：{_filesystem_detail(error)}") from None

    def move_book(self, path, target_category):
        source, managed = self._managed_source(path)
        if source.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise ShelfError("书架只支持 TXT、EPUB 和 PDF 文件。")
        destination_dir = self.category_path(target_category)
        if managed and source.parent.resolve() == destination_dir.resolve():
            return source
        destination = self._unique_path(destination_dir, source.name)
        if not managed:
            return self._copy_atomic(source, destination)
        try:
            source.rename(destination)
        except OSError as error:
            raise ShelfError(f"移动书籍失败：{_filesystem_detail(error)}") from None
        return destination

    def remove_book(self, path):
        """Remove a managed shelf copy; legacy books outside the shelf stay on disk."""
        source, managed = self._managed_source(path)
        if not managed:
            return False
        original_mode = None
        try:
            if os.name == "nt":
                original_mode = source.stat().st_mode
                if not original_mode & stat.S_IWRITE:
                    # copy2 preserves Windows' read-only flag from the source book.
                    source.chmod(original_mode | stat.S_IWRITE)
            source.unlink()
        except OSError as error:
            if original_mode is not None and not original_mode & stat.S_IWRITE:
                try:
                    source.chmod(original_mode)
                except OSError:
                    pass
            raise ShelfError(f"删除书架书籍失败：{_filesystem_detail(error)}") from None
        return True

    def delete_category(self, name, delete_books):
        if name == UNCLASSIFIED:
            raise ShelfError("未分类文件夹不能删除。")
        category = self.category_path(name)
        entries = list(category.iterdir())
        if any(entry.is_dir() and not entry.is_symlink() for entry in entries):
            raise ShelfError("分类中包含子文件夹，无法安全删除或移动。")
        if any(entry.is_symlink() or not entry.is_file() or entry.suffix.lower() not in SUPPORTED_SUFFIXES for entry in entries):
            raise ShelfError("分类中包含非书籍或无法安全处理的内容。")
        mapping = {}
        if delete_books:
            for entry in entries:
                if entry.is_dir() and not entry.is_symlink():
                    raise ShelfError("分类中包含子文件夹，无法安全删除或移动。")
                mapping[entry.resolve()] = None
            try:
                shutil.rmtree(category)
            except OSError as error:
                raise ShelfError(f"删除分类失败：{_filesystem_detail(error)}") from None
            return mapping

        self.ensure_layout()
        moved = []
        try:
            for entry in entries:
                if not entry.is_file() or entry.is_symlink():
                    raise ShelfError("分类中包含无法安全移动的内容。")
                destination = self._unique_path(self.unclassified, entry.name)
                entry.rename(destination)
                moved.append((entry, destination))
                mapping[entry.resolve()] = destination
            category.rmdir()
        except (OSError, ShelfError) as error:
            rollback_errors = []
            for previous, destination in reversed(moved):
                try:
                    destination.rename(previous)
                except OSError as rollback_error:
                    rollback_errors.append(_filesystem_detail(rollback_error))
            if rollback_errors:
                reason = _filesystem_detail(error)
                raise ShelfError(f"分类移动失败，回滚也未完成：{reason}; {'; '.join(rollback_errors)}") from None
            if isinstance(error, ShelfError):
                raise
            raise ShelfError(f"移动分类书籍失败：{_filesystem_detail(error)}") from None
        return mapping
