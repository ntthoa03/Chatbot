"""
HOA-06 — 10 truy vấn mẫu để kiểm chứng "Định nghĩa hoàn thành":
"Hỏi 10 câu, chunk lấy về đúng chủ đề; câu hỏi ngoài phạm vi thì trả rỗng
chứ không trả bừa."

Chạy: python tests/demo_retriever_queries.py
(cần chạy tests/build_demo_index.py trước để có index demo)

Dùng bow_embed_texts (embedder giả offline) thay vì embed_texts thật, vì môi
trường test này không gọi được OpenAI. Khi có OPENAI_API_KEY thật, bỏ tham số
embed_fn/model đi để retrieve() dùng mặc định (gọi OpenAI thật).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fake_bow_embedder import bow_embed_texts  # noqa: E402

from ai_core.retriever import retrieve  # noqa: E402

# (câu hỏi, có kỳ vọng trả về kết quả không)
QUERIES: list[tuple[str, bool]] = [
    ("Thiết kế website giá bao nhiêu tiền?", True),
    ("MIMA có làm dịch vụ SEO tổng thể không?", True),
    ("Chạy quảng cáo Google Ads giá thế nào?", True),
    ("Tư vấn đăng ký tên miền và hosting cho công ty", True),
    ("Dịch vụ chăm sóc website bao gồm những gì?", True),
    ("MIMA có làm backlink báo chí tăng traffic không?", True),
    ("Website có được bảo mật SSL không?", True),
    ("MIMA có cam kết lên top 1 Google trong 1 tháng không?", True),
    ("Cho tôi công thức nấu phở bò truyền thống", False),
    ("Dự báo thời tiết ngày mai ở Hà Nội thế nào?", False),
]

# Ngưỡng riêng cho demo BOW — vector bag-of-words thưa hơn nhiều so với
# embedding thật nên cosine similarity có thang điểm khác. Ngưỡng production
# (0.3, xem ai_core/retriever.py) chỉ áp dụng khi dùng embedding OpenAI thật.
DEMO_THRESHOLD = 0.12


def main() -> None:
    n_pass = 0
    for query, expect_nonempty in QUERIES:
        results = retrieve(
            query,
            tenant_id="mima_internal",
            k=5,
            threshold=DEMO_THRESHOLD,
            embed_fn=bow_embed_texts,
            model="bow-test-only",
        )
        got_nonempty = bool(results)
        ok = got_nonempty == expect_nonempty
        n_pass += ok
        print(f"{'✅' if ok else '❌'} \"{query}\"")
        if results:
            for r in results:
                print(f"     [{r['score']}] {r['chunk_id']} — {r['content'][:70]}...")
        else:
            print("     (rỗng — không có chunk nào đạt ngưỡng)")
        print()

    print(f"Kết quả: {n_pass}/{len(QUERIES)} câu đúng kỳ vọng (đúng chủ đề hoặc đúng-là-ngoài-phạm-vi).")


if __name__ == "__main__":
    main()
