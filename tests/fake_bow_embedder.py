"""
Embedder GIẢ (TF-IDF đơn giản) — CHỈ dùng để test/demo retriever.py và
index_chunks.py OFFLINE, khi chưa có mạng để gọi OpenAI thật.

Vector ở đây KHÔNG phải embedding ngữ nghĩa thật (không hiểu từ đồng nghĩa,
không hiểu ngữ cảnh) — chỉ đếm từ trùng nhau giữa câu hỏi và nội dung chunk,
có giảm trọng số các từ xuất hiện ở hầu hết chunk (vd "công ty", "dịch vụ")
để đỡ gây điểm giả. Đủ để kiểm tra LOGIC của retriever (lọc tenant, ngưỡng
điểm, top-k, trả rỗng khi ngoài phạm vi) chạy đúng, KHÔNG đủ để đánh giá chất
lượng truy xuất thật.

Khi có OPENAI_API_KEY thật: dùng ai_core.embedder.embed_texts (mặc định của
retrieve()) — không dùng file này trong production.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

import numpy as np

SEED_CHUNKS_PATH = Path(__file__).resolve().parent.parent / "seed_chunks.json"


def _tokenize(text: str) -> list[str]:
    """
    Unigram (âm tiết đơn) + bigram (cụm 2 âm tiết liền nhau). Bigram giúp giảm
    nhầm lẫn kiểu "công thức" (nấu ăn) vs "công ty" (doanh nghiệp) — hai cụm
    này chia sẻ âm tiết "công" nhưng KHÔNG cùng nghĩa. Đây vẫn là xấp xỉ thô sơ
    (không phải tách từ tiếng Việt thật, không dùng cho production).
    """
    text = text.lower()
    text = re.sub(r"[^\w\sà-ỹ]", " ", text, flags=re.UNICODE)
    syllables = text.split()
    bigrams = [f"{syllables[i]}_{syllables[i + 1]}" for i in range(len(syllables) - 1)]
    return syllables + bigrams


def _load_corpus() -> list[str]:
    with SEED_CHUNKS_PATH.open(encoding="utf-8") as f:
        chunks = json.load(f)
    return [c["content"] for c in chunks]


def _build_vocab_and_idf() -> tuple[list[str], dict[str, float]]:
    corpus = _load_corpus()
    doc_tokens = [set(_tokenize(t)) for t in corpus]
    vocab: set[str] = set()
    for toks in doc_tokens:
        vocab.update(toks)
    n_docs = len(corpus)
    df = Counter()
    for toks in doc_tokens:
        for w in toks:
            df[w] += 1
    # idf mượt (giống sklearn): log((1+N)/(1+df)) + 1 -> luôn dương, từ hiếm có trọng số cao hơn
    idf = {w: math.log((1 + n_docs) / (1 + df[w])) + 1 for w in vocab}
    return sorted(vocab), idf


_VOCAB, _IDF = _build_vocab_and_idf()
_VOCAB_INDEX = {w: i for i, w in enumerate(_VOCAB)}


def bow_embed_texts(texts: list[str], model: str | None = None) -> list[list[float]]:
    """Chữ ký khớp với ai_core.embedder.embed_texts để inject được vào retrieve()/build_index()."""
    vectors = []
    for t in texts:
        v = np.zeros(len(_VOCAB), dtype="float32")
        for w, c in Counter(_tokenize(t)).items():
            if w in _VOCAB_INDEX:
                v[_VOCAB_INDEX[w]] = c * _IDF.get(w, 1.0)
        vectors.append(v.tolist())
    return vectors
