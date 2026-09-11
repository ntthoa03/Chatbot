from __future__ import annotations

import json
import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from pypdf import PdfWriter

from ingestion.document_loader import (
    DocumentLoadError,
    _prose_chunks,
    _write_outputs,
    load_document,
    load_documents,
)
from ingestion.ocr_review import OcrReviewError, promote_review
from index_chunks import load_chunks


def _write_text_pdf(path: Path, text: str) -> None:
    stream = f"BT /F1 14 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, value in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode() + value + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects)+1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    path.write_bytes(output)


def _write_docx(path: Path) -> None:
    xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
<w:p><w:r><w:t>Dịch vụ thiết kế website doanh nghiệp.</w:t></w:r></w:p>
<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Gói</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Giá</w:t></w:r></w:p></w:tc></w:tr>
<w:tr><w:tc><w:p><w:r><w:t>Basic</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>2.000.000đ</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
</w:body></w:document>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)


def _write_xlsx(path: Path) -> None:
    workbook = """<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Bảng giá" sheetId="1" r:id="rId1"/></sheets></workbook>"""
    rels = """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>"""
    shared = """<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="4" uniqueCount="4"><si><t>Gói</t></si><si><t>Giá</t></si><si><t>Basic</t></si><si><t>2.000.000đ</t></si></sst>"""
    sheet = """<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row><row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2" t="s"><v>3</v></c></row></sheetData></worksheet>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", rels)
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)


def _write_complex_xlsx(path: Path) -> None:
    workbook = """<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Bảng giá" sheetId="1" r:id="rId1"/><sheet name="Điều khoản" sheetId="2" r:id="rId2"/></sheets></workbook>"""
    rels = """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/></Relationships>"""
    values = [
        "CÔNG TY MIMA", "BẢNG 1 — GÓI SEO", "Gói", "Giá / tháng", "Basic",
        "* Giá chưa gồm VAT", "BẢNG 2 — PHỤ PHÍ", "Ngành", "Phụ phí",
        "Nha khoa", "+20%", "ĐIỀU KHOẢN", "Thanh toán trước ngày 05",
    ]
    shared = (
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        + "".join(f"<si><t>{value}</t></si>" for value in values)
        + "</sst>"
    )
    styles = """<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="1"><numFmt numFmtId="165" formatCode="#,##0\\đ"/></numFmts><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="165"/></cellXfs></styleSheet>"""
    sheet1 = """<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
<row r="1"><c r="A1" t="s"><v>0</v></c></row>
<row r="3"><c r="A3" t="s"><v>1</v></c></row>
<row r="4"><c r="A4" t="s"><v>2</v></c><c r="B4" t="s"><v>3</v></c><c r="D4" t="s"><v>5</v></c></row>
<row r="5"><c r="A5" t="s"><v>4</v></c><c r="B5" s="1" t="n"><v>4000000</v></c><c r="D5" t="s"><v>5</v></c></row>
<row r="6"><c r="A6" s="1"/><c r="B6" s="1"/><c r="C6" s="1"/></row>
<row r="7"><c r="A7" t="s"><v>6</v></c></row>
<row r="8"><c r="A8" t="s"><v>7</v></c><c r="B8" t="s"><v>8</v></c></row>
<row r="9"><c r="A9" t="s"><v>9</v></c><c r="B9" t="s"><v>10</v></c></row>
</sheetData><mergeCells count="2"><mergeCell ref="A3:B3"/><mergeCell ref="A7:B7"/></mergeCells></worksheet>"""
    sheet2 = """<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>11</v></c></row><row r="2"><c r="A2" t="s"><v>12</v></c></row></sheetData></worksheet>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", rels)
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/styles.xml", styles)
        archive.writestr("xl/worksheets/sheet1.xml", sheet1)
        archive.writestr("xl/worksheets/sheet2.xml", sheet2)


class H404DocumentLoaderTests(unittest.TestCase):
    @patch("ingestion.document_loader.extract_text_with_vision")
    def test_default_image_route_uses_model_api_and_preserves_vision_table(self, vision_ocr) -> None:
        vision_ocr.return_value = "| Gói | Giá |\n| --- | --- |\n| Basic | 2.000.000đ |"
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "bang-gia.png"
            Image.new("RGB", (100, 50), "white").save(image)
            result = load_document(image, "mima_internal")
        vision_ocr.assert_called_once()
        self.assertEqual(vision_ocr.call_args.args[1], "mima_internal")
        self.assertEqual(len(result.chunks), 1)
        self.assertIn("| Basic | 2.000.000đ |", result.chunks[0]["content"])
        self.assertTrue(result.requires_manual_review)

    def test_loads_five_formats_and_preserves_excel_rows_and_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf = root / "service.pdf"
            docx = root / "service.docx"
            xlsx = root / "bang-gia.xlsx"
            csv_path = root / "bang-gia.csv"
            image = root / "scan.png"
            _write_text_pdf(pdf, "Website service packages for business customers")
            _write_docx(docx)
            _write_xlsx(xlsx)
            csv_path.write_text("Gói,Giá\nBasic,2.000.000đ\nPro,6.000.000đ\n", encoding="utf-8-sig")
            Image.new("RGB", (200, 80), "white").save(image)

            results = load_documents(
                [pdf, docx, xlsx, csv_path, image],
                "mima_internal",
                ocr=lambda _image: "Bảng giá chụp: Gói Basic giá 2.000.000đ",
            )
            self.assertEqual([item.file_type for item in results], ["pdf", "docx", "xlsx", "csv", "png"])
            self.assertTrue(all(item.chunks for item in results))
            excel_content = "\n".join(chunk["content"] for chunk in results[2].chunks)
            self.assertIn("| Gói | Giá |", excel_content)
            self.assertIn("| Basic | 2.000.000đ |", excel_content)
            self.assertEqual(results[2].chunks[0]["metadata"]["type"], "pricing")
            self.assertTrue(results[4].requires_manual_review)
            self.assertFalse(results[4].ready_for_index)

    def test_semicolon_csv_ignores_commas_inside_multiline_quoted_cell(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bang-gia.csv"
            path.write_text(
                'Gói;Giá;Ghi chú\n'
                'Cơ bản;2.000.000;"Giao diện mẫu, phù hợp cá nhân.\nKhông gồm SEO."\n',
                encoding="utf-8",
            )
            result = load_document(path, "mima_internal")

        content = "\n".join(chunk["content"] for chunk in result.chunks)
        self.assertIn("| Gói | Giá | Ghi chú |", content)
        self.assertIn("| Cơ bản | 2.000.000 |", content)
        self.assertNotIn("Gói;Giá;Ghi chú", content)
        self.assertIn("<br>", content)

    def test_complex_excel_splits_tables_keeps_notes_and_display_format(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bang-gia-phuc-tap.xlsx"
            _write_complex_xlsx(path)
            result = load_document(path, "mima_internal")

        contents = [chunk["content"] for chunk in result.chunks]
        joined = "\n\n".join(contents)
        self.assertNotIn("| CÔNG TY MIMA |  |", joined)
        self.assertIn("### BẢNG 1 — GÓI SEO\n| Gói | Giá / tháng |", joined)
        self.assertIn("| Basic | 4,000,000đ |", joined)
        self.assertIn("### BẢNG 2 — PHỤ PHÍ\n| Ngành | Phụ phí |", joined)
        self.assertIn("Ô D4: * Giá chưa gồm VAT", joined)
        self.assertIn("Thanh toán trước ngày 05", joined)
        table_headers = [line for line in joined.splitlines() if line.startswith("| ") and "---" not in line]
        self.assertFalse(any(line.count("|") > 3 for line in table_headers))

    def test_csv_escapes_literal_pipe_so_column_count_stays_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "faq.csv"
            path.write_text('Câu hỏi,Trả lời\n"A | B?","Chọn A | B"\n', encoding="utf-8")
            result = load_document(path, "mima_internal")
        content = result.chunks[0]["content"]
        self.assertIn(r"A \| B?", content)
        self.assertIn(r"Chọn A \| B", content)

    def test_scanned_pdf_uses_ocr_and_requires_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "scan.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=200, height=100)
            with path.open("wb") as handle:
                writer.write(handle)
            result = load_document(
                path, "mima_internal", ocr=lambda _image: "Nội dung OCR tiếng Việt có dấu"
            )
            self.assertTrue(result.used_ocr)
            self.assertTrue(result.requires_manual_review)
            self.assertIn("Nội dung OCR", result.chunks[0]["content"])
            self.assertEqual(result.chunks[0]["metadata"]["source"], "document")
            self.assertEqual(result.chunks[0]["metadata"]["source_confidence"], 0.85)

    def test_long_prose_chunks_do_not_split_words(self) -> None:
        text = " ".join(f"word{index:03d}" for index in range(100))
        chunks = _prose_chunks([text], target_chars=200, overlap_chars=30)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 200 for chunk in chunks))
        self.assertTrue(all(re.fullmatch(r"word\d{3}(?: word\d{3})*", chunk) for chunk in chunks))

    def test_unreviewed_ocr_cannot_be_exported_for_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = root / "scan.png"
            Image.new("RGB", (50, 50), "white").save(image)
            result = load_document(image, "tenant_a", ocr=lambda _: "giá OCR cần kiểm tra")
            output = root / "chunks.json"
            manifest = root / "manifest.json"
            with self.assertRaisesRegex(DocumentLoadError, "OCR chưa được duyệt"):
                _write_outputs([result], output, manifest)
            self.assertFalse(output.exists())
            self.assertTrue(output.with_suffix(".ocr-review.json").exists())
            self.assertFalse(json.loads(manifest.read_text(encoding="utf-8"))["ready_for_index"])
            review = json.loads(
                output.with_suffix(".ocr-review.json").read_text(encoding="utf-8")
            )
            self.assertEqual(review["schema_version"], "h4-04.ocr-review.v1")
            self.assertEqual(review["review_status"], "pending")
            manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(manifest_payload["ocr"]["call_count"], 1)
            self.assertIn("confidence", manifest_payload["ocr"]["calls"][0])

    def test_promotes_edited_review_without_calling_ocr_again(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = root / "scan.png"
            Image.new("RGB", (50, 50), "white").save(image)
            result = load_document(image, "tenant_a", ocr=lambda _: "giá OCR sai")
            output = root / "document-chunks.json"
            manifest = output.with_suffix(".manifest.json")
            with self.assertRaises(DocumentLoadError):
                _write_outputs([result], output, manifest)
            review_path = output.with_suffix(".ocr-review.json")
            review = json.loads(review_path.read_text(encoding="utf-8"))
            review["chunks"][0]["content"] = "Giá đã sửa và đối chiếu: 2.000.000đ"
            review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")

            promoted = promote_review(
                review_path, output, reviewer="tester", manifest_path=manifest,
            )

            self.assertEqual(load_chunks(output)[0]["content"], "Giá đã sửa và đối chiếu: 2.000.000đ")
            self.assertTrue(promoted["ready_for_index"])
            self.assertFalse(promoted["review"]["ocr_called_during_promotion"])
            self.assertEqual(promoted["review"]["reviewer"], "tester")

    def test_promote_rejects_unreadable_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            review = root / "chunks.ocr-review.json"
            review.write_text(json.dumps({"chunks": [{
                "tenant_id": "tenant_a", "chunk_id": "c1",
                "content": "[KHÔNG ĐỌC ĐƯỢC]",
                "metadata": {
                    "url": "https://upload.local/tenant_a/a.png#image-chunk-1",
                    "title": "A", "type": "pricing", "updated_at": "2026-09-09",
                    "source": "document", "source_priority": 80,
                    "source_confidence": 0.5,
                },
            }]}), encoding="utf-8")
            with self.assertRaisesRegex(OcrReviewError, "KHÔNG ĐỌC ĐƯỢC"):
                promote_review(review, root / "chunks.json", reviewer="tester")

    def test_reviewed_output_matches_existing_chunk_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = root / "scan.png"
            Image.new("RGB", (50, 50), "white").save(image)
            result = load_document(
                image, "tenant_a", ocr=lambda _: "giá đã kiểm tra thủ công", ocr_reviewed=True
            )
            output = root / "chunks.json"
            _write_outputs([result], output, root / "manifest.json")
            self.assertTrue(result.ready_for_index)
            self.assertEqual(len(load_chunks(output)), 1)

    def test_rejects_wrong_tenant_and_unsupported_legacy_xls(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "legacy.xls"
            path.write_bytes(b"not xls")
            with self.assertRaisesRegex(DocumentLoadError, "chưa hỗ trợ"):
                load_document(path, "tenant_a")
            csv_path = root / "data.csv"
            csv_path.write_text("a,b\n1,2", encoding="utf-8")
            with self.assertRaisesRegex(DocumentLoadError, "tenant_id"):
                load_document(csv_path, "../tenant")


if __name__ == "__main__":
    unittest.main()
