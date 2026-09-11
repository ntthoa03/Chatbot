"""Load uploaded PDF, Word, Excel, CSV and image files into knowledge chunks.

Tabular files are chunked by complete rows with the header repeated, rather
than being flattened and split as ordinary prose. OCR-derived content is
explicitly blocked from indexing until a human has reviewed it.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import sys
import unicodedata
import zipfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid5
from xml.etree import ElementTree as ET

from ai_core.models import KnowledgeChunk
from ai_core.vision_ocr import VisionOcrError, extract_text_with_vision, get_last_ocr_audit


DocumentType = Literal["service", "pricing", "policy", "faq", "blog"]
OcrCallable = Callable[[Any], str]
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}
SUPPORTED_SUFFIXES = {".pdf", ".docx", ".xlsx", ".csv", *IMAGE_SUFFIXES}


class DocumentLoadError(ValueError):
    """Raised when a document is unsupported, unsafe or cannot be extracted."""


@dataclass(frozen=True)
class ExtractedSection:
    locator: str
    title: str
    blocks: tuple[str, ...]
    is_table: bool = False
    used_ocr: bool = False


@dataclass(frozen=True)
class DocumentLoadResult:
    path: str
    file_type: str
    chunks: tuple[dict[str, Any], ...]
    used_ocr: bool
    requires_manual_review: bool
    ready_for_index: bool
    ocr_audit: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()

    def manifest(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "file_type": self.file_type,
            "chunk_count": len(self.chunks),
            "used_ocr": self.used_ocr,
            "requires_manual_review": self.requires_manual_review,
            "ready_for_index": self.ready_for_index,
            "ocr_audit": list(self.ocr_audit),
            "warnings": list(self.warnings),
        }


def _clean_text(value: Any) -> str:
    return re.sub(r"[ \t]+", " ", re.sub(r"\r\n?", "\n", str(value))).strip()


def _validate_path(path: Path) -> str:
    if not path.is_file():
        raise DocumentLoadError(f"Không tìm thấy tài liệu: {path}")
    suffix = path.suffix.casefold()
    if suffix not in SUPPORTED_SUFFIXES:
        raise DocumentLoadError(
            f"Định dạng {suffix or '(không có đuôi)'} chưa hỗ trợ; "
            "dùng PDF, DOCX, XLSX, CSV hoặc ảnh."
        )
    if path.stat().st_size > MAX_FILE_BYTES:
        raise DocumentLoadError(f"Tài liệu vượt giới hạn {MAX_FILE_BYTES // 1024 // 1024} MB.")
    return suffix


def _safe_zip(path: Path) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise DocumentLoadError(f"File Office không hợp lệ: {path.name}") from exc
    total = sum(item.file_size for item in archive.infolist())
    unsafe = any(
        name.startswith(("/", "\\")) or ".." in Path(name).parts
        for name in (item.filename for item in archive.infolist())
    )
    if unsafe or total > MAX_UNCOMPRESSED_BYTES:
        archive.close()
        raise DocumentLoadError("File Office không an toàn hoặc giải nén vượt giới hạn.")
    return archive


def _ocr_image(path: Path, ocr: OcrCallable) -> list[ExtractedSection]:
    try:
        from PIL import Image
        with Image.open(path) as image:
            image.load()
            text = _clean_text(ocr(image))
    except DocumentLoadError:
        raise
    except Exception as exc:
        raise DocumentLoadError(f"Không đọc được ảnh {path.name}: {exc}") from exc
    if not text:
        raise DocumentLoadError(f"OCR không trích được chữ từ {path.name}.")
    return [ExtractedSection(
        "image", path.stem, (text,), is_table=_looks_like_markdown_table(text), used_ocr=True,
    )]


def _looks_like_markdown_table(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    pipe_rows = [line for line in lines if "|" in line]
    if len(pipe_rows) >= 2 and len({line.count("|") for line in pipe_rows}) == 1:
        return True
    return len(lines) >= 2 and "|" in lines[0] and bool(
        re.fullmatch(r"\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?", lines[1])
    )


def _pdf_sections(path: Path, ocr: OcrCallable) -> list[ExtractedSection]:
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
    except Exception as exc:
        raise DocumentLoadError(f"Không đọc được PDF {path.name}: {exc}") from exc
    sections: list[ExtractedSection] = []
    scanned_pages: list[int] = []
    for index, page in enumerate(reader.pages):
        text = _clean_text(page.extract_text() or "")
        if len(re.sub(r"\s", "", text)) >= 20:
            sections.append(ExtractedSection(f"page-{index + 1}", f"Trang {index + 1}", (text,)))
        else:
            scanned_pages.append(index)
    if scanned_pages:
        try:
            import pymupdf
            from PIL import Image
            document = pymupdf.open(path)
            for index in scanned_pages:
                pixmap = document[index].get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
                image = Image.open(io.BytesIO(pixmap.tobytes("png")))
                text = _clean_text(ocr(image))
                if not text:
                    raise DocumentLoadError(f"OCR PDF trang {index + 1} không có kết quả.")
                sections.append(ExtractedSection(
                    f"page-{index + 1}", f"Trang {index + 1}", (text,),
                    is_table=_looks_like_markdown_table(text), used_ocr=True,
                ))
            document.close()
        except DocumentLoadError:
            raise
        except ImportError as exc:
            raise DocumentLoadError("PDF scan cần PyMuPDF và Pillow để chạy OCR.") from exc
        except Exception as exc:
            raise DocumentLoadError(f"Không OCR được PDF scan {path.name}: {exc}") from exc
    if not sections:
        raise DocumentLoadError(f"PDF {path.name} không có nội dung đọc được.")
    return sorted(sections, key=lambda item: int(item.locator.split("-")[-1]))


def _xml_text(element: ET.Element) -> str:
    return _clean_text("".join(node.text or "" for node in element.iter() if node.tag.endswith("}t")))


def _docx_sections(path: Path) -> list[ExtractedSection]:
    with _safe_zip(path) as archive:
        try:
            root = ET.fromstring(archive.read("word/document.xml"))
        except (KeyError, ET.ParseError) as exc:
            raise DocumentLoadError(f"DOCX thiếu document.xml hợp lệ: {path.name}") from exc
    body = next((node for node in root.iter() if node.tag.endswith("}body")), None)
    if body is None:
        raise DocumentLoadError(f"DOCX không có nội dung: {path.name}")
    sections: list[ExtractedSection] = []
    prose: list[str] = []
    table_number = 0
    for child in body:
        if child.tag.endswith("}p"):
            text = _xml_text(child)
            if text:
                prose.append(text)
        elif child.tag.endswith("}tbl"):
            if prose:
                sections.append(ExtractedSection("document", path.stem, tuple(prose)))
                prose = []
            rows = [
                [_xml_text(cell) for cell in row if cell.tag.endswith("}tc")]
                for row in child
                if row.tag.endswith("}tr")
            ]
            rows = [row for row in rows if any(row)]
            if rows:
                table_number += 1
                sections.extend(_table_sections(rows, f"table-{table_number}", f"Bảng {table_number}"))
    if prose:
        sections.append(ExtractedSection("document", path.stem, tuple(prose)))
    if not sections:
        raise DocumentLoadError(f"DOCX không có nội dung đọc được: {path.name}")
    return sections


def _cell_value(cell: ET.Element, shared: Sequence[str], ns: dict[str, str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return _xml_text(cell)
    value = cell.find("m:v", ns)
    if value is None or value.text is None:
        return ""
    if cell_type == "s":
        try:
            return shared[int(value.text)]
        except (ValueError, IndexError):
            return value.text
    if cell_type == "b":
        return "TRUE" if value.text == "1" else "FALSE"
    return value.text


def _markdown_cell(value: Any) -> str:
    """Render one value without allowing it to break a Markdown table."""

    return _clean_text(value).replace("|", r"\|").replace("\n", "<br>")


def _table_sections(
    rows: Sequence[Sequence[str]], locator: str, title: str, *, target_chars: int = 1800,
) -> list[ExtractedSection]:
    width = max((len(row) for row in rows), default=0)
    normalized = [[_markdown_cell(value) for value in row] + [""] * (width - len(row)) for row in rows]
    normalized = [row for row in normalized if any(row)]
    if not normalized:
        return []
    header = normalized[0]
    if not any(header):
        header = [f"Cột {index + 1}" for index in range(width)]
    rendered_header = "| " + " | ".join(header) + " |"
    separator = "| " + " | ".join("---" for _ in header) + " |"
    groups: list[list[str]] = []
    current = [rendered_header, separator]
    for row in normalized[1:] or normalized[:1]:
        rendered = "| " + " | ".join(row) + " |"
        if len("\n".join((*current, rendered))) > target_chars and len(current) > 2:
            groups.append(current)
            current = [rendered_header, separator]
        current.append(rendered)
    if len(current) > 2:
        groups.append(current)
    return [
        ExtractedSection(f"{locator}-part-{index}", title, ("\n".join(group),), is_table=True)
        for index, group in enumerate(groups, start=1)
    ]


def _xlsx_number_formats(archive: zipfile.ZipFile, ns: dict[str, str]) -> list[str]:
    """Return the Excel number format for every cell style index.

    The loader intentionally reads OOXML directly, so production does not
    require Excel/openpyxl. Only display-safe formats that matter to knowledge
    documents (money, percent and dates) are interpreted below.
    """

    try:
        root = ET.fromstring(archive.read("xl/styles.xml"))
    except (KeyError, ET.ParseError):
        return []
    custom = {
        int(item.attrib["numFmtId"]): item.attrib.get("formatCode", "General")
        for item in root.findall("m:numFmts/m:numFmt", ns)
        if item.attrib.get("numFmtId", "").isdigit()
    }
    built_in = {
        0: "General", 1: "0", 2: "0.00", 9: "0%", 10: "0.00%",
        14: "mm-dd-yy", 15: "d-mmm-yy", 16: "d-mmm", 17: "mmm-yy",
        18: "h:mm AM/PM", 19: "h:mm:ss AM/PM", 20: "h:mm", 21: "h:mm:ss",
        22: "m/d/yy h:mm", 37: "#,##0", 38: "#,##0;[Red]-#,##0",
        39: "#,##0.00", 40: "#,##0.00;[Red]-#,##0.00",
    }
    return [
        custom.get(int(xf.attrib.get("numFmtId", "0")), built_in.get(int(xf.attrib.get("numFmtId", "0")), "General"))
        for xf in root.findall("m:cellXfs/m:xf", ns)
    ]


def _xlsx_display_number(raw: str, format_code: str) -> str:
    try:
        number = float(raw)
    except ValueError:
        return raw
    code = format_code.split(";")[0]
    code_plain = re.sub(r'"[^"]*"|\\.|\[[^]]*\]|_.|\*.', "", code)
    code_lower = code_plain.casefold()
    if "%" in code_plain:
        decimals = len(code_plain.split(".", 1)[1].split("%", 1)[0]) if "." in code_plain else 0
        return f"{number * 100:.{decimals}f}%"
    if any(token in code_lower for token in ("yy", "dd", "mmm")):
        # Excel's 1900 date system includes the historical leap-year bug.
        from datetime import timedelta
        converted = datetime(1899, 12, 30) + timedelta(days=number)
        if any(token in code_lower for token in ("h", "s")):
            return converted.strftime("%Y-%m-%d %H:%M:%S").rstrip(":00")
        return converted.date().isoformat()
    if not math.isfinite(number) or code in ("", "General", "@"):
        return raw
    decimals = 0
    decimal_match = re.search(r"[.,]([0#]+)", code_plain.replace("#,##", "###"))
    if decimal_match:
        decimals = len(decimal_match.group(1))
    rendered = f"{number:,.{decimals}f}" if "," in code else f"{number:.{decimals}f}"
    literals = "".join(
        escaped or quoted for escaped, quoted in re.findall(r'\\(.)|"([^"]*)"', code)
    )
    return f"{rendered}{literals}"


def _xlsx_cell_value(
    cell: ET.Element, shared: Sequence[str], ns: dict[str, str], formats: Sequence[str],
) -> str:
    value = _cell_value(cell, shared, ns)
    formula = cell.find("m:f", ns)
    if not value and formula is not None and formula.text:
        return f"={formula.text}"
    if cell.attrib.get("t") in (None, "n") and value:
        try:
            style_index = int(cell.attrib.get("s", "0"))
        except ValueError:
            style_index = 0
        if 0 <= style_index < len(formats):
            return _xlsx_display_number(value, formats[style_index])
    return value


def _occupied_run(row: Sequence[str]) -> tuple[int, int] | None:
    """Choose the widest contiguous non-empty run (the likely table header)."""

    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate((*row, "")):
        if value and start is None:
            start = index
        elif not value and start is not None:
            runs.append((start, index))
            start = None
    return max(runs, key=lambda item: item[1] - item[0], default=None)


def _xlsx_group_sections(
    indexed_rows: Sequence[tuple[int, list[str]]], locator: str, sheet_name: str,
) -> list[ExtractedSection]:
    """Split one blank-line-delimited region into prose, table and side notes."""

    rows = [values for _, values in indexed_rows]
    header_index: int | None = None
    table_run: tuple[int, int] | None = None
    for index, row in enumerate(rows):
        run = _occupied_run(row)
        if run and run[1] - run[0] >= 2 and any(
            any(next_row[column:run[1]])
            for next_row in rows[index + 1:index + 3]
            for column in [run[0]]
        ):
            header_index, table_run = index, run
            break
    if header_index is None or table_run is None:
        prose = tuple(" | ".join(value for value in row if value) for row in rows)
        return [ExtractedSection(locator, sheet_name, prose)] if any(prose) else []

    start, end = table_run
    prefix = [" | ".join(value for value in row if value) for row in rows[:header_index]]
    caption = prefix[-1] if prefix else sheet_name
    output: list[ExtractedSection] = []
    if prefix[:-1]:
        output.append(ExtractedSection(f"{locator}-context", sheet_name, tuple(prefix[:-1])))
    table_rows = [row[start:end] for row in rows[header_index:]]
    table_sections = _table_sections(table_rows, f"{locator}-table", caption)
    # Keep the logical table shape clean, but never discard notes stored in
    # columns separated from the table by an empty spacer column.
    notes: list[str] = []
    for (row_number, row) in indexed_rows[header_index:]:
        for column, value in enumerate(row):
            if value and not start <= column < end:
                notes.append(f"Ô {chr(65 + column) if column < 26 else column + 1}{row_number}: {value}")
    if caption and table_sections:
        table_sections = [
            ExtractedSection(item.locator, caption, (f"### {caption}\n{item.blocks[0]}",), True)
            for item in table_sections
        ]
    output.extend(table_sections)
    if notes:
        output.append(ExtractedSection(f"{locator}-notes", f"{caption} — Ghi chú", tuple(notes)))
    return output


def _xlsx_sections(path: Path) -> list[ExtractedSection]:
    ns = {
        "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    }
    with _safe_zip(path) as archive:
        try:
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        except (KeyError, ET.ParseError) as exc:
            raise DocumentLoadError(f"XLSX thiếu cấu trúc workbook hợp lệ: {path.name}") from exc
        try:
            shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = [_xml_text(item) for item in shared_root.findall("m:si", ns)]
        except KeyError:
            shared = []
        formats = _xlsx_number_formats(archive, ns)
        rel_map = {item.attrib["Id"]: item.attrib["Target"] for item in relationships}
        sections: list[ExtractedSection] = []
        for sheet in workbook.findall("m:sheets/m:sheet", ns):
            name = sheet.attrib.get("name", "Sheet")
            rel_id = sheet.attrib.get(f"{{{ns['r']}}}id", "")
            target = rel_map.get(rel_id, "")
            if target.startswith("/"):
                target = target.lstrip("/")
            elif not target.startswith("xl/"):
                target = f"xl/{target}"
            try:
                root = ET.fromstring(archive.read(target))
            except (KeyError, ET.ParseError):
                continue
            indexed_rows: list[tuple[int, list[str]]] = []
            for row in root.findall(".//m:sheetData/m:row", ns):
                values: dict[int, str] = {}
                for cell in row.findall("m:c", ns):
                    ref = cell.attrib.get("r", "A1")
                    letters = re.match(r"[A-Z]+", ref.upper())
                    column = 0
                    for char in letters.group(0) if letters else "A":
                        column = column * 26 + ord(char) - 64
                    values[column - 1] = _xlsx_cell_value(cell, shared, ns, formats)
                # Formatting may materialize visually blank rows/cells in OOXML.
                # Treat them as real separators, otherwise two independent
                # tables separated by a styled blank row are accidentally merged.
                if values and any(value for value in values.values()):
                    indexed_rows.append((int(row.attrib.get("r", len(indexed_rows) + 1)), [
                        values.get(index, "") for index in range(max(values) + 1)
                    ]))
            groups: list[list[tuple[int, list[str]]]] = []
            current: list[tuple[int, list[str]]] = []
            previous_row: int | None = None
            for indexed_row in indexed_rows:
                if previous_row is not None and indexed_row[0] > previous_row + 1 and current:
                    groups.append(current)
                    current = []
                current.append(indexed_row)
                previous_row = indexed_row[0]
            if current:
                groups.append(current)
            for number, group in enumerate(groups, start=1):
                sections.extend(_xlsx_group_sections(group, f"sheet-{name}-region-{number}", name))
    if not sections:
        raise DocumentLoadError(f"XLSX không có bảng dữ liệu đọc được: {path.name}")
    return sections


def _csv_sections(path: Path) -> list[ExtractedSection]:
    raw = path.read_bytes()
    text = None
    for encoding in ("utf-8-sig", "utf-8", "cp1258"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise DocumentLoadError(f"Không nhận diện được encoding CSV: {path.name}")
    # Detect the delimiter from the header record.  Looking at an arbitrary
    # 4-KiB sample lets commas inside quoted, multi-line descriptions outvote
    # the real delimiter (a common pattern in semicolon-separated exports).
    header_record = text.splitlines()[0] if text.splitlines() else text
    try:
        dialect = csv.Sniffer().sniff(header_record, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [[_clean_text(value) for value in row] for row in csv.reader(io.StringIO(text), dialect)]
    sections = _table_sections(rows, "csv", path.stem)
    if not sections:
        raise DocumentLoadError(f"CSV không có dữ liệu: {path.name}")
    return sections


def _prose_chunks(blocks: Iterable[str], target_chars: int, overlap_chars: int) -> list[str]:
    if target_chars < 200 or overlap_chars < 0 or overlap_chars >= target_chars:
        raise DocumentLoadError("chunk_size phải >= 200 và overlap nằm trong [0, chunk_size).")
    text = "\n".join(value for value in (_clean_text(item) for item in blocks) if value)
    if not text:
        return []
    output: list[str] = []
    start = 0
    while len(text) - start > target_chars:
        hard_end = start + target_chars
        end = max(text.rfind("\n", start, hard_end + 1), text.rfind(" ", start, hard_end + 1))
        if end <= start + target_chars // 2:
            end = hard_end
        chunk = text[start:end].strip()
        if chunk:
            output.append(chunk)
        next_start = max(end - overlap_chars, start + 1)
        # Begin at a complete word. The overlap may be slightly smaller than
        # requested, but a retrieval chunk never starts halfway through a word.
        if next_start > 0 and not text[next_start - 1].isspace():
            boundary = text.find(" ", next_start, end)
            newline = text.find("\n", next_start, end)
            candidates = [value for value in (boundary, newline) if value != -1]
            next_start = min(candidates) + 1 if candidates else end
        start = next_start
    tail = text[start:].strip()
    if tail:
        output.append(tail)
    return output


def _infer_document_type(path: Path, sections: Sequence[ExtractedSection]) -> DocumentType:
    sample = " ".join([path.stem, *(block for section in sections for block in section.blocks[:2])]).casefold()
    normalized = "".join(
        char for char in unicodedata.normalize("NFD", sample) if unicodedata.category(char) != "Mn"
    ).replace("đ", "d")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    if any(token in normalized for token in ("chinh sach", "dieu khoan", "policy")):
        return "policy"
    if any(token in normalized for token in ("bang gia", "don gia", "gia ban", "price", "chi phi")):
        return "pricing"
    if any(token in normalized for token in ("faq", "hoi dap", "cau hoi")):
        return "faq"
    return "service"


def _source_url(tenant_id: str, path: Path, locator: str) -> str:
    return f"https://upload.local/{quote(tenant_id)}/{quote(path.name)}#{quote(locator)}"


def load_document(
    path: str | Path,
    tenant_id: str,
    *,
    document_type: DocumentType | None = None,
    chunk_size: int = 900,
    overlap_chars: int = 120,
    ocr: OcrCallable | None = None,
    ocr_reviewed: bool = False,
) -> DocumentLoadResult:
    """Extract one document and normalize it to the existing chunk contract."""

    source = Path(path).resolve()
    suffix = _validate_path(source)
    clean_tenant = tenant_id.strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", clean_tenant):
        raise DocumentLoadError("tenant_id không hợp lệ.")

    ocr_audit: list[dict[str, Any]] = []

    def model_ocr(image: Any) -> str:
        try:
            text = extract_text_with_vision(image, clean_tenant)
            audit = get_last_ocr_audit()
            if audit:
                ocr_audit.append(audit)
            return text
        except VisionOcrError as exc:
            raise DocumentLoadError(str(exc)) from exc

    ocr_fn = ocr or model_ocr
    if suffix == ".pdf":
        sections = _pdf_sections(source, ocr_fn)
    elif suffix == ".docx":
        sections = _docx_sections(source)
    elif suffix == ".xlsx":
        sections = _xlsx_sections(source)
    elif suffix == ".csv":
        sections = _csv_sections(source)
    else:
        sections = _ocr_image(source, ocr_fn)
    resolved_type = document_type or _infer_document_type(source, sections)
    updated_at = datetime.fromtimestamp(source.stat().st_mtime, timezone.utc).date().isoformat()
    used_ocr = any(section.used_ocr for section in sections)
    if used_ocr and ocr is not None and not ocr_audit:
        ocr_text = "\n".join(
            block for section in sections if section.used_ocr for block in section.blocks
        )
        unreadable = ocr_text.count("[KHÔNG ĐỌC ĐƯỢC]")
        lines = max(1, len([line for line in ocr_text.splitlines() if line.strip()]))
        ocr_audit.append({
            "provider": "custom",
            "model": "injected_ocr_callable",
            "started_at": None,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "latency_ms": None,
            "confidence": round(max(0.0, 1.0 - unreadable / lines), 4),
            "confidence_method": "local_ocr_quality_estimate",
            "tokens_in": 0,
            "tokens_out": 0,
            "usage_source": "custom_callable",
            "cost_usd": 0.0,
            "cost_method": "custom_callable_no_external_cost",
        })
    chunks: list[dict[str, Any]] = []
    for section in sections:
        contents = (
            list(section.blocks)
            if section.is_table
            else _prose_chunks(section.blocks, chunk_size, overlap_chars)
        )
        for index, content in enumerate(contents, start=1):
            locator = f"{section.locator}-chunk-{index}"
            stable_key = f"{clean_tenant}|{source.name}|{locator}|{content}"
            chunk = {
                "tenant_id": clean_tenant,
                "chunk_id": str(uuid5(NAMESPACE_URL, stable_key)),
                "content": content,
                "metadata": {
                    "url": _source_url(clean_tenant, source, locator),
                    "title": f"{source.stem} — {section.title}",
                    "type": resolved_type,
                    "updated_at": updated_at,
                    "source": "document",
                    "source_priority": 80,
                    "source_confidence": 0.85 if section.used_ocr else 1.0,
                },
            }
            chunks.append(KnowledgeChunk.model_validate(chunk).model_dump(mode="json"))
    if not chunks:
        raise DocumentLoadError(f"Không tạo được chunk từ {source.name}.")
    requires_review = used_ocr and not ocr_reviewed
    warnings = (
        ("Nội dung OCR tiếng Việt phải được người kiểm tra trước khi đánh index.",)
        if requires_review else ()
    )
    return DocumentLoadResult(
        path=str(source),
        file_type=suffix.lstrip("."),
        chunks=tuple(chunks),
        used_ocr=used_ocr,
        requires_manual_review=requires_review,
        ready_for_index=not requires_review,
        ocr_audit=tuple(ocr_audit),
        warnings=warnings,
    )


def load_documents(
    paths: Sequence[str | Path], tenant_id: str, **kwargs: Any,
) -> tuple[DocumentLoadResult, ...]:
    if not paths:
        raise DocumentLoadError("Cần ít nhất một tài liệu.")
    return tuple(load_document(path, tenant_id, **kwargs) for path in paths)


def _write_outputs(results: Sequence[DocumentLoadResult], output: Path, manifest: Path) -> None:
    all_chunks = [chunk for result in results for chunk in result.chunks]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ready_for_index": all(result.ready_for_index for result in results),
        "documents": [result.manifest() for result in results],
        "chunk_count": len(all_chunks),
        "ocr": {
            "call_count": sum(len(result.ocr_audit) for result in results),
            "calls": [audit for result in results for audit in result.ocr_audit],
            "cost_usd": round(sum(
                float(audit.get("cost_usd") or 0)
                for result in results for audit in result.ocr_audit
            ), 12),
        },
    }
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if not payload["ready_for_index"]:
        review_path = output.with_suffix(".ocr-review.json")
        review_path.parent.mkdir(parents=True, exist_ok=True)
        review_payload = {
            "schema_version": "h4-04.ocr-review.v1",
            "generated_at": payload["generated_at"],
            "tenant_ids": sorted({chunk["tenant_id"] for chunk in all_chunks}),
            "review_status": "pending",
            "reviewer": None,
            "reviewed_at": None,
            "chunks": all_chunks,
        }
        review_path.write_text(
            json.dumps(review_payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        raise DocumentLoadError(
            f"OCR chưa được duyệt; bản nháp ở {review_path}. "
            "Kiểm tra rồi promote file review; không chạy OCR lần hai."
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(all_chunks, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Nạp tài liệu upload thành KnowledgeChunk H4-04")
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--type", choices=["service", "pricing", "policy", "faq", "blog"])
    parser.add_argument("--chunk-size", type=int, default=900)
    parser.add_argument("--chunk-overlap", type=int, default=120)
    parser.add_argument(
        "--ocr-reviewed", action="store_true",
        help="Đã ngừng dùng ở CLI; hãy promote file *.ocr-review.json để không OCR lại.",
    )
    args = parser.parse_args(argv)
    if args.ocr_reviewed:
        parser.error(
            "--ocr-reviewed sẽ OCR lại tài liệu. Hãy dùng: "
            "python -m ingestion.ocr_review <file.ocr-review.json> "
            "--output <chunks.json> --reviewer <ten> --confirm-reviewed"
        )
    manifest = args.manifest or args.output.with_suffix(".manifest.json")
    try:
        results = load_documents(
            args.inputs,
            args.tenant_id,
            document_type=args.type,
            chunk_size=args.chunk_size,
            overlap_chars=args.chunk_overlap,
            ocr_reviewed=False,
        )
        _write_outputs(results, args.output, manifest)
    except DocumentLoadError as exc:
        parser.exit(2, f"H4-04: {exc}\n")
    print(f"Đã nạp {sum(len(item.chunks) for item in results)} chunks từ {len(results)} tài liệu.")
    print(args.output)
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
