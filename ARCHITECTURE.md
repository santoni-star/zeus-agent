# Zeus Agent — Architecture

## Core Architecture: EventBus + Modules

Zeus replaces the traditional **agent loop** (LLM → tool → LLM → tool → ...)
with a **modular EventBus architecture** where independent modules communicate
via typed events.

```
                  ┌──────────────────── EVENT BUS ────────────────────┐
                  │                                                    │
    ┌─────────────┼──────────────┬──────────────┬──────────────────┐   │
    │             │              │              │                  │   │
    ▼             ▼              ▼              ▼                  ▼   │
┌────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────────┐ │
│classif│ │  memory  │ │  router  │ │ pipeline │ │  sub_agent     │ │
│       │ │          │ │          │ │          │ │                │ │ │
│▶ user │ │▶ fact    │ │▶ command │ │▶ DAG exec│ │▶ parallel      │ │ │
│  input│ │  save    │ │▶ chat    │ │▶ tool    │ │  spawn         │ │ │
│       │ │▶ context │ │▶ LLM     │ │  orchest│ │  isolation     │ │ │
└───────┘ └──────────┘ └──────────┘ └──────────┘ └────────────────┘ │
┌────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────────┐ │
│reflect │ │  mcp     │ │self_rev │ │telemetry │ │  gateway       │ │
│        │ │          │ │          │ │          │ │                │ │ │
│▶ error │ │▶ project │ │▶heurist │ │▶ latency │ │▶ Telegram      │ │
│  anal. │ │  context │ │  scan   │ │  stats   │ │  bridge        │ │
│▶ tool  │ │  cache   │ │▶ approve│ │▶ insight │ │                │ │
│  creat │ │          │ │  reject │ │  engine  │ │                │ │
└────────┘ └──────────┘ └──────────┘ └──────────┘ └────────────────┘ │
    │             │              │              │                  │   │
    │             │              │              │                  │   │
    └─────────────┴──────────────┴──────────────┴──────────────────┘   │
                                                                       │
    Subscribes to:  user.input, user.output, context.request, ...      │
    Emits:          classification.result, route.result, context.result │
```

### Key Design Decisions

1. **No central agent loop** — each module is independent, subscribes to events it needs
2. **Modules can be disabled** — remove `self_review` from config, it won't load
3. **Modules can be added** — write a new module, register it, it receives events
4. **All modules are async** — non-blocking event processing
5. **Events are typed** — every event has a string type, data dict, and source

---

## Event Flow (Typical Request)

```
User: "search for python 3.14 and save results"
  1. USER_INPUT emitted
  2. ClassifierModule receives → classifies intent
  3. CLASSIFICATION_RESULT emitted
  4. RouterModule receives:
     - If command: terminal.execute()
     - If chat: _exec_chat() with history
     - If simple_question: _exec_llm() with history + profile + facts
     - If complex: emit pipeline.request
  5. PipelineModule receives:
     - ContextManager.build() → injects profile, facts, history, skills
     - Planner → generates DAG
     - Runtime → executes DAG
     - Synthesizer → final response
  6. USER_OUTPUT emitted
  7. MemoryModule receives → saves to SessionStore + FactStore
```

---

## Memory Architecture (4 layers)

```
Layer 1: ConversationBuffer (in-memory)
  • Last 20 turns
  • to_api_messages() for LLM

Layer 2: SessionStore (SQLite + FTS5)
  • All messages from all sessions
  • Full-text search via FTS5
  • /search <query>

Layer 3: FactStore (SQLite + entity resolution)
  • Entity + content + trust score
  • probe(entity), related(entity), reason(entities)
  • Trust: 0.0-1.0, increases on repeat

Layer 4: UserProfile (SQLite)
  • Language, style, environment
  • Auto-extracted from user text
```

---

## Context Management

```
ContextManager.build(user_input):
  1. System prompt          [fixed]
  2. User profile           [auto-inject]
  3. Relevant facts         [keyword search]
  4. Relevant skill         [tag matching]
  5. Conversation history   [pruned to budget]
  6. User input             [always]

Pruning strategy:
  Score = recency + role_bonus(user=0.3, assistant=0.1)
  → drop lowest-scored until fits budget

Budget:
  ContextBudget(context_window=32000)
    - reserve_output: 4096
    - reserve_tools: 2048
    - max_input: 25856 tokens
```

---

## Tool System

```
ToolRegistry (zeus/tools/registry.py)
  ├── register(name, schema, execute_fn)
  ├── call(name, params)        ← validation + execution
  ├── call_with_retry(name, params)  ← retry on failure
  ├── list_tools(category)
  ├── get_help(name)
  └── discover()  ──→ scans zeus/tools/*.py for SCHEMA + execute()
                    ──→ scans ~/.zeus/custom_tools/*.py

Each tool:
  SCHEMA = {
    "name": "tool_name",
    "description": "...",
    "parameters": { ... }  // JSON Schema
  }
  def execute(params: dict) -> str:
      ...
```

---

## Resilience & Performance

```
ResilientLLM(primary, fallbacks=[], max_retries=2)
  ├── Retry with exponential backoff (1s, 2s, 4s)
  ├── Fallback chain: primary → fallback1 → fallback2
  ├── Health tracking: marks unhealthy after max_retries, auto-recovery
  └── LLMFailure: when all providers exhausted

CachedLLM(llm, max_size=100, ttl=300)
  ├── LRU eviction
  ├── Keyed by SHA256 of (messages[-5:] + tools)
  └── Stats: hit_rate, size, hits/misses

DelegateManager(max_workers=3)
  ├── run_task(task, context, timeout) → subprocess child agent
  ├── run_parallel([(task, ctx), ...]) → ThreadPoolExecutor
  └── Child agent has isolated context + tools
```

---

## Skill System

```
SkillManager(~/.zeus/skills/*.md)
  ├── discover() → parses YAML frontmatter
  ├── get(name) → Skill
  ├── find(query) → match by name/description/tags
  ├── find_relevant(task) → scored keyword matching
  └── create(name, description, steps) → writes SKILL.md

Skill (SKILL.md):
  ---
  name: my-skill
  description: What this does
  tags: [python, testing]
  version: 1.0.0
  ---
  ## Steps
  1. First do this
  ## Commands
  ```bash
  python -m pytest
  ```
  ## Pitfalls
  - Watch out for X
```

---

## Module Reference

| Module | Events Subscribed | Events Emitted | Config Key |
|--------|------------------|---------------|------------|
| classifier | user.input | classification.result | — |
| memory | user.input, user.output, memory.save, memory.search, context.request | memory.result, context.result, memory.save | memory.enabled |
| router | classification.result | user.output, pipeline.request | — |
| pipeline | pipeline.request | user.output | — |
| reflection | task.completed, task.failed | memory.save, memory.search | — |
| sub_agent | various | various | — |
| mcp | context.request | context.result | — |
| self_review | user.input | user.output | — |
| telemetry | all events | (none, records to DB) | telemetry.enabled |
| gateway | user.output | user.input | gateway.enabled |

---

## File Layout

```
zeus/
├── __main__.py         CLI entry + interactive loop
├── module.py           EventBus, Module base, ModuleManager
├── llm.py              LLM client factory (32+ providers)
├── config.py           YAML + env config loader
├── context.py          ContextManager (budget + pruning + injection)
├── context_budget.py   Model context windows + token estimation
├── skills.py           SkillManager + Skill
├── resilient.py        ResilientLLM (retry + fallback)
├── performance.py      CachedLLM (LRU + TTL)
├── delegate.py         DelegateManager (child agent subprocess)
├── memory/
│   ├── session.py      SessionStore (SQLite + FTS5)
│   ├── history.py      ConversationBuffer + HistorySearcher
│   ├── profile.py      UserProfile + FactStore
│   └── extractor.py    Fact extraction from text
├── tools/              (9 built-in tools)
│   ├── registry.py     ToolRegistry
│   ├── [tool files]    (~10 files)
│   └── dynamic.py      NL-to-tool creation
├── modules/            (10 EventBus modules)
│   ├── [module files]  (~10 files)
│   └── __init__.py
└── providers/          (32+ provider plugins)
    ├── base.py         ProviderProfile base class
    └── plugins/        (per-provider directories)
```
