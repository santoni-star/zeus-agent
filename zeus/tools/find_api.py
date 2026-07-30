"""Find and call public APIs — uses the public-apis dataset (1659 APIs, 51 categories).

Locally cached from https://github.com/public-apis/public-apis
Updated on first call or when cache is stale.

Two modes:
  - search: find APIs by category or keyword → returns matching APIs
  - info: get details for a specific API name + category
  - call: find a matching API and call it directly (via api_call)
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any

DATA_URL = "https://raw.githubusercontent.com/public-apis/public-apis/master/README.md"
DATA_DIR = Path(__file__).parent.parent / "data"
CACHE_FILE = DATA_DIR / "public-apis.json"

SCHEMA = {
    "name": "find_api",
    "description": "Find and call public APIs. "
                   "Search 1600+ free APIs across 51 categories: weather, finance, "
                   "crypto, news, games, geocoding, music, books, movies, and more. "
                   "Use 'search' to find APIs, 'info' for details, 'call' to invoke one.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "info", "call"],
                "description": "What to do: search (find APIs), info (get API details), "
                               "call (find + call an API)",
                "default": "search",
            },
            "query": {
                "type": "string",
                "description": "Search keyword or category (e.g. 'weather', 'crypto', "
                               "'cat facts', 'jokes', 'movies')",
            },
            "category": {
                "type": "string",
                "description": "Optional: filter by category (e.g. 'Weather', 'Finance', "
                               "'Games & Comics'). Use search to see available categories.",
                "default": "",
            },
            "no_auth": {
                "type": "boolean",
                "description": "Only show APIs that require no authentication (default: true)",
                "default": True,
            },
            "api_name": {
                "type": "string",
                "description": "For 'info' or 'call' action: the exact API name to use",
                "default": "",
            },
            "https_only": {
                "type": "boolean",
                "description": "Only show APIs that support HTTPS (default: true)",
                "default": True,
            },
            "api_params": {
                "type": "string",
                "description": "For 'call' action: JSON params to pass to the API call "
                               "(e.g. {\"lat\": \"51.5\", \"lon\": \"-0.13\"})",
                "default": "",
            },
        },
        "required": ["action", "query"],
    },
}


def _load_cache() -> list[dict]:
    """Load the public-apis cache, downloading if needed."""
    if CACHE_FILE.exists():
        with open(CACHE_FILE) as f:
            return json.load(f)

    # Download and parse
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  Downloading public APIs list...")
    try:
        with urllib.request.urlopen(DATA_URL, timeout=30) as resp:
            text = resp.read().decode("utf-8")
    except Exception as e:
        return [{"name": f"Download failed: {e}", "description": "", "category": "Error"}]

    apis = []
    current_category = "Uncategorized"
    lines = text.split("\n")

    for line in lines:
        m = re.match(r'^##+\s+(.+)', line)
        if m:
            current_category = m.group(1).strip()
            continue

        if line.startswith("|") and line.count("|") >= 5:
            cells = [c.strip() for c in line.split("|")[1:-1]]
            if len(cells) < 5:
                continue
            name, desc = cells[0], cells[1]
            auth = cells[2] if len(cells) > 2 else ""
            https = cells[3] if len(cells) > 3 else ""
            cors = cells[4] if len(cells) > 4 else ""

            if name.lower() in ["api", "---"] or not name:
                continue

            url_col = cells[5] if len(cells) > 5 else ""
            if name.startswith("[") and "]" in name:
                name_url = re.match(r'\[(.+?)\]\((.+?)\)', name)
                if name_url:
                    name = name_url.group(1)
                    url_col = name_url.group(2)

            # Also extract URL from description if not in name
            if not url_col or "http" not in url_col:
                # Check for URLs in description
                desc_urls = re.findall(r'(https?://[^\s\)\]]+)', desc)
                if desc_urls:
                    url_col = desc_urls[0]

            desc = re.sub(r'\[.*?\]\(.*?\)', '', desc).strip()

            # Clean trailing markdown links from desc
            desc = re.sub(r'\s*\[.*?\]\(.*?\)', '', desc).strip()

            # Skip pure-link names
            if name.startswith("http") or name.startswith("<"):
                continue

            desc = re.sub(r'\s*\[.*?\]\(.*?\)', '', desc).strip()
            desc = desc.rstrip('.')

            apis.append({
                "name": name.strip(),
                "description": desc[:200],
                "category": current_category,
                "auth": "yes" if auth and auth.lower() not in ("no", "", "unknown") else "no",
                "https": https.lower() == "yes",
                "cors": cors,
                "url": url_col.strip() if url_col else "",
            })

    with open(CACHE_FILE, "w") as f:
        json.dump(apis, f, indent=1, ensure_ascii=False)
    return apis


def _search(apis: list[dict], query: str, category: str = "",
            no_auth: bool = True, https_only: bool = True) -> list[dict]:
    """Search APIs by keyword + filters."""
    q = query.lower()
    results = []

    for api in apis:
        # Category filter
        if category and category.lower() not in api["category"].lower():
            continue
        # Auth filter
        if no_auth and api["auth"] == "yes":
            continue
        # HTTPS filter
        if https_only and not api["https"]:
            continue

        name = api["name"].lower()
        desc = api["description"].lower()
        cat = api["category"].lower()

        # Multi-keyword search
        keywords = [kw for kw in q.split() if len(kw) > 2]
        if not keywords:
            keywords = [q]

        match = False
        for kw in keywords:
            if kw in name or kw in desc or kw in cat:
                match = True
                break

        if match:
            results.append(api)

    return results


def execute(params: dict) -> str:
    """Execute find_api tool."""
    action = params.get("action", "search")
    query = params.get("query", "")
    category = params.get("category", "")
    no_auth = params.get("no_auth", True)
    https_only = params.get("https_only", True)
    api_name = params.get("api_name", "")
    api_params = params.get("api_params", "")

    apis = _load_cache()
    if not apis:
        return "❌ Failed to load public APIs list. Check internet connection."

    if action == "search":
        return _do_search(apis, query, category, no_auth, https_only)

    elif action == "info":
        return _do_info(apis, api_name or query, category)

    elif action == "call":
        return _do_call(apis, query, api_name, api_params, category)

    return "❌ Unknown action. Use: search, info, call"


def _do_search(apis: list[dict], query: str, category: str = "",
               no_auth: bool = True, https_only: bool = True) -> str:
    """Search and format results."""
    results = _search(apis, query, category, no_auth, https_only)

    if not results:
        return "No APIs found. Try broader keywords, or enable auth-required APIs."

    # Unique by name
    seen = set()
    unique = []
    for r in results:
        if r["name"].lower() not in seen:
            seen.add(r["name"].lower())
            unique.append(r)

    lines = [f"🔍 Found {len(unique)} API(s) matching '{query}':\n"]
    for api in unique[:15]:
        auth_tag = " 🔑" if api["auth"] == "yes" else ""
        cors_tag = f" CORS:{api['cors']}" if api.get("cors") else ""
        https_tag = "" if api["https"] else " ⚠ no HTTPS"
        lines.append(f"  • {api['name']}{auth_tag}{https_tag}{cors_tag}")
        lines.append(f"    {api['category']} — {api['description'][:100]}")
        if api.get("url"):
            lines.append(f"    {api['url']}")
        lines.append("")

    if len(unique) > 15:
        lines.append(f"  ... and {len(unique) - 15} more\n")

    lines.append("💡 Use info action with api_name for details, or call to try it.")
    return "\n".join(lines)


def _do_info(apis: list[dict], api_name: str, category: str = "") -> str:
    """Get detailed info for a specific API."""
    for api in apis:
        if api["name"].lower() == api_name.lower():
            if category and api["category"].lower() != category.lower():
                continue
            lines = [
                f"📦 {api['name']}",
                f"   Category: {api['category']}",
                f"   Description: {api['description']}",
                f"   Auth: {'✅ No' if api['auth'] == 'no' else '🔑 Yes'}",
                f"   HTTPS: {'✅' if api['https'] else '⚠ No'}",
                f"   CORS: {api['cors'] if api.get('cors') else 'unknown'}",
            ]
            if api.get("url"):
                lines.append(f"   URL: {api['url']}")
            lines.append("\n💡 Use call action to invoke this API directly.")
            return "\n".join(lines)

    return f"❌ API '{api_name}' not found. Use search to find matching APIs."


def _do_call(apis: list[dict], query: str, api_name: str = "",
             api_params: str = "", category: str = "") -> str:
    """Find a matching API and call it via api_call."""
    target_name = api_name or ""

    if not target_name:
        # Find best matching API — score by relevance, not first alphabetically
        q = query.lower()
        q_words = set(q.split())

        def score_api(api):
            s = 0
            name_desc = f"{api['name']} {api['description']}".lower()
            for word in q_words:
                if word in api['name'].lower():
                    s += 5
                elif word in api['description'].lower():
                    s += 3
                if word in api.get('category', '').lower():
                    s += 2
            # Prefer JSON APIs over HTML pages
            url = api.get('url', '').lower()
            if '.json' in url or '/api/' in url:
                s += 1
            return s

        results = _search(apis, query, category, no_auth=True, https_only=True)
        if not results:
            # Try with auth too
            results = _search(apis, query, category, no_auth=False, https_only=True)
        if not results:
            return f"❌ No APIs found for '{query}'."

        # Sort by relevance score
        results.sort(key=score_api, reverse=True)
        target = results[0]
    else:
        for api in apis:
            if api["name"].lower() == target_name.lower():
                target = api
                break
        else:
            return f"❌ API '{target_name}' not found."

    if not target.get("url"):
        return f"❌ No URL for {target['name']}. Check info for details."

    # Call it
    try:
        from zeus.tools.api_call import execute as api_call_execute
        call_params = {
            "url": target["url"],
            "method": "GET",
            "timeout": 15,
        }
        # Parse extra params
        if api_params:
            try:
                extra = json.loads(api_params)
                call_params.update(extra)
            except json.JSONDecodeError:
                pass

        result = api_call_execute(call_params)
        return (
            f"📡 {target['name']}\n"
            f"   URL: {target['url']}\n"
            f"   Description: {target['description'][:100]}\n\n"
            f"{result}"
        )
    except Exception as e:
        return f"❌ Failed to call {target['name']}: {e}"
