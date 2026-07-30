# Zeus Agent

**Next-generation AI agent framework.**
Built from the lessons of Hermes Agent, Pi Agent, Claude Code, and every agent framework that came before.

Zeus is not just another agent loop. It's a **self-improving, meta-cognitive framework** designed around recursive learning, hierarchical planning, and dynamic tool composition.

## Philosophy

> "The best agent is the one that writes itself."

Zeus is built on five core beliefs:

1. **Agents should improve from every task** — not just remember, but restructure their own capabilities
2. **Planning is a separate muscle** — execution without planning is just guessing with tools
3. **Context is a battleground** — every byte of context must earn its place; predictive compression beats reactive truncation
4. **Tools are dynamic** — a fixed tool list is a crutch; generating tools at runtime unlocks infinite capability
5. **The user is a collaborator, not a supervisor** — ask when it matters, proceed silently when it doesn't

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
┌──────────┐ ┌─────────────┐ ┌─────────┐
│reflection│ │sub_agent_mgr│ │   mcp   │
│(patterns)│ │(parallel)   │ │(context)│
└──────────┘ └─────────────┘ └─────────┘
```

## Quick Start

```bash
# Install
pip install -e ~/zeus-agent

# Single query
python -m zeus "Hello"

# Interactive mode
python -m zeus --interactive

# Check health
python -m zeus --doctor

# List providers
python -m zeus --providers
```

## Features (Phase 3)

### EventBus + Module Architecture
7 independent modules communicating via typed events:
classifier, memory, router, pipeline, reflection, sub_agent, mcp

### Parallel DAG Execution
Independent DAG nodes run concurrently via asyncio.to_thread.
Thread-safe results snapshot, exception isolation.

### Dynamic Tools with Pip Dependencies
Tools auto-generate from natural language descriptions.
`/tools create <description>` → auto-installed pip deps.

### Cross-Session Memory
Every interaction auto-saves to SQLite+FTS5.
Context injection before planning.

### Sub-Agent Orchestration
Parallel sub-agent spawning (llm_call, terminal, search types).
Max 3 concurrent. Event-based results.

### MCP Context Server
Project context (README, git, files, directory structure).
30s TTL cache. Event-based responses.

### 32 Native LLM Providers
No Hermes dependency for providers.
OpenAI, Anthropic, OpenRouter, DeepSeek, Google, xAI, Grok, etc.

### Fast Path Planner
Simple queries (currency, search, file, terminal) skip LLM entirely.
Self-tuning: >80% success rate → auto-select fast path.

## Project Structure

```
~/zeus-agent/
├── README.md
├── PLAN.md
├── zeus/
│   ├── __init__.py
│   ├── __main__.py        — CLI entry point
│   ├── module.py          — EventBus, Module, ModuleManager
│   ├── modules/
│   │   ├── classifier.py  — Intent classification
│   │   ├── memory.py      — Cross-session SQLite+FTS5
│   │   ├── router.py      — Intent routing
│   │   ├── pipeline.py    — DAG execution
│   │   ├── reflection.py  — Task analysis + auto-tools
│   │   ├── sub_agent.py   — Parallel sub-agents
│   │   └── mcp.py         — Project context
│   ├── planner.py         — Fast path + LLM Planner
│   ├── runtime.py         — DAG executor
│   ├── synthesizer.py     — Final response from DAG
│   ├── classifier.py      — Legacy classifier
│   ├── router.py          — Legacy router
│   ├── llm.py             — LLM client factory
│   ├── memory.py          — SessionStore (SQLite+FTS5)
│   ├── proactive.py       — Background scheduler
│   ├── models/
│   ├── tools/
│   └── providers/          — 32 LLM provider plugins
```

## Status

**Phase 3 complete** — Sub-Agent Orchestration
**Current:** Phase 4 — Production (GatewayModule, Config system, SchedulerModule)

---

*"Я не копіюю себе, я будую."*
