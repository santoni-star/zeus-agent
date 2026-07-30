"""HTTP API client — make REST API calls (GET, POST, PUT, DELETE, PATCH).

Lets Zeus interact with REST APIs directly:
  - GET data from endpoints
  - POST/PUT/PATCH data (JSON, form, raw)
  - DELETE resources
  - Custom headers, timeouts, auth
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

SCHEMA = {
    "name": "api_call",
    "description": "Make HTTP requests to REST APIs. "
                   "Supports GET, POST, PUT, DELETE, PATCH. "
                   "Returns status code, headers, and body.",
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Full URL to call",
            },
            "method": {
                "type": "string",
                "enum": ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"],
                "description": "HTTP method",
                "default": "GET",
            },
            "headers": {
                "type": "string",
                "description": "JSON string of extra headers",
                "default": "",
            },
            "body": {
                "type": "string",
                "description": "Request body (JSON string, form data, or raw text)",
                "default": "",
            },
            "content_type": {
                "type": "string",
                "enum": ["json", "form", "text"],
                "description": "Content type for body",
                "default": "json",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds",
                "default": 15,
            },
        },
        "required": ["url"],
    },
}


def execute(params: dict) -> str:
    """Execute an HTTP request.

    Args:
        params: See SCHEMA.

    Returns:
        Formatted response with status, headers, body.
    """
    url = params["url"].strip()
    method = params.get("method", "GET").upper()
    headers_json = params.get("headers", "") or "{}"
    body = params.get("body", "")
    content_type = params.get("content_type", "json")
    timeout = min(params.get("timeout", 15), 60)

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        # Parse headers
        extra_headers = {}
        if headers_json.strip():
            extra_headers = json.loads(headers_json)

        # Build request
        req_headers = {
            "User-Agent": "Zeus-Agent/1.0",
        }
        req_headers.update(extra_headers)

        # Encode body
        data = None
        if body:
            if content_type == "json":
                # Try to parse as JSON (validate)
                try:
                    json.loads(body)
                except json.JSONDecodeError as e:
                    return f"❌ Invalid JSON body: {e}"
                req_headers.setdefault("Content-Type", "application/json")
                data = body.encode("utf-8")
            elif content_type == "form":
                req_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
                data = body.encode("utf-8")
            else:
                data = body.encode("utf-8")

        req = urllib.request.Request(
            url,
            data=data,
            headers=req_headers,
            method=method,
        )

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            resp_headers = dict(resp.headers)
            raw = resp.read()

        # Decode body
        encoding = resp_headers.get("Content-Type", "")
        if "charset=" in encoding:
            enc = encoding.split("charset=")[-1].split(";")[0].strip()
        else:
            enc = "utf-8"

        try:
            body_text = raw.decode(enc, errors="replace")
        except (LookupError, ValueError):
            body_text = raw.decode("utf-8", errors="replace")

        # Truncate long responses
        if len(body_text) > 3000:
            body_text = body_text[:3000] + f"\n\n[...truncated, {len(raw)} chars total]"

        # Format output
        content_type_resp = resp_headers.get("Content-Type", "unknown").split(";")[0]

        lines = [
            f"🌐 {method} {url}",
            f"   Status: {status} {resp.reason}",
            f"   Type: {content_type_resp}",
            f"   Size: {len(raw)} bytes",
        ]

        # Try pretty-print JSON
        if "json" in content_type_resp or body_text.strip().startswith("{"):
            try:
                parsed = json.loads(body_text)
                body_text = json.dumps(parsed, indent=2, ensure_ascii=False)[:3000]
            except json.JSONDecodeError:
                pass

        if body_text.strip():
            lines.append(f"\n{body_text}")

        return "\n".join(lines)

    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:1000]
        return f"❌ {method} {url}\n   HTTP {e.code}: {e.reason}\n   {body[:300]}"
    except urllib.error.URLError as e:
        return f"❌ {method} {url}\n   URL Error: {e.reason}"
    except json.JSONDecodeError as e:
        return f"❌ Invalid headers JSON: {e}"
    except Exception as e:
        return f"❌ {method} {url}\n   {e}"
