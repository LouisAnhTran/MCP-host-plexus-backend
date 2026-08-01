import re
import httpx
from html import unescape

ZENDESK_URL_RE = re.compile(
    r"^(https?://[^/]+)/hc/([a-z-]+)/categories/(\d+)(?:-.*)?/?$"
)


def parse_zendesk_url(url: str) -> tuple[str, str, int]:
    """Extract (base_url, locale, category_id) from a Zendesk help center URL."""
    m = ZENDESK_URL_RE.match(url.strip())
    if not m:
        raise ValueError(f"Not a recognized Zendesk help center URL: {url}")
    return m.group(1), m.group(2), int(m.group(3))


async def fetch_sections(base: str, locale: str, category_id: int) -> dict[int, dict]:
    url = f"{base}/api/v2/help_center/{locale}/categories/{category_id}/sections.json?per_page=100"
    sections = {}
    async with httpx.AsyncClient(timeout=30) as client:
        while url:
            r = await client.get(url)
            r.raise_for_status()
            data = r.json()
            for s in data.get("sections", []):
                sections[s["id"]] = s
            url = data.get("next_page")
    return sections


async def fetch_articles(base: str, locale: str, category_id: int) -> list[dict]:
    url = f"{base}/api/v2/help_center/{locale}/categories/{category_id}/articles.json?per_page=100"
    articles = []
    async with httpx.AsyncClient(timeout=30) as client:
        while url:
            r = await client.get(url)
            r.raise_for_status()
            data = r.json()
            articles.extend(data.get("articles", []))
            url = data.get("next_page")
    return articles


def strip_html(html: str) -> str:
    """Minimal HTML → plain text for embedding."""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html or "", flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", unescape(text)).strip()
