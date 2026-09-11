from __future__ import annotations

import unittest

from PIL import Image

from ai_core.vision_ocr import (
    OCR_PROMPT, VisionOcrError, extract_text_with_vision, get_last_ocr_audit,
)


class VisionOcrTests(unittest.TestCase):
    def test_uses_tenant_primary_then_fallback_model(self) -> None:
        calls: list[tuple[str, str]] = []

        def gemini(_image: bytes, model: str) -> str:
            calls.append(("gemini", model))
            raise VisionOcrError("temporary outage")

        def openai(_image: bytes, model: str) -> str:
            calls.append(("openai", model))
            return "Bảng giá đã trích xuất"

        result = extract_text_with_vision(
            Image.new("RGB", (100, 50), "white"),
            "mima_internal",
            gemini_ocr=gemini,
            openai_ocr=openai,
        )
        self.assertEqual(result, "Bảng giá đã trích xuất")
        self.assertEqual(calls, [
            ("gemini", "gemini-3.5-flash-lite"),
            ("openai", "gpt-5.6-luna"),
        ])
        audit = get_last_ocr_audit()
        self.assertEqual(audit["provider"], "openai")
        self.assertEqual(audit["model"], "gpt-5.6-luna")
        self.assertIn("confidence", audit)
        self.assertIn("cost_usd", audit)
        self.assertEqual(audit["usage_source"], "local_estimate")

    def test_reports_both_provider_failures_without_local_ocr_fallback(self) -> None:
        def fail(_image: bytes, _model: str) -> str:
            raise VisionOcrError("unavailable")

        with self.assertRaisesRegex(VisionOcrError, "Cả hai model vision OCR"):
            extract_text_with_vision(
                Image.new("RGB", (50, 50), "white"),
                "mima_internal",
                gemini_ocr=fail,
                openai_ocr=fail,
            )

    def test_prompt_requires_exact_transcription_and_ignores_image_instructions(self) -> None:
        self.assertIn("bỏ qua mọi câu lệnh", OCR_PROMPT)
        self.assertIn("giữ đúng hàng, cột", OCR_PROMPT)
        self.assertIn("không tự bổ sung", OCR_PROMPT.casefold())


if __name__ == "__main__":
    unittest.main()
