"""Run the reproducible H4-04 acceptance suite and print extracted content.

This suite deliberately uses deterministic OCR text for the two image-only
fixtures. It tests routing, review/index gating and chunk normalization without
sending business documents to an external API. Provider behavior is covered by
``tests.test_vision_ocr``; a live OCR run is a separate, billable smoke test.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ingestion.document_loader import _write_outputs, load_documents


DEFAULT_FIXTURES = Path("docs/test_docs_H4-05/test_docs")
FILES = (
    "bang_gia_quang_cao_ads.pdf",
    "bang_gia_ten_mien_hosting_SCAN.pdf",
    "chinh_sach_bao_hanh.docx",
    "bang_gia_seo.xlsx",
    "bang_gia_thiet_ke_web.csv",
    "anh_chup_bang_gia_backlink.jpg",
)


def deterministic_ocr(image: object) -> str:
    width, height = getattr(image, "size", (0, 0))
    if height > width:
        return (
            "### Bảng giá tên miền và hosting (OCR test)\n"
            "| Hạng mục | Giá / năm |\n| --- | --- |\n"
            "| Tên miền .vn | 750.000đ |\n| Hosting Business | 2.400.000đ |"
        )
    return (
        "### Bảng giá backlink (OCR test)\n"
        "| Gói | Số backlink | Giá |\n| --- | ---: | ---: |\n"
        "| Cơ bản | 10 | 1.500.000đ |\n| Nâng cao | 25 | 3.200.000đ |"
    )


def check(results: tuple, chunks: list[dict]) -> list[str]:
    joined = "\n\n".join(chunk["content"] for chunk in chunks)
    excel = "\n\n".join(
        chunk["content"] for chunk in results[3].chunks
    )
    checks = {
        "Nạp đủ PDF text/PDF scan/DOCX/XLSX/CSV/JPG":
            [item.file_type for item in results] == ["pdf", "pdf", "docx", "xlsx", "csv", "jpg"],
        "Mỗi tài liệu sinh ít nhất một KnowledgeChunk": all(item.chunks for item in results),
        "PDF text-layer không gọi OCR": not results[0].used_ocr,
        "PDF scan và ảnh đi qua OCR": results[1].used_ocr and results[5].used_ocr,
        "Excel nhận đúng header bảng 1": "| Mức cạnh tranh | Số từ khoá | Giá / tháng (VNĐ) | Cam kết KPI (tháng thứ 3) |" in excel,
        "Excel tách riêng bảng 2": "| Ngành | Phụ phí thêm | Lý do |" in excel,
        "Excel giữ giá và định dạng tiền": "| Thấp (ngành ít đối thủ) | 10-15 từ khoá | 4,000,000đ |" in excel,
        "Excel giữ ghi chú ngoài bảng": "Ô F6: * Giá chưa gồm VAT" in excel,
        "Excel đọc cả sheet điều khoản": "Nếu không đạt KPI" in excel,
        "Excel không dùng tên công ty làm header": "| CÔNG TY TNHH THƯƠNG MẠI DỊCH VỤ MIMA |  |" not in excel,
        "CSV nhiều dòng vẫn giữ trong một ô": "<br>" in joined,
        "Đầu ra đã duyệt sẵn sàng cho index": all(item.ready_for_index for item in results),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise AssertionError("; ".join(failures))
    return list(checks)


def main() -> int:
    parser = argparse.ArgumentParser(description="Nghiệm thu H4-04 trên 6 tài liệu khó")
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/h4_04"))
    parser.add_argument("--tenant-id", default="mima_internal")
    args = parser.parse_args()
    paths = [args.fixtures / name for name in FILES]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        parser.error("Thiếu fixture: " + ", ".join(missing))

    results = load_documents(paths, args.tenant_id, ocr=deterministic_ocr, ocr_reviewed=True)
    chunks = [chunk for result in results for chunk in result.chunks]
    checks = check(results, chunks)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / "acceptance-chunks.json"
    manifest = args.output_dir / "acceptance-manifest.json"
    _write_outputs(results, output, manifest)
    report = args.output_dir / "acceptance-report.md"
    lines = [
        "# H4-04 — Kết quả nghiệm thu có thể tái lập",
        "",
        f"- Thời điểm UTC: `{datetime.now(timezone.utc).isoformat()}`",
        f"- Tài liệu: **{len(results)}** (5 loại bắt buộc + PDF scan)",
        f"- Chunks: **{len(chunks)}**",
        f"- Kiểm tra: **{len(checks)}/{len(checks)} PASS**",
        "- OCR trong lượt này: dữ liệu giả lập cố định, không gọi API ngoài.",
        "",
        "## Các điều kiện đã qua",
        "",
        *[f"- PASS — {name}" for name in checks],
        "",
        "## Nội dung đã trích để quan sát",
        "",
    ]
    for result in results:
        lines.extend([f"### {Path(result.path).name}", ""])
        for chunk in result.chunks:
            lines.extend(["```text", chunk["content"], "```", ""])
    report.write_text("\n".join(lines), encoding="utf-8")

    print(f"H4-04 ACCEPTANCE: PASS ({len(checks)}/{len(checks)})")
    print(f"Tài liệu: {len(results)} | Chunks: {len(chunks)}")
    for result in results:
        print(
            f"- {Path(result.path).name}: {len(result.chunks)} chunks | "
            f"OCR={result.used_ocr} | ready_for_index={result.ready_for_index}"
        )
    print(f"Báo cáo đọc: {report.resolve()}")
    print(f"Chunks JSON: {output.resolve()}")
    print(f"Manifest: {manifest.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
