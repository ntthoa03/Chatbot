"""Cached LLM judge for effective RAG data sufficiency.

This judge inspects only the question and retrieved chunks. It never sees the
expected eval answer, preventing the coverage metric from leaking the test key.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Callable, Sequence
from typing import Any

from ai_core.chat import _generate_with_fallback
from ai_core.config import load_config
from ai_core.evaluator import AnswerabilityVerdict


GenerateCallable = Callable[[Any, str, list[dict[str, str]]], Any]


def _parse_json(text: str) -> dict[str, Any]:
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.S)
        if not match:
            raise ValueError("Answerability judge did not return a JSON object.")
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("Answerability judge result must be a JSON object.")
    return value


class CachedAnswerabilityJudge:
    """Thread-safe, deterministic answerability judge with per-run caching."""

    def __init__(
        self,
        tenant_id: str,
        config_version: int = 1,
        *,
        generate_fn: GenerateCallable = _generate_with_fallback,
        max_chunks: int = 5,
        max_content_chars: int = 4000,
    ) -> None:
        config = load_config(tenant_id, config_version)
        self._config = config.model_copy(update={
            "enabled_tools": [],
            "model_policy": config.model_policy.model_copy(update={"temperature": 0.0}),
        })
        self._generate = generate_fn
        self._max_chunks = max_chunks
        self._max_content_chars = max_content_chars
        self._cache: dict[str, AnswerabilityVerdict] = {}
        self._lock = threading.Lock()
        self.calls = 0
        self.cache_hits = 0

    def _evidence(self, chunks: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        evidence = []
        for index, chunk in enumerate(chunks[: self._max_chunks], start=1):
            source = chunk.get("source") if isinstance(chunk.get("source"), dict) else {}
            evidence.append({
                "chunk_id": str(chunk.get("chunk_id") or f"chunk-{index}"),
                "title": str(source.get("title") or chunk.get("title") or ""),
                "content": str(chunk.get("content") or "")[: self._max_content_chars],
            })
        return evidence

    def __call__(
        self, question: str, chunks: Sequence[dict[str, Any]],
    ) -> AnswerabilityVerdict:
        evidence = self._evidence(chunks)
        cache_key = hashlib.sha256(json.dumps(
            {"question": question, "chunks": evidence},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                self.cache_hits += 1
                return cached

        system_prompt = (
            "Bạn là bộ kiểm tra coverage RAG. Chỉ xác định các chunk có chứa trực tiếp "
            "đủ dữ kiện để trả lời câu hỏi hay không; không dùng kiến thức bên ngoài, "
            "không suy đoán và không chấm chất lượng câu trả lời của bot. Một chunk chỉ "
            "liên quan chủ đề nhưng thiếu dữ kiện cụ thể phải là answerable=false. "
            "Nếu câu hỏi nhắc tên file hoặc thời điểm chỉ để định danh nguồn, nhưng chunk "
            "đã có đúng dữ kiện được hỏi, hãy coi là answerable; chỉ bắt buộc khớp thời "
            "điểm khi thời điểm đó có thể làm thay đổi đáp án. "
            "Trả đúng một JSON object: "
            '{"answerable":true|false,"confidence":0.0,"supporting_chunk_ids":[],"reason":"lý do ngắn"}.'
        )
        payload = json.dumps(
            {"question": question, "retrieved_chunks": evidence}, ensure_ascii=False,
        )
        generated = self._generate(
            self._config, system_prompt, [{"role": "user", "content": payload}],
        )
        text = generated.text if hasattr(generated, "text") else str(generated)
        verdict = AnswerabilityVerdict.model_validate(_parse_json(text))
        known_ids = {item["chunk_id"] for item in evidence}
        unsupported_ids = set(verdict.supporting_chunk_ids) - known_ids
        if unsupported_ids:
            raise ValueError(f"Judge cited unknown chunks: {sorted(unsupported_ids)}")
        if verdict.answerable and not verdict.supporting_chunk_ids:
            raise ValueError("Answerable verdict must cite at least one supporting chunk.")
        with self._lock:
            self.calls += 1
            self._cache[cache_key] = verdict
        return verdict

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "provider_calls": self.calls,
                "cache_hits": self.cache_hits,
                "cache_entries": len(self._cache),
            }
