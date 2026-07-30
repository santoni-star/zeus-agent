"""Session search tool — search across all past conversations.

Uses the existing HistorySearcher to find relevant sessions
by keyword, time range, or natural language query.

Example queries:
  - "what did we do yesterday?"
  - "github discussion about agents"
  - "all sessions from last 2 days"
"""

from __future__ import annotations

from zeus.memory.history import HistorySearcher

SCHEMA = {
    "name": "session_search",
    "description": "Search past conversation sessions by keyword, time, or topic. "
                   "Supports natural language queries like 'what did we do yesterday'.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language search query. "
                               "Can include time references like 'yesterday', 'last 2 days'.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum results to return (default: 10)",
                "default": 10,
            },
        },
        "required": ["query"],
    },
}


def execute(params: dict) -> str:
    """Search across all conversation sessions.

    Args:
        params: {"query": str, "limit": int}

    Returns:
        Formatted search results with sessions and messages.
    """
    query = params["query"]
    limit = min(params.get("limit", 10), 50)

    searcher = HistorySearcher()
    result = searcher.smart_search(query, limit=limit)

    if result.get("error"):
        return f"❌ Search error: {result['error']}"

    formatted = searcher.format_result(result)

    if not formatted.strip() or formatted == "Нічого не знайдено за вашим запитом.":
        # Try without time filtering
        result2 = searcher.smart_search(query, limit=limit)
        formatted2 = searcher.format_result(result2)
        if formatted2.strip() and formatted2 != "Нічого не знайдено за вашим запитом.":
            return formatted2
        return "📭 Нічого не знайдено."

    return formatted
