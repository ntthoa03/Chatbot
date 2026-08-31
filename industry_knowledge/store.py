"""Loader/retriever offline cho tầng tri thức ngành thử nghiệm H3-13.

Module này cố ý không được gọi tự động từ ``ai_core.chat``. H3-13 chỉ kiểm tra
tính khả thi; production phải có quy trình duyệt dữ liệu và eval riêng trước khi
bật tầng dùng chung cho bất kỳ tenant nào.
"""

from __future__ import annotations

import re
import unicodedata
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


SCHEMA_VERSION = "h3-13.industry-knowledge.v1"
DEFAULT_ROOT = Path(__file__).resolve().parent
_SAFE_ID = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_FORBIDDEN_KEYS = {
    "tenant_id",
    "company",
    "company_name",
    "brand",
    "phone",
    "email",
    "url",
    "address",
    "price",
    "pricing",
}
_SENSITIVE_PATTERNS = (
    re.compile(r"https?://|www\.", re.IGNORECASE),
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b", re.IGNORECASE),
    re.compile(r"(?<!\d)(?:\+?84|0)\d{8,10}(?!\d)"),
    re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:tr|triệu|vnd|vnđ|đồng|usd)\b", re.IGNORECASE),
    re.compile(r"\d{1,3}(?:[.,]\d{3}){2,}"),
)
_KNOWN_TENANT_IDENTITIES = (
    "mima",
    "mimadigi",
    "hyhy",
    "phước thịnh",
    "phuoc thinh",
    "haiyan",
    "thiên minh",
    "thien minh",
)


class IndustryKnowledgeError(ValueError):
    """Dữ liệu ngành sai schema hoặc có nguy cơ chứa dữ liệu tenant."""


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold().replace("đ", "d"))
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", text).split())


def _walk(value: Any, path: str = "root") -> list[tuple[str, Any]]:
    rows = [(path, value)]
    if isinstance(value, dict):
        for key, child in value.items():
            rows.extend(_walk(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            rows.extend(_walk(child, f"{path}[{index}]"))
    return rows


def validate_industry_document(document: Any, *, expected_id: str | None = None) -> dict:
    """Fail-closed nếu tài liệu có danh tính tenant, liên hệ hoặc giá cụ thể."""

    if not isinstance(document, dict):
        raise IndustryKnowledgeError("Tài liệu ngành phải là YAML object.")
    allowed = {
        "schema_version",
        "industry_id",
        "display_name",
        "experimental",
        "evidence",
        "terminology",
        "patterns",
    }
    unknown = set(document) - allowed
    if unknown:
        raise IndustryKnowledgeError("Trường top-level không hỗ trợ: " + ", ".join(sorted(unknown)))
    if document.get("schema_version") != SCHEMA_VERSION:
        raise IndustryKnowledgeError(f"schema_version phải là {SCHEMA_VERSION}.")
    industry_id = document.get("industry_id")
    if not isinstance(industry_id, str) or not _SAFE_ID.fullmatch(industry_id):
        raise IndustryKnowledgeError("industry_id không hợp lệ.")
    if expected_id is not None and industry_id != expected_id:
        raise IndustryKnowledgeError("industry_id không khớp tên file.")
    if document.get("experimental") is not True:
        raise IndustryKnowledgeError("H3-13 bắt buộc đánh dấu experimental=true.")

    evidence = document.get("evidence")
    if not isinstance(evidence, dict) or evidence.get("anonymized") is not True:
        raise IndustryKnowledgeError("evidence.anonymized phải là true.")
    if not isinstance(evidence.get("tenant_count"), int) or evidence["tenant_count"] < 1:
        raise IndustryKnowledgeError("evidence.tenant_count phải là số nguyên dương.")
    limitations = evidence.get("limitations")
    if not isinstance(limitations, str) or not limitations.strip():
        raise IndustryKnowledgeError("Phải ghi rõ giới hạn bằng evidence.limitations.")

    patterns = document.get("patterns")
    if not isinstance(patterns, list) or not patterns:
        raise IndustryKnowledgeError("patterns phải là danh sách không rỗng.")
    ids: set[str] = set()
    for item in patterns:
        if not isinstance(item, dict):
            raise IndustryKnowledgeError("Mỗi pattern phải là object.")
        if set(item) != {"id", "keywords", "common_questions", "answer_guidance", "do_not_claim"}:
            raise IndustryKnowledgeError("Pattern phải có đúng id/keywords/common_questions/answer_guidance/do_not_claim.")
        pattern_id = item["id"]
        if not isinstance(pattern_id, str) or not _SAFE_ID.fullmatch(pattern_id) or pattern_id in ids:
            raise IndustryKnowledgeError("Pattern id không hợp lệ hoặc bị trùng.")
        ids.add(pattern_id)
        for list_key in ("keywords", "common_questions", "do_not_claim"):
            if not isinstance(item[list_key], list) or not item[list_key] or not all(
                isinstance(value, str) and value.strip() for value in item[list_key]
            ):
                raise IndustryKnowledgeError(f"{pattern_id}.{list_key} phải là danh sách chuỗi không rỗng.")
        if not isinstance(item["answer_guidance"], str) or not item["answer_guidance"].strip():
            raise IndustryKnowledgeError(f"{pattern_id}.answer_guidance không hợp lệ.")

    for path, value in _walk(document):
        key = path.rsplit(".", 1)[-1]
        if key in _FORBIDDEN_KEYS:
            raise IndustryKnowledgeError(f"Không được dùng trường nhận diện/kinh doanh tại {path}.")
        if not isinstance(value, str):
            continue
        normalized = _normalize(value)
        if any(identity in normalized for identity in (_normalize(x) for x in _KNOWN_TENANT_IDENTITIES)):
            raise IndustryKnowledgeError(f"Phát hiện danh tính tenant tại {path}.")
        if any(pattern.search(value) for pattern in _SENSITIVE_PATTERNS):
            raise IndustryKnowledgeError(f"Phát hiện liên hệ/URL/giá cụ thể tại {path}.")
    return deepcopy(document)


class IndustryKnowledgeStore:
    """Kho YAML ngành chỉ trả hướng dẫn ẩn danh, không trả dữ liệu tenant."""

    def __init__(self, root: str | Path = DEFAULT_ROOT) -> None:
        self.root = Path(root)

    def list_industries(self) -> list[str]:
        return sorted(path.stem for path in self.root.glob("*.yaml"))

    def load(self, industry_id: str) -> dict:
        if not isinstance(industry_id, str) or not _SAFE_ID.fullmatch(industry_id):
            raise IndustryKnowledgeError("industry_id không hợp lệ.")
        path = self.root / f"{industry_id}.yaml"
        if not path.is_file():
            raise IndustryKnowledgeError(f"Chưa có tri thức cho ngành '{industry_id}'.")
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise IndustryKnowledgeError(f"Không đọc được {path}: {exc}") from exc
        return validate_industry_document(document, expected_id=industry_id)

    def retrieve(self, query: str, industry_id: str, k: int = 1) -> list[dict]:
        if not isinstance(query, str) or not query.strip():
            return []
        if not isinstance(k, int) or isinstance(k, bool) or k <= 0:
            raise IndustryKnowledgeError("k phải là số nguyên dương.")
        document = self.load(industry_id)
        query_normalized = _normalize(query)
        query_tokens = set(query_normalized.split())
        ranked: list[tuple[float, dict]] = []
        for pattern in document["patterns"]:
            phrases = [_normalize(value) for value in pattern["keywords"]]
            phrase_hits = sum(1 for phrase in phrases if phrase and phrase in query_normalized)
            keyword_tokens = {token for phrase in phrases for token in phrase.split()}
            token_hits = len(query_tokens.intersection(keyword_tokens))
            score = phrase_hits * 3 + token_hits
            if score:
                ranked.append((float(score), pattern))
        ranked.sort(key=lambda row: (-row[0], row[1]["id"]))
        return [
            {
                "industry_id": industry_id,
                "pattern_id": pattern["id"],
                "answer_guidance": pattern["answer_guidance"],
                "do_not_claim": list(pattern["do_not_claim"]),
                "score": score,
                "source_type": "industry_knowledge",
                "experimental": True,
            }
            for score, pattern in ranked[:k]
        ]
