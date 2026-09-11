"""Free local checks that must pass before the paid H4-05 experiment."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from ai_core.config import load_config
from ai_core.evaluator import load_cases
from index_chunks import load_chunks


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


def run_preflight(
    *, cases_path: Path, document_chunks: Path, base_index: Path,
    output: Path, tenant_id: str, config_version: int,
    document_manifest: Path | None = None,
    expected_evidence: dict[str, str] | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    cases = []
    chunks = []
    config = None
    try:
        cases = load_cases(cases_path)
        checks.append(_check("eval_cases", True, f"{len(cases)} cases hợp lệ"))
    except Exception as exc:
        checks.append(_check("eval_cases", False, str(exc)))
    try:
        chunks = load_chunks(document_chunks)
        tenants = {item["tenant_id"] for item in chunks}
        checks.append(_check(
            "document_chunks", tenants == {tenant_id},
            f"{len(chunks)} chunks; tenants={sorted(tenants)}",
        ))
    except Exception as exc:
        checks.append(_check("document_chunks", False, str(exc)))
    checks.append(_check(
        "not_pending_ocr_review",
        not document_chunks.name.endswith(".ocr-review.json"),
        "Không cho eval trực tiếp bản OCR chưa promote.",
    ))
    selected_document_manifest = document_manifest or document_chunks.with_suffix(".manifest.json")
    if selected_document_manifest.exists():
        try:
            manifest = json.loads(selected_document_manifest.read_text(encoding="utf-8"))
            used_ocr = any(
                isinstance(item, dict) and item.get("used_ocr")
                for item in manifest.get("documents", [])
            )
            legacy_reviewed = all(
                not isinstance(item, dict)
                or not item.get("used_ocr")
                or (
                    item.get("requires_manual_review") is False
                    and item.get("ready_for_index") is True
                )
                for item in manifest.get("documents", [])
            )
            review_ok = not used_ocr or (
                manifest.get("ready_for_index") is True
                and (
                    (manifest.get("review") or {}).get("status") == "approved"
                    or legacy_reviewed
                )
            )
            checks.append(_check(
                "ocr_review_approved", review_ok,
                (
                    "OCR đã promote"
                    if (manifest.get("review") or {}).get("status") == "approved"
                    else "OCR legacy đã đánh dấu reviewed; lượt mới phải dùng promote"
                ) if review_ok else "Manifest OCR chưa có review approved",
            ))
        except Exception as exc:
            checks.append(_check("ocr_review_approved", False, str(exc)))
    elif document_manifest is not None:
        checks.append(_check(
            "document_manifest", False,
            f"Không tìm thấy manifest được chỉ định: {selected_document_manifest}",
        ))
    else:
        checks.append(_check(
            "document_manifest", True,
            "Không có manifest đi kèm; input được coi là chunk không OCR/legacy.",
        ))
    if expected_evidence:
        case_ids = {case.id for case in cases}
        unknown = sorted(set(expected_evidence) - case_ids)
        valid_values = all(
            isinstance(value, str) and value.strip()
            for value in expected_evidence.values()
        )
        checks.append(_check(
            "expected_evidence",
            not unknown and valid_values,
            (
                f"{len(expected_evidence)} case evidence hợp lệ"
                if not unknown and valid_values
                else f"case không có trong suite={unknown}; evidence rỗng={not valid_values}"
            ),
        ))
    try:
        manifest = json.loads((base_index / "manifest.json").read_text(encoding="utf-8"))
        metadata = json.loads((base_index / "metadata.json").read_text(encoding="utf-8"))
        vectors = np.load(base_index / "vectors.npy", mmap_mode="r")
        valid = (
            vectors.ndim == 2
            and vectors.shape[0] == len(metadata) == int(manifest["record_count"])
            and vectors.shape[1] == int(manifest["dimension"])
        )
        checks.append(_check(
            "base_index", valid,
            f"records={len(metadata)}, dimension={vectors.shape[1] if vectors.ndim == 2 else 'invalid'}",
        ))
        isolated = all(item.get("tenant_id") == tenant_id for item in metadata)
        checks.append(_check("base_index_tenant", isolated, f"tenant={tenant_id}"))
    except Exception as exc:
        checks.append(_check("base_index", False, str(exc)))
    try:
        config = load_config(tenant_id, config_version)
        checks.append(_check("tenant_config", True, f"version={config_version}"))
    except Exception as exc:
        checks.append(_check("tenant_config", False, str(exc)))
    if config is not None:
        providers = {
            "gemini" if model.casefold().startswith("gemini") else "openai"
            for model in (config.model_primary, config.model_fallback)
        }
        available = {
            "gemini": bool(os.getenv("GEMINI_API_KEY")),
            "openai": bool(os.getenv("OPENAI_API_KEY")),
        }
        usable = [provider for provider in providers if available[provider]]
        checks.append(_check(
            "provider_credentials", bool(usable),
            f"provider có credential: {', '.join(sorted(usable)) or 'không có'}",
        ))
    try:
        output.mkdir(parents=True, exist_ok=True)
        probe = output / ".preflight-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks.append(_check("output_writable", True, str(output.resolve())))
    except Exception as exc:
        checks.append(_check("output_writable", False, str(exc)))

    chat_calls = len(cases) * 2
    judge_calls_upper_bound = len(cases) * 2
    embedding_calls_upper_bound = len(chunks)
    estimated_cost = 0.0
    if config is not None:
        for model in (config.model_primary, config.model_fallback):
            # Upper bound assumes every chat/judge request reaches the selected model.
            estimated_cost = max(
                estimated_cost,
                chat_calls * config.model_policy.estimate_cost_usd(model, 3000, 700)
                + judge_calls_upper_bound
                * config.model_policy.estimate_cost_usd(model, 1800, 200),
            )
    return {
        "schema_version": "h4-05.preflight.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tenant_id": tenant_id,
        "passed": all(item["passed"] for item in checks),
        "checks": checks,
        "estimated": {
            "chat_calls": chat_calls,
            "answerability_judge_calls_upper_bound": judge_calls_upper_bound,
            "embedding_items_upper_bound": embedding_calls_upper_bound,
            "model_cost_usd_upper_bound": round(estimated_cost, 8),
            "embedding_cost_note": "Không cộng vì bảng giá embedding chưa có trong tenant config.",
        },
    }


def save_preflight(report: dict[str, Any], output: Path) -> tuple[Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "preflight.json"
    md_path = output / "preflight.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# H4-05 — Preflight trước API trả phí", "",
        f"Kết quả: **{'PASS' if report['passed'] else 'FAIL'}**", "",
        "| Kiểm tra | Kết quả | Chi tiết |", "| --- | --- | --- |",
    ]
    for item in report["checks"]:
        detail = str(item["detail"]).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {item['name']} | {'PASS' if item['passed'] else 'FAIL'} | {detail} |")
    estimate = report["estimated"]
    lines.extend([
        "", "## Ước tính", "",
        f"- Chat: {estimate['chat_calls']} calls.",
        f"- Answerability judge tối đa: {estimate['answerability_judge_calls_upper_bound']} calls.",
        f"- Embedding tối đa: {estimate['embedding_items_upper_bound']} items.",
        f"- Chi phí model ước tính tối đa: ${estimate['model_cost_usd_upper_bound']:.8f}.",
        f"- {estimate['embedding_cost_note']}",
    ])
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path
