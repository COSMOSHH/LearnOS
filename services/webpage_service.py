import re
from html import unescape
from urllib.parse import urljoin, urlparse, urlunparse

import requests

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover - optional dependency
    BeautifulSoup = None


REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36 LearnOS/1.0"
    )
}

META_CHARSET_PATTERN = re.compile(br"<meta[^>]+charset=['\"]?([A-Za-z0-9._-]+)", re.IGNORECASE)

CONTENT_CONTAINER_PATTERN = re.compile(
    r"(article|content|post|entry|markdown|doc|docs|readme|page|main)",
    re.IGNORECASE,
)

TAG_CLEANUP_PATTERNS = [
    re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<style\b[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<noscript\b[^>]*>.*?</noscript>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<svg\b[^>]*>.*?</svg>", re.IGNORECASE | re.DOTALL),
]

BLOCK_TAG_PATTERN = re.compile(r"</?(p|div|section|article|main|br|li|ul|ol|h[1-6]|pre|blockquote|tr)\b[^>]*>", re.IGNORECASE)
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
MULTI_BLANK_PATTERN = re.compile(r"\n{3,}")
TITLE_PATTERN = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
H1_PATTERN = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
ANCHOR_HREF_PATTERN = re.compile(r"<a[^>]+href=['\"](.*?)['\"]", re.IGNORECASE)
BLOCKED_LINK_TOKENS = {"tag", "tags", "category", "categories", "about", "archive", "archives", "search", "feed"}


def fetch_webpage_content(url: str, timeout: int = 20) -> dict:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http/https URLs are supported.")

    response = requests.get(url, headers=REQUEST_HEADERS, timeout=timeout)
    response.raise_for_status()
    html = _decode_html(response)
    if not html.strip():
        raise ValueError("The webpage returned empty HTML.")

    title = ""
    content = ""
    sections = []

    if BeautifulSoup is not None:
        title, content, sections = _extract_with_bs4(html)

    if not content:
        title = title or _extract_title_with_regex(html)
        content = _extract_content_with_regex(html)
        sections = _build_fallback_sections(title or parsed.netloc, content)

    content = _normalize_text(content)
    title = _normalize_text(title).splitlines()[0] if title else ""
    title = title.lstrip("# ").strip()

    if not content:
        raise ValueError("Unable to extract readable article content from the webpage.")

    if not title:
        title = parsed.netloc

    return {
        "title": title[:200],
        "text": content,
        "source_url": response.url,
        "site_name": parsed.netloc,
        "sections": sections,
    }


def fetch_webpage_batch(url: str, max_pages: int = 5, timeout: int = 20) -> dict:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http/https URLs are supported.")

    response = requests.get(url, headers=REQUEST_HEADERS, timeout=timeout)
    response.raise_for_status()
    html = _decode_html(response)
    source_url = response.url
    site_name = urlparse(source_url).netloc

    candidate_urls = _extract_same_site_links(html, base_url=source_url)
    ranked_urls = sorted(candidate_urls, key=lambda item: _score_candidate_url(item, source_url), reverse=True)
    selected_urls = []
    seen = set()
    for candidate in ranked_urls:
        if candidate in seen:
            continue
        seen.add(candidate)
        selected_urls.append(candidate)
        if len(selected_urls) >= max(1, int(max_pages or 5)):
            break

    pages = []
    if not selected_urls and _is_probable_article_url(source_url, source_url):
        selected_urls = [source_url]

    for candidate in selected_urls:
        try:
            pages.append(fetch_webpage_content(candidate, timeout=timeout))
        except Exception:
            continue

    if not pages:
        raise ValueError("Unable to discover readable same-site articles from the provided webpage.")

    return {
        "source_url": source_url,
        "site_name": site_name,
        "pages": pages,
        "discovered_urls": selected_urls,
    }


def _decode_html(response: requests.Response) -> str:
    raw_bytes = response.content
    head_bytes = raw_bytes[:2048]
    meta_match = META_CHARSET_PATTERN.search(head_bytes)

    candidate_encodings = []
    if meta_match:
        try:
            candidate_encodings.append(meta_match.group(1).decode("ascii", errors="ignore"))
        except Exception:
            pass
    if response.encoding and response.encoding.lower() != "iso-8859-1":
        candidate_encodings.append(response.encoding)
    if response.apparent_encoding and response.apparent_encoding.lower() != "iso-8859-1":
        candidate_encodings.append(response.apparent_encoding)
    candidate_encodings.append("utf-8")

    tried = set()
    for encoding in candidate_encodings:
        normalized = (encoding or "").strip()
        if not normalized or normalized.lower() in tried:
            continue
        tried.add(normalized.lower())
        try:
            return raw_bytes.decode(normalized)
        except (LookupError, UnicodeDecodeError):
            continue

    return raw_bytes.decode("utf-8", errors="replace")


def _extract_with_bs4(html: str) -> tuple[str, str, list[dict]]:
    soup = BeautifulSoup(html, "html.parser")
    for tag_name in ["script", "style", "noscript", "svg", "footer", "nav", "aside", "form", "button"]:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    root = soup.find("article")
    if root is None:
        root = soup.find("main")
    if root is None:
        root = soup.find(
            attrs={
                "class": CONTENT_CONTAINER_PATTERN,
            }
        )
    if root is None:
        root = soup.find(
            attrs={
                "id": CONTENT_CONTAINER_PATTERN,
            }
        )
    if root is None:
        root = soup.body or soup

    title = ""
    title_tag = root.find("h1") if root else None
    if title_tag:
        title = title_tag.get_text(" ", strip=True)
    elif soup.title:
        title = soup.title.get_text(" ", strip=True)

    sections = []
    current_headings: dict[int, str] = {}
    current_section: dict | None = None
    selectors = ["h1", "h2", "h3", "h4", "p", "li", "pre", "blockquote"]
    for element in root.find_all(selectors):
        text = element.get_text("\n" if element.name == "pre" else " ", strip=True)
        text = _normalize_text(text)
        if not text:
            continue
        if element.name in {"h1", "h2", "h3", "h4"}:
            level = int(element.name[1])
            current_headings[level] = text
            current_headings = {key: value for key, value in current_headings.items() if key <= level}
            heading_path = " > ".join(current_headings[key] for key in sorted(current_headings))
            current_section = {
                "section_title": text,
                "heading_path": heading_path or text,
                "content_parts": [],
                "chunk_type": "heading_section",
            }
            sections.append(current_section)
            continue

        if current_section is None:
            current_section = {
                "section_title": title or "正文",
                "heading_path": title or "正文",
                "content_parts": [],
                "chunk_type": "body_section",
            }
            sections.append(current_section)

        if current_section["content_parts"] and text == current_section["content_parts"][-1]:
            continue
        current_section["content_parts"].append(text)

    if not sections and root:
        fallback_text = root.get_text("\n", strip=True)
        fallback_text = _normalize_text(fallback_text)
        if fallback_text:
            sections.append(
                {
                    "section_title": title or "正文",
                    "heading_path": title or "正文",
                    "content_parts": [fallback_text],
                    "chunk_type": "fallback_section",
                }
            )

    normalized_sections = []
    pieces = []
    for section in sections:
        content_parts = [item for item in section.get("content_parts", []) if item]
        content = _normalize_text("\n\n".join(content_parts))
        if not content:
            continue
        normalized_sections.append(
            {
                "section_title": section.get("section_title") or title or "正文",
                "heading_path": section.get("heading_path") or title or "正文",
                "content": content,
                "chunk_type": section.get("chunk_type", "section"),
            }
        )
        pieces.append(content)

    return title, "\n\n".join(pieces), normalized_sections


def _extract_same_site_links(html: str, base_url: str) -> list[str]:
    if BeautifulSoup is not None:
        soup = BeautifulSoup(html, "html.parser")
        hrefs = [tag.get("href", "") for tag in soup.find_all("a")]
    else:
        hrefs = ANCHOR_HREF_PATTERN.findall(html)

    links = []
    for href in hrefs:
        normalized = _normalize_candidate_url(base_url, href)
        if normalized and _is_probable_article_url(normalized, base_url):
            links.append(normalized)
    return links


def _normalize_candidate_url(base_url: str, href: str) -> str:
    href = (href or "").strip()
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
        return ""

    absolute = urljoin(base_url, href)
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"}:
        return ""
    if parsed.netloc != urlparse(base_url).netloc:
        return ""

    normalized = urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/") or "/", "", "", ""))
    return normalized


def _is_probable_article_url(candidate_url: str, base_url: str) -> bool:
    parsed = urlparse(candidate_url)
    base_parsed = urlparse(base_url)
    if parsed.netloc != base_parsed.netloc:
        return False

    path = parsed.path.lower().strip("/")
    if not path:
        return False
    if any(token in path.split("/") for token in BLOCKED_LINK_TOKENS):
        return False
    if any(path.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".gif", ".svg", ".css", ".js", ".xml", ".pdf", ".zip"]):
        return False

    segments = [segment for segment in path.split("/") if segment]
    if path.endswith((".html", ".htm", ".md")):
        return True
    return len(segments) >= 2


def _score_candidate_url(candidate_url: str, base_url: str) -> tuple[int, int]:
    candidate_path = urlparse(candidate_url).path.lower().strip("/")
    base_path = urlparse(base_url).path.lower().strip("/")
    candidate_segments = [segment for segment in candidate_path.split("/") if segment]
    base_segments = [segment for segment in base_path.split("/") if segment]

    shared_prefix = 0
    for left, right in zip(candidate_segments, base_segments):
        if left != right:
            break
        shared_prefix += 1

    score = 0
    if candidate_path.endswith(".html"):
        score += 4
    if candidate_path.endswith(".htm"):
        score += 3
    score += min(shared_prefix, 3) * 2
    score += min(len(candidate_segments), 5)
    if any(token in candidate_segments for token in BLOCKED_LINK_TOKENS):
        score -= 5
    return score, len(candidate_path)


def _extract_title_with_regex(html: str) -> str:
    match = H1_PATTERN.search(html) or TITLE_PATTERN.search(html)
    if not match:
        return ""
    return _html_fragment_to_text(match.group(1))


def _extract_content_with_regex(html: str) -> str:
    lowered = html.lower()
    for tag in ["article", "main"]:
        start_token = f"<{tag}"
        start_index = lowered.find(start_token)
        if start_index >= 0:
            fragment = html[start_index:]
            end_index = fragment.lower().find(f"</{tag}>")
            if end_index >= 0:
                return _html_fragment_to_text(fragment[: end_index + len(tag) + 3])

    body_start = lowered.find("<body")
    if body_start >= 0:
        fragment = html[body_start:]
        body_end = fragment.lower().find("</body>")
        if body_end >= 0:
            return _html_fragment_to_text(fragment[: body_end + 7])

    return _html_fragment_to_text(html)


def _build_fallback_sections(title: str, content: str) -> list[dict]:
    normalized_content = _normalize_text(content)
    if not normalized_content:
        return []
    return [
        {
            "section_title": title or "正文",
            "heading_path": title or "正文",
            "content": normalized_content,
            "chunk_type": "fallback_section",
        }
    ]


def _html_fragment_to_text(fragment: str) -> str:
    cleaned = fragment
    for pattern in TAG_CLEANUP_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)
    cleaned = BLOCK_TAG_PATTERN.sub("\n", cleaned)
    cleaned = HTML_TAG_PATTERN.sub(" ", cleaned)
    cleaned = unescape(cleaned)
    return _normalize_text(cleaned)


def _normalize_text(text: str) -> str:
    if not text:
        return ""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r" ?\n ?", "\n", normalized)
    normalized = MULTI_BLANK_PATTERN.sub("\n\n", normalized)
    return normalized.strip()
