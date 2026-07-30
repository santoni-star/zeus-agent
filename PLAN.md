# Zeus Agent — Development Plan

## Phase 0-3: Core (done)
- EventBus + Module architecture
- Stream Processor + Task Runtime
- Dynamic Tools (auto-create from NL)
- Fast Path Planner (self-tuning)
- Sub-agent management
- MCP Protocol integration
- Reflection & Tool Creation

## Phase 4: Production (done)
- GatewayModule (Telegram bridge)
- Config system (YAML + env + CLI)
- SchedulerModule (cron + SQLite)
- Module auto-loading
- Error recovery

## Phase 5: Self-Evolution (done)
- SelfReviewModule (heuristic scan + user-in-the-loop)
- TelemetryModule (performance monitoring + insights)
- Conversation history buffer
- Semantic search across sessions

---

## Phase 6: Agent Completeness (поточна фаза)

Мета: довести Zeus до рівня повноцінного AI агента, здатного працювати
автономно, пам'ятати контекст, мати інструменти і вміти делегувати.

### P6.1 ✦ Tool Ecosystem — повний набір інструментів

Zeus має базові інструменти (terminal, file), але не вистачає:
- web_search — пошук в інтернеті (через DuckDuckGo або Google)
- structured_file — patch() + write_file() з валідацією
- session_search — пошук по власних сесіях (FTS5)
- code_exec — ізольоване виконання Python
- file_search — пошук файлів по імені/вмісту (grep+find)

**Що зробити:**
- [ ] ToolRegistry — центральний реєстр всіх інструментів
- [ ] web_search — HTTP-клієнт для пошуку (DuckDuckGo або requests + html)
- [ ] structured_file — patch (fuzzy find/replace) + write_file (atomic write)
- [ ] session_search — пошук по повідомленнях в SQLite+FTS5
- [ ] code_exec — ізольований Python executor (subprocess + timeout)
- [ ] file_search — пошук файлів по імені та вмісту (via rg/find)
- [ ] tool_help — кожен інструмент має опис, параметри, приклади
- [ ] Tool call validation — перевірка параметрів перед викликом
- [ ] Error recovery — повтор при таймаутах

### P6.2 ✦ Persistence & Memory — довготривала пам'ять

Zeus не пам'ятає користувача між сесіями. Потрібно:
- USER.md — профіль користувача (мова, стиль, уподобання)
- MEMORY.md — фактів (налаштування, конвенції, lessons learned)
- fact_store — entity-resolved пам'ять з trust scoring
- auto-inject — автоматичне додавання релевантної пам'яті в context

**Що зробити:**
- [ ] UserProfile — зберігання і автоматичне оновлення профілю
- [ ] FactMemory — SQLite + FTS5 з entity resolution
- [ ] Trust scoring — вага фактів на основі частоти згадування
- [ ] Auto-inject — вибір релевантних фактів перед кожним запитом
- [ ] Memory CLI — /remember, /forget, /facts команди
- [ ] Memory auto-save — збереження фактів після кожної відповіді

### P6.3 ✦ Skill System — процедурні знання

Skills — це багаторазові процедури для специфічних задач.
Zeus може створювати, зберігати і використовувати skills.

**Що зробити:**
- [ ] SkillStore — SQLite сховище для skills
- [ ] SKILL.md формат — YAML frontmatter + markdown body
- [ ] Skill auto-load — завантаження релевантних skills по темі
- [ ] Skill creation — /skill create <name> з NL опису
- [ ] Skill execution — виклик skill по імені
- [ ] Skill improvement — авто-оновлення на основі помилок

### P6.4 ✦ Delegation & Parallelism — саб-агенти

Zeus повинен вміти spawn-ити ізольовані підагенти для:
- Паралельної роботи (дослідження + код одночасно)
- Ізольованих експериментів (не засмічують контекст)
- Фонової обробки (не блокують основний потік)

**Що зробити:**
- [ ] ChildAgent — ізольований контекст з власним LLM та інструментами
- [ ] delegate_task — API для spawn-у підагентів
- [ ] Result collector — збір і агрегація результатів
- [ ] Parallel executor — запуск N підагентів одночасно
- [ ] Timeout management — обмеження часу виконання

### P6.5 ✦ Provider Resilience — надійність LLM

Один провайдер → одна точка відмови.

**Що зробити:**
- [ ] Provider chain — fallback ланцюжок (основний → запасний)
- [ ] Retry with backoff — повтор при 429/503 з exponential backoff
- [ ] Model selector — auto-вибір моделі за складністю задачі
- [ ] Cost tracking — облік токенів по провайдерах
- [ ] Health checks — періодична перевірка доступності

### P6.6 ✦ Performance — оптимізація

**Що зробити:**
- [ ] Response cache — кешування повторних запитів (LRU)
- [ ] LLM call dedup — уникнення однакових викликів в одному turn
- [ ] Context pruning — автоматичне скорочення контексту
- [ ] Batch processing — об'єднання дрібних запитів
- [ ] Lazy loading — відкладена ініціалізація модулів

---

## Поточний стан

```
Phase 6 progress: [#######___] 35%
  P6.1 Tools:      [######### ] 90% — ToolRegistry + structured_file + code_exec + session_search
  P6.2 Memory:     [########  ] 80% — UserProfile + FactStore + CLI (/remember, /facts)
  P6.3 Skills:     [__________] 0%
  P6.4 Delegate:   [__________] 0%
  P6.5 Resilience: [__________] 0%
  P6.6 Performance: [__________] 0%
```

Загальна мета Phase 6 — функціональний паритет з Hermes Agent.
Zeus має працювати як повноцінний AI асистент з інструментами,
пам'яттю, скілами та можливістю делегування.
