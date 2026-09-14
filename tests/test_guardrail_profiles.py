from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

import yaml

from ai_core.config import ConfigError, load_config


ROOT = Path(__file__).resolve().parents[1]


class GuardrailProfileTests(unittest.TestCase):
    def _tenant_yaml(self, tenant_id: str = "mima_internal") -> dict:
        path = ROOT / "tenants" / f"{tenant_id}.yaml"
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def test_real_tenants_are_short_but_resolve_full_rules(self) -> None:
        # Tenant không còn phải chứa regex; loader phải phục hồi đủ rule từ profile.
        mima_raw = self._tenant_yaml("mima_internal")
        clinic_raw = self._tenant_yaml("phongkham_hyhy")
        self.assertNotIn("output", mima_raw["guardrails"])
        self.assertNotIn("output", clinic_raw["guardrails"])
        self.assertIn("Cam kết kết quả hoặc thứ hạng", mima_raw["guardrails"]["forbidden"])
        self.assertIn("Chẩn đoán bệnh qua chat", clinic_raw["guardrails"]["forbidden"])

        mima = load_config("mima_internal")
        clinic = load_config("phongkham_hyhy")
        self.assertEqual(mima.guardrail_profile, "digital_agency")
        self.assertEqual(clinic.guardrail_profile, "medical_clinic")
        self.assertEqual(len(mima.guardrails.output.rules), 8)
        self.assertEqual(len(clinic.guardrails.output.rules), 7)
        self.assertTrue(mima.guardrails.output.grounding.enabled)
        self.assertTrue(clinic.guardrails.output.grounding.enabled)
        self.assertIn("result_guarantee", mima.guardrails.forbidden_rule_ids)
        self.assertIn("medical_diagnosis", clinic.guardrails.forbidden_rule_ids)

    def test_vietnamese_label_maps_to_stable_reason(self) -> None:
        raw = self._tenant_yaml()
        raw["guardrails"]["forbidden"] = ["Cam kết kết quả hoặc thứ hạng"]
        with patch("ai_core.config._load_yaml", return_value=raw):
            config = load_config("mima_internal")
        self.assertEqual(config.guardrails.forbidden_rule_ids, ["result_guarantee"])
        self.assertEqual([rule.reason for rule in config.guardrails.output.rules], ["result_guarantee"])

    def test_common_profile_can_be_used_without_copying_regex(self) -> None:
        raw = self._tenant_yaml()
        raw["guardrail_profile"] = "common"
        raw["guardrails"]["forbidden"] = [
            "banking_secret_request",
            "technical_information_disclosure",
        ]
        with patch("ai_core.config._load_yaml", return_value=raw):
            config = load_config("mima_internal")
        self.assertEqual(
            [rule.reason for rule in config.guardrails.output.rules],
            ["banking_secret_request", "technical_information_disclosure"],
        )
        self.assertFalse(config.guardrails.output.grounding.enabled)

    def test_forbidden_filters_profile_and_prompt_descriptions(self) -> None:
        raw = self._tenant_yaml()
        raw["guardrails"]["forbidden"] = ["result_guarantee"]
        with patch("ai_core.config._load_yaml", return_value=raw):
            config = load_config("mima_internal")
        self.assertEqual([rule.reason for rule in config.guardrails.output.rules], ["result_guarantee"])
        self.assertEqual(len(config.guardrails.forbidden), 1)
        self.assertIn("Cam kết kết quả", config.guardrails.forbidden[0])
        self.assertFalse(config.guardrails.output.grounding.enabled)

    def test_unknown_forbidden_reason_fails_closed(self) -> None:
        raw = self._tenant_yaml()
        raw["guardrails"]["forbidden"] = ["rule_khong_ton_tai"]
        with patch("ai_core.config._load_yaml", return_value=raw):
            with self.assertRaisesRegex(ConfigError, "rule_khong_ton_tai"):
                load_config("mima_internal")

    def test_missing_profile_fails_closed(self) -> None:
        raw = self._tenant_yaml()
        raw["guardrail_profile"] = "profile_khong_ton_tai"
        with patch("ai_core.config._load_yaml", return_value=raw):
            with self.assertRaisesRegex(ConfigError, "profile_khong_ton_tai"):
                load_config("mima_internal")

    def test_tenant_output_can_override_one_profile_rule(self) -> None:
        raw = self._tenant_yaml()
        raw["guardrails"]["forbidden"] = ["result_guarantee"]
        base = next(
            rule
            for rule in load_config("mima_internal").guardrails.output.rules
            if rule.reason == "result_guarantee"
        ).model_dump()
        override = deepcopy(base)
        override["description"] = "Mô tả override riêng của tenant"
        raw["guardrails"]["output"] = {"rules": [override]}
        with patch("ai_core.config._load_yaml", return_value=raw):
            config = load_config("mima_internal")
        self.assertEqual(config.guardrails.output.rules[0].description, override["description"])


if __name__ == "__main__":
    unittest.main()
