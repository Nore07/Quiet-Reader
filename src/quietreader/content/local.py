"""Bounded local imports. No extraction, script execution or network access."""

from hashlib import sha256
from html.entities import name2codepoint as _HTML_NAMED_ENTITIES
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
import posixpath
import re
from urllib.parse import unquote, urlsplit
import zipfile

from charset_normalizer import from_bytes
from defusedxml import ElementTree as ET

from quietreader.domain.models import Book, EPUBFormatRange, EPUBLink, NavigationEntry, Section

MAX_FILE = 32 * 1024 * 1024
MAX_EXPANDED = 64 * 1024 * 1024
MAX_EPUB_TITLE_LENGTH = 300
CHUNK_SIZE = 24000
MAX_EPUB_ANCHORS = 100000
MAX_EPUB_LINKS = 50000
MAX_EPUB_NAV_ENTRIES = 20000
MAX_EPUB_FORMAT_RANGES = 50000
MAX_EPUB_IMAGE_REFERENCES = 4096
ENCODINGS = ("auto", "utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "gb18030", "gbk")


class ImportError(ValueError):
    def __init__(self, message: str, encoding_choice=False):
        super().__init__(message)
        self.encoding_choice = encoding_choice


def _valid_text(text):
    if not text.strip():
        raise ImportError("这本书没有可阅读的文字。")
    controls = sum(ord(c) < 32 and c not in "\n\r\t\f" for c in text)
    if controls:
        raise ImportError("文件含有异常字符，请手动指定编码或检查文件。", True)
    return text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")


def decode_text(data: bytes, encoding="auto") -> tuple[str, str]:
    if encoding not in ENCODINGS:
        raise ImportError("不支持这个编码选项。", True)
    if not data:
        raise ImportError("这个文件是空的。")
    if encoding != "auto":
        try:
            return _valid_text(data.decode(encoding, errors="strict")), encoding
        except UnicodeError:
            raise ImportError("所选编码无法读取这个文件，请尝试其他编码。", True) from None
    for bom, codec in [(b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")]:
        if data.startswith(bom):
            return decode_text(data, codec)
    try:
        return _valid_text(data.decode("utf-8", errors="strict")), "utf-8"
    except UnicodeError:
        pass
    # BOM-less UTF-16 and legacy encodings remain ambiguous: offer an explicit choice.
    sample = data[:128000]
    guess = from_bytes(sample).best()
    if guess and guess.encoding in {"gb18030", "gb2312", "gbk", "cp936"} and guess.chaos < 0.05:
        try:
            text = data.decode("gb18030", errors="strict")
            if sum("\u3400" <= c <= "\u9fff" for c in text) / max(1, len(text)) > 0.15:
                return _valid_text(text), "gb18030"
        except UnicodeError:
            pass
    raise ImportError("无法可靠识别文字编码，请选择 GB18030、UTF-16 或其他编码重试。", True)


def split_section(key: str, title: str, text: str) -> list[Section]:
    parts = []
    start = 0
    while start < len(text):
        end = min(len(text), start + CHUNK_SIZE)
        if end < len(text):
            boundary = text.rfind("\n", start + CHUNK_SIZE // 2, end)
            if boundary >= 0:
                end = boundary + 1
        count = len(parts)
        parts.append(Section(f"{key}:{count}", title if not count else f"{title} · 续 {count}", text[start:end]))
        start = end
    return parts


def parse_txt(text: str) -> list[Section]:
    text = _valid_text(text)
    heading = re.compile(r"^\s*(第[零〇一二三四五六七八九十百千万两\d]+[章回节卷部篇].{0,60}|(?:chapter|part)\s+\d+.{0,60}|序章|序言|楔子|尾声|后记)\s*$", re.I)
    sections, lines, title, number = [], [], "正文", 0
    def flush():
        nonlocal number
        body = re.sub(r"\n[ \t\n]*\n", "\n", "\n".join(lines)).strip()
        if body:
            sections.extend(split_section(f"txt:{number}", title, body))
            number += 1
    for line in text.split("\n"):
        if heading.fullmatch(line):
            flush()
            lines = []
            title = line.strip()
        else:
            lines.append(line.rstrip())
    flush()
    if not sections:
        sections = split_section("txt:0", title, text)
    return sections


class TextExtractor(HTMLParser):
    """Retain text only; no widget ever receives book HTML."""
    FORMAT_TAGS = {
        "h1": ("heading", "1"), "h2": ("heading", "2"), "h3": ("heading", "3"),
        "h4": ("heading", "4"), "h5": ("heading", "5"), "h6": ("heading", "6"),
        "strong": ("bold", ""), "b": ("bold", ""), "em": ("italic", ""), "i": ("italic", ""),
        "cite": ("italic", ""), "dfn": ("italic", ""), "del": ("strike", ""),
        "s": ("strike", ""), "strike": ("strike", ""), "code": ("code", ""),
        "tt": ("code", ""), "kbd": ("code", ""), "samp": ("code", ""),
        "sup": ("superscript", ""), "sub": ("subscript", ""), "u": ("underline", ""),
        "ins": ("underline", ""), "mark": ("highlight", ""), "blockquote": ("quote", ""),
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.length = 0
        self.blocked = 0
        self.heading_depth = 0
        self.heading = []
        self._heading_found = False
        self._capture_heading = False
        self.anchors = {}
        self.links = []
        self.formats = []
        self.pagebreaks = []
        self._open_links = []
        self._open_formats = []
        self._lists = []

    def _append(self, value):
        if value:
            self.parts.append(value)
            self.length += len(value)

    def _begin_format(self, tag, kind, value=""):
        if len(self.formats) + len(self._open_formats) >= MAX_EPUB_FORMAT_RANGES:
            raise ImportError("EPUB 文本格式标记数量超过当前支持范围。")
        self._open_formats.append((tag, self.length, kind, value))

    def _end_format(self, tag):
        for index in range(len(self._open_formats) - 1, -1, -1):
            open_tag, start, kind, value = self._open_formats[index]
            if open_tag == tag:
                del self._open_formats[index]
                if self.length > start:
                    self.formats.append(EPUBFormatRange(start, self.length, kind, value))
                break

    def _close_open_formats(self):
        while self._open_formats:
            tag, _start, _kind, _value = self._open_formats[-1]
            self._end_format(tag)

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "svg", "math", "iframe", "object"):
            self.blocked += 1
        if self.blocked:
            return
        values = {key.rsplit(":", 1)[-1].rsplit("}", 1)[-1].lower(): value for key, value in attrs if key}
        anchor = values.get("id") or (values.get("name") if tag == "a" else "")
        if anchor:
            if anchor not in self.anchors and len(self.anchors) >= MAX_EPUB_ANCHORS:
                raise ImportError("EPUB 锚点数量超过当前支持范围。")
            self.anchors.setdefault(anchor, self.length)
        semantic = (values.get("type", "") + " " + values.get("role", "")).lower().split()
        if "pagebreak" in semantic or "doc-pagebreak" in semantic:
            self.pagebreaks.append({"offset": self.length, "fragment": anchor or "", "label": values.get("title") or values.get("aria-label") or anchor or ""})
        if tag in ("a", "area") and values.get("href"):
            if len(self.links) + len(self._open_links) >= MAX_EPUB_LINKS:
                raise ImportError("EPUB 内部链接数量超过当前支持范围。")
            self._open_links.append((self.length, values["href"]))
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.heading_depth += 1
            if self.heading_depth == 1 and not self._heading_found:
                self._heading_found = True
                self._capture_heading = True
        if tag in ("ol", "ul"):
            try:
                start = max(1, min(1000000, int(values.get("start", "1"))))
            except (TypeError, ValueError):
                start = 1
            self._lists.append({"tag": tag, "next": start})
        is_list_item = tag == "li"
        if is_list_item:
            depth = min(32, max(0, len(self._lists) - 1))
            marker = "•"
            if self._lists and self._lists[-1]["tag"] == "ol":
                try:
                    number = max(1, min(1000000, int(values.get("value", self._lists[-1]["next"]))))
                except (TypeError, ValueError):
                    number = self._lists[-1]["next"]
                marker = f"{number}."
                self._lists[-1]["next"] = min(1000001, number + 1)
            self._append("\n" + "  " * depth + marker + " ")
            self._begin_format(tag, "list_item", str(depth))
        elif tag in ("p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "section", "tr", "blockquote"):
            self._append("\n")
        if tag == "hr":
            self._append("\n")
            self._begin_format(tag, "rule")
            self._append("────────────────")
            self._end_format(tag)
            self._append("\n")
        elif tag in self.FORMAT_TAGS:
            kind, value = self.FORMAT_TAGS[tag]
            self._begin_format(tag, kind, value)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "svg", "math", "iframe", "object"):
            self.blocked = max(0, self.blocked - 1)
            return
        if self.blocked:
            return
        if tag in self.FORMAT_TAGS or tag == "li":
            self._end_format(tag)
        if tag in ("a", "area") and self._open_links:
            start, href = self._open_links.pop()
            self.links.append((start, self.length, href))
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.heading_depth = max(0, self.heading_depth - 1)
            if self.heading_depth == 0:
                self._capture_heading = False
        if tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "section", "tr", "blockquote"):
            self._append("\n")
        if tag in ("ol", "ul"):
            for index in range(len(self._lists) - 1, -1, -1):
                if self._lists[index]["tag"] == tag:
                    del self._lists[index:]
                    break

    def handle_data(self, data):
        if not self.blocked:
            self._append(data)
            if self._capture_heading:
                self.heading.append(data)

    def close(self):
        super().close()
        while self._open_links:
            start, href = self._open_links.pop()
            self.links.append((start, self.length, href))
        self._close_open_formats()


def _normalize_with_positions(text, positions):
    """Apply the text cleanup while keeping source offsets in the cleaned text."""
    wanted = sorted(set(max(0, min(len(text), value)) for value in positions))
    mapped = {}
    output = []
    output_length = 0
    event = 0

    def exact(start, end):
        nonlocal event, output_length
        while event < len(wanted) and wanted[event] < end:
            mapped[wanted[event]] = output_length + wanted[event] - start
            event += 1
        output.append(text[start:end])
        output_length += end - start

    cursor = 0
    for match in re.finditer(r"\s+", text):
        exact(cursor, match.start())
        whitespace = match.group()
        replacement = "\n" if whitespace.count("\n") >= 2 else re.sub(r"[ \t]+", " ", whitespace)
        while event < len(wanted) and wanted[event] < match.end():
            mapped[wanted[event]] = output_length + (len(replacement) if wanted[event] > match.start() else 0)
            event += 1
        output.append(replacement)
        output_length += len(replacement)
        cursor = match.end()
    exact(cursor, len(text))
    while event < len(wanted):
        mapped[wanted[event]] = output_length
        event += 1
    intermediate = "".join(output)
    left_trim = len(intermediate) - len(intermediate.lstrip())
    cleaned = intermediate.strip()
    result = {position: max(0, min(len(cleaned), offset - left_trim)) for position, offset in mapped.items()}
    return cleaned, result


def _attribute(element, name):
    for key, value in element.attrib.items():
        if key.rsplit(":", 1)[-1].rsplit("}", 1)[-1].lower() == name:
            return value
    return ""


def _navigation_target(document_path, href):
    try:
        parsed = urlsplit(href)
        if parsed.scheme or parsed.netloc or "\\" in href:
            return None
        path = _zip_path(posixpath.dirname(document_path), parsed.path) if parsed.path else document_path
        return path, unquote(parsed.fragment)
    except (ImportError, ValueError):
        return None


def _xhtml_navigation_items(parent, document_path):
    def parse_item(node):
        label_node = next((child for child in node if _tag(child) in ("a", "span")), None)
        if label_node is None:
            return None
        label = "".join(label_node.itertext()).strip()
        target = _navigation_target(document_path, label_node.get("href", "")) if label_node.get("href") else None
        children = []
        for child in node:
            if _tag(child) == "ol":
                children.extend(parse_item(value) for value in child if _tag(value) == "li")
        children = [child for child in children if child is not None]
        if not label or (target is None and not children):
            return None
        path, fragment = target if target else ("", "")
        return NavigationEntry(label, path, fragment, children, _attribute(label_node, "type"))

    lists = [child for child in parent if _tag(child) == "ol"]
    if lists:
        return [entry for ordered in lists for entry in (parse_item(item) for item in ordered if _tag(item) == "li") if entry]
    return [
        NavigationEntry("".join(node.itertext()).strip(), *(_navigation_target(document_path, node.get("href")) or ("", "")), semantic_type=_attribute(node, "type"))
        for node in parent.iter() if _tag(node) == "a" and node.get("href") and "".join(node.itertext()).strip()
    ]


def _parse_epub3_navigation(root, document_path):
    result = {"toc": [], "page_list": [], "landmarks": []}
    has_toc = False
    for nav in (element for element in root.iter() if _tag(element) == "nav"):
        kinds = _attribute(nav, "type").split()
        kind = next((value for value in kinds if value in result), "")
        if not kind and not has_toc:
            kind = "toc"  # Compatibility with older EPUB3 files that omit epub:type.
        if not kind:
            continue
        entries = _xhtml_navigation_items(nav, document_path)
        if entries and not result[kind]:
            result[kind] = entries
        if kind == "toc":
            has_toc = True
    return result


def _parse_ncx_navigation(root, document_path):
    result = {"toc": [], "page_list": [], "landmarks": []}

    def point(node, element_name):
        label_node = next((child for child in node if _tag(child) in ("navLabel", "pageLabel")), None)
        content = next((child for child in node if _tag(child) in ("content", "navTarget")), None)
        label = "".join(label_node.itertext()).strip() if label_node is not None else ""
        target = _navigation_target(document_path, content.get("src", "")) if content is not None else None
        children = [point(child, element_name) for child in node if _tag(child) == element_name]
        children = [child for child in children if child is not None]
        if not label or target is None:
            return None
        path, fragment = target
        return NavigationEntry(label, path, fragment, children, _attribute(node, "type"))

    for node in root.iter():
        if _tag(node) == "navMap":
            result["toc"] = [entry for entry in (point(child, "navPoint") for child in node if _tag(child) == "navPoint") if entry]
        elif _tag(node) == "pageList":
            result["page_list"] = [entry for entry in (point(child, "pageTarget") for child in node if _tag(child) == "pageTarget") if entry]
        elif _tag(node) == "navList" and _attribute(node, "type").lower() == "landmarks":
            result["landmarks"] = [entry for entry in (point(child, "navTarget") for child in node if _tag(child) == "navTarget") if entry]
    return result


def _walk_navigation(entry):
    yield entry
    for child in entry.children:
        yield from _walk_navigation(child)


def _tag(element):
    return element.tag.rsplit("}", 1)[-1]


def _zip_path(base: str, href: str) -> str:
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc or "\\" in href:
        raise ImportError("EPUB 含有不支持的外部或异常资源路径。")
    path = unquote(parsed.path)
    joined = posixpath.normpath(posixpath.join(base, path))
    if joined.startswith(("../", "/")) or joined == ".." or "\x00" in joined:
        raise ImportError("EPUB 含有不安全的资源路径。")
    return joined


def _safe_vector(payload):
    probe = payload.lstrip()
    if not probe.startswith((b"<", b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff")):
        return False
    vector = ET.fromstring(payload)
    if _tag(vector) != "svg":
        raise ImportError("EPUB 插图损坏或格式不受支持。")
    for part in vector.iter():
        if _tag(part) in ("script", "foreignObject", "image", "animate", "set", "animateTransform"):
            raise ImportError("EPUB SVG 插图包含不支持的活动或外部内容。")
        for key, value in list(part.attrib.items()) + [("style", part.text or "")]:
            if key.rsplit("}", 1)[-1] == "href" and not value.startswith("#"):
                raise ImportError("EPUB SVG 插图包含不支持的活动或外部内容。")
            if "@import" in value.lower() or any(not ref.strip().strip("'\"").startswith("#") for ref in re.findall(r"url\((.*?)\)", value, re.I)):
                raise ImportError("EPUB SVG 插图包含不支持的活动或外部内容。")
    return True


def _validate_image_payload(payload):
    if len(payload) > 8 * 1024 * 1024:
        raise ImportError("EPUB 插图超过 8 MiB，无法安全读取。")
    from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
    from PySide6.QtGui import QImageReader
    safe_vector = _safe_vector(payload)
    buffer = QBuffer()
    buffer.setData(QByteArray(payload))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    decoder = QImageReader(buffer)
    if bytes(decoder.format()).lower() in (b"svg", b"svgz") and not safe_vector:
        raise ImportError("EPUB 插图损坏或格式不受支持。")
    size = decoder.size()
    if not size.isValid() or size.width() * size.height() > 16000000:
        raise ImportError("EPUB 插图尺寸过大或格式不受支持。")
    decoder.setScaledSize(size.scaled(2000, 2000, Qt.AspectRatioMode.KeepAspectRatio))
    if decoder.read().isNull():
        raise ImportError("EPUB 插图损坏或格式不受支持。")
    return payload


_XML_PREDEFINED_ENTITIES = frozenset({"amp", "lt", "gt", "quot", "apos"})
_XML_DECLARATION_ENCODING = re.compile(rb'^\s*<\?xml\b[^?>]{0,256}\bencoding\s*=\s*["\']([A-Za-z0-9._-]+)["\']')
_XHTML_SCAN_START = re.compile(r"<!--|<!\[CDATA\[|<\?|<!DOCTYPE\b|&([A-Za-z][A-Za-z0-9]{0,31});")


def _decode_xml_bytes(payload: bytes) -> str:
    if payload.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return payload.decode("utf-32")
    if payload.startswith(b"\xef\xbb\xbf"):
        return payload.decode("utf-8-sig")
    if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        return payload.decode("utf-16")
    match = _XML_DECLARATION_ENCODING.match(payload[:300])
    encoding = match.group(1).decode("ascii") if match else "utf-8"
    return payload.decode(encoding).lstrip("\ufeff")


def _find_doctype_end(text: str, start: int) -> int:
    length = len(text)
    index = start
    quote = ""
    in_subset = False
    while index < length:
        char = text[index]
        if quote:
            if char == quote:
                quote = ""
        elif char in ('"', "'"):
            quote = char
        elif in_subset:
            if char == "]":
                in_subset = False
        elif char == "[":
            in_subset = True
        elif char == ">":
            return index + 1
        index += 1
    return -1


def _replace_html_named_entities(text: str) -> str:
    """Single-pass O(n) replacement of standard HTML named entities outside XML constructs."""
    parts: list[str] = []
    pos = 0
    length = len(text)
    while pos < length:
        match = _XHTML_SCAN_START.search(text, pos)
        if match is None:
            parts.append(text[pos:])
            break
        start, end = match.span()
        parts.append(text[pos:start])
        name = match.group(1)
        if name is not None:
            if name in _XML_PREDEFINED_ENTITIES:
                parts.append(match.group(0))
            else:
                codepoint = _HTML_NAMED_ENTITIES.get(name)
                parts.append(f"&#{codepoint};" if codepoint is not None else match.group(0))
            pos = end
            continue
        token = match.group(0)
        if token == "<!--":
            close = text.find("-->", end)
            if close < 0:
                parts.append(text[start:])
                break
            close += 3
        elif token == "<![CDATA[":
            close = text.find("]]>", end)
            if close < 0:
                parts.append(text[start:])
                break
            close += 3
        elif token == "<?":
            close = text.find("?>", end)
            if close < 0:
                parts.append(text[start:])
                break
            close += 2
        else:
            close = _find_doctype_end(text, end)
            if close < 0:
                parts.append(text[start:])
                break
        parts.append(text[start:close])
        pos = close
    return "".join(parts)


def _parse_epub_xml(payload: bytes):
    """Parse EPUB XML/XHTML safely while tolerating standard HTML named entities."""
    try:
        return ET.fromstring(payload)
    except ET.ParseError as error:
        if "undefined entity" not in str(error).lower():
            raise
        decoded = _decode_xml_bytes(payload)
        rewritten = _replace_html_named_entities(decoded)
        if rewritten == decoded:
            raise
        return ET.fromstring(rewritten)


def _read_epub_package(archive):
    infos = archive.infolist()
    if len(infos) > 4096 or sum(item.file_size for item in infos) > MAX_EXPANDED:
        raise ImportError("EPUB 解压后过大或资源过多，当前版本不支持。")
    for info in infos:
        if info.flag_bits & 1 or info.file_size > MAX_FILE or info.file_size > max(1, info.compress_size) * 500:
            raise ImportError("EPUB 加密或压缩比例异常，无法安全读取。")
        if ".." in PurePosixPath(info.filename).parts or info.filename.startswith("/") or "\\" in info.filename:
            raise ImportError("EPUB 含有不安全的资源路径。")
    names = set(archive.namelist())
    if len(names) != len(infos):
        raise ImportError("EPUB 存在重复资源，无法可靠读取。")
    if "META-INF/encryption.xml" in names:
        raise ImportError("此 EPUB 包含加密资源（可能是 DRM 或字体混淆），当前版本不支持。")
    if archive.read("mimetype").strip() != b"application/epub+zip":
        raise ImportError("这不是有效的 EPUB 文件。")
    container = ET.fromstring(archive.read("META-INF/container.xml"))
    rootfile = next(element for element in container.iter() if _tag(element) == "rootfile")
    opf_path = _zip_path("", rootfile.attrib["full-path"])
    opf = ET.fromstring(archive.read(opf_path))
    base = posixpath.dirname(opf_path)
    metadata = next((element for element in opf.iter() if _tag(element) == "metadata"), opf)
    return opf, base, metadata


def _epub_metadata_values(metadata, name):
    values = []
    for node in metadata.iter():
        if _tag(node) != name:
            continue
        value = " ".join(" ".join(node.itertext()).split())
        if value:
            values.append(value[:4000])
    return values


def _extract_epub_cover(archive, opf, base, metadata) -> bytes:
    manifest = {e.attrib["id"]: e.attrib for e in opf.iter() if _tag(e) == "item" and "id" in e.attrib}
    cover_id = next((item["id"] for item in manifest.values() if "cover-image" in item.get("properties", "").split()), "")
    if not cover_id:
        cover_id = next((e.get("content", "") for e in metadata.iter() if _tag(e) == "meta" and e.get("name", "").lower() == "cover"), "")
    cover_item = manifest.get(cover_id)
    if cover_item and cover_item.get("href"):
        try:
            cover_path = _zip_path(base, cover_item["href"])
            cover_info = archive.getinfo(cover_path)
            if cover_info.file_size <= 8 * 1024 * 1024:
                return _validate_image_payload(archive.read(cover_path))
        except (ImportError, KeyError, OSError, ValueError, ET.ParseError):
            return b""
    return b""


def read_epub_cover(file: str | Path) -> bytes:
    """Read only the validated EPUB cover image bytes, or empty bytes if unavailable."""
    path = Path(file)
    if path.suffix.lower() != ".epub":
        return b""
    try:
        if not path.is_file() or path.stat().st_size > MAX_FILE:
            return b""
        with zipfile.ZipFile(path) as archive:
            opf, base, metadata = _read_epub_package(archive)
            return _extract_epub_cover(archive, opf, base, metadata)
    except Exception:
        return b""


def read_epub_title(file: str | Path) -> str | None:
    """Read only the EPUB package title, without parsing its chapters."""
    path = Path(file)
    if path.suffix.lower() != ".epub":
        return None
    try:
        if not path.is_file():
            raise ImportError("文件不存在或无法访问，请重新定位文件。")
        if path.stat().st_size > MAX_FILE:
            raise ImportError("文件超过 32 MiB，当前版本暂不支持。")
        with zipfile.ZipFile(path) as archive:
            _opf, _base, metadata = _read_epub_package(archive)
            title = next(iter(_epub_metadata_values(metadata, "title")), None)
            return title[:MAX_EPUB_TITLE_LENGTH] if title else None
    except ImportError:
        raise
    except Exception:
        raise ImportError("EPUB 结构损坏、内容缺失或 XML 不受支持，无法读取。") from None


def _load_epub(path: Path) -> tuple[str, str, list[Section]]:
    try:
        with zipfile.ZipFile(path) as archive:
            opf, base, metadata = _read_epub_package(archive)

            title = next(iter(_epub_metadata_values(metadata, "title")), path.stem)
            title = title[:MAX_EPUB_TITLE_LENGTH]
            authors = _epub_metadata_values(metadata, "creator")
            author = " · ".join(authors[:20])
            language = next(iter(_epub_metadata_values(metadata, "language")), "")[:100]
            identifiers = [node for node in metadata.iter() if _tag(node) == "identifier" and " ".join(" ".join(node.itertext()).split())]
            unique_id = opf.get("unique-identifier", "")
            identifier_node = next((node for node in identifiers if node.get("id") == unique_id), identifiers[0] if identifiers else None)
            identifier = " ".join(" ".join(identifier_node.itertext()).split())[:500] if identifier_node is not None else ""
            publisher = next(iter(_epub_metadata_values(metadata, "publisher")), "")[:500]
            publication_date = next(iter(_epub_metadata_values(metadata, "date")), "")[:100]
            description = next(iter(_epub_metadata_values(metadata, "description")), "")[:4000]
            manifest = {e.attrib["id"]: e.attrib for e in opf.iter() if _tag(e) == "item" and "id" in e.attrib}
            cover = _extract_epub_cover(archive, opf, base, metadata)
            navigation = {"toc": [], "page_list": [], "landmarks": []}
            for item in manifest.values():
                media_type = item.get("media-type")
                is_ncx = media_type == "application/x-dtbncx+xml"
                if "nav" in item.get("properties", "").split() or is_ncx:
                    nav_path = _zip_path(base, item["href"])
                    nav = _parse_epub_xml(archive.read(nav_path))
                    parsed = _parse_ncx_navigation(nav, nav_path) if is_ncx else _parse_epub3_navigation(nav, nav_path)
                    pending = list(entry for entries in parsed.values() for entry in entries)
                    nav_count = 0
                    while pending:
                        current = pending.pop()
                        nav_count += 1
                        pending.extend(current.children)
                    if nav_count > MAX_EPUB_NAV_ENTRIES:
                        raise ImportError("EPUB 目录项目数量超过当前支持范围。")
                    for kind, entries in parsed.items():
                        if entries and (is_ncx is False or not navigation[kind]):
                            navigation[kind] = entries
            sections = []
            content_pages = []
            image_cache = {}
            image_count = 0
            for ref in (e for e in opf.iter() if _tag(e) == "itemref" and e.get("linear", "yes") != "no"):
                item = manifest[ref.attrib["idref"]]
                if item.get("media-type") not in ("application/xhtml+xml", "text/html"):
                    raise ImportError("EPUB 的阅读顺序包含不支持的内容类型。")
                item_path = _zip_path(base, item["href"])
                # XML decoding respects the document's declared encoding and rejects custom entities.
                element = _parse_epub_xml(archive.read(item_path))
                # Strip XML namespaces before the text-only HTML parser sees tags.
                for node in element.iter():
                    node.tag = _tag(node)
                pictures = []
                for node in element.iter():
                    source = None
                    if node.tag == "img":
                        source = node.get("src")
                    elif node.tag == "svg":
                        embedded = next((child for child in node.iter() if child.tag == "image"), None)
                        if embedded is not None:
                            source = embedded.get("href") or embedded.get("{http://www.w3.org/1999/xlink}href")
                    if source:
                        image_path = _zip_path(posixpath.dirname(item_path), source)
                        image_count += 1
                        if image_count > MAX_EPUB_IMAGE_REFERENCES:
                            raise ImportError("EPUB 插图数量超过当前支持范围。")
                        payload = image_cache.get(image_path)
                        if payload is None:
                            payload = _validate_image_payload(archive.read(image_path))
                            image_cache[image_path] = payload
                        pictures.append(payload)
                        tail = node.tail
                        attributes = dict(node.attrib)
                        node.clear()
                        node.tag = "div"
                        node.attrib.update(attributes)
                        node.text, node.tail = "\ufffc", tail
                parser = TextExtractor()
                parser.feed(ET.tostring(element, encoding="unicode"))
                parser.close()
                raw_text = "".join(parser.parts)
                source_offsets = list(parser.anchors.values())
                for start, end, _ in parser.links:
                    source_offsets.extend((start, end))
                for item in parser.formats:
                    source_offsets.extend((item.start, item.end))
                source_offsets.extend(page["offset"] for page in parser.pagebreaks)
                text, normalized = _normalize_with_positions(raw_text, source_offsets)
                if text:
                    nav_titles = (entry for root_entry in navigation["toc"] for entry in _walk_navigation(root_entry))
                    chapter_title = next((entry.label for entry in nav_titles if entry.path == item_path and entry.label), "".join(parser.heading).strip()) or f"章节 {len(sections) + 1}"
                    positions = [i for i, char in enumerate(text) if char == "\ufffc"]
                    if len(positions) != len(pictures):
                        raise ImportError("EPUB 插图结构不受支持。")
                    images = dict(zip(positions, pictures))
                    anchors = {name: normalized[offset] for name, offset in parser.anchors.items()}
                    links = []
                    for start, end, href in parser.links:
                        target = _navigation_target(item_path, href)
                        if target and normalized[end] > normalized[start]:
                            links.append(EPUBLink(normalized[start], normalized[end], *target))
                    formats = [
                        EPUBFormatRange(normalized[item.start], normalized[item.end], item.kind, item.value)
                        for item in parser.formats if normalized[item.end] > normalized[item.start]
                    ]
                    for number, page in enumerate(parser.pagebreaks, 1):
                        content_pages.append(NavigationEntry(
                            page["label"] or f"{number}", item_path, page["fragment"], source_offset=normalized[page["offset"]]
                        ))
                    chunks = split_section("epub:" + item_path, chapter_title[:160], text)
                    start = 0
                    for chunk_index, chunk in enumerate(chunks):
                        end = start + len(chunk.text)
                        chunk.images = {offset - start: data for offset, data in images.items() if start <= offset < start + len(chunk.text)}
                        chunk.source_path = item_path
                        chunk.anchors = {
                            name: offset - start for name, offset in anchors.items()
                            if start <= offset < end or chunk_index == len(chunks) - 1 and offset == end
                        }
                        chunk.links = [
                            EPUBLink(max(start, link.start) - start, min(end, link.end) - start, link.path, link.fragment)
                            for link in links if link.start < end and link.end > start
                        ]
                        chunk.formats = [
                            EPUBFormatRange(max(start, item.start) - start, min(end, item.end) - start, item.kind, item.value)
                            for item in formats if item.start < end and item.end > start
                        ]
                        start = end
                    sections.extend(chunks)
            if not sections:
                raise ImportError("EPUB 中没有可阅读的文字章节。")
            if not navigation["page_list"]:
                navigation["page_list"] = content_pages
            book_metadata = {"language": language, "identifier": identifier, "publisher": publisher, "publication_date": publication_date, "description": description, "cover": cover}
            return title[:300], author[:300], sections, navigation, book_metadata
    except ImportError:
        raise
    except Exception:
        raise ImportError("EPUB 结构损坏、内容缺失或 XML 不受支持，无法读取。") from None


def load_book(file: str | Path, encoding="auto") -> Book:
    path = Path(file)
    try:
        if path.suffix.lower() not in (".txt", ".epub"):
            raise ImportError("请选择 TXT 或未加密的 EPUB 文件。")
        if not path.is_file():
            raise ImportError("文件不存在或无法访问，请重新定位文件。")
        size = path.stat().st_size
        if size > MAX_FILE:
            raise ImportError("文件超过 32 MiB，当前版本暂不支持。")
        if size == 0:
            raise ImportError("这个文件是空的。")
        path = path.resolve()
        book_id = sha256(str(path).casefold().encode("utf-8")).hexdigest()[:24]
        if path.suffix.lower() == ".txt":
            with path.open("rb") as stream:
                data = stream.read(MAX_FILE + 1)
            if len(data) > MAX_FILE:
                raise ImportError("文件超过 32 MiB，当前版本暂不支持。")
            text, detected = decode_text(data, encoding)
            return Book(book_id, str(path), path.stem, sections=parse_txt(text), encoding=detected)
        title, author, sections, navigation, metadata = _load_epub(path)
        return Book(book_id, str(path), title, author, sections, "EPUB", navigation["toc"], navigation["page_list"], navigation["landmarks"], **metadata)
    except ImportError:
        raise
    except (OSError, ValueError):
        raise ImportError("读取文件失败，请检查权限，或重新定位文件。") from None
