"""H4-02: gom cụm knowledge gap và xuất top 10 chủ đề thiếu theo tenant."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_core.gap_clustering import (  # noqa: E402
    DEFAULT_SIMILARITY_THRESHOLD,
    GapClusteringError,
    build_cluster_report,
    cluster_tenant_gaps,
    llm_cluster_namer,
    partition_actionable_gaps,
)
from storage import Storage, StorageError, build_storage  # noqa: E402
from storage.factory import DEFAULT_SQLITE_PATH, STORAGE_BACKEND_ENV  # noqa: E402


DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "h4_02"


def _load_review_overrides(path: Path | None) -> dict[str, list[dict[str, Any]]]:
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GapClusteringError(f"Không đọc được file review H4-02 {path}: {exc}") from exc
    if payload.get("schema_version") != "h4-02.manual-review.v1":
        raise GapClusteringError("File review H4-02 sai schema_version")
    tenants = payload.get("tenants")
    if not isinstance(tenants, dict):
        raise GapClusteringError("File review H4-02 phải có object tenants")
    result: dict[str, list[dict[str, Any]]] = {}
    for tenant_id, tenant in tenants.items():
        groups = tenant.get("groups") if isinstance(tenant, dict) else None
        if not isinstance(groups, list):
            raise GapClusteringError(f"Review của tenant {tenant_id!r} phải có danh sách groups")
        result[str(tenant_id)] = [dict(group) for group in groups]
    return result


def _configured_tenant_ids() -> list[str]:
    pattern = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
    return sorted(
        path.stem
        for path in (ROOT / "tenants").glob("*.yaml")
        if pattern.fullmatch(path.stem)
    )


def _markdown(report: dict[str, Any], warnings: list[str]) -> str:
    lines = [
        "# H4-02 — Chủ đề còn thiếu theo tenant",
        "",
        f"- Ngưỡng embedding similarity: `{report['similarity_threshold']:.2f}`",
        f"- Nguồn: `{report['source']}`",
        f"- Nhãn dữ liệu: `{report.get('source_data_label', 'PRODUCTION_OR_UNSPECIFIED')}`",
        f"- Sinh lúc: `{report['generated_at']}`",
    ]
    if warnings:
        lines.extend(["", "## Cảnh báo", "", *[f"- {item}" for item in warnings]])
    for tenant_id, tenant in report["tenants"].items():
        lines.extend(
            [
                "",
                f"## {tenant_id}",
                "",
                f"Gap nguồn: **{tenant['raw_gap_count']}** · Gap dùng để gom cụm: "
                f"**{tenant['actionable_gap_count']}** · Đã loại: **{tenant['excluded_gap_count']}**",
                f"Số cụm: **{tenant['cluster_count']}** · Đã duyệt: "
                f"**{tenant['approved_cluster_count']}** · Chờ duyệt: **{tenant['pending_review_count']}**",
                "",
                "| Hạng | Chủ đề thiếu | Tần suất | Câu khác nhau | Độ khớp thấp nhất | Ví dụ | Review |",
                "|---:|---|---:|---:|---:|---|---|",
            ]
        )
        for cluster in tenant["top_missing_topics"]:
            examples = "<br>".join(str(item).replace("|", "\\|") for item in cluster["examples"][:3])
            lines.append(
                f"| {cluster['rank']} | {cluster['name'].replace('|', '\\|')} | "
                f"{cluster['frequency']} | {cluster['unique_question_count']} | "
                f"{cluster['minimum_similarity']:.3f} | {examples} | "
                f"{cluster['review_status']} |"
            )
        if not tenant["top_missing_topics"]:
            lines.append("| — | Chưa có knowledge gap | 0 | 0 | — | — | — |")
    lines.extend(
        [
            "",
            "## Quy tắc review thủ công",
            "",
            "Mở `top10_manual_review.csv` để kiểm tra từng cụm top 10. Nếu cần tách/đổi tên, "
            "ghi quyết định đã duyệt vào file JSON theo schema `h4-02.manual-review.v1`, rồi chạy lại "
            "với `--review-overrides`. CSV là bản kiểm tra, không phải nguồn quyết định. Cụm có câu "
            "khác chủ đề phải được tách trước khi dùng cho H4-03/H4-10.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_review_csv(path: Path, report: dict[str, Any]) -> None:
    fields = [
        "tenant_id",
        "rank",
        "cluster_id",
        "cluster_name",
        "frequency",
        "unique_question_count",
        "minimum_similarity",
        "representative_question",
        "examples",
        "review_status",
        "review_notes",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for tenant_id, tenant in report["tenants"].items():
            for cluster in tenant["top_missing_topics"]:
                writer.writerow(
                    {
                        "tenant_id": tenant_id,
                        "rank": cluster["rank"],
                        "cluster_id": cluster["cluster_id"],
                        "cluster_name": cluster["name"],
                        "frequency": cluster["frequency"],
                        "unique_question_count": cluster["unique_question_count"],
                        "minimum_similarity": cluster["minimum_similarity"],
                        "representative_question": cluster["representative_question"],
                        "examples": " || ".join(cluster["examples"]),
                        "review_status": cluster["review_status"],
                        "review_notes": cluster.get("review_notes", ""),
                    }
                )


def run(
    *,
    database: Path | None,
    tenant_ids: list[str],
    output_dir: Path,
    threshold: float,
    use_llm: bool,
    storage_backend: str = "sqlite",
    storage: Storage | None = None,
    embed_fn=None,
    manual_review_groups: dict[str, list[dict[str, Any]]] | None = None,
    review_source: str | None = None,
    source_data_label: str | None = None,
) -> dict[str, Any]:
    warnings: list[str] = []

    def safe_namer(tenant_id: str, clusters):
        try:
            return llm_cluster_namer(tenant_id, clusters)
        except Exception as exc:
            warnings.append(
                f"{tenant_id}: LLM đặt tên không khả dụng ({exc}); đã dùng tên từ câu đại diện."
            )
            return {}

    if storage is not None and database is not None:
        raise GapClusteringError("Chỉ truyền storage hoặc database, không truyền đồng thời")
    if storage_backend != "sqlite" and database is not None:
        raise GapClusteringError("database path chỉ áp dụng cho backend sqlite")
    active_storage = storage or build_storage(
        backend=storage_backend,
        sqlite_path=database,
    )
    owns_storage = storage is None
    tenant_clusters: dict[str, list[dict[str, Any]]] = {}
    tenant_stats: dict[str, dict[str, Any]] = {}
    review_groups = manual_review_groups or {}
    try:
        for tenant_id in tenant_ids:
            raw_gaps = active_storage.list_knowledge_gaps(tenant_id)
            gaps, excluded_reasons = partition_actionable_gaps(raw_gaps)
            cluster_kwargs = {}
            if embed_fn is not None:
                cluster_kwargs["embed_fn"] = embed_fn
            tenant_clusters[tenant_id] = cluster_tenant_gaps(
                gaps,
                tenant_id,
                similarity_threshold=threshold,
                namer=safe_namer if use_llm and gaps else None,
                manual_groups=review_groups.get(tenant_id, []),
                **cluster_kwargs,
            )
            clusters = tenant_clusters[tenant_id]
            tenant_stats[tenant_id] = {
                "raw_gap_count": len(raw_gaps),
                "actionable_gap_count": len(gaps),
                "excluded_gap_count": len(raw_gaps) - len(gaps),
                "excluded_reason_counts": excluded_reasons,
                "approved_cluster_count": sum(
                    item["review_status"] == "approved" for item in clusters
                ),
                "pending_review_count": sum(
                    item["review_status"] == "needs_manual_review" for item in clusters
                ),
            }
    finally:
        if owns_storage:
            active_storage.close()

    source = (
        str(database.resolve())
        if database is not None
        else f"storage-backend:{storage_backend}"
    )

    report = build_cluster_report(
        tenant_clusters,
        similarity_threshold=threshold,
        source=source,
    )
    for tenant_id, stats in tenant_stats.items():
        report["tenants"][tenant_id].update(stats)
    report["manual_review_source"] = review_source
    if source_data_label:
        report["source_data_label"] = source_data_label
    report["warnings"] = warnings
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "knowledge_gap_clusters.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "top_missing_topics.md").write_text(
        _markdown(report, warnings),
        encoding="utf-8",
    )
    _write_review_csv(output_dir / "top10_manual_review.csv", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--storage-backend",
        choices=("sqlite", "postgres"),
        default=os.getenv(STORAGE_BACKEND_ENV, "sqlite"),
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help="SQLite source; nếu bỏ trống dùng AI_API_SQLITE_PATH hoặc database mặc định.",
    )
    parser.add_argument("--tenant-id", action="append", dest="tenant_ids")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--threshold", type=float, default=DEFAULT_SIMILARITY_THRESHOLD)
    parser.add_argument(
        "--review-overrides",
        type=Path,
        default=None,
        help="JSON chứa quyết định tách/đặt tên cụm đã duyệt thủ công.",
    )
    parser.add_argument(
        "--source-data-label",
        default=None,
        help="Nhãn phân biệt dữ liệu thật với dữ liệu test/synthetic trong báo cáo.",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Dùng tên từ câu đại diện; mặc định thử LLM rồi fallback an toàn nếu lỗi.",
    )
    args = parser.parse_args()
    database = args.database
    if args.storage_backend == "sqlite" and database is None:
        configured = os.getenv("AI_API_SQLITE_PATH")
        database = Path(configured) if configured else DEFAULT_SQLITE_PATH
    tenant_ids = sorted(set(args.tenant_ids or _configured_tenant_ids()))
    if not tenant_ids:
        parser.error("Không tìm thấy tenant; truyền ít nhất một --tenant-id")
    try:
        manual_review_groups = _load_review_overrides(args.review_overrides)
        report = run(
            database=database,
            tenant_ids=tenant_ids,
            output_dir=args.output_dir,
            threshold=args.threshold,
            use_llm=not args.no_llm,
            storage_backend=args.storage_backend,
            manual_review_groups=manual_review_groups,
            review_source=str(args.review_overrides.resolve()) if args.review_overrides else None,
            source_data_label=args.source_data_label,
        )
    except (GapClusteringError, StorageError) as exc:
        parser.error(str(exc))
    for tenant_id, tenant in report["tenants"].items():
        print(
            f"{tenant_id}: {tenant['total_gaps']} gap -> "
            f"{tenant['cluster_count']} cụm, top {len(tenant['top_missing_topics'])}"
        )
    print(f"Báo cáo: {args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
