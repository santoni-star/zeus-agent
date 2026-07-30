# Zeus vs Hermes — Benchmark Results

**Date:** 2026-07-30
**Model:** deepseek-v4-flash-free via opencode-zen
**Environment:** Termux on Android 16 (Xiaomi HyperOS)

## Results

| Task | Zeus | Hermes | Notes |
|------|------|--------|-------|
| Simple chat | ✅ 246ms | ❌ | Hermes requires PTY |
| Simple command | ✅ 258ms | ❌ | Hermes requires PTY |
| Web search | ✅ 6.2s | ❌ | Zeus creates DAG, executes tools |
| Multi-step search→extract→write | ✅ 7.5s | ❌ | 2-node DAG with tool + LLM |
| Complex research | ✅ 5.2s | ❌ | 1-node DAG (web search) |

**Zeus: 5/5 ✅ | Hermes: 0/5 ❌**

## Key Findings

1. **Zeus works headless** — CLI works via subprocess, pipes, automation
2. **Hermes requires PTY** — designed for interactive terminal use
3. **Zeus DAG pipeline** — creates Task DAGs, executes tools, synthesizes
4. **Zeus Provider system** — 32 providers, auto-config from Hermes

## Speed Analysis (Zeus only)

- Simple operations: **~250ms** (no LLM overhead)
- Web search: **~300ms** per tool call
- LLM extraction: **~2-5s** per call (depends on model)
- Full multi-step: **~5-8s** for search + extract + save

## Phase 0 Verdict

**Zeus Phase 0 is functional** — it can solve real tasks via:
- Planner → DAG → Runtime → Synthesizer pipeline
- 3 tools: terminal, file, web_search
- LLM nodes for reasoning/extraction
- Auto-configuration from Hermes or env vars

Next: improve Planner reliability for complex multi-step DAGs,
add more tools, implement L0 Proactive Engine.