"""H4-06 — import useful Q&A knowledge from legacy chat exports.

Supported inputs are CSV, JSON arrays and JSONL.  The importer accepts either
one message per row (conversation_id/role/message) or one Q&A per row
(conversation_id/question/answer).  PII redaction happens before filtering,
clustering, or writing any derived artifact.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence
from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid5

from ai_core.models import KnowledgeChunk


LLMGenerate = Callable[[Any, str, Sequence[dict[str, str]]], Any]


EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PHONE_RE = re.compile(r"(?<!\d)(?:\+?84|0)(?:[\s.\-]?\d){8,10}(?!\d)")
SELF_NAME_RE = re.compile(
    r"\b(?i:tôi|em|mình)\s+(?:(?i:tên)\s+)?(?:(?i:là)\s+)?"
    r"([^\W\d_]+(?:\s+[^\W\d_]+){1,4})",
)
TENANT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
ROLE_USER = {"user", "customer", "khach", "khách", "client", "visitor"}
ROLE_ASSISTANT = {"assistant", "agent", "staff", "admin", "page", "oa", "support"}
ROLE_SYSTEM = {"system", "event", "bot_event"}
GREETING_RE = re.compile(
    r"^(?:xin\s+)?(?:chào|hello|hi|alo|cảm ơn|cam on|ok|oke|vâng|vang|dạ|da)[!,. ]*$",
    re.I,
)
LOW_VALUE_ANSWER_RE = re.compile(
    r"^(?:dạ|vâng|ok|cảm ơn|xin chào|bạn vui lòng chờ|đã nhận thông tin)[!,. ]*$",
    re.I,
)
FIELD_ALIASES = {
    "conversation_id": ("conversation_id", "conversationId", "thread_id", "threadId"),
    "role": ("role", "sender_type", "from_role", "author_type"),
    "message": ("message", "text", "content", "body"),
    "timestamp": ("timestamp", "created_at", "time", "sent_at"),
    "customer_name": ("customer_name", "sender_name", "display_name", "name"),
    "question": ("question", "customer_message", "user_message"),
    "answer": ("answer", "agent_message", "staff_reply", "reply"),
    "tenant_id": ("tenant_id", "tenantId"),
}


class ChatLogImportError(ValueError):
    pass


@dataclass(frozen=True)
class RedactionStats:
    phones: int = 0
    emails: int = 0
    names: int = 0

    def __add__(self, other: "RedactionStats") -> "RedactionStats":
        return RedactionStats(
            self.phones + other.phones,
            self.emails + other.emails,
            self.names + other.names,
        )


def _value(record: dict[str, Any], field: str) -> str:
    for key in FIELD_ALIASES[field]:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFD", text.casefold().replace("đ", "d"))
    value = "".join(char for char in value if unicodedata.category(char) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def redact_pii(text: str, customer_names: Iterable[str] = ()) -> tuple[str, RedactionStats]:
    """Redact phone, email and known/self-declared end-customer names."""

    value = str(text)
    value, email_count = EMAIL_RE.subn("[REDACTED_EMAIL]", value)
    value, phone_count = PHONE_RE.subn("[REDACTED_PHONE]", value)
    name_count = 0
    for name in sorted({item.strip() for item in customer_names if len(item.strip()) >= 3}, key=len, reverse=True):
        value, count = re.subn(rf"(?<!\w){re.escape(name)}(?!\w)", "[REDACTED_NAME]", value, flags=re.I)
        name_count += count

    def replace_self_name(match: re.Match[str]) -> str:
        nonlocal name_count
        candidate = match.group(1)
        words = candidate.split()
        name_words: list[str] = []
        for word in words:
            if word and word[0].isupper():
                name_words.append(word)
            else:
                break
        if len(name_words) < 2:
            return match.group(0)
        name_count += 1
        prefix = match.group(0)[: match.start(1) - match.start(0)]
        name_text = " ".join(name_words)
        return f"{prefix}[REDACTED_NAME]{candidate[len(name_text):]}"

    value = SELF_NAME_RE.sub(replace_self_name, value)
    return value.strip(), RedactionStats(phone_count, email_count, name_count)


def _read_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise ChatLogImportError(f"Không tìm thấy log chat: {path}")
    suffix = path.suffix.casefold()
    if suffix == ".csv":
        text = path.read_text(encoding="utf-8-sig")
        header = text.splitlines()[0] if text.splitlines() else text
        try:
            dialect = csv.Sniffer().sniff(header, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        return [dict(row) for row in csv.DictReader(io.StringIO(text), dialect=dialect)]
    if suffix == ".json":
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw = raw.get("messages") or raw.get("data") or raw.get("conversations")
        if not isinstance(raw, list):
            raise ChatLogImportError("JSON phải là array hoặc có trường messages/data/conversations.")
        return [dict(item) for item in raw if isinstance(item, dict)]
    if suffix == ".jsonl":
        return [
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    raise ChatLogImportError("Chỉ hỗ trợ export .csv, .json hoặc .jsonl.")


def _validate_tenants(records: list[dict[str, Any]], tenant_id: str) -> None:
    if not TENANT_RE.fullmatch(tenant_id):
        raise ChatLogImportError("tenant_id không hợp lệ.")
    mismatches = sorted({
        value for row in records if (value := _value(row, "tenant_id")) and value != tenant_id
    })
    if mismatches:
        raise ChatLogImportError(
            f"Log chứa tenant khác {tenant_id!r}: {', '.join(mismatches)}"
        )


def _is_valuable(question: str, answer: str) -> bool:
    return bool(
        len(question) >= 8
        and len(answer) >= 15
        and not GREETING_RE.fullmatch(question.strip())
        and not LOW_VALUE_ANSWER_RE.fullmatch(answer.strip())
    )


def extract_pairs(records: list[dict[str, Any]], tenant_id: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Extract redacted adjacent user→agent pairs; never return raw PII."""

    _validate_tenants(records, tenant_id)
    names = {
        _value(row, "customer_name") for row in records
        if _value(row, "customer_name") and _value(row, "role").casefold() not in ROLE_ASSISTANT
    }
    redactions = RedactionStats()
    candidates: list[dict[str, Any]] = []

    # Pair-per-row exports are common in CRM/reporting tools.
    if any(_value(row, "question") and _value(row, "answer") for row in records):
        for index, row in enumerate(records, start=1):
            question, q_stats = redact_pii(_value(row, "question"), names)
            answer, a_stats = redact_pii(_value(row, "answer"), names)
            redactions += q_stats + a_stats
            if _is_valuable(question, answer):
                candidates.append({
                    "question": question, "answer": answer,
                    "conversation_id": _value(row, "conversation_id") or f"row-{index}",
                    "timestamp": _value(row, "timestamp"),
                })
    else:
        conversations: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
        for index, row in enumerate(records):
            conversation_id = _value(row, "conversation_id")
            if not conversation_id:
                raise ChatLogImportError(f"Bản ghi {index + 1} thiếu conversation_id.")
            conversations[conversation_id].append((index, row))
        for conversation_id, entries in conversations.items():
            entries.sort(key=lambda item: (_value(item[1], "timestamp"), item[0]))
            pending_questions: list[str] = []
            pending_answers: list[str] = []

            def flush() -> None:
                nonlocal redactions
                if not pending_questions or not pending_answers:
                    pending_questions.clear(); pending_answers.clear(); return
                question, q_stats = redact_pii(" ".join(pending_questions), names)
                answer, a_stats = redact_pii(" ".join(pending_answers), names)
                redactions += q_stats + a_stats
                if _is_valuable(question, answer):
                    candidates.append({"question": question, "answer": answer,
                                       "conversation_id": conversation_id, "timestamp": ""})
                pending_questions.clear(); pending_answers.clear()

            for _, row in entries:
                role = _value(row, "role").casefold()
                message = _value(row, "message")
                if role in ROLE_SYSTEM or not message:
                    continue
                if role in ROLE_USER:
                    if pending_answers:
                        flush()
                    pending_questions.append(message)
                elif role in ROLE_ASSISTANT and pending_questions:
                    pending_answers.append(message)
            flush()

    return candidates, {
        "raw_records": len(records), "candidate_pairs": len(candidates),
        "pii_phone_replacements": redactions.phones,
        "pii_email_replacements": redactions.emails,
        "pii_name_replacements": redactions.names,
    }


def _similarity(left: str, right: str) -> float:
    a, b = _normalize(left), _normalize(right)
    a_tokens, b_tokens = set(a.split()), set(b.split())
    jaccard = len(a_tokens & b_tokens) / len(a_tokens | b_tokens) if a_tokens | b_tokens else 0.0
    return max(jaccard, SequenceMatcher(None, a, b).ratio())


def cluster_pairs(pairs: list[dict[str, Any]], tenant_id: str, threshold: float = 0.86) -> list[dict[str, Any]]:
    if not 0.0 <= threshold <= 1.0:
        raise ChatLogImportError("cluster_threshold phải nằm trong [0, 1].")
    clusters: list[list[dict[str, Any]]] = []
    for pair in pairs:
        target = next(
            (cluster for cluster in clusters if _similarity(pair["question"], cluster[0]["question"]) >= threshold),
            None,
        )
        (target if target is not None else clusters.append([]) or clusters[-1]).append(pair)

    output: list[dict[str, Any]] = []
    for cluster in clusters:
        representative = max(
            cluster,
            key=lambda item: (min(len(item["answer"]), 700) + 30 * bool(re.search(r"\d", item["answer"])), len(item["question"])),
        )
        digest = hashlib.sha256(_normalize(representative["question"]).encode()).hexdigest()[:16]
        output.append({
            "pair_id": f"qa-{digest}", "tenant_id": tenant_id,
            "question": representative["question"], "answer": representative["answer"],
            "question_variants": sorted({item["question"] for item in cluster}),
            "source_conversation_ids": sorted({item["conversation_id"] for item in cluster}),
            "occurrence_count": len(cluster), "pii_redacted": True,
            "source_data_label": "TEST/SYNTHETIC" if any(
                str(item["conversation_id"]).startswith("test-") for item in cluster
            ) else "UNSPECIFIED_EXPORT",
        })
    return output


def _parse_json_object(text: str) -> dict[str, Any]:
    """Parse a model JSON response without accepting explanatory prose."""

    value = text.strip()
    if re.match(r"^```(?:json)?\s*", value, flags=re.I):
        value = re.sub(r"^```(?:json)?\s*", "", value, count=1, flags=re.I)
        value = re.sub(r"\s*```$", "", value, count=1).strip()
    if not value:
        raise ChatLogImportError("LLM H4-06 trả nội dung rỗng thay vì JSON.")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        preview = re.sub(r"\s+", " ", value)[:120]
        raise ChatLogImportError(
            f"LLM H4-06 trả nội dung không phải JSON (vị trí {exc.pos}): {preview!r}"
        ) from exc
    if not isinstance(parsed, dict):
        raise ChatLogImportError("LLM H4-06 phải trả về một JSON object.")
    return parsed


def enrich_pairs_with_llm(
    pairs: list[dict[str, Any]], tenant_id: str, *,
    config_version: int = 1, batch_size: int = 5,
    generate_fn: LLMGenerate | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Use an LLM to retain reusable facts and shorten answers, fail closed.

    This function receives only already-redacted pairs. It deliberately keeps
    the public KnowledgeChunk schema unchanged; review details live on the
    staging pair and in the audit manifest.
    """

    if batch_size < 1 or batch_size > 25:
        raise ChatLogImportError("llm_batch_size phải nằm trong [1, 25].")
    if generate_fn is None:
        # Import lazily so pure parsing/redaction users do not need provider SDKs.
        from ai_core.chat import _generate_with_fallback
        generate_fn = _generate_with_fallback
    from ai_core.config import load_config

    config = load_config(tenant_id, config_version)
    system_prompt = (
        "Bạn là bộ biên tập tri thức từ log chăm sóc khách hàng. Chỉ giữ cặp "
        "có thông tin nghiệp vụ có thể tái sử dụng cho nhiều khách. Loại chào hỏi, "
        "hẹn chờ, chuyển nhân viên, câu chỉ đúng cho một khách/đơn hàng và câu không "
        "chứa câu trả lời thực tế. Viết answer gọn, đủ ý, không thêm hoặc suy diễn "
        "dữ kiện. Giữ cách xưng hô và giọng điệu của answer gốc. Không khôi phục PII. "
        "Trả duy nhất JSON đúng dạng: {\"items\":[{\"pair_id\":\"...\","
        "\"keep\":true,\"rewritten_answer\":\"...\",\"reason\":\"...\","
        "\"confidence\":0.0}]}. Với keep=false, rewritten_answer phải là chuỗi rỗng."
    )
    accepted: list[dict[str, Any]] = []
    rejected = 0
    calls = 0
    tokens_in = 0
    tokens_out = 0
    models: Counter[str] = Counter()
    for start in range(0, len(pairs), batch_size):
        batch = pairs[start:start + batch_size]
        request_items = [
            {"pair_id": item["pair_id"], "question": item["question"], "answer": item["answer"]}
            for item in batch
        ]
        try:
            result = generate_fn(config, system_prompt, [{
                "role": "user",
                "content": json.dumps({"tenant_id": tenant_id, "items": request_items}, ensure_ascii=False),
            }])
        except Exception as exc:
            raise ChatLogImportError(f"LLM H4-06 thất bại; không ghi output một phần: {exc}") from exc
        calls += 1
        tokens_in += int(getattr(result, "tokens_in", 0) or 0)
        tokens_out += int(getattr(result, "tokens_out", 0) or 0)
        models[str(getattr(result, "model", "unknown"))] += 1
        payload = _parse_json_object(str(getattr(result, "text", result)))
        decisions = payload.get("items")
        if not isinstance(decisions, list):
            raise ChatLogImportError("LLM H4-06 thiếu array items.")
        by_id: dict[str, dict[str, Any]] = {}
        expected = {item["pair_id"] for item in batch}
        for decision in decisions:
            if not isinstance(decision, dict) or str(decision.get("pair_id", "")) not in expected:
                raise ChatLogImportError("LLM H4-06 trả pair_id lạ hoặc item sai dạng.")
            pair_id = str(decision["pair_id"])
            if pair_id in by_id:
                raise ChatLogImportError(f"LLM H4-06 trả trùng pair_id: {pair_id}")
            by_id[pair_id] = decision
        if set(by_id) != expected:
            raise ChatLogImportError("LLM H4-06 không trả quyết định cho đủ mọi pair.")

        for pair in batch:
            decision = by_id[pair["pair_id"]]
            keep = decision.get("keep")
            confidence = decision.get("confidence")
            if not isinstance(keep, bool) or isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                raise ChatLogImportError("LLM H4-06 trả keep/confidence sai kiểu.")
            confidence = float(confidence)
            if not 0.0 <= confidence <= 1.0:
                raise ChatLogImportError("LLM H4-06 trả confidence ngoài [0, 1].")
            reason = str(decision.get("reason", "")).strip()
            rewritten = str(decision.get("rewritten_answer", "")).strip()
            if not reason:
                raise ChatLogImportError("LLM H4-06 thiếu lý do kiểm duyệt.")
            if not keep:
                rejected += 1
                continue
            if len(rewritten) < 10:
                raise ChatLogImportError(f"LLM H4-06 trả answer quá ngắn cho {pair['pair_id']}.")
            try:
                assert_no_pii({"answer": rewritten})
            except ChatLogImportError as exc:
                raise ChatLogImportError(
                    f"LLM H4-06 tạo dữ liệu giống PII cho {pair['pair_id']}; từ chối toàn bộ output."
                ) from exc
            enriched = dict(pair)
            enriched["answer"] = rewritten
            enriched["llm_review"] = {
                "keep": True, "reason": reason, "confidence": confidence,
            }
            accepted.append(enriched)

    assert_no_pii(accepted)
    return accepted, {
        "enabled": True, "input_pairs": len(pairs), "accepted_pairs": len(accepted),
        "rejected_pairs": rejected, "provider_calls": calls,
        "models": dict(models), "tokens_in": tokens_in, "tokens_out": tokens_out,
        "batch_size": batch_size,
    }


def to_knowledge_chunks(pairs: list[dict[str, Any]], tenant_id: str, updated_at: str) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for pair in pairs:
        chunk = {
            "tenant_id": tenant_id,
            "chunk_id": str(uuid5(NAMESPACE_URL, f"chat-log:{tenant_id}:{pair['pair_id']}")),
            "content": f"Hỏi: {pair['question']}\nĐáp: {pair['answer']}",
            "metadata": {
                "url": f"https://upload.local/{quote(tenant_id)}/chat-log#{pair['pair_id']}",
                "title": f"Hỏi đáp từ chat đã ẩn danh — {pair['pair_id']}",
                "type": "faq", "updated_at": updated_at,
            },
        }
        chunks.append(KnowledgeChunk.model_validate(chunk).model_dump(mode="json"))
    return chunks


def assert_no_pii(payload: Any, customer_names: Iterable[str] = ()) -> None:
    serialized = json.dumps(payload, ensure_ascii=False)
    leaks: list[str] = []
    if EMAIL_RE.search(serialized): leaks.append("email")
    if PHONE_RE.search(serialized): leaks.append("phone")
    normalized = serialized.casefold()
    if any(name.strip().casefold() in normalized for name in customer_names if len(name.strip()) >= 3):
        leaks.append("customer_name")
    if leaks:
        raise ChatLogImportError(f"PII còn sót trong output: {', '.join(leaks)}")


def import_chat_log(
    source: Path, tenant_id: str, *, cluster_threshold: float = 0.86,
    minimum_pairs: int = 0, llm_enrich: bool = False,
    config_version: int = 1, llm_batch_size: int = 5,
    generate_fn: LLMGenerate | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    records = _read_records(source)
    customer_names = {_value(row, "customer_name") for row in records if _value(row, "customer_name")}
    candidates, stats = extract_pairs(records, tenant_id)
    pairs = cluster_pairs(candidates, tenant_id, cluster_threshold)
    llm_audit: dict[str, Any] = {"enabled": False}
    if llm_enrich:
        pairs, llm_audit = enrich_pairs_with_llm(
            pairs, tenant_id, config_version=config_version,
            batch_size=llm_batch_size, generate_fn=generate_fn,
        )
    if len(pairs) < minimum_pairs:
        raise ChatLogImportError(
            f"Chỉ trích được {len(pairs)} cặp có giá trị, thấp hơn minimum_pairs={minimum_pairs}."
        )
    updated_at = datetime.now(timezone.utc).date().isoformat()
    chunks = to_knowledge_chunks(pairs, tenant_id, updated_at)
    assert_no_pii({"pairs": pairs, "chunks": chunks}, customer_names)
    audit = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tenant_id": tenant_id,
        "source_file": str(source.resolve()),
        "source_data_label": (
            "TEST/SYNTHETIC" if all(str(row.get("conversation_id", "")).startswith("test-") for row in records)
            else "UNSPECIFIED_EXPORT"
        ),
        **stats, "cluster_count": len(pairs), "candidate_chunk_count": len(chunks),
        "cluster_threshold": cluster_threshold, "pii_scan_passed": True,
        "llm_enrichment": llm_audit,
    }
    return pairs, chunks, audit


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="H4-06 import Q&A từ log chat cũ.")
    parser.add_argument("source", type=Path)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/h4_06"))
    parser.add_argument("--cluster-threshold", type=float, default=0.86)
    parser.add_argument("--minimum-pairs", type=int, default=0)
    parser.add_argument("--config-version", type=int, default=1)
    parser.add_argument("--llm-batch-size", type=int, default=5)
    parser.add_argument(
        "--skip-llm", action="store_true",
        help="Chỉ dùng cho test offline; đầu ra không đạt nghiệm thu LLM H4-06.",
    )
    args = parser.parse_args()
    try:
        pairs, chunks, audit = import_chat_log(
            args.source, args.tenant_id, cluster_threshold=args.cluster_threshold,
            minimum_pairs=args.minimum_pairs, llm_enrich=not args.skip_llm,
            config_version=args.config_version, llm_batch_size=args.llm_batch_size,
        )
        pair_path = args.output_dir / "qa_pairs.redacted.json"
        candidate_path = args.output_dir / "candidate_chunks.DO_NOT_INDEX.json"
        extracted_path = args.output_dir / "extracted_knowledge.DO_NOT_INDEX.json"
        _write_json(pair_path, pairs)
        # H4-06 is staging only. H4-07 decides which pairs become eval cases
        # and which may become knowledge; this file must never be indexed.
        _write_json(candidate_path, chunks)
        # Explicit H4-06 deliverable. It remains staging until H4-07 performs
        # the mutually-exclusive eval/knowledge split.
        _write_json(extracted_path, chunks)
        audit["artifacts"] = {
            "qa_pairs_redacted_sha256": hashlib.sha256(pair_path.read_bytes()).hexdigest(),
            "candidate_chunks_sha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
            "extracted_knowledge_sha256": hashlib.sha256(extracted_path.read_bytes()).hexdigest(),
        }
        _write_json(args.output_dir / "audit.json", audit)
    except (ChatLogImportError, OSError, json.JSONDecodeError) as exc:
        print(f"H4-06 lỗi: {exc}")
        return 2
    print(
        f"H4-06: {audit['raw_records']} bản ghi -> {len(pairs)} cặp Q&A -> "
        f"{len(chunks)} candidate chunks (DO_NOT_INDEX); PII scan=PASS; "
        f"label={audit['source_data_label']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
