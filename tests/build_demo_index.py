"""
Dựng index DEMO (offline, dùng bag-of-words embedder giả) để test retriever.py
khi chưa có OPENAI_API_KEY thật.

Chạy: python tests/build_demo_index.py

⚠️ Index sinh ra ở đây CHỈ để demo/test. Trước khi triển khai thật, XOÁ
index/ này và chạy `python index_chunks.py` với OPENAI_API_KEY thật để có
index bằng embedding ngữ nghĩa thật.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fake_bow_embedder import bow_embed_texts  # noqa: E402

from index_chunks import build_index, load_chunks, save_index  # noqa: E402


def main() -> None:
    chunks = load_chunks(REPO_ROOT / "seed_chunks.json")
    records, _cache, n_new, n_cached = build_index(chunks, {}, embed_fn=bow_embed_texts, model="bow-test-only")
    save_index(records, REPO_ROOT / "index")
    print(f"[DEMO — BOW, KHÔNG PHẢI EMBEDDING THẬT] Đã dựng index: {len(records)} chunk ({n_new} mới, {n_cached} cache).")
    print("⚠️  Đây là index test offline. Chạy `python index_chunks.py` với OPENAI_API_KEY thật trước khi triển khai.")


if __name__ == "__main__":
    main()
