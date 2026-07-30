# Zeus Agent — Tool Reference

Zeus has **9 built-in tools** + dynamic tool creation from natural language.

## Tool Registry

All tools are registered in a central `ToolRegistry` with schema validation,
retry support, and help documentation.

```python
from zeus.tools.registry import get_registry

reg = get_registry()
reg.list_tools()       # List all tools with descriptions
reg.get_help("web")   # Show tool details
reg.call("web_search", {"query": "python"})  # Execute tool
```

## Tool Reference

### 1. `terminal` — Shell Command Execution

Execute any shell command and capture output.

**Schema:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| command | string | yes | Shell command to execute |
| timeout | integer | no | Max seconds (default: 30) |
| workdir | string | no | Working directory |

**Scenarios:**
```
User: "show disk usage"
  → terminal("df -h")
  → Output: filesystem usage table

User: "install pytest"
  → terminal("pip install pytest", timeout=60)
  → Output: installation log

User: "find large files"
  → terminal("find ~ -type f -size +100M -exec ls -lh {} \;")
  → Output: list of large files
```

---

### 2. `file` — File Operations

Read, write, search, and list files on the filesystem.

**Schema:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| action | enum | yes | read, write, search, list |
| path | string | yes | File or directory path |
| content | string | for write | File content |
| pattern | string | for search | Glob pattern |

**Scenarios:**
```
User: "read the config file"
  → file(action="read", path="~/zeus-agent/zeus/config.py")
  → Output: file contents with line numbers

User: "create a note file"
  → file(action="write", path="~/todo.md", content="# TODO\n- fix bug\n")
  → Output: "Written 20 bytes to ~/todo.md"

User: "list python files"
  → file(action="list", path="~/project", pattern="*.py")
  → Output: directory listing
```

---

### 3. `web_search` — DuckDuckGo Search

Search the web via DuckDuckGo Lite (no API key needed).

**Schema:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| query | string | yes | Search query |
| max_results | integer | no | Max results (default: 5, max: 10) |

**Scenarios:**
```
User: "search for latest python release"
  → web_search(query="python latest version 2026")
  → Output: search results with titles, URLs, snippets

User: "find trending AI agent frameworks"
  → web_search(query="open source ai agent framework 2026 github", max_results=10)
  → Output: top 10 results
```

---

### 4. `web_fetch` — URL Content Fetcher

Fetch and extract readable text from any URL.

**Schema:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| url | string | yes | URL to fetch |
| max_length | integer | no | Max chars (default: 5000, max: 20000) |

**Scenarios:**
```
User: "get the content of that article"
  → web_fetch(url="https://example.com/article")
  → Output: article text content (HTML stripped)

User: "read the GitHub README"
  → web_fetch(url="github.com/santoni-star/zeus-agent")
  → Output: page content as readable text
```

---

### 5. `structured_file` — Structured File Editing

Patch (find/replace), write, or read files with fuzzy matching.

**Schema:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| action | enum | yes | patch, write, read |
| path | string | yes | File path |
| old_string | string | for patch | Text to find |
| new_string | string | for patch | Replacement text |
| content | string | for write | Full file content |
| replace_all | boolean | no | Replace all occurrences |
| offset | integer | no | Read offset (1-indexed) |
| limit | integer | no | Max lines to read |

**Scenarios:**
```
User: "change the version in config"
  → structured_file(action="patch", path="setup.py",
     old_string="version = '1.0.0'",
     new_string="version = '2.0.0'")
  → Output: "Patched setup.py (10 → 10 lines)"

User: "create a new module"
  → structured_file(action="write", path="~/project/new_module.py",
     content="# New module\n\ndef hello():\n    print('Hello!')\n")
  → Output: "Written 45 bytes to ~/project/new_module.py"

User: "show the first 20 lines"
  → structured_file(action="read", path="~/file.py", limit=20)
  → Output: file with line numbers
```

---

### 6. `code_exec` — Isolated Python Execution

Run Python code in a subprocess with timeout protection.

**Schema:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| code | string | yes | Python code to execute |
| timeout | integer | no | Max seconds (default: 15) |
| workdir | string | no | Working directory |

**Scenarios:**
```
User: "what's 2^100?"
  → code_exec(code="2**100")
  → Output: 1267650600228229401496703205376

User: "calculate fibonacci"
  → code_exec(code="def fib(n): return n if n<2 else fib(n-1)+fib(n-2); print([fib(i) for i in range(10)])")
  → Output: [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]

User: "test this data processing"
  → code_exec(code="data = [1,2,3,4,5]; print(sum(data), sum(data)/len(data))")
  → Output: 15 3.0
```

---

### 7. `session_search` — Conversation Search

Search across all past conversation sessions by keyword or time.

**Schema:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| query | string | yes | Natural language query |
| limit | integer | no | Max results (default: 10, max: 50) |

**Scenarios:**
```
User: "what did we do yesterday?"
  → session_search(query="що ми вчора робили")
  → Output: sessions from yesterday with message previews

User: "find the discussion about EventBus"
  → session_search(query="EventBus architecture")
  → Output: sessions mentioning EventBus

User: "show everything from last 2 days"
  → session_search(query="за останні 2 дні")
  → Output: all sessions within the last 48 hours
```

---

### 8. `search_files` — File Content Search

Search file contents by regex or find files by name.

**Schema:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| pattern | string | yes | Regex (content) or glob (files) |
| target | enum | no | "content" or "files" (default: content) |
| path | string | no | Directory to search |
| file_glob | string | no | Filter by filename glob |
| context | integer | no | Context lines before/after |
| max_results | integer | no | Max results (default: 20) |

**Scenarios:**
```
User: "find all TODO comments in code"
  → search_files(pattern="TODO|FIXME", file_glob="*.py", context=1)
  → Output: matches with surrounding code

User: "find config files"
  → search_files(pattern="*config*", target="files", path="~/project")
  → Output: all files with "config" in name

User: "search for async functions"
  → search_files(pattern="async def", file_glob="*.py", path="./zeus/modules")
  → Output: all async function definitions
```

---

### 9. `utility` / `utils` — Utilities

Calculator, timestamp, UUID generation, JSON formatting.

**Schema:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| action | enum | yes | calc, timestamp, uuid, json_format, echo |
| expression | string | for calc | Math expression |
| data | string | for json_format | JSON to format |
| message | string | for echo | Text to return |
| format | enum | no | unix, iso, human (for timestamp) |

**Scenarios:**
```
User: "calculate 15% of 2000"
  → utility(action="calc", expression="2000 * 0.15")
  → Output: 2000 * 0.15 = 300.0

User: "what time is it?"
  → utility(action="timestamp")
  → Output: "Local time: 2026-07-30 14:55:52"

User: "format this JSON"
  → utility(action="json_format", data='{"name":"Zeus","version":6}')
  → Output: formatted JSON with indentation
```

---

## Dynamic Tool Creation

Zeus can create tools from natural language descriptions with automatic
pip dependency installation.

```
User: "create a tool that converts markdown to html"
  → /tool create markdown-to-html: convert markdown files to html
  → Tool created. Dependencies: markdown (pip install)
  → You can now use: markdown-to-html(path="file.md")
```

---

## Tool Statistics

| tool (file) | lines | priority | uses LLM? |
|---|---|---|---|
| terminal (terminal.py) | ~60 | ⚡ fast | no |
| file (file.py) | ~100 | ⚡ fast | no |
| utility (utils.py) | ~100 | ⚡ fast | no |
| search_files (search_files.py) | ~150 | ⚡ fast | no |
| structured_file (structured.py) | ~200 | ⚡ medium | no |
| web_search (web.py) | ~130 | 🌐 medium | no |
| web_fetch (web_fetch.py) | ~140 | 🌐 medium | no |
| code_exec (code.py) | ~100 | ⚡ fast | no |
| session_search (search_session.py) | ~50 | 📚 medium | no (FTS5) |
| api_call (api_call.py) | ~100 | 🌐 medium | no |
| image (image.py) | ~150 | 🖼 medium | no |
