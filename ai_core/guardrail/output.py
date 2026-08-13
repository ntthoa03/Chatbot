"""Kiểm duyệt câu trả lời trước khi gửi cho khách (HOA-12).

YAML chỉ chứa chính sách. Mọi giá và dữ kiện nghiệp vụ phải được kiểm chứng bằng
evidence RAG/tool của chính lượt trả lời.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_core.config import AgentConfig, OutputRuleConfig


_AMOUNT_PATTERN = re.compile(
    r"(?P<number>\d+(?:[., ]\d+)*)\s*(?P<unit>trieu|tr|nghin|k|vnd|dong|d)\b"
)
_NON_VND_PATTERN = re.compile(
    r"(?:\$\s*\d|\b\d+(?:[.,]\d+)?\s*(?:usd|eur|gbp|jpy|cny)\b)"
)
_STOPWORDS = {
    "anh", "chi", "ban", "ben", "cac", "cho", "co", "cua", "duoc", "em",
    "gia", "goi", "la", "mot", "nay", "nhung", "the", "thi", "toi", "tu",
    "va", "voi", "website", "dich", "vu",
}


def _normalize(text: str) -> str:
    text = text.casefold().replace("đ", "d")
    text = "".join(
        char
        for char in unicodedata.normalize("NFD", text)
        if unicodedata.category(char) != "Mn"
    )
    return re.sub(r"\s+", " ", text).strip()


def _clause_at(text: str, start: int, end: int) -> str:
    left = max(text.rfind(mark, 0, start) for mark in (".", "!", "?", ";", "\n"))
    right_candidates = [text.find(mark, end) for mark in (".", "!", "?", ";", "\n")]
    right_candidates = [position for position in right_candidates if position >= 0]
    right = min(right_candidates) if right_candidates else len(text)
    return text[left + 1:right]


def _is_safely_negated(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 55):start]
    after = text[end:min(len(text), end + 40)]
    negated_before = re.search(
        r"(?:khong(?: the| he| bao gio)?|chua|tuyet doi khong|se khong)\s+$"
        r"|\bkhong(?: the)?\s+(?:tiet lo|chia se|cung cap|xac nhan).{0,35}$",
        before,
    )
    prohibited_after = re.search(
        r"^[\s'\".,:;!?-]*(?:la\s+)?(?:cach noi\s+)?(?:khong duoc|khong nen|bi cam)\b",
        after,
    )
    return bool(negated_before or prohibited_after)


def _matches_rule(text: str, rule: OutputRuleConfig) -> bool:
    if not rule.enabled:
        return False
    for pattern in rule.patterns:
        for match in re.finditer(pattern, text):
            clause = _clause_at(text, match.start(), match.end())
            if any(re.search(allowed, clause) for allowed in rule.allow_patterns):
                continue
            if not _is_safely_negated(text, match.start(), match.end()):
                return True
    return False


def _parse_amount(number: str, unit: str) -> int:
    if unit in {"trieu", "tr"}:
        compact = number.replace(" ", "")
        if re.fullmatch(r"\d+[.,]\d{1,2}", compact):
            return round(float(compact.replace(",", ".")) * 1_000_000)
        return int(re.sub(r"\D", "", compact)) * 1_000_000
    digits = int(re.sub(r"\D", "", number))
    return digits * 1_000 if unit in {"nghin", "k"} else digits


def _currency_mentions(text: str) -> list[tuple[int, int, int]]:
    normalized = _normalize(text)
    return [
        (match.start(), match.end(), _parse_amount(match.group("number"), match.group("unit")))
        for match in _AMOUNT_PATTERN.finditer(normalized)
    ]


def _currency_amounts(text: str) -> set[int]:
    return {amount for _, _, amount in _currency_mentions(text)}


def _grounding_tokens(text: str) -> set[str]:
    tokens = set(re.findall(r"[a-z0-9]+", _normalize(text)))
    return {token for token in tokens if token not in _STOPWORDS and (len(token) >= 3 or token.isdigit())}


def _config_contact_evidence(config: AgentConfig) -> list[str]:
    evidence: list[str] = []
    if config.contact.hotline:
        evidence.append(f"hotline {config.contact.hotline}")
    if config.contact.zalo:
        evidence.append(f"zalo {config.contact.zalo}")
    return evidence


def _has_unauthorized_price(
    reply: str,
    evidence: Sequence[str],
    config: AgentConfig,
) -> bool:
    """Require every quoted VND amount and its subject to occur in RAG/tool data."""

    normalized = _normalize(reply)
    if _NON_VND_PATTERN.search(normalized):
        return True

    evidence_sentences = [
        part.strip()
        for item in evidence
        for part in re.split(r"[!?;\n]+", _normalize(item))
        if part.strip()
    ]
    reply_sentences = [
        part.strip() for part in re.split(r"[!?;\n]+", normalized) if part.strip()
    ]
    for sentence in reply_sentences:
        mentions = _currency_mentions(sentence)
        if not mentions:
            continue
        for service_key in config.pricing.must_contact:
            service = _normalize(service_key.replace("_", " "))
            explicit_priced_service = (
                rf"\b(?:goi|dich vu)\s+{re.escape(service)}\b"
                rf"|\b{re.escape(service)}\b.{{0,25}}\b(?:co gia|gia|chi phi|bao gia)\b"
                rf"|\b(?:gia|chi phi|bao gia)\b.{{0,18}}\b{re.escape(service)}\b"
            )
            if re.search(explicit_priced_service, sentence):
                return True
        for _start, _end, amount in mentions:
            candidates = [
                item for item in evidence_sentences if amount in _currency_amounts(item)
            ]
            if not candidates:
                return True

            # An amount alone is insufficient when the reply names a package/service:
            # at least one salient subject token must match evidence carrying that amount.
            subject_tokens = {
                token
                for token in _grounding_tokens(sentence)
                if not any(char.isdigit() for char in token)
                and token not in {"trieu", "nghin", "vnd", "dong", "chi", "phi", "bao"}
            }
            if subject_tokens and not any(
                subject_tokens & _grounding_tokens(candidate) for candidate in candidates
            ):
                return True
    return False


def _has_ungrounded_claim(
    reply: str,
    config: AgentConfig,
    evidence: Sequence[str],
    conversation_evidence: Sequence[str],
) -> bool:
    policy = config.guardrails.output.grounding
    if not policy.enabled:
        return False
    evidence_text = "\n".join((*evidence, *_config_contact_evidence(config)))
    evidence_tokens = _grounding_tokens(evidence_text)
    conversation_tokens = _grounding_tokens("\n".join(conversation_evidence))
    evidence_numbers = {
        re.sub(r"\D", "", value)
        for value in re.findall(r"\b\d[\d .,-]*\b", _normalize(evidence_text))
        if re.sub(r"\D", "", value)
    }
    evidence_emails = {
        value.casefold()
        for value in re.findall(
            r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
            evidence_text,
            flags=re.I,
        )
    }

    for raw_sentence in re.split(r"(?<=[.!?;])\s+|\n+", reply):
        sentence = _normalize(raw_sentence)
        if not sentence or any(re.search(pattern, sentence) for pattern in policy.ignore_patterns):
            continue
        bot_name = re.escape(_normalize(config.persona.bot_name))
        bot_fact_claim = bool(
            bot_name
            and re.search(
                rf"\b{bot_name}\b.{{0,100}}\b(?:la|co|dat tai|thanh lap|hoat dong|cung cap|so huu)\b",
                sentence,
            )
        )
        if not bot_fact_claim and not any(
            re.search(pattern, sentence) for pattern in policy.claim_patterns
        ):
            continue
        if any(re.search(pattern, sentence) for pattern in policy.conversation_claim_patterns):
            sentence_tokens = _grounding_tokens(sentence)
            if sentence_tokens and len(sentence_tokens & conversation_tokens) >= min(2, len(sentence_tokens)):
                continue
        if _currency_amounts(sentence) - _currency_amounts(evidence_text):
            return True
        sentence_emails = {
            value.casefold()
            for value in re.findall(
                r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
                raw_sentence,
                flags=re.I,
            )
        }
        if sentence_emails - evidence_emails:
            return True
        tokens = _grounding_tokens(sentence)
        if not tokens:
            continue
        matching = len(tokens & evidence_tokens)
        required = min(policy.min_matching_tokens, len(tokens))
        if matching < required or matching / len(tokens) < policy.min_token_overlap:
            return True
        # Numeric identifiers/durations must occur verbatim in trusted evidence.
        if not _currency_amounts(sentence):
            for number in re.findall(r"\b\d[\d .,-]*\b", sentence):
                compact = re.sub(r"\D", "", number)
                if compact and compact not in evidence_numbers:
                    return True
    return False


def check_output(
    reply: str,
    config: AgentConfig | None = None,
    *,
    evidence: Sequence[str] | None = None,
    conversation_evidence: Sequence[str] = (),
) -> dict:
    """Trả kết quả chặn và mã lý do; không tự thay nội dung câu trả lời.

    ``evidence=None`` dành cho kiểm tra rule độc lập. Luồng chat luôn truyền danh
    sách evidence (RAG/tool/config), kể cả danh sách rỗng, để bật grounding.
    """

    if not isinstance(reply, str) or not reply.strip():
        return {"blocked": True, "reason": "empty_output"}
    if config is None:
        return {"blocked": True, "reason": "missing_guardrail_config"}

    normalized = _normalize(reply)
    for rule in config.guardrails.output.rules:
        if _matches_rule(normalized, rule):
            return {"blocked": True, "reason": rule.reason}

    if config.guardrails.output.block_configured_model_names:
        for model_name in (config.model_primary, config.model_fallback):
            for match in re.finditer(re.escape(_normalize(model_name)), normalized):
                if not _is_safely_negated(normalized, match.start(), match.end()):
                    return {"blocked": True, "reason": "technical_information_disclosure"}

    if _has_unauthorized_price(reply, evidence or (), config):
        return {"blocked": True, "reason": "unauthorized_price"}

    if evidence is not None and _has_ungrounded_claim(
        reply,
        config,
        evidence,
        conversation_evidence,
    ):
        return {"blocked": True, "reason": config.guardrails.output.grounding.reason}

    return {"blocked": False, "reason": None}


def redact_output_for_trace(reply: str, limit: int = 1000) -> str:
    """Che bí mật và PII thường gặp trước khi lưu nội dung bị guardrail chặn."""

    redacted = re.sub(
        r"(?i)\b(api[_ ]?key|secret[_ ]?key|access[_ ]?token)\s*[:=]\s*\S+",
        r"\1=[REDACTED]",
        reply,
    )
    redacted = re.sub(
        r"((?i:\bOTP\b)\D{0,12})\d{4,8}\b",
        r"\1[REDACTED]",
        redacted,
    )
    redacted = re.sub(r"\b(?:\d[ -]?){13,19}\b", "[REDACTED]", redacted)
    redacted = re.sub(r"\b(?:0|\+84)[\s.-]?(?:\d[\s.-]?){8,10}\b", "[REDACTED]", redacted)
    redacted = re.sub(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "[REDACTED]", redacted, flags=re.I)
    return redacted[:limit]
