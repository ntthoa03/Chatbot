import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from ai_core.evaluator import EvalConfigError, load_cases
from index_chunks import IndexError_, load_chunks
from ingestion.chat_log_importer import ChatLogImportError
from ingestion.chat_log_router import build_route_groups, route_and_write, split_pairs, split_route_groups


def _pairs(count: int = 20) -> list[dict]:
    return [
        {
            "pair_id": f"qa-{index:03d}", "tenant_id": "tenant_a",
            "question": f"Câu hỏi nghiệp vụ số {index} có nội dung gì?",
            "answer": f"Đây là câu trả lời nghiệp vụ đầy đủ dành cho mục số {index}.",
            "pii_redacted": True, "source_data_label": "TEST/SYNTHETIC",
        }
        for index in range(count)
    ]


def _write_staging(root: Path, pairs: list[dict], tenant_id: str = "tenant_a") -> Path:
    source = root / "pairs.json"
    source.write_text(json.dumps(pairs, ensure_ascii=False), encoding="utf-8")
    audit = {
        "tenant_id": tenant_id, "pii_scan_passed": True,
        "artifacts": {"qa_pairs_redacted_sha256": hashlib.sha256(source.read_bytes()).hexdigest()},
    }
    (root / "audit.json").write_text(json.dumps(audit), encoding="utf-8")
    return source


class H407RoutingTests(unittest.TestCase):
    def test_same_conversation_and_semantic_neighbors_never_split(self) -> None:
        pairs = _pairs(4)
        pairs[0]["source_conversation_ids"] = ["conversation-shared"]
        pairs[1]["source_conversation_ids"] = ["conversation-shared"]
        vectors = [[1.0, 0.0], [0.0, 1.0], [0.7, 0.7], [0.71, 0.69]]
        groups = build_route_groups(pairs, vectors=vectors, semantic_threshold=0.99)
        eval_pairs, knowledge_pairs, _ = split_route_groups(
            groups, "tenant_a", eval_ratio=0.5,
        )
        destinations = {
            pair["pair_id"]: "eval" for pair in eval_pairs
        } | {pair["pair_id"]: "knowledge" for pair in knowledge_pairs}
        self.assertEqual(destinations["qa-000"], destinations["qa-001"])
        self.assertEqual(destinations["qa-002"], destinations["qa-003"])

    def test_incremental_assignment_is_stable_and_conflict_fails_closed(self) -> None:
        groups = [[_pairs(2)[0]], [_pairs(2)[1]]]
        _, _, assignments = split_route_groups(
            groups, "tenant_a", eval_ratio=0.5,
            previous_assignments={"qa-000": "knowledge", "qa-001": "eval"},
        )
        self.assertEqual(assignments, {"qa-000": "knowledge", "qa-001": "eval"})
        with self.assertRaisesRegex(ChatLogImportError, "Rò semantic lịch sử"):
            split_route_groups(
                [[_pairs(2)[0], _pairs(2)[1]]], "tenant_a", eval_ratio=0.5,
                previous_assignments={"qa-000": "knowledge", "qa-001": "eval"},
            )

    def test_split_is_disjoint_complete_and_deterministic(self) -> None:
        pairs = _pairs()
        first = split_pairs(pairs, "tenant_a", eval_ratio=0.25)
        second = split_pairs(pairs, "tenant_a", eval_ratio=0.25)
        eval_ids = {item["pair_id"] for item in first[0]}
        knowledge_ids = {item["pair_id"] for item in first[1]}
        self.assertEqual(first, second)
        self.assertFalse(eval_ids & knowledge_ids)
        self.assertEqual(eval_ids | knowledge_ids, {item["pair_id"] for item in pairs})

    def test_writes_two_physical_stores_with_purpose_guards(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = _write_staging(root, _pairs())
            manifest = route_and_write(source, "tenant_a", root / "routed", eval_ratio=0.25)
            eval_path = root / "routed" / "eval_cases" / "cases.yaml"
            knowledge_path = root / "routed" / "knowledge" / "knowledge_chunks.json"

            eval_cases = load_cases(eval_path)
            chunks = load_chunks(knowledge_path)
            eval_purpose = json.loads((eval_path.parent / "PURPOSE.json").read_text())
            knowledge_purpose = json.loads((knowledge_path.parent / "PURPOSE.json").read_text())

        self.assertEqual(len(eval_cases) + len(chunks), 20)
        self.assertTrue(all(manifest["invariants"].values()))
        self.assertEqual(eval_purpose["purpose"], "evaluation_only")
        self.assertEqual(knowledge_purpose["purpose"], "knowledge_only")

    def test_cross_consumption_fails_and_eval_question_is_not_in_knowledge(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = _write_staging(root, _pairs())
            route_and_write(source, "tenant_a", root / "routed", eval_ratio=0.25)
            eval_path = root / "routed" / "eval_cases" / "cases.yaml"
            knowledge_path = root / "routed" / "knowledge" / "knowledge_chunks.json"
            eval_questions = {case.question for case in load_cases(eval_path)}
            knowledge_content = "\n".join(chunk["content"] for chunk in load_chunks(knowledge_path))
            self.assertFalse(any(question in knowledge_content for question in eval_questions))
            with self.assertRaises((IndexError_, json.JSONDecodeError)):
                load_chunks(eval_path)
            with self.assertRaises(EvalConfigError):
                load_cases(knowledge_path)

    def test_rejects_unredacted_or_cross_tenant_staging(self) -> None:
        for mutation in (
            {"pii_redacted": False},
            {"tenant_id": "tenant_b"},
        ):
            pairs = _pairs(2)
            pairs[0].update(mutation)
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = _write_staging(root, pairs)
                with self.assertRaises(ChatLogImportError):
                    route_and_write(source, "tenant_a", root / "routed")

    def test_rejects_tampered_staging_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = _write_staging(root, _pairs())
            source.write_text(source.read_text(encoding="utf-8") + " ", encoding="utf-8")
            with self.assertRaisesRegex(ChatLogImportError, "hash"):
                route_and_write(source, "tenant_a", root / "routed")


if __name__ == "__main__":
    unittest.main()
