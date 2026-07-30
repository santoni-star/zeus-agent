"""Web fetch tool — fetch and extract content from a URL.

Converts HTML to readable text, handles redirects, timeouts.
Alternative to web_search for full page content.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser


class _HTMLToText(HTMLParser):
    """Simple HTML to text converter."""
    def __init__(self):
        super().__init__()
        self._text = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip = True
        if tag in ("p", "br", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._text.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self._skip = False
        if tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._text.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self._text.append(data)

    def get_text(self) -> str:
        result = "".join(self._text)
        # Clean up whitespace
        result = re.sub(r'\n{3,}', '\n\n', result)
        result = re.sub(r'  +', ' ', result)
        return result.strip()


SCHEMA = {
    "name": "web_fetch",
    "description": "Fetch content from a URL and return it as readable text.",
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "URL to fetch (http/https)",
            },
            "max_length": {
                "type": "integer",
                "description": "Max characters to return (default: 5000)",
                "default": 5000,
            },
        },
        "required": ["url"],
    },
}


def execute(params: dict) -> str:
    """Fetch a URL and return its content.

    Args:
        params: {"url": str, "max_length": int}

    Returns:
        Page content as readable text.
    """
    url = params["url"].strip()
    max_length = min(params.get("max_length", 5000), 20000)

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml",
            },
        )

        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read()
            # Detect encoding
            encoding = resp.headers.get_content_charset()
            if not encoding:
                # Try from HTML meta
                meta_match = re.search(
                    rb'<meta[^>]+charset=[\s"\']*([a-zA-Z0-9-]+)',
                    content[:2000], re.IGNORECASE
                )
                encoding = meta_match.group(1).decode() if meta_match else "utf-8"

            try:
                html = content.decode(encoding, errors="replace")
            except (LookupError, ValueError):
                html = content.decode("utf-8", errors="replace")

        # Convert to text
        parser = _HTMLToText()
        parser.feed(html)
        text = parser.get_text()

        if len(text) > max_length:
            text = text[:max_length] + "\n\n[...truncated]"

        if not text.strip():
            return f"Page at {url} appears to be empty or unparseable."

        return (
            f"📄 Content from {url}\n"
            f"{'─' * 40}\n"
            f"{text}"
        )

    except urllib.error.HTTPError as e:
        return f"❌ HTTP {e.code}: {e.reason} for {url}"
    except urllib.error.URLError as e:
        return f"❌ URL Error: {e.reason}"
    except Exception as e:
        return f"❌ Fetch failed: {e}"
