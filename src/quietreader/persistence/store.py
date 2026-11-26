from copy import deepcopy
import json
import os
from pathlib import Path
import shutil

from quietreader.domain.models import Settings

VERSION = 1
TERM_NOTES_VERSION = 1
MAX_BOOKMARKS = 200
MAX_BOOKMARK_NOTE = 1000
MAX_TERM_NOTES = 500
MAX_TERM_TEXT = 60
MAX_TERM_NOTE = 1000
MAX_TERM_NOTES_BYTES = 16 * 1024 * 1024
MAX_TERM_NOTES_BYTES_MIB = MAX_TERM_NOTES_BYTES // (1024 * 1024)
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
    for book_id, records in raw.items():
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
            if len(rows) >= MAX_TERM_NOTES:
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
        if rows:
            clean[book_id] = rows
    return clean


def _merge_term_notes(primary: dict, fallback: dict) -> dict:
    merged = _clean_term_notes(primary)
    for book_id, rows in _clean_term_notes(fallback).items():
        target = merged.setdefault(book_id, [])
        seen = {item["term"].casefold() for item in target}
        for row in rows:
            if len(target) >= MAX_TERM_NOTES:
                break
            key = row["term"].casefold()
            if key in seen:
                continue
            seen.add(key)
            target.append(dict(row))
    return merged


def _term_notes_payload_size(raw: dict) -> int:
    payload = {"version": TERM_NOTES_VERSION, "term_notes": _clean_term_notes(raw)}
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    newline_extra = max(0, len(os.linesep.encode("ascii")) - 1) * serialized.count("\n")
    return len(serialized.encode("utf-8")) + newline_extra


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
        self.term_notes_path = Path(directory) / "term_notes.json"
        self.term_notes_backup = self.term_notes_path.with_suffix(".backup.json")
        self.warning = ""
        self.read_only = False
        self.term_notes_warning = ""
        self.term_notes_read_only = False
        self.state = initial_state()
        self._load()

    def _load_term_notes(self, legacy_term_notes: dict):
        loaded = False
        for path in (self.term_notes_path, self.term_notes_backup):
            if not path.exists():
                continue
            try:
                if path.stat().st_size > MAX_TERM_NOTES_BYTES:
                    raise ValueError("term_notes too big")
                value = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("invalid term_notes schema")
                version = value.get("version", 0)
                if type(version) is not int or version not in (0, TERM_NOTES_VERSION):
                    self.term_notes_read_only = True
                    self.term_notes_warning = "词条标注数据来自不支持的版本。已保护原文件，本次不会保存词条更改。"
                    if not self.warning:
                        self.warning = self.term_notes_warning
                    self.state["term_notes"] = legacy_term_notes
                    return
                raw_notes = value.get("term_notes", {})
                if not isinstance(raw_notes, dict):
                    raise ValueError("invalid term_notes mapping")
                sidecar = _clean_term_notes(raw_notes)
                self.state["term_notes"] = (
                    _merge_term_notes(sidecar, legacy_term_notes) if legacy_term_notes else sidecar
                )
                if path == self.term_notes_backup:
                    self.term_notes_warning = "上次保存的词条标注数据损坏，已从备份恢复。"
                    if not self.warning:
                        self.warning = self.term_notes_warning
                    if self.term_notes_path.exists():
                        self.term_notes_path.replace(self.term_notes_path.with_suffix(".damaged.json"))
                loaded = True
                break
            except (OSError, ValueError, TypeError, RecursionError):
                self.term_notes_warning = "词条标注数据无法读取，已保护原文件，本次不会保存词条更改。"
        if not loaded:
            if self.term_notes_path.exists():
                self.term_notes_read_only = True
                if not self.warning:
                    self.warning = self.term_notes_warning
            self.state["term_notes"] = legacy_term_notes

    def _load(self):
        legacy_term_notes = {}
        loaded_state = False
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
                legacy_term_notes = _clean_term_notes(value.get("term_notes", {}))
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
                loaded_state = True
                break
            except (OSError, ValueError, TypeError, RecursionError):
                self.warning = "保存数据无法读取，已使用默认设置；原文件保留。"
        if not loaded_state and self.path.exists():
            self.read_only = True
        self._load_term_notes(legacy_term_notes)

    @staticmethod
    def _atomic_write_json(path: Path, backup: Path, payload: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            backup_temp = backup.with_suffix(".tmp")
            shutil.copy2(path, backup_temp)
            os.replace(backup_temp, backup)
        os.replace(temp, path)

    @staticmethod
    def term_notes_fit_storage_limit(term_notes: dict) -> bool:
        try:
            return _term_notes_payload_size(term_notes) <= MAX_TERM_NOTES_BYTES
        except (TypeError, ValueError, UnicodeError, RecursionError):
            return False

    def save(self):
        if self.read_only:
            return None
        data = deepcopy(self.state)
        term_notes = _clean_term_notes(data.pop("term_notes", {}))
        data["version"] = VERSION
        write_term_notes = (
            not self.term_notes_read_only
            and (self.term_notes_path.exists() or bool(term_notes))
        )
        if write_term_notes and not self.term_notes_fit_storage_limit(term_notes):
            return False
        self._atomic_write_json(self.path, self.backup, data)
        if write_term_notes:
            self._atomic_write_json(
                self.term_notes_path,
                self.term_notes_backup,
                {"version": TERM_NOTES_VERSION, "term_notes": term_notes},
            )
