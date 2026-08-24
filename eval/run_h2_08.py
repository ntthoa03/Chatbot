"""Đo semantic-cache hit rate trên 60 case normal H2-01 mà không gọi lại chat model."""

from __future__ import annotations

import argparse
import csv
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from ai_core.cache import SemanticResponseCache, request_is_cacheable, response_is_cacheable
from ai_core.config import load_config
from ai_core.embedder import embed_texts
from ai_core.evaluator import load_cases, score_case


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "outputs" / "h2_01" / "20260817T045849.767071Z.json"
DEFAULT_CASES = ROOT / "eval" / "cases.yaml"
DEFAULT_TRACES = ROOT / "outputs" / "traces.jsonl"
DEFAULT_OUTPUT = ROOT / "outputs" / "h2_08"
THRESHOLDS = (0.92, 0.94, 0.96, 0.98)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_trace_responses(path: Path, wanted: set[str]) -> dict[str, dict[str, Any]]:
    responses: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            trace_id = str(item.get("trace_id", ""))
            if trace_id in wanted and isinstance(item.get("final_response"), dict):
                responses[trace_id] = item["final_response"]
    return responses


def _load_or_embed(
    questions: list[str],
    *,
    provider: str,
    model: str,
    path: Path,
) -> list[list[float]]:
    if path.exists():
        cached = _load_json(path)
        if (
            cached.get("provider") == provider
            and cached.get("model") == model
            and cached.get("questions") == questions
        ):
            return cached["vectors"]
    vectors = embed_texts(
        questions,
        provider=provider,
        model=model,
        task_type="RETRIEVAL_QUERY",
    )
    path.write_text(
        json.dumps(
            {"provider": provider, "model": model, "questions": questions, "vectors": vectors},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return vectors


def _baseline_response(result: dict[str, Any], traces: dict[str, dict[str, Any]]) -> dict[str, Any]:
    response = dict(traces.get(str(result.get("trace_id", "")), {}))
    if not response:
        response = {
            "reply": result.get("reply", ""),
            "sources": [],
            "tool_calls": [],
            "need_human": result.get("need_human", False),
            "lead_captured": None,
            "guardrail": {"blocked": result.get("guardrail_blocked", False), "reason": None},
            "usage": {
                "model": result.get("model", ""),
                "tokens_in": 0,
                "tokens_out": 0,
                "cost_usd": result.get("cost_usd", 0.0),
                "latency_ms": result.get("latency_ms", 0),
            },
            "trace_id": result.get("trace_id", ""),
        }
    response["_h2_source_case_id"] = result["id"]
    return response


def run_experiment(
    *,
    baseline_path: Path,
    cases_path: Path,
    traces_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    baseline = _load_json(baseline_path)
    results = [item for item in baseline["results"] if item.get("type") == "normal"][:60]
    if len(results) != 60:
        raise RuntimeError(f"Cần đúng 60 normal case H2-01, hiện có {len(results)}.")
    cases = {case.id: case for case in load_cases(cases_path)}
    wanted_traces = {str(item.get("trace_id", "")) for item in results}
    trace_responses = _load_trace_responses(traces_path, wanted_traces)
    responses = {item["id"]: _baseline_response(item, trace_responses) for item in results}

    tenant_id = str(baseline["tenant_id"])
    config_version = int(baseline["config_version"])
    config = load_config(tenant_id, config_version)
    embedding = config.embedding_policy.primary
    questions = [str(item["question"]) for item in results]
    output_dir.mkdir(parents=True, exist_ok=True)
    vectors = _load_or_embed(
        questions,
        provider=embedding.provider,
        model=embedding.model,
        path=output_dir / "question_embeddings.json",
    )
    vector_by_id = {item["id"]: vector for item, vector in zip(results, vectors)}

    baseline_passed = sum(bool(item.get("passed")) for item in results)
    summaries: list[dict[str, Any]] = []
    detail_rows: list[dict[str, Any]] = []
    for threshold in THRESHOLDS:
        cache = SemanticResponseCache(similarity_threshold=threshold)
        threshold_rows: list[dict[str, Any]] = []
        for round_number in (1, 2):
            for result in results:
                case_id = result["id"]
                question = str(result["question"])
                vector = vector_by_id[case_id]
                # Domain/tool case bị loại vì production chat cũng không cache tool response.
                eligible = result.get("topic") != "domain" and request_is_cacheable(question, [])
                lookup = None
                effective = responses[case_id]
                if eligible:
                    lookup = cache.lookup(
                        tenant_id=tenant_id,
                        config_version=config_version,
                        question=question,
                        vector=vector,
                    )
                    if lookup.hit and lookup.response is not None:
                        effective = lookup.response
                    elif response_is_cacheable(effective):
                        cache.put(
                            tenant_id=tenant_id,
                            config_version=config_version,
                            question=question,
                            vector=vector,
                            response=effective,
                        )

                source_case_id = str(effective.get("_h2_source_case_id", case_id))
                semantic_hit = bool(lookup and lookup.hit and source_case_id != case_id)
                exact_hit = bool(lookup and lookup.hit and source_case_id == case_id)
                if semantic_hit:
                    scored = score_case(cases[case_id], effective)
                    quality_status = scored.status
                    quality_passed: bool | None = scored.passed if scored.status != "MANUAL_REVIEW" else None
                else:
                    quality_status = str(result["status"])
                    quality_passed = bool(result["passed"])
                row = {
                    "threshold": threshold,
                    "round": round_number,
                    "case_id": case_id,
                    "topic": result.get("topic"),
                    "question": question,
                    "eligible": eligible,
                    "cache_hit": bool(lookup and lookup.hit),
                    "hit_type": "semantic" if semantic_hit else "exact" if exact_hit else "miss",
                    "similarity": lookup.similarity if lookup else None,
                    "matched_question": lookup.matched_question if lookup else None,
                    "source_case_id": source_case_id,
                    "baseline_passed": bool(result["passed"]),
                    "quality_status": quality_status,
                    "quality_passed": quality_passed,
                    "reply": effective.get("reply", ""),
                }
                threshold_rows.append(row)
                detail_rows.append(row)

        eligible_rows = [row for row in threshold_rows if row["eligible"]]
        hit_rows = [row for row in eligible_rows if row["cache_hit"]]
        semantic_rows = [row for row in hit_rows if row["hit_type"] == "semantic"]
        cold_eligible = [row for row in eligible_rows if row["round"] == 1]
        warm_eligible = [row for row in eligible_rows if row["round"] == 2]
        cold_hits = [row for row in cold_eligible if row["cache_hit"]]
        warm_hits = [row for row in warm_eligible if row["cache_hit"]]
        warm_rows = [row for row in threshold_rows if row["round"] == 2]
        warm_known = [row for row in warm_rows if row["quality_passed"] is not None]
        regressions = [
            row
            for row in warm_known
            if row["baseline_passed"] and row["quality_passed"] is False
        ]
        manual_semantic = [row for row in semantic_rows if row["quality_passed"] is None]
        warm_passed = sum(row["quality_passed"] is True for row in warm_known)
        summaries.append(
            {
                "threshold": threshold,
                "requests": len(threshold_rows),
                "eligible_lookups": len(eligible_rows),
                "hits": len(hit_rows),
                "hit_rate": round(len(hit_rows) / len(eligible_rows), 4) if eligible_rows else 0.0,
                "cold_hits": len(cold_hits),
                "cold_hit_rate": round(len(cold_hits) / len(cold_eligible), 4) if cold_eligible else 0.0,
                "warm_hits": len(warm_hits),
                "warm_hit_rate": round(len(warm_hits) / len(warm_eligible), 4) if warm_eligible else 0.0,
                "warm_hit_rate_all_60": round(len(warm_hits) / 60, 4),
                "exact_hits": sum(row["hit_type"] == "exact" for row in hit_rows),
                "semantic_hits": len(semantic_rows),
                "semantic_manual_review": len(manual_semantic),
                "warm_quality_known": len(warm_known),
                "warm_passed": warm_passed,
                "warm_pass_rate": round(warm_passed / len(warm_known), 4) if warm_known else 0.0,
                "baseline_passed": baseline_passed,
                "baseline_pass_rate": round(baseline_passed / 60, 4),
                "quality_regressions": len(regressions),
            }
        )

    # Ưu tiên không regression, không cần review semantic, sau đó mới tối đa hóa hit rate.
    safe = [
        item
        for item in summaries
        if item["quality_regressions"] == 0 and item["semantic_manual_review"] == 0
    ]
    # Nếu hit rate bằng nhau thì chọn ngưỡng cao hơn để giảm nguy cơ trả nhầm.
    recommended = max(
        safe,
        key=lambda item: (item["hit_rate"], item["threshold"]),
        default=None,
    )
    payload = {
        "schema_version": "h2-08.experiment.v1",
        "created_at": datetime.now(UTC).isoformat(),
        "tenant_id": tenant_id,
        "config_version": config_version,
        "baseline_run_id": baseline.get("run_id"),
        "dataset": "60 normal cases H2-01, chạy cold + warm",
        "embedding_provider": embedding.provider,
        "embedding_model": embedding.model,
        "baseline_passed": baseline_passed,
        "baseline_pass_rate": round(baseline_passed / 60, 4),
        "summaries": summaries,
        "recommended": recommended,
        "limitation": "H2-07 chưa hoàn thành; phép đo dùng baseline H2-01 và không gọi lại chat model.",
    }
    (output_dir / "H2-08-experiment.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (output_dir / "H2-08-summary.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summaries[0]))
        writer.writeheader()
        writer.writerows(summaries)
    with (output_dir / "H2-08-details.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(detail_rows[0]))
        writer.writeheader()
        writer.writerows(detail_rows)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--traces", type=Path, default=DEFAULT_TRACES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = run_experiment(
        baseline_path=args.baseline,
        cases_path=args.cases,
        traces_path=args.traces,
        output_dir=args.output_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
