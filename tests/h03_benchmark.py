"""
HOA-03 — Benchmark thật: gọi 2 LLM (Gemini + OpenAI) và 2 embedding model
(Gemini + OpenAI) trên 15 câu hỏi tiếng Việt (có dấu + không dấu), đo độ trễ,
tính giá thực tế, xuất bảng markdown.

Chạy:
    pip install openai google-genai
    export OPENAI_API_KEY=sk-...
    export GEMINI_API_KEY=...
    python tests/hoa03_benchmark.py

Kết quả: tests/hoa03_benchmark_results.md — copy bảng này vào mục 5 của
docs/HOA-03-so-sanh-model.md sau khi chạy xong.

⚠️ Cần API key thật + có mạng. Không chạy được trong môi trường build (không
có mạng) — đây là lý do mục 5 của docs/HOA-03-so-sanh-model.md còn để trống.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from ai_core.embedder import embed_texts  # noqa: E402
from ai_core.llm_client import generate_reply  # noqa: E402

QUESTIONS_PATH = REPO_ROOT / "tests" / "hoa03_test_questions.json"
RESULTS_PATH = REPO_ROOT / "tests" / "hoa03_benchmark_results.md"

# Giá tại thời điểm tra cứu 10/08/2026 — xem docs/HOA-03-so-sanh-model.md mục 1.
# Đơn vị: USD / 1M token. Cập nhật lại nếu giá đổi trước khi dùng số liệu này.
PRICING = {
    ("gemini", "gemini-2.5-flash"): {"input": 0.30, "output": 2.50},
    ("openai", "gpt-4o-mini"): {"input": 0.15, "output": 0.60},
    ("gemini", "gemini-embedding-001"): {"input": 0.15, "output": 0.0},
    ("openai", "text-embedding-3-small"): {"input": 0.02, "output": 0.0},
}

LLM_CANDIDATES = [
    ("gemini", "gemini-2.5-flash"),
    ("openai", "gpt-4o-mini"),
]

EMBEDDING_CANDIDATES = [
    ("gemini", "gemini-embedding-001"),
    ("openai", "text-embedding-3-small"),
]

SYSTEM_PROMPT_MINIMAL = (
    "Bạn là trợ lý tư vấn của công ty thiết kế website MIMA. Trả lời ngắn gọn, "
    "thân thiện bằng tiếng Việt, 2-4 câu."
)


def cost_usd(provider: str, model: str, tokens_in: int, tokens_out: int) -> float:
    price = PRICING.get((provider, model))
    if not price:
        return 0.0
    return (tokens_in / 1_000_000) * price["input"] + (tokens_out / 1_000_000) * price["output"]


def load_questions() -> list[dict]:
    with QUESTIONS_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def benchmark_llm(provider: str, model: str, questions: list[dict]) -> dict:
    latencies, costs, samples = [], [], []
    errors = 0
    for q in questions:
        for variant in ("with_diacritics", "no_diacritics"):
            text = q[variant]
            try:
                result = generate_reply(
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT_MINIMAL},
                        {"role": "user", "content": text},
                    ],
                    model=model,
                    provider=provider,
                )
            except Exception as e:  # noqa: BLE001 — ghi nhận lỗi, không dừng cả batch
                errors += 1
                print(f"  ❌ [{provider}/{model}] lỗi ở câu {q['id']} ({variant}): {e}")
                continue
            latencies.append(result["latency_ms"])
            costs.append(cost_usd(provider, model, result["tokens_in"], result["tokens_out"]))
            samples.append({"id": q["id"], "variant": variant, "question": text, "reply": result["text"]})
            print(f"  ✓ [{provider}/{model}] {q['id']}/{variant}: {result['latency_ms']}ms")

    return {
        "provider": provider,
        "model": model,
        "n_calls": len(latencies),
        "n_errors": errors,
        "avg_latency_ms": round(statistics.mean(latencies), 1) if latencies else None,
        "p95_latency_ms": round(statistics.quantiles(latencies, n=20)[18], 1) if len(latencies) >= 5 else None,
        "total_cost_usd": round(sum(costs), 6),
        "samples": samples,
    }


def benchmark_embedding(provider: str, model: str, questions: list[dict]) -> dict:
    texts = [q[v] for q in questions for v in ("with_diacritics", "no_diacritics")]
    import time

    start = time.monotonic()
    try:
        vectors = embed_texts(texts, model=model, provider=provider)
    except Exception as e:  # noqa: BLE001
        print(f"  ❌ [{provider}/{model}] lỗi embedding: {e}")
        return {"provider": provider, "model": model, "n_calls": 0, "n_errors": len(texts), "error": str(e)}
    latency_ms = int((time.monotonic() - start) * 1000)

    # Giá embedding chỉ tính theo input token — ước lượng thô bằng số ký tự / 4
    # (không có usage_metadata chuẩn hoá chung giữa 2 provider cho embedding).
    approx_tokens = sum(len(t) for t in texts) // 4
    cost = cost_usd(provider, model, approx_tokens, 0)

    return {
        "provider": provider,
        "model": model,
        "n_calls": len(texts),
        "n_errors": 0,
        "total_latency_ms": latency_ms,
        "dim": len(vectors[0]) if vectors else 0,
        "approx_cost_usd": round(cost, 6),
    }


def render_markdown(llm_results: list[dict], embed_results: list[dict]) -> str:
    lines = ["# Kết quả benchmark HOA-03 (số liệu thật, đo tự động)", ""]
    lines.append("## LLM")
    lines.append("| Provider/Model | Số lần gọi | Lỗi | Độ trễ TB (ms) | p95 (ms) | Tổng giá (USD, 15 câu x2) |")
    lines.append("|---|---|---|---|---|---|")
    for r in llm_results:
        lines.append(
            f"| {r['provider']}/{r['model']} | {r['n_calls']} | {r['n_errors']} | "
            f"{r['avg_latency_ms']} | {r['p95_latency_ms']} | ${r['total_cost_usd']} |"
        )
    lines.append("")
    lines.append("## Embedding")
    lines.append("| Provider/Model | Số câu embed | Tổng độ trễ (ms) | Số chiều vector | Giá ước tính (USD) |")
    lines.append("|---|---|---|---|---|")
    for r in embed_results:
        if r.get("error"):
            lines.append(f"| {r['provider']}/{r['model']} | LỖI: {r['error']} | | | |")
            continue
        lines.append(
            f"| {r['provider']}/{r['model']} | {r['n_calls']} | {r['total_latency_ms']} | "
            f"{r['dim']} | ${r['approx_cost_usd']} |"
        )
    lines.append("")
    lines.append("## Mẫu câu trả lời (để tự chấm chất lượng 1-5, KHÔNG tự động chấm)")
    for r in llm_results:
        lines.append(f"\n### {r['provider']}/{r['model']}")
        for s in r["samples"][:5]:  # in mẫu 5 câu đầu, đủ để đọc nhanh
            lines.append(f"- **{s['id']} ({s['variant']})**: {s['question']}")
            lines.append(f"  → {s['reply']}")
    return "\n".join(lines)


def main() -> None:
    questions = load_questions()
    print(f"Đã nạp {len(questions)} câu hỏi.\n")

    llm_results = []
    for provider, model in LLM_CANDIDATES:
        print(f"--- Benchmark LLM: {provider}/{model} ---")
        llm_results.append(benchmark_llm(provider, model, questions))

    embed_results = []
    for provider, model in EMBEDDING_CANDIDATES:
        print(f"--- Benchmark Embedding: {provider}/{model} ---")
        embed_results.append(benchmark_embedding(provider, model, questions))

    report = render_markdown(llm_results, embed_results)
    RESULTS_PATH.write_text(report, encoding="utf-8")
    print(f"\nĐã ghi kết quả vào {RESULTS_PATH}")
    print("⚠️ Chất lượng trả lời/truy xuất cần TỰ ĐỌC và chấm điểm 1-5 bằng tay — script này chỉ đo được số (độ trễ, giá).")


if __name__ == "__main__":
    main()
