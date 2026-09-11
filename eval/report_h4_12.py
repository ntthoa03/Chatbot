"""H4-12: compile measured data-source impact for one tenant only."""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TENANT_ID = "mima_internal"
STOPWORDS = {
    "và", "là", "có", "không", "được", "cho", "của", "một", "cần", "thể",
    "phải", "sau", "khi", "theo", "với", "từ", "tại", "này", "để", "ạ",
    "anh", "chị", "khách", "bên", "em", "trong", "những", "thường",
}


def _tokens(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    words = re.findall(r"[\w]+", normalized, flags=re.UNICODE)
    return {word for word in words if len(word) > 1 and word not in STOPWORDS}


def _question_part(content: str) -> str:
    match = re.search(r"(?:Hỏi|Câu hỏi):\s*(.*?)(?:\n|$)", content, flags=re.IGNORECASE)
    return match.group(1) if match else content[:700]


def _answer_part(content: str) -> str:
    match = re.search(
        r"(?:Đáp|Câu trả lời(?: chính thức từ doanh nghiệp)?):\s*(.*)",
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return match.group(1) if match else content


def evidence_eval(cases: list[dict[str, Any]], chunks: list[dict[str, Any]]) -> dict[str, Any]:
    passed = 0
    sufficient = 0
    details = []
    for case in cases:
        query_tokens = _tokens(str(case["question"]))
        ranked = []
        for chunk in chunks:
            candidate = _tokens(_question_part(str(chunk.get("content", ""))))
            union = query_tokens | candidate
            score = len(query_tokens & candidate) / len(union) if union else 0.0
            ranked.append((score, chunk))
        score, top = max(ranked, key=lambda item: item[0]) if ranked else (0.0, {})
        has_data = score >= 0.12
        rubric = str(case.get("rubric", ""))
        expected = rubric.split(":", 1)[-1]
        expected_tokens = _tokens(expected)
        answer_tokens = _tokens(_answer_part(str(top.get("content", ""))))
        coverage = len(expected_tokens & answer_tokens) / len(expected_tokens) if expected_tokens else 0.0
        correct = has_data and coverage >= 0.35
        sufficient += int(has_data)
        passed += int(correct)
        details.append({
            "id": case.get("id"), "top_chunk_id": top.get("chunk_id"),
            "question_similarity": round(score, 4), "rubric_token_coverage": round(coverage, 4),
            "data_sufficient": has_data, "passed": correct,
        })
    total = len(cases)
    return {
        "total": total, "passed": passed, "pass_rate": passed / total,
        "data_sufficient": sufficient, "data_sufficiency_rate": sufficient / total,
        "details": details,
    }


def _load_chunks(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        {key: item.get(key) for key in ("tenant_id", "chunk_id", "content", "metadata")}
        for item in raw
        if isinstance(item, dict) and item.get("tenant_id") == TENANT_ID
    ]


def build_report() -> dict[str, Any]:
    h403 = json.loads((ROOT / "outputs/h4_03/report.json").read_text(encoding="utf-8"))
    crawl_health = next(item for item in h403["tenants"] if item["tenant_id"] == TENANT_ID)
    documents = json.loads(
        (ROOT / "outputs/h4_05/experiment/h4_05_comparison.json").read_text(encoding="utf-8")
    )
    h406 = json.loads((ROOT / "outputs/h4_06/audit.json").read_text(encoding="utf-8"))
    tenant_answers = json.loads((ROOT / "outputs/h4_11/acceptance.json").read_text(encoding="utf-8"))

    cases = yaml.safe_load((ROOT / "outputs/h4_07/eval_cases/cases.yaml").read_text(encoding="utf-8"))
    crawl_chunks = _load_chunks(ROOT / "index/metadata.json")
    chat_chunks = _load_chunks(ROOT / "outputs/h4_07/knowledge/knowledge_chunks.json")
    chat_baseline = evidence_eval(cases, crawl_chunks)
    chat_post = evidence_eval(cases, [*crawl_chunks, *chat_chunks])

    doc_before = documents["baseline"]
    doc_after = documents["post_documents"]
    sources = [
        {
            "source": "crawl",
            "measurement": "H4-05 targeted suite, empty knowledge → crawl index",
            "case_count": doc_before["total"],
            "before_accuracy": 0.0,
            "after_accuracy": doc_before["pass_rate"],
            "accuracy_gain": doc_before["pass_rate"],
            "before_sufficiency": 0.0,
            "after_sufficiency": doc_before["data_sufficiency_rate"],
            "sufficiency_gain": doc_before["data_sufficiency_rate"],
            "method": "existing controlled eval; empty control has no retrievable evidence",
        },
        {
            "source": "tài liệu",
            "measurement": "H4-05 controlled A/B",
            "case_count": doc_before["total"],
            "before_accuracy": doc_before["pass_rate"],
            "after_accuracy": doc_after["pass_rate"],
            "accuracy_gain": documents["delta"]["pass_rate"],
            "before_sufficiency": doc_before["data_sufficiency_rate"],
            "after_sufficiency": doc_after["data_sufficiency_rate"],
            "sufficiency_gain": documents["delta"]["data_sufficiency_rate"],
            "method": "existing online model eval, TEST/SYNTHETIC documents",
        },
        {
            "source": "log chat",
            "measurement": "H4-07 holdout cases, crawl → crawl + separated knowledge split",
            "case_count": chat_baseline["total"],
            "before_accuracy": chat_baseline["pass_rate"],
            "after_accuracy": chat_post["pass_rate"],
            "accuracy_gain": chat_post["pass_rate"] - chat_baseline["pass_rate"],
            "before_sufficiency": chat_baseline["data_sufficiency_rate"],
            "after_sufficiency": chat_post["data_sufficiency_rate"],
            "sufficiency_gain": chat_post["data_sufficiency_rate"] - chat_baseline["data_sufficiency_rate"],
            "method": "offline evidence retrieval; no external egress",
        },
        {
            "source": "tenant tự trả lời",
            "measurement": "H4-11 five-question controlled A/B",
            "case_count": tenant_answers["case_count"],
            "before_accuracy": tenant_answers["baseline"]["pass_rate"],
            "after_accuracy": tenant_answers["post_tenant_answers"]["pass_rate"],
            "accuracy_gain": tenant_answers["delta"]["pass_rate"],
            "before_sufficiency": tenant_answers["baseline"]["data_sufficiency_rate"],
            "after_sufficiency": tenant_answers["post_tenant_answers"]["data_sufficiency_rate"],
            "sufficiency_gain": tenant_answers["delta"]["data_sufficiency_rate"],
            "method": "offline immediate-index acceptance, TEST/SYNTHETIC answers",
        },
    ]
    ranking = sorted(sources, key=lambda item: (-item["accuracy_gain"], -item["sufficiency_gain"]))
    return {
        "schema_version": "h4-12.data-source-impact.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "tenant_id": TENANT_ID,
        "comparison_scope": "same tenant; paired before/after within each source",
        "data_label": "MIXED: existing eval + TEST/SYNTHETIC acceptance; not production KPI",
        "h4_03_crawl_health": {
            "case_count": crawl_health["total"],
            "accuracy": crawl_health["effective_pass_rate"],
            "data_sufficiency": crawl_health["data_sufficiency_rate"],
        },
        "h4_06_input": {
            "candidate_pairs": h406["candidate_pairs"],
            "knowledge_chunks_after_h4_07_split": len(chat_chunks),
        },
        "sources": sources,
        "ranking": [item["source"] for item in ranking],
        "chat_offline_details": {"baseline": chat_baseline, "post": chat_post},
        "conclusion": (
            "Trong acceptance hiện có, tenant tự trả lời tạo mức tăng accuracy lớn nhất; "
            "tài liệu đứng sau. Đây là thứ hạng thử nghiệm, phải đo lại trên cùng một bộ case "
            "production trước quyết định ngân sách quý."
        ),
    }


def _pct(value: float) -> str:
    return f"{value:.1%}"


def write_report(payload: dict[str, Any], markdown_path: Path, json_path: Path) -> None:
    rows = []
    for rank, source_name in enumerate(payload["ranking"], start=1):
        item = next(row for row in payload["sources"] if row["source"] == source_name)
        rows.append(
            f"| {rank} | {item['source']} | {item['case_count']} | "
            f"{_pct(item['before_accuracy'])} → {_pct(item['after_accuracy'])} | "
            f"{_pct(item['accuracy_gain'])} | "
            f"{_pct(item['before_sufficiency'])} → {_pct(item['after_sufficiency'])} | "
            f"{_pct(item['sufficiency_gain'])} | {item['method']} |"
        )
    health = payload["h4_03_crawl_health"]
    markdown = "\n".join([
        "# H4-12 — Tác động của từng nguồn data", "",
        "> Phạm vi: chỉ tenant `mima_internal`. Số liệu gồm eval đã chạy và acceptance TEST/SYNTHETIC; chưa phải KPI production.", "",
        "## Kết quả định lượng", "",
        "| Hạng | Nguồn | Số câu | Điểm eval trước → sau | Tăng điểm | Đủ dữ liệu trước → sau | Tăng độ phủ | Cách đo |",
        "|---:|---|---:|---:|---:|---:|---:|---|", *rows, "",
        "## Kiểm tra nền crawl H4-03", "",
        f"Trên 15 case H4-03 của cùng tenant: điểm hiệu dụng **{_pct(health['accuracy'])}**, "
        f"tỷ lệ đủ dữ liệu **{_pct(health['data_sufficiency'])}**. Dòng crawl trong bảng dùng bộ 12 case thiếu dữ liệu H4-05 để đo đóng góp so với kho rỗng.", "",
        "## Kết luận", "", payload["conclusion"], "",
        "Ưu tiên đề xuất: (1) thu câu trả lời tenant cho gap có tần suất cao; (2) tiếp tục nạp tài liệu có cấu trúc; "
        "(3) log chat chỉ đưa nhánh knowledge đã tách khỏi eval vào kho; (4) crawl vẫn là nền độ phủ.", "",
        "## Giới hạn bắt buộc khi diễn giải", "",
        "- Mọi nguồn được đo trên cùng tenant, nhưng mỗi A/B dùng bộ case phù hợp nguồn; không cộng các phần trăm thành một chuỗi nhân quả.",
        "- Tài liệu, log chat và câu tenant trong acceptance hiện là TEST/SYNTHETIC; không trình bày các số này như hiệu quả khách hàng thật.",
        "- Eval log chat là đo evidence retrieval offline vì môi trường không cho phép gửi knowledge chunks nội bộ ra API ngoài.",
        "- Trước quyết định ngân sách quý, cần chạy lại cùng một bộ case production cố định và có tenant xác nhận ground truth.", "",
        "## Truy vết", "",
        "- Crawl: `outputs/h4_03/report.json`", "- Tài liệu: `outputs/h4_05/experiment/h4_05_comparison.json`",
        "- Log chat: `outputs/h4_06/audit.json` + split H4-07", "- Tenant: `outputs/h4_11/acceptance.json`",
    ]) + "\n"
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(markdown, encoding="utf-8")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    report = build_report()
    write_report(
        report,
        ROOT / "docs" / "tac-dong-nguon-data.md",
        ROOT / "outputs" / "h4_12" / "impact_metrics.json",
    )
    print(json.dumps({"tenant_id": report["tenant_id"], "ranking": report["ranking"], "sources": report["sources"]}, ensure_ascii=False, indent=2))
