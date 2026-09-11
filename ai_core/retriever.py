"""Tenant-safe retriever with interchangeable local and remote vector stores."""

from __future__ import annotations

import inspect
import json
import os
import re
import unicodedata
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import Iterator

import numpy as np

from ai_core.embedder import embed_texts
from ai_core.config import ConfigError, load_config
from ai_core.vector_store import (
    LocalNumpyVectorStore,
    VectorStore,
    VectorStoreError,
    remote_store_from_env,
)


DEFAULT_INDEX_DIR = Path(__file__).resolve().parent.parent / "index"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_THRESHOLD = 0.65
DEFAULT_RELATIVE_SCORE_MARGIN = 1.0
_INDEX_DIR_OVERRIDE: ContextVar[str | None] = ContextVar("ai_core_index_dir_override", default=None)


class RetrieverError(RuntimeError):
    """Raised when the on-disk index is missing or inconsistent."""


@contextmanager
def use_index_dir(index_dir: str | Path | None) -> Iterator[None]:
    """Temporarily route retrieval to a local index for the current context.

    ContextVar keeps Streamlit sessions/threads isolated; unlike an environment
    variable, one tester cannot silently switch another tester's knowledge base.
    """

    resolved = str(Path(index_dir).resolve()) if index_dir else None
    token = _INDEX_DIR_OVERRIDE.set(resolved)
    try:
        yield
    finally:
        _INDEX_DIR_OVERRIDE.reset(token)


def normalize_query(query: str) -> str:
    """Lowercase, remove punctuation and collapse whitespace; retain Vietnamese accents."""
    normalized = unicodedata.normalize("NFKC", query).lower()
    normalized = re.sub(r"[^\w\s]", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


def _is_exact_tenant_answer(item: dict, normalized_query: str) -> bool:
    metadata = item.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("source") != "tenant_provided":
        return False
    content = normalize_query(str(item.get("content", "")))
    return bool(normalized_query and normalized_query in content)


def _source_rank(item: dict, normalized_query: str) -> tuple[int, int, float]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    priority = metadata.get("source_priority", 0)
    if isinstance(priority, bool) or not isinstance(priority, int):
        priority = 0
    return (
        1 if _is_exact_tenant_answer(item, normalized_query) else 0,
        priority if metadata.get("source") == "tenant_provided" else 0,
        float(item.get("score", 0.0)),
    )


@lru_cache(maxsize=8)
def _load_index_cached(
    index_dir: str,
    vectors_mtime_ns: int,
    metadata_mtime_ns: int,
    manifest_mtime_ns: int,
) -> tuple[np.ndarray, list[dict], dict]:
    del vectors_mtime_ns, metadata_mtime_ns, manifest_mtime_ns
    directory = Path(index_dir)
    vectors = np.load(directory / "vectors.npy", allow_pickle=False)
    with (directory / "metadata.json").open(encoding="utf-8") as handle:
        metadata = json.load(handle)
    with (directory / "manifest.json").open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    if vectors.ndim != 2 or vectors.shape[0] != len(metadata):
        raise RetrieverError("Index hỏng: số vector không khớp metadata.")
    if manifest.get("record_count") != len(metadata) or manifest.get("dimension") != vectors.shape[1]:
        raise RetrieverError("Index hỏng: manifest không khớp vectors/metadata.")
    return vectors.astype("float32", copy=False), metadata, manifest


def _load_index(index_dir: Path) -> tuple[np.ndarray, list[dict], dict]:
    paths = [index_dir / "vectors.npy", index_dir / "metadata.json", index_dir / "manifest.json"]
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        raise RetrieverError("Chưa có index hoàn chỉnh. Thiếu: " + ", ".join(missing))
    try:
        mtimes = [path.stat().st_mtime_ns for path in paths]
        return _load_index_cached(str(index_dir.resolve()), *mtimes)
    except RetrieverError:
        raise
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RetrieverError(f"Không đọc được index tại {index_dir}: {exc}") from exc


def _embed_query(
    embed_fn: Callable[..., list[list[float]]],
    query: str,
    *,
    model: str,
    provider: str | None,
) -> np.ndarray:
    parameters = inspect.signature(embed_fn).parameters
    kwargs: dict[str, str] = {"model": model}
    if provider and "provider" in parameters:
        kwargs["provider"] = provider
    if "task_type" in parameters:
        kwargs["task_type"] = "RETRIEVAL_QUERY"
    vectors = embed_fn([query], **kwargs)
    if len(vectors) != 1:
        raise RetrieverError("Embedder phải trả đúng một vector cho query.")
    return np.asarray(vectors[0], dtype="float32")


def retrieve(
    query: str,
    tenant_id: str,
    k: int = 5,
    *,
    threshold: float | None = None,
    relative_score_margin: float | None = None,
    index_dir: str | Path | None = None,
    embed_fn: Callable[..., list[list[float]]] = embed_texts,
    model: str | None = None,
    provider: str | None = None,
    backend: str | None = None,
    vector_store: VectorStore | None = None,
) -> list[dict]:
    """Return at most ``k`` matching chunks for exactly one tenant.

    Existing callers keep using ``retrieve(query, tenant_id, k)``. The two new
    keyword-only seams select/inject a store without changing that interface.
    """
    # Default deny: không có tenant hợp lệ thì dừng trước embedding và đọc index.
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise ValueError("tenant_id là bắt buộc; truy vấn không tenant bị từ chối.")
    if not isinstance(k, int) or isinstance(k, bool) or k <= 0:
        raise ValueError("k phải là số nguyên dương.")
    try:
        # Tenant phải có file cấu hình riêng; không dùng cấu hình mặc định cho tenant lạ.
        tenant_config = load_config(tenant_id)
        retrieval_policy = tenant_config.retrieval_policy
    except ConfigError as exc:
        raise RetrieverError(f"tenant_id không hợp lệ hoặc chưa được đăng ký: {tenant_id!r}.") from exc
    if threshold is None:
        effective_threshold = retrieval_policy.min_score
    else:
        effective_threshold = threshold
    if relative_score_margin is None:
        effective_margin = retrieval_policy.relative_score_margin
    else:
        effective_margin = relative_score_margin
    if not 0.0 <= effective_threshold <= 1.0:
        raise ValueError("threshold phải nằm trong khoảng 0..1.")
    if not 0.0 <= effective_margin <= 1.0:
        raise ValueError("relative_score_margin phải nằm trong khoảng 0..1.")
    normalized_query = normalize_query(query)
    if not normalized_query:
        return []

    context_index_dir = _INDEX_DIR_OVERRIDE.get() if index_dir is None else None
    selected_backend = (backend or os.getenv("AI_CORE_VECTOR_STORE_BACKEND", "auto")).strip().lower()
    if context_index_dir is not None:
        selected_backend = "local"
    if selected_backend not in {"auto", "local", "remote"}:
        raise ValueError("backend phải là auto, local hoặc remote.")
    if vector_store is None:
        use_remote = selected_backend == "remote" or (
            selected_backend == "auto" and bool(os.getenv("AI_CORE_VECTOR_STORE_URL", "").strip())
        )
        if use_remote:
            configured = tenant_config.embedding_policy.primary
            remote_provider = provider or configured.provider
            remote_model = model or configured.model
            if not remote_provider or not remote_model:
                raise RetrieverError("Remote vector store cần provider/model embedding.")
            try:
                vector_store = remote_store_from_env(provider=remote_provider, model=remote_model)
            except VectorStoreError as exc:
                raise RetrieverError(str(exc)) from exc
        else:
            if index_dir is None:
                if context_index_dir is not None:
                    selected_index_dir = Path(context_index_dir)
                else:
                    # Mỗi tenant tự khai báo vị trí knowledge index trong YAML của mình.
                    configured_dir = tenant_config.knowledge.local_index_dir
                    selected_index_dir = PROJECT_ROOT / configured_dir
            else:
                selected_index_dir = Path(index_dir)
            vector_store = LocalNumpyVectorStore(selected_index_dir, _load_index)

    index_provider, index_model = vector_store.embedding_spec()
    selected_model = model or index_model
    selected_provider = provider or index_provider
    if not selected_model:
        raise RetrieverError("Manifest thiếu model embedding.")
    if model and index_model and model != index_model:
        raise RetrieverError(f"Query model '{model}' không khớp index model '{index_model}'.")

    query_vector = _embed_query(
        embed_fn,
        normalized_query,
        model=selected_model,
        provider=selected_provider,
    )
    try:
        # Over-fetch so a relevant tenant-provided correction cannot be hidden by
        # several older crawl chunks before source-priority rules are applied.
        ranked = vector_store.query(
            query_vector.tolist(), tenant_id=tenant_id, k=min(max(k * 4, 20), 100)
        )
    except VectorStoreError as exc:
        raise RetrieverError(str(exc)) from exc
    if not ranked:
        return []
    relative_cutoff = ranked[0]["score"] - effective_margin
    score_cutoff = max(effective_threshold, relative_cutoff)

    eligible = [
        item
        for item in ranked
        if item["score"] >= effective_threshold
        and (
            item["score"] >= score_cutoff
            or _is_exact_tenant_answer(item, normalized_query)
        )
    ]
    eligible.sort(key=lambda item: _source_rank(item, normalized_query), reverse=True)

    results: list[dict] = []
    for item in eligible:
        score = item["score"]
        result = dict(item)
        result["score"] = round(float(score), 6)
        results.append(result)
        if len(results) == k:
            break
    return results
