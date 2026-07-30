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

### P4.2 ✦ Config system (IN PROGRESS)
- [x] YAML config для Zeus (zeus.yaml → ~/.zeus/config.yaml)
- [x] Config reader/merger: defaults → file → env → CLI
- [x] Per-module enable/disable
- [x] ZeusConfig.get() через dot-notation
- [x] `python -m zeus --config` — показати поточну конфігурацію
- [ ] Auto-discovery модулів з директорії zeus/modules/
- [ ] ModuleManager.load_all() — завантажити всі активні модулі з config

### P4.3 ✦ SchedulerModule — cron scheduling як модуль EventBus
- [ ] Конвертувати `proactive.py` (background thread) в SchedulerModule
- [ ] Jobs зберігаються в SQLite (persistent)
- [ ] Wake-up з тригерів (Event від інших модулів)
- [ ] CLI: /schedule list, /schedule add, /schedule remove
- [ ] Підтримка cron-синтаксису ("every 30m", "0 9 * * *")

### P4.4 ✦ Module auto-loading
- [ ] Zeus сканує `zeus/modules/*.py` при старті
- [ ] Кожен модуль має `check_fn()` — чи доступний він
- [ ] ModuleManager.load_all() — завантажити всі активні модулі

### P4.5 ✦ Error recovery
- [ ] Graceful shutdown модулів (SIGTERM → stop_all())
- [ ] Per-module restart при failure (max 3 retries)
- [ ] Логування в ~/.zeus/logs/zeus.log
- [ ] Health check endpoint (для gateway)

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
2. Тест (ручний або автоматизований)
3. Коміт з описом
4. Позначка `[x]` в плані

Починаємо з P4.1 — GatewayModule.
