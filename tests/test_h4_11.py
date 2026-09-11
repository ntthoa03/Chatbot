from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from ai_core.retriever import retrieve
from index_chunks import build_index, save_index
from ingestion.tenant_answer_ingestion import (
    build_tenant_answer_chunk,
    ingest_tenant_answer,
)


TOPICS = ("duy trì", "bao lâu", "hỗ trợ", "thanh toán", "sở hữu")


def topic_embed(texts, **_kwargs):
    vectors = []
    for text in texts:
        lowered = text.casefold()
        vectors.append([1.0 if topic in lowered else 0.0 for topic in TOPICS])
    return vectors


def crawl_chunk(question: str, answer: str, chunk_id: str = "crawl-conflict") -> dict:
    return {
        "tenant_id": "mima_internal",
        "chunk_id": chunk_id,
        "content": f"Câu hỏi: {question}\nCâu trả lời từ website cũ: {answer}",
        "metadata": {
            "url": "https://mimadigi.com/faq",
            "title": "FAQ crawl cũ",
            "type": "faq",
            "updated_at": "2026-08-01",
            "source": "crawl",
            "source_priority": 10,
            "source_confidence": 0.7,
        },
    }


class H411TenantAnswerTests(unittest.TestCase):
    def test_chunk_has_highest_confidence_tenant_metadata(self) -> None:
        chunk = build_tenant_answer_chunk(
            "mima_internal", "Phí duy trì là bao nhiêu?", "2 triệu đồng mỗi năm."
        )
        self.assertEqual(chunk["metadata"]["source"], "tenant_provided")
        self.assertEqual(chunk["metadata"]["source_priority"], 100)
        self.assertEqual(chunk["metadata"]["source_confidence"], 1.0)

    def test_five_answers_are_available_immediately_and_override_crawl(self) -> None:
        cases = [
            ("Phí duy trì website mỗi năm là bao nhiêu?", "Phí duy trì chính thức là 2 triệu đồng mỗi năm."),
            ("Thiết kế website mất bao lâu?", "Thời gian tiêu chuẩn là 20 ngày làm việc."),
            ("Sau bàn giao có hỗ trợ không?", "Có hỗ trợ kỹ thuật trong 12 tháng."),
            ("Thanh toán được chia mấy đợt?", "Thanh toán được chia thành 3 đợt."),
            ("Khách hàng có sở hữu mã nguồn không?", "Khách hàng sở hữu mã nguồn sau khi hoàn tất thanh toán."),
        ]
        with tempfile.TemporaryDirectory() as directory:
            index_dir = Path(directory)
            baseline = [crawl_chunk(cases[0][0], "Phí cũ là 10 triệu đồng mỗi năm.")]
            records, _, _, _ = build_index(
                baseline, {}, embed_fn=topic_embed, model="topic-test", provider="test"
            )
            save_index(records, index_dir, model="topic-test", provider="test")

            started = time.perf_counter()
            receipts = [
                ingest_tenant_answer(
                    "mima_internal",
                    question,
                    answer,
                    index_dir=index_dir,
                    embed_fn=topic_embed,
                    updated_at="2026-09-07",
                )
                for question, answer in cases
            ]
            elapsed = time.perf_counter() - started
            for (question, answer), receipt in zip(cases, receipts):
                results = retrieve(
                    question,
                    "mima_internal",
                    index_dir=index_dir,
                    embed_fn=topic_embed,
                    model="topic-test",
                    provider="test",
                    threshold=0.65,
                )
                self.assertEqual(results[0]["metadata"]["source"], "tenant_provided")
                self.assertIn(answer, results[0]["content"])
                self.assertLess(receipt["elapsed_seconds"], 60)

            conflict_results = retrieve(
                cases[0][0],
                "mima_internal",
                index_dir=index_dir,
                embed_fn=topic_embed,
                model="topic-test",
                provider="test",
                threshold=0.65,
            )
            metadata = json.loads((index_dir / "metadata.json").read_text(encoding="utf-8"))

        self.assertLess(elapsed, 60)
        self.assertEqual(len(receipts), 5)
        self.assertEqual(conflict_results[0]["metadata"]["source_priority"], 100)
        self.assertIn("2 triệu", conflict_results[0]["content"])
        self.assertTrue(any(item["metadata"].get("source") == "crawl" for item in metadata))
        self.assertTrue(any(item["metadata"].get("source") == "tenant_provided" for item in metadata))


if __name__ == "__main__":
    unittest.main()
