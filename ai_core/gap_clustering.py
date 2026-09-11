"""Gom cụm knowledge gap theo tenant bằng embedding similarity.

Thuật toán dùng complete-linkage: hai cụm chỉ được nhập khi *mọi* cặp câu hỏi
chéo đều đạt ngưỡng. Cách này bảo thủ hơn nối thành phần liên thông và tránh một
câu trung gian kéo hai chủ đề khác nhau vào cùng cụm.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import numpy as np

from ai_core.config import load_config
from ai_core.embedder import embed_texts


DEFAULT_SIMILARITY_THRESHOLD = 0.85
MAX_CLUSTER_NAME_LENGTH = 80
EmbedCallable = Callable[..., list[list[float]]]
ClusterNamer = Callable[[str, Sequence[dict[str, Any]]], Mapping[str, str]]
ACTIONABLE_GAP_REASONS = frozenset({"below_threshold", "no_match", "fallback_response"})
_NON_KNOWLEDGE_QUESTIONS = frozenset(
    {
        "alo",
        "cam on",
        "chao",
        "hello",
        "hi",
        "xin chao",
    }
)


class GapClusteringError(RuntimeError):
    """Dữ liệu gap, embedding hoặc phản hồi đặt tên cụm không hợp lệ."""


def normalize_question(question: str) -> str:
    normalized = unicodedata.normalize("NFKC", question).casefold()
    normalized = re.sub(r"[^\w\s]", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


def partition_actionable_gaps(
    gaps: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Tách gap tri thức thật khỏi lỗi hạ tầng và lời chào không cần tenant trả lời."""

    included: list[dict[str, Any]] = []
    excluded: Counter[str] = Counter()
    for raw in gaps:
        row = dict(raw)
        reason = str(row.get("reason") or "unknown")
        normalized = normalize_question(str(row.get("question") or ""))
        ascii_question = "".join(
            character
            for character in unicodedata.normalize("NFD", normalized)
            if unicodedata.category(character) != "Mn"
        ).replace("đ", "d")
        if reason not in ACTIONABLE_GAP_REASONS:
            excluded[f"reason:{reason}"] += 1
        elif ascii_question in _NON_KNOWLEDGE_QUESTIONS:
            excluded["non_knowledge:greeting"] += 1
        else:
            included.append(row)
    return included, dict(sorted(excluded.items()))


def _apply_manual_groups(
    memberships: list[list[int]],
    normalized_questions: Sequence[str],
    manual_groups: Sequence[Mapping[str, Any]],
) -> tuple[list[list[int]], dict[tuple[int, ...], dict[str, str]]]:
    """Áp dụng các nhóm đã kiểm tay; câu ngoài review giữ membership embedding."""

    index_by_question = {question: index for index, question in enumerate(normalized_questions)}
    covered: set[int] = set()
    reviewed: list[list[int]] = []
    metadata: dict[tuple[int, ...], dict[str, str]] = {}
    for position, raw_group in enumerate(manual_groups, start=1):
        name = str(raw_group.get("name") or "").strip()
        questions = raw_group.get("questions")
        status = str(raw_group.get("review_status") or "approved").strip()
        notes = str(raw_group.get("review_notes") or "").strip()
        if not name or not isinstance(questions, list) or not questions:
            raise GapClusteringError(f"manual group {position} thiếu name/questions")
        if status != "approved":
            raise GapClusteringError(
                f"manual group {position} phải có review_status=approved sau khi tách"
            )
        indices: list[int] = []
        for question in questions:
            normalized = normalize_question(str(question))
            if normalized not in index_by_question:
                raise GapClusteringError(
                    f"manual group {position} chứa câu không có trong gap: {question!r}"
                )
            index = index_by_question[normalized]
            if index in covered:
                raise GapClusteringError(
                    f"manual group {position} lặp câu đã được review: {question!r}"
                )
            covered.add(index)
            indices.append(index)
        key = tuple(sorted(indices))
        reviewed.append(list(key))
        metadata[key] = {"name": name[:MAX_CLUSTER_NAME_LENGTH], "notes": notes}

    remainder = [
        [index for index in membership if index not in covered]
        for membership in memberships
    ]
    return [*reviewed, *[item for item in remainder if item]], metadata


def _validate_gaps(gaps: Sequence[Mapping[str, Any]], tenant_id: str) -> list[dict[str, Any]]:
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise GapClusteringError("tenant_id là bắt buộc")
    validated: list[dict[str, Any]] = []
    for index, raw in enumerate(gaps):
        row = dict(raw)
        if row.get("tenant_id") != tenant_id:
            raise GapClusteringError(
                f"gap thứ {index + 1} không thuộc tenant {tenant_id!r}; từ chối gom chéo tenant"
            )
        question = row.get("question")
        if not isinstance(question, str) or not normalize_question(question):
            raise GapClusteringError(f"gap thứ {index + 1} thiếu question hợp lệ")
        validated.append(row)
    return validated


def _embed_questions(
    questions: list[str],
    *,
    embed_fn: EmbedCallable,
    model: str,
    provider: str,
) -> np.ndarray:
    parameters = inspect.signature(embed_fn).parameters
    kwargs: dict[str, str] = {}
    if "model" in parameters:
        kwargs["model"] = model
    if "provider" in parameters:
        kwargs["provider"] = provider
    if "task_type" in parameters:
        kwargs["task_type"] = "CLUSTERING"
    try:
        raw_vectors = embed_fn(questions, **kwargs)
    except Exception as exc:
        if isinstance(exc, GapClusteringError):
            raise
        raise GapClusteringError(f"Không sinh được embedding cho knowledge gap: {exc}") from exc
    vectors = np.asarray(raw_vectors, dtype="float64")
    if vectors.ndim != 2 or vectors.shape[0] != len(questions) or vectors.shape[1] == 0:
        raise GapClusteringError("Embedding phải có dạng N x D và đủ một vector cho mỗi câu hỏi")
    if not np.isfinite(vectors).all():
        raise GapClusteringError("Embedding chứa NaN hoặc infinity")
    norms = np.linalg.norm(vectors, axis=1)
    if np.any(norms == 0):
        raise GapClusteringError("Embedding không được là vector 0")
    return vectors / norms[:, None]


def _complete_linkage(similarities: np.ndarray, threshold: float) -> list[list[int]]:
    clusters: list[list[int]] = [[index] for index in range(similarities.shape[0])]
    while True:
        best: tuple[float, int, int] | None = None
        for left in range(len(clusters)):
            for right in range(left + 1, len(clusters)):
                minimum = min(
                    float(similarities[a, b])
                    for a in clusters[left]
                    for b in clusters[right]
                )
                candidate = (minimum, -left, -right)
                if minimum >= threshold and (best is None or candidate > best):
                    best = candidate
        if best is None:
            break
        left = -best[1]
        right = -best[2]
        clusters[left] = sorted([*clusters[left], *clusters[right]])
        del clusters[right]
    return clusters


def _fallback_cluster_name(representative: str) -> str:
    clean = representative.strip().rstrip("?.!")
    clean = re.sub(
        r"^(?:cho (?:anh|chị|tôi) hỏi\s+|(?:bên mình|công ty|bên bạn)\s+|"
        r"(?:anh|chị|tôi|mình) muốn biết\s+)",
        "",
        clean,
        flags=re.IGNORECASE,
    ).strip()
    if not clean:
        clean = representative.strip()
    return clean[:MAX_CLUSTER_NAME_LENGTH].rstrip()


def _cluster_id(tenant_id: str, normalized_questions: Sequence[str]) -> str:
    material = tenant_id + "\n" + "\n".join(sorted(normalized_questions))
    return "gap-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def cluster_tenant_gaps(
    gaps: Sequence[Mapping[str, Any]],
    tenant_id: str,
    *,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    embed_fn: EmbedCallable = embed_texts,
    model: str | None = None,
    provider: str | None = None,
    namer: ClusterNamer | None = None,
    manual_groups: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Gom gap của đúng một tenant và trả các cụm đã xếp theo tần suất."""

    if not math.isfinite(similarity_threshold) or not 0 <= similarity_threshold <= 1:
        raise GapClusteringError("similarity_threshold phải nằm trong khoảng 0..1")
    rows = _validate_gaps(gaps, tenant_id)
    if not rows:
        return []

    grouped: dict[str, list[dict[str, Any]]] = {}
    display_questions: dict[str, str] = {}
    for row in rows:
        normalized = normalize_question(str(row["question"]))
        grouped.setdefault(normalized, []).append(row)
        display_questions.setdefault(normalized, str(row["question"]).strip())
    normalized_questions = sorted(grouped)

    config = load_config(tenant_id)
    primary = config.embedding_policy.primary
    fallback = config.embedding_policy.fallback
    requested_spec = (model or primary.model, provider or primary.provider)
    embedding_specs = [requested_spec]
    if model is None and provider is None:
        fallback_spec = (fallback.model, fallback.provider)
        if fallback_spec != requested_spec:
            embedding_specs.append(fallback_spec)
    embedding_error: GapClusteringError | None = None
    vectors: np.ndarray | None = None
    selected_model = requested_spec[0]
    selected_provider = requested_spec[1]
    for candidate_model, candidate_provider in embedding_specs:
        try:
            vectors = _embed_questions(
                [display_questions[item] for item in normalized_questions],
                embed_fn=embed_fn,
                model=candidate_model,
                provider=candidate_provider,
            )
            selected_model = candidate_model
            selected_provider = candidate_provider
            break
        except GapClusteringError as exc:
            embedding_error = exc
    if vectors is None:
        raise embedding_error or GapClusteringError("Không sinh được embedding")
    similarities = np.clip(vectors @ vectors.T, -1.0, 1.0)
    memberships = _complete_linkage(similarities, similarity_threshold)
    memberships, manual_metadata = _apply_manual_groups(
        memberships,
        normalized_questions,
        manual_groups,
    )

    clusters: list[dict[str, Any]] = []
    for members in memberships:
        member_questions = [normalized_questions[index] for index in members]
        member_rows = [row for question in member_questions for row in grouped[question]]
        counts = {question: len(grouped[question]) for question in member_questions}
        representative_key = min(member_questions, key=lambda item: (-counts[item], item))
        pair_scores = [
            float(similarities[left, right])
            for offset, left in enumerate(members)
            for right in members[offset + 1 :]
        ]
        scores = [
            float(row["top_score"])
            for row in member_rows
            if row.get("top_score") is not None
        ]
        timestamps = [
            str(row["occurred_at"])
            for row in member_rows
            if row.get("occurred_at")
        ]
        reasons = Counter(str(row.get("reason") or "unknown") for row in member_rows)
        ordered_examples = sorted(
            member_questions,
            key=lambda item: (-counts[item], item),
        )
        representative = display_questions[representative_key]
        manual = manual_metadata.get(tuple(members))
        clusters.append(
            {
                "cluster_id": _cluster_id(tenant_id, member_questions),
                "tenant_id": tenant_id,
                "embedding_model": selected_model,
                "embedding_provider": selected_provider,
                "name": manual["name"] if manual else _fallback_cluster_name(representative),
                "naming_method": "manual" if manual else "fallback",
                "frequency": len(member_rows),
                "unique_question_count": len(member_questions),
                "representative_question": representative,
                "examples": [display_questions[item] for item in ordered_examples[:5]],
                "minimum_similarity": round(min(pair_scores), 6) if pair_scores else 1.0,
                "average_top_score": round(sum(scores) / len(scores), 6) if scores else None,
                "maximum_top_score": round(max(scores), 6) if scores else None,
                "reason_counts": dict(sorted(reasons.items())),
                "first_seen": min(timestamps) if timestamps else None,
                "last_seen": max(timestamps) if timestamps else None,
                "review_required": bool(manual),
                "review_status": "approved" if manual else "not_required",
                "review_notes": manual["notes"] if manual else "",
            }
        )

    clusters.sort(
        key=lambda item: (
            -item["frequency"],
            -item["unique_question_count"],
            item["cluster_id"],
        )
    )
    for rank, cluster in enumerate(clusters, start=1):
        cluster["rank"] = rank
        if rank <= 10 and cluster["review_status"] != "approved":
            cluster["review_required"] = True
            cluster["review_status"] = "needs_manual_review"

    if namer is not None:
        naming_candidates = [
            cluster for cluster in clusters if cluster["naming_method"] != "manual"
        ]
        try:
            names = dict(namer(tenant_id, naming_candidates)) if naming_candidates else {}
        except Exception as exc:
            raise GapClusteringError(f"Không đặt được tên cụm: {exc}") from exc
        for cluster in clusters:
            proposed = names.get(cluster["cluster_id"])
            if isinstance(proposed, str) and proposed.strip():
                cluster["name"] = proposed.strip()[:MAX_CLUSTER_NAME_LENGTH]
                cluster["naming_method"] = "llm"
    return clusters


def llm_cluster_namer(
    tenant_id: str,
    clusters: Sequence[dict[str, Any]],
) -> Mapping[str, str]:
    """Đặt tên ngắn cho cụm bằng LLM cấu hình của tenant, theo batch 20 cụm."""

    from ai_core.chat import _generate_with_fallback

    config = load_config(tenant_id)
    names: dict[str, str] = {}
    system_prompt = (
        "Bạn đặt nhãn chủ đề cho các nhóm câu hỏi khách hàng. Trả về duy nhất JSON object "
        "ánh xạ cluster_id sang tên tiếng Việt 3-8 từ. Không thêm thông tin không có trong ví dụ."
    )
    for start in range(0, len(clusters), 20):
        batch = clusters[start : start + 20]
        payload = [
            {
                "cluster_id": item["cluster_id"],
                "representative": item["representative_question"],
                "examples": item["examples"],
            }
            for item in batch
        ]
        result = _generate_with_fallback(
            config,
            system_prompt,
            [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        )
        text = result.text.strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
        if fenced:
            text = fenced.group(1)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise GapClusteringError("LLM đặt tên cụm không trả JSON hợp lệ") from exc
        if not isinstance(parsed, dict):
            raise GapClusteringError("LLM đặt tên cụm phải trả JSON object")
        allowed = {item["cluster_id"] for item in batch}
        for cluster_id, name in parsed.items():
            if cluster_id in allowed and isinstance(name, str) and name.strip():
                names[cluster_id] = name.strip()[:MAX_CLUSTER_NAME_LENGTH]
    return names


def build_cluster_report(
    tenant_clusters: Mapping[str, Sequence[dict[str, Any]]],
    *,
    similarity_threshold: float,
    source: str,
) -> dict[str, Any]:
    return {
        "schema_version": "knowledge-gap-clusters.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "similarity_threshold": similarity_threshold,
        "source": source,
        "tenants": {
            tenant_id: {
                "total_gaps": sum(item["frequency"] for item in clusters),
                "cluster_count": len(clusters),
                "top_missing_topics": list(clusters[:10]),
                "clusters": list(clusters),
            }
            for tenant_id, clusters in sorted(tenant_clusters.items())
        },
    }


__all__ = [
    "DEFAULT_SIMILARITY_THRESHOLD",
    "GapClusteringError",
    "build_cluster_report",
    "cluster_tenant_gaps",
    "llm_cluster_namer",
    "normalize_question",
    "partition_actionable_gaps",
]
