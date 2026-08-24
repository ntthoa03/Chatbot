"""Chạy ma trận tinh chỉnh RAG H2-07, mỗi profile chỉ đổi đúng một biến."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

import yaml

import ai_core.chat as chat_module
from ai_core.evaluator import EvalReport, report_as_dict, run_eval, save_report
from ai_core.trace import find_trace
from eval.tune import _split_content


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "outputs" / "h2_07"
SOURCE_CHUNKS = ROOT / "seed_chunks.json"
SOURCE_CASES = ROOT / "eval" / "cases.yaml"
SOURCE_LOGS = ROOT / "eval" / "synthetic_zalo_fanpage_h2_01.jsonl"
TENANT_ID = "mima_internal"
CONFIG_VERSION = 1
BASELINE = {"chunk_size": 500, "overlap": 50, "top_k": 5, "threshold": 0.65}


@dataclass(frozen=True)
class Profile:
    name: str
    chunk_size: int
    overlap: int
    top_k: int
    threshold: float
    changed_variable: str

    @property
    def index_name(self) -> str:
        return f"chunk_{self.chunk_size}_overlap_{self.overlap}"

    def context(self) -> dict[str, Any]:
        return {
            "task": "H2-07",
            "dataset": "synthetic_h2_01_60",
            "profile": self.name,
            "changed_variable": self.changed_variable,
            "chunk_size": self.chunk_size,
            "overlap": self.overlap,
            "top_k": self.top_k,
            "threshold": self.threshold,
            "relative_score_margin": 0.05,
            "cache_enabled": False,
            "llm_judge_enabled": False,
        }


PROFILES = (
    Profile("baseline", 500, 50, 5, 0.65, "none"),
    Profile("chunk_300", 300, 50, 5, 0.65, "chunk_size"),
    Profile("chunk_800", 800, 50, 5, 0.65, "chunk_size"),
    Profile("overlap_0", 500, 0, 5, 0.65, "overlap"),
    Profile("overlap_100", 500, 100, 5, 0.65, "overlap"),
    Profile("top_k_3", 500, 50, 3, 0.65, "top_k"),
    Profile("top_k_8", 500, 50, 8, 0.65, "top_k"),
    Profile("threshold_050", 500, 50, 5, 0.50, "threshold"),
    Profile("threshold_075", 500, 50, 5, 0.75, "threshold"),
)


def validate_matrix() -> None:
    """Chứng minh mỗi profile ngoài baseline chỉ đổi đúng một biến."""

    keys = ("chunk_size", "overlap", "top_k", "threshold")
    names: set[str] = set()
    for profile in PROFILES:
        if profile.name in names:
            raise RuntimeError(f"Tên profile bị trùng: {profile.name}")
        names.add(profile.name)
        differences = [key for key in keys if getattr(profile, key) != BASELINE[key]]
        if profile.name == "baseline":
            if differences or profile.changed_variable != "none":
                raise RuntimeError("Baseline không được thay đổi biến.")
        elif differences != [profile.changed_variable]:
            raise RuntimeError(
                f"{profile.name} phải đổi đúng một biến; hiện đổi {differences}."
            )


def build_h2_01_cases(destination: Path) -> list[dict[str, Any]]:
    """Lấy đúng 60 case theo thứ tự log H2-01 và giữ rubric gốc."""

    logs = [
        json.loads(line)
        for line in SOURCE_LOGS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    source_cases = yaml.safe_load(SOURCE_CASES.read_text(encoding="utf-8"))
    by_id = {case["id"]: case for case in source_cases}
    selected: list[dict[str, Any]] = []
    for log in logs:
        case_id = log["case_id"]
        if case_id not in by_id:
            raise RuntimeError(f"Log H2-01 tham chiếu case không tồn tại: {case_id}")
        case = dict(by_id[case_id])
        selected.append(case)
    if len(selected) != 60 or len({case["id"] for case in selected}) != 60:
        raise RuntimeError("Bộ H2-01 phải có đúng 60 case ID duy nhất.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        yaml.safe_dump(selected, allow_unicode=True, sort_keys=False, width=110),
        encoding="utf-8",
    )
    return selected


def build_chunk_variant(chunk_size: int, overlap: int, destination: Path) -> dict[str, Any]:
    """Gom chunk hiện có theo URL rồi chia lại để các kích thước thực sự khác nhau."""

    source = json.loads(SOURCE_CHUNKS.read_text(encoding="utf-8"))
    grouped: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for item in source:
        if item.get("tenant_id") != TENANT_ID:
            raise RuntimeError("seed_chunks.json chứa tenant ngoài mima_internal.")
        url = str(item.get("metadata", {}).get("url") or "")
        if not url:
            raise RuntimeError("Chunk nguồn thiếu metadata.url.")
        grouped.setdefault(url, []).append(item)

    output: list[dict[str, Any]] = []
    source_chars = 0
    for url, items in grouped.items():
        document = "\n\n".join(str(item["content"]).strip() for item in items)
        source_chars += len(document)
        pieces = _split_content(document, chunk_size, overlap_chars=overlap)
        for index, piece in enumerate(pieces, 1):
            digest = hashlib.sha256(
                f"{url}|{chunk_size}|{overlap}|{index}|{piece}".encode("utf-8")
            ).hexdigest()[:20]
            output.append(
                {
                    "tenant_id": TENANT_ID,
                    "chunk_id": f"h207-{digest}",
                    "content": piece,
                    # Metadata phải giữ đúng contract ingestion; thông số thí
                    # nghiệm được lưu ở prepare_manifest thay vì nhét vào chunk.
                    "metadata": dict(items[0]["metadata"]),
                }
            )
    ids = [item["chunk_id"] for item in output]
    if not output or len(ids) != len(set(ids)):
        raise RuntimeError("Biến thể chunk rỗng hoặc trùng chunk_id.")
    if any(len(item["content"]) > chunk_size for item in output):
        raise RuntimeError("Có chunk vượt quá chunk_size.")
    total_chars = sum(len(item["content"]) for item in output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "chunk_size": chunk_size,
        "overlap": overlap,
        "source_count": len(grouped),
        "chunk_count": len(output),
        "source_chars": source_chars,
        "indexed_chars": total_chars,
        "duplicate_chars": max(0, total_chars - source_chars),
        "duplicate_rate": round(max(0, total_chars - source_chars) / source_chars, 4),
        "average_chunk_chars": round(total_chars / len(output), 2),
        "maximum_chunk_chars": max(len(item["content"]) for item in output),
        "input_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
    }


def prepare_inputs() -> dict[str, Any]:
    """Tạo case suite và năm biến thể chunk, hoàn toàn chưa gọi API."""

    validate_matrix()
    cases_path = OUTPUT_ROOT / "inputs" / "h2_01_60_cases.yaml"
    cases = build_h2_01_cases(cases_path)
    variants: dict[str, Any] = {}
    for profile in PROFILES:
        if profile.index_name in variants:
            continue
        input_path = OUTPUT_ROOT / "inputs" / f"{profile.index_name}.json"
        variants[profile.index_name] = build_chunk_variant(
            profile.chunk_size, profile.overlap, input_path
        )
    manifest = {
        "schema_version": "h2-07.prepare.v1",
        "tenant_id": TENANT_ID,
        "dataset_type": "synthetic",
        "case_count": len(cases),
        "cases_path": str(cases_path.relative_to(ROOT)),
        "profiles": [profile.context() for profile in PROFILES],
        "variants": variants,
    }
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    (OUTPUT_ROOT / "prepare_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def build_indexes(
    *,
    rebuild: bool = False,
    embedding_provider: str = "openai",
    embedding_model: str = "text-embedding-3-small",
) -> None:
    """Embed năm bộ chunk vào thư mục H2-07 riêng, không chạm index production."""

    shared_cache = OUTPUT_ROOT / "indexes" / "embedding_cache.json"
    completed: set[str] = set()
    for profile in PROFILES:
        if profile.index_name in completed:
            continue
        completed.add(profile.index_name)
        index_dir = OUTPUT_ROOT / "indexes" / profile.index_name
        manifest_path = index_dir / "manifest.json"
        if manifest_path.exists() and not rebuild:
            print(f"[index] dùng lại {profile.index_name}", flush=True)
            continue
        input_path = OUTPUT_ROOT / "inputs" / f"{profile.index_name}.json"
        index_dir.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            str(ROOT / "index_chunks.py"),
            "--tenant-id", TENANT_ID,
            "--input", str(input_path),
            "--out-dir", str(index_dir),
            "--cache", str(shared_cache),
            "--provider", embedding_provider,
            "--model", embedding_model,
            "--batch-size", "100",
        ]
        print(f"[index] tạo {profile.index_name}", flush=True)
        subprocess.run(command, cwd=ROOT, check=True)


@contextmanager
def apply_profile(profile: Profile) -> Iterator[None]:
    """Override thử nghiệm tại runtime; không sửa tenant YAML hoặc index production."""

    original_retrieve = chat_module.retrieve
    index_dir = OUTPUT_ROOT / "indexes" / profile.index_name

    def tuned_retrieve(query: str, tenant_id: str, k: int = 5, **kwargs: Any):
        # Chỉ thay threshold của lượt retrieval chính. Các lượt fallback chủ động
        # truyền threshold=0 vẫn giữ nguyên để không vô tình đổi thêm một hành vi.
        kwargs.setdefault("threshold", profile.threshold)
        kwargs["index_dir"] = index_dir
        return original_retrieve(
            query,
            tenant_id,
            k=profile.top_k,
            **kwargs,
        )

    with (
        patch.object(chat_module, "retrieve", tuned_retrieve),
        patch.object(chat_module, "cache_is_enabled", return_value=False),
    ):
        yield


def _baseline_payload(report: EvalReport) -> dict[str, Any]:
    return {
        "run_id": report.run_id,
        "fingerprint": report.fingerprint,
        "case_fingerprint": report.case_fingerprint,
        **report.summary.model_dump(mode="json"),
    }


def _trace_metrics(report: EvalReport) -> dict[str, Any]:
    """Tính chỉ số retrieval từ trace mà không gọi thêm model."""

    attempted = 0
    with_sources = 0
    source_counts: list[int] = []
    score_values: list[float] = []
    rag_passed = 0
    rag_evaluated = 0
    details: dict[str, Any] = {}
    for result in report.results:
        trace = find_trace(result.trace_id) if result.trace_id else None
        retrieval = (trace or {}).get("retrieval") or {}
        query = retrieval.get("query")
        chunks = retrieval.get("chunks") or []
        if query:
            attempted += 1
            source_counts.append(len(chunks))
            if chunks:
                with_sources += 1
            for chunk in chunks:
                if isinstance(chunk, dict) and chunk.get("score") is not None:
                    score_values.append(float(chunk["score"]))
            if result.status in {"PASS", "FAIL"}:
                rag_evaluated += 1
                rag_passed += result.status == "PASS"
        details[result.id] = {
            "trace_id": result.trace_id,
            "retrieval_attempted": bool(query),
            "retrieval_query": query,
            "sources": (trace or {}).get("sources") or [],
            "retrieved_chunks": chunks,
        }
    return {
        "retrieval_attempted": attempted,
        "retrieval_with_sources": with_sources,
        "retrieval_source_rate": round(with_sources / attempted, 4) if attempted else 0.0,
        "average_sources_when_attempted": round(sum(source_counts) / attempted, 2) if attempted else 0.0,
        "average_retrieval_score": round(sum(score_values) / len(score_values), 4) if score_values else 0.0,
        "rag_pass_rate": round(rag_passed / rag_evaluated, 4) if rag_evaluated else 0.0,
        "rag_passed": rag_passed,
        "rag_evaluated": rag_evaluated,
        "details": details,
    }


def aggregate_row(profile: Profile, report: EvalReport, baseline: EvalReport) -> dict[str, Any]:
    traces = _trace_metrics(report)
    summary = report.summary
    return {
        "profile": profile.name,
        "changed_variable": profile.changed_variable,
        "chunk_size": profile.chunk_size,
        "overlap": profile.overlap,
        "top_k": profile.top_k,
        "threshold": profile.threshold,
        "passed": summary.passed,
        "failed": summary.failed,
        "errors": summary.errors,
        "manual_review": summary.manual_review,
        "pass_rate": summary.pass_rate,
        "pass_rate_delta": round(summary.pass_rate - baseline.summary.pass_rate, 4),
        "rag_pass_rate": traces["rag_pass_rate"],
        "rag_passed": traces["rag_passed"],
        "rag_evaluated": traces["rag_evaluated"],
        "average_cost_usd": summary.average_cost_usd,
        "average_model_call_cost_usd": summary.average_model_call_cost_usd,
        "total_cost_usd": summary.total_cost_usd,
        "average_latency_ms": summary.average_latency_ms,
        "duration_seconds": summary.duration_seconds,
        "model_calls": summary.model_calls,
        "zero_cost_turns": summary.zero_cost_turns,
        "retrieval_attempted": traces["retrieval_attempted"],
        "retrieval_with_sources": traces["retrieval_with_sources"],
        "retrieval_source_rate": traces["retrieval_source_rate"],
        "average_sources_when_attempted": traces["average_sources_when_attempted"],
        "average_retrieval_score": traces["average_retrieval_score"],
        "report_run_id": report.run_id,
        "trace_details": traces["details"],
    }


def save_aggregate(run_dir: Path, rows: list[dict[str, Any]]) -> None:
    serializable = rows
    (run_dir / "h2_07_results.json").write_text(
        json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    flat_rows = [{key: value for key, value in row.items() if key != "trace_details"} for row in rows]
    with (run_dir / "h2_07_results.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(flat_rows[0]))
        writer.writeheader()
        writer.writerows(flat_rows)
    eligible = [row for row in flat_rows if row["errors"] == 0]
    best = sorted(
        eligible,
        key=lambda row: (
            -row["pass_rate"],
            -row["rag_pass_rate"],
            row["average_cost_usd"],
            row["average_latency_ms"],
        ),
    )[0]
    lines = [
        "# H2-07 — kết quả tinh chỉnh RAG",
        "",
        "> Dữ liệu synthetic; khuyến nghị chỉ là tạm thời và phải chạy lại khi có dữ liệu thật.",
        "",
        "| Cấu hình | Biến đổi | Chunk | Overlap | Top-k | Ngưỡng | Đúng | Đúng RAG | Chi phí TB | Độ trễ TB | Lỗi | Review |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in flat_rows:
        lines.append(
            f"| {row['profile']} | {row['changed_variable']} | {row['chunk_size']} | "
            f"{row['overlap']} | {row['top_k']} | {row['threshold']:.2f} | "
            f"{row['pass_rate']:.1%} | {row['rag_pass_rate']:.1%} | "
            f"{row['average_cost_usd']:.8f} | {row['average_latency_ms']:.0f} | "
            f"{row['errors']} | {row['manual_review']} |"
        )
    lines.extend(
        [
            "",
            "## Khuyến nghị tạm thời",
            "",
            f"`{best['profile']}`: đúng {best['pass_rate']:.1%}, đúng nhóm có retrieval "
            f"{best['rag_pass_rate']:.1%}, chi phí trung bình ${best['average_cost_usd']:.8f}/lượt, "
            f"độ trễ trung bình {best['average_latency_ms']:.0f} ms/lượt.",
            "",
            "Không tự động áp dụng production. Chạy lại toàn bộ khi có dữ liệu thật.",
        ]
    )
    (run_dir / "H2-07-khuyen-nghi.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_profiles(
    *,
    run_dir: Path,
    workers: int,
    requests_per_minute: float,
    selected_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    cases_path = OUTPUT_ROOT / "inputs" / "h2_01_60_cases.yaml"
    selected = [profile for profile in PROFILES if not selected_names or profile.name in selected_names]
    if not selected or selected[0].name != "baseline":
        raise RuntimeError("Mọi lần chạy phải gồm baseline trước các profile khác.")
    baseline_report: EvalReport | None = None
    rows: list[dict[str, Any]] = []
    for profile in selected:
        checkpoint = run_dir / "checkpoints" / f"{profile.name}.json"
        if checkpoint.exists():
            report = EvalReport.model_validate_json(checkpoint.read_text(encoding="utf-8"))
            print(f"[eval] dùng checkpoint {profile.name}", flush=True)
        else:
            print(f"[eval] bắt đầu {profile.name}", flush=True)
            baseline_payload = _baseline_payload(baseline_report) if baseline_report else None
            with apply_profile(profile):
                report = run_eval(
                    cases_path,
                    chat_module.chat_for_eval,
                    tenant_id=TENANT_ID,
                    config_version=CONFIG_VERSION,
                    baseline=baseline_payload,
                    workers=workers,
                    requests_per_minute=requests_per_minute,
                    diagnostic_resolver=find_trace,
                    judge_fn=None,
                    experiment_context=profile.context(),
                )
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            checkpoint.write_text(
                json.dumps(report_as_dict(report), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            save_report(report, run_dir / "reports" / profile.name)
            print(
                f"[eval] xong {profile.name}: đúng={report.summary.pass_rate:.1%}, "
                f"cost_usd={report.summary.average_cost_usd:.8f}, latency={report.summary.average_latency_ms:.0f}",
                flush=True,
            )
        if baseline_report is None:
            baseline_report = report
        rows.append(aggregate_row(profile, report, baseline_report))
        save_aggregate(run_dir, rows)
    return rows


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chạy H2-07 với 9 cấu hình RAG.")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--rebuild-indexes", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--profiles", nargs="*")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--requests-per-minute", type=float, default=15.0)
    parser.add_argument("--embedding-provider", choices=("gemini", "openai"), default="openai")
    parser.add_argument("--embedding-model", default="text-embedding-3-small")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    manifest = prepare_inputs()
    print(json.dumps(manifest["variants"], ensure_ascii=False, indent=2), flush=True)
    if args.prepare_only:
        return 0
    build_indexes(
        rebuild=args.rebuild_indexes,
        embedding_provider=args.embedding_provider,
        embedding_model=args.embedding_model,
    )
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = OUTPUT_ROOT / "runs" / run_id
    selected = set(args.profiles or [profile.name for profile in PROFILES])
    unknown = selected - {profile.name for profile in PROFILES}
    if unknown:
        raise SystemExit(f"Profile không tồn tại: {', '.join(sorted(unknown))}")
    selected.add("baseline")
    rows = run_profiles(
        run_dir=run_dir,
        workers=max(1, args.workers),
        requests_per_minute=args.requests_per_minute,
        selected_names=selected,
    )
    print(f"[done] {len(rows)} profile; kết quả: {run_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
