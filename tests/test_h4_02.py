"""H4-02 — clustering, xếp hạng và báo cáo knowledge gap theo tenant."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ai_core.chat import LLMResult
from ai_core.gap_clustering import (
    GapClusteringError,
    build_cluster_report,
    cluster_tenant_gaps,
    llm_cluster_namer,
    normalize_question,
    partition_actionable_gaps,
)
from scripts.cluster_knowledge_gaps import run
from storage import SQLiteStore


TENANT_ID = "mima_internal"


def semantic_embed(texts: list[str], **_kwargs) -> list[list[float]]:
    vectors = []
    for text in texts:
        normalized = normalize_question(text)
        if any(
            token in normalized
            for token in (
                "mo cua",
                "mở cửa",
                "ngoai gio",
                "ngoài giờ",
                "chu nhat",
                "chủ nhật",
                "cuoi tuan",
                "cuối tuần",
            )
        ):
            vectors.append([1.0, 0.0, 0.0])
        elif any(token in normalized for token in ("gia", "giá", "bao nhieu", "bao nhiêu", "chi phi", "chi phí")):
            vectors.append([0.0, 1.0, 0.0])
        else:
            vectors.append([0.0, 0.0, 1.0])
    return vectors


def gap(question: str, *, tenant_id: str = TENANT_ID, index: int = 1) -> dict:
    return {
        "knowledge_gap_id": index,
        "tenant_id": tenant_id,
        "conversation_id": f"conversation-{index}",
        "question": question,
        "top_score": 0.4,
        "threshold": 0.65,
        "reason": "below_threshold",
        "trace_id": f"trace-{index}",
        "occurred_at": f"2026-09-04T08:{index:02d}:00+00:00",
    }


def sample_gaps() -> list[dict]:
    questions = [
        "Bên mình có mở cửa Chủ nhật không?",
        "Cuối tuần công ty có làm việc không?",
        "Có hỗ trợ ngoài giờ không?",
        "Bên mình có mở cửa Chủ nhật không?",
        "Dịch vụ này giá bao nhiêu?",
        "Cho tôi xin chi phí thực hiện",
        "Dịch vụ này giá bao nhiêu?",
        "Chính sách đổi trả thế nào?",
    ]
    return [gap(question, index=index) for index, question in enumerate(questions, start=1)]


class GapClusteringTests(unittest.TestCase):
    def test_filters_technical_errors_and_greetings_before_clustering(self) -> None:
        rows = [
            gap("Xin chào!", index=1),
            {**gap("Không kết nối được index", index=2), "reason": "retrieval_error"},
            {**gap("MIMA có hỗ trợ Chủ nhật không?", index=3), "reason": "no_match"},
        ]
        included, excluded = partition_actionable_gaps(rows)
        self.assertEqual(["MIMA có hỗ trợ Chủ nhật không?"], [row["question"] for row in included])
        self.assertEqual(
            {"non_knowledge:greeting": 1, "reason:retrieval_error": 1},
            excluded,
        )

    def test_groups_paraphrases_counts_duplicates_and_ranks_by_frequency(self) -> None:
        clusters = cluster_tenant_gaps(sample_gaps(), TENANT_ID, embed_fn=semantic_embed)
        self.assertEqual(3, len(clusters))
        self.assertEqual([4, 3, 1], [item["frequency"] for item in clusters])
        self.assertEqual([3, 2, 1], [item["unique_question_count"] for item in clusters])
        self.assertEqual([1, 2, 3], [item["rank"] for item in clusters])
        self.assertTrue(all(item["review_required"] for item in clusters))
        self.assertTrue(all(item["minimum_similarity"] >= 0.85 for item in clusters))

    def test_never_accepts_rows_from_another_tenant(self) -> None:
        rows = [gap("Giá bao nhiêu?"), gap("Giá bao nhiêu?", tenant_id="phongkham_hyhy", index=2)]
        with self.assertRaisesRegex(GapClusteringError, "không thuộc tenant"):
            cluster_tenant_gaps(rows, TENANT_ID, embed_fn=semantic_embed)

    def test_complete_linkage_prevents_bridge_from_merging_broad_topics(self) -> None:
        # A-B và B-C khoảng 0.906 nhưng A-C chỉ khoảng 0.643. Single-linkage sẽ
        # nhập cả ba; complete-linkage bắt buộc tách ít nhất một câu.
        angles = {"a": 0.0, "b": 25.0, "c": 50.0}

        def bridge_embed(texts: list[str], **_kwargs):
            return [
                [math.cos(math.radians(angles[text])), math.sin(math.radians(angles[text]))]
                for text in texts
            ]

        rows = [gap("a", index=1), gap("b", index=2), gap("c", index=3)]
        clusters = cluster_tenant_gaps(rows, TENANT_ID, embed_fn=bridge_embed)
        self.assertEqual(2, len(clusters))
        self.assertTrue(all(item["minimum_similarity"] >= 0.85 for item in clusters))

    def test_injected_namer_labels_clusters_without_changing_membership(self) -> None:
        def namer(_tenant_id: str, clusters):
            return {item["cluster_id"]: f"Chủ đề {item['rank']}" for item in clusters}

        clusters = cluster_tenant_gaps(
            sample_gaps(),
            TENANT_ID,
            embed_fn=semantic_embed,
            namer=namer,
        )
        self.assertEqual("Chủ đề 1", clusters[0]["name"])
        self.assertTrue(all(item["naming_method"] == "llm" for item in clusters))

    def test_approved_manual_groups_split_an_overly_broad_embedding_cluster(self) -> None:
        rows = [
            gap("Văn phòng có mở Chủ nhật không?", index=1),
            gap("Văn phòng có chỗ đậu ô tô không?", index=2),
            gap("Có hỗ trợ kỹ thuật lúc 2 giờ sáng không?", index=3),
        ]

        def broad_embed(texts: list[str], **_kwargs):
            return [[1.0, 0.0] for _ in texts]

        clusters = cluster_tenant_gaps(
            rows,
            TENANT_ID,
            embed_fn=broad_embed,
            manual_groups=[
                {
                    "name": "Giờ mở cửa Chủ nhật",
                    "questions": ["Văn phòng có mở Chủ nhật không?"],
                    "review_status": "approved",
                    "review_notes": "Đã kiểm tra.",
                },
                {
                    "name": "Chỗ đậu ô tô",
                    "questions": ["Văn phòng có chỗ đậu ô tô không?"],
                    "review_status": "approved",
                },
            ],
        )
        self.assertEqual(3, len(clusters))
        approved = [item for item in clusters if item["review_status"] == "approved"]
        self.assertEqual(2, len(approved))
        self.assertEqual(
            {"Giờ mở cửa Chủ nhật", "Chỗ đậu ô tô"},
            {item["name"] for item in approved},
        )
        self.assertTrue(all(item["naming_method"] == "manual" for item in approved))

    def test_embedding_provider_falls_back_using_tenant_policy(self) -> None:
        calls: list[tuple[str | None, str | None]] = []

        def flaky_embed(texts: list[str], model=None, provider=None, **_kwargs):
            calls.append((model, provider))
            if provider == "gemini":
                raise RuntimeError("primary unavailable")
            return semantic_embed(texts)

        clusters = cluster_tenant_gaps(sample_gaps(), TENANT_ID, embed_fn=flaky_embed)
        self.assertEqual("gemini", calls[0][1])
        self.assertEqual("openai", calls[1][1])
        self.assertTrue(all(item["embedding_provider"] == "openai" for item in clusters))

    @patch("ai_core.chat._generate_with_fallback")
    def test_llm_namer_parses_fenced_json_and_only_known_cluster_ids(self, generate) -> None:
        clusters = cluster_tenant_gaps(sample_gaps(), TENANT_ID, embed_fn=semantic_embed)
        first_id = clusters[0]["cluster_id"]
        generate.return_value = LLMResult(
            f'```json\n{{"{first_id}": "Giờ làm việc", "unknown": "Bỏ qua"}}\n```',
            "test-model",
            10,
            5,
        )
        names = llm_cluster_namer(TENANT_ID, clusters[:1])
        self.assertEqual({first_id: "Giờ làm việc"}, names)


class GapClusterReportTests(unittest.TestCase):
    def test_only_first_ten_clusters_are_marked_for_manual_review(self) -> None:
        rows = [gap(f"topic-{index}", index=index) for index in range(1, 13)]

        def identity_embed(texts: list[str], **_kwargs):
            return [
                [1.0 if left == right else 0.0 for right in range(len(texts))]
                for left in range(len(texts))
            ]

        clusters = cluster_tenant_gaps(rows, TENANT_ID, embed_fn=identity_embed)
        self.assertEqual(12, len(clusters))
        self.assertEqual(10, sum(item["review_required"] for item in clusters))
        self.assertTrue(all(item["review_required"] for item in clusters[:10]))
        self.assertTrue(all(not item["review_required"] for item in clusters[10:]))
        report = build_cluster_report(
            {TENANT_ID: clusters},
            similarity_threshold=0.85,
            source="test.sqlite3",
        )
        self.assertEqual(10, len(report["tenants"][TENANT_ID]["top_missing_topics"]))

    def test_report_keeps_top_ten_for_each_tenant(self) -> None:
        clusters = cluster_tenant_gaps(sample_gaps(), TENANT_ID, embed_fn=semantic_embed)
        report = build_cluster_report(
            {TENANT_ID: clusters},
            similarity_threshold=0.85,
            source="test.sqlite3",
        )
        tenant = report["tenants"][TENANT_ID]
        self.assertEqual(8, tenant["total_gaps"])
        self.assertEqual(3, tenant["cluster_count"])
        self.assertEqual(3, len(tenant["top_missing_topics"]))

    def test_script_writes_json_markdown_and_manual_review_csv(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "gaps.sqlite3"
            output = root / "report"
            with SQLiteStore(database) as storage:
                storage.upsert_tenant(TENANT_ID, "MIMA")
                for index, row in enumerate(sample_gaps(), start=1):
                    storage.create_conversation(TENANT_ID, row["conversation_id"])
                    storage.save_knowledge_gap(
                        TENANT_ID,
                        row["conversation_id"],
                        question=row["question"],
                        top_score=row["top_score"],
                        threshold=row["threshold"],
                        reason=row["reason"],
                        trace_id=row["trace_id"],
                        occurred_at=row["occurred_at"],
                    )
            report = run(
                database=database,
                tenant_ids=[TENANT_ID],
                output_dir=output,
                threshold=0.85,
                use_llm=False,
                embed_fn=semantic_embed,
            )
            self.assertEqual(8, report["tenants"][TENANT_ID]["total_gaps"])
            for filename in (
                "knowledge_gap_clusters.json",
                "top_missing_topics.md",
                "top10_manual_review.csv",
            ):
                self.assertTrue((output / filename).is_file(), filename)
            saved = json.loads((output / "knowledge_gap_clusters.json").read_text(encoding="utf-8"))
            self.assertEqual(0.85, saved["similarity_threshold"])
            with (output / "top10_manual_review.csv").open(encoding="utf-8-sig", newline="") as handle:
                review_rows = list(csv.DictReader(handle))
            self.assertEqual(3, len(review_rows))
            self.assertTrue(all(row["review_status"] == "needs_manual_review" for row in review_rows))


if __name__ == "__main__":
    unittest.main()
