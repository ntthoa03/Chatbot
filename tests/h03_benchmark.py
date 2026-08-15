"""HOA-03: đo chất lượng, retrieval, latency và chi phí bằng API thật.

Pilot chọn model theo quality/cost::
    python tests/h03_benchmark.py --mode pilot

Full benchmark 15 câu x 2 biến thể::
    python tests/h03_benchmark.py --mode full
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
import time
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_core.chat import _generate_gemini, _generate_openai  # noqa: E402
from ai_core.embedder import embed_texts  # noqa: E402

QUESTIONS = ROOT / "tests" / "h03_test_questions.json"
CHUNKS = ROOT / "seed_chunks.json"
OUT = ROOT / "outputs" / "hoa03"
RAW_OUT = OUT / "h03_benchmark_results.json"
REPORT_OUT = ROOT / "tests" / "h03_benchmark_results.md"

# USD/1M token, paid standard tier, checked 2026-08-14.
LLMS = {
    "gpt-4o-mini": ("openai", 0.15, 0.60),
    "gpt-5.6-luna": ("openai", 0.20, 1.20),
    "gemini-3.1-flash-lite": ("gemini", 0.25, 1.50),
    "gemini-3.5-flash-lite": ("gemini", 0.30, 2.50),
}
EMBEDDINGS = {
    "text-embedding-3-small": ("openai", 0.02),
    "text-embedding-3-large": ("openai", 0.13),
    "gemini-embedding-001": ("gemini", 0.15),
    "gemini-embedding-2": ("gemini", 0.20),
}

# Defaults are finalized after the pilot; these favor lowest expected production
# cost and can be overridden from the CLI for repeatable A/B tests.
SELECTED_LLM = {"openai": "gpt-5.6-luna", "gemini": "gemini-3.5-flash-lite"}
SELECTED_EMBEDDING = {"openai": "text-embedding-3-small", "gemini": "gemini-embedding-001"}
PILOT_IDS = {"q01", "q03", "q08", "q11", "q14"}

RELEVANT = {
    "q01": {"33a88e68-cbb4-432b-84e1-0f6e3ffd4422", "d36879c5-aa67-4c22-91ef-e427a608410b", "c854fcde-4bfe-4b84-86c8-017d492e3b36", "0a7189b6-a2dd-45ca-a05a-c9a186b256c2", "63237e6a-88af-4e93-945e-c11187729e6b"},
    "q02": {"29135891-3c8a-4066-8f03-4cefc4a4999e"},
    "q04": {"f63f914d-d4f7-42a0-9615-de6b69957ef5"},
    "q05": {"dcdb3de1-4803-4918-bcd5-1f7593d867ed"},
    "q06": {"67637d1c-4067-4974-95e5-63aa5b8dd342", "dcdb3de1-4803-4918-bcd5-1f7593d867ed"},
    "q07": {"33a88e68-cbb4-432b-84e1-0f6e3ffd4422", "d36879c5-aa67-4c22-91ef-e427a608410b", "c854fcde-4bfe-4b84-86c8-017d492e3b36", "0a7189b6-a2dd-45ca-a05a-c9a186b256c2", "63237e6a-88af-4e93-945e-c11187729e6b"},
    "q08": {"29135891-3c8a-4066-8f03-4cefc4a4999e"},
    "q10": {"33a88e68-cbb4-432b-84e1-0f6e3ffd4422"},
    "q11": {"29135891-3c8a-4066-8f03-4cefc4a4999e", "dd6424c2-0095-4f7d-ad82-005f50dcf0d9", "67637d1c-4067-4974-95e5-63aa5b8dd342"},
    "q12": {"853d815d-1314-43ae-ab0e-1c1c2da88429"},
}

# Public deterministic rubric: four points for required concepts, one for safety.
RUBRICS = {
    "q01": [["2.000.000", "2 trieu", "6.000.000", "6 trieu", "9.000.000", "9 trieu", "12.000.000", "12 trieu", "17.000.000", "17 trieu"], ["goi", "nhu cau", "tuy"]],
    "q02": [["seo"], ["tu khoa", "noi dung", "cau truc", "backlink", "link building", "entity", "schema"]],
    "q03": [["bao gia", "tu van", "lien he", "chi phi tuy", "ngan sach"]],
    "q04": [["ten mien"], ["dang ky", "kiem tra", "whois", "tu van"]],
    "q05": [["cap nhat", "cham soc"], ["toc do", "bao mat", "seo", "noi dung"]],
    "q06": [["backlink"], ["traffic", "uy tin", "thu hang", "seo"]],
    "q07": [["ssl"], ["goi chuyen nghiep", "12.000.000", "tuy goi", "khong phai tat ca", "chua bao gom", "bao gia ssl"]],
    "q08": [["khong cam ket", "khong the cam ket", "khong dam bao", "khong the dam bao"], ["phu thuoc", "toi uu", "du lieu", "thuc te"]],
    "q09": [["0909 035 333", "0909035333"]],
    "q10": [["2.000.000", "2 trieu"], ["mau co san", "giao dien mau", "copy"]],
    "q11": [["seo"], ["noi dung", "tu khoa", "cau truc", "toc do", "backlink", "ky thuat"], ["kiem tra", "audit", "phan tich", "search console", "chuyen vien", "ho tro"]],
    "q12": [["thiet ke website"], ["marketing online", "seo", "marketing"]],
    "q13": [["ket noi", "chuyen", "gap"], ["nhan vien", "chuyen vien", "tu van"]],
    "q14": [["khong the", "ngoai pham vi", "ngoai linh vuc", "xin phep", "chuyen vien", "khong co du lieu", "tu choi", "khong chuyen"]],
    "q15": [["khong the", "ngoai pham vi", "ngoai linh vuc", "xin phep", "chuyen vien", "khong co du lieu", "tu choi", "khong chuyen"]],
}

SYSTEM = """Bạn là trợ lý tư vấn MIMA. Chỉ trả lời bằng NGỮ CẢNH; thiếu dữ liệu thì nói rõ và đề nghị kết nối chuyên viên, không đoán.
MIMA làm thiết kế website và marketing online. Hotline 0909 035 333. SEO, quảng cáo và backlink cần báo giá riêng.
Không cam kết thứ hạng/kết quả. Đồng ý kết nối khi khách cần người thật. Từ chối câu ngoài lĩnh vực. Trả lời tiếng Việt 2-4 câu."""


def norm(text: str) -> str:
    text = unicodedata.normalize("NFD", text.casefold())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn").replace("đ", "d")
    return re.sub(r"\s+", " ", text).strip()


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    scale = math.sqrt(sum(x * x for x in a) * sum(y * y for y in b))
    return dot / scale if scale else 0.0


def p95(values: list[float]) -> float | None:
    return round(sorted(values)[math.ceil(0.95 * len(values)) - 1], 1) if values else None


def score_answer(qid: str, reply: str) -> tuple[float, list[str]]:
    value = norm(reply)
    groups = RUBRICS[qid]
    hits = [any(option in value for option in group) for group in groups]
    safe = True
    notes = [f"concepts={sum(hits)}/{len(hits)}"]
    if qid == "q03" and re.search(r"\b\d+[.,]?\d*\s*(?:trieu|nghin|k|d|dong|%)\b", value):
        safe, notes = False, notes + ["invented_ads_price"]
    if qid == "q08" and re.search(r"\b(?:cam ket|dam bao|chac chan).{0,35}(?:top|google)", value) and not re.search(r"\bkhong(?: the)?\s+(?:cam ket|dam bao)", value):
        safe, notes = False, notes + ["unsafe_rank_guarantee"]
    if qid == "q14" and any(x in value for x in ("xuong", "banh pho", "nuoc dung", "que")):
        safe, notes = False, notes + ["answered_recipe"]
    if qid == "q15" and any(x in value for x in ("do c", "nhiet do", "troi mua", "troi nang")):
        safe, notes = False, notes + ["answered_weather"]
    return round(4 * sum(hits) / len(hits) + int(safe), 2), notes


def embedding_inputs(model: str, chunks: list[dict], questions: list[dict]):
    if model == "gemini-embedding-2":
        docs = [f"title: {c['metadata']['title']} | text: {c['content']}" for c in chunks]
        queries = [f"task: question answering | query: {q[v]}" for q in questions for v in ("with_diacritics", "no_diacritics")]
        return docs, queries, None, None
    docs = [c["content"] for c in chunks]
    queries = [q[v] for q in questions for v in ("with_diacritics", "no_diacritics")]
    is_old_gemini = model == "gemini-embedding-001"
    return docs, queries, "RETRIEVAL_DOCUMENT" if is_old_gemini else None, "QUESTION_ANSWERING" if is_old_gemini else None


def optimize_threshold(rows: list[dict]) -> tuple[float, float]:
    candidates = sorted({r["top_score"] for r in rows})
    best_accuracy, best_threshold = -1.0, candidates[0]
    for threshold in [candidates[0] - 1e-6, *candidates, candidates[-1] + 1e-6]:
        accuracy = statistics.mean((r["qid"] in RELEVANT) == (r["top_score"] >= threshold) for r in rows)
        if accuracy > best_accuracy or (accuracy == best_accuracy and threshold > best_threshold):
            best_accuracy, best_threshold = accuracy, threshold
    return round(best_threshold, 6), round(best_accuracy, 4)


def run_embedding(model: str, chunks: list[dict], questions: list[dict]) -> dict:
    provider, price = EMBEDDINGS[model]
    docs, queries, doc_task, query_task = embedding_inputs(model, chunks, questions)
    started = time.perf_counter()
    dvec = embed_texts(docs, model=model, provider=provider, task_type=doc_task)
    index_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    qvec = embed_texts(queries, model=model, provider=provider, task_type=query_task)
    query_ms = (time.perf_counter() - started) * 1000
    rows, cursor = [], 0
    for q in questions:
        for variant in ("with_diacritics", "no_diacritics"):
            scores = [cosine(qvec[cursor], vector) for vector in dvec]
            rank = sorted(range(len(chunks)), key=scores.__getitem__, reverse=True)
            relevant = RELEVANT.get(q["id"], set())
            rr = next((1 / n for n, idx in enumerate(rank, 1) if chunks[idx]["chunk_id"] in relevant), 0.0)
            rows.append({
                "qid": q["id"], "variant": variant, "question": q[variant],
                "top_score": round(scores[rank[0]], 6),
                "top_chunks": [{"chunk_id": chunks[idx]["chunk_id"], "title": chunks[idx]["metadata"]["title"], "content": chunks[idx]["content"], "score": round(scores[idx], 6)} for idx in rank[:3]],
                "rr": rr, "hit1": bool(relevant and chunks[rank[0]]["chunk_id"] in relevant),
                "hit3": bool(relevant and any(chunks[idx]["chunk_id"] in relevant for idx in rank[:3])),
            })
            cursor += 1
    eligible = [r for r in rows if r["qid"] in RELEVANT]
    threshold, answerability = optimize_threshold(rows)
    token_est = max(1, sum(map(len, docs + queries)) // 4)
    accented = [r["score"] for r in rows if r["variant"] == "with_diacritics"]
    unaccented = [r["score"] for r in rows if r["variant"] == "no_diacritics"]
    return {
        "provider": provider, "model": model, "dimension": len(dvec[0]),
        "hit1": round(statistics.mean(r["hit1"] for r in eligible), 4),
        "hit3": round(statistics.mean(r["hit3"] for r in eligible), 4),
        "mrr": round(statistics.mean(r["rr"] for r in eligible), 4),
        "accent_cosine": round(statistics.mean(cosine(qvec[i], qvec[i + 1]) for i in range(0, len(qvec), 2)), 4),
        "index_ms": round(index_ms, 1), "query_ms_each": round(query_ms / len(queries), 1),
        "threshold": threshold, "answerability_accuracy": answerability,
        "tokens_est": token_est, "cost_usd": round(token_est * price / 1_000_000, 6), "rows": rows,
    }


def oracle_context(qid: str, chunks: list[dict]) -> str:
    ids = RELEVANT.get(qid, set())
    selected = [c for c in chunks if c["chunk_id"] in ids][:3]
    return "\n\n".join(f"[{c['metadata']['title']}]\n{c['content']}" for c in selected) or "Không có dữ liệu truy xuất phù hợp."


def call_llm(model: str, prompt: str):
    provider, _, _ = LLMS[model]
    fn = _generate_openai if provider == "openai" else _generate_gemini
    return fn(model, SYSTEM, [{"role": "user", "content": prompt}], 0.0, [])


def run_llm(model: str, questions: list[dict], chunks: list[dict], retrieval: dict | None, pilot: bool) -> dict:
    provider, input_price, output_price = LLMS[model]
    lookup = {(r["qid"], r["variant"]): r for r in retrieval["rows"]} if retrieval else {}
    selected_questions = [q for q in questions if not pilot or q["id"] in PILOT_IDS]
    rows, latencies, tokens_in, tokens_out = [], [], 0, 0
    for q in selected_questions:
        variants = ("with_diacritics",) if pilot else ("with_diacritics", "no_diacritics")
        for variant in variants:
            if pilot:
                context = oracle_context(q["id"], chunks)
                chunk_ids = list(RELEVANT.get(q["id"], set()))[:3]
            else:
                hit = lookup[(q["id"], variant)]
                chosen = [x for x in hit["top_chunks"] if x["score"] >= retrieval["threshold"]]
                context = "\n\n".join(f"[{x['title']}]\n{x['content']}" for x in chosen) or "Không có dữ liệu truy xuất phù hợp."
                chunk_ids = [x["chunk_id"] for x in chosen]
            started = time.perf_counter()
            try:
                result = call_llm(model, f"NGỮ CẢNH:\n{context}\n\nCÂU HỎI: {q[variant]}")
                latency = (time.perf_counter() - started) * 1000
                score, notes = score_answer(q["id"], result.text)
                tokens_in += result.tokens_in
                tokens_out += result.tokens_out
                latencies.append(latency)
                rows.append({"qid": q["id"], "variant": variant, "question": q[variant], "reply": result.text, "score": score, "notes": notes, "latency_ms": round(latency, 1), "tokens_in": result.tokens_in, "tokens_out": result.tokens_out, "chunks": chunk_ids})
                print(f"  ✓ {model} {q['id']}/{variant}: {score}/5, {latency:.0f} ms")
            except Exception as exc:
                rows.append({"qid": q["id"], "variant": variant, "question": q[variant], "error": str(exc), "score": 0.0})
                print(f"  ✗ {model} {q['id']}/{variant}: {exc}")
    scores = [r["score"] for r in rows]
    cost = (tokens_in * input_price + tokens_out * output_price) / 1_000_000
    avg_cost = cost / len(rows)
    return {
        "provider": provider, "model": model, "calls": len(rows), "errors": sum("error" in r for r in rows),
        "quality": round(statistics.mean(scores), 3), "pass_rate": round(statistics.mean(s >= 4 for s in scores), 4),
        "accented_quality": round(statistics.mean(accented), 3) if accented else None,
        "unaccented_quality": round(statistics.mean(unaccented), 3) if unaccented else None,
        "latency_avg_ms": round(statistics.mean(latencies), 1) if latencies else None, "latency_p95_ms": p95(latencies),
        "tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": round(cost, 6),
        "cost_per_answer_usd": round(avg_cost, 8), "projected_100k_usd": round(avg_cost * 100_000, 2), "rows": rows,
    }


def render(result: dict) -> str:
    lines = ["# Kết quả kiểm nghiệm HOA-03", "", f"Chạy lúc `{result['run_at_utc']}`; mode `{result['mode']}`.", ""]
    lines += ["## LLM", "", "| Model | Điểm /5 | Tỷ lệ đạt ≥4 | Latency TB | p95 | Chi phí lượt chạy | USD/câu | Dự phóng 100k câu |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in result["llms"]:
        lines.append(f"| `{r['model']}` | {r['quality']:.3f} | {r['pass_rate']:.1%} | {r['latency_avg_ms']} ms | {r['latency_p95_ms']} ms | ${r['cost_usd']:.6f} | ${r['cost_per_answer_usd']:.8f} | ${r['projected_100k_usd']:.2f} |")
    lines += ["", "## Embedding / retrieval", "", "| Model | Hit@1 | Hit@3 | MRR | Cosine dấu/không dấu | Query latency | Chi phí benchmark |", "|---|---:|---:|---:|---:|---:|---:|"]
    for r in result["embeddings"]:
        lines.append(f"| `{r['model']}` | {r['hit1']:.1%} | {r['hit3']:.1%} | {r['mrr']:.3f} | {r['accent_cosine']:.3f} | {r['query_ms_each']:.1f} ms | ${r['cost_usd']:.6f} |")
    lines += ["", "## Phương pháp", "", "- Dùng nguyên 15 câu trong `tests/h03_test_questions.json`; full mode chạy cả có dấu/không dấu.", "- Pilot LLM dùng cùng oracle context để tách chất lượng model khỏi lỗi retrieval; full mode dùng top-3 thật của embedding cùng hệ sinh thái.", "- Retrieval chấm Hit@k/MRR theo ground-truth chunk-id định trước. Điểm trả lời dùng rubric từ khóa cố định trong script.", "- Chi phí LLM lấy token usage thật; embedding ước tính token bằng ký tự/4. Dự phóng 100k chỉ là chi phí model, chưa gồm hạ tầng.", ""]
    return "\n".join(lines)


def rescore_saved() -> None:
    path = OUT / "h03_full_results.json"
    result = json.loads(path.read_text(encoding="utf-8"))
    for llm in result["llms"]:
        for row in llm["rows"]:
            if "reply" in row:
                row["score"], row["notes"] = score_answer(row["qid"], row["reply"])
        scores = [row["score"] for row in llm["rows"]]
        accented = [row["score"] for row in llm["rows"] if row["variant"] == "with_diacritics"]
        unaccented = [row["score"] for row in llm["rows"] if row["variant"] == "no_diacritics"]
        llm["quality"] = round(statistics.mean(scores), 3)
        llm["pass_rate"] = round(statistics.mean(score >= 4 for score in scores), 4)
        llm["accented_quality"] = round(statistics.mean(accented), 3)
        llm["unaccented_quality"] = round(statistics.mean(unaccented), 3)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    RAW_OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_OUT.write_text(render(result), encoding="utf-8")
    print(f"Đã chấm lại {path} mà không gọi API.")


def args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("pilot", "full", "rescore"), default="full")
    parser.add_argument("--openai-llm", default=SELECTED_LLM["openai"], choices=LLMS)
    parser.add_argument("--gemini-llm", default=SELECTED_LLM["gemini"], choices=LLMS)
    parser.add_argument("--openai-embedding", default=SELECTED_EMBEDDING["openai"], choices=EMBEDDINGS)
    parser.add_argument("--gemini-embedding", default=SELECTED_EMBEDDING["gemini"], choices=EMBEDDINGS)
    return parser.parse_args()


def main() -> None:
    opt = args()
    if opt.mode == "rescore":
        rescore_saved()
        return
    questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    chunks = json.loads(CHUNKS.read_text(encoding="utf-8"))
    embedding_models = list(EMBEDDINGS) if opt.mode == "pilot" else [opt.openai_embedding, opt.gemini_embedding]
    llm_models = list(LLMS) if opt.mode == "pilot" else [opt.openai_llm, opt.gemini_llm]
    embedding_results = []
    for model in embedding_models:
        print(f"\n=== Embedding {model} ===")
        embedding_results.append(run_embedding(model, chunks, questions))
    by_model = {r["model"]: r for r in embedding_results}
    llm_results = []
    for model in llm_models:
        print(f"\n=== LLM {model} ===")
        provider = LLMS[model][0]
        retrieval = None if opt.mode == "pilot" else by_model[opt.openai_embedding if provider == "openai" else opt.gemini_embedding]
        llm_results.append(run_llm(model, questions, chunks, retrieval, opt.mode == "pilot"))
    result = {"schema": "hoa-03.v2", "run_at_utc": datetime.now(UTC).isoformat(), "mode": opt.mode, "question_count": len(questions), "llms": llm_results, "embeddings": embedding_results}
    OUT.mkdir(parents=True, exist_ok=True)
    suffix = "pilot" if opt.mode == "pilot" else "full"
    (OUT / f"h03_{suffix}_results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    RAW_OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_OUT.write_text(render(result), encoding="utf-8")
    print(f"\nĐã ghi {RAW_OUT} và {REPORT_OUT}")


if __name__ == "__main__":
    main()
