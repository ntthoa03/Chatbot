import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ai_core import retriever as retriever_module
from eval.run_h4_05 import (
    audit_report_index,
    chat_for_index,
    conclusion,
    load_expected_evidence,
    prepare_augmented_chunks,
)
from eval.preflight_h4_05 import run_preflight, save_preflight


ROOT = Path(__file__).resolve().parent.parent


class H405ExperimentTests(unittest.TestCase):
    def test_expected_evidence_is_loaded_from_dataset_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "evidence.yaml"
            path.write_text(
                'REAL-01: "phí giao hàng là 20.000đ"\nREAL-02: "bảo hành 12 tháng"\n',
                encoding="utf-8",
            )
            evidence = load_expected_evidence(path)
        self.assertEqual(set(evidence), {"REAL-01", "REAL-02"})
        self.assertNotIn("H405-08", evidence)

    def test_free_preflight_passes_and_writes_auditable_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            "os.environ", {"GEMINI_API_KEY": "test-key"}
        ):
            output = Path(temp)
            report = run_preflight(
                cases_path=ROOT / "eval" / "cases_h4_05_test.yaml",
                document_chunks=ROOT / "outputs" / "h4_05" / "test-document-chunks.json",
                base_index=ROOT / "index",
                output=output,
                tenant_id="mima_internal",
                config_version=1,
            )
            paths = save_preflight(report, output)
            self.assertTrue(report["passed"])
            self.assertEqual(report["estimated"]["chat_calls"], 24)
            self.assertTrue(all(path.exists() for path in paths))

    def test_preflight_rejects_pending_ocr_review_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            "os.environ", {"GEMINI_API_KEY": "test-key"}
        ):
            root = Path(temp)
            pending = root / "document-chunks.ocr-review.json"
            pending.write_text(
                (ROOT / "outputs" / "h4_05" / "test-document-chunks.json").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )
            report = run_preflight(
                cases_path=ROOT / "eval" / "cases_h4_05_test.yaml",
                document_chunks=pending,
                base_index=ROOT / "index",
                output=root / "out",
                tenant_id="mima_internal",
                config_version=1,
            )
        self.assertFalse(report["passed"])
        failed = {item["name"] for item in report["checks"] if not item["passed"]}
        self.assertIn("not_pending_ocr_review", failed)

    def test_prepare_augmented_chunks_keeps_baseline_and_appends_documents(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            metadata = root / "metadata.json"
            added = root / "added.json"
            output = root / "combined.json"
            metadata.write_text(json.dumps([{
                "tenant_id": "mima_internal", "chunk_id": "base", "content": "baseline",
                "content_hash": "ignored", "metadata": {"url": "https://a.test", "title": "A", "type": "faq", "updated_at": "2026-01-01"},
            }]), encoding="utf-8")
            added.write_text(json.dumps([{
                "tenant_id": "mima_internal", "chunk_id": "new", "content": "document",
                "metadata": {"url": "https://b.test", "title": "B", "type": "pricing", "updated_at": "2026-01-02"},
            }]), encoding="utf-8")

            base_count, added_count = prepare_augmented_chunks(metadata, added, output)
            combined = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual((base_count, added_count, len(combined)), (1, 1, 2))
        self.assertNotIn("content_hash", combined[0])

    def test_conclusion_separates_data_gain_from_technical_gap(self) -> None:
        self.assertTrue(conclusion(0.2, 0.2).startswith("DATA_BOTTLENECK"))
        self.assertTrue(conclusion(0.0, 0.2).startswith("TECHNICAL_OR_POLICY_BOTTLENECK"))

    def test_index_context_is_bound_inside_each_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            baseline = root / "baseline"
            post = root / "post"

            def probe(payload: dict) -> dict:
                return {
                    "marker": payload["marker"],
                    "index_dir": retriever_module._INDEX_DIR_OVERRIDE.get(),
                }

            baseline_chat = chat_for_index(baseline, chat_fn=probe)
            post_chat = chat_for_index(post, chat_fn=probe)
            calls = [
                (baseline_chat if index % 2 == 0 else post_chat, {"marker": index})
                for index in range(100)
            ]

            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(lambda item: item[0](item[1]), calls))

        for index, result in enumerate(results):
            expected = baseline.resolve() if index % 2 == 0 else post.resolve()
            self.assertEqual(result["index_dir"], str(expected))
        self.assertIsNone(retriever_module._INDEX_DIR_OVERRIDE.get())

    def test_index_audit_detects_cross_arm_and_missing_expected_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            index_dir = Path(temp)
            (index_dir / "metadata.json").write_text(
                json.dumps([{"chunk_id": "base"}]),
                encoding="utf-8",
            )
            report = SimpleNamespace(
                results=[SimpleNamespace(id="H405-08", trace_id="trace-1")]
            )
            trace = {
                "retrieval": {
                    "chunks": [
                        {"chunk_id": "document-only", "content": "Nội dung không đúng"}
                    ]
                }
            }
            with patch("eval.run_h4_05.find_trace", return_value=trace):
                audit = audit_report_index(
                    report,
                    index_dir,
                    forbidden_chunk_ids={"document-only"},
                    expected_case_evidence={"H405-08": "tối thiểu 20 trang nội dung"},
                )

        self.assertFalse(audit["passed"])
        self.assertEqual(audit["expected_evidence_hits"], {"H405-08": False})
        self.assertEqual(len(audit["violations"]), 3)


if __name__ == "__main__":
    unittest.main()
