"""Onboard một tenant mới bằng một lệnh (H3-03).

Luồng thực tế dùng lại các script đã ổn định:
config từ template -> crawl/chunk -> embed/index -> smoke RAG 10 câu.
Mỗi bước được checkpoint để lần chạy sau tiếp tục từ bước lỗi, không lặp lại
crawl hoặc embedding đã hoàn thành.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Callable
import unicodedata
from urllib.parse import urlparse

import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai_core.config import ConfigError, load_config, validate_tenant_id


STEPS = ("config", "crawl", "index", "smoke")
INDUSTRY_ALIASES = {
    "construction": "construction",
    "xay_dung": "construction",
    "xaydung": "construction",
    "commerce": "commerce",
    "thuong_mai": "commerce",
    "thuongmai": "commerce",
    "services": "services",
    "service": "services",
    "dich_vu": "services",
    "dichvu": "services",
    # Hai template bổ trợ của H3-02 vẫn được phép dùng khi nghiệp vụ phù hợp.
    "medical": "medical",
    "y_te": "medical",
    "yte": "medical",
    "retail": "retail",
    "ban_le": "retail",
    "banle": "retail",
}

INDUSTRY_QUESTIONS = {
    "construction": [
        "Bên mình đang cung cấp những dịch vụ xây dựng nào?",
        "Quy trình tư vấn và triển khai một công trình gồm những bước nào?",
        "Tôi cần chuẩn bị thông tin gì để được báo giá?",
        "Bên mình có nhận thiết kế theo yêu cầu không?",
        "Chính sách bảo hành công trình như thế nào?",
    ],
    "commerce": [
        "Bên mình đang bán những nhóm sản phẩm nào?",
        "Tôi muốn mua số lượng lớn thì được tư vấn thế nào?",
        "Chính sách giao hàng hiện nay ra sao?",
        "Bên mình có chính sách đổi trả không?",
        "Tôi cần cung cấp gì để kiểm tra đơn hàng?",
    ],
    "services": [
        "Bên mình đang cung cấp những dịch vụ nào?",
        "Quy trình đăng ký dịch vụ gồm những bước nào?",
        "Tôi cần cung cấp thông tin gì để được tư vấn?",
        "Dịch vụ nào phù hợp với doanh nghiệp nhỏ?",
        "Tôi có thể liên hệ tư vấn bằng cách nào?",
    ],
    "medical": [
        "Cơ sở đang cung cấp những dịch vụ khám nào?",
        "Tôi cần chuẩn bị gì trước khi đến khám?",
        "Quy trình đặt lịch khám như thế nào?",
        "Địa chỉ và thời gian làm việc ở đâu?",
        "Tôi có thể liên hệ đặt lịch bằng cách nào?",
    ],
    "retail": [
        "Cửa hàng đang có những nhóm sản phẩm nào?",
        "Chính sách giao hàng như thế nào?",
        "Tôi muốn kiểm tra đơn hàng thì cần thông tin gì?",
        "Cửa hàng có chính sách đổi trả không?",
        "Tôi có thể liên hệ mua hàng bằng cách nào?",
    ],
}


class OnboardingError(RuntimeError):
    """Lỗi đã được ghi checkpoint và có thể tiếp tục bằng --resume."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json_atomic(path: Path, value: object) -> None:
    """Ghi atomically để mất điện giữa lúc ghi không làm hỏng checkpoint."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def write_yaml_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    temporary.replace(path)


def normalize_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise OnboardingError("--url phải là URL tuyệt đối bắt đầu bằng http:// hoặc https://")
    return value.strip().rstrip("/")


def normalize_industry(value: str) -> str:
    # Chấp nhận cả mã kỹ thuật và cách gõ tiếng Việt có dấu/không dấu.
    ascii_value = "".join(
        character
        for character in unicodedata.normalize("NFD", value.casefold().replace("đ", "d"))
        if unicodedata.category(character) != "Mn"
    )
    key = re.sub(r"[^a-z0-9_]+", "_", ascii_value.strip()).strip("_")
    template_id = INDUSTRY_ALIASES.get(key)
    if not template_id:
        allowed = ", ".join(sorted(INDUSTRY_ALIASES))
        raise OnboardingError(f"Mã ngành '{value}' chưa được hỗ trợ. Chọn một trong: {allowed}")
    return template_id


def derive_tenant_id(url: str) -> str:
    """Sinh tenant ID ổn định từ hostname khi người dùng chỉ truyền URL + ngành."""

    hostname = (urlparse(url).hostname or "").casefold().removeprefix("www.")
    candidate = re.sub(r"[^a-z0-9]+", "_", hostname).strip("_")[:64]
    return validate_tenant_id(candidate)


def derive_bot_name(url: str) -> str:
    hostname = (urlparse(url).hostname or "Tenant").removeprefix("www.")
    brand = hostname.split(".")[0].replace("-", " ").replace("_", " ").strip()
    return f"Trợ lý {brand.title()}"


def build_tenant_config(
    *, tenant_id: str, template_id: str, bot_name: str, index_dir: Path
) -> dict:
    """Chỉ sinh phần riêng; phần chung được kế thừa từ template H3-02."""

    relative_index = index_dir.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    return {
        "tenant_id": tenant_id,
        "industry_template": template_id,
        "persona": {"bot_name": bot_name},
        "contact": {"hotline": None, "zalo": None},
        "knowledge": {"local_index_dir": relative_index},
    }


def build_smoke_questions(chunks: list[dict], template_id: str) -> list[str]:
    """Tạo 10 câu tự động: 5 câu ngành + 5 câu từ tiêu đề dữ liệu thật."""

    questions = list(INDUSTRY_QUESTIONS[template_id])
    titles: list[str] = []
    for chunk in chunks:
        title = str(chunk.get("metadata", {}).get("title") or "").strip()
        if title and title.casefold() not in {item.casefold() for item in titles}:
            titles.append(title)
    for title in titles:
        questions.append(f"Anh/chị cho tôi biết thông tin về {title} được không?")
        if len(questions) == 10:
            break
    # Website ít title vẫn phải chạy đủ đúng 10 câu, không cần LLM sinh câu.
    fallbacks = [
        "Nội dung nổi bật nhất trên website là gì?",
        "Khách hàng mới nên bắt đầu tìm hiểu từ đâu?",
        "Có thông tin liên hệ nào trên website không?",
        "Bên mình có giới thiệu quy trình làm việc không?",
        "Tôi muốn được tư vấn thêm thì làm thế nào?",
    ]
    for question in fallbacks:
        if len(questions) == 10:
            break
        questions.append(question)
    return questions[:10]


def initial_state(
    *, url: str, tenant_id: str, template_id: str, bot_name: str | None = None
) -> dict:
    return {
        "schema_version": "h3-03.onboarding-state.v1",
        "url": url,
        "tenant_id": tenant_id,
        "industry_template": template_id,
        "bot_name": bot_name,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "steps": {
            name: {
                "status": "pending",
                "attempts": 0,
                "started_at": None,
                "finished_at": None,
                "elapsed_seconds": None,
                "error": None,
            }
            for name in STEPS
        },
    }


def first_incomplete_step(state: dict) -> str | None:
    for name in STEPS:
        if state["steps"][name]["status"] != "succeeded":
            return name
    return None


def reset_from_step(state: dict, step_name: str) -> None:
    """Cho phép chạy lại một bước và toàn bộ bước phụ thuộc phía sau."""

    start = STEPS.index(step_name)
    for name in STEPS[start:]:
        previous_attempts = int(state["steps"][name].get("attempts", 0))
        state["steps"][name] = {
            "status": "pending",
            "attempts": previous_attempts,
            "started_at": None,
            "finished_at": None,
            "elapsed_seconds": None,
            "error": None,
        }


def configure_logger(log_path: Path, verbose: bool) -> logging.Logger:
    logger = logging.getLogger(f"h3_03.{log_path.parent.name}")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


def run_command(command: list[str], logger: logging.Logger) -> None:
    """Stream stdout/stderr vào log; command không chứa API key."""

    logger.info("Lệnh: %s", subprocess.list2cmdline(command))
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        logger.info("  %s", line.rstrip())
    return_code = process.wait()
    if return_code:
        raise OnboardingError(f"Lệnh thất bại với exit code {return_code}")


def run_checkpointed_step(
    *,
    state: dict,
    state_path: Path,
    step_name: str,
    action: Callable[[], None],
    logger: logging.Logger,
) -> None:
    step = state["steps"][step_name]
    step["status"] = "running"
    step["attempts"] = int(step.get("attempts", 0)) + 1
    step["started_at"] = utc_now()
    step["finished_at"] = None
    step["error"] = None
    state["updated_at"] = utc_now()
    write_json_atomic(state_path, state)
    started = time.perf_counter()
    logger.info("[%s/%s] BẮT ĐẦU %s", STEPS.index(step_name) + 1, len(STEPS), step_name)
    try:
        action()
    except (Exception, KeyboardInterrupt) as exc:
        step["status"] = "failed"
        step["error"] = f"{type(exc).__name__}: {exc}"
        step["finished_at"] = utc_now()
        step["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        state["updated_at"] = utc_now()
        write_json_atomic(state_path, state)
        logger.error("[%s] THẤT BẠI: %s", step_name, step["error"])
        raise OnboardingError(step["error"]) from exc
    step["status"] = "succeeded"
    step["finished_at"] = utc_now()
    step["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    state["updated_at"] = utc_now()
    write_json_atomic(state_path, state)
    logger.info("[%s] HOÀN THÀNH trong %.3fs", step_name, step["elapsed_seconds"])


def write_summary(
    *, state: dict, output_dir: Path, smoke_report_path: Path, started: float
) -> dict:
    smoke: dict = {}
    crawl: dict = {}
    index: dict = {}
    if smoke_report_path.exists():
        smoke = json.loads(smoke_report_path.read_text(encoding="utf-8"))
    crawl_path = output_dir / "crawl_manifest.json"
    index_path = output_dir / "index" / "manifest.json"
    if crawl_path.exists():
        crawl = json.loads(crawl_path.read_text(encoding="utf-8"))
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
    rows = smoke.get("queries", [])
    successful_queries = sum(int(row.get("result_count", 0)) > 0 for row in rows)
    isolation_passed = bool(smoke.get("isolation_probe", {}).get("passed"))
    completed = all(state["steps"][name]["status"] == "succeeded" for name in STEPS)
    total_seconds = round(time.perf_counter() - started, 3)
    summary = {
        "schema_version": "h3-03.onboarding-summary.v1",
        "generated_at": utc_now(),
        "tenant_id": state["tenant_id"],
        "url": state["url"],
        "industry_template": state["industry_template"],
        "status": "succeeded" if completed else "failed",
        "total_elapsed_seconds_this_run": total_seconds,
        "target_seconds": 900,
        "under_15_minutes_this_run": completed and total_seconds < 900,
        "result": {
            "fetched_pages": int(crawl.get("fetched_page_count", 0)),
            "chunks": int(crawl.get("chunk_count", 0)),
            "index_records": int(index.get("record_count", 0)),
            "embedding_provider": index.get("provider"),
            "embedding_model": index.get("model"),
        },
        "smoke": {
            "question_count": len(rows),
            "queries_with_results": successful_queries,
            "isolation_passed": isolation_passed,
            "passed": len(rows) == 10 and successful_queries >= 8 and isolation_passed,
        },
        "steps": state["steps"],
        "artifacts": {
            "tenant_config": f"tenants/{state['tenant_id']}.yaml",
            "chunks": str((output_dir / "chunks.json").relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "crawl_manifest": str(crawl_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "index": str((output_dir / "index").relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "smoke_questions": str((output_dir / "smoke_questions.json").relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "smoke_report": str(smoke_report_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "log": str((output_dir / "onboarding.log").relative_to(PROJECT_ROOT)).replace("\\", "/"),
        },
        "resume_command": (
            f'python scripts/onboard_tenant.py --url "{state["url"]}" '
            f'--industry {state["industry_template"]} --tenant-id {state["tenant_id"]} --resume'
        ),
    }
    write_json_atomic(output_dir / "summary.json", summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="H3-03: onboard tenant bằng URL + mã ngành, có checkpoint/resume"
    )
    parser.add_argument("--url", required=True, help="Website public cần onboard")
    parser.add_argument("--industry", required=True, help="Mã ngành/template H3-02")
    parser.add_argument("--tenant-id", help="Mặc định tự sinh từ hostname")
    parser.add_argument("--bot-name", help="Mặc định tự sinh từ hostname")
    parser.add_argument("--resume", action="store_true", help="Tiếp tục từ bước lỗi/chưa xong")
    parser.add_argument("--from-step", choices=STEPS, help="Chạy lại từ bước chỉ định")
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--min-chunks", type=int, default=10)
    parser.add_argument("--chunk-size", type=int, default=900)
    parser.add_argument("--chunk-overlap", type=int, default=120)
    parser.add_argument("--min-chars", type=int, default=160)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--delay-seconds", type=float, default=0.10)
    parser.add_argument("--smoke-threshold", type=float, default=0.30)
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_started = time.perf_counter()
    try:
        url = normalize_url(args.url)
        template_id = normalize_industry(args.industry)
        tenant_id = validate_tenant_id(args.tenant_id) if args.tenant_id else derive_tenant_id(url)
    except (ConfigError, OnboardingError) as exc:
        print(f"❌ Input không hợp lệ: {exc}")
        return 2

    output_dir = PROJECT_ROOT / "outputs" / "h3_03" / tenant_id
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = configure_logger(output_dir / "onboarding.log", args.verbose)
    state_path = output_dir / "onboarding_state.json"
    chunks_path = output_dir / "chunks.json"
    crawl_manifest_path = output_dir / "crawl_manifest.json"
    index_dir = output_dir / "index"
    smoke_questions_path = output_dir / "smoke_questions.json"
    smoke_report_path = output_dir / "smoke_report.json"
    tenant_config_path = PROJECT_ROOT / "tenants" / f"{tenant_id}.yaml"

    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        identity = (state.get("url"), state.get("tenant_id"), state.get("industry_template"))
        if identity != (url, tenant_id, template_id):
            logger.error("Checkpoint hiện có thuộc URL/tenant/ngành khác; không ghi đè.")
            return 2
        if not args.resume and not args.from_step:
            logger.error("Tenant đã có checkpoint. Dùng --resume hoặc --from-step <step>.")
            return 2
    else:
        state = initial_state(
            url=url,
            tenant_id=tenant_id,
            template_id=template_id,
            bot_name=args.bot_name or derive_bot_name(url),
        )
        write_json_atomic(state_path, state)

    if args.from_step:
        reset_from_step(state, args.from_step)
        write_json_atomic(state_path, state)

    start_step = first_incomplete_step(state)
    if start_step is None:
        # Không ghi đè thời gian acceptance gốc bằng một lần --resume mất vài ms.
        summary_path = output_dir / "summary.json"
        summary = (
            json.loads(summary_path.read_text(encoding="utf-8"))
            if summary_path.exists()
            else write_summary(
                state=state,
                output_dir=output_dir,
                smoke_report_path=smoke_report_path,
                started=run_started,
            )
        )
        logger.info("Tenant đã hoàn tất trước đó: %s", summary["status"])
        return 0 if summary.get("status") == "succeeded" else 1

    start_index = STEPS.index(start_step)
    logger.info(
        "Onboard tenant=%s | ngành=%s | bắt đầu từ bước=%s",
        tenant_id,
        template_id,
        start_step,
    )

    def config_action() -> None:
        config_data = build_tenant_config(
            tenant_id=tenant_id,
            template_id=template_id,
            bot_name=state.get("bot_name") or args.bot_name or derive_bot_name(url),
            index_dir=index_dir,
        )
        if tenant_config_path.exists():
            existing = yaml.safe_load(tenant_config_path.read_text(encoding="utf-8"))
            if existing != config_data:
                raise OnboardingError(
                    f"Config {tenant_config_path} đã tồn tại và khác nội dung tự sinh; không ghi đè."
                )
        else:
            write_yaml_atomic(tenant_config_path, config_data)
        loaded = load_config(tenant_id)
        if loaded.industry_template != template_id:
            raise OnboardingError("Config load được nhưng industry_template không khớp.")

    def crawl_action() -> None:
        command = [
            sys.executable,
            "crawl_chunks.py",
            "--base-url", url,
            "--tenant-id", tenant_id,
            "--output", str(chunks_path.relative_to(PROJECT_ROOT)),
            "--manifest", str(crawl_manifest_path.relative_to(PROJECT_ROOT)),
            "--max-pages", str(max(1, args.max_pages)),
            "--min-chunks", str(max(1, args.min_chunks)),
            "--chunk-size", str(max(200, args.chunk_size)),
            "--chunk-overlap", str(max(0, args.chunk_overlap)),
            "--min-chars", str(max(40, args.min_chars)),
            "--delay-seconds", str(max(0.0, args.delay_seconds)),
            "--timeout", str(max(1.0, args.timeout)),
        ]
        run_command(command, logger)

    def index_action() -> None:
        run_command(
            [
                sys.executable,
                "index_chunks.py",
                "--tenant-id", tenant_id,
                "--input", str(chunks_path.relative_to(PROJECT_ROOT)),
                "--out-dir", str(index_dir.relative_to(PROJECT_ROOT)),
            ],
            logger,
        )

    def smoke_action() -> None:
        chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
        questions = build_smoke_questions(chunks, template_id)
        write_json_atomic(smoke_questions_path, questions)
        command = [
            sys.executable,
            "eval/smoke_tenant_index.py",
            "--index-dir", str(index_dir.relative_to(PROJECT_ROOT)),
            "--tenant-id", tenant_id,
            "--wrong-tenant", "mima_internal" if tenant_id != "mima_internal" else "phongkham_hyhy",
            "--output", str(smoke_report_path.relative_to(PROJECT_ROOT)),
            "--threshold", str(max(0.0, min(1.0, args.smoke_threshold))),
            "--top-k", "3",
        ]
        for question in questions:
            command.extend(["--query", question])
        run_command(command, logger)
        report = json.loads(smoke_report_path.read_text(encoding="utf-8"))
        if len(report.get("queries", [])) != 10:
            raise OnboardingError("Smoke report không đủ đúng 10 câu.")
        if not report.get("isolation_probe", {}).get("passed"):
            raise OnboardingError("Smoke test phát hiện nguy cơ rò dữ liệu chéo tenant.")

    actions = {
        "config": config_action,
        "crawl": crawl_action,
        "index": index_action,
        "smoke": smoke_action,
    }

    try:
        for step_name in STEPS[start_index:]:
            if state["steps"][step_name]["status"] == "succeeded":
                logger.info("[%s] bỏ qua vì checkpoint đã thành công", step_name)
                continue
            run_checkpointed_step(
                state=state,
                state_path=state_path,
                step_name=step_name,
                action=actions[step_name],
                logger=logger,
            )
    except (OnboardingError, KeyboardInterrupt) as exc:
        summary = write_summary(
            state=state,
            output_dir=output_dir,
            smoke_report_path=smoke_report_path,
            started=run_started,
        )
        logger.error("Pipeline dừng có kiểm soát. Báo cáo: %s", output_dir / "summary.json")
        logger.error("Chạy lại: %s", summary["resume_command"])
        return 1

    summary = write_summary(
        state=state,
        output_dir=output_dir,
        smoke_report_path=smoke_report_path,
        started=run_started,
    )
    logger.info(
        "HOÀN TẤT tenant=%s trong %.3fs | smoke=%s/10 | isolation=%s",
        tenant_id,
        summary["total_elapsed_seconds_this_run"],
        summary["smoke"]["queries_with_results"],
        "PASS" if summary["smoke"]["isolation_passed"] else "FAIL",
    )
    return 0 if summary["smoke"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
