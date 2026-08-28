"""Definition of Done và bẫy cách ly tenant của H3-04."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import unittest

from storage import (
    SQLiteStore,
    Storage,
    StorageConfigurationError,
    StorageValidationError,
    build_storage,
)
from storage.sqlite_store import SCHEMA_PATH


ROOT = Path(__file__).resolve().parent.parent
REQUIRED_TABLES = {"tenants", "conversations", "messages", "leads", "usage_events"}


class H304SchemaTests(unittest.TestCase):
    def test_schema_has_exactly_five_application_tables_and_all_have_tenant_id(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        self.assertEqual(REQUIRED_TABLES, tables)
        for table in REQUIRED_TABLES:
            with self.subTest(table=table):
                columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                self.assertIn("tenant_id", columns)
        connection.close()

    def test_sqlite_store_implements_storage_interface(self) -> None:
        with SQLiteStore(":memory:") as storage:
            self.assertIsInstance(storage, Storage)

    def test_factory_selects_sqlite_without_api_knowing_implementation(self) -> None:
        with build_storage(backend="sqlite", sqlite_path=":memory:") as storage:
            self.assertIsInstance(storage, SQLiteStore)

    def test_factory_rejects_unknown_backend(self) -> None:
        with self.assertRaises(StorageConfigurationError):
            build_storage(backend="unknown")


class H304RoundTripTests(unittest.TestCase):
    def setUp(self) -> None:
        self.storage = SQLiteStore(":memory:")
        self.storage.upsert_tenant("tenant_a", "Tenant A")
        self.storage.upsert_tenant("tenant_b", "Tenant B")
        self.storage.create_conversation("tenant_a", "conversation-01")

    def tearDown(self) -> None:
        self.storage.close()

    def test_conversation_lead_and_usage_are_saved_and_read_back(self) -> None:
        self.storage.save_message("tenant_a", "conversation-01", "user", "Tôi cần tư vấn")
        self.storage.save_message(
            "tenant_a",
            "conversation-01",
            "assistant",
            "Dạ, em hỗ trợ anh/chị ạ.",
            trace_id="trace-01",
        )
        self.storage.save_lead(
            "tenant_a",
            "conversation-01",
            name="An",
            phone="0912345678",
        )
        self.storage.save_usage_event(
            "tenant_a",
            "conversation-01",
            trace_id="trace-01",
            usage={
                "model": "test-model",
                "tokens_in": 100,
                "tokens_out": 20,
                "cached_tokens_in": 5,
                "cache_write_tokens_in": 2,
                "cost_usd": 0.001,
                "latency_ms": 300,
            },
        )
        conversation = self.storage.get_conversation("tenant_a", "conversation-01")
        self.assertIsNotNone(conversation)
        self.assertEqual(["user", "assistant"], [row["role"] for row in conversation["messages"]])
        self.assertEqual("0912345678", self.storage.list_leads("tenant_a")[0]["phone"])
        usage = self.storage.list_usage_events("tenant_a")[0]
        self.assertEqual("trace-01", usage["trace_id"])
        self.assertEqual(0.001, usage["cost_usd"])

    def test_wrong_or_missing_tenant_never_returns_other_tenant_data(self) -> None:
        self.storage.save_message("tenant_a", "conversation-01", "user", "Dữ liệu tenant A")
        self.assertIsNone(self.storage.get_conversation("tenant_b", "conversation-01"))
        self.assertEqual([], self.storage.list_leads("tenant_b"))
        self.assertEqual([], self.storage.list_usage_events("tenant_b"))
        for invalid in (None, "", " ", "../tenant_a", "TENANT A"):
            with self.subTest(invalid=invalid), self.assertRaises(StorageValidationError):
                self.storage.get_conversation(invalid, "conversation-01")  # type: ignore[arg-type]

    def test_conversation_id_cannot_be_reused_by_another_tenant(self) -> None:
        with self.assertRaises(StorageValidationError):
            self.storage.create_conversation("tenant_b", "conversation-01")


class H304AcceptanceArtifactTests(unittest.TestCase):
    def test_demo_report_is_explicitly_synthetic_and_passed(self) -> None:
        report_path = ROOT / "outputs" / "h3_04" / "acceptance.json"
        self.assertTrue(report_path.is_file())
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertTrue(report["synthetic"])
        self.assertTrue(report["passed"])
        self.assertEqual(2, report["checks"]["message_count"])
        self.assertTrue(report["checks"]["wrong_tenant_returns_none"])

    def test_demo_database_can_be_reopened_and_read_from_disk(self) -> None:
        report_path = ROOT / "outputs" / "h3_04" / "acceptance.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        database_path = ROOT / report["database"]

        # Đóng/mở bằng store mới để chứng minh dữ liệu đã được ghi thật xuống SQLite.
        with SQLiteStore(database_path) as storage:
            conversation = storage.get_conversation(
                report["tenant_id"], report["conversation_id"]
            )
            self.assertIsNotNone(conversation)
            self.assertEqual(2, len(conversation["messages"]))
            self.assertEqual(
                1,
                len(
                    storage.list_leads(
                        report["tenant_id"], report["conversation_id"]
                    )
                ),
            )
            self.assertEqual(
                1,
                len(
                    storage.list_usage_events(
                        report["tenant_id"], report["conversation_id"]
                    )
                ),
            )


if __name__ == "__main__":
    unittest.main()
