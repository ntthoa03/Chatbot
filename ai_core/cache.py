"""Semantic response cache an toàn theo tenant cho H2-08."""

from __future__ import annotations

import copy
import math
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from ai_core.config import ConfigError, validate_tenant_id
from ai_core.embedder import embed_texts


MIN_SAFE_SIMILARITY = 0.92
DEFAULT_SIMILARITY = 0.94
DEFAULT_TTL_SECONDS = 3_600.0
DEFAULT_PRICE_TTL_SECONDS = 300.0
DEFAULT_MAX_ENTRIES_PER_SCOPE = 500


class CacheError(ValueError):
    """Raised when a cache request would violate tenant or safety constraints."""


@dataclass(frozen=True)
class CacheLookup:
    hit: bool
    similarity: float | None = None
    matched_question: str | None = None
    response: dict[str, Any] | None = None


@dataclass
class _CacheEntry:
    question: str
    normalized_question: str
    vector: tuple[float, ...]
    response: dict[str, Any]
    expires_at: float
    is_price: bool


def _normalize_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.casefold())
    without_accents = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", without_accents).split())


def is_price_question(question: str) -> bool:
    """Detect price-sensitive questions that require the shorter cache TTL."""

    normalized = _normalize_text(question)
    return bool(
        re.search(
            r"\b(?:gia|bao nhieu(?: tien)?|chi phi|bao gia|[0-9]+\s*(?:tr|trieu|k|nghin))\b",
            normalized,
        )
    )


def cache_is_enabled() -> bool:
    """Cache chỉ hoạt động khi được bật rõ ràng để rollout không ảnh hưởng bot cũ."""

    return os.getenv("AI_CORE_SEMANTIC_CACHE_ENABLED", "0").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def request_is_cacheable(question: str, history: Sequence[Any]) -> bool:
    """Chỉ cache câu độc lập, không chứa dữ liệu nhạy cảm hoặc phụ thuộc lịch sử."""

    if history or not isinstance(question, str) or not question.strip():
        return False
    normalized = _normalize_text(question)
    sensitive_patterns = (
        r"\botp\b",
        r"\bcvv\b",
        r"\bmat khau\b",
        r"\bpassword\b",
        r"\bso the\b",
        r"\bbenh an\b",
        r"\bket qua xet nghiem\b",
    )
    return not any(re.search(pattern, normalized) for pattern in sensitive_patterns)


def response_is_cacheable(response: dict[str, Any]) -> bool:
    """Chỉ lưu câu trả lời tĩnh, có nguồn và không yêu cầu xử lý bởi người/tool."""

    guardrail = response.get("guardrail") or {}
    return bool(
        response.get("reply")
        and response.get("sources")
        and not response.get("tool_calls")
        and not response.get("need_human")
        and not response.get("lead_captured")
        and not guardrail.get("blocked")
    )


def embed_cache_question(
    question: str,
    *,
    provider: str,
    model: str,
    embed_fn: Callable[..., list[list[float]]] = embed_texts,
) -> list[float]:
    """Embed đúng một câu hỏi; hàm được tách riêng để test không cần gọi API."""

    vectors = embed_fn(
        [question],
        provider=provider,
        model=model,
        task_type="RETRIEVAL_QUERY",
    )
    if len(vectors) != 1:
        raise CacheError("Embedder phải trả đúng một vector cho câu hỏi cache.")
    return [float(value) for value in vectors[0]]


def _scope(tenant_id: object, config_version: object) -> tuple[str, int]:
    try:
        safe_tenant = validate_tenant_id(tenant_id)
    except ConfigError as exc:
        raise CacheError(str(exc)) from exc
    if isinstance(config_version, bool) or not isinstance(config_version, int) or config_version < 1:
        raise CacheError("config_version phải là số nguyên dương.")
    return safe_tenant, config_version


def _unit_vector(vector: Sequence[float]) -> tuple[float, ...]:
    if not vector:
        raise CacheError("Embedding cache không được rỗng.")
    values = tuple(float(value) for value in vector)
    if not all(math.isfinite(value) for value in values):
        raise CacheError("Embedding cache phải chỉ chứa số hữu hạn.")
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0.0:
        raise CacheError("Embedding cache không được là vector 0.")
    return tuple(value / norm for value in values)


class SemanticResponseCache:
    """In-memory cosine cache, phân vùng tuyệt đối theo tenant và config version."""

    def __init__(
        self,
        *,
        similarity_threshold: float = DEFAULT_SIMILARITY,
        default_ttl_seconds: float = DEFAULT_TTL_SECONDS,
        price_ttl_seconds: float = DEFAULT_PRICE_TTL_SECONDS,
        max_entries_per_scope: int = DEFAULT_MAX_ENTRIES_PER_SCOPE,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not MIN_SAFE_SIMILARITY <= similarity_threshold <= 1.0:
            raise CacheError(
                f"similarity_threshold phải nằm trong [{MIN_SAFE_SIMILARITY}, 1.0]."
            )
        if default_ttl_seconds <= 0 or price_ttl_seconds <= 0:
            raise CacheError("TTL cache phải lớn hơn 0.")
        if price_ttl_seconds > default_ttl_seconds:
            raise CacheError("TTL câu giá không được dài hơn TTL mặc định.")
        if isinstance(max_entries_per_scope, bool) or max_entries_per_scope < 1:
            raise CacheError("max_entries_per_scope phải là số nguyên dương.")
        self.similarity_threshold = float(similarity_threshold)
        self.default_ttl_seconds = float(default_ttl_seconds)
        self.price_ttl_seconds = float(price_ttl_seconds)
        self.max_entries_per_scope = int(max_entries_per_scope)
        self._clock = clock
        self._entries: dict[tuple[str, int], list[_CacheEntry]] = {}
        self._lock = threading.RLock()
        self._lookups = 0
        self._hits = 0
        self._misses = 0
        self._expired = 0

    def lookup(
        self,
        *,
        tenant_id: object,
        config_version: object,
        question: str,
        vector: Sequence[float],
    ) -> CacheLookup:
        scope = _scope(tenant_id, config_version)
        if not isinstance(question, str) or not question.strip():
            raise CacheError("question là bắt buộc khi đọc cache.")
        query_vector = _unit_vector(vector)
        now = self._clock()
        with self._lock:
            self._lookups += 1
            # Chỉ quét đúng partition tenant/config; không có tìm kiếm global rồi lọc sau.
            active = [entry for entry in self._entries.get(scope, []) if entry.expires_at > now]
            self._expired += len(self._entries.get(scope, [])) - len(active)
            self._entries[scope] = active
            best: _CacheEntry | None = None
            best_score = -1.0
            for entry in active:
                if len(entry.vector) != len(query_vector):
                    continue
                score = sum(left * right for left, right in zip(entry.vector, query_vector))
                if score > best_score:
                    best = entry
                    best_score = score
            if best is None or best_score < self.similarity_threshold:
                self._misses += 1
                return CacheLookup(hit=False, similarity=max(best_score, 0.0) if best else None)
            self._hits += 1
            return CacheLookup(
                hit=True,
                similarity=round(best_score, 6),
                matched_question=best.question,
                response=copy.deepcopy(best.response),
            )

    def put(
        self,
        *,
        tenant_id: object,
        config_version: object,
        question: str,
        vector: Sequence[float],
        response: dict[str, Any],
    ) -> None:
        scope = _scope(tenant_id, config_version)
        if not isinstance(question, str) or not question.strip():
            raise CacheError("question là bắt buộc khi ghi cache.")
        if not response_is_cacheable(response):
            raise CacheError("Response không đủ điều kiện an toàn để ghi cache.")
        normalized_question = _normalize_text(question)
        normalized_vector = _unit_vector(vector)
        price_sensitive = is_price_question(question)
        ttl = self.price_ttl_seconds if price_sensitive else self.default_ttl_seconds
        entry = _CacheEntry(
            question=question.strip(),
            normalized_question=normalized_question,
            vector=normalized_vector,
            response=copy.deepcopy(response),
            expires_at=self._clock() + ttl,
            is_price=price_sensitive,
        )
        with self._lock:
            current = [
                item
                for item in self._entries.get(scope, [])
                if item.normalized_question != normalized_question
            ]
            current.append(entry)
            # Giữ các entry mới nhất trong từng tenant/config để bộ nhớ có giới hạn.
            self._entries[scope] = current[-self.max_entries_per_scope :]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._lookups = self._hits = self._misses = self._expired = 0

    def stats(self) -> dict[str, float | int]:
        with self._lock:
            total_entries = sum(len(entries) for entries in self._entries.values())
            return {
                "lookups": self._lookups,
                "hits": self._hits,
                "misses": self._misses,
                "expired": self._expired,
                "entries": total_entries,
                "scopes": len(self._entries),
                "hit_rate": round(self._hits / self._lookups, 6) if self._lookups else 0.0,
            }


_GLOBAL_CACHE: SemanticResponseCache | None = None
_GLOBAL_LOCK = threading.Lock()


def get_semantic_cache() -> SemanticResponseCache:
    """Tạo cache dùng chung theo cấu hình môi trường ở lần truy cập đầu tiên."""

    global _GLOBAL_CACHE
    with _GLOBAL_LOCK:
        if _GLOBAL_CACHE is None:
            _GLOBAL_CACHE = SemanticResponseCache(
                similarity_threshold=float(
                    os.getenv("AI_CORE_SEMANTIC_CACHE_THRESHOLD", str(DEFAULT_SIMILARITY))
                ),
                default_ttl_seconds=float(
                    os.getenv("AI_CORE_SEMANTIC_CACHE_TTL_SECONDS", str(DEFAULT_TTL_SECONDS))
                ),
                price_ttl_seconds=float(
                    os.getenv(
                        "AI_CORE_SEMANTIC_CACHE_PRICE_TTL_SECONDS",
                        str(DEFAULT_PRICE_TTL_SECONDS),
                    )
                ),
            )
        return _GLOBAL_CACHE


def reset_semantic_cache() -> None:
    """Reset singleton cho test hoặc khi thay đổi cấu hình rollout."""

    global _GLOBAL_CACHE
    with _GLOBAL_LOCK:
        _GLOBAL_CACHE = None
