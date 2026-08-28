"""Kiểm tra offline rằng rubric H3-09 có bằng chứng trong đúng corpus tenant."""

from __future__ import annotations

import csv
import json
from pathlib import Path
import re
import unicodedata

from ai_core.evaluator import load_cases


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "outputs" / "h3_09"
TENANTS = {
    "mima_internal": (ROOT / "eval/cases_mima_internal.yaml", ROOT / "index/metadata.json"),
    "phongkham_hyhy": (
        ROOT / "eval/cases_phongkham_hyhy.yaml",
        ROOT / "outputs/h2_04/index_phongkham_hyhy/metadata.json",
    ),
    "bat_dong_san_phuoc_thinh": (
        ROOT / "eval/cases_bat_dong_san_phuoc_thinh.yaml",
        ROOT / "outputs/h3_01/index_bat_dong_san_phuoc_thinh/metadata.json",
    ),
    "giao_duc_haiyan": (
        ROOT / "eval/cases_giao_duc_haiyan.yaml",
        ROOT / "outputs/h3_01/index_giao_duc_haiyan/metadata.json",
    ),
    "thuc_pham_thien_minh": (
        ROOT / "eval/cases_thuc_pham_thien_minh.yaml",
        ROOT / "outputs/h3_01/index_thuc_pham_thien_minh/metadata.json",
    ),
}


def _normalise(value: str) -> str:
    text = unicodedata.normalize("NFD", value.casefold().replace("đ", "d"))
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return re.sub(r"\s+", " ", text).strip()


def _corpus_text(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload if isinstance(payload, list) else payload.get("items") or payload.get("metadata") or []
    pieces: list[str] = []
    for row in rows:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else row
        pieces.extend(
            str(value)
            for value in (
                metadata.get("title", ""),
                metadata.get("content", ""),
                row.get("content", ""),
                metadata.get("url", ""),
            )
            if value
        )
    return _normalise("\n".join(pieces))


def main() -> int:
    rows: list[dict] = []
    summaries: list[dict] = []
    for tenant_id, (cases_path, metadata_path) in TENANTS.items():
        corpus = _corpus_text(metadata_path)
        tenant_rows: list[dict] = []
        for case in load_cases(cases_path):
            required_all = [term for term in case.must_contain if _normalise(term) in corpus]
            required_any = [term for term in case.must_contain_any if _normalise(term) in corpus]
            evidence_ok = len(required_all) == len(case.must_contain) and (
                not case.must_contain_any or bool(required_any)
            )
            row = {
                "tenant_id": tenant_id,
                "id": case.id,
                "topic": case.topic,
                "question": case.question,
                "evidence_ok": evidence_ok,
                "matched_terms": " | ".join(required_all + required_any),
                "expected_terms": " | ".join(case.must_contain + case.must_contain_any),
            }
            tenant_rows.append(row)
            rows.append(row)
        covered = sum(bool(row["evidence_ok"]) for row in tenant_rows)
        summaries.append(
            {
                "tenant_id": tenant_id,
                "covered": covered,
                "total": len(tenant_rows),
                "coverage_rate": round(covered / len(tenant_rows), 4),
            }
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "h3-09.corpus-coverage.v1",
        "offline": True,
        "note": "Chỉ xác nhận dữ kiện có trong corpus; không phải điểm chất lượng chatbot.",
        "summary": summaries,
        "rows": rows,
    }
    (OUTPUT_DIR / "corpus_coverage.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (OUTPUT_DIR / "corpus_coverage.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    return 0 if all(item["covered"] == item["total"] for item in summaries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
