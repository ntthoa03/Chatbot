from __future__ import annotations

import unittest

from ai_core.config import load_config
from ai_core.guardrail.output import check_output
from ai_core.guardrail.pricing import (
    contains_priced_item,
    currency_amounts,
    currency_mentions,
    customer_budget_amounts,
    is_budget_context,
)


class SharedPricingParserTests(unittest.TestCase):
    def test_budget_variants_are_normalized_without_tenant_context(self) -> None:
        self.assertEqual(customer_budget_amounts(["ns <= 10tr"]), {10_000_000})
        self.assertEqual(customer_budget_amounts(["tai chinh 20 000 000"]), {20_000_000})
        self.assertEqual(
            customer_budget_amounts(["voi so tien 5tr thi co dich vu nao"]),
            {5_000_000},
        )
        self.assertEqual(customer_budget_amounts(["budget under 9.5m"]), {9_500_000})
        self.assertEqual(
            customer_budget_amounts(["tu van thiet ke app tu 15-30tr"]),
            {15_000_000, 30_000_000},
        )
        self.assertEqual(
            customer_budget_amounts(["tu van cho toi dich vu 15-30tr"]),
            {15_000_000, 30_000_000},
        )
        self.assertEqual(
            customer_budget_amounts(["price range from 5tr to 10tr"]),
            {5_000_000, 10_000_000},
        )

    def test_comparison_symbol_without_words_is_a_budget_constraint(self) -> None:
        self.assertEqual(customer_budget_amounts(["<10000000"]), {10_000_000})
        self.assertEqual(customer_budget_amounts([">= 15tr"]), {15_000_000})

    def test_package_price_is_not_treated_as_customer_budget(self) -> None:
        self.assertEqual(customer_budget_amounts(["Gói Basic giá 10 triệu"]), set())
        text = "Với ngân sách 10.000.000đ, gói Basic giá 10.000.000đ."
        mentions = currency_mentions(text)
        self.assertTrue(is_budget_context(text, mentions[0][0], mentions[0][1]))
        self.assertFalse(is_budget_context(text, mentions[1][0], mentions[1][1]))

    def test_currency_and_priced_item_helpers_are_industry_neutral(self) -> None:
        self.assertEqual(currency_amounts("Giá 1,5 triệu đồng"), {1_500_000})
        for text in ("gói khám", "dịch vụ du lịch", "treatment plan", "course subscription"):
            with self.subTest(text=text):
                self.assertTrue(contains_priced_item(text))

    def test_plural_package_budget_filter_is_not_a_service_price_assignment(self) -> None:
        question = "có những gói nào giá dưới 15tr mô tả hết đi"
        reply = (
            "Bên em có các gói website với mức giá dưới 15.000.000đ. "
            "Gói Basic giá 2.000.000đ và gói Khởi nghiệp giá 9.000.000đ."
        )
        evidence = [
            "Gói Website Basic giá 2.000.000đ.",
            "Gói Website khởi nghiệp giá 9.000.000đ.",
        ]

        self.assertEqual(customer_budget_amounts([question]), {15_000_000})
        self.assertEqual(
            check_output(
                reply,
                load_config("mima_internal", 1),
                evidence=evidence,
                conversation_evidence=[question],
            ),
            {"blocked": False, "reason": None},
        )

    def test_both_ends_of_dash_budget_range_keep_budget_context(self) -> None:
        text = "với khoảng ngân sách 15.000.000đ–30.000.000đ"
        mentions = currency_mentions(text)

        self.assertEqual([amount for _, _, amount in mentions], [15_000_000, 30_000_000])
        self.assertTrue(
            all(is_budget_context(text, start, end) for start, end, _ in mentions)
        )


if __name__ == "__main__":
    unittest.main()
