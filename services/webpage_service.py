import re
from html import unescape
from urllib.parse import urlparse

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

    if BeautifulSoup is not None:
        title, content = _extract_with_bs4(html)

    if not content:
        title = title or _extract_title_with_regex(html)
        content = _extract_content_with_regex(html)

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


def _extract_with_bs4(html: str) -> tuple[str, str]:
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

    pieces = []
    selectors = ["h1", "h2", "h3", "h4", "p", "li", "pre", "blockquote"]
    for element in root.find_all(selectors):
        text = element.get_text("\n" if element.name == "pre" else " ", strip=True)
        text = _normalize_text(text)
        if not text:
            continue
        if pieces and text == pieces[-1]:
            continue
        pieces.append(text)

    if not pieces and root:
        fallback_text = root.get_text("\n", strip=True)
        fallback_text = _normalize_text(fallback_text)
        if fallback_text:
            pieces.append(fallback_text)

    return title, "\n\n".join(pieces)


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
