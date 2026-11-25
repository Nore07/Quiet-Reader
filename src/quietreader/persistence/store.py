from copy import deepcopy
import json
import os
from pathlib import Path
import shutil

from quietreader.domain.models import Settings

VERSION = 1
MAX_BOOKMARKS = 200
MAX_BOOKMARK_NOTE = 1000
MAX_TERM_NOTES = 500
MAX_TERM_TEXT = 60
MAX_TERM_NOTE = 1000
MAX_BOOK_RECORDS = 500
MAX_BOOK_TITLE_OVERRIDE = 200
MAX_JUMP_HISTORY = 30
MAX_JUMP_HISTORY_BOOKS = 100


def normalize_term_text(raw: str) -> str:
    if not isinstance(raw, str):
        return ""
    cleaned = " ".join(raw.replace("\x00", "").split())
    return cleaned[:MAX_TERM_TEXT].strip()


def normalize_term_note(raw: str) -> str:
    if not isinstance(raw, str):
        return ""
    cleaned = raw.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return cleaned[:MAX_TERM_NOTE].strip()


def _clean_books(raw):
    clean = []
    for book in raw[:MAX_BOOK_RECORDS]:
        if not isinstance(book, dict) or not all(
            isinstance(book.get(key), str) for key in ("id", "path", "title")
        ):
            continue
        row = dict(book)
        override = row.get("title_override")
        if isinstance(override, str) and len(override) <= MAX_BOOK_TITLE_OVERRIDE:
            override = " ".join(override.split())
            if override:
                row["title_override"] = override
            else:
                row.pop("title_override", None)
        else:
            row.pop("title_override", None)
        clean.append(row)
    return clean


def _clean_bookmarks(raw):
    if not isinstance(raw, dict):
        return {}
    clean = {}
    remaining = MAX_BOOKMARKS
    for book_id, records in raw.items():
        if remaining <= 0:
            break
        if not isinstance(book_id, str) or not book_id or len(book_id) > 100 or not isinstance(records, list):
            continue
        rows = []
        for record in records:
            if remaining <= 0:
                break
            if not isinstance(record, dict):
                continue
            section_id = record.get("section_id")
            offset = record.get("offset")
            label = record.get("label")
            note = record.get("note", "")
            created = record.get("created", "")
            if (
                not isinstance(section_id, str) or not section_id or len(section_id) > 200
                or type(offset) is not int or not 0 <= offset <= 100_000_000
                or not isinstance(label, str) or not label
                or not isinstance(note, str) or not isinstance(created, str)
            ):
                continue
            rows.append({
                "section_id": section_id,
                "offset": offset,
                "label": label[:160],
                "note": note[:MAX_BOOKMARK_NOTE],
                "created": created[:40],
            })
            remaining -= 1
        if rows:
            clean[book_id] = rows
    return clean


def _clean_jump_history(raw):
    if not isinstance(raw, dict):
        return {}
    clean = {}
    recent_books = list(raw.items())[-MAX_JUMP_HISTORY_BOOKS:]
    for book_id, records in recent_books:
        if not isinstance(book_id, str) or not book_id or len(book_id) > 100 or not isinstance(records, list):
            continue
        rows = []
        for record in records[-MAX_JUMP_HISTORY:]:
            if not isinstance(record, dict):
                continue
            section_id = record.get("section_id")
            offset = record.get("offset")
            if (
                not isinstance(section_id, str) or not section_id or len(section_id) > 200
                or type(offset) is not int or not 0 <= offset <= 100_000_000
            ):
                continue
            rows.append({"section_id": section_id, "offset": offset})
        if rows:
            clean[book_id] = rows
    return clean


def _clean_term_notes(raw):
    if not isinstance(raw, dict):
        return {}
    clean = {}
    remaining = MAX_TERM_NOTES
    for book_id, records in raw.items():
        if remaining <= 0:
            break
        if (
            not isinstance(book_id, str)
            or not book_id
            or "\x00" in book_id
            or len(book_id) > 100
            or not isinstance(records, list)
        ):
            continue
        rows = []
        seen = set()
        for record in records:
            if remaining <= 0:
                break
            if not isinstance(record, dict):
                continue
            raw_term = record.get("term")
            raw_note = record.get("note")
            created = record.get("created", "")
            if (
                not isinstance(raw_term, str)
                or not isinstance(raw_note, str)
                or not isinstance(created, str)
            ):
                continue
            term = normalize_term_text(raw_term)
            note = normalize_term_note(raw_note)
            if not term or not note:
                continue
            key = term.casefold()
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "term": term,
                "note": note,
                "created": created.replace("\x00", "")[:40],
            })
            remaining -= 1
        if rows:
            clean[book_id] = rows
    return clean


def initial_state():
    return {
        "version": VERSION,
        "settings": Settings().to_dict(),
        "books": [],
        "positions": {},
        "bookmarks": {},
        "jump_history": {},
        "forward_history": {},
        "term_notes": {},
        "last_book": "",
        "last_browse_dir": "",
        "last_update_check": 0,
        "window": [100, 100, 1000, 760],
        "moyu_window": None,
    }


class StateStore:
    """Single GUI-thread writer; unknown future versions are never overwritten."""
    def __init__(self, directory: Path):
        self.path = Path(directory) / "state.json"
        self.backup = self.path.with_suffix(".backup.json")
        self.warning = ""
        self.read_only = False
        self.state = initial_state()
        self._load()

    def _load(self):
        for path in (self.path, self.backup):
            if not path.exists():
                continue
            try:
                if path.stat().st_size > 2 * 1024 * 1024:
                    raise ValueError("state too big")
                value = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("invalid schema")
                version = value.get("version", 0)
                if type(version) is not int or version not in (0, VERSION):
                    self.read_only = True
                    self.warning = "保存数据来自不支持的版本。已保护原文件，本次不会保存更改。"
                    return
                books, positions = value.get("books", []), value.get("positions", {})
                if not isinstance(books, list) or not isinstance(positions, dict):
                    raise ValueError("invalid records")
                clean = initial_state()
                clean["settings"] = Settings.from_dict(value.get("settings")).to_dict()
                clean["books"] = _clean_books(books)
                clean["positions"] = {k: p for k, p in positions.items() if isinstance(k, str) and isinstance(p, dict) and isinstance(p.get("section_id"), str) and type(p.get("offset")) is int}
                clean["bookmarks"] = _clean_bookmarks(value.get("bookmarks", {}))
                clean["jump_history"] = _clean_jump_history(value.get("jump_history", {}))
                clean["forward_history"] = _clean_jump_history(value.get("forward_history", {}))
                clean["term_notes"] = _clean_term_notes(value.get("term_notes", {}))
                clean["last_book"] = value.get("last_book", "") if isinstance(value.get("last_book", ""), str) else ""
                browse_dir = value.get("last_browse_dir", "")
                if isinstance(browse_dir, str) and "\x00" not in browse_dir and len(browse_dir) <= 4096:
                    clean["last_browse_dir"] = browse_dir
                last_update_check = value.get("last_update_check")
                if type(last_update_check) is int and last_update_check >= 0:
                    clean["last_update_check"] = last_update_check
                bounds = value.get("window")
                if isinstance(bounds, list) and len(bounds) == 4 and all(type(v) is int and abs(v) < 100000 for v in bounds):
                    clean["window"] = bounds
                moyu_bounds = value.get("moyu_window")
                if (
                    isinstance(moyu_bounds, list) and len(moyu_bounds) == 4
                    and all(type(v) is int and abs(v) < 100000 for v in moyu_bounds)
                    and moyu_bounds[2] >= 180 and moyu_bounds[3] > 0
                ):
                    clean["moyu_window"] = moyu_bounds
                self.state = clean
                if path == self.backup:
                    self.warning = "上次保存数据损坏，已从备份恢复。"
                    # Keep damaged primary out of the backup rotation.
                    self.path.replace(self.path.with_suffix(".damaged.json")) if self.path.exists() else None
                return
            except (OSError, ValueError, TypeError, RecursionError):
                self.warning = "保存数据无法读取，已使用默认设置；原文件保留。"
        if self.path.exists():
            self.read_only = True

    def save(self):
        if self.read_only:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        data = deepcopy(self.state)
        data["version"] = VERSION
        with temp.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        if self.path.exists():
            backup_temp = self.backup.with_suffix(".tmp")
            shutil.copy2(self.path, backup_temp)
            os.replace(backup_temp, self.backup)
        os.replace(temp, self.path)
