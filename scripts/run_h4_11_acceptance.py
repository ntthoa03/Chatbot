"""Run the five-question H4-11 acceptance test without external API calls."""

from __future__ import annotations

import json
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_core.retriever import retrieve  # noqa: E402
from index_chunks import build_index, save_index  # noqa: E402
from ingestion.tenant_answer_ingestion import ingest_tenant_answer  # noqa: E402


TOPICS = ("duy trì", "bao lâu", "hỗ trợ", "thanh toán", "sở hữu")
CASES = (
    ("Phí duy trì website mỗi năm là bao nhiêu?", "Phí duy trì chính thức là 2 triệu đồng mỗi năm."),
    ("Thiết kế website mất bao lâu?", "Thời gian tiêu chuẩn là 20 ngày làm việc."),
    ("Sau bàn giao có hỗ trợ không?", "Có hỗ trợ kỹ thuật trong 12 tháng."),
    ("Thanh toán được chia mấy đợt?", "Thanh toán được chia thành 3 đợt."),
    ("Khách hàng có sở hữu mã nguồn không?", "Khách hàng sở hữu mã nguồn sau khi hoàn tất thanh toán."),
)


def topic_embed(texts, **_kwargs):
    return [
        [1.0 if topic in text.casefold() else 0.0 for topic in TOPICS]
        for text in texts
    ]


def run(output: Path) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        index_dir = Path(directory)
        crawl = [{
            "tenant_id": "mima_internal",
            "chunk_id": "crawl-conflict",
            "content": f"Câu hỏi: {CASES[0][0]}\nCâu trả lời từ website cũ: Phí cũ là 10 triệu đồng mỗi năm.",
            "metadata": {
                "url": "https://mimadigi.com/faq", "title": "FAQ crawl cũ",
                "type": "faq", "updated_at": "2026-08-01", "source": "crawl",
                "source_priority": 10, "source_confidence": 0.7,
            },
        }]
        records, _, _, _ = build_index(
            crawl, {}, embed_fn=topic_embed, model="topic-test", provider="test"
        )
        save_index(records, index_dir, model="topic-test", provider="test")
        baseline_results = [
            retrieve(q, "mima_internal", index_dir=index_dir, embed_fn=topic_embed,
                     model="topic-test", provider="test", threshold=0.65)
            for q, _ in CASES
        ]
        baseline_sufficient = sum(bool(items) for items in baseline_results)
        baseline_correct = sum(
            bool(items) and answer in items[0]["content"]
            for items, (_, answer) in zip(baseline_results, CASES)
        )

        started = time.perf_counter()
        receipts = [
            ingest_tenant_answer(
                "mima_internal", question, answer, index_dir=index_dir,
                embed_fn=topic_embed, updated_at="2026-09-07",
            )
            for question, answer in CASES
        ]
        elapsed = time.perf_counter() - started
        post_results = [
            retrieve(q, "mima_internal", index_dir=index_dir, embed_fn=topic_embed,
                     model="topic-test", provider="test", threshold=0.65)
            for q, _ in CASES
        ]
        post_sufficient = sum(bool(items) for items in post_results)
        post_correct = sum(
            bool(items) and answer in items[0]["content"]
            for items, (_, answer) in zip(post_results, CASES)
        )
        conflict_override = (
            post_results[0][0]["metadata"].get("source") == "tenant_provided"
            and "2 triệu" in post_results[0][0]["content"]
        )

    total = len(CASES)
    result = {
        "schema_version": "h4-11.acceptance.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "tenant_id": "mima_internal",
        "source_data_label": "TEST/SYNTHETIC — không phải câu trả lời tenant thật",
        "case_count": total,
        "baseline": {
            "passed": baseline_correct, "pass_rate": baseline_correct / total,
            "data_sufficient": baseline_sufficient,
            "data_sufficiency_rate": baseline_sufficient / total,
        },
        "post_tenant_answers": {
            "passed": post_correct, "pass_rate": post_correct / total,
            "data_sufficient": post_sufficient,
            "data_sufficiency_rate": post_sufficient / total,
        },
        "delta": {
            "pass_rate": (post_correct - baseline_correct) / total,
            "data_sufficiency_rate": (post_sufficient - baseline_sufficient) / total,
        },
        "elapsed_seconds_for_five_answers": round(elapsed, 3),
        "all_receipts_under_60_seconds": all(item["elapsed_seconds"] < 60 for item in receipts),
        "crawl_conflict_overridden": conflict_override,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    target = ROOT / "outputs" / "h4_11" / "acceptance.json"
    print(json.dumps(run(target), ensure_ascii=False, indent=2))
