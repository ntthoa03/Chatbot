"""Kiểm chứng offline các artifact bắt buộc của H3-01.

Test không gọi mạng hay LLM: nó kiểm tra config, index, metadata, báo cáo smoke đã
được tạo bằng embedding thật và tiêu chí thời gian tenant 5 nhanh hơn tenant 3.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
import unittest

import numpy as np

from ai_core.config import load_config


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "outputs" / "h3_01"

TENANTS = {
    "mima_internal": ("digital_agency", ROOT / "index"),
    "phongkham_hyhy": ("medical_clinic", ROOT / "outputs" / "h2_04" / "index_phongkham_hyhy"),
    "bat_dong_san_phuoc_thinh": ("real_estate", OUTPUT / "index_bat_dong_san_phuoc_thinh"),
    "giao_duc_haiyan": ("education", OUTPUT / "index_giao_duc_haiyan"),
    "thuc_pham_thien_minh": ("food", OUTPUT / "index_thuc_pham_thien_minh"),
}


class H301FiveTenantTests(unittest.TestCase):
    def test_five_distinct_tenants_and_industries_load_from_config(self) -> None:
        self.assertEqual(5, len(TENANTS))
        self.assertEqual(5, len({industry for industry, _ in TENANTS.values()}))
        for tenant_id in TENANTS:
            with self.subTest(tenant_id=tenant_id):
                config = load_config(tenant_id)
                self.assertEqual(tenant_id, config.tenant_id)

    def test_each_index_contains_only_its_owner_tenant(self) -> None:
        for tenant_id, (_, index_dir) in TENANTS.items():
            with self.subTest(tenant_id=tenant_id):
                manifest = json.loads((index_dir / "manifest.json").read_text(encoding="utf-8"))
                metadata = json.loads((index_dir / "metadata.json").read_text(encoding="utf-8"))
                vectors = np.load(index_dir / "vectors.npy", allow_pickle=False)
                self.assertEqual([tenant_id], manifest["tenants"])
                self.assertEqual(manifest["record_count"], len(metadata))
                self.assertEqual((len(metadata), manifest["dimension"]), vectors.shape)
                self.assertEqual({tenant_id}, {row["tenant_id"] for row in metadata})

    def test_three_new_crawls_meet_declared_minimum(self) -> None:
        for tenant_id in (
            "bat_dong_san_phuoc_thinh",
            "giao_duc_haiyan",
            "thuc_pham_thien_minh",
        ):
            with self.subTest(tenant_id=tenant_id):
                manifest = json.loads(
                    (OUTPUT / f"{tenant_id}_crawl_manifest.json").read_text(encoding="utf-8")
                )
                self.assertEqual(tenant_id, manifest["tenant_id"])
                self.assertTrue(manifest["meets_minimum_chunks"])
                self.assertGreaterEqual(manifest["chunk_count"], manifest["minimum_required_chunks"])

    def test_five_smoke_reports_have_results_and_pass_isolation(self) -> None:
        for tenant_id in TENANTS:
            with self.subTest(tenant_id=tenant_id):
                report = json.loads((OUTPUT / f"smoke_{tenant_id}.json").read_text(encoding="utf-8"))
                self.assertEqual(tenant_id, report["tenant_id"])
                self.assertTrue(report["isolation_probe"]["passed"])
                self.assertEqual(3, len(report["queries"]))
                self.assertTrue(all(row["result_count"] > 0 for row in report["queries"]))

    def test_tenant_five_onboards_faster_than_tenant_three(self) -> None:
        with (OUTPUT / "onboarding_times.csv").open(encoding="utf-8", newline="") as handle:
            rows = {row["tenant_id"]: row for row in csv.DictReader(handle)}
        tenant_3 = float(rows["bat_dong_san_phuoc_thinh"]["onboarding_technical_seconds"])
        tenant_5 = float(rows["thuc_pham_thien_minh"]["onboarding_technical_seconds"])
        self.assertLess(tenant_5, tenant_3)


if __name__ == "__main__":
    unittest.main()
