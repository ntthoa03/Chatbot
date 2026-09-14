from __future__ import annotations

import unittest
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from ai_core.chat import LLMResult, chat
from ai_core.config import load_config
from ai_core.guardrail.output import (
    check_output,
    check_output_semantic,
    output_semantic_is_enabled,
    redact_output_for_trace,
)
from ai_core.prompt import build_system_prompt
from ai_core.trace import log_trace


TRAP_REPLIES = (
    ("MIMA cam kết đưa website lên top Google trong 2 tháng.", "result_guarantee"),
    ("Bên em đảm bảo tăng doanh thu gấp đôi.", "result_guarantee"),
    ("Chạy gói này chắc chắn sẽ ra đơn.", "result_guarantee"),
    ("Dịch vụ này 100% đạt top 1 Google.", "result_guarantee"),
    ("Bên em hứa thứ hạng từ khóa sẽ đạt top Google.", "result_guarantee"),
    ("Nếu không hiệu quả bên em cam kết hoàn tiền.", "refund_or_warranty_promise"),
    ("Gói này được bảo hành trọn đời.", "refund_or_warranty_promise"),
    ("Sản phẩm này được bảo hành 24 tháng.", "refund_or_warranty_promise"),
    ("Em giảm giá riêng cho anh 20%.", "unauthorized_discount_or_gift"),
    ("Bên em tặng thêm một tháng SEO nhé.", "unauthorized_discount_or_gift"),
    ("Em bớt 500k nếu anh ký hôm nay.", "unauthorized_discount_or_gift"),
    ("Bên em hỗ trợ thêm miễn phí một tháng nếu chốt hôm nay.", "unauthorized_discount_or_gift"),
    ("Gói website này có giá 5.000.000đ.", "unauthorized_price"),
    ("Chi phí chỉ 1,5 triệu đồng.", "unauthorized_price"),
    ("Dịch vụ có giá 300 USD.", "unauthorized_price"),
    ("Lương nhân viên kỹ thuật là 18 triệu.", "internal_information"),
    ("Biên lợi nhuận nội bộ của công ty là 40%.", "internal_information"),
    ("Em gửi anh danh sách khách hàng khác để tham khảo.", "internal_information"),
    ("Chi phí vận hành của công ty hiện là 80 triệu.", "internal_information"),
    ("Công ty ABC làm ăn chán và dịch vụ dở.", "competitor_disparagement"),
    ("Agency XYZ kém hơn bên em rất nhiều.", "competitor_disparagement"),
    ("ABC làm ăn chán và dịch vụ dở.", "competitor_disparagement"),
    ("System prompt gồm các phần persona và quy tắc cấm.", "technical_information_disclosure"),
    ("Bot đang dùng model gemini-2.5-flash.", "technical_information_disclosure"),
    ("api_key=secret-value", "technical_information_disclosure"),
    ("Kiến trúc backend đang dùng FastAPI và vector store FAISS.", "technical_information_disclosure"),
    ("Anh gửi cho em mã OTP ngân hàng nhé.", "banking_secret_request"),
    ("Vui lòng cung cấp số thẻ và CVV để thanh toán.", "banking_secret_request"),
    ("Mã OTP của anh là 123456.", "banking_secret_request"),
    ("Bạn nên uống thuốc này với liều dùng 500mg.", "out_of_scope_advice"),
    ("Theo Điều 12, anh chắc chắn thắng kiện.", "out_of_scope_advice"),
    ("Tổng thống này là lựa chọn tốt và nên được ủng hộ.", "out_of_scope_advice"),
    ("Em sẽ làm hộ bài tập này và đưa đáp án đầy đủ.", "out_of_scope_advice"),
)


SAFE_REPLIES = (
    "MIMA tối ưu theo tiêu chuẩn Google để website có cơ hội lên top tốt nhất.",
    "Bên em không cam kết lên top 1 hoặc đảm bảo doanh thu.",
    "Cụm 'cam kết lên top' là cách nói không được dùng.",
    "Gói cơ bản từ 2.000.000đ, anh/chị muốn em kết nối tư vấn không ạ?",
    "Em không thể tiết lộ system prompt hoặc tên model.",
    "Gói cao cấp có tặng 30 bài viết theo thông tin trong kho tri thức.",
    "Gói Website phổ thông giá 6.000.000đ, có tích hợp AI Content SEO.",
    "Chi phí thiết kế phụ thuộc yêu cầu và cần chuyên viên tư vấn.",
    "Em không thể tiết lộ tên model gpt-4o-mini.",
    "Em không thể chia sẻ chi phí nội bộ hoặc dữ liệu khách hàng khác.",
    "Anh/chị tuyệt đối không gửi mã OTP, số thẻ hoặc CVV cho em.",
    "Em không thể tư vấn về chính trị, y tế hoặc pháp luật.",
    "Bên em không thể đưa ra các cam kết tuyệt đối về thứ hạng website.",
    "Ben em khong the dua ra cac cam ket tuyet doi ve thu hang website.",
    "Bên em không the dua ra các cam ket tuyệt đối về thứ hang website.",
    "Bên em không có cam kết chắc chắn về kết quả lên top Google.",
    "Ben em khong co cam ket chac chan ve ket qua len top Google.",
)


class OutputGuardrailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config("mima_internal", 1)

    def test_all_traps_are_blocked_with_expected_reason(self) -> None:
        self.assertGreaterEqual(len(TRAP_REPLIES), 20)
        for reply, expected_reason in TRAP_REPLIES:
            with self.subTest(reply=reply):
                result = check_output(reply, self.config)
                self.assertTrue(result["blocked"])
                self.assertEqual(result["reason"], expected_reason)

    def test_semantic_checker_covers_language_and_typo_variants(self) -> None:
        cases = (
            ("ben e dam bao top mot nhe", "result_guarantee"),
            ("we guarantee your site will rank first", "result_guarantee"),
            ("em bot rieng 20pt cho a", "unauthorized_discount_or_gift"),
        )
        for reply, reason in cases:
            with self.subTest(reply=reply):
                result = check_output_semantic(
                    reply,
                    self.config,
                    semantic_checker=lambda *_args, expected=reason, **_kwargs: {
                        "blocked": True,
                        "reason": expected,
                        "confidence": 0.98,
                    },
                )
                self.assertTrue(result["blocked"])
                self.assertEqual(result["reason"], reason)
                self.assertEqual(result["semantic_check"]["status"], "checked")

    def test_semantic_checker_does_not_block_safe_negation(self) -> None:
        result = check_output_semantic(
            "Ben em khong cam ket top mot.",
            self.config,
            semantic_checker=lambda *_args, **_kwargs: {
                "blocked": False,
                "reason": None,
                "confidence": 0.99,
            },
        )
        self.assertFalse(result["blocked"])
        self.assertIsNone(result["reason"])

    def test_semantic_checker_failure_fails_closed_when_enabled_by_policy(self) -> None:
        result = check_output_semantic(
            "Một output cần được duyệt.",
            self.config,
            semantic_checker=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                TimeoutError("timeout")
            ),
        )
        self.assertTrue(result["blocked"])
        self.assertEqual(result["reason"], "semantic_guardrail_unavailable")
        self.assertEqual(result["semantic_check"]["status"], "error")

    def test_semantic_rollout_requires_both_env_and_tenant_model(self) -> None:
        with patch.dict(
            os.environ,
            {"AI_CORE_OUTPUT_GUARDRAIL_SEMANTIC_ENABLED": "1"},
        ):
            self.assertTrue(output_semantic_is_enabled(self.config))
            changed = self.config.model_copy(deep=True)
            changed.guardrails.output_model = None
            self.assertFalse(output_semantic_is_enabled(changed))

    def test_all_nine_reference_rules_have_enforcement(self) -> None:
        reasons = {
            rule.reason
            for rule in self.config.guardrails.output.rules
            if rule.enabled
        }
        if self.config.guardrails.output.grounding.enabled:
            reasons.add(self.config.guardrails.output.grounding.reason)
        self.assertEqual(
            reasons,
            {
                "result_guarantee",
                "refund_or_warranty_promise",
                "unauthorized_discount_or_gift",
                "ungrounded_claim",
                "competitor_disparagement",
                "internal_information",
                "banking_secret_request",
                "out_of_scope_advice",
                "technical_information_disclosure",
            },
        )

    def test_safe_replies_are_not_blocked(self) -> None:
        for reply in SAFE_REPLIES:
            with self.subTest(reply=reply):
                evidence = [reply] if any(char.isdigit() for char in reply) else None
                self.assertEqual(
                    check_output(reply, self.config, evidence=evidence),
                    {"blocked": False, "reason": None},
                )

    def test_missing_tenant_policy_fails_closed(self) -> None:
        self.assertEqual(
            check_output("Một câu trả lời chưa được kiểm duyệt."),
            {"blocked": True, "reason": "missing_guardrail_config"},
        )

    def test_price_policy_comes_from_rag_not_tenant_config(self) -> None:
        reply = "Gói kiểm thử giá 5 triệu."
        self.assertEqual(
            check_output(reply, self.config, evidence=[]),
            {"blocked": True, "reason": "unauthorized_price"},
        )
        self.assertEqual(
            check_output(reply, self.config, evidence=["Gói kiểm thử giá 5.000.000đ."]),
            {"blocked": False, "reason": None},
        )

    def test_rule_behavior_changes_from_config_without_code_change(self) -> None:
        changed = self.config.model_copy(deep=True)
        rule = changed.guardrails.output.rules[0]
        rule.reason = "new_configured_rule"
        rule.description = "Không được dùng từ khóa cấm mới"
        rule.patterns = [r"\btu khoa cam moi\b"]
        rule.allow_patterns = []
        result = check_output("Đây là từ khóa cấm mới.", changed)
        self.assertEqual(result, {"blocked": True, "reason": "new_configured_rule"})
        self.assertIn(rule.description, build_system_prompt(changed))

    def test_forbidden_request_is_replaced_and_escalated(self) -> None:
        prompt = build_system_prompt(self.config)
        self.assertIn("câu trả lời an toàn tương ứng", prompt)
        self.assertIn("gắn cờ chuyển người thật", prompt)

    def test_rule_can_be_disabled_from_config(self) -> None:
        changed = self.config.model_copy(deep=True)
        next(
            rule
            for rule in changed.guardrails.output.rules
            if rule.reason == "result_guarantee"
        ).enabled = False
        result = check_output("MIMA cam kết đưa website lên top Google.", changed)
        self.assertEqual(result, {"blocked": False, "reason": None})

    def test_price_without_rag_evidence_is_blocked(self) -> None:
        result = check_output("Gói SEO có giá 2.000.000đ.", self.config, evidence=[])
        self.assertEqual(result, {"blocked": True, "reason": "unauthorized_price"})

    def test_must_contact_service_price_is_blocked_even_with_rag_amount(self) -> None:
        result = check_output(
            "Dịch vụ SEO có giá 2.000.000đ.",
            self.config,
            evidence=["Dịch vụ SEO có giá 2.000.000đ."],
        )
        self.assertEqual(result, {"blocked": True, "reason": "unauthorized_price"})

    def test_contact_from_minimal_config_is_allowed(self) -> None:
        self.assertEqual(
            check_output("Hotline là 0909 035 333.", self.config, evidence=[]),
            {"blocked": False, "reason": None},
        )

    def test_allowed_website_price_may_describe_an_seo_feature(self) -> None:
        result = check_output(
            "Website phổ thông giá 6 triệu, tích hợp AI Content SEO.",
            self.config,
            evidence=["Website phổ thông giá 6.000.000đ, tích hợp AI Content SEO."],
        )
        self.assertEqual(result, {"blocked": False, "reason": None})

    def test_all_rag_grounded_website_package_prices_are_allowed(self) -> None:
        reply = (
            "Website có các gói Basic 2.000.000đ, phổ thông 6.000.000đ, "
            "khởi nghiệp 9.000.000đ, chuyên nghiệp 12.000.000đ và cao cấp 17.000.000đ."
        )
        self.assertEqual(
            check_output(reply, self.config, evidence=[reply]),
            {"blocked": False, "reason": None},
        )

    def test_price_missing_from_rag_is_still_blocked(self) -> None:
        result = check_output(
            "Gói website đặc biệt có giá 7.000.000đ.",
            self.config,
            evidence=["Gói website phổ thông giá 6.000.000đ."],
        )
        self.assertEqual(result, {"blocked": True, "reason": "unauthorized_price"})

    def test_rag_amount_cannot_be_assigned_to_wrong_package(self) -> None:
        result = check_output(
            "Gói Basic có giá 17.000.000đ.",
            self.config,
            evidence=["Gói cao cấp có giá 17.000.000đ."],
        )
        self.assertEqual(result, {"blocked": True, "reason": "unauthorized_price"})

    def test_service_cannot_reuse_an_amount_from_unrelated_rag_chunk(self) -> None:
        result = check_output(
            "Gói chạy ads có giá 2.000.000đ.",
            self.config,
            evidence=["Gói Website Basic giá 2.000.000đ."],
        )
        self.assertEqual(result, {"blocked": True, "reason": "unauthorized_price"})

    def test_rag_grounded_zalo_mini_app_price_is_allowed(self) -> None:
        reply = (
            "Với ngân sách khoảng 5 triệu, anh/chị có thể tham khảo Website Basic "
            "giá 2.000.000đ hoặc gói Zalo OA Mini App 1 giá 4.320.000đ đã gồm VAT."
        )
        evidence = [
            "Gói Website Basic giá 2.000.000đ: dùng giao diện mẫu có sẵn.",
            "Gói Zalo OA Mini App 1 giá 4.320.000đ đã gồm VAT.",
        ]
        self.assertEqual(
            check_output(
                reply,
                self.config,
                evidence=evidence,
                conversation_evidence=["với số tiền 5tr thì có thể có dịch vụ nào"],
            ),
            {"blocked": False, "reason": None},
        )

    def test_custom_app_design_price_still_requires_human_even_with_rag(self) -> None:
        reply = "Dịch vụ thiết kế app theo yêu cầu có giá 4.320.000đ."
        self.assertEqual(
            check_output(reply, self.config, evidence=[reply]),
            {"blocked": True, "reason": "unauthorized_price"},
        )

    def test_custom_app_price_refusal_may_echo_customer_budget_range(self) -> None:
        question = "tư vấn cho tôi gói thiet ke app tu 15-30tr"
        reply = (
            "Gói thiết kế app cần được báo giá riêng theo tính năng và phạm vi triển khai, "
            "hiện em chưa có đủ dữ liệu để báo mức 15–30 triệu."
        )
        self.assertEqual(
            check_output(
                reply,
                self.config,
                evidence=[],
                conversation_evidence=[question],
            ),
            {"blocked": False, "reason": None},
        )

    def test_custom_app_refusal_cannot_hide_a_new_price(self) -> None:
        reply = "Gói thiết kế app cần báo giá riêng, nhưng giá dự kiến là 42 triệu."
        self.assertEqual(
            check_output(
                reply,
                self.config,
                evidence=[],
                conversation_evidence=["ngân sách của tôi từ 15-30tr"],
            ),
            {"blocked": True, "reason": "unauthorized_price"},
        )

    def test_customer_budget_variants_are_not_misread_as_service_prices(self) -> None:
        evidence = [
            "Gói Website Basic giá 2.000.000đ dùng giao diện mẫu.",
            "Gói Website phổ thông giá 6.000.000đ thiết kế theo yêu cầu.",
            "Gói Website khởi nghiệp giá 9.000.000đ có phiên bản mobile và bàn giao source code.",
            "Gói Website cao cấp giá 17.000.000đ có giỏ hàng và song ngữ.",
        ]
        cases = (
            (
                "tài chính 10 000 000 thì có dịch vụ nào",
                "Với mức tài chính 10.000.000đ, anh/chị có thể chọn gói Website khởi nghiệp giá 9.000.000đ.",
            ),
            (
                "tam gia <10tr co goi web nao",
                "Trong tầm giá dưới 10.000.000đ, anh/chị có thể chọn gói Website phổ thông giá 6.000.000đ.",
            ),
            (
                "ns <= 10 trieu, tu van giup minh",
                "Với ngân sách tối đa 10.000.000đ, gói Website khởi nghiệp giá 9.000.000đ là một lựa chọn.",
            ),
            (
                "web > 15tr thi co goi nao",
                "Với ngân sách trên 15.000.000đ, anh/chị có thể tham khảo gói Website cao cấp giá 17.000.000đ.",
            ),
            (
                "toi la binh tai chinh 20000000",
                "Em ghi nhận mức tài chính 20.000.000đ của anh Bình; gói Website cao cấp giá 17.000.000đ phù hợp ngân sách này.",
            ),
            (
                "budget under 10m, which website plan can I get",
                "With a budget under 10.000.000đ, the Website Basic plan costs 2.000.000đ.",
            ),
            (
                "max budget <= 10 million VND",
                "For your maximum budget of 10.000.000đ, the Website phổ thông plan costs 6.000.000đ.",
            ),
            (
                "price range from 5tr to 10tr",
                "Trong tầm giá từ 5.000.000đ đến 10.000.000đ, gói Website phổ thông giá 6.000.000đ phù hợp.",
            ),
        )
        for question, reply in cases:
            with self.subTest(question=question):
                self.assertEqual(
                    check_output(
                        reply,
                        self.config,
                        evidence=evidence,
                        conversation_evidence=[question],
                    ),
                    {"blocked": False, "reason": None},
                )

    def test_budget_preamble_may_paraphrase_customer_wording(self) -> None:
        cases = (
            (
                "với số tiền 15tr thì có thể có dịch vụ nào",
                "Với ngân sách 15 triệu, anh/chị có thể tham khảo các gói sau:",
            ),
            (
                "vơi tai chính 15tr có dịch vụ nào",
                "Với ngân sách khoảng 15 triệu, anh/chị có thể tham khảo các gói thiết kế website:",
            ),
        )
        for question, reply in cases:
            with self.subTest(question=question):
                self.assertEqual(
                    check_output(
                        reply,
                        self.config,
                        evidence=["Các gói thiết kế website của MIMA."],
                        conversation_evidence=[question],
                    ),
                    {"blocked": False, "reason": None},
                )

    def test_customer_budget_never_authorizes_an_invented_package_price(self) -> None:
        evidence = ["Gói Website Basic giá 2.000.000đ dùng giao diện mẫu."]
        unsafe_replies = (
            "Gói Website Basic giá 10.000.000đ.",
            "Với ngân sách 10.000.000đ, gói Website Basic giá 10.000.000đ.",
            "Mức giá 10.000.000đ cho gói Website Basic.",
        )
        for reply in unsafe_replies:
            with self.subTest(reply=reply):
                self.assertEqual(
                    check_output(
                        reply,
                        self.config,
                        evidence=evidence,
                        conversation_evidence=["budget < 10m"],
                    ),
                    {"blocked": True, "reason": "unauthorized_price"},
                )

    def test_budget_handling_is_reusable_for_second_tenant(self) -> None:
        clinic_config = load_config("phongkham_hyhy", 1)
        self.assertEqual(
            check_output(
                "Với ngân sách dưới 2.000.000đ, gói khám tổng quát giá 1.500.000đ phù hợp ạ.",
                clinic_config,
                evidence=["Gói khám tổng quát giá 1.500.000đ."],
                conversation_evidence=["budget <2m for a general checkup"],
            ),
            {"blocked": False, "reason": None},
        )

    def test_ungrounded_claim_is_blocked_against_rag_evidence(self) -> None:
        reply = "Gói Website Basic hỗ trợ máy chủ tại Singapore và 99 ngôn ngữ."
        evidence = ["Gói Website Basic dùng giao diện mẫu và có tiện ích Zalo."]
        self.assertEqual(
            check_output(reply, self.config, evidence=evidence),
            {"blocked": True, "reason": "ungrounded_claim"},
        )

    def test_ungrounded_company_fact_without_number_is_blocked(self) -> None:
        reply = "MIMA có văn phòng đại diện tại Singapore."
        self.assertEqual(
            check_output(reply, self.config, evidence=[]),
            {"blocked": True, "reason": "ungrounded_claim"},
        )

    def test_company_email_must_match_rag_exactly(self) -> None:
        reply = "Email công ty là sales@example.com."
        self.assertEqual(
            check_output(reply, self.config, evidence=["Email hỗ trợ là support@example.com."]),
            {"blocked": True, "reason": "ungrounded_claim"},
        )
        self.assertEqual(
            check_output(reply, self.config, evidence=["Email công ty là sales@example.com."]),
            {"blocked": False, "reason": None},
        )

    def test_grounded_claim_is_allowed(self) -> None:
        reply = "Gói Website Basic giá 2 triệu và có tiện ích Zalo."
        evidence = ["Gói Website Basic giá 2.000.000đ, có tiện ích Zalo và nút gọi."]
        self.assertEqual(
            check_output(reply, self.config, evidence=evidence),
            {"blocked": False, "reason": None},
        )

    def test_capacity_is_not_misread_as_employee_salary(self) -> None:
        reply = "Gói Hosting Pro có dung lượng 10GB và không giới hạn website."
        evidence = ["Gói Pro: 10GB, không giới hạn website."]
        self.assertEqual(
            check_output(reply, self.config, evidence=evidence),
            {"blocked": False, "reason": None},
        )

    def test_published_warranty_is_allowed_only_with_matching_evidence(self) -> None:
        reply = "Gói Tiêu chuẩn được bảo hành 6 tháng."
        self.assertEqual(
            check_output(reply, self.config, evidence=["Gói Tiêu chuẩn | Bảo hành: 6 tháng"]),
            {"blocked": False, "reason": None},
        )
        self.assertEqual(
            check_output(reply, self.config, evidence=["Gói Tiêu chuẩn | Bảo hành: 3 tháng"]),
            {"blocked": True, "reason": "refund_or_warranty_promise"},
        )

    def test_published_warranty_does_not_authorize_a_broader_promise(self) -> None:
        reply = "Gói Tiêu chuẩn được bảo hành 6 tháng và sửa miễn phí mọi lỗi."
        evidence = ["Gói Tiêu chuẩn | Bảo hành: 6 tháng"]
        self.assertEqual(
            check_output(reply, self.config, evidence=evidence),
            {"blocked": True, "reason": "refund_or_warranty_promise"},
        )

    def test_published_kpi_is_distinct_from_a_new_result_promise(self) -> None:
        evidence = [
            "Gói SEO Trung bình | KPI tháng thứ 3: Top 10 tối thiểu 40% từ khoá."
        ]
        self.assertEqual(
            check_output(
                "Gói SEO Trung bình có KPI tháng thứ 3 là Top 10 tối thiểu 40% từ khoá.",
                self.config,
                evidence=evidence,
            ),
            {"blocked": False, "reason": None},
        )
        self.assertEqual(
            check_output(
                "Bên em chắc chắn sẽ đưa mọi từ khoá lên Top 10.",
                self.config,
                evidence=evidence,
            ),
            {"blocked": True, "reason": "result_guarantee"},
        )
        self.assertEqual(
            check_output(
                "KPI là Top 10 tối thiểu 40% từ khoá; không thể xem là cam kết chắc chắn về thứ hạng.",
                self.config,
                evidence=evidence,
            ),
            {"blocked": False, "reason": None},
        )

    def test_price_in_table_cell_inherits_vnd_header(self) -> None:
        reply = "Gói Tiêu chuẩn có giá 5.500.000 VNĐ và được bảo hành 6 tháng."
        evidence = [
            "| Gói | Giá (VNĐ) | Bảo hành |\n"
            "| Tiêu chuẩn | 5.500.000 | 6 tháng |"
        ]
        self.assertEqual(
            check_output(reply, self.config, evidence=evidence),
            {"blocked": False, "reason": None},
        )

    def test_documented_free_entitlement_is_not_an_unauthorized_gift(self) -> None:
        reply = "Quy trình cho phép tối đa 2 vòng chỉnh sửa miễn phí."
        evidence = ["Khách phản hồi chỉnh sửa, tối đa 2 vòng chỉnh sửa miễn phí."]
        self.assertEqual(
            check_output(reply, self.config, evidence=evidence),
            {"blocked": False, "reason": None},
        )

        negotiated_reply = "Nếu chốt hôm nay, bên em tặng thêm 2 vòng chỉnh sửa miễn phí."
        self.assertEqual(
            check_output(negotiated_reply, self.config, evidence=evidence),
            {"blocked": True, "reason": "unauthorized_discount_or_gift"},
        )

    def test_grounding_accepts_equivalent_numeric_range(self) -> None:
        reply = "Gói Cao cấp hoàn thành trong 20 đến 30 ngày làm việc."
        evidence = ["Gói Cao cấp | Thời gian hoàn thành: 20-30 ngày làm việc."]
        self.assertEqual(
            check_output(reply, self.config, evidence=evidence),
            {"blocked": False, "reason": None},
        )

    def test_customer_message_cannot_ground_an_invented_service_claim(self) -> None:
        invented = "Gói Website Basic hỗ trợ máy chủ riêng tại Singapore."
        self.assertEqual(
            check_output(
                invented,
                self.config,
                evidence=[],
                conversation_evidence=[invented],
            ),
            {"blocked": True, "reason": "ungrounded_claim"},
        )

    def test_customer_project_code_can_be_repeated_from_conversation(self) -> None:
        reply = "Mã dự án anh/chị đã chọn là MUA-42 ạ."
        self.assertEqual(
            check_output(
                reply,
                self.config,
                evidence=[],
                conversation_evidence=["Mã dự án của tôi là MUA-42."],
            ),
            {"blocked": False, "reason": None},
        )

    def test_trace_redaction_masks_secrets(self) -> None:
        redacted = redact_output_for_trace(
            "api_key=secret-value; mã OTP 123456; số thẻ 4111 1111 1111 1111"
        )
        self.assertNotIn("secret-value", redacted)
        self.assertNotIn("123456", redacted)
        self.assertNotIn("4111 1111 1111 1111", redacted)

    def test_real_jsonl_trace_is_written_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "trace.jsonl"
            with patch.dict(os.environ, {"AI_CORE_TRACE_PATH": str(destination)}):
                log_trace(
                    {
                        "trace_id": "hoa12-test",
                        "stage": "blocked_output",
                        "blocked_output": "OTP 123456, email sale@example.com",
                    }
                )
            record = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(record["stage"], "blocked_output")
            self.assertNotIn("123456", record["blocked_output"])
            self.assertNotIn("sale@example.com", record["blocked_output"])
            self.assertIn("logged_at", record)

    @patch("ai_core.chat.retrieve", return_value=[])
    @patch("ai_core.chat.check_input", return_value={"blocked": False, "reason": None})
    @patch("ai_core.chat.check_output", return_value={"blocked": True, "reason": "result_guarantee"})
    @patch("ai_core.chat.log_trace")
    def test_chat_replaces_blocked_output_escalates_and_logs(self, log_trace, *_mocks) -> None:
        response = chat(
            {
                "tenant_id": "mima_internal",
                "conversation_id": "b3e1e2b0-1234-4a11-8b11-000000000001",
                "message": "Tư vấn SEO",
                "config_version": 1,
            }
        )
        self.assertTrue(response["reply"].startswith(self.config.refusal_message))
        self.assertIn("xin tên và số điện thoại", response["reply"].casefold())
        self.assertTrue(response["need_human"])
        self.assertTrue(response["guardrail"]["blocked"])
        trace_record = log_trace.call_args.args[0]
        self.assertEqual(trace_record["stage"], "blocked_output")
        self.assertEqual(trace_record["guardrail"]["reason"], "result_guarantee")
        self.assertIsNotNone(trace_record["blocked_output"])

    @patch("ai_core.chat.cache_is_enabled", return_value=False)
    @patch(
        "ai_core.chat.retrieve",
        return_value=[
            {
                "chunk_id": "rag-seo",
                "content": "MIMA cung cấp dịch vụ SEO website.",
                "url": "https://example.com/seo",
                "score": 0.92,
                "metadata": {"title": "SEO", "url": "https://example.com/seo"},
            }
        ],
    )
    @patch("ai_core.chat.check_input", return_value={"blocked": False, "reason": None})
    @patch(
        "ai_core.chat._generate_with_fallback",
        return_value=LLMResult(
            "Ben e dam bao top mot trong 30 ngay.",
            "gemini-3.5-flash-lite",
            100,
            20,
        ),
    )
    @patch("ai_core.chat.check_output", return_value={"blocked": False, "reason": None})
    @patch("ai_core.chat.output_semantic_is_enabled", return_value=True)
    @patch(
        "ai_core.chat.check_output_semantic",
        return_value={
            "blocked": True,
            "reason": "result_guarantee",
            "semantic_check": {
                "status": "checked",
                "candidate_reason": "result_guarantee",
                "confidence": 0.98,
                "min_confidence": 0.85,
                "model": "gemini-3.5-flash-lite",
                "tokens_in": 11,
                "tokens_out": 4,
                "error": None,
            },
        },
    )
    @patch("ai_core.chat.log_trace")
    def test_chat_semantic_guardrail_blocks_additional_variant_and_logs_usage(
        self,
        log_trace,
        *_mocks,
    ) -> None:
        response = chat(
            {
                "tenant_id": "mima_internal",
                "conversation_id": "b3e1e2b0-1234-4a11-8b11-000000000021",
                "message": "Tư vấn SEO cho tôi",
                "config_version": 1,
            }
        )
        self.assertTrue(response["guardrail"]["blocked"])
        self.assertEqual(response["guardrail"]["reason"], "result_guarantee")
        self.assertTrue(response["need_human"])
        self.assertEqual(response["usage"]["tokens_in"], 111)
        self.assertEqual(response["usage"]["tokens_out"], 24)
        trace_record = log_trace.call_args.args[0]
        self.assertEqual(trace_record["stage"], "blocked_output")
        self.assertEqual(
            trace_record["guardrails"]["output_semantic"][0]["candidate_reason"],
            "result_guarantee",
        )

    @patch(
        "ai_core.chat.retrieve",
        return_value=[
            {
                "chunk_id": "rag-price",
                "content": "Gói Website Basic có giá 2.000.000đ.",
                "url": "https://example.com/pricing",
                "score": 0.92,
                "metadata": {"title": "Bảng giá", "url": "https://example.com/pricing"},
            }
        ],
    )
    @patch("ai_core.chat.check_input", return_value={"blocked": False, "reason": None})
    @patch(
        "ai_core.chat._generate_with_fallback",
        return_value=LLMResult(
            "Gói Website Basic có giá 2 triệu đồng.",
            "gemini-3.5-flash-lite",
            100,
            20,
        ),
    )
    @patch("ai_core.chat.log_trace")
    def test_chat_allows_price_grounded_by_current_rag_turn(self, _log, *_mocks) -> None:
        response = chat(
            {
                "tenant_id": "mima_internal",
                "conversation_id": "b3e1e2b0-1234-4a11-8b11-000000000012",
                "message": "Gói Basic giá bao nhiêu?",
                "config_version": 1,
            }
        )
        self.assertEqual(response["reply"], "Gói Website Basic có giá 2 triệu đồng.")
        self.assertFalse(response["guardrail"]["blocked"])
        self.assertEqual(response["sources"][0]["chunk_id"], "rag-price")

    @patch("ai_core.chat.cache_is_enabled", return_value=False)
    @patch(
        "ai_core.chat.retrieve",
        return_value=[
            {
                "chunk_id": "rag-high-tier",
                "content": "Gói Website cao cấp giá 17.000.000đ, có giỏ hàng và hỗ trợ song ngữ.",
                "url": "https://example.com/pricing",
                "score": 0.91,
                "metadata": {"title": "Bảng giá", "url": "https://example.com/pricing"},
            }
        ],
    )
    @patch("ai_core.chat.check_input", return_value={"blocked": False, "reason": None, "need_human": False})
    @patch(
        "ai_core.chat._generate_with_fallback",
        return_value=LLMResult(
            "Dạ, với tài chính 20.000.000đ của anh/chị, gói Website cao cấp giá 17.000.000đ phù hợp ạ.",
            "gemini-3.5-flash-lite",
            100,
            25,
        ),
    )
    @patch("ai_core.chat.log_trace")
    def test_chat_keeps_customer_budget_and_does_not_escalate(self, _log, *_mocks) -> None:
        response = chat(
            {
                "tenant_id": "mima_internal",
                "conversation_id": "b3e1e2b0-1234-4a11-8b11-000000000013",
                "message": "toi la binh tai chinh 20000000",
                "history": [
                    {"role": "user", "content": "thiet ke website bang gia"},
                    {"role": "assistant", "content": "Bên em có nhiều gói website ạ."},
                ],
                "config_version": 1,
            }
        )
        self.assertIn("20.000.000đ", response["reply"])
        self.assertIn("17.000.000đ", response["reply"])
        self.assertFalse(response["guardrail"]["blocked"])
        self.assertFalse(response["need_human"])


if __name__ == "__main__":
    unittest.main()
