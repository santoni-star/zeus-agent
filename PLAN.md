# Zeus Agent — План до суперагента

**Поточний стан:** Phase 3 завершено (Sub-Agent Orchestration).
**Мета:** Production-ready агент з модульною архітектурою, Telegram gateway, конфіг системою, cron.

---

## P4. Production (поточна фаза)

- [x] GatewayModule — Telegram gateway як модуль EventBus
  - Підписується на user.output → відправляє в Telegram
  - Отримує повідомлення з Telegram → публікує user.input
  - Працює паралельно з CLI та іншими модулями
  - Інтегровано TelegramBot API через zeus/gateway.py
  - Підтримка команд: /gateway, /gw
  - Batched delivery (1s debounce)
  - CLI: --gateway, --gateway-token, --gateway-chat флаги

### P4.2 ✦ Config system (DONE)
- [x] YAML config для Zeus (zeus.yaml → ~/.zeus/config.yaml)
- [x] Config reader/merger: defaults → file → env → CLI
- [x] Per-module enable/disable
- [x] ZeusConfig.get() через dot-notation
- [x] `python -m zeus --config` — показати поточну конфігурацію
- [x] Auto-discovery модулів з директорії zeus/modules/
- [x] ModuleManager.load_all() — завантажити всі активні модулі з config

### P4.3 ✦ SchedulerModule — cron scheduling як модуль EventBus (DONE)
- [x] Конвертувати `proactive.py` (background thread) в SchedulerModule
- [x] Jobs зберігаються в SQLite (persistent)
- [x] Wake-up з тригерів (Event від інших модулів)
- [x] CLI: /schedule through EventBus events
- [x] Підтримка cron-синтаксису (interval, watchdog, memory trigger)

### P4.4 ✦ Module auto-loading (DONE via config.py)
- [x] Zeus сканує `zeus/modules/*.py` при старті (+ discover_modules())
- [x] Кожен модуль має параметри через конструктор
- [x] ModuleManager.load_from_config() — завантажити всі активні модулі

### P4.5 ✦ Error recovery (DONE via ModuleManager)
- [x] Graceful shutdown модулів (asyncio.gather with return_exceptions)
- [x] Per-module restart при failure (asyncio.gather не падає при помилці одного)
- [x] Логування через logging (всі модулі)
- [x] Health check через doctor + gateway status

---

## P5. Self-Evolution

### P5.1 ✦ Self-code review (DONE)
- [x] SelfReviewModule — EventBus модуль для аналізу коду
- [x] Евристичний сканер: довгі функції, bare except, глибока вкладеність, sync/async
- [x] LLM сканер (якщо модель підтримує великі промпти)
- [x] ReviewStore (SQLite) для збереження пропозицій
- [x] User-in-the-loop: scan → list → show → approve/reject
- [x] CLI: /review scan, /review list, /review show, /review approve, /review reject
- [x] Авто-скан кожні 10 задач
- [x] Код не змінюється без апруву користувача

### P5.2 ✦ Architecture evolution (DONE)
- [x] TelemetryStore: SQLite для метрик продуктивності модулів
- [x] TelemetryModule: авто-запис подій (duration, LLM calls, success rate)
- [x] Architecture insights: bottleneck detection, error analysis, LLM efficiency
- [x] CLI: /stats, /telemetry, /insights, /errors
- [x] Per-module latency bars, success rates, event counts

---

## Виконання

Кожен пункт виконується як:
1. Реалізація коду
2. Тест (ручний або автоматизований)
3. Коміт з описом
4. Позначка `[x]` в плані

Починаємо з P4.1 — GatewayModule.
