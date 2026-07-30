# Zeus Agent

**Next-generation AI agent framework.** Self-evolving, modular, EventBus-driven.
Phase 6 complete — feature parity with Hermes, but with a fundamentally better architecture.

## Philosophy

> "The best agent is the one that writes itself."

1. **Agents should improve from every task** — not just remember, but restructure their own capabilities
2. **EventBus > Pipeline** — modules are independent, can be added/replaced without touching others
3. **Context is a limited resource** — every byte must earn its place; ContextManager enforces budget
4. **Tools are dynamic** — a fixed tool list is a crutch; generating tools at runtime unlocks infinite capability
5. **The user is a collaborator, not a supervisor** — ask when it matters, proceed silently when it doesn't

## Quick Start

```bash
# Single query
python -m zeus "Hello, who are you?"

# Interactive mode (recommended)
python -m zeus --interactive

# Check provider health
python -m zeus --doctor

# List providers
python -m zeus --providers

# Use specific provider
python -m zeus --provider openrouter --model deepseek/deepseek-v4-flash-free "search for python"
```

## Features

### 🧩 EventBus Architecture
10+ independent modules communicating via typed events:
- `classifier` — intent classification
- `memory` — cross-session SQLite+FTS5
- `router` — intent routing (command, chat, LLM, pipeline)
- `pipeline` — DAG execution for complex tasks
- `reflection` — task analysis + auto-tools
- `sub_agent` — parallel sub-agent management
- `mcp` — project context server
- `self_review` — user-in-the-loop code review
- `telemetry` — performance monitoring + insights
- `gateway` — Telegram bridge

### 🛠 Tool Ecosystem (9 tools)
| Tool | Purpose |
|------|---------|
| `terminal` | Execute shell commands |
| `file` | Read, write, search files |
| `web_search` | DuckDuckGo web search |
| `web_fetch` | Fetch & extract URL content |
| `structured_file` | Patch (fuzzy find/replace), write, read |
| `code_exec` | Isolated Python subprocess |
| `session_search` | Semantic search across all past sessions |
| `search_files` | Regex file content search (rg/fallback) |
| `utility` | Calculator, timestamp, UUID, JSON format |
| + dynamic | Create tools from natural language + pip deps |

### 🧠 Memory System (4 layers)
| Layer | What | Persistence |
|-------|------|-------------|
| ConversationBuffer | Last 20 dialog turns | In-memory |
| SessionStore | All messages across all sessions | SQLite + FTS5 |
| FactStore | Entity-resolved facts with trust scoring | SQLite |
| UserProfile | Language, style, environment preferences | SQLite |

### 📚 Skills
SKILL.md format with YAML frontmatter. Auto-discovered from `~/.zeus/skills/`.
- `/skill list` — show available skills
- `/skill show <name>` — view skill details
- `/skill create <name>: <description>` — create new skill
- `/do <name>` — execute a skill

### 📊 Self-Review (user-in-the-loop)
```bash
/review scan     # Heuristic code analysis (long functions, bare except, etc.)
/review list     # Pending suggestions
/review show 3   # View suggestion details
/review approve 3  # Accept changes
/review reject 3   # Reject suggestions
```

### 📈 Telemetry
```bash
/stats     # Module performance (latency bars)
/insights  # Bottleneck detection + recommendations
/errors    # Error reports
```

### 🔄 Context Management
- Model-aware context budget (auto from provider data)
- Smart pruning (priority: system > user > assistant > tool)
- Dynamic injection: profile + facts + skills
- Token estimation by language

### 💪 Resilience
- Retry with exponential backoff
- Fallback provider chain
- Health tracking with auto-recovery
- Response caching (LRU + TTL)

## Interactive Commands

| Command | Description |
|---------|-------------|
| `/help` | Show all commands |
| `/history` | Show current conversation |
| `/search <query>` | Search past sessions |
| `/remember <fact>` | Save a fact |
| `/forget <query>` | Forget a fact |
| `/facts` | Show all saved facts |
| `/profile` | Show user profile |
| `/skills` | List available skills |
| `/skill show <name>` | View skill details |
| `/skill create <name>: <desc>` | Create new skill |
| `/do <name>` | Execute a skill |
| `/stats` | Telemetry stats |
| `/insights` | Performance insights |
| `/errors` | Error reports |
| `/review scan` | Run code review |
| `/review list` | Pending reviews |
| `/tools` | List available tools |
| `/tool <name>` | Show tool help |
| `/context` | Show context budget |
| `/exit` | Quit |

## Architecture

```
                    EventBus
    ┌───────────────┬──┼──┬───────────────┐
    │               │  │  │               │
    ▼               ▼  ▼  ▼               ▼
┌─────────┐ ┌─────────┐ ┌─────────┐ ┌──────────┐
│classifier│  │ memory  │ │ router  │ │ pipeline │
│(intent)  │  │(facts)  │ │(routing)│ │(DAG exec)│
└─────────┘ └─────────┘ └─────────┘ └──────────┘
┌──────────┐ ┌─────────────┐ ┌─────────┐ ┌───────────┐
│reflection│ │sub_agent_mgr│ │   mcp   │ │self_review│
│(patterns)│ │(parallel)   │ │(context)│ │(heuristic)│
└──────────┘ └─────────────┘ └─────────┘ └───────────┘
┌───────────┐ ┌──────────┐ ┌──────────┐
│telemetry  │ │  gateway │ │  skills  │
│(monitor)  │ │(Telegram)│ │(proced.) │
└───────────┘ └──────────┘ └──────────┘
```

## Project Structure

```
zeus-agent/
├── README.md           <-- This file
├── TOOLS.md            <-- Tool reference + scenarios
├── COMMANDS.md         <-- CLI commands reference
├── ARCHITECTURE.md     <-- Architecture documentation
├── PLAN.md             <-- Development plan
├── BENCHMARK.md        <-- Performance benchmarks
├── ROADMAP.md          <-- Future roadmap
└── zeus/
    ├── __init__.py
    ├── __main__.py          -- CLI entry point
    ├── module.py            -- EventBus, Module, ModuleManager
    ├── llm.py               -- LLM client factory
    ├── config.py            -- YAML + env config
    ├── context.py           -- ContextManager (budget + pruning)
    ├── context_budget.py    -- Model context windows
    ├── skills.py            -- SkillManager
    ├── resilient.py         -- ResilientLLM (retry + fallback)
    ├── performance.py       -- CachedLLM (LRU + TTL)
    ├── delegate.py          -- DelegateManager (subprocess child agents)
    ├── memory/
    │   ├── session.py       -- SessionStore (SQLite + FTS5)
    │   ├── history.py       -- ConversationBuffer + HistorySearcher
    │   ├── profile.py       -- UserProfile + FactStore
    │   └── extractor.py     -- Fact extraction
    ├── modules/
    │   ├── classifier.py
    │   ├── memory.py
    │   ├── router.py
    │   ├── pipeline.py
    │   ├── reflection.py
    │   ├── sub_agent.py
    │   ├── mcp.py
    │   ├── self_review.py
    │   ├── telemetry.py
    │   └── gateway.py
    ├── tools/               -- 9 built-in tools
    │   ├── registry.py      -- Central ToolRegistry
    │   ├── terminal.py
    │   ├── file.py
    │   ├── web.py
    │   ├── web_fetch.py
    │   ├── structured.py
    │   ├── code.py
    │   ├── search_session.py
    │   ├── search_files.py
    │   ├── utils.py
    │   └── dynamic.py       -- NL-to-tool creation
    └── providers/           -- 32+ LLM provider plugins
```

## Requirements

- **Python 3.10+**
- SQLite (built-in)
- Optional: ripgrep (rg) for faster file search
- Optional: Telegram bot token (for gateway)

No external databases, no Docker, no heavy dependencies.

## Status

**Phase 6 complete — Feature Parity.** 10 EventBus modules, 9 tools, 4-layer memory, skills, delegate, resilience, caching.

---

*"Я не копіюю себе, я будую."*
