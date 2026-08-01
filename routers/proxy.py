from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["proxy"])

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}


def _inject_base_tag(html: str, url: str) -> str:
    """Inject <base href="origin/"> so relative CSS/image URLs resolve correctly."""
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    base_tag = f'<base href="{origin}/" target="_blank">'

    lower = html.lower()
    head_idx = lower.find("<head")
    if head_idx == -1:
        return html
    gt_idx = html.find(">", head_idx)
    if gt_idx == -1:
        return html
    return html[: gt_idx + 1] + base_tag + html[gt_idx + 1 :]


def _fallback_html(url: str, message: str) -> str:
    safe_url = url.replace('"', "&quot;")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Article preview</title>
<style>
  body {{ font-family: system-ui, -apple-system, sans-serif; background: #0f172a;
          color: #e2e8f0; margin: 0; padding: 2rem; display: flex; flex-direction: column;
          align-items: center; justify-content: center; min-height: 100vh; text-align: center; }}
  h1 {{ font-size: 1rem; font-weight: 600; margin: 0 0 .5rem; }}
  p {{ font-size: .85rem; color: #94a3b8; margin: 0 0 1.25rem; max-width: 28rem; line-height: 1.5; }}
  a {{ color: #6366f1; font-size: .85rem; text-decoration: none; padding: .5rem 1rem;
       border: 1px solid #334155; border-radius: 6px; }}
  a:hover {{ background: #1e293b; }}
</style></head>
<body>
  <h1>Preview unavailable</h1>
  <p>{message}</p>
  <a href="{safe_url}" target="_blank" rel="noopener noreferrer">Open article in new tab ↗</a>
</body></html>"""


@router.get("/api/proxy-article")
async def proxy_article(url: str):
    parsed = urlparse(url)
    if not parsed.scheme.startswith("http"):
        raise HTTPException(400, detail="Invalid article URL")

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url, headers=BROWSER_HEADERS)
            r.raise_for_status()
    except httpx.HTTPStatusError as e:
        return HTMLResponse(
            content=_fallback_html(
                url,
                f"The source site blocked this preview ({e.response.status_code}). "
                "Click below to open the article directly.",
            ),
            status_code=200,
        )
    except httpx.HTTPError:
        return HTMLResponse(
            content=_fallback_html(url, "Could not reach the source site."),
            status_code=200,
        )

    return HTMLResponse(content=_inject_base_tag(r.text, url))
