"""In bảng kiểm tra cách ly tenant của cache H2-08, không gọi API."""

from __future__ import annotations

from ai_core.cache import CacheError, SemanticResponseCache


MIMA = "mima_internal"
HYHY = "phongkham_hyhy"
VECTOR = [1.0, 0.0]


def _response() -> dict:
    return {
        "reply": "CÂU TRẢ LỜI CHỈ THUỘC TENANT MIMA",
        "sources": [
            {
                "chunk_id": "mima-price-001",
                "url": "https://mimadigi.com/gia",
                "score": 0.99,
            }
        ],
        "tool_calls": [],
        "need_human": False,
        "lead_captured": None,
        "guardrail": {"blocked": False, "reason": None},
        "usage": {"model": "demo", "tokens_in": 1, "tokens_out": 1, "cost_usd": 1.0},
        "trace_id": "00000000-0000-0000-0000-000000000001",
    }


def main() -> int:
    cache = SemanticResponseCache(similarity_threshold=0.92)
    cache.put(
        tenant_id=MIMA,
        config_version=1,
        question="Thiết kế website bao nhiêu tiền?",
        vector=VECTOR,
        response=_response(),
    )

    print("\n=== H2-08: KIỂM TRA KHÓA CACHE CÓ TENANT_ID ===")
    print("Đã ghi 1 entry của MIMA với vector [1.0, 0.0].")
    print("Các lượt đọc bên dưới cố tình dùng CÙNG VECTOR (similarity 100%).\n")
    print(f"{'STT':<4} {'Tenant đọc':<22} {'Config':<8} {'Thực tế':<10} {'Mong đợi':<10} {'Kết luận'}")
    print("-" * 78)

    scenarios = [
        ("1", MIMA, 1, True, "Cùng tenant/config nên được dùng cache"),
        ("2", HYHY, 1, False, "Khác tenant nên tuyệt đối không thấy cache MIMA"),
        ("3", MIMA, 2, False, "Khác config version nên không dùng cache cũ"),
    ]
    all_passed = True
    for number, tenant_id, config_version, expected_hit, explanation in scenarios:
        result = cache.lookup(
            tenant_id=tenant_id,
            config_version=config_version,
            question="Câu kiểm tra cùng vector",
            vector=VECTOR,
        )
        actual = "HIT" if result.hit else "MISS"
        expected = "HIT" if expected_hit else "MISS"
        passed = result.hit is expected_hit
        all_passed &= passed
        verdict = "PASS" if passed else "FAIL — CÓ NGUY CƠ RÒ DỮ LIỆU"
        print(f"{number:<4} {tenant_id:<22} {config_version:<8} {actual:<10} {expected:<10} {verdict}")
        print(f"     ↳ {explanation}")

    print("\nKiểm tra tenant thiếu/rỗng:")
    for value in (None, "", "   "):
        try:
            cache.lookup(
                tenant_id=value,
                config_version=1,
                question="Câu kiểm tra",
                vector=VECTOR,
            )
        except CacheError as exc:
            print(f"  tenant_id={value!r:<6} → ERROR đúng yêu cầu: {exc}")
        else:
            all_passed = False
            print(f"  tenant_id={value!r:<6} → FAIL: không phát sinh lỗi")

    print("\n=== KẾT LUẬN ===")
    if all_passed:
        print("PASS: Cache MIMA không bị tenant Hỷ Hỷ đọc được, kể cả khi vector giống 100%.")
        return 0
    print("FAIL: Phát hiện tình huống không đúng kỳ vọng; không được đưa cache vào production.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
