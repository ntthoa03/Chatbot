"""Acceptance test cho template config theo ngành H3-02."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

import yaml

from ai_core.config import (
    AgentConfig,
    ConfigError,
    _apply_guardrail_profile,
    _apply_industry_template,
    _deep_merge_config,
    _load_industry_template,
    load_config,
)
from ai_core.guardrail.output import check_output
from ai_core.tools import execute_tool, get_tool_schemas, message_may_need_tools


ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
OUTPUT = ROOT / "outputs" / "h3_02"
H301_TENANTS = (
    "mima_internal",
    "phongkham_hyhy",
    "bat_dong_san_phuoc_thinh",
    "giao_duc_haiyan",
    "thuc_pham_thien_minh",
)


class H302TemplateContractTests(unittest.TestCase):
    def test_three_primary_templates_follow_mima_portfolio_counts(self) -> None:
        evidence = json.loads(
            (OUTPUT / "mima_portfolio_industry_counts.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            ["construction", "commerce", "services"],
            evidence["primary_templates"],
        )
        self.assertEqual([13, 11, 10], [row["projects"] for row in evidence["counts"][:3]])
        for template_id in evidence["primary_templates"]:
            self.assertTrue((TEMPLATES / f"{template_id}.yaml").is_file())

    def test_each_primary_template_builds_a_valid_tenant_config(self) -> None:
        for template_id in ("construction", "commerce", "services"):
            with self.subTest(template_id=template_id):
                raw = {
                    "tenant_id": f"acceptance_{template_id}",
                    "industry_template": template_id,
                    "persona": {"bot_name": f"Bot {template_id}"},
                }
                resolved = _apply_guardrail_profile(_apply_industry_template(raw))
                config = AgentConfig.model_validate(resolved)
                self.assertEqual(template_id, config.industry_template)

    def test_templates_have_required_industry_behaviour(self) -> None:
        construction = _load_industry_template("construction")
        commerce = _load_industry_template("commerce")
        medical = _load_industry_template("medical")
        retail = _load_industry_template("retail")
        services = _load_industry_template("services")
        self.assertIn("thi_cong_theo_du_an", construction["pricing"]["must_contact"])
        self.assertTrue(
            any(
                rule["reason"] == "construction_unverified_guarantee"
                for rule in construction["guardrails"]["output"]["rules"]
            )
        )
        self.assertIn("check_order", commerce["enabled_tools"])
        self.assertIn("hop_dong_phan_phoi", commerce["pricing"]["must_contact"])
        self.assertEqual("medical_clinic", medical["guardrail_profile"])
        self.assertEqual(["request_appointment"], medical["enabled_tools"])
        self.assertIn("check_order", retail["enabled_tools"])
        self.assertEqual(3, services["lead"]["ask_after_turns"])
        self.assertTrue(
            any(
                "hợp đồng hoặc khiếu nại" in item
                for item in services["guardrails"]["escalate_when"]
            )
        )

    def test_construction_template_blocks_unverified_guarantee(self) -> None:
        raw = {
            "tenant_id": "acceptance_construction_guardrail",
            "industry_template": "construction",
            "persona": {"bot_name": "Bot xây dựng"},
        }
        config = AgentConfig.model_validate(
            _apply_guardrail_profile(_apply_industry_template(raw))
        )
        result = check_output(
            "Bên em cam kết công trình chắc chắn đúng tiến độ và không phát sinh.",
            config,
        )
        self.assertTrue(result["blocked"])
        self.assertEqual("construction_unverified_guarantee", result["reason"])

    def test_dicts_merge_deep_but_lists_are_replaced(self) -> None:
        merged = _deep_merge_config(
            {"persona": {"tone": "base", "reply_length": "3-6"}, "enabled_tools": ["a"]},
            {"persona": {"tone": "tenant"}, "enabled_tools": []},
        )
        self.assertEqual({"tone": "tenant", "reply_length": "3-6"}, merged["persona"])
        self.assertEqual([], merged["enabled_tools"])

    def test_invalid_or_unknown_template_fails_closed(self) -> None:
        with self.assertRaises(ConfigError):
            _apply_industry_template({"industry_template": "../medical"})
        with self.assertRaises(ConfigError):
            _apply_industry_template({"industry_template": "not_registered"})

    def test_existing_five_tenants_remain_backward_compatible(self) -> None:
        for tenant_id in H301_TENANTS:
            with self.subTest(tenant_id=tenant_id):
                self.assertEqual(tenant_id, load_config(tenant_id).tenant_id)


class H302TenantSixAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config("tenant6_demo_services")

    def test_tenant_six_yaml_contains_only_private_fields(self) -> None:
        raw = yaml.safe_load(
            (ROOT / "tenants" / "tenant6_demo_services.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(
            {"tenant_id", "industry_template", "persona", "pricing", "contact", "knowledge"},
            set(raw),
        )
        self.assertEqual({"bot_name"}, set(raw["persona"]))

    def test_tenant_six_inherits_service_defaults_and_overrides_private_fields(self) -> None:
        self.assertEqual("services", self.config.industry_template)
        self.assertEqual("Trợ lý Dịch vụ Tenant 6", self.config.persona.bot_name)
        self.assertEqual("em", self.config.persona.self_address)
        self.assertEqual(3, self.config.lead.ask_after_turns)
        self.assertEqual("gemini-embedding-001", self.config.embedding_policy.primary.model)
        self.assertEqual(["dich_vu_co_gia_cong_khai"], self.config.pricing.can_quote)

    def test_measured_config_onboarding_is_under_thirty_minutes(self) -> None:
        timing = json.loads(
            (OUTPUT / "onboarding_tenant6.json").read_text(encoding="utf-8")
        )
        self.assertTrue(timing["passed"])
        self.assertLess(timing["elapsed_seconds"], timing["target_seconds"])


class H302IndustryToolTests(unittest.TestCase):
    def test_medical_and_retail_tools_are_registered(self) -> None:
        names = {schema["name"] for schema in get_tool_schemas(["request_appointment", "check_order"])}
        self.assertEqual({"request_appointment", "check_order"}, names)
        self.assertTrue(message_may_need_tools("kiểm tra đơn DH-12345", ["check_order"]))

    def test_order_tool_never_invents_status_without_backend(self) -> None:
        result = execute_tool("check_order", {"order_code": "DH-12345"}, ["check_order"])
        self.assertEqual("handoff_required", result["status"])
        self.assertTrue(result["requires_human"])
        self.assertNotIn("đã giao", result["message"].casefold())


if __name__ == "__main__":
    unittest.main()
