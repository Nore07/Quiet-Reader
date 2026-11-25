from dataclasses import asdict, dataclass, field
import math
import re
from quietreader.keys import BINDING_FIELDS, binding_error, normalize_binding


@dataclass
class EPUBLink:
    start: int
    end: int
    path: str
    fragment: str = ""


@dataclass
class EPUBFormatRange:
    start: int
    end: int
    kind: str
    value: str = ""


@dataclass
class NavigationEntry:
    label: str
    path: str
    fragment: str = ""
    children: list["NavigationEntry"] = field(default_factory=list)
    semantic_type: str = ""
    source_offset: int = -1


@dataclass
class Section:
    id: str
    title: str
    text: str
    images: dict[int, bytes] = field(default_factory=dict)
    source_path: str = ""
    anchors: dict[str, int] = field(default_factory=dict)
    links: list[EPUBLink] = field(default_factory=list)
    formats: list[EPUBFormatRange] = field(default_factory=list)


@dataclass
class Book:
    id: str
    path: str
    title: str
    author: str = ""
    sections: list[Section] = field(default_factory=list)
    encoding: str = ""
    toc: list[NavigationEntry] = field(default_factory=list)
    page_list: list[NavigationEntry] = field(default_factory=list)
    landmarks: list[NavigationEntry] = field(default_factory=list)
    language: str = ""
    identifier: str = ""
    publisher: str = ""
    publication_date: str = ""
    description: str = ""
    cover: bytes = b""


@dataclass
class Bookmark:
    section_id: str
    offset: int
    label: str
    note: str = ""
    created: str = ""


@dataclass
class TermNote:
    term: str
    note: str
    created: str = ""


@dataclass
class Position:
    section_id: str = ""
    offset: int = 0


def valid_hotkey(value: str) -> bool:
    from quietreader.keys import windows_hotkey
    return windows_hotkey(value) is not None


@dataclass
class Settings:
    font_size: int = 20
    font_family: str = "Microsoft YaHei"
    line_height: float = 1.8
    padding: int = 32
    letter_spacing: float = 0.5
    paragraph_spacing: int = 12
    opacity: float = 0.92
    background_color: str = ""
    text_color: str = ""
    language: str = "system"
    theme: str = "system"
    always_on_top: bool = False
    close_to_tray: bool = True
    global_hotkey: bool = False
    hotkey: str = "Ctrl+Alt+R"
    emergency_key: str = "Ctrl+Alt+Q"
    next_key: str = "PgDown"
    previous_key: str = "PgUp"
    next_chapter_key: str = "Ctrl+PgDown"
    previous_chapter_key: str = "Ctrl+PgUp"
    focus_key: str = "F10"
    focus_opacity_key: str = "F9"
    focus_resample_key: str = "F8"
    focus_adjust_key: str = "Alt"
    auto_check_updates: bool = False
    focus_auto_background: bool = False
    focus_sample_trigger: str = "auto"
    focus_complex_opacity: float = 0.20

    @classmethod
    def from_dict(cls, raw):
        raw = raw if isinstance(raw, dict) else {}
        result = cls()
        for key, low, high, convert in [
            ("font_size", 14, 42, int), ("line_height", 1.2, 2.5, float),
            ("padding", 12, 72, int), ("letter_spacing", 0, 5, float),
            ("paragraph_spacing", 0, 40, int), ("opacity", 0, 1, float),
            ("focus_complex_opacity", 0, 1, float),
        ]:
            try:
                value = float(raw.get(key, getattr(result, key)))
                if math.isfinite(value):
                    setattr(result, key, convert(max(low, min(high, value))))
            except (ValueError, TypeError, OverflowError):
                pass
        for key in ["always_on_top", "close_to_tray", "global_hotkey", "auto_check_updates", "focus_auto_background"]:
            if type(raw.get(key)) is bool:
                setattr(result, key, raw[key])
        if raw.get("theme") in ("system", "light", "dark", "sepia"):
            result.theme = raw["theme"]
        color = raw.get("background_color")
        if isinstance(color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            result.background_color = color.lower()
        color = raw.get("text_color")
        if isinstance(color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            result.text_color = color.lower()
        if raw.get("language") in ("system", "zh_CN", "en"):
            result.language = raw["language"]
        if raw.get("focus_adjust_key") in ("Alt", "Ctrl", "Shift"):
            result.focus_adjust_key = raw["focus_adjust_key"]
        if raw.get("focus_sample_trigger") in ("auto", "manual"):
            result.focus_sample_trigger = raw["focus_sample_trigger"]
        if isinstance(raw.get("font_family"), str) and 0 < len(raw["font_family"]) <= 100:
            result.font_family = raw["font_family"]
        bindings = {key: normalize_binding(raw.get(key, getattr(result, key))) for key in BINDING_FIELDS}
        if not bindings["focus_opacity_key"] or bindings["focus_opacity_key"] in {
            value for key, value in bindings.items() if key != "focus_opacity_key"
        }:
            used = {value for key, value in bindings.items() if key != "focus_opacity_key"}
            candidates = [f"F{number}" for number in (*range(9, 25), *range(8, 0, -1))]
            bindings["focus_opacity_key"] = next((candidate for candidate in candidates if candidate not in used), "F9")
        if "focus_resample_key" not in raw or not bindings["focus_resample_key"] or bindings["focus_resample_key"] in {
            value for key, value in bindings.items() if key != "focus_resample_key"
        }:
            used = {value for key, value in bindings.items() if key != "focus_resample_key"}
            candidates = [f"F{number}" for number in (*range(8, 0, -1), *range(11, 25), 9, 10)]
            bindings["focus_resample_key"] = next((candidate for candidate in candidates if candidate not in used), "F8")
        if "emergency_key" not in raw:
            used = {value for key, value in bindings.items() if key != "emergency_key"}
            bindings["emergency_key"] = next(key for key in ("Ctrl+Alt+Q", "Ctrl+Shift+Q", "Ctrl+Alt+Shift+Q", "Ctrl+Alt+F9", "Ctrl+Alt+F8") if key not in used)
        for field, candidates in [
            ("next_chapter_key", ("Ctrl+Shift+PgDown", "Alt+PgDown", "Ctrl+Alt+PgDown", "Ctrl+Shift+N")),
            ("previous_chapter_key", ("Ctrl+Shift+PgUp", "Alt+PgUp", "Ctrl+Alt+PgUp", "Ctrl+Shift+P")),
        ]:
            if field not in raw or not bindings[field]:
                used = {value for key, value in bindings.items() if key != field}
                if bindings[field] in used:
                    bindings[field] = next((candidate for candidate in candidates if candidate not in used), bindings[field])
        if not binding_error(bindings):
            for key, value in bindings.items():
                setattr(result, key, value)
        return result

    def to_dict(self):
        return asdict(self)


def restore_position(sections: list[Section], position: Position) -> tuple[int, int]:
    if not sections:
        return 0, 0
    index = next((i for i, section in enumerate(sections) if section.id == position.section_id), 0)
    try:
        offset = max(0, min(len(sections[index].text), int(position.offset)))
    except (TypeError, ValueError, OverflowError):
        offset = 0
    return index, offset
