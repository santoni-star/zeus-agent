# Zeus Agent — План до суперагента

**Поточний стан:** Phase 0 завершено. Модульна архітектура (EventBus + 4 modules).
**Мета:** Повноцінний агент з meta-cognition, самовдосконаленням, паралельними субагентами.

---

## P1. Meta-Cognition Engine

### P1.1 ✦ CLI на модульній архітектурі (DONE)
- [x] `main()` створює ModuleManager + стартує всі модулі
- [x] Interactive loop читає `user.output` події замість прямого виклику `process()`
- [x] `/tools`, `/schedule`, `/memory` команди через модулі (обгортки)
- [x] Старий `process()` видалено, замінено на `process_via_modules()`

### P1.2 ✦ Reflection — аналіз виконаних задач (DONE)
- [x] Після `task.completed` — ReflectionModule аналізує:
  - Що спрацювало? Які тулзи використовувались?
  - Чи можна створити тулзу/скіл для цього патерну?
- [x] Якщо патерн повторюється (2+ рази) → авто-створення тулзи
- [x] Після `task.failed` — аналіз помилки, збереження для самоаналізу

### P1.3 ✦ Planner self-tuning (DONE)
- [x] Fast path: прості запити (currency, search, file, terminal) → 1-node DAG без LLM
- [x] Трекінг стратегій: success rate per strategy (fast_path vs llm_planner)
- [x] Self-tuning: якщо fast_path має >80% success → автоматичний вибір

### P1.4 ✦ Failure handling per sub-tree (DONE)
- [x] Якщо node fail → retry (до node.retry разів)
- [x] Якщо retry fail → реплан тільки цієї гілки DAG
- [x] Логування помилок для Reflection
- [x] Cancelled nodes позначені, незалежні гілки продовжують

---

## P2. Dynamic Tools & Memory

### P2.1 ✦ Динамічні тулзи з депенденсами (DONE)
- [x] Тулза може мати REQUIREMENTS: у docstring
- [x] Auto-install pip залежностей при створенні
- [x] Auto-install при завантаженні (discover_custom_tools)
- [x] /tools install-deps (через auto-install)

### P2.2 ✦ Cross-session memory (DONE)
- [x] Auto-save key facts після кожної сесії (interaction, task_goal, task_step)
- [x] FTS5 пошук по всіх сесіях (через SessionStore.search())
- [x] MemoryModule: підписка на user.input → save + context search
- [x] Pipeline: context.request до Memory перед плануванням

### P2.3 ✦ Proactive Engine v2 (DONE)
- [x] Watchdog triggers: check_fn кожні N секунд → якщо True → fire task
- [x] Memory triggers: пошук факту в пам'яті → якщо знайдено → fire task
- [x] CLI: /schedule support through SchedulerModule

---

## P3. Sub-Agent Orchestration

### P3.1 ✦ Sub-agent spawning (DONE)
- [x] Кожен субагент — SubAgentInstance (ізольований контекст)
- [x] SubAgentManager — spawn/трекінг/cleanup
- [x] Event-based комунікація (sub_agent.completed)
- [x] Три типи: llm_call, terminal, search
- [x] Parallel spawning: asyncio.create_task()

### P3.2 ✦ MCP Context Server (DONE)
- [x] MCP module: reads README, AGENTS.md, CLAUDE.md
- [x] Git context: branch, status, recent commits
- [x] Directory structure: top-level listing
- [x] File search: keyword-based grep for relevant files
- [x] Context caching: 30s TTL to avoid repeated reads
- [x] Event-based: responds to context.request and pipeline.request

### P3.3 ✦ Parallel DAG execution (DONE)
- [x] Незалежні ноди DAG виконуються паралельно (asyncio.to_thread)
- [x] Thread-safe: кожен thread отримує snapshot results
- [x] Exception handling: return_exceptions=True
- [x] Deadlock detection: safety break через max iterations
- [x] execute_dag_async() — нова функція для паралельного DAG

---

## P4. Production

### P4.1 ✦ Telegram Gateway як модуль
- [ ] GatewayModule — підписується на `user.output`, відправляє в Telegram
- [ ] Отримує повідомлення з Telegram → `user.input`
- [ ] Працює паралельно з CLI

### P4.2 ✦ Config system
- [ ] YAML config для модулів
- [ ] Per-module enable/disable
- [ ] Auto-discovery модулів з директорії

### P4.3 ✦ Cron scheduling через модуль
- [ ] SchedulerModule — незалежний модуль
- [ ] Jobs зберігаються в SQLite (persistent)
- [ ] Wake-up з тригерів

---

## P5. Self-Evolution

### P5.1 ✦ Self-code review
- [ ] Zeus читає свій код, аналізує неефективності
- [ ] Пропонує патчі (користувач апрувить)
- [ ] Використовує dynamic tools для рефакторингу

### P5.2 ✦ Architecture evolution
- [ ] Відстежує які модулі навантажені найбільше
- [ ] Пропонує міграцію на окремі процеси
- [ ] Self-tuning на основі метрик продуктивності

---

## Виконання

Кожен пункт виконується як:
1. Реалізація коду
2. Тест (ручний або автоматичний)
3. Коміт з описом
4. Позначка `[x]` в плані

Починаємо з P1.1.