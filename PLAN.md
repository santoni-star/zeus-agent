# Zeus Agent — План до суперагента

**Поточний стан:** Phase 3 завершено (Sub-Agent Orchestration).
**Мета:** Production-ready агент з модульною архітектурою, Telegram gateway, конфіг системою, cron.

---

## P4. Production (поточна фаза)

### P4.1 ✦ GatewayModule — Telegram gateway як EventBus модуль
- [ ] Створити GatewayModule у `zeus/modules/gateway.py`
  - Підписується на `user.output` → відправляє в Telegram
  - Отримує повідомлення з Telegram → публікує `user.input`
  - Працює паралельно з CLI та іншими модулями
- [ ] Інтегрувати TelegramBot API (python-telegram-bot або власний HTTP клієнт)
- [ ] Підтримка команд: /start, /help, /memory, /tools
- [ ] Підтримка кількох чатів (group, private)
- [ ] Message queue для гарантованої доставки

### P4.2 ✦ Config system
- [ ] YAML config для модулів (zeus.yaml)
- [ ] Per-module enable/disable
- [ ] Auto-discovery модулів з директорії `zeus/modules/`
- [ ] Zeug config merge: cli args > zeus.yaml > Hermes auto-config
- [ ] `python -m zeus --config list` — показати поточну конфігурацію

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
