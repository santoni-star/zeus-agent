"""Web search tool — DuckDuckGo search via requests."""

from __future__ import annotations
import json
import urllib.parse
import urllib.request
import urllib.error
import re


SCHEMA = {
    "name": "web_search",
    "description": "Search the web via DuckDuckGo (no API key needed)",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum results to return (default: 5)",
                "default": 5,
            },
        },
        "required": ["query"],
    },
}


def execute(params: dict) -> str:
    """Search the web via DuckDuckGo HTML interface.

    Args:
        params: {"query": str, "max_results": int}

    Returns:
        Search result summaries.
    """
    query = params["query"]
    max_results = min(params.get("max_results", 5), 10)

    try:
        return _search_duckduckgo(query, max_results)
    except Exception as e:
        # Fallback to a simpler approach
        try:
            return _search_fallback(query, max_results)
        except Exception as e2:
            return f"Search failed: {e2}"


def _search_duckduckgo(query: str, max_results: int) -> str:
    """Search DuckDuckGo via their Lite HTML interface."""
    url = f"https://lite.duckduckgo.com/lite/?q={urllib.parse.quote(query)}"

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36",
        },
    )

    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    # Parse results from the HTML
    results = []
    # Find result links in the table
    # DuckDuckGo Lite format: <a href="..." class="result-link">...</a>
    links = re.findall(
        r'<a[^>]*href="(https?://[^"]+)"[^>]*class="result-link"[^>]*>(.*?)</a>',
        html,
        re.DOTALL,
    )

    # Find snippets
    snippets = re.findall(
        r'<td[^>]*class="result-snippet"[^>]*>(.*?)</td>',
        html,
        re.DOTALL,
    )

    for i, (url_text, title) in enumerate(links[:max_results]):
        snippet = snippets[i] if i < len(snippets) else ""
        # Clean HTML tags
        title = re.sub(r'<[^>]+>', '', title).strip()
        snippet = re.sub(r'<[^>]+>', '', snippet).strip()
        results.append(f"{i+1}. {title}\n   {url_text}\n   {snippet}")

    if not results:
        return f"Запит: {query}\nРезультатів не знайдено."

    return f"Результати пошуку для '{query}':\n\n" + "\n\n".join(results)


def _search_fallback(query: str, max_results: int) -> str:
    """Fallback: use a different search approach."""
    # Try Google via simple scraping
    url = f"https://www.google.com/search?q={urllib.parse.quote(query)}&num={max_results}"

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120.0.0.0",
        },
    )

    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    results = []
    # Parse Google results
    for m in re.finditer(
        r'<a[^>]*href="/url\?q=(https?://[^"&]+)[^"]*"[^>]*>(.*?)</a>',
        html,
    ):
        url_text = urllib.parse.unquote(m.group(1))
        title = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        results.append(f"• {title}\n  {url_text}")

    if not results:
        return f"Запит: {query}\nРезультатів не знайдено."

    return f"Результати пошуку:\n\n" + "\n\n".join(results[:max_results])