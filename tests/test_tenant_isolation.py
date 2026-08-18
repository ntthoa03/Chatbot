"""H2-06 — security tests for strict tenant isolation and default deny."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import numpy as np
from pydantic import ValidationError

from ai_core.chat import chat
from ai_core.config import ConfigError, load_config, validate_tenant_id
from ai_core.models import ChatRequest
from ai_core.retriever import RetrieverError, retrieve
from ai_core.vector_store import LocalNumpyVectorStore, RemoteVectorStore, VectorStoreError
from index_chunks import build_index, save_index


ROOT = Path(__file__).resolve().parents[1]
MIMA = "mima_internal"
HYHY = "phongkham_hyhy"


def two_dimensional_embed(texts: list[str], **_: object) -> list[list[float]]:
    return [
        [float(text.casefold().count("website")), float(text.casefold().count("phòng khám"))]
        for text in texts
    ]


class TenantIdentifierBoundaryTests(unittest.TestCase):
    # Nhóm test này chứng minh tenant thiếu, rỗng, sai hoặc chưa đăng ký đều phải lỗi.
    def test_two_registered_tenants_load_successfully(self) -> None:
        self.assertEqual(load_config(MIMA, 1).tenant_id, MIMA)
        self.assertEqual(load_config(HYHY, 1).tenant_id, HYHY)

    def test_unknown_tenant_is_an_error_not_an_empty_result(self) -> None:
        embed = Mock(return_value=[[1.0]])
        with self.assertRaisesRegex(RetrieverError, "chưa được đăng ký"):
            retrieve("website", "tenant_khong_ton_tai", embed_fn=embed)
        embed.assert_not_called()

    def test_malformed_and_path_traversal_tenant_ids_are_rejected(self) -> None:
        for value in ("../mima_internal", "..\\mima_internal", "MIMA", "tenant id", "/root"):
            with self.subTest(value=value):
                with self.assertRaises(ConfigError):
                    validate_tenant_id(value)

    def test_missing_tenant_argument_raises_type_error(self) -> None:
        with self.assertRaises(TypeError):
            retrieve("website")  # type: ignore[call-arg]

    def test_none_empty_and_whitespace_tenant_are_rejected_before_embedding(self) -> None:
        for value in (None, "", "   "):
            embed = Mock(return_value=[[1.0]])
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    retrieve("website", value, embed_fn=embed)  # type: ignore[arg-type]
                embed.assert_not_called()

    def test_chat_contract_rejects_missing_empty_and_malformed_tenant(self) -> None:
        base = {
            "conversation_id": "7fd40ca2-c7e9-43e4-9bb0-3613d71ca9aa",
            "message": "Xin chào",
            "config_version": 1,
        }
        for payload in (
            base,
            {**base, "tenant_id": ""},
            {**base, "tenant_id": "../mima_internal"},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    ChatRequest.model_validate(payload)

    def test_public_chat_rejects_missing_and_empty_tenant(self) -> None:
        base = {
            "conversation_id": "11174ca6-55e6-4545-9f10-63df1263433e",
            "message": "Xin chào",
            "config_version": 1,
        }
        for payload in (base, {**base, "tenant_id": ""}):
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    chat(payload)

    def test_chat_unknown_tenant_fails_before_rag_or_model(self) -> None:
        with self.assertRaises(ConfigError):
            chat(
                {
                    "tenant_id": "tenant_khong_ton_tai",
                    "conversation_id": "c41199d0-6a58-47dc-8c08-1cf4b36357d5",
                    "message": "Xin chào",
                    "config_version": 1,
                }
            )


class RealIndexIsolationTests(unittest.TestCase):
    # Dùng vector có sẵn để kiểm tra hai index thật mà không gọi API embedding bên ngoài.
    @staticmethod
    def _first_vector_embed(index_dir: Path):
        vector = np.load(index_dir / "vectors.npy", allow_pickle=False)[0].tolist()

        def embed(_texts: list[str], **_: object) -> list[list[float]]:
            return [vector]

        return embed

    @staticmethod
    def _ids(index_dir: Path) -> set[str]:
        metadata = json.loads((index_dir / "metadata.json").read_text(encoding="utf-8"))
        return {item["chunk_id"] for item in metadata}

    def test_each_real_tenant_index_returns_only_its_own_chunks(self) -> None:
        mima_dir = ROOT / "index"
        hyhy_dir = ROOT / "outputs" / "h2_04" / "index_phongkham_hyhy"
        mima_ids = self._ids(mima_dir)
        hyhy_ids = self._ids(hyhy_dir)
        self.assertTrue(mima_ids.isdisjoint(hyhy_ids))

        mima_results = retrieve(
            "kiểm tra isolation",
            MIMA,
            k=5,
            threshold=0.0,
            relative_score_margin=1.0,
            embed_fn=self._first_vector_embed(mima_dir),
            backend="local",
        )
        hyhy_results = retrieve(
            "kiểm tra isolation",
            HYHY,
            k=5,
            threshold=0.0,
            relative_score_margin=1.0,
            embed_fn=self._first_vector_embed(hyhy_dir),
            backend="local",
        )
        self.assertTrue(mima_results)
        self.assertTrue(hyhy_results)
        # Mỗi tập kết quả phải là tập con của đúng index và không giao với tenant còn lại.
        self.assertTrue({item["chunk_id"] for item in mima_results} <= mima_ids)
        self.assertTrue({item["chunk_id"] for item in hyhy_results} <= hyhy_ids)
        self.assertFalse({item["chunk_id"] for item in mima_results} & hyhy_ids)
        self.assertFalse({item["chunk_id"] for item in hyhy_results} & mima_ids)
        self.assertTrue(all("mimadigi.com" in str(item["url"]) for item in mima_results))
        self.assertTrue(all("phongkhamhyhy.com" in str(item["url"]) for item in hyhy_results))

    def test_valid_but_wrong_tenant_against_other_index_returns_no_rows(self) -> None:
        mima_dir = ROOT / "index"
        hyhy_dir = ROOT / "outputs" / "h2_04" / "index_phongkham_hyhy"
        # Tenant hợp lệ nhưng trỏ nhầm kho chỉ được trả rỗng, không được lộ chunk của kho đó.
        self.assertEqual(
            retrieve(
                "website",
                HYHY,
                k=5,
                threshold=0.0,
                relative_score_margin=1.0,
                index_dir=mima_dir,
                embed_fn=self._first_vector_embed(mima_dir),
                backend="local",
            ),
            [],
        )
        self.assertEqual(
            retrieve(
                "phòng khám",
                MIMA,
                k=5,
                threshold=0.0,
                relative_score_margin=1.0,
                index_dir=hyhy_dir,
                embed_fn=self._first_vector_embed(hyhy_dir),
                backend="local",
            ),
            [],
        )

    def test_mixed_index_is_filtered_before_ranking_for_both_tenants(self) -> None:
        chunks = [
            {"tenant_id": MIMA, "chunk_id": "mima-web", "content": "website website", "metadata": {"url": "https://mimadigi.com/web"}},
            {"tenant_id": MIMA, "chunk_id": "mima-seo", "content": "website", "metadata": {"url": "https://mimadigi.com/seo"}},
            {"tenant_id": HYHY, "chunk_id": "hyhy-clinic", "content": "phòng khám phòng khám", "metadata": {"url": "https://phongkhamhyhy.com/kham"}},
            {"tenant_id": HYHY, "chunk_id": "hyhy-doctor", "content": "phòng khám", "metadata": {"url": "https://phongkhamhyhy.com/bac-si"}},
        ]
        records, _, _, _ = build_index(
            chunks,
            {},
            embed_fn=two_dimensional_embed,
            model="test-model",
            provider="test",
        )
        with tempfile.TemporaryDirectory() as temporary:
            index_dir = Path(temporary)
            save_index(records, index_dir, model="test-model", provider="test")
            mima_results = retrieve(
                "website",
                MIMA,
                k=10,
                threshold=0.0,
                relative_score_margin=1.0,
                index_dir=index_dir,
                embed_fn=two_dimensional_embed,
                model="test-model",
            )
            hyhy_results = retrieve(
                "phòng khám",
                HYHY,
                k=10,
                threshold=0.0,
                relative_score_margin=1.0,
                index_dir=index_dir,
                embed_fn=two_dimensional_embed,
                model="test-model",
            )
        self.assertEqual({item["chunk_id"] for item in mima_results}, {"mima-web", "mima-seo"})
        self.assertEqual({item["chunk_id"] for item in hyhy_results}, {"hyhy-clinic", "hyhy-doctor"})


class VectorStoreBoundaryTests(unittest.TestCase):
    # Kiểm tra lớp lưu trữ độc lập để không thể vượt bảo vệ bằng cách bỏ qua retriever.
    def test_local_store_rejects_empty_tenant_before_loading_index(self) -> None:
        loader = Mock(side_effect=AssertionError("index không được load"))
        store = LocalNumpyVectorStore(ROOT / "index", loader)
        for value in (None, "", "   ", "../mima_internal", "MIMA"):
            with self.subTest(value=value):
                with self.assertRaises(VectorStoreError):
                    store.query([1.0], tenant_id=value, k=1)  # type: ignore[arg-type]
        loader.assert_not_called()

    def test_remote_store_rejects_empty_tenant_before_network(self) -> None:
        transport = Mock(return_value={"matches": []})
        store = RemoteVectorStore(
            endpoint="https://vector.invalid/query",
            provider="test",
            model="test-model",
            transport=transport,
        )
        for value in (None, "", "   ", "../mima_internal", "MIMA"):
            with self.subTest(value=value):
                with self.assertRaises(VectorStoreError):
                    store.query([1.0], tenant_id=value, k=1)  # type: ignore[arg-type]
        transport.assert_not_called()

    def test_local_store_filters_foreign_and_missing_tenant_before_ranking(self) -> None:
        vectors = np.asarray([[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]], dtype="float32")
        metadata = [
            {"tenant_id": MIMA, "chunk_id": "allowed", "content": "đúng tenant", "metadata": {}},
            {"tenant_id": HYHY, "chunk_id": "foreign", "content": "sai tenant", "metadata": {}},
            {"chunk_id": "missing", "content": "thiếu tenant", "metadata": {}},
        ]
        loader = Mock(return_value=(vectors, metadata, {"dimension": 2, "record_count": 3}))
        store = LocalNumpyVectorStore(ROOT / "unused", loader)
        results = store.query([1.0, 0.0], tenant_id=MIMA, k=10)
        self.assertEqual([item["chunk_id"] for item in results], ["allowed"])

    def test_remote_request_has_double_filter_and_drops_foreign_response_rows(self) -> None:
        captured: dict = {}

        def transport(_endpoint: str, payload: dict, _headers: dict, _timeout: float) -> dict:
            captured.update(payload)
            return {
                "matches": [
                    {"id": "mima-ok", "score": 0.8, "metadata": {"tenant_id": MIMA, "content": "MIMA"}},
                    {"id": "hyhy-secret", "score": 0.99, "metadata": {"tenant_id": HYHY, "content": "Hỷ Hỷ"}},
                    {"id": "missing", "score": 1.0, "metadata": {"content": "không tenant"}},
                ]
            }

        store = RemoteVectorStore("https://vector.invalid/query", "test", "test-model", transport=transport)
        results = store.query([1.0], tenant_id=MIMA, k=5)
        # Remote request phải có hai khóa cách ly và response chỉ giữ đúng tenant MIMA.
        self.assertEqual(captured["namespace"], MIMA)
        self.assertEqual(captured["filter"], {"tenant_id": {"$eq": MIMA}})
        self.assertEqual([item["chunk_id"] for item in results], ["mima-ok"])


if __name__ == "__main__":
    unittest.main()
