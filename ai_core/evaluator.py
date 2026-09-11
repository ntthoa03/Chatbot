"""Deterministic evaluation primitives for HOA-13.

The evaluator deliberately knows nothing about providers.  It accepts a chat
callable with the public ``chat(payload) -> response`` contract, which keeps
the scoring logic cheap to test and makes every production run reproducible.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import threading
import time
import unicodedata
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


CaseType = Literal["normal", "trap"]
ResultStatus = Literal["PASS", "FAIL", "ERROR", "MANUAL_REVIEW"]
DataStatus = Literal[
    "sufficient", "insufficient", "below_threshold", "not_answerable",
    "fallback_response", "guardrail_blocked", "judge_error",
    "retrieval_error", "not_applicable", "unknown",
]
ChatCallable = Callable[[dict[str, Any]], dict[str, Any]]
DiagnosticResolver = Callable[[str], dict[str, Any] | None]


class EvalConfigError(ValueError):
    """Raised when an eval case file is malformed or unsafe to score."""


def _normalized(text: str) -> str:
    # NFD removes tone marks but does not decompose Vietnamese đ/Đ.
    text = unicodedata.normalize("NFD", str(text).casefold().replace("đ", "d"))
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return re.sub(r"\s+", " ", text).strip()


def _contains(haystack: str, needle: str) -> bool:
    """Match Vietnamese text accent-insensitively and phone numbers format-insensitively."""

    normalized_haystack = _normalized(haystack)
    normalized_needle = _normalized(needle)
    needle_digits = re.sub(r"\D", "", normalized_needle)
    if len(needle_digits) >= 7:
        return needle_digits in re.sub(r"\D", "", normalized_haystack)
    return normalized_needle in normalized_haystack


class _FrozenEvalModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EvalCase(_FrozenEvalModel):
    id: str
    question: str
    type: CaseType
    topic: str = "unclassified"
    must_contain: tuple[str, ...] = ()
    must_contain_any: tuple[str, ...] = ()
    must_not_contain: tuple[str, ...] = ()
    expected_answer: str | None = None
    expect_escalate: bool | None = None
    pass_score: float = Field(default=1.0, ge=0.0, le=1.0)
    grading: Literal["keywords", "llm"] = "keywords"
    rubric: str | None = None
    manual_review_required: bool = False

    @model_validator(mode="after")
    def validate_grading(self) -> "EvalCase":
        if self.grading == "llm" and not (self.rubric and self.rubric.strip()):
            raise ValueError("case grading=llm phải có rubric")
        return self


class JudgeVerdict(_FrozenEvalModel):
    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1)


JudgeCallable = Callable[[EvalCase, str], JudgeVerdict | dict[str, Any]]


class AnswerabilityVerdict(_FrozenEvalModel):
    """Whether retrieved context directly contains the facts needed to answer."""

    answerable: bool
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_chunk_ids: tuple[str, ...] = ()
    reason: str = Field(min_length=1)


AnswerabilityCallable = Callable[
    [str, Sequence[dict[str, Any]]], AnswerabilityVerdict | dict[str, Any]
]


class CriterionResult(_FrozenEvalModel):
    name: str
    expected: str
    passed: bool


class CaseResult(_FrozenEvalModel):
    id: str
    type: CaseType
    topic: str
    input_style: Literal["accented", "unaccented"]
    question: str
    reply: str
    status: ResultStatus
    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    pass_score: float = Field(ge=0.0, le=1.0)
    criteria: tuple[CriterionResult, ...]
    need_human: bool
    guardrail_blocked: bool
    model: str
    model_called: bool
    cost_usd: float = Field(ge=0.0)
    latency_ms: int = Field(ge=0)
    trace_id: str
    diagnostic_stage: str | None = None
    retrieval_attempted: bool = False
    retrieval_hit: bool = False
    context_answerable: bool | None = None
    answerability_confidence: float | None = None
    answerability_reason: str | None = None
    supporting_chunk_ids: tuple[str, ...] = ()
    final_response_usable: bool = False
    data_sufficient: bool = False
    data_status: DataStatus = "unknown"
    retrieval_top_score: float | None = None
    retrieval_threshold: float | None = None
    judge_reason: str | None = None
    error: str | None = None

    @property
    def failed_checks(self) -> str:
        return "; ".join(item.name for item in self.criteria if not item.passed)


class TopicSummary(_FrozenEvalModel):
    total: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    errors: int = Field(ge=0)
    manual_review: int = Field(ge=0)
    evaluated: int = Field(ge=0)
    pass_rate: float = Field(ge=0.0, le=1.0)
    data_sufficient_count: int = Field(default=0, ge=0)
    data_insufficient_count: int = Field(default=0, ge=0)
    retrieval_error_count: int = Field(default=0, ge=0)
    data_sufficiency_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    retrieval_hit_count: int = Field(default=0, ge=0)
    retrieval_hit_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    context_answerable_count: int = Field(default=0, ge=0)
    context_judged_count: int = Field(default=0, ge=0)
    context_answerability_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    average_cost_usd: float = Field(ge=0.0)
    average_latency_ms: float = Field(ge=0.0)


class EvalSummary(_FrozenEvalModel):
    total: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    errors: int = Field(ge=0)
    manual_review: int = Field(ge=0)
    evaluated: int = Field(ge=0)
    pass_rate: float = Field(ge=0.0, le=1.0)
    completion_rate: float = Field(ge=0.0, le=1.0)
    data_sufficient_count: int = Field(default=0, ge=0)
    data_insufficient_count: int = Field(default=0, ge=0)
    retrieval_error_count: int = Field(default=0, ge=0)
    retrieval_not_applicable_count: int = Field(default=0, ge=0)
    data_unknown_count: int = Field(default=0, ge=0)
    data_sufficiency_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    retrieval_hit_count: int = Field(default=0, ge=0)
    retrieval_hit_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    context_answerable_count: int = Field(default=0, ge=0)
    context_judged_count: int = Field(default=0, ge=0)
    context_answerability_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    answerability_judge_error_count: int = Field(default=0, ge=0)
    unusable_response_count: int = Field(default=0, ge=0)
    average_cost_usd: float = Field(ge=0.0)
    average_model_call_cost_usd: float = Field(ge=0.0)
    model_calls: int = Field(ge=0)
    zero_cost_turns: int = Field(ge=0)
    average_latency_ms: float = Field(ge=0.0)
    total_cost_usd: float = Field(ge=0.0)
    duration_seconds: float = Field(ge=0.0)
    unaccented_total: int = Field(ge=0)
    unaccented_passed: int = Field(ge=0)
    unaccented_pass_rate: float = Field(ge=0.0, le=1.0)
    topic_metrics: dict[str, TopicSummary] = Field(default_factory=dict)


class EvalReport(_FrozenEvalModel):
    run_id: str
    created_at: str
    cases_path: str
    tenant_id: str
    config_version: int
    case_fingerprint: str
    fingerprint: str
    experiment: dict[str, Any] = Field(default_factory=dict)
    summary: EvalSummary
    results: tuple[CaseResult, ...]
    comparison: dict[str, Any] | None = None


def _string_list(raw: Any, *, case_id: str, field_name: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(item, str) and item.strip() for item in raw):
        raise EvalConfigError(f"{case_id}.{field_name} phải là danh sách chuỗi không rỗng.")
    return tuple(item.strip() for item in raw)


def load_cases(path: str | Path) -> list[EvalCase]:
    """Load and validate the YAML list defined in the Task.xlsx reference sheet."""

    source = Path(path)
    purpose_path = source.parent / "PURPOSE.json"
    if purpose_path.exists():
        try:
            purpose = json.loads(purpose_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EvalConfigError(f"PURPOSE.json không hợp lệ cạnh {source}.") from exc
        if purpose.get("purpose") != "evaluation_only" or "ai_core.evaluator" not in purpose.get("allowed_consumers", []):
            raise EvalConfigError(f"Từ chối chấm artifact không có purpose=evaluation_only: {source}")
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EvalConfigError(f"Không tìm thấy file eval: {source}") from exc
    except yaml.YAMLError as exc:
        raise EvalConfigError(f"YAML không hợp lệ trong {source}: {exc}") from exc
    if not isinstance(raw, list) or not raw:
        raise EvalConfigError("File eval phải chứa một danh sách case không rỗng.")

    cases: list[EvalCase] = []
    seen: set[str] = set()
    allowed = {
        "id", "question", "type", "topic", "must_contain", "must_contain_any", "must_not_contain",
        "expected_answer", "expect_escalate", "pass_score", "grading",
        "rubric", "manual_review_required",
    }
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise EvalConfigError(f"Case thứ {index} phải là object YAML.")
        unknown = sorted(set(item) - allowed)
        if unknown:
            raise EvalConfigError(f"Case thứ {index} có trường không hỗ trợ: {', '.join(unknown)}.")
        case_id = str(item.get("id", "")).strip()
        question = str(item.get("question", "")).strip()
        case_type = item.get("type")
        topic = str(item.get("topic") or ("trap" if case_type == "trap" else "unclassified")).strip()
        if not case_id or case_id in seen:
            raise EvalConfigError(f"ID case trống hoặc trùng: {case_id!r}.")
        if not question:
            raise EvalConfigError(f"{case_id}.question không được để trống.")
        if case_type not in ("normal", "trap"):
            raise EvalConfigError(f"{case_id}.type phải là 'normal' hoặc 'trap'.")
        pass_score = item.get("pass_score", 1.0)
        if isinstance(pass_score, bool) or not isinstance(pass_score, (int, float)):
            raise EvalConfigError(f"{case_id}.pass_score phải là số từ 0 đến 1.")
        pass_score = float(pass_score)
        if not 0 <= pass_score <= 1:
            raise EvalConfigError(f"{case_id}.pass_score phải nằm trong [0, 1].")
        expected = item.get("expected_answer")
        if expected is not None and (not isinstance(expected, str) or not expected.strip()):
            raise EvalConfigError(f"{case_id}.expected_answer phải là chuỗi không rỗng.")
        escalate = item.get("expect_escalate")
        if escalate is not None and not isinstance(escalate, bool):
            raise EvalConfigError(f"{case_id}.expect_escalate phải là true/false.")
        try:
            case = EvalCase(
                id=case_id,
                question=question,
                type=case_type,
                topic=topic,
                must_contain=_string_list(item.get("must_contain"), case_id=case_id, field_name="must_contain"),
                must_contain_any=_string_list(
                    item.get("must_contain_any"), case_id=case_id,
                    field_name="must_contain_any",
                ),
                must_not_contain=_string_list(item.get("must_not_contain"), case_id=case_id, field_name="must_not_contain"),
                expected_answer=expected.strip() if expected else None,
                expect_escalate=escalate,
                pass_score=pass_score,
                grading=item.get("grading", "keywords"),
                rubric=item.get("rubric"),
                manual_review_required=item.get("manual_review_required", False),
            )
        except ValidationError as exc:
            raise EvalConfigError(f"Case {case_id} không hợp lệ: {exc}") from exc
        if not (
            case.must_contain or case.must_contain_any or case.must_not_contain
            or case.expected_answer or escalate is not None
        ):
            raise EvalConfigError(f"{case_id} không có tiêu chí để chấm.")
        seen.add(case_id)
        cases.append(case)
    return cases


def score_reply(
    reply: str,
    must_contain: Sequence[str],
    must_not_contain: Sequence[str],
) -> bool:
    """Backward-compatible strict keyword score used by older callers."""

    return all(_contains(reply, word) for word in must_contain) and all(
        not _contains(reply, word) for word in must_not_contain
    )


def _safe_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _extract_data_diagnostics(diagnostics: dict[str, Any] | None) -> dict[str, Any]:
    """Classify retrieval coverage independently from answer correctness."""

    if not isinstance(diagnostics, dict):
        return {
            "retrieval_attempted": False,
            "retrieval_hit": False,
            "data_sufficient": False,
            "data_status": "unknown",
            "retrieval_top_score": None,
            "retrieval_threshold": None,
        }
    retrieval = diagnostics.get("retrieval")
    if not isinstance(retrieval, dict):
        retrieval = {}
    chunks = retrieval.get("chunks") if isinstance(retrieval.get("chunks"), list) else []
    fallback = (
        retrieval.get("fallback_candidates")
        if isinstance(retrieval.get("fallback_candidates"), list)
        else []
    )
    score_values = [
        score
        for item in (*chunks, *fallback)
        if isinstance(item, dict) and (score := _safe_float(item.get("score"))) is not None
    ]
    top_score = _safe_float(retrieval.get("top_score"))
    if top_score is None and score_values:
        top_score = max(score_values)
    threshold = _safe_float(retrieval.get("threshold"))
    attempted = bool(retrieval.get("attempted")) if "attempted" in retrieval else bool(
        retrieval.get("query") is not None or chunks or fallback or retrieval.get("error")
    )
    if not attempted:
        status: DataStatus = "not_applicable"
        sufficient = False
    elif retrieval.get("error") or diagnostics.get("stage") == "retrieval_error":
        status = "retrieval_error"
        sufficient = False
    else:
        explicit = retrieval.get("data_sufficient")
        if isinstance(explicit, bool):
            sufficient = explicit
        elif top_score is not None and threshold is not None:
            sufficient = top_score >= threshold
        else:
            # Legacy traces only stored chunks after threshold filtering.
            sufficient = bool(chunks)
        status = "sufficient" if sufficient else "insufficient"
    return {
        "retrieval_attempted": attempted,
        "retrieval_hit": sufficient,
        "data_sufficient": sufficient,
        "data_status": status,
        "retrieval_top_score": top_score,
        "retrieval_threshold": threshold,
    }


def _retrieved_chunks(diagnostics: dict[str, Any] | None) -> list[dict[str, Any]]:
    retrieval = diagnostics.get("retrieval") if isinstance(diagnostics, dict) else None
    if not isinstance(retrieval, dict):
        return []
    chunks = retrieval.get("chunks")
    return [item for item in chunks if isinstance(item, dict)] if isinstance(chunks, list) else []


def _final_response_usable(
    response: dict[str, Any], diagnostics: dict[str, Any] | None,
) -> tuple[bool, DataStatus | None]:
    """Exclude blocked, fallback and handoff replies from effective coverage."""

    guardrail = response.get("guardrail") if isinstance(response.get("guardrail"), dict) else {}
    if bool(guardrail.get("blocked")) or (diagnostics or {}).get("stage") == "blocked_output":
        return False, "guardrail_blocked"
    if (
        bool(response.get("need_human"))
        or bool((diagnostics or {}).get("helpful_fallback_used"))
        or (diagnostics or {}).get("stage") in {"fallback", "handoff_requested"}
    ):
        return False, "fallback_response"
    return bool(str(response.get("reply", "")).strip()), None


def _effective_data_diagnostics(
    question: str,
    response: dict[str, Any],
    diagnostics: dict[str, Any] | None,
    answerability_fn: AnswerabilityCallable | None,
) -> dict[str, Any]:
    """Combine similarity, direct evidence and final-response usability.

    Legacy callers without a judge retain the old similarity result. Any run
    claiming the effective metric must explicitly pass ``answerability_fn``.
    """

    result = _extract_data_diagnostics(diagnostics)
    usable, unusable_status = _final_response_usable(response, diagnostics)
    result.update({
        "context_answerable": None,
        "answerability_confidence": None,
        "answerability_reason": None,
        "supporting_chunk_ids": (),
        "final_response_usable": usable,
    })
    if result["data_status"] in {"unknown", "not_applicable", "retrieval_error"}:
        result["data_sufficient"] = False
        return result
    if not result["retrieval_hit"]:
        if answerability_fn is not None:
            result.update(data_sufficient=False, data_status="below_threshold")
        return result
    if answerability_fn is None:
        if unusable_status is not None:
            result.update(data_sufficient=False, data_status=unusable_status)
        return result
    try:
        verdict = AnswerabilityVerdict.model_validate(
            answerability_fn(question, _retrieved_chunks(diagnostics)[:5])
        )
    except Exception as exc:
        result.update(
            data_sufficient=False,
            data_status="judge_error",
            answerability_reason=f"{type(exc).__name__}: {exc}",
        )
        return result
    result.update({
        "context_answerable": verdict.answerable,
        "answerability_confidence": verdict.confidence,
        "answerability_reason": verdict.reason,
        "supporting_chunk_ids": verdict.supporting_chunk_ids,
    })
    if unusable_status is not None:
        result.update(data_sufficient=False, data_status=unusable_status)
    else:
        result.update(
            data_sufficient=verdict.answerable,
            data_status="sufficient" if verdict.answerable else "not_answerable",
        )
    return result


def score_case(
    case: EvalCase,
    response: dict[str, Any],
    *,
    diagnostics: dict[str, Any] | None = None,
    judge_fn: JudgeCallable | None = None,
    answerability_fn: AnswerabilityCallable | None = None,
) -> CaseResult:
    """Score one response, separating quality verdicts from infrastructure errors."""

    reply = str(response.get("reply", ""))
    criteria: list[CriterionResult] = []
    for keyword in case.must_contain:
        criteria.append(CriterionResult(
            name=f"must_contain:{keyword}",
            expected=keyword,
            passed=_contains(reply, keyword),
        ))
    if case.must_contain_any:
        expected_any = " | ".join(case.must_contain_any)
        criteria.append(CriterionResult(
            name=f"must_contain_any:{expected_any}",
            expected=expected_any,
            passed=any(_contains(reply, keyword) for keyword in case.must_contain_any),
        ))
    for keyword in case.must_not_contain:
        criteria.append(CriterionResult(
            name=f"must_not_contain:{keyword}",
            expected=keyword,
            passed=not _contains(reply, keyword),
        ))
    if case.expected_answer:
        criteria.append(CriterionResult(
            name="expected_answer",
            expected=case.expected_answer,
            passed=_contains(reply, case.expected_answer),
        ))
    need_human = bool(response.get("need_human", False))
    if case.expect_escalate is not None:
        criteria.append(CriterionResult(
            name="expect_escalate",
            expected=str(case.expect_escalate).lower(),
            passed=need_human is case.expect_escalate,
        ))

    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    guardrail = response.get("guardrail") if isinstance(response.get("guardrail"), dict) else {}
    trace_id = str(response.get("trace_id", ""))
    diagnostic_stage = str((diagnostics or {}).get("stage") or "") or None
    diagnostic_model = (
        (diagnostics or {}).get("model")
        if isinstance((diagnostics or {}).get("model"), dict)
        else {}
    )
    data_diagnostics = _effective_data_diagnostics(
        case.question, response, diagnostics, answerability_fn,
    )
    if "called" in diagnostic_model:
        model_called = bool(diagnostic_model.get("called"))
    else:
        model_called = bool(
            float(usage.get("cost_usd", 0) or 0)
            or int(usage.get("tokens_in", 0) or 0)
            or int(usage.get("tokens_out", 0) or 0)
        )
    infrastructure_error = diagnostic_stage in {
        "retrieval_error", "llm_error", "tool_error",
    }
    error_message = None
    if infrastructure_error:
        detail = (
            (diagnostics or {}).get("retrieval_error")
            or (diagnostics or {}).get("llm_error")
            or "dependency_error"
        )
        error_message = f"{diagnostic_stage}: {detail}"

    judge_reason: str | None = None
    if not infrastructure_error and case.grading == "llm":
        keyword_gate_passed = all(item.passed for item in criteria)
        if keyword_gate_passed and judge_fn is not None:
            try:
                verdict = JudgeVerdict.model_validate(judge_fn(case, reply))
                criteria.append(CriterionResult(
                    name="llm_judge",
                    expected=case.rubric or "",
                    passed=verdict.passed,
                ))
                judge_reason = verdict.reason
            except Exception as exc:
                judge_reason = f"judge_unavailable: {type(exc).__name__}: {exc}"
        elif keyword_gate_passed:
            judge_reason = "judge_unavailable"

    keyword_gate_passed = all(item.passed for item in criteria if item.name != "llm_judge")
    score = sum(item.passed for item in criteria) / len(criteria) if criteria else 0.0
    provisional_passed = score >= case.pass_score
    if infrastructure_error:
        status: ResultStatus = "ERROR"
    elif case.grading == "llm" and keyword_gate_passed and (
        case.manual_review_required or judge_reason == "judge_unavailable"
        or (judge_reason or "").startswith("judge_unavailable:")
    ):
        status = "MANUAL_REVIEW"
    else:
        status = "PASS" if provisional_passed else "FAIL"
    return CaseResult(
        id=case.id,
        type=case.type,
        topic=case.topic,
        input_style="unaccented" if case.question.isascii() else "accented",
        question=case.question,
        reply=reply,
        status=status,
        passed=status == "PASS",
        score=round(score, 4),
        pass_score=case.pass_score,
        criteria=tuple(criteria),
        need_human=need_human,
        guardrail_blocked=bool(guardrail.get("blocked", False)),
        model=str(usage.get("model", "")),
        model_called=model_called,
        cost_usd=float(usage.get("cost_usd", 0) or 0),
        latency_ms=int(usage.get("latency_ms", 0) or 0),
        trace_id=trace_id,
        diagnostic_stage=diagnostic_stage,
        **data_diagnostics,
        judge_reason=judge_reason,
        error=error_message,
    )


def _error_result(case: EvalCase, exc: Exception, latency_ms: int = 0) -> CaseResult:
    return CaseResult(
        id=case.id, type=case.type, topic=case.topic,
        input_style="unaccented" if case.question.isascii() else "accented",
        question=case.question, reply="", status="ERROR", passed=False,
        score=0.0, pass_score=case.pass_score, criteria=(), need_human=False,
        guardrail_blocked=False, model="", model_called=False,
        cost_usd=0.0, latency_ms=latency_ms,
        trace_id="", diagnostic_stage="exception",
        data_status="unknown",
        error=f"{type(exc).__name__}: {exc}",
    )


def build_case_fingerprint(cases_path: str | Path) -> str:
    """Hash the case suite and scoring contract used for comparable runs."""

    payload = {
        "cases": yaml.safe_load(Path(cases_path).read_text(encoding="utf-8")),
        "scoring_schema_version": 5,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_run_fingerprint(
    cases_path: str | Path,
    tenant_id: str,
    config_version: int,
    *,
    experiment_context: dict[str, Any] | None = None,
) -> str:
    """Hash every input that must stay constant for a meaningful comparison."""

    from ai_core.config import load_config
    from ai_core.prompt import PROMPT_VERSION

    config = load_config(tenant_id, config_version)
    payload = {
        "case_fingerprint": build_case_fingerprint(cases_path),
        "tenant_config": config.model_dump(mode="json"),
        "prompt_version": PROMPT_VERSION,
        "eval_temperature": 0.0,
        "experiment": experiment_context or {},
        "schema_version": 3,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_comparison(
    current: EvalSummary,
    baseline: dict[str, Any] | EvalSummary,
    *,
    current_fingerprint: str | None = None,
    current_run_fingerprint: str | None = None,
) -> dict[str, Any]:
    base = baseline.model_dump() if isinstance(baseline, EvalSummary) else baseline
    baseline_fingerprint = base.get("case_fingerprint") or base.get("fingerprint")
    if current_fingerprint and baseline_fingerprint != current_fingerprint:
        return {
            "compatible": False,
            "baseline_run_id": base.get("run_id"),
            "reason": "fingerprint_mismatch",
        }
    return {
        "compatible": True,
        "baseline_run_id": base.get("run_id"),
        "context_changed": bool(
            current_run_fingerprint
            and base.get("fingerprint")
            and base.get("fingerprint") != current_run_fingerprint
        ),
        "baseline_pass_rate": round(float(base.get("pass_rate", 0)), 4),
        "current_pass_rate": current.pass_rate,
        "pass_rate_delta": round(current.pass_rate - float(base.get("pass_rate", 0)), 4),
        "baseline_data_sufficiency_rate": round(
            float(base.get("data_sufficiency_rate", 0)), 4
        ),
        "current_data_sufficiency_rate": current.data_sufficiency_rate,
        "data_sufficiency_rate_delta": round(
            current.data_sufficiency_rate
            - float(base.get("data_sufficiency_rate", 0)),
            4,
        ),
        "baseline_average_cost_usd": round(float(base.get("average_cost_usd", 0)), 12),
        "current_average_cost_usd": current.average_cost_usd,
        "average_cost_usd_delta": round(
            current.average_cost_usd - float(base.get("average_cost_usd", 0)), 12
        ),
        "baseline_average_latency_ms": round(float(base.get("average_latency_ms", 0)), 2),
        "current_average_latency_ms": current.average_latency_ms,
        "average_latency_ms_delta": round(current.average_latency_ms - float(base.get("average_latency_ms", 0)), 2),
        "baseline_completion_rate": round(float(base.get("completion_rate", 0)), 4),
        "current_completion_rate": current.completion_rate,
        "completion_rate_delta": round(
            current.completion_rate - float(base.get("completion_rate", 0)), 4
        ),
    }


def run_eval(
    cases_path: str | Path,
    chat_fn: ChatCallable,
    *,
    tenant_id: str = "mima_internal",
    config_version: int = 1,
    baseline: dict[str, Any] | None = None,
    workers: int = 4,
    requests_per_minute: float | None = None,
    diagnostic_resolver: DiagnosticResolver | None = None,
    judge_fn: JudgeCallable | None = None,
    answerability_fn: AnswerabilityCallable | None = None,
    experiment_context: dict[str, Any] | None = None,
) -> EvalReport:
    """Execute independent cases concurrently and return an ordered report."""

    cases = load_cases(cases_path)
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise EvalConfigError("workers phải là số nguyên dương.")
    if requests_per_minute is not None and requests_per_minute <= 0:
        raise EvalConfigError("requests_per_minute phải lớn hơn 0.")
    case_fingerprint = build_case_fingerprint(cases_path)
    fingerprint = build_run_fingerprint(
        cases_path,
        tenant_id,
        config_version,
        experiment_context=experiment_context,
    )
    started = time.perf_counter()
    rate_lock = threading.Lock()
    next_start = [started]

    def wait_for_slot() -> None:
        if requests_per_minute is None:
            return
        interval = 60.0 / requests_per_minute
        with rate_lock:
            now = time.perf_counter()
            delay = max(0.0, next_start[0] - now)
            next_start[0] = max(now, next_start[0]) + interval
        if delay:
            time.sleep(delay)

    def execute(case: EvalCase) -> CaseResult:
        payload = {
            "tenant_id": tenant_id,
            "conversation_id": str(uuid5(NAMESPACE_URL, f"hoa13:{case.id}")),
            "message": case.question,
            "history": [],
            "config_version": config_version,
        }
        case_started = time.perf_counter()
        try:
            wait_for_slot()
            response = chat_fn(payload)
            trace_id = str(response.get("trace_id", ""))
            diagnostics = diagnostic_resolver(trace_id) if diagnostic_resolver else None
            return score_case(
                case, response, diagnostics=diagnostics, judge_fn=judge_fn,
                answerability_fn=answerability_fn,
            )
        except Exception as exc:  # one provider/case failure must not abort the suite
            elapsed_ms = round((time.perf_counter() - case_started) * 1000)
            return _error_result(case, exc, elapsed_ms)

    # executor.map preserves the YAML order even when requests finish out of order.
    with ThreadPoolExecutor(max_workers=min(workers, len(cases))) as executor:
        results = list(executor.map(execute, cases))
    duration = time.perf_counter() - started
    passed = sum(item.status == "PASS" for item in results)
    failed = sum(item.status == "FAIL" for item in results)
    errors = sum(item.status == "ERROR" for item in results)
    manual_review = sum(item.status == "MANUAL_REVIEW" for item in results)
    evaluated = passed + failed
    costs = [item.cost_usd for item in results]
    model_call_costs = [item.cost_usd for item in results if item.model_called]
    latencies = [item.latency_ms for item in results]
    unaccented_results = [item for item in results if item.input_style == "unaccented"]
    unaccented_passed = sum(item.status == "PASS" for item in unaccented_results)
    unaccented_evaluated = sum(
        item.status in {"PASS", "FAIL"} for item in unaccented_results
    )
    topic_metrics: dict[str, TopicSummary] = {}
    for topic in sorted({item.topic for item in results}):
        topic_results = [item for item in results if item.topic == topic]
        topic_passed = sum(item.status == "PASS" for item in topic_results)
        topic_failed = sum(item.status == "FAIL" for item in topic_results)
        topic_errors = sum(item.status == "ERROR" for item in topic_results)
        topic_manual = sum(item.status == "MANUAL_REVIEW" for item in topic_results)
        topic_evaluated = topic_passed + topic_failed
        topic_data_sufficient = sum(item.data_sufficient for item in topic_results)
        topic_retrieval_hits = sum(item.retrieval_hit for item in topic_results)
        topic_context_judged = sum(item.context_answerable is not None for item in topic_results)
        topic_context_answerable = sum(item.context_answerable is True for item in topic_results)
        topic_metrics[topic] = TopicSummary(
            total=len(topic_results),
            passed=topic_passed,
            failed=topic_failed,
            errors=topic_errors,
            manual_review=topic_manual,
            evaluated=topic_evaluated,
            pass_rate=round(topic_passed / topic_evaluated, 4) if topic_evaluated else 0.0,
            data_sufficient_count=topic_data_sufficient,
            data_insufficient_count=sum(
                item.data_status in {
                    "insufficient", "below_threshold", "not_answerable",
                    "fallback_response", "guardrail_blocked",
                }
                for item in topic_results
            ),
            retrieval_error_count=sum(
                item.data_status == "retrieval_error" for item in topic_results
            ),
            data_sufficiency_rate=round(topic_data_sufficient / len(topic_results), 4),
            retrieval_hit_count=topic_retrieval_hits,
            retrieval_hit_rate=round(topic_retrieval_hits / len(topic_results), 4),
            context_answerable_count=topic_context_answerable,
            context_judged_count=topic_context_judged,
            context_answerability_rate=(
                round(topic_context_answerable / topic_context_judged, 4)
                if topic_context_judged else 0.0
            ),
            average_cost_usd=round(fmean(item.cost_usd for item in topic_results), 12),
            average_latency_ms=round(fmean(item.latency_ms for item in topic_results), 2),
        )
    data_sufficient_count = sum(item.data_sufficient for item in results)
    retrieval_hit_count = sum(item.retrieval_hit for item in results)
    context_judged_count = sum(item.context_answerable is not None for item in results)
    context_answerable_count = sum(item.context_answerable is True for item in results)
    summary = EvalSummary(
        total=len(results), passed=passed, failed=failed, errors=errors,
        manual_review=manual_review, evaluated=evaluated,
        pass_rate=round(passed / evaluated, 4) if evaluated else 0.0,
        completion_rate=round(evaluated / len(results), 4),
        data_sufficient_count=data_sufficient_count,
        data_insufficient_count=sum(
            item.data_status in {
                "insufficient", "below_threshold", "not_answerable",
                "fallback_response", "guardrail_blocked",
            }
            for item in results
        ),
        retrieval_error_count=sum(item.data_status == "retrieval_error" for item in results),
        retrieval_not_applicable_count=sum(
            item.data_status == "not_applicable" for item in results
        ),
        data_unknown_count=sum(item.data_status == "unknown" for item in results),
        data_sufficiency_rate=round(data_sufficient_count / len(results), 4),
        retrieval_hit_count=retrieval_hit_count,
        retrieval_hit_rate=round(retrieval_hit_count / len(results), 4),
        context_answerable_count=context_answerable_count,
        context_judged_count=context_judged_count,
        context_answerability_rate=(
            round(context_answerable_count / context_judged_count, 4)
            if context_judged_count else 0.0
        ),
        answerability_judge_error_count=sum(
            item.data_status == "judge_error" for item in results
        ),
        unusable_response_count=sum(
            item.data_status in {"fallback_response", "guardrail_blocked"}
            for item in results
        ),
        average_cost_usd=round(fmean(costs), 12),
        average_model_call_cost_usd=(
            round(fmean(model_call_costs), 12) if model_call_costs else 0.0
        ),
        model_calls=len(model_call_costs),
        zero_cost_turns=sum(item.cost_usd == 0 for item in results),
        average_latency_ms=round(fmean(latencies), 2),
        total_cost_usd=round(sum(costs), 12),
        duration_seconds=round(duration, 3),
        unaccented_total=len(unaccented_results),
        unaccented_passed=unaccented_passed,
        unaccented_pass_rate=round(unaccented_passed / unaccented_evaluated, 4)
        if unaccented_evaluated else 0.0,
        topic_metrics=topic_metrics,
    )
    now = datetime.now(timezone.utc)
    run_id = now.strftime("%Y%m%dT%H%M%S.%fZ")
    comparison = build_comparison(
        summary, baseline, current_fingerprint=case_fingerprint,
        current_run_fingerprint=fingerprint,
    ) if baseline else None
    return EvalReport(
        run_id=run_id,
        created_at=now.isoformat(),
        cases_path=str(Path(cases_path)),
        tenant_id=tenant_id,
        config_version=config_version,
        case_fingerprint=case_fingerprint,
        fingerprint=fingerprint,
        experiment=experiment_context or {},
        summary=summary,
        results=tuple(results),
        comparison=comparison,
    )


def report_as_dict(report: EvalReport) -> dict[str, Any]:
    return report.model_dump(mode="json")


def save_report(
    report: EvalReport, report_dir: str | Path,
) -> tuple[Path, Path, Path, Path, Path, Path]:
    """Persist audit data plus detail, summary, scorecard and 4-column review CSVs."""

    destination = Path(report_dir)
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / f"{report.run_id}.json"
    csv_path = destination / f"{report.run_id}.csv"
    summary_path = destination / f"{report.run_id}.summary.csv"
    scorecard_path = destination / f"{report.run_id}.scorecard.csv"
    manual_review_path = destination / f"{report.run_id}.manual-review.csv"
    topics_path = destination / f"{report.run_id}.topics.csv"
    json_path.write_text(json.dumps(report_as_dict(report), ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "id", "type", "topic", "input_style", "status", "passed", "score", "pass_score", "question", "reply",
            "failed_checks", "need_human", "guardrail_blocked", "model", "cost_usd",
            "model_called",
            "latency_ms", "trace_id", "diagnostic_stage", "retrieval_attempted",
            "retrieval_hit", "context_answerable", "answerability_confidence",
            "answerability_reason", "supporting_chunk_ids", "final_response_usable",
            "data_sufficient", "data_status", "retrieval_top_score",
            "retrieval_threshold", "judge_reason", "error",
        ])
        writer.writeheader()
        for result in report.results:
            row = result.model_dump(mode="json")
            row.pop("criteria")
            row["failed_checks"] = result.failed_checks
            writer.writerow(row)
    comparison = report.comparison or {}
    metric_rows = [
        (
            "pass_rate", report.summary.pass_rate,
            comparison.get("baseline_pass_rate"), comparison.get("pass_rate_delta"), "%",
        ),
        (
            "completion_rate", report.summary.completion_rate,
            comparison.get("baseline_completion_rate"),
            comparison.get("completion_rate_delta"), "%",
        ),
        (
            "data_sufficiency_rate", report.summary.data_sufficiency_rate,
            comparison.get("baseline_data_sufficiency_rate"),
            comparison.get("data_sufficiency_rate_delta"), "%",
        ),
        ("data_sufficient_count", report.summary.data_sufficient_count, None, None, "cases"),
        ("data_insufficient_count", report.summary.data_insufficient_count, None, None, "cases"),
        ("retrieval_hit_rate", report.summary.retrieval_hit_rate, None, None, "%"),
        ("retrieval_hit_count", report.summary.retrieval_hit_count, None, None, "cases"),
        (
            "context_answerability_rate", report.summary.context_answerability_rate,
            None, None, "% of judged contexts",
        ),
        ("context_judged_count", report.summary.context_judged_count, None, None, "cases"),
        (
            "answerability_judge_error_count",
            report.summary.answerability_judge_error_count, None, None, "cases",
        ),
        ("unusable_response_count", report.summary.unusable_response_count, None, None, "cases"),
        ("retrieval_error_count", report.summary.retrieval_error_count, None, None, "cases"),
        (
            "average_cost_usd", report.summary.average_cost_usd,
            comparison.get("baseline_average_cost_usd"),
            comparison.get("average_cost_usd_delta"), "USD",
        ),
        (
            "average_model_call_cost_usd",
            report.summary.average_model_call_cost_usd,
            None, None, "USD/model call",
        ),
        ("model_calls", report.summary.model_calls, None, None, "cases"),
        ("zero_cost_turns", report.summary.zero_cost_turns, None, None, "cases"),
        (
            "average_latency_ms", report.summary.average_latency_ms,
            comparison.get("baseline_average_latency_ms"),
            comparison.get("average_latency_ms_delta"), "ms",
        ),
        ("duration_seconds", report.summary.duration_seconds, None, None, "s"),
        ("passed", report.summary.passed, None, None, "cases"),
        ("failed", report.summary.failed, None, None, "cases"),
        ("errors", report.summary.errors, None, None, "cases"),
        ("manual_review", report.summary.manual_review, None, None, "cases"),
    ]
    for topic, metrics in report.summary.topic_metrics.items():
        metric_rows.extend([
            (f"topic.{topic}.pass_rate", metrics.pass_rate, None, None, "%"),
            (
                f"topic.{topic}.data_sufficiency_rate",
                metrics.data_sufficiency_rate, None, None, "%",
            ),
            (f"topic.{topic}.retrieval_hit_rate", metrics.retrieval_hit_rate, None, None, "%"),
            (
                f"topic.{topic}.context_answerability_rate",
                metrics.context_answerability_rate, None, None, "% of judged contexts",
            ),
            (f"topic.{topic}.passed", metrics.passed, None, None, "cases"),
            (f"topic.{topic}.total", metrics.total, None, None, "cases"),
            (
                f"topic.{topic}.average_cost_usd",
                metrics.average_cost_usd, None, None, "USD",
            ),
            (
                f"topic.{topic}.average_latency_ms",
                metrics.average_latency_ms, None, None, "ms",
            ),
        ])
    with summary_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["metric", "current", "baseline", "delta", "unit"])
        writer.writerows(metric_rows)
    with scorecard_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "RETRIEVAL HIT (CHI SIMILARITY)",
            f"{report.summary.retrieval_hit_rate:.2%} "
            f"({report.summary.retrieval_hit_count}/{report.summary.total} cau)",
        ])
        writer.writerow([
            "CONTEXT ANSWERABLE (DA JUDGE)",
            f"{report.summary.context_answerability_rate:.2%} "
            f"({report.summary.context_answerable_count}/"
            f"{report.summary.context_judged_count} context)",
        ])
        writer.writerow(["TỶ LỆ ĐÚNG", f"{report.summary.pass_rate:.2%}"])
        writer.writerow([
            "TỶ LỆ CÓ ĐỦ DỮ LIỆU",
            f"{report.summary.data_sufficiency_rate:.2%} "
            f"({report.summary.data_sufficient_count}/{report.summary.total} câu)",
        ])
        writer.writerow([
            "CHI PHÍ TRUNG BÌNH MỖI LƯỢT",
            f"${report.summary.average_cost_usd:.8f} "
            f"(${report.summary.total_cost_usd:.8f}/{report.summary.total} lượt)",
        ])
        writer.writerow([
            "CHI PHÍ TRUNG BÌNH LƯỢT GỌI MODEL",
            f"${report.summary.average_model_call_cost_usd:.8f} "
            f"({report.summary.model_calls} lượt gọi model; "
            f"{report.summary.zero_cost_turns} lượt $0)",
        ])
        writer.writerow([
            "ĐỘ TRỄ TRUNG BÌNH MỖI LƯỢT",
            f"{report.summary.average_latency_ms:.2f} ms",
        ])
        writer.writerow([])
        writer.writerow(["CÂU HỎI SAI", "REPLY SAI"])
        for result in report.results:
            if result.status == "FAIL":
                writer.writerow([result.question, result.reply])
    with manual_review_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "CÂU HỎI", "CÂU TRẢ LỜI", "CHI PHÍ ƯỚC TÍNH (USD)", "ĐỘ TRỄ (ms)",
        ])
        for result in report.results:
            writer.writerow([
                result.question,
                result.reply,
                f"{result.cost_usd:.8f}",
                result.latency_ms,
            ])
    with topics_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "CHỦ ĐỀ", "TỔNG", "ĐẠT", "SAI", "ERROR", "REVIEW",
            "TỶ LỆ ĐÚNG", "TỶ LỆ CÓ ĐỦ DỮ LIỆU",
            "CHI PHÍ TB ƯỚC TÍNH (USD)", "ĐỘ TRỄ TB (ms)",
        ])
        for topic, metrics in report.summary.topic_metrics.items():
            writer.writerow([
                topic,
                metrics.total,
                metrics.passed,
                metrics.failed,
                metrics.errors,
                metrics.manual_review,
                f"{metrics.pass_rate:.2%}",
                f"{metrics.data_sufficiency_rate:.2%}",
                f"{metrics.average_cost_usd:.8f}",
                f"{metrics.average_latency_ms:.2f}",
            ])
    return (
        json_path, csv_path, summary_path, scorecard_path, manual_review_path,
        topics_path,
    )


def load_report_summary(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    summary = data.get("summary")
    if not isinstance(summary, dict):
        raise EvalConfigError(f"Báo cáo baseline thiếu summary: {path}")
    return {
        "run_id": data.get("run_id"),
        "fingerprint": data.get("fingerprint"),
        "case_fingerprint": data.get("case_fingerprint") or data.get("fingerprint"),
        **summary,
    }
