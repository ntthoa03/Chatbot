from __future__ import annotations

import inspect
import unittest
from pathlib import Path

from ai_core.interfaces import ChatPort, RetrieverPort
from storage.base import Storage


ROOT = Path(__file__).resolve().parents[1]
HANDOVER_PATH = ROOT / "docs" / "ban-giao-cho-hieu.md"


class H312HandoverTests(unittest.TestCase):
    """Nghiệm thu tài liệu và biển báo code của H3-12."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.handover = HANDOVER_PATH.read_text(encoding="utf-8")

    def test_handover_has_keep_replace_and_build_lists(self) -> None:
        # Definition of Done yêu cầu Hiếu nhìn được ngay phần nào giữ/thay/làm mới.
        for label in ("**Giữ lại**", "**Thay thế**", "**Hiếu làm mới**"):
            with self.subTest(label=label):
                self.assertIn(label, self.handover)

    def test_handover_has_takeover_order_and_acceptance_checklist(self) -> None:
        # Tài liệu phải đủ để tiếp quản tuần tự, không chỉ mô tả kiến trúc chung chung.
        for step in range(1, 8):
            self.assertIn(f"### Bước {step}", self.handover)
        self.assertIn("## 6. Định nghĩa hoàn thành bàn giao", self.handover)
        self.assertIn("## 7. Bẫy cần tránh", self.handover)

    def test_handover_extends_week_two_ingestion_spec_end_to_end(self) -> None:
        # H3-12 phải bổ sung storage/API vào spec tuần 2, không được thành tài liệu rời.
        required_flow = (
            "`docs/ingestion-spec.md` tuần 2",
            "LUỒNG 1 — INGESTION NỀN",
            "LUỒNG 2 — RUNTIME CHAT",
            "LUỒNG 3 — PERSISTENCE",
            "Content API -> parse/chunk -> KnowledgeChunk -> embed -> upsert/xóa vector",
            "RemoteVectorStore",
            "POST /chat",
            "Storage",
            "DB production",
        )
        for item in required_flow:
            with self.subTest(item=item):
                self.assertIn(item, self.handover)

    def test_ingestion_policy_and_three_production_endpoints_are_distinguished(self) -> None:
        # Hiếu phải hiểu Content API, vector query và Postgres là ba trách nhiệm khác nhau.
        for item in (
            "900 ký tự",
            "overlap **120**",
            "minimum **160**",
            "Content API",
            "Vector Query API",
            "AI_API_POSTGRES_DSN",
            "không tự fallback local",
        ):
            with self.subTest(item=item):
                self.assertIn(item, self.handover)

    def test_frozen_ai_signatures_are_documented_and_unchanged(self) -> None:
        # Hai interface tuần 2 là dependency của backend, không được đổi chữ ký âm thầm.
        retrieve_signature = inspect.signature(RetrieverPort.retrieve)
        chat_signature = inspect.signature(ChatPort.chat)
        self.assertEqual(tuple(retrieve_signature.parameters), ("self", "query", "tenant_id", "k"))
        self.assertEqual(retrieve_signature.parameters["k"].default, 5)
        self.assertEqual(tuple(chat_signature.parameters), ("self", "payload"))
        self.assertIn("retrieve(query: str, tenant_id: str, k: int = 5)", self.handover)
        self.assertIn("chat(payload: dict)", self.handover)

    def test_storage_boundary_is_documented_for_postgres_replacement(self) -> None:
        # Hiếu phải thay implementation sau ABC, không đưa SQL vào ai_core hay endpoint.
        abstract_methods = {
            "upsert_tenant",
            "create_conversation",
            "save_message",
            "save_lead",
            "save_usage_event",
            "get_conversation",
            "list_leads",
            "list_usage_events",
            "close",
        }
        self.assertEqual(Storage.__abstractmethods__, abstract_methods)
        self.assertIn("storage/postgres_store.py", self.handover)
        self.assertIn("PostgresStore(Storage)", self.handover)
        self.assertIn("AI_API_STORAGE_BACKEND=postgres", self.handover)

    def test_temporary_code_points_have_todo_and_reason(self) -> None:
        # Bẫy của task: phần tạm phải được đánh dấu ngay trong code, không chỉ ở tài liệu.
        expected_markers = {
            "api/main.py": (
                "TODO(Hieu/Auth)",
                "TODO(Hieu/Security)",
                "TODO(Hieu/Streaming)",
                "TODO(Hieu/Runtime)",
                "TODO(Hieu/Postgres)",
            ),
            "storage/base.py": ("TODO(Hieu/Postgres)",),
            "storage/factory.py": ("TODO(Hieu/Postgres)",),
            "storage/sqlite_store.py": ("TODO(Hieu/Postgres)",),
            "storage/schema.sql": ("TODO(Hieu/Postgres)",),
        }
        for relative_path, markers in expected_markers.items():
            source = (ROOT / relative_path).read_text(encoding="utf-8")
            for marker in markers:
                with self.subTest(relative_path=relative_path, marker=marker):
                    self.assertIn(marker, source)

    def test_handover_explicitly_discloses_demo_limitations(self) -> None:
        # Không được khiến người nhận hiểu nhầm API/SQLite demo đã sẵn sàng production.
        for limitation in (
            "SQLite",
            "public key tĩnh",
            "CORS `*`",
            "giả streaming",
            "**chưa phải production**",
        ):
            with self.subTest(limitation=limitation):
                self.assertIn(limitation, self.handover)


if __name__ == "__main__":
    unittest.main()
