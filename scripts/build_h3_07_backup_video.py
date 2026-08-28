"""Tạo video dự phòng H3-07 từ response của một rehearsal live đã lưu.

Video là playback offline có trace/source/tool/guardrail thật từ báo cáo rehearsal,
không giả làm API đang chạy live và không phụ thuộc mạng trong buổi trình bày.
"""

from __future__ import annotations

from html import escape
import json
import subprocess
from pathlib import Path

import imageio_ffmpeg


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "outputs" / "h3_07"
REPORT_PATH = OUTPUT_DIR / "live-rehearsal-report.json"
OUTPUT_PATH = OUTPUT_DIR / "H3-07-backup-demo.mp4"
FRAME_DIR = ROOT / "tmp" / "slides" / "h3-07-demo" / "recording-frames"
CONCAT_PATH = FRAME_DIR / "concat.txt"
EDGE_CANDIDATES = (
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
)


def _find_edge() -> Path:
    for path in EDGE_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError("Không tìm thấy Microsoft Edge để render bản ghi dự phòng.")


def _compact_reply(reply: str, *, pricing: bool = False) -> str:
    """Giữ nguyên các dòng bằng chứng quan trọng, tránh nhồi toàn bộ JSON lên video."""

    lines = [line.strip(" -*") for line in reply.splitlines() if line.strip()]
    if pricing:
        priced = [line for line in lines if "Gói Website" in line or "Gói thiết kế" in line]
        lines = priced[:6] or lines[:8]
    else:
        lines = lines[:8]
    return "\n".join(lines)


def _frame_html(
    *,
    step: str,
    title: str,
    question: str,
    result: str,
    evidence: list[str],
    accent: str,
) -> str:
    evidence_html = "".join(f"<li>{escape(item)}</li>" for item in evidence if item)
    return f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><style>
* {{ box-sizing: border-box; }}
body {{ margin:0; width:1280px; height:720px; overflow:hidden; background:#f7f8fc;
       color:#172033; font-family:Arial,Helvetica,sans-serif; }}
.top {{ height:10px; background:{accent}; }}
.wrap {{ padding:38px 58px 30px; }}
.badge {{ color:{accent}; font-weight:700; font-size:16px; letter-spacing:.7px; }}
h1 {{ font-size:38px; margin:12px 0 24px; }}
.grid {{ display:grid; grid-template-columns:38% 62%; gap:24px; }}
.card {{ background:white; border:1px solid #d9deea; border-radius:18px; padding:24px;
         min-height:430px; box-shadow:0 8px 24px rgba(25,36,64,.06); }}
.label {{ color:#63708a; font-size:14px; font-weight:700; text-transform:uppercase; margin-bottom:10px; }}
.question {{ font-size:25px; line-height:1.35; font-weight:700; }}
.result {{ white-space:pre-line; font-size:20px; line-height:1.43; }}
ul {{ margin:22px 0 0; padding-left:22px; color:#40506b; font-size:16px; line-height:1.45; }}
.footer {{ margin-top:20px; font-size:14px; color:#6c7890; }}
.notice {{ display:inline-block; margin-top:18px; padding:8px 12px; border-radius:8px;
           background:#fff3cd; color:#715700; font-size:14px; font-weight:700; }}
</style></head><body><div class="top"></div><div class="wrap">
<div class="badge">{escape(step)}</div><h1>{escape(title)}</h1>
<div class="grid"><div class="card"><div class="label">Câu hỏi đã chạy</div>
<div class="question">{escape(question)}</div><div class="notice">BẢN GHI RUN THỰC TẾ — KHÔNG PHẢI API LIVE</div></div>
<div class="card"><div class="label">Kết quả đã lưu</div><div class="result">{escape(result)}</div>
<ul>{evidence_html}</ul></div></div>
<div class="footer">Nguồn: outputs/h3_07/live-rehearsal-report.json</div>
</div></body></html>"""


def _write_frames() -> list[Path]:
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    if not report.get("passed") or len(report.get("runs", [])) < 2:
        raise RuntimeError("Báo cáo live chưa chứng minh đủ hai lần rehearsal đạt.")
    scenarios = {item["name"]: item for item in report["runs"][0]["scenarios"]}
    required = {"mima_real_question", "clinic_same_system", "domain_tool", "guardrail_trap"}
    if not required.issubset(scenarios):
        raise RuntimeError("Báo cáo live thiếu một phần bắt buộc của kịch bản H3-07.")

    metrics = json.loads(
        (ROOT / "outputs" / "h2_09" / "h2_09_comparison.json").read_text(encoding="utf-8")
    )["routed"]
    mima = scenarios["mima_real_question"]
    clinic = scenarios["clinic_same_system"]
    domain = scenarios["domain_tool"]
    trap = scenarios["guardrail_trap"]

    frames = [
        _frame_html(
            step="01 / 05 · MIMA",
            title="Tenant MIMA trả lời bằng tri thức riêng",
            question=mima["question"],
            result=_compact_reply(mima["response"]["reply"], pricing=True),
            evidence=[
                f"HTTP {mima['status_code']} · need_human={str(mima['response']['need_human']).lower()}",
                f"Nguồn: {mima['response']['sources'][0]['url']}",
                f"Trace: {mima['response']['trace_id']}",
            ],
            accent="#6447ff",
        ),
        _frame_html(
            step="02 / 05 · PHÒNG KHÁM",
            title="Cùng endpoint, đổi tenant và policy",
            question=clinic["question"],
            result=_compact_reply(clinic["response"]["reply"]),
            evidence=[
                f"HTTP {clinic['status_code']} · guardrail.blocked={str(clinic['response']['guardrail']['blocked']).lower()}",
                f"Nguồn: {clinic['response']['sources'][0]['url']}",
                f"Trace: {clinic['response']['trace_id']}",
            ],
            accent="#18a999",
        ),
        _frame_html(
            step="03 / 05 · TOOL",
            title="Tên miền đi qua tool, không chôn trong RAG",
            question=domain["question"],
            result=_compact_reply(domain["response"]["reply"]),
            evidence=[
                f"Tool: {domain['response']['tool_calls'][0]['name']}",
                "Nguồn tool: mock · authoritative=false",
                f"Trace: {domain['response']['trace_id']}",
            ],
            accent="#ff7657",
        ),
        _frame_html(
            step="04 / 05 · GUARDRAIL",
            title="Câu bẫy bị chặn trước khi tới khách",
            question=trap["question"],
            result=_compact_reply(trap["response"]["reply"]),
            evidence=[
                f"blocked={str(trap['response']['guardrail']['blocked']).lower()}",
                f"reason={trap['response']['guardrail']['reason']}",
                f"need_human={str(trap['response']['need_human']).lower()} · Trace: {trap['response']['trace_id']}",
            ],
            accent="#e24a4a",
        ),
        _frame_html(
            step="05 / 05 · SỐ LIỆU",
            title="Bốn con số cùng một nguồn đo",
            question="Hệ thống đã chứng minh được gì sau ba tuần?",
            result=(
                f"5 tenant có config + index\n"
                f"{metrics['pass_rate'] * 100:.2f}% đúng trên eval 60 câu\n"
                f"${metrics['average_cost_usd']:.7f} chi phí model trung bình/lượt\n"
                f"{metrics['average_latency_ms'] / 1000:.2f} giây độ trễ trung bình/lượt"
            ).replace(".", ","),
            evidence=[
                "Run auto-routing H2-09; không ghép số từ các lần chạy khác nhau.",
                "Một AI core phục vụ nhiều tenant; production còn phải thay hạ tầng tạm.",
            ],
            accent="#4f35c9",
        ),
    ]

    FRAME_DIR.mkdir(parents=True, exist_ok=True)
    html_paths: list[Path] = []
    for index, content in enumerate(frames, start=1):
        path = FRAME_DIR / f"frame-{index:02d}.html"
        path.write_text(content, encoding="utf-8")
        html_paths.append(path)
    return html_paths


def _render_frames(html_paths: list[Path]) -> list[Path]:
    edge = _find_edge()
    profile = FRAME_DIR / "edge-profile"
    images: list[Path] = []
    for index, html_path in enumerate(html_paths, start=1):
        image_path = FRAME_DIR / f"frame-{index:02d}.png"
        command = [
            str(edge),
            "--headless",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-first-run",
            f"--user-data-dir={profile}",
            "--window-size=1280,720",
            f"--screenshot={image_path}",
            html_path.resolve().as_uri(),
        ]
        subprocess.run(command, check=True, timeout=30, capture_output=True)
        if not image_path.is_file() or image_path.stat().st_size < 10_000:
            raise RuntimeError(f"Render frame thất bại: {image_path}")
        images.append(image_path)
    return images


def _build_video(images: list[Path]) -> None:
    lines: list[str] = []
    for path in images:
        safe_path = path.resolve().as_posix().replace("'", "'\\''")
        lines.extend((f"file '{safe_path}'", "duration 8"))
    lines.append(f"file '{images[-1].resolve().as_posix()}'")
    CONCAT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(CONCAT_PATH),
        "-vf",
        "format=yuv420p",
        "-r",
        "30",
        "-c:v",
        "libx264",
        "-movflags",
        "+faststart",
        str(OUTPUT_PATH),
    ]
    subprocess.run(command, check=True)


def main() -> int:
    html_paths = _write_frames()
    images = _render_frames(html_paths)
    _build_video(images)
    print(f"Đã tạo bản ghi dự phòng từ rehearsal live: {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
