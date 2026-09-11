from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ai_core.retriever import retrieve, use_index_dir
from index_chunks import build_index, save_cache, save_index
from ingestion.document_test_index import DocumentTestIndexError, build_document_test_index


def topic_embed(texts, **_kwargs):
    return [
        [1.0 if "combo" in text.casefold() else 0.0,
         1.0 if "cũ" in text.casefold() else 0.0]
        for text in texts
    ]


def chunk(chunk_id: str, content: str, tenant_id: str = "mima_internal") -> dict:
    return {
        "tenant_id": tenant_id,
        "chunk_id": chunk_id,
        "content": content,
        "metadata": {
            "url": f"https://test.local/{chunk_id}",
            "title": chunk_id,
            "type": "pricing",
            "updated_at": "2026-09-09",
            "source": "document",
            "source_priority": 80,
            "source_confidence": 1.0,
        },
    }


class H404UiDocumentIndexTests(unittest.TestCase):
    def _base_index(self, root: Path) -> Path:
        base = root / "base"
        records, cache, _, _ = build_index(
            [chunk("base", "Bảng giá cũ")], {}, embed_fn=topic_embed,
            model="topic-test", provider="test",
        )
        save_index(records, base, model="topic-test", provider="test")
        save_cache(base / "embedding_cache.json", cache)
        return base

    def test_builds_isolated_index_and_context_routes_retrieval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = self._base_index(root)
            base_before = (base / "metadata.json").read_bytes()
            output = root / "ui-test"
            receipt = build_document_test_index(
                "mima_internal", [chunk("new", "Combo marketing giá 6 triệu")],
                base_index_dir=base, output_dir=output, embed_fn=topic_embed,
            )
            with use_index_dir(output):
                results = retrieve(
                    "combo marketing", "mima_internal", embed_fn=topic_embed,
                    model="topic-test", provider="test", threshold=0.1,
                )

            self.assertEqual((receipt["base_chunks"], receipt["document_chunks"]), (1, 1))
            self.assertEqual(receipt["total_chunks"], 2)
            self.assertEqual(results[0]["chunk_id"], "new")
            self.assertEqual((base / "metadata.json").read_bytes(), base_before)

    def test_rejects_cross_tenant_document(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = self._base_index(root)
            with self.assertRaisesRegex(DocumentTestIndexError, "tenant khác"):
                build_document_test_index(
                    "mima_internal", [chunk("foreign", "Combo", "tenant_other")],
                    base_index_dir=base, output_dir=root / "ui-test", embed_fn=topic_embed,
                )


if __name__ == "__main__":
    unittest.main()
