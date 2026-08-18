"""Crawl public website pages and produce tenant-scoped knowledge chunks.

The crawler is deliberately tenant-agnostic: URL, tenant ID, output path and
chunking limits are all command-line parameters.  It respects robots.txt,
prefers sitemap URLs, stays on the selected host and never submits forms.

Example (H2-04):
    python crawl_chunks.py \
      --base-url https://phongkhamhyhy.com \
      --tenant-id phongkham_hyhy \
      --output outputs/h2_04/phongkham_hyhy_chunks.json \
      --manifest outputs/h2_04/crawl_manifest.json \
      --max-pages 100 --min-chunks 100
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser
import uuid
import xml.etree.ElementTree as ET

from ai_core.models import KnowledgeChunk


USER_AGENT = "TenantKnowledgeCrawler/1.0 (+internal evaluation)"
BLOCK_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th"}
IGNORED_TAGS = {"script", "style", "noscript", "svg", "form", "button", "select", "textarea"}
SPACE_RE = re.compile(r"\s+")
TRACKING_PARAMS = {"fbclid", "gclid", "utm_campaign", "utm_content", "utm_medium", "utm_source", "utm_term"}


class CrawlError(RuntimeError):
    pass


@dataclass(frozen=True)
class Page:
    url: str
    title: str
    blocks: tuple[str, ...]
    updated_at: str


class ContentParser(HTMLParser):
    """Small dependency-free extractor for headings, paragraphs and list/table rows."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._title_parts: list[str] = []
        self._block_parts: list[str] = []
        self._blocks: list[str] = []
        self._ignored_depth = 0
        self._active_block_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in IGNORED_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if tag == "title":
            self._in_title = True
        if tag in BLOCK_TAGS:
            if self._active_block_depth == 0:
                self._block_parts = []
            self._active_block_depth += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in IGNORED_TAGS:
            self._ignored_depth = max(0, self._ignored_depth - 1)
            return
        if self._ignored_depth:
            return
        if tag == "title":
            self._in_title = False
            self.title = clean_text(" ".join(self._title_parts))
        if tag in BLOCK_TAGS and self._active_block_depth:
            self._active_block_depth -= 1
            if self._active_block_depth == 0:
                value = clean_text(" ".join(self._block_parts))
                if len(value) >= 20:
                    self._blocks.append(value)
                self._block_parts = []

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        if self._in_title:
            self._title_parts.append(data)
        if self._active_block_depth:
            self._block_parts.append(data)

    @property
    def blocks(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self._blocks))


def clean_text(value: str) -> str:
    return SPACE_RE.sub(" ", value).strip()


def canonical_url(value: str) -> str:
    value, _fragment = urldefrag(value.strip())
    parsed = urlparse(value)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    port = parsed.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query_parts = []
    for item in parsed.query.split("&"):
        key = item.partition("=")[0].lower()
        if item and key not in TRACKING_PARAMS:
            query_parts.append(item)
    return urlunparse((scheme, netloc, path, "", "&".join(query_parts), ""))


def fetch(url: str, *, timeout: float, user_agent: str = USER_AGENT) -> tuple[bytes, dict[str, str], str]:
    request = Request(url, headers={"User-Agent": user_agent, "Accept": "text/html,application/xml,text/xml;q=0.9,*/*;q=0.1"})
    with urlopen(request, timeout=timeout) as response:
        headers = {key.lower(): value for key, value in response.headers.items()}
        return response.read(), headers, canonical_url(response.url)


def parse_sitemap(data: bytes, sitemap_url: str) -> tuple[list[str], list[str], dict[str, str]]:
    root = ET.fromstring(data)
    kind = root.tag.rsplit("}", 1)[-1]
    urls: list[str] = []
    child_sitemaps: list[str] = []
    lastmods: dict[str, str] = {}
    if kind == "sitemapindex":
        for node in root:
            loc = next((clean_text(child.text or "") for child in node if child.tag.rsplit("}", 1)[-1] == "loc"), "")
            if loc:
                child_sitemaps.append(canonical_url(urljoin(sitemap_url, loc)))
    elif kind == "urlset":
        for node in root:
            values = {child.tag.rsplit("}", 1)[-1]: clean_text(child.text or "") for child in node}
            if values.get("loc"):
                url = canonical_url(urljoin(sitemap_url, values["loc"]))
                urls.append(url)
                if values.get("lastmod"):
                    lastmods[url] = values["lastmod"][:10]
    else:
        raise CrawlError(f"Sitemap root không hỗ trợ: {kind}")
    return urls, child_sitemaps, lastmods


def sitemap_urls(base_url: str, *, timeout: float, max_sitemaps: int = 20) -> tuple[list[str], dict[str, str], list[str]]:
    queue = [urljoin(base_url.rstrip("/") + "/", "sitemap.xml")]
    visited: set[str] = set()
    urls: list[str] = []
    lastmods: dict[str, str] = {}
    errors: list[str] = []
    while queue and len(visited) < max_sitemaps:
        sitemap_url = canonical_url(queue.pop(0))
        if sitemap_url in visited:
            continue
        visited.add(sitemap_url)
        try:
            body, _headers, _final = fetch(sitemap_url, timeout=timeout)
            page_urls, children, dates = parse_sitemap(body, sitemap_url)
            urls.extend(page_urls)
            lastmods.update(dates)
            queue.extend(children)
        except (HTTPError, URLError, TimeoutError, ET.ParseError, CrawlError) as exc:
            errors.append(f"{sitemap_url}: {type(exc).__name__}: {exc}")
    return list(dict.fromkeys(urls)), lastmods, errors


def same_site(url: str, base_url: str) -> bool:
    candidate = (urlparse(url).hostname or "").lower().removeprefix("www.")
    base = (urlparse(base_url).hostname or "").lower().removeprefix("www.")
    return candidate == base and urlparse(url).scheme in {"http", "https"}


def extract_page(body: bytes, headers: dict[str, str], url: str, updated_at: str | None) -> Page:
    content_type = headers.get("content-type", "")
    if "html" not in content_type.lower():
        raise CrawlError(f"Content-Type không phải HTML: {content_type or 'missing'}")
    charset_match = re.search(r"charset=([^;\s]+)", content_type, flags=re.I)
    charset = charset_match.group(1).strip('"\'') if charset_match else "utf-8"
    html = body.decode(charset, errors="replace")
    parser = ContentParser()
    parser.feed(html)
    title = parser.title or urlparse(url).path.strip("/").replace("-", " ") or urlparse(url).hostname or url
    date = updated_at or datetime.now(timezone.utc).date().isoformat()
    return Page(url=url, title=title, blocks=parser.blocks, updated_at=date)


def remove_boilerplate(pages: Iterable[Page], *, repeat_threshold: int = 5) -> list[Page]:
    pages = list(pages)
    frequency = Counter(block.casefold() for page in pages for block in set(page.blocks))
    cleaned = []
    for page in pages:
        blocks = tuple(block for block in page.blocks if frequency[block.casefold()] < repeat_threshold)
        cleaned.append(Page(page.url, page.title, blocks, page.updated_at))
    return cleaned


def infer_type(url: str, title: str) -> str:
    value = f"{url} {title}".lower()
    if any(token in value for token in ("chinh-sach", "bao-mat", "dieu-khoan")):
        return "policy"
    if any(token in value for token in ("bang-gia", "gia-kham", "chi-phi")):
        return "pricing"
    if any(token in value for token in ("faq", "cau-hoi", "hoi-dap")):
        return "faq"
    if any(token in value for token in ("dich-vu", "goi-kham", "chuyen-khoa", "kham-")):
        return "service"
    return "blog"


def split_blocks(blocks: Iterable[str], *, target_chars: int, overlap_chars: int, min_chars: int) -> list[str]:
    if overlap_chars >= target_chars:
        raise ValueError("overlap_chars phải nhỏ hơn target_chars")
    chunks: list[str] = []
    current: list[str] = []
    current_size = 0
    for raw_block in blocks:
        block = clean_text(raw_block)
        if not block:
            continue
        pieces = [block[i : i + target_chars] for i in range(0, len(block), target_chars)]
        for piece in pieces:
            added = len(piece) + (1 if current else 0)
            if current and current_size + added > target_chars:
                chunks.append("\n".join(current))
                overlap: list[str] = []
                size = 0
                for previous in reversed(current):
                    if size + len(previous) > overlap_chars:
                        break
                    overlap.insert(0, previous)
                    size += len(previous) + 1
                current = overlap
                current_size = sum(len(item) for item in current) + max(0, len(current) - 1)
            current.append(piece)
            current_size += len(piece) + (1 if len(current) > 1 else 0)
    if current:
        chunks.append("\n".join(current))
    if len(chunks) > 1 and len(chunks[-1]) < min_chars:
        tail = chunks.pop()
        chunks[-1] = f"{chunks[-1]}\n{tail}"
    return [chunk for chunk in chunks if len(chunk) >= min_chars]


def page_chunks(page: Page, tenant_id: str, *, target_chars: int, overlap_chars: int, min_chars: int) -> list[dict]:
    output: list[dict] = []
    for position, content in enumerate(
        split_blocks(page.blocks, target_chars=target_chars, overlap_chars=overlap_chars, min_chars=min_chars)
    ):
        stable_key = f"{tenant_id}|{page.url}|{position}|{content}"
        item = {
            "tenant_id": tenant_id,
            "chunk_id": str(uuid.uuid5(uuid.NAMESPACE_URL, stable_key)),
            "content": content,
            "metadata": {
                "url": page.url,
                "title": page.title,
                "type": infer_type(page.url, page.title),
                "updated_at": page.updated_at,
            },
        }
        output.append(KnowledgeChunk.model_validate(item).model_dump(mode="json"))
    return output


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
    temporary.replace(path)


def crawl(
    *,
    base_url: str,
    tenant_id: str,
    max_pages: int,
    timeout: float,
    delay_seconds: float,
    target_chars: int,
    overlap_chars: int,
    min_chars: int,
    min_chunks: int,
) -> tuple[list[dict], dict]:
    base_url = canonical_url(base_url)
    if not urlparse(base_url).scheme or not urlparse(base_url).hostname:
        raise CrawlError("--base-url phải là URL tuyệt đối http/https")

    robots_url = urljoin(base_url.rstrip("/") + "/", "robots.txt")
    robots = RobotFileParser()
    robots.set_url(robots_url)
    try:
        robots.read()
    except (HTTPError, URLError, TimeoutError) as exc:
        raise CrawlError(f"Không đọc được robots.txt; dừng để tránh crawl sai phép: {exc}") from exc

    discovered, lastmods, sitemap_errors = sitemap_urls(base_url, timeout=timeout)
    candidates = [url for url in discovered if same_site(url, base_url) and robots.can_fetch(USER_AGENT, url)]
    if not candidates:
        raise CrawlError("Sitemap không có URL cùng miền được robots.txt cho phép.")

    pages: list[Page] = []
    seen_final_urls: set[str] = set()
    errors: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for url in candidates[:max_pages]:
        try:
            body, headers, final_url = fetch(url, timeout=timeout)
            if not same_site(final_url, base_url):
                skipped.append({"url": url, "reason": f"redirect_outside_site:{final_url}"})
                continue
            if not robots.can_fetch(USER_AGENT, final_url):
                skipped.append({"url": url, "reason": "robots_disallow_after_redirect"})
                continue
            if final_url in seen_final_urls:
                skipped.append({"url": url, "reason": f"duplicate_final_url:{final_url}"})
                continue
            page = extract_page(body, headers, final_url, lastmods.get(url))
            if sum(len(block) for block in page.blocks) < min_chars:
                skipped.append({"url": url, "reason": "too_little_public_text"})
                continue
            pages.append(page)
            seen_final_urls.add(final_url)
        except (HTTPError, URLError, TimeoutError, UnicodeError, CrawlError) as exc:
            errors.append({"url": url, "error": f"{type(exc).__name__}: {exc}"})
        if delay_seconds:
            time.sleep(delay_seconds)

    cleaned_pages = remove_boilerplate(pages)
    raw_chunks = [
        chunk
        for page in cleaned_pages
        for chunk in page_chunks(
            page,
            tenant_id,
            target_chars=target_chars,
            overlap_chars=overlap_chars,
            min_chars=min_chars,
        )
    ]
    chunks: list[dict] = []
    seen_content: set[str] = set()
    for chunk in raw_chunks:
        digest = hashlib.sha256(chunk["content"].encode("utf-8")).hexdigest()
        if digest in seen_content:
            continue
        seen_content.add(digest)
        chunks.append(chunk)
    manifest = {
        "schema_version": "h2-04.crawl.v1",
        "crawled_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "tenant_id": tenant_id,
        "user_agent": USER_AGENT,
        "robots_url": robots_url,
        "sitemap_url": urljoin(base_url.rstrip("/") + "/", "sitemap.xml"),
        "sitemap_url_count": len(discovered),
        "allowed_candidate_count": len(candidates),
        "requested_page_limit": max_pages,
        "fetched_page_count": len(pages),
        "chunk_count": len(chunks),
        "duplicate_content_chunks_removed": len(raw_chunks) - len(chunks),
        "minimum_required_chunks": min_chunks,
        "meets_minimum_chunks": len(chunks) >= min_chunks,
        "chunk_size": target_chars,
        "chunk_overlap": overlap_chars,
        "min_chunk_chars": min_chars,
        "sitemap_errors": sitemap_errors,
        "page_errors": errors,
        "skipped": skipped,
        "source_urls": [page.url for page in pages],
    }
    return chunks, manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawl website công khai thành knowledge chunks theo tenant")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--min-chunks", type=int, default=100)
    parser.add_argument("--chunk-size", type=int, default=900)
    parser.add_argument("--chunk-overlap", type=int, default=120)
    parser.add_argument("--min-chars", type=int, default=160)
    parser.add_argument("--delay-seconds", type=float, default=0.15)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()
    try:
        chunks, manifest = crawl(
            base_url=args.base_url,
            tenant_id=args.tenant_id,
            max_pages=max(1, args.max_pages),
            timeout=max(1.0, args.timeout),
            delay_seconds=max(0.0, args.delay_seconds),
            target_chars=max(200, args.chunk_size),
            overlap_chars=max(0, args.chunk_overlap),
            min_chars=max(40, args.min_chars),
            min_chunks=max(1, args.min_chunks),
        )
        write_json(Path(args.output), chunks)
        write_json(Path(args.manifest), manifest)
    except (CrawlError, ValueError) as exc:
        print(f"❌ Lỗi crawl: {exc}")
        raise SystemExit(1) from None
    print(
        f"Xong: {manifest['fetched_page_count']} trang, {len(chunks)} chunks cho tenant "
        f"'{args.tenant_id}'. Chunks: {args.output}; manifest: {args.manifest}"
    )
    if not manifest["meets_minimum_chunks"]:
        print(
            f"❌ Chưa đạt tối thiểu {manifest['minimum_required_chunks']} chunks; "
            "xem page_errors/skipped trong manifest."
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
