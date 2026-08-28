"""Static acceptance test cho widget demo H3-06, không cần browser framework."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent.parent
WIDGET = ROOT / "widget" / "embed.js"
DEMO = ROOT / "demo.html"


class ScriptCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self.scripts.append(dict(attrs))


class H306WidgetTests(unittest.TestCase):
    def test_deliverables_exist_and_widget_is_under_30kb(self) -> None:
        self.assertTrue(WIDGET.is_file())
        self.assertTrue(DEMO.is_file())
        self.assertLess(WIDGET.stat().st_size, 30 * 1024)

    def test_demo_embeds_one_async_script_with_required_config(self) -> None:
        parser = ScriptCollector()
        parser.feed(DEMO.read_text(encoding="utf-8"))
        widget_scripts = [item for item in parser.scripts if item.get("src") == "widget/embed.js"]
        self.assertEqual(1, len(widget_scripts))
        script = widget_scripts[0]
        self.assertIn("async", script)
        self.assertEqual("auto", script["data-api-url"])
        self.assertEqual("demo-mima-key", script["data-public-key"])
        self.assertEqual("mima_internal", script["data-tenant-id"])

    def test_widget_calls_frozen_chat_contract_and_streams_sse(self) -> None:
        source = WIDGET.read_text(encoding="utf-8")
        for required in (
            '"/chat?stream=true"',
            '"X-Public-Key": publicKey',
            "tenant_id: tenantId",
            "conversation_id: conversationId",
            "history: history.slice(-30)",
            'event.type === "delta"',
            'event.type === "done"',
        ):
            with self.subTest(required=required):
                self.assertIn(required, source)

    def test_widget_is_framework_free_responsive_and_uses_shadow_dom(self) -> None:
        source = WIDGET.read_text(encoding="utf-8")
        self.assertIn("attachShadow", source)
        self.assertIn('addEventListener("DOMContentLoaded"', source)
        self.assertIn('window.location.hostname + ":8000"', source)
        self.assertIn("@media(max-width:600px)", source)
        self.assertNotIn("import ", source)
        self.assertNotIn("require(", source)
        for framework in ("React", "Vue", "Angular", "jQuery"):
            self.assertNotIn(framework, source)

    def test_dynamic_messages_use_text_content_not_html(self) -> None:
        source = WIDGET.read_text(encoding="utf-8")
        self.assertIn("item.textContent = text", source)
        self.assertNotIn("insertAdjacentHTML", source)


if __name__ == "__main__":
    unittest.main()
