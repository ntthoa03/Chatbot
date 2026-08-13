"""Interchangeable vector-store backends for tenant-safe retrieval."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np


class VectorStoreError(RuntimeError):
    """Raised when a vector store cannot execute a safe query."""


class VectorStore(Protocol):
    """Common interface shared by the local prototype and H-06 store."""

    def embedding_spec(self) -> tuple[str | None, str | None]: ...

    def query(
        self, vector: Sequence[float], *, tenant_id: str, k: int
    ) -> list[dict[str, Any]]: ...


IndexLoader = Callable[[Path], tuple[np.ndarray, list[dict], dict]]


@dataclass(frozen=True)
class LocalNumpyVectorStore:
    """HOA-06 file index retained for offline/demo use."""

    index_dir: Path
    loader: IndexLoader

    def _load(self) -> tuple[np.ndarray, list[dict], dict]:
        return self.loader(self.index_dir)

    def embedding_spec(self) -> tuple[str | None, str | None]:
        _, _, manifest = self._load()
        return manifest.get("provider"), manifest.get("model")

    def query(
        self, vector: Sequence[float], *, tenant_id: str, k: int
    ) -> list[dict[str, Any]]:
        vectors, metadata, _ = self._load()
        query_vector = np.asarray(vector, dtype="float32")
        if query_vector.ndim != 1 or query_vector.shape[0] != vectors.shape[1]:
            raise VectorStoreError(
                f"Số chiều query ({query_vector.shape[0] if query_vector.ndim else 0}) "
                f"không khớp index ({vectors.shape[1]})."
            )
        query_norm = float(np.linalg.norm(query_vector))
        if query_norm == 0.0:
            return []

        # Filter before ranking; there is no global cross-tenant similarity search.
        tenant_rows = [i for i, item in enumerate(metadata) if item.get("tenant_id") == tenant_id]
        if not tenant_rows:
            return []
        selected = vectors[tenant_rows]
        denominators = np.linalg.norm(selected, axis=1) * query_norm
        similarities = np.divide(
            selected @ query_vector,
            denominators,
            out=np.zeros(len(tenant_rows), dtype="float32"),
            where=denominators != 0,
        )
        ranked = sorted(zip(tenant_rows, similarities.tolist()), key=lambda pair: pair[1], reverse=True)
        return [_local_result(metadata[row], score) for row, score in ranked[:k]]


RemoteTransport = Callable[[str, dict[str, Any], dict[str, str], float], dict[str, Any]]


def _post_json(
    endpoint: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout_seconds: float,
) -> dict[str, Any]:
    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            body = response.read()
    except HTTPError as exc:
        raise VectorStoreError(f"Vector store trả HTTP {exc.code}.") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise VectorStoreError("Không kết nối được vector store.") from exc
    try:
        parsed = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VectorStoreError("Vector store trả response không phải JSON hợp lệ.") from exc
    if not isinstance(parsed, dict):
        raise VectorStoreError("Vector store phải trả một JSON object.")
    return parsed


@dataclass(frozen=True)
class RemoteVectorStore:
    """HTTP adapter for H-06/Pinecone-compatible query endpoints.

    Requests contain both namespace and tenant filter. Responses are checked again
    and records without the exact tenant ID are discarded (fail closed).
    """

    endpoint: str
    provider: str
    model: str
    api_key: str | None = None
    timeout_seconds: float = 10.0
    transport: RemoteTransport = _post_json

    def embedding_spec(self) -> tuple[str | None, str | None]:
        return self.provider, self.model

    def query(
        self, vector: Sequence[float], *, tenant_id: str, k: int
    ) -> list[dict[str, Any]]:
        payload = {
            "vector": [float(value) for value in vector],
            "top_k": min(max(k * 4, k), 100),
            "namespace": tenant_id,
            "filter": {"tenant_id": {"$eq": tenant_id}},
            "include_metadata": True,
            "include_values": False,
        }
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            headers["Api-Key"] = self.api_key
        response = self.transport(self.endpoint, payload, headers, self.timeout_seconds)
        raw_matches = response.get("matches", response.get("results"))
        if not isinstance(raw_matches, list):
            raise VectorStoreError("Vector store response thiếu mảng 'matches' hoặc 'results'.")

        safe: list[dict[str, Any]] = []
        for item in raw_matches:
            normalized = _remote_result(item, tenant_id)
            if normalized is not None:
                safe.append(normalized)
        safe.sort(key=lambda item: item["score"], reverse=True)
        return safe[:k]


def remote_store_from_env(*, provider: str, model: str) -> RemoteVectorStore:
    endpoint = os.getenv("AI_CORE_VECTOR_STORE_URL", "").strip()
    if not endpoint:
        raise VectorStoreError(
            "AI_CORE_VECTOR_STORE_URL là bắt buộc khi AI_CORE_VECTOR_STORE_BACKEND=remote."
        )
    timeout_text = os.getenv("AI_CORE_VECTOR_STORE_TIMEOUT_SECONDS", "10")
    try:
        timeout_seconds = float(timeout_text)
    except ValueError as exc:
        raise VectorStoreError("AI_CORE_VECTOR_STORE_TIMEOUT_SECONDS phải là số.") from exc
    if timeout_seconds <= 0:
        raise VectorStoreError("AI_CORE_VECTOR_STORE_TIMEOUT_SECONDS phải lớn hơn 0.")
    return RemoteVectorStore(
        endpoint=endpoint,
        provider=provider,
        model=model,
        api_key=os.getenv("AI_CORE_VECTOR_STORE_API_KEY") or None,
        timeout_seconds=timeout_seconds,
    )


def _local_result(item: dict, score: float) -> dict[str, Any]:
    source_metadata = item.get("metadata")
    if not isinstance(source_metadata, dict):
        source_metadata = item  # Backward-compatible reader for index version 1.
    return {
        "chunk_id": item["chunk_id"],
        "content": item["content"],
        "url": source_metadata.get("url"),
        "score": float(score),
        "metadata": _public_metadata(source_metadata),
    }


def _remote_result(item: Any, tenant_id: str) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    match_metadata = item.get("metadata")
    if not isinstance(match_metadata, dict):
        match_metadata = {}
    embedded_chunk = match_metadata.get("chunk")
    if isinstance(embedded_chunk, dict):
        chunk = embedded_chunk
    elif isinstance(match_metadata.get("content"), str):
        # Pinecone-style flat vector metadata.
        chunk = match_metadata
    else:
        # Canonical Task.xlsx/H-06 response shape at the match top level.
        chunk = item
    result_tenant = chunk.get("tenant_id", item.get("tenant_id", match_metadata.get("tenant_id")))
    if result_tenant != tenant_id:
        return None
    chunk_id = chunk.get("chunk_id") or item.get("chunk_id") or item.get("id")
    content = chunk.get("content") or item.get("content") or match_metadata.get("text")
    score = item.get("score")
    if not isinstance(chunk_id, str) or not chunk_id or not isinstance(content, str) or not content:
        return None
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
        return None
    source_metadata = chunk.get("metadata")
    if not isinstance(source_metadata, dict):
        source_metadata = chunk
    url = source_metadata.get("url", item.get("url"))
    return {
        "chunk_id": chunk_id,
        "content": content,
        "url": url,
        "score": float(score),
        "metadata": _public_metadata(source_metadata),
    }


def build_remote_vector_record(chunk: dict[str, Any], vector: Sequence[float]) -> dict[str, Any]:
    """Map the Task.xlsx chunk contract to a filterable vector DB record.

    Vector databases generally require scalar metadata, so source metadata is
    flattened only inside the storage envelope. The business chunk itself remains
    nested exactly as specified by the workbook.
    """
    from ai_core.models import KnowledgeChunk

    canonical = KnowledgeChunk.model_validate(chunk).model_dump(mode="json")
    source = canonical["metadata"]
    return {
        "id": canonical["chunk_id"],
        "namespace": canonical["tenant_id"],
        "values": [float(value) for value in vector],
        "metadata": {
            "tenant_id": canonical["tenant_id"],
            "chunk_id": canonical["chunk_id"],
            "content": canonical["content"],
            "url": source["url"],
            "title": source["title"],
            "type": source["type"],
            "updated_at": source["updated_at"],
        },
    }


def _public_metadata(item: dict) -> dict[str, Any]:
    return {
        "source_id": item.get("source_id"),
        "url": item.get("url"),
        "title": item.get("title"),
        "type": item.get("type"),
        "updated_at": item.get("updated_at"),
    }
