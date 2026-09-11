import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ai_core.models import KnowledgeChunk
from ingestion.chat_log_importer import (
    ChatLogImportError,
    assert_no_pii,
    enrich_pairs_with_llm,
    import_chat_log,
    redact_pii,
)
from index_chunks import load_chunks


class H406ChatLogImporterTests(unittest.TestCase):
    def test_llm_filters_low_value_and_rewrites_without_changing_chunk_contract(self) -> None:
        pairs = [
            {
                "pair_id": "qa-useful", "tenant_id": "tenant_a",
                "question": "Thời gian làm website mất bao lâu?",
                "answer": "Dạ bên em xin chia sẻ là thời gian dự kiến khoảng 10 ngày làm việc ạ.",
                "question_variants": [], "source_conversation_ids": ["test-1"],
                "occurrence_count": 1, "pii_redacted": True,
            },
            {
                "pair_id": "qa-wait", "tenant_id": "tenant_a",
                "question": "Bạn kiểm tra đơn giúp mình nhé?",
                "answer": "Dạ bạn chờ em kiểm tra một chút ạ.",
                "question_variants": [], "source_conversation_ids": ["test-2"],
                "occurrence_count": 1, "pii_redacted": True,
            },
        ]
        captured: dict = {}

        def generate(_config, system, messages):
            captured["system"] = system
            captured["messages"] = messages
            return SimpleNamespace(
                model="test-llm", tokens_in=120, tokens_out=40,
                text=json.dumps({"items": [
                    {"pair_id": "qa-useful", "keep": True,
                     "rewritten_answer": "Dạ, thời gian dự kiến khoảng 10 ngày làm việc ạ.",
                     "reason": "Thông tin thời gian có thể tái sử dụng", "confidence": 0.98},
                    {"pair_id": "qa-wait", "keep": False, "rewritten_answer": "",
                     "reason": "Chỉ là lời hẹn chờ", "confidence": 0.99},
                ]}, ensure_ascii=False),
            )

        with patch("ai_core.config.load_config", return_value=SimpleNamespace()):
            enriched, audit = enrich_pairs_with_llm(
                pairs, "tenant_a", generate_fn=generate, batch_size=10,
            )
        self.assertEqual(len(enriched), 1)
        self.assertEqual(enriched[0]["answer"], "Dạ, thời gian dự kiến khoảng 10 ngày làm việc ạ.")
        self.assertEqual(audit["accepted_pairs"], 1)
        self.assertEqual(audit["rejected_pairs"], 1)
        self.assertEqual(audit["models"], {"test-llm": 1})
        self.assertIn("Giữ cách xưng hô và giọng điệu", captured["system"])
        chunks = __import__(
            "ingestion.chat_log_importer", fromlist=["to_knowledge_chunks"]
        ).to_knowledge_chunks(enriched, "tenant_a", "2026-09-09")
        self.assertEqual(len([KnowledgeChunk.model_validate(item) for item in chunks]), 1)

    def test_llm_missing_decision_fails_closed(self) -> None:
        pair = {
            "pair_id": "qa-one", "tenant_id": "tenant_a", "question": "Có bảo hành không?",
            "answer": "Dạ bên em có chính sách bảo hành mười hai tháng ạ.",
        }

        def generate(*_args):
            return SimpleNamespace(model="test", tokens_in=1, tokens_out=1, text='{"items": []}')

        with patch("ai_core.config.load_config", return_value=SimpleNamespace()):
            with self.assertRaisesRegex(ChatLogImportError, "đủ mọi pair"):
                enrich_pairs_with_llm([pair], "tenant_a", generate_fn=generate)

    def test_pair_export_redacts_all_required_pii_before_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "export.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=[
                    "tenant_id", "conversation_id", "customer_name", "question", "answer"
                ])
                writer.writeheader()
                writer.writerow({
                    "tenant_id": "tenant_a", "conversation_id": "test-001",
                    "customer_name": "Nguyễn Văn Nam",
                    "question": "Tôi là Nguyễn Văn Nam số 0909 123 456 cần hỏi thời gian làm web?",
                    "answer": "Bên em làm trong 10 ngày. Gửi hồ sơ tới nam@example.com nhé.",
                })
            pairs, chunks, audit = import_chat_log(path, "tenant_a", minimum_pairs=1)

        serialized = json.dumps({"pairs": pairs, "chunks": chunks}, ensure_ascii=False)
        self.assertIn("[REDACTED_NAME]", serialized)
        self.assertIn("[REDACTED_PHONE]", serialized)
        self.assertIn("[REDACTED_EMAIL]", serialized)
        self.assertNotIn("Nguyễn Văn Nam", serialized)
        self.assertTrue(audit["pii_scan_passed"])

    def test_message_export_skips_greeting_and_system_then_pairs_adjacent_turns(self) -> None:
        records = [
            {"tenant_id": "tenant_a", "conversation_id": "test-1", "role": "system", "message": "opened"},
            {"tenant_id": "tenant_a", "conversation_id": "test-1", "role": "user", "message": "Xin chào"},
            {"tenant_id": "tenant_a", "conversation_id": "test-1", "role": "assistant", "message": "Xin chào"},
            {"tenant_id": "tenant_a", "conversation_id": "test-1", "role": "user", "message": "Thời gian thiết kế website mất bao lâu?"},
            {"tenant_id": "tenant_a", "conversation_id": "test-1", "role": "assistant", "message": "Thời gian triển khai dự kiến từ 10 đến 14 ngày làm việc."},
        ]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "export.json"
            path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
            pairs, chunks, audit = import_chat_log(path, "tenant_a", minimum_pairs=1)

        self.assertEqual(len(pairs), 1)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(audit["candidate_pairs"], 1)

    def test_similar_questions_cluster_and_best_answer_is_selected(self) -> None:
        rows = [
            {"tenant_id": "tenant_a", "conversation_id": "test-1", "question": "Thiết kế website mất bao lâu?", "answer": "Khoảng 10 ngày làm việc ạ."},
            {"tenant_id": "tenant_a", "conversation_id": "test-2", "question": "Thiết kế website mất bao lâu ạ?", "answer": "Thời gian dự kiến là 10-14 ngày làm việc tùy phạm vi và thời điểm duyệt nội dung ạ."},
        ]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "export.json"
            path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            pairs, _, _ = import_chat_log(path, "tenant_a", minimum_pairs=1)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["occurrence_count"], 2)
        self.assertIn("10-14", pairs[0]["answer"])

    def test_cross_tenant_input_is_rejected(self) -> None:
        rows = [{
            "tenant_id": "tenant_b", "conversation_id": "x", "question": "Dịch vụ này là gì?",
            "answer": "Đây là câu trả lời đủ dài để được giữ lại làm tri thức.",
        }]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "export.json"
            path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ChatLogImportError, "tenant khác"):
                import_chat_log(path, "tenant_a")

    def test_output_chunks_match_contract_and_leak_scanner_fails_closed(self) -> None:
        rows = [{
            "tenant_id": "tenant_a", "conversation_id": "test-x",
            "question": "Quy trình bàn giao mã nguồn thế nào?",
            "answer": "Mã nguồn được bàn giao sau khi nghiệm thu và hoàn tất thanh toán.",
        }]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "export.jsonl"
            path.write_text(json.dumps(rows[0], ensure_ascii=False), encoding="utf-8")
            _, chunks, _ = import_chat_log(path, "tenant_a", minimum_pairs=1)
            chunk_path = Path(temp) / "chunks.json"
            chunk_path.write_text(json.dumps(chunks, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(len(load_chunks(chunk_path)), 1)
        with self.assertRaises(ChatLogImportError):
            assert_no_pii({"answer": "Liên hệ user@example.com"})

    def test_redactor_handles_compact_and_international_phone(self) -> None:
        redacted, stats = redact_pii("Gọi 0912345678 hoặc +84 912 345 678")
        self.assertNotRegex(redacted, r"0912345678|912 345 678")
        self.assertEqual(stats.phones, 2)

    def test_redactor_does_not_hide_normal_business_phrase_after_em(self) -> None:
        text = "Bên em bắt đầu bằng bước khảo sát rồi gửi báo giá cho khách."
        redacted, stats = redact_pii(text)
        self.assertEqual(redacted, text)
        self.assertEqual(stats.names, 0)

    def test_csv_preserves_multiline_quoted_answer(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "multiline.csv"
            path.write_text(
                'tenant_id;conversation_id;question;answer\n'
                'tenant_a;test-1;"Quy trình làm website thế nào?";'
                '"Bước một là khảo sát nhu cầu.\nBước hai là duyệt giao diện."\n',
                encoding="utf-8",
            )
            pairs, _, _ = import_chat_log(path, "tenant_a", minimum_pairs=1)
        self.assertIn("\n", pairs[0]["answer"])
        self.assertIn("Bước hai", pairs[0]["answer"])


if __name__ == "__main__":
    unittest.main()
