"""HOA-17 one-variable-at-a-time evaluation runner.

Run all profiles with::

    python -m eval.tune

Every profile changes exactly one variable from the baseline. The runner stores
complete HOA-13 reports plus aggregate JSON/CSV/Markdown tables under
``outputs/hoa17/<run-id>/``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import sys
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

import ai_core.chat as chat_module
from ai_core.evaluator import EvalReport, run_eval, save_report
from ai_core.trace import find_trace
from eval.run import DEFAULT_CASES, _make_llm_judge


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_ROOT = ROOT / "outputs" / "hoa17"
CHUNK_INPUT = ROOT / "seed_chunks.json"
CHUNK_VARIANT_SIZE = 350
PROMPT_SUFFIX = """[THỬ NGHIỆM HOA-17 — TRẢ LỜI RÕ RÀNG]
- Trả lời trực tiếp, chỉ dùng dữ liệu RAG/tool của lượt hiện tại.
- Khi không đủ dữ liệu, nói rõ điều chưa xác minh và đề nghị chuyên viên tư vấn.
- Với yêu cầu vi phạm an toàn hoặc riêng tư, phải từ chối rõ hành vi đó và nêu lý do ngắn; không chỉ trả một câu chuyển tiếp chung chung.
- Không thêm tuyên bố ngoài bằng chứng chỉ để kéo dài câu trả lời."""


@dataclass(frozen=True)
class Profile:
    name: str
    changed_variable: str
    value: str
    retrieval_k: int | None = None
    threshold: float | None = None
    index_dir: Path | None = None
    prompt_suffix: str | None = None
    primary_model: str | None = None
    fallback_model: str | None = None

    def context(self) -> dict[str, Any]:
        return {
            "profile": self.name,
            "changed_variable": self.changed_variable,
            "value": self.value,
            "retrieval_k": self.retrieval_k,
            "threshold": self.threshold,
            "index_dir": str(self.index_dir) if self.index_dir else None,
            "prompt_suffix_sha256": (
                hashlib.sha256(self.prompt_suffix.encode("utf-8")).hexdigest()
                if self.prompt_suffix else None
            ),
            "primary_model": self.primary_model,
            "fallback_model": self.fallback_model,
        }


def _split_content(text: str, max_chars: int, overlap_chars: int = 60) -> list[str]:
    """Split at sentence/space boundaries with a small deterministic overlap."""

    clean = " ".join(text.split())
    if len(clean) <= max_chars:
        return [clean]
    pieces: list[str] = []
    start = 0
    while start < len(clean):
        hard_end = min(len(clean), start + max_chars)
        end = hard_end
        if hard_end < len(clean):
            candidates = [
                clean.rfind(marker, start + max_chars // 2, hard_end)
                for marker in (". ", "; ", ", ", " ")
            ]
            boundary = max(candidates)
            if boundary > start:
                end = boundary + (1 if clean[boundary] != " " else 0)
        piece = clean[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(clean):
            break
        start = max(start + 1, end - overlap_chars)
    return pieces


def prepare_chunk_variant(source: Path, destination: Path, max_chars: int) -> int:
    chunks = json.loads(source.read_text(encoding="utf-8"))
    output: list[dict[str, Any]] = []
    for chunk in chunks:
        pieces = _split_content(str(chunk["content"]), max_chars)
        if len(pieces) == 1:
            output.append(chunk)
            continue
        for index, piece in enumerate(pieces, start=1):
            item = dict(chunk)
            item["chunk_id"] = f"{chunk['chunk_id']}--c{max_chars}-{index:02d}"
            item["content"] = piece
            item["metadata"] = dict(chunk["metadata"])
            output.append(item)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return len(output)


def ensure_chunk_index(
    *,
    variant_input: Path,
    index_dir: Path,
    rebuild: bool,
) -> None:
    prepare_chunk_variant(CHUNK_INPUT, variant_input, CHUNK_VARIANT_SIZE)
    manifest = index_dir / "manifest.json"
    if manifest.exists() and not rebuild:
        return
    index_dir.mkdir(parents=True, exist_ok=True)
    base_cache = ROOT / "index" / "embedding_cache.json"
    variant_cache = index_dir / "embedding_cache.json"
    if base_cache.exists() and not variant_cache.exists():
        shutil.copy2(base_cache, variant_cache)
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "index_chunks.py"),
            "--tenant-id", "mima_internal",
            "--input", str(variant_input),
            "--out-dir", str(index_dir),
        ],
        cwd=ROOT,
        check=True,
    )


@contextmanager
def apply_profile(profile: Profile) -> Iterator[None]:
    original_retrieve = chat_module.retrieve
    original_load_config = chat_module.load_config
    original_build_prompt = chat_module.build_system_prompt

    def tuned_retrieve(query: str, tenant_id: str, k: int = 5, **kwargs: Any):
        return original_retrieve(
            query,
            tenant_id,
            k=profile.retrieval_k or k,
            threshold=profile.threshold,
            index_dir=profile.index_dir or kwargs.pop("index_dir", chat_module.PROJECT_ROOT / "index"),
            **kwargs,
        )

    def tuned_load_config(tenant_id: str, config_version: int | None = None):
        config = original_load_config(tenant_id, config_version)
        if not profile.primary_model:
            return config
        model_policy = config.model_policy.model_copy(update={
            "primary": profile.primary_model,
            "fallback": profile.fallback_model or config.model_fallback,
        })
        return config.model_copy(update={"model_policy": model_policy})

    def tuned_build_prompt(config):
        prompt = original_build_prompt(config)
        return f"{prompt}\n\n{profile.prompt_suffix}" if profile.prompt_suffix else prompt

    with ExitStack() as stack:
        stack.enter_context(patch.object(chat_module, "retrieve", tuned_retrieve))
        stack.enter_context(patch.object(chat_module, "load_config", tuned_load_config))
        stack.enter_context(patch.object(chat_module, "build_system_prompt", tuned_build_prompt))
        yield


def _baseline_payload(report: EvalReport) -> dict[str, Any]:
    return {
        "run_id": report.run_id,
        "fingerprint": report.fingerprint,
        "case_fingerprint": report.case_fingerprint,
        **report.summary.model_dump(mode="json"),
    }


def _aggregate_row(profile: Profile, report: EvalReport, baseline: EvalReport) -> dict[str, Any]:
    summary = report.summary
    return {
        "profile": profile.name,
        "changed_variable": profile.changed_variable,
        "value": profile.value,
        "passed": summary.passed,
        "failed": summary.failed,
        "errors": summary.errors,
        "manual_review": summary.manual_review,
        "pass_rate": summary.pass_rate,
        "pass_rate_delta": round(summary.pass_rate - baseline.summary.pass_rate, 4),
        "average_cost_usd": summary.average_cost_usd,
        "average_cost_delta_vnd": round(
            summary.average_cost_usd - baseline.summary.average_cost_usd, 12
        ),
        "average_model_call_cost_usd": summary.average_model_call_cost_usd,
        "average_latency_ms": summary.average_latency_ms,
        "average_latency_delta_ms": round(
            summary.average_latency_ms - baseline.summary.average_latency_ms, 2
        ),
        "duration_seconds": summary.duration_seconds,
        "within_5_minutes": summary.duration_seconds < 300,
        "report_run_id": report.run_id,
    }


def save_aggregate(output_dir: Path, rows: list[dict[str, Any]]) -> tuple[Path, Path, Path]:
    json_path = output_dir / "hoa17_tuning_results.json"
    csv_path = output_dir / "hoa17_tuning_results.csv"
    md_path = output_dir / "hoa17_tuning_results.md"
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    eligible = [row for row in rows if row["errors"] == 0]
    best = sorted(
        eligible,
        key=lambda row: (-row["pass_rate"], row["average_cost_usd"], row["average_latency_ms"]),
    )[0] if eligible else rows[0]
    lines = [
        "# HOA-17 — kết quả tinh chỉnh",
        "",
        "Mỗi dòng chỉ thay đúng một biến so với `baseline`.",
        "",
        "| Cấu hình | Biến thay đổi | Giá trị | Đúng | Δ đúng | Chi phí TB ước tính (USD) | Độ trễ TB (ms) | <5 phút | Lỗi |",
        "|---|---|---|---:|---:|---:|---:|:---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['profile']} | {row['changed_variable']} | {row['value']} | "
            f"{row['pass_rate']:.1%} | {row['pass_rate_delta']:+.1%} | "
            f"{row['average_cost_usd']:.8f} | {row['average_latency_ms']:.2f} | "
            f"{'Có' if row['within_5_minutes'] else 'Không'} | {row['errors']} |"
        )
    lines.extend([
        "",
        "## Cấu hình được chọn",
        "",
        f"`{best['profile']}` — tỷ lệ đúng {best['pass_rate']:.1%}, "
        f"chi phí trung bình ${best['average_cost_usd']:.8f}/lượt, "
        f"độ trễ trung bình {best['average_latency_ms']:.2f} ms/lượt.",
        "",
        "Tiêu chí chọn: không có lỗi hạ tầng, ưu tiên tỷ lệ đúng; nếu bằng nhau thì "
        "ưu tiên chi phí rồi độ trễ thấp hơn.",
    ])
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, csv_path, md_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chạy ma trận tinh chỉnh HOA-17.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--profiles", nargs="*", help="Tên profile cần chạy; mặc định chạy tất cả.")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--requests-per-minute", type=float, default=15.0)
    parser.add_argument("--rebuild-chunk-index", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    output_dir = args.output_root / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    variant_input = output_dir / "inputs" / f"chunks_{CHUNK_VARIANT_SIZE}.json"
    variant_index = args.output_root / "indexes" / f"chunks_{CHUNK_VARIANT_SIZE}"
    profiles = [
        Profile("baseline", "none", "production configuration"),
        Profile(
            "chunk_350", "chunk_size", f"max {CHUNK_VARIANT_SIZE} chars, overlap 60",
            index_dir=variant_index,
        ),
        Profile("top_k_3", "top_k", "3", retrieval_k=3),
        Profile("threshold_070", "min_score", "0.70", threshold=0.70),
        Profile("prompt_clear_refusal", "prompt", "explicit refusal experiment", prompt_suffix=PROMPT_SUFFIX),
        Profile(
            "model_gpt_primary", "primary_model", "gpt-5.6-luna",
            primary_model="gpt-5.6-luna", fallback_model="gemini-3.5-flash-lite",
        ),
    ]
    selected = set(args.profiles or [profile.name for profile in profiles])
    unknown = selected - {profile.name for profile in profiles}
    if unknown:
        raise SystemExit(f"Profile không tồn tại: {', '.join(sorted(unknown))}")
    selected.add("baseline")
    profiles = [profile for profile in profiles if profile.name in selected]
    if any(profile.name == "chunk_350" for profile in profiles):
        ensure_chunk_index(
            variant_input=variant_input,
            index_dir=variant_index,
            rebuild=args.rebuild_chunk_index,
        )
    judge = _make_llm_judge("mima_internal", 1)
    reports: list[tuple[Profile, EvalReport]] = []
    baseline_report: EvalReport | None = None
    for profile in profiles:
        print(f"\n=== HOA-17 profile: {profile.name} ({profile.changed_variable}={profile.value}) ===")
        baseline_payload = _baseline_payload(baseline_report) if baseline_report else None
        with apply_profile(profile):
            report = run_eval(
                args.cases,
                chat_module.chat_for_eval,
                tenant_id="mima_internal",
                config_version=1,
                baseline=baseline_payload,
                workers=args.workers,
                requests_per_minute=args.requests_per_minute,
                diagnostic_resolver=find_trace,
                judge_fn=judge,
                experiment_context=profile.context(),
            )
        save_report(report, output_dir / "reports" / profile.name)
        reports.append((profile, report))
        if profile.name == "baseline" or baseline_report is None:
            baseline_report = report
    assert baseline_report is not None
    rows = [_aggregate_row(profile, report, baseline_report) for profile, report in reports]
    json_path, csv_path, md_path = save_aggregate(output_dir, rows)
    print(f"\nBảng JSON: {json_path}")
    print(f"Bảng CSV: {csv_path}")
    print(f"Báo cáo Markdown: {md_path}")
    has_errors = any(report.summary.errors for _, report in reports)
    over_budget = any(report.summary.duration_seconds >= 300 for _, report in reports)
    return 2 if has_errors or over_budget else 0


if __name__ == "__main__":
    raise SystemExit(main())
