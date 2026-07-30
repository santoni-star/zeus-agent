# Zeus Agent — Architecture

## Core Principle: Stream Processor + Task Runtime

Zeus replaces the traditional **agent loop** (LLM → tool → LLM → tool → ...) with a **two-tier pipeline** that separates reasoning from execution.

The key insight: **half the steps in an agent loop don't need an LLM**. Zeus calls the LLM only when reasoning is required — everything else is deterministic code.

```
┌──────────────────────────────────────────────────────────────┐
│                    USER INPUT                                 │
│  "знайди останню версію python, завантаж, встанови"         │
└────────────────────────┬─────────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────────┐
│  L1 — STREAM PROCESSOR  (мінімум LLM)                       │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │ Classifier   │  │ Router       │  │ Queue            │   │
│  │ (LLM fast)   │  │ (code)       │  │ (code)           │   │
│  │ маленька     │  │ match/case   │  │ priority + dedup │   │
│  │ модель       │  │ на типі      │  │                  │   │
│  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘   │
│         │                 │                    │              │
│  ┌──────▼─────────────────▼────────────────────▼──────────┐  │
│  │  "це складна задача" → L2                              │  │
│  │  "просте питання"    → Direct LLM reply                │  │
│  │  "команда"           → TerminalExec                    │  │
│  │  "пошук скіла"       → SkillsClient                    │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  LLM викликів: 1 (маленька модель, 1B-3B параметрів)       │
└────────────────────────┬─────────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────────┐
│  L2 — TASK RUNTIME (для складних задач)                     │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  1. Planner (LLM, 1 виклик)                            │  │
│  │     • Отримує: запит + список доступних інструментів   │  │
│  │     • Повертає: Task DAG (JSON)                        │  │
│  │     • DAG = граф залежностей з нодами:                 │  │
│  │       - tool: виклик інструмента                       │  │
│  │       - llm:  виклик LLM для підзадачі                 │  │
│  │       - wait: очікування батьківських нод              │  │
│  │       - merge: об'єднання результатів                   │  │
│  └────────────────────────────────────────────────────────┘  │
│                              │                                │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  2. DAG Executor (код, 0 LLM)                          │  │
│  │     • Топологічний обхід графа                         │  │
│  │     • Паралельне виконання незалежних нод              │  │
│  │     • Retry + timeout на кожну ноду                    │  │
│  │     • Прогрес-репортинг                                │  │
│  └────────────────────────────────────────────────────────┘  │
│                              │                                │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  3. Synthesizer (LLM, 1 виклик)                        │  │
│  │     • Отримує: всі результати DAG                      │  │
│  │     • Повертає: відповідь користувачу                  │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  LLM викликів: 2 (Planner + Synthesizer)                    │
│  + 1 (Error recovery, тільки якщо щось впало)              │
└────────────────────────┬─────────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────────┐
│  L0 — PROACTIVE ENGINE  (фоновий, завжди працює)            │
│                                                              │
│  • Watchdogs (моніторинг: диск, мережа, залежності)         │
│  • Triggers (події: git push, session_end, error)           │
│  • Decision Engine (чи варто турбувати користувача?)         │
│  • Action Queue (відкладена доставка повідомлень)            │
│                                                              │
│  Phase 2+. В Phase 0 тільки закладаємо інтерфейси.          │
└──────────────────────────────────────────────────────────────┘
```

## Реальна кількість LLM викликів

| Компонент | LLM? | Модель | Викликів на задачу |
|-----------|------|--------|--------------------|
| Classifier | Так | Маленька (1B-3B) | 1 |
| Router | Ні | Код | 0 |
| Queue | Ні | Код | 0 |
| Planner | Так | Головна | 1 |
| DAG Executor | Ні | Код | 0 |
| Error Recovery | Так | Головна | 1 (тільки при помилці) |
| Synthesizer | Так | Головна | 1 |

**Типова задача:** 2-3 LLM виклики (1 дешевий + 1-2 дорогих)
**Звичайний agent loop:** 10-50 LLM викликів

## Task DAG — формат

```json
{
  "goal": "Знайди останню версію Python, завантаж і встанови",
  "nodes": [
    {
      "id": "search_version",
      "type": "tool",
      "tool": "web_search",
      "params": {"query": "latest python version download"},
      "success_criteria": "отримано URL з python.org",
      "retry": 3
    },
    {
      "id": "download",
      "type": "tool",
      "tool": "terminal",
      "params": {"command": "curl -Lo python.tar.gz {{nodes.search_version.result}}"},
      "depends_on": ["search_version"],
      "success_criteria": "файл завантажено",
      "retry": 2
    },
    {
      "id": "install",
      "type": "tool",
      "tool": "terminal",
      "params": {"command": "tar -xzf python.tar.gz && cd python-* && ./configure && make"},
      "depends_on": ["download"],
      "timeout": 300,
      "success_criteria": "python --version працює"
    },
    {
      "id": "verify",
      "type": "tool",
      "tool": "terminal",
      "params": {"command": "python3 --version"},
      "depends_on": ["install"]
    }
  ]
}
```

## Шари пам'яті (оновлено)

```
┌─────────────────────────────────────────────────────────────┐
│                 RETRIEVAL ORCHESTRATOR                       │
│  "What does Zeus need RIGHT NOW?"                            │
│  Fusion з кількох шарів                                      │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌─────────────────┐  ┌─────────────────┐                    │
│  │ L1: WORKING      │  │ L2: EPISODIC    │                    │
│  │ (in-context)     │  │ (structured)    │                    │
│  │ • Поточний DAG   │  │ • goal, outcome │                    │
│  │ • Останні ходи   │  │ • key decisions │                    │
│  │                  │  │ • corrections   │                    │
│  │                  │  │ ✖ tool output   │                    │
│  └─────────────────┘  └─────────────────┘                    │
│                                                               │
│  ┌─────────────────┐  ┌─────────────────┐                    │
│  │ L3: SEMANTIC    │  │ L4: RAW ARCHIVE  │                    │
│  │ (knowledge)     │  │ (gzip, no index) │                    │
│  │ • user prefs    │  │ • повна історія  │                    │
│  │ • environment   │  │ • для trace only │                    │
│  │ • projects      │  │                  │                    │
│  │ • trust scores  │  │                  │                    │
│  └─────────────────┘  └─────────────────┘                    │
│                                                               │
│  Memory Extractor (LLM, 1 виклик після сесії):               │
│  бере сиру сесію → витягує goal, decisions, corrections,    │
│  outcome → зберігає в L2, витягує факти → зберігає в L3    │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

## Phase 0 архітектура (зараз)

Мінімально працездатний продукт:

```
zeus/
├── __init__.py
├── classifier.py     # Intent classification (tiny LLM or keyword)
├── router.py         # Route request to handler
├── planner.py        # LLM call → Task DAG JSON
├── runtime.py        # DAG executor (no LLM)
├── synthesizer.py    # LLM call → final response
├── tools/
│   ├── __init__.py
│   ├── terminal.py   # Shell command execution
│   ├── file.py       # File read/write/search
│   └── web.py        # Web search (DuckDuckGo)
├── memory/
│   ├── __init__.py
│   ├── episodic.py   # Structured session store
│   └── semantic.py   # Fact store
├── models/
│   ├── __init__.py
│   ├── dag.py        # Task DAG data model
│   └── types.py      # Shared types
└── cli.py            # Entry point
```

## Філософія Zeus

1. **LLM — це ресурс, не контролер.** Не кожен крок потребує мислення.
2. **Планування — це інвестиція.** Один хороший план дешевший за 50 спроб.
3. **Помилки ізольовані.** Впала одна нода DAG — перезапускаємо тільки її.
4. **Пам'ять — це структура, не сховище.** Якість > кількість.
5. **Проактивність — це повага.** Не турбувати без потреби.

---

*Архітектура жива. Змінюється з досвідом.*