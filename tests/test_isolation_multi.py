"""H3-10 — kiểm tra cách ly dữ liệu tự động cho toàn bộ 5 tenant.

Bộ test cố ý sinh cặp chéo từ danh sách tenant thay vì viết tay. Với 5 tenant,
ma trận có 5 x 4 = 20 phép thử có hướng (A hỏi dữ liệu đặc thù của B).
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import numpy as np
from pydantic import ValidationError

from ai_core.cache import CacheError, SemanticResponseCache
from ai_core.config import load_config
from ai_core.models import ChatRequest
from ai_core.retriever import retrieve
from index_chunks import build_index, save_index


ROOT = Path(__file__).resolve().parents[1]
TENANTS = {
    "mima_internal": {
        "brand": "MIMA",
        "probe": "gói Website Basic giá 2 triệu",
        "index": ROOT / "index",
    },
    "phongkham_hyhy": {
        "brand": "Hỷ Hỷ",
        "probe": "bác sĩ Hồ Hữu Thật khám nội thần kinh",
        "index": ROOT / "outputs" / "h2_04" / "index_phongkham_hyhy",
    },
    "bat_dong_san_phuoc_thinh": {
        "brand": "Phước Thịnh",
        "probe": "nhà hẻm Hưng Phú phường 10 quận 8",
        "index": ROOT / "outputs" / "h3_01" / "index_bat_dong_san_phuoc_thinh",
    },
    "giao_duc_haiyan": {
        "brand": "HaiYan",
        "probe": "lớp tiếng Trung cho nhân viên logistics",
        "index": ROOT / "outputs" / "h3_01" / "index_giao_duc_haiyan",
    },
    "thuc_pham_thien_minh": {
        "brand": "Thiện Minh",
        "probe": "bánh gạo lứt Ohsawa vị hạt điều rong biển",
        "index": ROOT / "outputs" / "h3_01" / "index_thuc_pham_thien_minh",
    },
}
TENANT_IDS = tuple(TENANTS)
CROSS_PAIRS = tuple(
    (request_tenant, foreign_tenant)
    for request_tenant in TENANT_IDS
    for foreign_tenant in TENANT_IDS
    if request_tenant != foreign_tenant
)


def _constant_embed(texts: list[str], **_: object) -> list[list[float]]:
    """Cho mọi câu cùng điểm để chứng minh bộ lọc tenant chạy trước xếp hạng."""

    return [[1.0, 0.0] for _text in texts]


def _cacheable_response(tenant_id: str) -> dict:
    return {
        "reply": f"CACHE_MARKER::{tenant_id}",
        "sources": [{"chunk_id": f"{tenant_id}-source", "url": "https://example.invalid"}],
        "tool_calls": [],
        "need_human": False,
        "lead_captured": None,
        "guardrail": {"blocked": False},
    }


class FiveTenantCrossIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Tạo một index trộn cố ý: mọi vector giống nhau nên nếu lọc tenant sai,
        # chunk của tenant khác chắc chắn có cơ hội lọt vào kết quả.
        cls._temporary = tempfile.TemporaryDirectory()
        cls.mixed_index = Path(cls._temporary.name)
        chunks = [
            {
                "tenant_id": tenant_id,
                "chunk_id": f"{tenant_id}-distinctive",
                "content": f"{data['brand']} | {data['probe']}",
                "metadata": {"url": f"https://{tenant_id}.invalid/distinctive"},
            }
            for tenant_id, data in TENANTS.items()
        ]
        records, _, _, _ = build_index(
            chunks,
            {},
            embed_fn=_constant_embed,
            model="h3-10-test-model",
            provider="offline",
        )
        save_index(
            records,
            cls.mixed_index,
            model="h3-10-test-model",
            provider="offline",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def test_exactly_20_directed_cross_tenant_pairs_are_generated(self) -> None:
        self.assertEqual(5, len(TENANT_IDS))
        self.assertEqual(20, len(CROSS_PAIRS))
        self.assertEqual(20, len(set(CROSS_PAIRS)))

    def test_all_20_cross_queries_return_only_request_tenant_chunks(self) -> None:
        """Tenant A hỏi thông tin riêng của B nhưng chỉ được nhận chunk của A."""

        for request_tenant, foreign_tenant in CROSS_PAIRS:
            with self.subTest(request_tenant=request_tenant, foreign_tenant=foreign_tenant):
                results = retrieve(
                    TENANTS[foreign_tenant]["probe"],
                    request_tenant,
                    k=10,
                    threshold=0.0,
                    relative_score_margin=1.0,
                    index_dir=self.mixed_index,
                    embed_fn=_constant_embed,
                    model="h3-10-test-model",
                    backend="local",
                )
                self.assertTrue(results)
                # Retriever cố ý không trả tenant_id ra API công khai; chunk_id duy nhất
                # của index kiểm thử là bằng chứng trực tiếp rằng chỉ partition A được giữ.
                self.assertEqual(
                    {f"{request_tenant}-distinctive"},
                    {item["chunk_id"] for item in results},
                )
                result_text = json.dumps(results, ensure_ascii=False).casefold()
                self.assertNotIn(TENANTS[foreign_tenant]["brand"].casefold(), result_text)

    def test_all_20_cross_cache_lookups_miss_foreign_partitions(self) -> None:
        """Cùng câu/vector của B không được hit cache khi scope request là A."""

        cache = SemanticResponseCache(similarity_threshold=0.92)
        vectors: dict[str, list[float]] = {}
        for index, tenant_id in enumerate(TENANT_IDS):
            vector = [0.0] * len(TENANT_IDS)
            vector[index] = 1.0
            vectors[tenant_id] = vector
            cache.put(
                tenant_id=tenant_id,
                config_version=1,
                question=TENANTS[tenant_id]["probe"],
                vector=vector,
                response=_cacheable_response(tenant_id),
            )

        for request_tenant, foreign_tenant in CROSS_PAIRS:
            with self.subTest(request_tenant=request_tenant, foreign_tenant=foreign_tenant):
                lookup = cache.lookup(
                    tenant_id=request_tenant,
                    config_version=1,
                    question=TENANTS[foreign_tenant]["probe"],
                    vector=vectors[foreign_tenant],
                )
                self.assertFalse(lookup.hit)
                self.assertIsNone(lookup.response)

    def test_each_real_index_contains_only_its_owner(self) -> None:
        """Kiểm tra chính 5 index đang bàn giao, không chỉ index giả lập của test."""

        chunk_sets: dict[str, set[str]] = {}
        for tenant_id, data in TENANTS.items():
            with self.subTest(tenant_id=tenant_id):
                index_dir = data["index"]
                metadata = json.loads((index_dir / "metadata.json").read_text(encoding="utf-8"))
                manifest = json.loads((index_dir / "manifest.json").read_text(encoding="utf-8"))
                vectors = np.load(index_dir / "vectors.npy", allow_pickle=False)
                self.assertTrue(metadata)
                self.assertEqual({tenant_id}, {row["tenant_id"] for row in metadata})
                self.assertEqual([tenant_id], manifest["tenants"])
                self.assertEqual(len(metadata), vectors.shape[0])
                chunk_sets[tenant_id] = {row["chunk_id"] for row in metadata}

        for left, right in CROSS_PAIRS:
            with self.subTest(left=left, right=right):
                self.assertTrue(chunk_sets[left].isdisjoint(chunk_sets[right]))

    def test_loaded_config_never_contains_another_tenant_brand(self) -> None:
        for tenant_id in TENANT_IDS:
            with self.subTest(tenant_id=tenant_id):
                config = load_config(tenant_id)
                self.assertEqual(tenant_id, config.tenant_id)
                # Chỉ kiểm tra dữ liệu nhận diện/kinh doanh tenant. Guardrail dùng chung có
                # thể chứa tên tenant khác như một mẫu phát hiện tấn công, không phải rò dữ liệu.
                identity_payload = {
                    "tenant_id": config.tenant_id,
                    "persona": config.persona.model_dump(),
                    "contact": config.contact.model_dump(),
                    "lead": config.lead.model_dump(),
                    "pricing": config.pricing.model_dump(),
                }
                serialized = json.dumps(identity_payload, ensure_ascii=False).casefold()
                for foreign_tenant in TENANT_IDS:
                    if foreign_tenant != tenant_id:
                        self.assertNotIn(
                            TENANTS[foreign_tenant]["brand"].casefold(),
                            serialized,
                            f"config {tenant_id} chứa thương hiệu {foreign_tenant}",
                        )


class TenantIdFailClosedTests(unittest.TestCase):
    def test_missing_tenant_id_is_rejected_by_public_contract(self) -> None:
        with self.assertRaises(ValidationError):
            ChatRequest.model_validate(
                {
                    "conversation_id": "4be3f056-e37b-43c0-8e4f-da486b5d9b46",
                    "message": "xin chào",
                    "config_version": 1,
                }
            )

    def test_retriever_missing_empty_and_whitespace_tenant_fail_before_embedding(self) -> None:
        with self.assertRaises(TypeError):
            retrieve("xin chào")  # type: ignore[call-arg]
        for value in (None, "", "   "):
            embed = Mock(return_value=[[1.0, 0.0]])
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    retrieve("xin chào", value, embed_fn=embed)  # type: ignore[arg-type]
                embed.assert_not_called()

    def test_cache_missing_empty_and_whitespace_tenant_fail_closed(self) -> None:
        cache = SemanticResponseCache()
        with self.assertRaises(TypeError):
            cache.lookup(config_version=1, question="xin chào", vector=[1.0])  # type: ignore[call-arg]
        for value in (None, "", "   "):
            with self.subTest(value=value):
                with self.assertRaises(CacheError):
                    cache.lookup(
                        tenant_id=value,
                        config_version=1,
                        question="xin chào",
                        vector=[1.0],
                    )


if __name__ == "__main__":
    unittest.main()
