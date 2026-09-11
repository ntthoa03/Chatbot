"""H4-11: normalize a tenant answer, index it immediately, then verify retrieval."""

from __future__ import annotations

import argparse
import json
import re
import time
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from uuid import UUID, uuid5

from ai_core.config import load_config, validate_tenant_id
from ai_core.embedder import embed_texts
from ai_core.models import KnowledgeChunk
from ai_core.retriever import retrieve
from index_chunks import build_index, load_cache, save_cache, save_index


ROOT = Path(__file__).resolve().parent.parent
ANSWER_NAMESPACE = UUID("f72c7839-c940-46a0-8d25-34a5139074ca")


class TenantAnswerError(RuntimeError):
    pass


def _clean_text(value: str, *, label: str, maximum: int) -> str:
    clean = unicodedata.normalize("NFC", re.sub(r"\s+", " ", value)).strip()
    if not clean:
        raise TenantAnswerError(f"{label} không được để trống.")
    if len(clean) > maximum:
        raise TenantAnswerError(f"{label} dài quá {maximum} ký tự.")
    return clean


def build_tenant_answer_chunk(
    tenant_id: str,
    question: str,
    answer: str,
    *,
    updated_at: str | None = None,
) -> dict[str, Any]:
    validate_tenant_id(tenant_id)
    clean_question = _clean_text(question, label="Câu hỏi", maximum=1000)
    clean_answer = _clean_text(answer, label="Câu trả lời", maximum=4000)
    identity = f"{tenant_id}\n{clean_question.casefold()}"
    chunk_id = str(uuid5(ANSWER_NAMESPACE, identity))
    day = updated_at or datetime.now(UTC).date().isoformat()
    chunk = {
        "tenant_id": tenant_id,
        "chunk_id": chunk_id,
        "content": f"Câu hỏi: {clean_question}\nCâu trả lời chính thức từ doanh nghiệp: {clean_answer}",
        "metadata": {
            "url": f"https://tenant-provided.local/{tenant_id}/{chunk_id}",
            "title": f"Tenant xác nhận: {clean_question[:120]}",
            "type": "faq",
            "updated_at": day,
            "source": "tenant_provided",
            "source_priority": 100,
            "source_confidence": 1.0,
        },
    }
    return KnowledgeChunk.model_validate(chunk).model_dump(mode="json", exclude_none=True)


def _existing_chunks(index_dir: Path) -> list[dict[str, Any]]:
    metadata_path = index_dir / "metadata.json"
    if not metadata_path.exists():
        return []
    try:
        rows = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TenantAnswerError(f"Không đọc được index hiện tại: {exc}") from exc
    if not isinstance(rows, list):
        raise TenantAnswerError("metadata.json của index phải là JSON array.")
    chunks: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise TenantAnswerError("Index chứa metadata không hợp lệ.")
        chunks.append({
            "tenant_id": row.get("tenant_id"),
            "chunk_id": row.get("chunk_id"),
            "content": row.get("content"),
            "metadata": row.get("metadata", {}),
        })
    return chunks


def ingest_tenant_answer(
    tenant_id: str,
    question: str,
    answer: str,
    *,
    index_dir: Path | None = None,
    embed_fn: Callable[..., list[list[float]]] = embed_texts,
    updated_at: str | None = None,
) -> dict[str, Any]:
    """Upsert one authoritative FAQ and verify that the same question retrieves it first."""

    started = time.perf_counter()
    config = load_config(tenant_id)
    selected_dir = index_dir or ROOT / config.knowledge.local_index_dir
    chunk = build_tenant_answer_chunk(tenant_id, question, answer, updated_at=updated_at)
    manifest_path = selected_dir / "manifest.json"
    if not manifest_path.exists():
        raise TenantAnswerError(f"Chưa có index để cập nhật: {selected_dir}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TenantAnswerError(f"Không đọc được manifest index: {exc}") from exc
    provider = str(manifest.get("provider") or "").strip()
    model = str(manifest.get("model") or "").strip()
    if not provider or not model:
        raise TenantAnswerError("Manifest thiếu provider/model embedding.")

    existing = _existing_chunks(selected_dir)
    combined = [
        item
        for item in existing
        if not (item.get("tenant_id") == tenant_id and item.get("chunk_id") == chunk["chunk_id"])
    ]
    combined.append(chunk)
    cache_path = selected_dir / "embedding_cache.json"
    cache = load_cache(cache_path)
    records, cache, embedded_new, cache_hits = build_index(
        combined,
        cache,
        embed_fn=embed_fn,
        model=model,
        provider=provider,
    )
    save_index(records, selected_dir, model=model, provider=provider)
    save_cache(cache_path, cache)

    results = retrieve(
        question,
        tenant_id,
        k=5,
        index_dir=selected_dir,
        embed_fn=embed_fn,
        model=model,
        provider=provider,
    )
    elapsed = time.perf_counter() - started
    if not results or results[0].get("chunk_id") != chunk["chunk_id"]:
        raise TenantAnswerError("Đã ghi index nhưng kiểm tra hỏi lại chưa ưu tiên câu trả lời tenant.")
    if elapsed >= 60:
        raise TenantAnswerError(f"Nạp và kiểm tra mất {elapsed:.1f}s, vượt SLA 60s.")
    return {
        "schema_version": "h4-11.tenant-answer-receipt.v1",
        "tenant_id": tenant_id,
        "chunk": chunk,
        "index_dir": str(selected_dir.resolve()),
        "embedded_new": embedded_new,
        "cache_hits": cache_hits,
        "verified_top_chunk_id": results[0]["chunk_id"],
        "verified_source": results[0].get("metadata", {}).get("source"),
        "elapsed_seconds": round(elapsed, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--answer", required=True)
    parser.add_argument("--index-dir", type=Path)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    receipt = ingest_tenant_answer(
        args.tenant_id, args.question, args.answer, index_dir=args.index_dir
    )
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
