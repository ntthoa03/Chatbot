from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.robotparser import RobotFileParser

from crawl_chunks import USER_AGENT, crawl, crawlable_page_url, links_from_html, load_robots


def allow_all_robots() -> RobotFileParser:
    robots = RobotFileParser()
    robots.parse(["User-agent: *", "Allow: /"])
    return robots


def html_page(title: str, links: str = "") -> bytes:
    paragraphs = "".join(
        f"<p>Nội dung dịch vụ công khai số {index} đủ dài để tạo knowledge chunk cho chatbot.</p>"
        for index in range(12)
    )
    return f"<html><title>{title}</title><body>{links}{paragraphs}</body></html>".encode()


class H314CrawlerTests(TestCase):
    @patch("crawl_chunks.fetch")
    def test_missing_robots_allows_public_pages_but_explicit_disallow_is_respected(self, fetch) -> None:
        fetch.side_effect = HTTPError("https://sme.test/robots.txt", 404, "missing", {}, None)
        missing, _url, status = load_robots("https://sme.test", timeout=2)
        self.assertEqual(status, "missing_allow")
        self.assertTrue(missing.can_fetch(USER_AGENT, "https://sme.test/dich-vu"))

        fetch.side_effect = None
        fetch.return_value = (
            b"User-agent: *\nDisallow: /private",
            {"content-type": "text/plain"},
            "https://sme.test/robots.txt",
        )
        loaded, _url, status = load_robots("https://sme.test", timeout=2)
        self.assertEqual(status, "loaded")
        self.assertFalse(loaded.can_fetch(USER_AGENT, "https://sme.test/private/customer"))

    def test_homepage_discovery_keeps_safe_same_site_html_only(self) -> None:
        body = b"""<html><body>
        <a href='/dich-vu?utm_source=test'>Dich vu</a>
        <a href='https://evil.test/no'>Ngoai mien</a>
        <a href='/file.pdf'>PDF</a><a href='/logout'>Logout</a>
        </body></html>"""
        links = links_from_html(body, {"content-type": "text/html; charset=utf-8"}, "https://sme.test", "https://sme.test")
        self.assertEqual(links, ["https://sme.test/dich-vu"])
        self.assertTrue(crawlable_page_url("https://sme.test/gioi-thieu", "https://sme.test"))
        self.assertFalse(crawlable_page_url("https://sme.test/wp-admin/a", "https://sme.test"))

    @patch("crawl_chunks.fetch_rendered")
    @patch("crawl_chunks.fetch")
    @patch("crawl_chunks.sitemap_urls", return_value=([], {}, ["404 sitemap"]))
    @patch("crawl_chunks.load_robots")
    def test_no_sitemap_uses_homepage_links_without_headless(
        self, load_robots, _sitemap, fetch, fetch_rendered
    ) -> None:
        load_robots.return_value = (allow_all_robots(), "https://sme.test/robots.txt", "missing_allow")
        homepage = html_page("Trang chủ", "<a href='/dich-vu'>Dịch vụ</a>")
        service = html_page("Dịch vụ")
        fetch.side_effect = [
            (homepage, {"content-type": "text/html"}, "https://sme.test/"),
            (homepage, {"content-type": "text/html"}, "https://sme.test/"),
            (service, {"content-type": "text/html"}, "https://sme.test/dich-vu"),
        ]

        chunks, manifest = crawl(
            base_url="https://sme.test",
            tenant_id="sme_test",
            max_pages=2,
            timeout=2,
            delay_seconds=0,
            target_chars=400,
            overlap_chars=80,
            min_chars=80,
            min_chunks=1,
        )

        self.assertGreaterEqual(len(chunks), 1)
        self.assertEqual(manifest["discovery_strategy"], "homepage_links")
        self.assertEqual(manifest["headless_attempt_count"], 0)
        fetch_rendered.assert_not_called()

    @patch("crawl_chunks.fetch_rendered")
    @patch("crawl_chunks.fetch")
    @patch("crawl_chunks.sitemap_urls", return_value=([], {}, ["404 sitemap"]))
    @patch("crawl_chunks.load_robots")
    def test_js_shell_uses_headless_only_after_static_discovery_fails(
        self, load_robots, _sitemap, fetch, fetch_rendered
    ) -> None:
        load_robots.return_value = (allow_all_robots(), "https://spa.test/robots.txt", "loaded")
        shell = b"<html><title>SPA</title><body><div id='app'></div></body></html>"
        rendered = html_page("SPA", "<a href='/dich-vu'>Dịch vụ</a>")
        fetch.return_value = (shell, {"content-type": "text/html"}, "https://spa.test/")
        fetch_rendered.return_value = (rendered, {"content-type": "text/html"}, "https://spa.test/")

        chunks, manifest = crawl(
            base_url="https://spa.test",
            tenant_id="spa_test",
            max_pages=1,
            timeout=2,
            delay_seconds=0,
            target_chars=400,
            overlap_chars=80,
            min_chars=80,
            min_chunks=1,
            max_headless_pages=2,
        )

        self.assertGreaterEqual(len(chunks), 1)
        self.assertEqual(manifest["discovery_strategy"], "headless_homepage_links")
        self.assertEqual(manifest["headless_attempt_count"], 2)
        self.assertEqual(manifest["headless_success_count"], 2)

    @patch("crawl_chunks.fetch_rendered")
    @patch("crawl_chunks.fetch")
    @patch("crawl_chunks.sitemap_urls")
    @patch("crawl_chunks.load_robots")
    def test_valid_sitemap_never_starts_headless_for_normal_html(
        self, load_robots, sitemap, fetch, fetch_rendered
    ) -> None:
        load_robots.return_value = (allow_all_robots(), "https://sme.test/robots.txt", "loaded")
        sitemap.return_value = (["https://sme.test/dich-vu"], {}, [])
        fetch.return_value = (html_page("Dịch vụ"), {"content-type": "text/html"}, "https://sme.test/dich-vu")

        _chunks, manifest = crawl(
            base_url="https://sme.test",
            tenant_id="sme_test",
            max_pages=1,
            timeout=2,
            delay_seconds=0,
            target_chars=400,
            overlap_chars=80,
            min_chars=80,
            min_chunks=1,
        )

        self.assertEqual(manifest["discovery_strategy"], "sitemap")
        self.assertEqual(manifest["headless_attempt_count"], 0)
        fetch_rendered.assert_not_called()


if __name__ == "__main__":
    import unittest

    unittest.main()
