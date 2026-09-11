"""H4-05 controlled before/after experiment using TEST/SYNTHETIC documents.

The baseline and post-document runs share the same cases, prompt, tenant
configuration, model sampling, retrieval threshold, and chunking output.  The
only experimental change is appending reviewed H4-04 document chunks to a
copy of the baseline knowledge index.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

import yaml

import ai_core.chat as chat_module
from ai_core.config import load_config
from ai_core.evaluator import EvalReport, build_case_fingerprint, run_eval, save_report
from ai_core.prompt import PROMPT_VERSION, build_system_prompt
from ai_core.retriever import use_index_dir
from ai_core.trace import find_trace
from eval.answerability import CachedAnswerabilityJudge
from eval.preflight_h4_05 import run_preflight, save_preflight
from index_chunks import build_index_with_fallback, load_cache, load_chunks, save_cache, save_index


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES = ROOT / "eval" / "cases_h4_05_test.yaml"
DEFAULT_DOCUMENT_CHUNKS = ROOT / "outputs" / "h4_05" / "test-document-chunks.json"
DEFAULT_BASE_INDEX = ROOT / "index"
DEFAULT_OUTPUT = ROOT / "outputs" / "h4_05" / "experiment"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_expected_evidence(path: Path | None) -> dict[str, str]:
    """Load optional case_id -> required context text without dataset hardcoding."""

    if path is None:
        return {}
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"Không đọc được expected evidence {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Expected evidence phải là object case_id -> chuỗi evidence.")
    normalized = {str(key).strip(): str(value).strip() for key, value in payload.items()}
    if not normalized or any(not key or not value for key, value in normalized.items()):
        raise ValueError("Expected evidence không được rỗng.")
    return normalized


def prepare_augmented_chunks(base_metadata: Path, added_chunks: Path, output: Path) -> tuple[int, int]:
    base_raw = json.loads(base_metadata.read_text(encoding="utf-8"))
    base = [
        {key: item[key] for key in ("tenant_id", "chunk_id", "content", "metadata")}
        for item in base_raw
    ]
    added = load_chunks(added_chunks)
    combined = base + added
    keys = [(item["tenant_id"], item["chunk_id"]) for item in combined]
    if len(keys) != len(set(keys)):
        raise ValueError("Trùng tenant_id/chunk_id giữa baseline và tài liệu thêm.")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
    load_chunks(output)
    return len(base), len(added)


def build_augmented_index(base_index: Path, chunks_path: Path, out_dir: Path) -> dict[str, Any]:
    manifest = json.loads((base_index / "manifest.json").read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_path = out_dir / "embedding_cache.json"
    if not cache_path.exists():
        shutil.copy2(base_index / "embedding_cache.json", cache_path)
    chunks = load_chunks(chunks_path)
    cache = load_cache(cache_path)
    candidate = [(str(manifest["provider"]), str(manifest["model"]))]
    records, cache, new, cached, provider, model = build_index_with_fallback(
        chunks, cache, candidate
    )
    save_cache(cache_path, cache)
    save_index(records, out_dir, provider=provider, model=model)
    return {"record_count": len(records), "embedded_new": new, "cache_hits": cached,
            "provider": provider, "model": model}


def chat_for_index(
    index_dir: Path,
    *,
    chat_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Bind one eval callable to one index inside the executing worker thread.

    ``ContextVar`` values do not automatically move from the caller into a new
    ``ThreadPoolExecutor`` worker.  Opening ``use_index_dir`` inside this
    wrapper makes every individual request select its own index and avoids the
    process-global ``chat_module.retrieve`` monkeypatch previously used here.
    """

    resolved = Path(index_dir).resolve()
    delegate = chat_fn or chat_module.chat_for_eval

    def invoke(payload: dict[str, Any]) -> dict[str, Any]:
        with use_index_dir(resolved):
            return delegate(payload)

    return invoke


def _index_chunk_ids(index_dir: Path) -> set[str]:
    metadata = json.loads((index_dir / "metadata.json").read_text(encoding="utf-8"))
    return {str(item["chunk_id"]) for item in metadata}


def audit_report_index(
    report: EvalReport,
    index_dir: Path,
    *,
    forbidden_chunk_ids: set[str] | None = None,
    expected_case_evidence: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Verify every traced retrieval belongs to its arm's assigned index."""

    allowed = _index_chunk_ids(index_dir)
    forbidden = forbidden_chunk_ids or set()
    expected = expected_case_evidence or {}
    traced_ids: set[str] = set()
    violations: list[str] = []
    expected_hits: dict[str, bool] = {}

    for result in report.results:
        trace = find_trace(result.trace_id) if result.trace_id else None
        retrieval = (trace or {}).get("retrieval", {})
        chunks = retrieval.get("chunks", [])
        fallback_candidates = retrieval.get("fallback_candidates", [])
        case_contents = [str(chunk.get("content") or "") for chunk in chunks]
        for chunk in (*chunks, *fallback_candidates):
            chunk_id = str(chunk.get("chunk_id") or "")
            if chunk_id:
                traced_ids.add(chunk_id)
                if chunk_id not in allowed:
                    violations.append(f"{result.id}: chunk ngoài index {chunk_id}")
                if chunk_id in forbidden:
                    violations.append(f"{result.id}: chunk tài liệu mới lọt vào baseline {chunk_id}")
        if result.id in expected:
            needle = expected[result.id].casefold()
            hit = any(needle in content.casefold() for content in case_contents)
            expected_hits[result.id] = hit
            if not hit:
                violations.append(f"{result.id}: thiếu evidence bắt buộc {expected[result.id]!r}")

    return {
        "index_dir": str(Path(index_dir).resolve()),
        "allowed_chunk_count": len(allowed),
        "traced_chunk_count": len(traced_ids),
        "expected_evidence_hits": expected_hits,
        "violations": violations,
        "passed": not violations,
    }


def _baseline_payload(report: EvalReport) -> dict[str, Any]:
    return {
        "run_id": report.run_id,
        "case_fingerprint": report.case_fingerprint,
        "fingerprint": report.fingerprint,
        **report.summary.model_dump(mode="json"),
    }


def conclusion(pass_delta: float, sufficiency_delta: float) -> str:
    if pass_delta >= 0.10:
        return (
            "DATA_BOTTLENECK_CONFIRMED: chỉ thêm tài liệu đã làm điểm đúng tăng mạnh; "
            "độ phủ hiệu dụng được xác nhận bằng similarity, evidence trực tiếp và "
            "khả năng dùng được của phản hồi cuối."
        )
    if sufficiency_delta >= 0.10:
        return "TECHNICAL_OR_POLICY_BOTTLENECK: dữ liệu đã phủ tốt hơn nhưng điểm đúng chưa tăng tương ứng."
    return "NO_CLEAR_DATA_EFFECT: tài liệu chưa làm tăng đáng kể độ phủ; cần xem mức liên quan/retrieval."


def write_comparison(
    output: Path, baseline: EvalReport, post: EvalReport, controls: dict[str, Any],
    index_stats: dict[str, Any], base_count: int, added_count: int,
    *, tenant_id: str, data_label: str,
) -> tuple[Path, Path]:
    pass_delta = round(post.summary.pass_rate - baseline.summary.pass_rate, 4)
    suff_delta = round(
        post.summary.data_sufficiency_rate - baseline.summary.data_sufficiency_rate, 4
    )
    retrieval_delta = round(
        post.summary.retrieval_hit_rate - baseline.summary.retrieval_hit_rate, 4
    )
    answerability_delta = round(
        post.summary.context_answerability_rate
        - baseline.summary.context_answerability_rate, 4
    )
    payload = {
        "data_label": data_label,
        "tenant_id": tenant_id,
        "only_changed_variable": "added_document_chunks",
        "baseline": baseline.summary.model_dump(mode="json"),
        "post_documents": post.summary.model_dump(mode="json"),
        "delta": {
            "pass_rate": pass_delta,
            "retrieval_hit_rate": retrieval_delta,
            "context_answerability_rate": answerability_delta,
            "data_sufficiency_rate": suff_delta,
        },
        "knowledge": {"baseline_chunks": base_count, "added_chunks": added_count,
                      "post_chunks": base_count + added_count, **index_stats},
        "controls": controls,
        "conclusion": conclusion(pass_delta, suff_delta),
    }
    json_path = output / "h4_05_comparison.json"
    md_path = output / "h4_05_comparison.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(
        "\n".join([
            f"# H4-05 — Tác động của tài liệu ({data_label})", "",
            f"> Tenant: `{tenant_id}`. Nhãn dữ liệu do người chạy cung cấp: `{data_label}`.", "",
            "| Chỉ số | Trước | Sau | Chênh lệch |", "| --- | ---: | ---: | ---: |",
            f"| Điểm đúng | {baseline.summary.pass_rate:.1%} | {post.summary.pass_rate:.1%} | {pass_delta:+.1%} |",
            f"| Tỷ lệ đủ dữ liệu | {baseline.summary.data_sufficiency_rate:.1%} | {post.summary.data_sufficiency_rate:.1%} | {suff_delta:+.1%} |",
            f"| Số câu đạt | {baseline.summary.passed}/{baseline.summary.evaluated} | {post.summary.passed}/{post.summary.evaluated} | {post.summary.passed-baseline.summary.passed:+d} |",
            f"| Lỗi hạ tầng | {baseline.summary.errors} | {post.summary.errors} | {post.summary.errors-baseline.summary.errors:+d} |",
            "", f"Retrieval hit (similarity only): {baseline.summary.retrieval_hit_rate:.1%} -> {post.summary.retrieval_hit_rate:.1%} ({retrieval_delta:+.1%}).",
            f"Context answerable (LLM judge): {baseline.summary.context_answerability_rate:.1%} -> {post.summary.context_answerability_rate:.1%} ({answerability_delta:+.1%}).",
            f"Effective data sufficiency: {baseline.summary.data_sufficiency_rate:.1%} -> {post.summary.data_sufficiency_rate:.1%} ({suff_delta:+.1%}).",
            "", f"Knowledge index: {base_count} + {added_count} = {base_count + added_count} chunks.",
            "", f"Kết luận: **{payload['conclusion']}**", "",
            "Biến kiểm soát được khóa: cùng case fingerprint, tenant/config, prompt version, "
            "temperature 0, model policy, embedding model và retrieval threshold. Chỉ index sau có thêm tài liệu.",
        ]) + "\n", encoding="utf-8"
    )
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Chạy thí nghiệm H4-05 trước/sau có kiểm soát.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--document-chunks", type=Path, default=DEFAULT_DOCUMENT_CHUNKS)
    parser.add_argument(
        "--document-manifest", type=Path,
        help="Manifest H4-04; mặc định suy ra cạnh file document chunks.",
    )
    parser.add_argument(
        "--expected-evidence", type=Path,
        help="JSON/YAML tùy chọn ánh xạ case_id -> đoạn evidence bắt buộc.",
    )
    parser.add_argument("--base-index", type=Path, default=DEFAULT_BASE_INDEX)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--data-label", default="UNSPECIFIED")
    parser.add_argument("--config-version", type=int, default=1)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--requests-per-minute", type=float, default=30.0)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--confirm-paid-run", action="store_true")
    parser.add_argument("--max-estimated-usd", type=float, default=1.0)
    args = parser.parse_args()
    args.data_label = args.data_label.strip()
    if not args.data_label:
        parser.error("--data-label không được để trống.")
    try:
        expected_evidence = load_expected_evidence(args.expected_evidence)
    except ValueError as exc:
        parser.error(str(exc))

    args.output.mkdir(parents=True, exist_ok=True)
    preflight = run_preflight(
        cases_path=args.cases,
        document_chunks=args.document_chunks,
        base_index=args.base_index,
        output=args.output,
        tenant_id=args.tenant_id,
        config_version=args.config_version,
        document_manifest=args.document_manifest,
        expected_evidence=expected_evidence,
    )
    preflight_json, preflight_md = save_preflight(preflight, args.output)
    print(f"PREFLIGHT: {'PASS' if preflight['passed'] else 'FAIL'}")
    print(preflight_md)
    if not preflight["passed"]:
        return 2
    if args.preflight_only:
        return 0
    estimated_usd = float(preflight["estimated"]["model_cost_usd_upper_bound"])
    if args.max_estimated_usd < 0 or estimated_usd > args.max_estimated_usd:
        print(
            f"DỪNG: chi phí model ước tính ${estimated_usd:.8f} vượt "
            f"--max-estimated-usd ${args.max_estimated_usd:.8f}."
        )
        return 2
    if not args.confirm_paid_run:
        print(
            "DỪNG trước API trả phí. Chạy lại với --confirm-paid-run sau khi xem "
            f"{preflight_json}."
        )
        return 2
    base_hash_before = _sha256(args.base_index / "metadata.json")
    combined = args.output / "augmented_chunks.json"
    base_count, added_count = prepare_augmented_chunks(
        args.base_index / "metadata.json", args.document_chunks, combined
    )
    post_index = args.output / "post_index"
    index_stats = build_augmented_index(args.base_index, combined, post_index)
    config = load_config(args.tenant_id, args.config_version)
    answerability_judge = CachedAnswerabilityJudge(args.tenant_id, args.config_version)
    controls = {
        "case_fingerprint": build_case_fingerprint(args.cases),
        "tenant_config_sha256": _sha256(ROOT / "tenants" / f"{args.tenant_id}.yaml"),
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": hashlib.sha256(build_system_prompt(config).encode()).hexdigest(),
        "temperature": 0.0,
        "semantic_response_cache": False,
        "document_chunk_size": 900,
        "document_overlap_chars": 120,
        "model_policy": config.model_policy.model_dump(mode="json"),
        "retrieval_policy": config.retrieval_policy.model_dump(mode="json"),
        "data_sufficiency": {
            "formula": "retrieval_hit AND context_answerable AND final_response_usable",
            "answerability_judge": "llm_primary_with_fallback_temperature_0",
            "max_chunks": 5,
            "excludes": ["fallback_response", "handoff", "guardrail_blocked"],
        },
        "embedding": {"provider": index_stats["provider"], "model": index_stats["model"]},
        "document_chunks_sha256": _sha256(args.document_chunks),
        "document_manifest": (
            str(args.document_manifest.resolve()) if args.document_manifest else None
        ),
        "expected_evidence": expected_evidence,
        "index_routing": {
            "mode": "contextvar_per_worker",
            "baseline_index": str(args.base_index.resolve()),
            "post_document_index": str(post_index.resolve()),
        },
    }
    experiment = {"id": "H4-05", "data_label": args.data_label, "controls": controls}

    baseline_chat = chat_for_index(args.base_index)
    post_chat = chat_for_index(post_index)
    # Disable response replay equally in both arms. Index selection itself is
    # request-local and does not use a process-global monkeypatch.
    with patch.dict(os.environ, {"AI_CORE_SEMANTIC_CACHE_ENABLED": "0"}):
        baseline = run_eval(
            args.cases, baseline_chat, tenant_id=args.tenant_id,
            config_version=args.config_version, workers=args.workers,
            requests_per_minute=args.requests_per_minute, diagnostic_resolver=find_trace,
            answerability_fn=answerability_judge,
            experiment_context=experiment,
        )
        post = run_eval(
            args.cases, post_chat, tenant_id=args.tenant_id,
            config_version=args.config_version, baseline=_baseline_payload(baseline),
            workers=args.workers, requests_per_minute=args.requests_per_minute,
            diagnostic_resolver=find_trace, answerability_fn=answerability_judge,
            experiment_context=experiment,
        )
    controls["data_sufficiency"]["judge_stats"] = answerability_judge.stats()
    save_report(baseline, args.output / "baseline")
    save_report(post, args.output / "post_documents")
    base_ids = _index_chunk_ids(args.base_index)
    post_ids = _index_chunk_ids(post_index)
    added_ids = post_ids - base_ids
    baseline_audit = audit_report_index(
        baseline,
        args.base_index,
        forbidden_chunk_ids=added_ids,
    )
    post_audit = audit_report_index(
        post,
        post_index,
        expected_case_evidence=expected_evidence,
    )
    controls["index_routing"]["audit"] = {
        "baseline": baseline_audit,
        "post_documents": post_audit,
    }
    if _sha256(args.base_index / "metadata.json") != base_hash_before:
        raise RuntimeError("Baseline index đã bị thay đổi ngoài ý muốn.")
    json_path, md_path = write_comparison(
        args.output, baseline, post, controls, index_stats, base_count, added_count,
        tenant_id=args.tenant_id, data_label=args.data_label,
    )
    print(f"Baseline: accuracy={baseline.summary.pass_rate:.1%}, data={baseline.summary.data_sufficiency_rate:.1%}")
    print(f"Sau tài liệu: accuracy={post.summary.pass_rate:.1%}, data={post.summary.data_sufficiency_rate:.1%}")
    print(json_path)
    print(md_path)
    audit_failed = not baseline_audit["passed"] or not post_audit["passed"]
    if audit_failed:
        print("INDEX ROUTING AUDIT: FAIL")
        for item in (*baseline_audit["violations"], *post_audit["violations"]):
            print(f"- {item}")
    else:
        print("INDEX ROUTING AUDIT: PASS")
    return 2 if baseline.summary.errors or post.summary.errors or audit_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
