"""Application-relative data paths and migration from the previous location."""

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from quietreader.library.shelf import BOOKSHELF_DIR, SUPPORTED_SUFFIXES, UNCLASSIFIED, book_id_for_path


LEGACY_BOOKSHELF_DIR = "书架"
LEGACY_UNCLASSIFIED_DIR = "未分类"
STATE_FILES = ("state.json", "state.backup.json")


def application_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


def application_data_directory() -> Path:
    return application_root() / "data"


def legacy_migration_needed(legacy_directory: Path, destination: Path) -> bool:
    legacy = Path(legacy_directory)
    target = Path(destination)
    try:
        if legacy.resolve() == target.resolve() or target.is_symlink():
            return False
        if target.exists():
            if not target.is_dir():
                return False
            if any(entry.name != "quietreader.lock" for entry in target.iterdir()):
                return False
    except OSError:
        return False

    has_state = any((legacy / name).is_file() and not (legacy / name).is_symlink() for name in STATE_FILES)
    old_bookshelf = legacy / LEGACY_BOOKSHELF_DIR
    has_bookshelf = old_bookshelf.is_dir() and not old_bookshelf.is_symlink()
    return legacy.is_dir() and (has_state or has_bookshelf)


def _copy_bookshelf(source: Path, destination: Path) -> dict[str, str]:
    if source.is_symlink() or not source.is_dir():
        return {}

    categories = [entry for entry in source.iterdir() if entry.is_dir() and not entry.is_symlink()]
    categories.sort(key=lambda entry: (entry.name.casefold() != LEGACY_UNCLASSIFIED_DIR.casefold(), entry.name.casefold()))
    target_root = destination / BOOKSHELF_DIR
    category_names: dict[str, str] = {}
    used_names: set[str] = set()

    for category in categories:
        desired = UNCLASSIFIED if category.name.casefold() == LEGACY_UNCLASSIFIED_DIR.casefold() else category.name
        target_name = desired
        suffix = 2
        while target_name.casefold() in used_names:
            target_name = f"{desired} ({suffix})"
            suffix += 1
        used_names.add(target_name.casefold())
        category_names[category.name.casefold()] = target_name

        target_category = target_root / target_name
        target_category.mkdir(parents=True, exist_ok=True)
        for book in category.iterdir():
            if book.is_symlink() or not book.is_file() or book.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            shutil.copy2(book, target_category / book.name)

    return category_names


def _rewrite_state_paths(
    path: Path,
    old_bookshelf: Path,
    staged_bookshelf: Path,
    final_bookshelf: Path,
    category_names: dict[str, str],
) -> None:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, RecursionError):
        return
    if not isinstance(state, dict):
        return

    try:
        old_root = old_bookshelf.resolve()
    except OSError:
        return

    id_map: dict[str, str] = {}
    books = state.get("books")
    if isinstance(books, list):
        for record in books:
            if not isinstance(record, dict) or not isinstance(record.get("path"), str):
                continue
            try:
                old_path = Path(record["path"]).resolve()
                relative = old_path.relative_to(old_root)
            except (OSError, ValueError, RuntimeError):
                continue
            if len(relative.parts) != 2:
                continue
            category = category_names.get(relative.parts[0].casefold())
            if category is None:
                continue
            staged_path = staged_bookshelf / category / relative.parts[1]
            if not staged_path.is_file():
                continue

            old_id = record.get("id")
            new_path = final_bookshelf / category / relative.parts[1]
            new_id = book_id_for_path(new_path)
            record["path"] = str(new_path.resolve())
            record["id"] = new_id
            if isinstance(old_id, str):
                id_map[old_id] = new_id

    for key in ("positions", "bookmarks", "term_notes"):
        records = state.get(key)
        if isinstance(records, dict):
            state[key] = {id_map.get(book_id, book_id): value for book_id, value in records.items()}
    if isinstance(state.get("last_book"), str):
        state["last_book"] = id_map.get(state["last_book"], state["last_book"])

    path.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def migrate_legacy_data(legacy_directory: Path, destination: Path) -> bool:
    """Copy legacy settings and books into an empty app-root data folder."""
    legacy = Path(legacy_directory)
    target = Path(destination)
    if not legacy_migration_needed(legacy, target):
        return False

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".quietreader-migration-", dir=target.parent) as temporary:
        staged = Path(temporary) / "data"
        staged.mkdir()

        for name in STATE_FILES:
            source = legacy / name
            if source.is_file() and not source.is_symlink():
                shutil.copy2(source, staged / name)

        old_bookshelf = legacy / LEGACY_BOOKSHELF_DIR
        category_names = _copy_bookshelf(old_bookshelf, staged)
        new_bookshelf = staged / BOOKSHELF_DIR
        if category_names:
            for name in STATE_FILES:
                state_file = staged / name
                if state_file.is_file():
                    _rewrite_state_paths(state_file, old_bookshelf, new_bookshelf, target / BOOKSHELF_DIR, category_names)

        if target.exists():
            if target.is_symlink() or not target.is_dir():
                return False
            existing = list(target.iterdir())
            if any(entry.name != "quietreader.lock" for entry in existing):
                return False
            if existing:
                moved: list[Path] = []
                try:
                    for entry in staged.iterdir():
                        destination_entry = target / entry.name
                        if destination_entry.exists():
                            raise FileExistsError(destination_entry)
                        os.replace(entry, destination_entry)
                        moved.append(destination_entry)
                except OSError:
                    for entry in reversed(moved):
                        try:
                            os.replace(entry, staged / entry.name)
                        except OSError:
                            pass
                    raise
            else:
                target.rmdir()
                os.replace(staged, target)
        else:
            os.replace(staged, target)
    return True


def remap_relocated_shelf_paths(state: dict, data_directory: Path) -> bool:
    """Rebase saved book paths when a copied app-root data folder is relocated."""
    if not isinstance(state, dict) or not isinstance(state.get("books"), list):
        return False

    shelf_root = Path(data_directory) / BOOKSHELF_DIR
    if shelf_root.is_symlink() or not shelf_root.is_dir():
        return False
    try:
        resolved_shelf_root = shelf_root.resolve(strict=True)
        entries = list(shelf_root.iterdir())
    except OSError:
        return False

    categories = {}
    for entry in entries:
        if entry.is_symlink() or not entry.is_dir():
            continue
        key = entry.name.casefold()
        if key in categories:
            categories[key] = None
        else:
            categories[key] = entry.name

    id_map = {}
    changed = False
    for record in state["books"]:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            continue
        try:
            old_path = Path(record["path"]).expanduser()
            if not old_path.is_absolute():
                continue
            parts = old_path.parts
            bookshelf_index = next(
                (
                    index for index, part in enumerate(parts)
                    if part.casefold() in {BOOKSHELF_DIR.casefold(), LEGACY_BOOKSHELF_DIR.casefold()}
                    and len(parts) - index == 3
                ),
                None,
            )
            if bookshelf_index is None:
                continue

            category_key = parts[bookshelf_index + 1].casefold()
            if category_key == LEGACY_UNCLASSIFIED_DIR.casefold():
                category_key = UNCLASSIFIED.casefold()
            category_name = categories.get(category_key)
            if not category_name:
                continue

            category_path = shelf_root / category_name
            matches = [
                entry for entry in category_path.iterdir()
                if entry.name.casefold() == parts[bookshelf_index + 2].casefold()
                and not entry.is_symlink()
                and entry.is_file()
                and entry.suffix.lower() in SUPPORTED_SUFFIXES
            ]
            if len(matches) != 1:
                continue
            new_path = matches[0].resolve(strict=True)
            new_path.relative_to(resolved_shelf_root)
            old_resolved = old_path.resolve(strict=False)
        except (OSError, RuntimeError, ValueError, IndexError):
            continue

        new_id = book_id_for_path(new_path)
        old_id = record.get("id")
        if old_resolved == new_path and old_id == new_id:
            continue
        record["path"] = str(new_path)
        record["id"] = new_id
        if isinstance(old_id, str) and old_id:
            id_map[old_id] = new_id
        changed = True

    for key in ("positions", "bookmarks", "term_notes"):
        records = state.get(key)
        if not isinstance(records, dict):
            continue
        for old_id, new_id in id_map.items():
            if old_id == new_id or old_id not in records:
                continue
            value = records.pop(old_id)
            records.setdefault(new_id, value)
    last_book = state.get("last_book")
    if isinstance(last_book, str) and last_book in id_map:
        state["last_book"] = id_map[last_book]

    return changed
