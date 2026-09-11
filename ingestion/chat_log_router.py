"""H4-07 — route redacted chat Q&A into disjoint eval and knowledge stores."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ai_core.evaluator import load_cases
from index_chunks import load_chunks
from ingestion.chat_log_importer import ChatLogImportError, assert_no_pii, to_knowledge_chunks


ROUTING_VERSION = "h4-07-ranked-sha256-v1"
SEMANTIC_GROUP_VERSION = "h4-07-semantic-group-v1"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _route_score(tenant_id: str, pair_id: str) -> float:
    digest = hashlib.sha256(f"{tenant_id}:{pair_id}:{ROUTING_VERSION}".encode()).digest()
    return int.from_bytes(digest, "big") / (1 << (8 * len(digest)))


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    denominator = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / denominator if denominator else 0.0


def build_route_groups(
    pairs: list[dict[str, Any]], *, vectors: list[list[float]] | None = None,
    semantic_threshold: float = 0.88,
) -> list[list[dict[str, Any]]]:
    """Join conversation-related and semantically equivalent pairs before routing."""

    if vectors is not None and len(vectors) != len(pairs):
        raise ChatLogImportError("Số vector semantic không khớp số pair.")
    if not 0.0 <= semantic_threshold <= 1.0:
        raise ChatLogImportError("semantic_threshold phải nằm trong [0, 1].")
    parent = list(range(len(pairs)))

    def find(item: int) -> int:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: int, right: int) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[max(a, b)] = min(a, b)

    conversations: dict[str, int] = {}
    for index, pair in enumerate(pairs):
        for conversation_id in pair.get("source_conversation_ids", []):
            if conversation_id in conversations:
                union(index, conversations[conversation_id])
            else:
                conversations[conversation_id] = index
    if vectors is not None:
        for left in range(len(pairs)):
            for right in range(left + 1, len(pairs)):
                if _cosine(vectors[left], vectors[right]) >= semantic_threshold:
                    union(left, right)
    grouped: dict[int, list[dict[str, Any]]] = {}
    for index, pair in enumerate(pairs):
        grouped.setdefault(find(index), []).append(pair)
    return list(grouped.values())


def split_route_groups(
    groups: list[list[dict[str, Any]]], tenant_id: str, *, eval_ratio: float,
    previous_assignments: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    """Route whole groups and preserve prior pair assignments across incremental runs."""

    previous_assignments = previous_assignments or {}
    target_eval = max(1, round(sum(map(len, groups)) * eval_ratio))
    eval_pairs: list[dict[str, Any]] = []
    knowledge_pairs: list[dict[str, Any]] = []
    assignments: dict[str, str] = {}
    ordered = sorted(groups, key=lambda group: _route_score(
        tenant_id, min(str(pair["pair_id"]) for pair in group)
    ))
    for group in ordered:
        prior = {previous_assignments[pair["pair_id"]] for pair in group if pair["pair_id"] in previous_assignments}
        if len(prior) > 1:
            raise ChatLogImportError(
                "Rò semantic lịch sử: một nhóm mới nối pair eval với pair knowledge; cần duyệt thủ công."
            )
        destination = next(iter(prior), "eval" if len(eval_pairs) < target_eval else "knowledge")
        target = eval_pairs if destination == "eval" else knowledge_pairs
        target.extend(group)
        assignments.update({pair["pair_id"]: destination for pair in group})
    if not eval_pairs or not knowledge_pairs:
        raise ChatLogImportError("Routing phải tạo được cả nhánh eval và knowledge.")
    assert_disjoint_complete([pair for group in groups for pair in group], eval_pairs, knowledge_pairs)
    return eval_pairs, knowledge_pairs, assignments


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_redacted_pairs(path: Path, tenant_id: str) -> list[dict[str, Any]]:
    audit_path = path.parent / "audit.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ChatLogImportError(f"Thiếu Q&A staging hoặc audit H4-06: {path}") from exc
    expected_hash = ((audit.get("artifacts") or {}).get("qa_pairs_redacted_sha256"))
    if (
        audit.get("tenant_id") != tenant_id
        or audit.get("pii_scan_passed") is not True
        or expected_hash != _sha256_bytes(path.read_bytes())
    ):
        raise ChatLogImportError("Audit H4-06 không hợp lệ hoặc hash staging không khớp.")
    if not isinstance(raw, list) or not raw:
        raise ChatLogImportError("Q&A staging phải là JSON array không rỗng.")
    required = {"pair_id", "tenant_id", "question", "answer", "pii_redacted"}
    pairs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict) or not required.issubset(item):
            raise ChatLogImportError(f"Pair thứ {index} thiếu trường bắt buộc.")
        if item["tenant_id"] != tenant_id:
            raise ChatLogImportError(f"Pair thứ {index} không thuộc tenant {tenant_id!r}.")
        if item["pii_redacted"] is not True:
            raise ChatLogImportError(f"Pair thứ {index} chưa qua cổng che PII.")
        pair_id = str(item["pair_id"])
        if pair_id in seen:
            raise ChatLogImportError(f"Trùng pair_id: {pair_id}")
        if not str(item["question"]).strip() or not str(item["answer"]).strip():
            raise ChatLogImportError(f"Pair thứ {index} có question/answer rỗng.")
        seen.add(pair_id)
        pairs.append(dict(item))
    assert_no_pii(pairs)
    return pairs


def split_pairs(
    pairs: list[dict[str, Any]], tenant_id: str, *, eval_ratio: float = 0.20,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Assign every cluster to exactly one purpose with a stable hash."""

    if not 0.0 < eval_ratio < 1.0:
        raise ChatLogImportError("eval_ratio phải nằm trong khoảng (0, 1).")
    if len(pairs) < 2:
        raise ChatLogImportError("Cần tối thiểu 2 pair để tách eval và knowledge.")
    ranked = sorted(pairs, key=lambda pair: _route_score(tenant_id, pair["pair_id"]))
    eval_count = max(1, min(len(pairs) - 1, round(len(pairs) * eval_ratio)))
    eval_ids = {pair["pair_id"] for pair in ranked[:eval_count]}
    eval_pairs = [pair for pair in pairs if pair["pair_id"] in eval_ids]
    knowledge_pairs = [pair for pair in pairs if pair["pair_id"] not in eval_ids]
    assert_disjoint_complete(pairs, eval_pairs, knowledge_pairs)
    return eval_pairs, knowledge_pairs


def assert_disjoint_complete(
    source: list[dict[str, Any]], eval_pairs: list[dict[str, Any]],
    knowledge_pairs: list[dict[str, Any]],
) -> None:
    source_ids = {item["pair_id"] for item in source}
    eval_ids = {item["pair_id"] for item in eval_pairs}
    knowledge_ids = {item["pair_id"] for item in knowledge_pairs}
    if eval_ids & knowledge_ids:
        raise ChatLogImportError("Rò luồng: pair xuất hiện trong cả eval và knowledge.")
    if eval_ids | knowledge_ids != source_ids:
        raise ChatLogImportError("Routing làm thiếu hoặc sinh thêm pair ngoài input.")


def build_eval_cases(pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": f"H407-{pair['pair_id'].removeprefix('qa-').upper()}",
            "question": pair["question"],
            "type": "normal",
            "topic": "chat_log_derived",
            # A harmless always-negative gate satisfies the current evaluator
            # schema; semantic correctness is decided by the isolated rubric.
            "must_not_contain": ["[EVAL_LEAK_SENTINEL]"],
            "grading": "llm",
            "rubric": f"Câu trả lời phải truyền đạt đúng ý nghiệp vụ sau: {pair['answer']}",
            "pass_score": 0.8,
        }
        for pair in pairs
    ]


def _resolve_separate_dirs(output_root: Path) -> tuple[Path, Path]:
    root = output_root.resolve()
    eval_dir = (root / "eval_cases").resolve()
    knowledge_dir = (root / "knowledge").resolve()
    if eval_dir == knowledge_dir or eval_dir in knowledge_dir.parents or knowledge_dir in eval_dir.parents:
        raise ChatLogImportError("Kho eval và knowledge phải là hai thư mục tách biệt.")
    return eval_dir, knowledge_dir


def route_and_write(
    source: Path, tenant_id: str, output_root: Path, *, eval_ratio: float = 0.20,
    semantic_vectors: list[list[float]] | None = None, semantic_threshold: float = 0.88,
) -> dict[str, Any]:
    pairs = load_redacted_pairs(source, tenant_id)
    previous_assignments: dict[str, str] = {}
    previous_manifest = output_root / "routing_manifest.json"
    if previous_manifest.exists():
        try:
            previous = json.loads(previous_manifest.read_text(encoding="utf-8"))
            if previous.get("tenant_id") != tenant_id:
                raise ChatLogImportError("Output H4-07 hiện có thuộc tenant khác.")
            previous_assignments.update({str(item): "eval" for item in previous.get("eval_pair_ids", [])})
            previous_assignments.update({str(item): "knowledge" for item in previous.get("knowledge_pair_ids", [])})
        except json.JSONDecodeError as exc:
            raise ChatLogImportError("Manifest H4-07 cũ không đọc được; từ chối ghi đè.") from exc
    groups = build_route_groups(
        pairs, vectors=semantic_vectors, semantic_threshold=semantic_threshold,
    )
    eval_pairs, knowledge_pairs, assignments = split_route_groups(
        groups, tenant_id, eval_ratio=eval_ratio, previous_assignments=previous_assignments,
    )
    eval_cases = build_eval_cases(eval_pairs)
    updated_at = datetime.now(timezone.utc).date().isoformat()
    knowledge_chunks = to_knowledge_chunks(knowledge_pairs, tenant_id, updated_at)
    assert_no_pii({"eval_cases": eval_cases, "knowledge_chunks": knowledge_chunks})

    eval_questions = {item["question"] for item in eval_cases}
    knowledge_content = "\n".join(item["content"] for item in knowledge_chunks)
    if any(question in knowledge_content for question in eval_questions):
        raise ChatLogImportError("Rò nội dung: câu hỏi eval xuất hiện trong knowledge output.")

    eval_dir, knowledge_dir = _resolve_separate_dirs(output_root)
    eval_dir.mkdir(parents=True, exist_ok=True)
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    eval_path = eval_dir / "cases.yaml"
    knowledge_path = knowledge_dir / "knowledge_chunks.json"
    _write_text_atomic(eval_path,
        "# H4-07 EVAL ONLY — cấm đưa vào embedding/knowledge index.\n"
        + yaml.safe_dump(eval_cases, allow_unicode=True, sort_keys=False),
    )
    _write_text_atomic(
        knowledge_path, json.dumps(knowledge_chunks, ensure_ascii=False, indent=2)
    )
    # Validate each artifact through its own production consumer.
    load_cases(eval_path)
    load_chunks(knowledge_path)

    eval_purpose = {
        "purpose": "evaluation_only",
        "allowed_consumers": ["ai_core.evaluator"],
        "forbidden_consumers": ["embedder", "retriever", "knowledge_index"],
        "tenant_id": tenant_id,
    }
    knowledge_purpose = {
        "purpose": "knowledge_only",
        "allowed_consumers": ["index_chunks", "retriever"],
        "forbidden_consumers": ["eval_case_loader", "evaluation_scoring"],
        "tenant_id": tenant_id,
    }
    _write_text_atomic(eval_dir / "PURPOSE.json", json.dumps(eval_purpose, ensure_ascii=False, indent=2))
    _write_text_atomic(knowledge_dir / "PURPOSE.json", json.dumps(knowledge_purpose, ensure_ascii=False, indent=2))
    review = {
        "tenant_id": tenant_id, "status": "draft", "approved_at": None,
        "approved_by": None, "note": "Phải được người có thẩm quyền duyệt trước khi kích hoạt.",
    }
    _write_text_atomic(eval_dir / "REVIEW.json", json.dumps(review, ensure_ascii=False, indent=2))
    _write_text_atomic(knowledge_dir / "REVIEW.json", json.dumps(review, ensure_ascii=False, indent=2))

    eval_ids = sorted(pair["pair_id"] for pair in eval_pairs)
    knowledge_ids = sorted(pair["pair_id"] for pair in knowledge_pairs)
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tenant_id": tenant_id,
        "source_data_label": (
            "TEST/SYNTHETIC" if all(pair.get("source_data_label") == "TEST/SYNTHETIC" for pair in pairs)
            else "UNSPECIFIED_EXPORT"
        ),
        "routing_version": ROUTING_VERSION,
        "semantic_group_version": SEMANTIC_GROUP_VERSION,
        "semantic_group_count": len(groups),
        "semantic_check": "embedding" if semantic_vectors is not None else "conversation_only",
        "semantic_threshold": semantic_threshold,
        "eval_ratio_requested": eval_ratio,
        "input_pair_count": len(pairs),
        "eval_case_count": len(eval_cases),
        "knowledge_chunk_count": len(knowledge_chunks),
        "eval_pair_ids": eval_ids,
        "knowledge_pair_ids": knowledge_ids,
        "assignments": assignments,
        "invariants": {
            "pair_ids_disjoint": not bool(set(eval_ids) & set(knowledge_ids)),
            "all_input_pairs_routed_once": len(eval_ids) + len(knowledge_ids) == len(pairs),
            "eval_questions_absent_from_knowledge": True,
            "tenant_isolation": True,
            "pii_scan_passed": True,
        },
        "artifacts": {
            "eval_cases": str(eval_path), "knowledge_chunks": str(knowledge_path),
            "eval_sha256": _sha256_bytes(eval_path.read_bytes()),
            "knowledge_sha256": _sha256_bytes(knowledge_path.read_bytes()),
        },
    }
    manifest_path = output_root / "routing_manifest.json"
    # Commit marker is replaced last: consumers never see a manifest claiming
    # success before both stores and their guards have been fully written.
    _write_text_atomic(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="H4-07 tách log chat thành eval và knowledge.")
    parser.add_argument("source", type=Path, help="qa_pairs.redacted.json từ H4-06")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--output-root", type=Path, default=Path("outputs/h4_07"))
    parser.add_argument("--eval-ratio", type=float, default=0.20)
    parser.add_argument("--semantic-threshold", type=float, default=0.88)
    parser.add_argument(
        "--skip-semantic-embedding", action="store_true",
        help="Chỉ dành cho test offline; production phải kiểm tra gần nghĩa bằng embedding.",
    )
    args = parser.parse_args()
    try:
        vectors = None
        if not args.skip_semantic_embedding:
            from ai_core.config import load_config
            from ai_core.embedder import embed_texts
            config = load_config(args.tenant_id)
            errors: list[str] = []
            texts = [f"Câu hỏi: {pair['question']}\nTrả lời: {pair['answer']}" for pair in load_redacted_pairs(args.source, args.tenant_id)]
            for embedding in (config.embedding_policy.primary, config.embedding_policy.fallback):
                try:
                    vectors = embed_texts(
                        texts, model=embedding.model, provider=embedding.provider,
                        task_type="SEMANTIC_SIMILARITY" if embedding.provider == "gemini" else None,
                    )
                    break
                except Exception as exc:
                    errors.append(f"{embedding.provider}/{embedding.model}: {exc}")
            if vectors is None:
                raise ChatLogImportError("Cả hai embedding provider đều lỗi: " + " | ".join(errors))
        manifest = route_and_write(
            args.source, args.tenant_id, args.output_root, eval_ratio=args.eval_ratio,
            semantic_vectors=vectors, semantic_threshold=args.semantic_threshold,
        )
    except (ChatLogImportError, OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(f"H4-07 lỗi: {exc}")
        return 2
    print(
        f"H4-07: {manifest['input_pair_count']} pairs -> "
        f"{manifest['eval_case_count']} eval-only + "
        f"{manifest['knowledge_chunk_count']} knowledge-only; invariants=PASS"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
