"""
05 — Pipeline embedding và đánh index cho bộ chunk tri thức.

Chạy mặc định với Gemini:
    export GEMINI_API_KEY=...
    python index_chunks.py

Hoặc dùng OpenAI:
    export OPENAI_API_KEY=sk-...
    python index_chunks.py --provider openai --model text-embedding-3-small

Chạy lại nhiều lần: chunk nào ĐÃ embed trước đó (theo hash nội dung, không
phải chunk_id — nội dung không đổi thì dù chunk_id đổi vẫn không embed lại;
nội dung đổi thì dù chunk_id giữ nguyên vẫn embed lại) sẽ được lấy từ cache,
không tốn tiền gọi API lại. Xem thêm câu hỏi mở trong contract.md mục 3.6 về
việc chunk_id có ổn định qua các lần crawl lại hay không — cache theo hash nội
dung ở đây không phụ thuộc câu trả lời đó, nên an toàn cả hai trường hợp.

Output:
    index/vectors.npy         — ma trận N x dim (float32)
    index/metadata.json       — N object, thứ tự khớp với hàng trong vectors.npy
    index/manifest.json        — model/provider/số chiều dùng để kiểm tra tương thích
    index/embedding_cache.json — cache theo provider + model + hash nội dung
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
from pathlib import Path
from typing import Callable

from ai_core.embedder import DEFAULT_EMBEDDING_MODEL, EmbedderError, embed_texts, infer_provider
from ai_core.config import ConfigError, load_config
from ai_core.models import KnowledgeChunk
from pydantic import ValidationError

CACHE_VERSION = 3
INDEX_VERSION = 2


class IndexError_(ValueError):  # tránh trùng tên với builtin IndexError
    pass


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_chunks(path: Path) -> list[dict]:
    if not path.exists():
        raise IndexError_(f"Không tìm thấy file input: {path}")
    with path.open(encoding="utf-8") as f:
        chunks = json.load(f)
    if not isinstance(chunks, list):
        raise IndexError_(f"{path} phải là một JSON array các chunk.")
    validated: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for i, c in enumerate(chunks):
        if not isinstance(c, dict):
            raise IndexError_(f"Chunk thứ {i} phải là JSON object.")
        try:
            normalized = KnowledgeChunk.model_validate(c).model_dump(mode="json")
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                for error in exc.errors()
            )
            raise IndexError_(f"Chunk thứ {i} không đúng format Task.xlsx: {details}") from exc
        key = (normalized["tenant_id"], normalized["chunk_id"])
        if key in seen:
            raise IndexError_(
                f"Trùng chunk_id '{normalized['chunk_id']}' "
                f"trong tenant '{normalized['tenant_id']}'."
            )
        seen.add(key)
        validated.append(normalized)
    return validated


def load_cache(cache_path: Path) -> dict:
    if cache_path.exists():
        with cache_path.open(encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("version") == CACHE_VERSION:
            data.setdefault("embeddings", {})
            data.setdefault("chunks", {})
            return data
        # Migration cache v2: giữ nguyên vector theo content hash, bổ sung map chunk_id.
        if isinstance(data, dict) and data.get("version") == 2:
            return {
                "version": CACHE_VERSION,
                "embeddings": data.get("embeddings", {}),
                "chunks": {},
            }
        # Tương thích cache bản đầu: content_hash -> vector.
        if isinstance(data, dict):
            return {"version": CACHE_VERSION, "embeddings": data, "chunks": {}}
        raise IndexError_(f"Cache không đúng định dạng: {cache_path}")
    return {"version": CACHE_VERSION, "embeddings": {}, "chunks": {}}


def save_cache(cache_path: Path, cache: dict) -> None:
    _write_json_atomic(cache_path, cache, ensure_ascii=True, indent=None)


def _write_json_atomic(path: Path, data: object, **dump_kwargs: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, **dump_kwargs)
    os.replace(temporary, path)


def _embed(
    embed_fn: Callable[..., list[list[float]]],
    texts: list[str],
    *,
    model: str,
    provider: str,
) -> list[list[float]]:
    parameters = inspect.signature(embed_fn).parameters
    kwargs: dict[str, str] = {"model": model}
    if "provider" in parameters:
        kwargs["provider"] = provider
    if "task_type" in parameters:
        kwargs["task_type"] = "RETRIEVAL_DOCUMENT"
    return embed_fn(texts, **kwargs)


def build_index(
    chunks: list[dict],
    cache: dict,
    embed_fn: Callable[..., list[list[float]]] = embed_texts,
    model: str = DEFAULT_EMBEDDING_MODEL,
    provider: str | None = None,
    batch_size: int = 100,
) -> tuple[list[dict], dict, int, int]:
    """
    Trả về (records, cache_đã_cập_nhật, số_embed_mới, số_lấy_từ_cache).

    embed_fn có thể inject (ví dụ hàm giả trong test) để không phải gọi API
    thật khi chỉ muốn kiểm tra logic cache/index.
    """
    if provider:
        selected_provider = provider.lower()
    elif embed_fn is embed_texts:
        selected_provider = infer_provider(model)
    else:
        selected_provider = "custom"
    embeddings = cache.setdefault("embeddings", {})
    chunk_state = cache.setdefault("chunks", {})
    cache["version"] = CACHE_VERSION
    hashes = [content_hash(c["content"]) for c in chunks]
    cache_keys = [f"{selected_provider}:{model}:RETRIEVAL_DOCUMENT:{value}" for value in hashes]

    to_embed_idx = [i for i, key in enumerate(cache_keys) if key not in embeddings]
    to_embed_texts = [chunks[i]["content"] for i in to_embed_idx]

    if batch_size < 1:
        raise IndexError_("batch_size phải >= 1.")
    for start in range(0, len(to_embed_texts), batch_size):
        batch_texts = to_embed_texts[start : start + batch_size]
        batch_indices = to_embed_idx[start : start + batch_size]
        new_vectors = _embed(embed_fn, batch_texts, model=model, provider=selected_provider)
        if len(new_vectors) != len(batch_texts):
            raise IndexError_(
                f"embed_fn trả về {len(new_vectors)} vector nhưng gửi đi {len(batch_texts)} text — không khớp."
            )
        for idx, vec in zip(batch_indices, new_vectors):
            embeddings[cache_keys[idx]] = vec

    # chunk_id là identity của chunk; content_hash quyết định vector có còn hợp lệ.
    # Vector vẫn deduplicate theo content hash để chunk khác ID nhưng cùng nội dung
    # không làm tốn thêm lượt gọi API.
    for chunk, content_digest, embedding_key in zip(chunks, hashes, cache_keys):
        tenant_chunks = chunk_state.setdefault(chunk["tenant_id"], {})
        tenant_chunks[chunk["chunk_id"]] = {
            "content_hash": content_digest,
            "embedding_key": embedding_key,
        }

    records = []
    for c, h, cache_key in zip(chunks, hashes, cache_keys):
        meta = c.get("metadata", {})
        records.append(
            {
                "chunk_id": c["chunk_id"],
                "tenant_id": c["tenant_id"],
                "content": c["content"],
                "content_hash": h,
                # Preserve the exact Task.xlsx chunk contract in local metadata.
                "metadata": {
                    "url": meta.get("url"),
                    "title": meta.get("title"),
                    "type": meta.get("type"),
                    "updated_at": meta.get("updated_at"),
                },
                "vector": embeddings[cache_key],
            }
        )

    n_new = len(to_embed_texts)
    n_cached = len(chunks) - n_new
    return records, cache, n_new, n_cached


def save_index(
    records: list[dict],
    out_dir: Path,
    *,
    model: str = DEFAULT_EMBEDDING_MODEL,
    provider: str | None = None,
) -> None:
    import numpy as np

    if not records:
        raise IndexError_("Không thể tạo index rỗng.")
    out_dir.mkdir(parents=True, exist_ok=True)
    vectors = np.array([r["vector"] for r in records], dtype="float32")
    if vectors.ndim != 2 or vectors.shape[1] == 0:
        raise IndexError_("Embedding phải tạo ma trận N x dimension hợp lệ.")
    vector_tmp = out_dir / "vectors.npy.tmp"
    with vector_tmp.open("wb") as handle:
        np.save(handle, vectors)
    os.replace(vector_tmp, out_dir / "vectors.npy")

    metadata = [{k: v for k, v in r.items() if k != "vector"} for r in records]
    _write_json_atomic(out_dir / "metadata.json", metadata, ensure_ascii=False, indent=2)
    selected_provider = provider or (infer_provider(model) if model != "bow-test-only" else "test")
    manifest = {
        "version": INDEX_VERSION,
        "provider": selected_provider,
        "model": model,
        "dimension": int(vectors.shape[1]),
        "record_count": int(vectors.shape[0]),
        "tenants": sorted({record["tenant_id"] for record in records}),
    }
    _write_json_atomic(out_dir / "manifest.json", manifest, ensure_ascii=False, indent=2)


def resolve_embedding_candidates(
    tenant_id: str,
    *,
    model: str | None = None,
    provider: str | None = None,
) -> list[tuple[str, str]]:
    """Resolve primary/fallback embedding routes from tenant config."""
    if model or provider:
        selected_model = model or DEFAULT_EMBEDDING_MODEL
        return [((provider or infer_provider(selected_model)).lower(), selected_model)]
    config = load_config(tenant_id)
    policy = config.embedding_policy
    return [
        (policy.primary.provider, policy.primary.model),
        (policy.fallback.provider, policy.fallback.model),
    ]


def build_index_with_fallback(
    chunks: list[dict],
    cache: dict,
    candidates: list[tuple[str, str]],
    *,
    embed_fn: Callable[..., list[list[float]]] = embed_texts,
    batch_size: int = 100,
) -> tuple[list[dict], dict, int, int, str, str]:
    """Try embedding routes in order and report the route that built the index."""
    last_error: EmbedderError | None = None
    for attempt, (candidate_provider, candidate_model) in enumerate(candidates, start=1):
        try:
            records, cache, n_new, n_cached = build_index(
                chunks,
                cache,
                embed_fn=embed_fn,
                model=candidate_model,
                provider=candidate_provider,
                batch_size=batch_size,
            )
            return records, cache, n_new, n_cached, candidate_provider, candidate_model
        except EmbedderError as exc:
            last_error = exc
            label = "primary" if attempt == 1 else "fallback"
            print(f"Embedding {label} {candidate_provider}/{candidate_model} thất bại; đang thử tuyến kế tiếp.")
    raise last_error or EmbedderError("Không có embedding route khả dụng.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Embed + đánh index bộ chunk tri thức (HOA-05)")
    parser.add_argument("--input", default="seed_chunks.json", help="File JSON chứa list chunk")
    parser.add_argument("--out-dir", default="index", help="Thư mục lưu index (vectors.npy + metadata.json)")
    parser.add_argument("--cache", help="Mặc định: <out-dir>/embedding_cache.json")
    parser.add_argument("--tenant-id", default="mima_internal", help="Tenant dùng để nạp embedding_policy")
    parser.add_argument("--model", help="Ghi đè model trong embedding_policy")
    parser.add_argument("--provider", choices=("gemini", "openai"), help="Tự suy ra từ model nếu bỏ trống")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Số chunks mỗi request embedding; Gemini cho phép tối đa 100",
    )
    args = parser.parse_args()

    try:
        chunks = load_chunks(Path(args.input))
        cache_path = Path(args.cache) if args.cache else Path(args.out_dir) / "embedding_cache.json"
        cache = load_cache(cache_path)
        candidates = resolve_embedding_candidates(
            args.tenant_id,
            model=args.model,
            provider=args.provider,
        )
        records, cache, n_new, n_cached, selected_provider, selected_model = build_index_with_fallback(
            chunks,
            cache,
            candidates,
            batch_size=args.batch_size,
        )
        save_cache(cache_path, cache)
        save_index(
            records,
            Path(args.out_dir),
            model=selected_model,
            provider=selected_provider,
        )
    except (IndexError_, EmbedderError, ConfigError) as e:
        print(f"❌ Lỗi: {e}")
        raise SystemExit(1) from None

    print(
        f"Xong: {len(records)} chunk tổng cộng — "
        f"{n_new} embed mới, {n_cached} lấy từ cache. "
        f"Provider/model: {selected_provider}/{selected_model}. "
        f"Index lưu tại {args.out_dir}/"
    )


if __name__ == "__main__":
    main()
