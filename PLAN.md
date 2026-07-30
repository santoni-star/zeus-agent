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

### P2.2 ✦ Cross-session memory
- [ ] Після кожної сесії — автоматичне збереження ключових фактів
- [ ] При старті — предзавантаження релевантних фактів
- [ ] FTS5 пошук по всіх сесіях

### P2.3 ✦ Proactive Engine v2
- [ ] Pattern triggers: "якщо з'явилась помилка N разів → дія"
- [ ] Memory triggers: "якщо факт X → перевірити Y"
- [ ] Background watchers: моніторинг директорій, процесів

---

## P3. Sub-Agent Orchestration

### P3.1 ✦ Sub-agent spawning
- [ ] Кожен субагент — це окремий Module (ізольований контекст)
- [ ] SubAgentManager — створює/знищує субагентів
- [ ] Event-based комунікація між субагентами
- [ ] Агрегація результатів від кількох субагентів

### P3.2 ✦ MCP Context Server
- [ ] MCP module — читає контекст проекту (files, git log, issues)
- [ ] Підписка на `pipeline.request` — додає контекст перед Planner
- [ ] Працює паралельно (не блокує pipeline)

### P3.3 ✦ Parallel DAG execution
- [ ] Незалежні ноди DAG виконуються паралельно (async)
- [ ] Динамічне додавання нод під час виконання
- [ ] Deadlock detection

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