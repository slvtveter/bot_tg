# Agentic AI Platform

*[Русская версия](README.ru.md) — или прокрутите вниз / scroll down*

A modular Telegram bot that routes each message to a specialized AI agent instead of one generic assistant. It began as a nutrition tracker and grew into a small agent framework where adding a new mode costs a single registry entry plus a system prompt.

## Modes

Switchable per user from the bottom keyboard or the `/mode` command:

- **General** — a smart, to-the-point assistant for any question.
- **Nutrition** — estimates calories and macros from a photo or a text description, answers food/diet questions, and logs daily and weekly totals (`/today`, `/week`).
- **Math** — solves problems step by step, with formulas rendered as proper LaTeX.
- **Fitness** — a personal trainer: workout programs, exercise technique, home and gym alternatives.
- **Writing** — emails, posts, resumes, rewrites, summaries, and translations, returned ready to copy.
- **Code** — a senior engineer: correct, idiomatic code with a short explanation.

The defining behavior across all modes is **adaptive formatting**: the bot answers a simple question in a sentence or two and only reaches for tables, headings, or lists when they genuinely make the answer clearer — no report-style table for every reply.

Beyond text the bot accepts **photos** (a meal in Nutrition, a code screenshot in Code, an exercise in Fitness, and so on) and **voice messages** (transcribed via Gemini's native audio understanding, then handled exactly like typed text). It can also join **group chats**, where it only responds when @mentioned or replied to.

## Commands

| Command | Who | Description |
|---|---|---|
| `/start` | everyone | Register and show the mode keyboard |
| `/mode` | everyone | Switch between modes |
| `/week` | everyone | Last 7 days of nutrition, with a daily average |
| `/settings` | everyone | Response length, creativity, language |
| `/stats` | everyone | Your activity: requests, meals, latency, join date, last seen |
| `/clear` | everyone | Clear history and start a fresh conversation |
| `/feedback <text>` | everyone | Send feedback or an idea (stored and forwarded to admins) |
| `/admin` | admins | Dashboard: growth, active users, latency, key pool, top users, feedback, CSV export |
| `/broadcast <text>` | admins | Message every registered user |
| `/disable_model` `/enable_model` | admins | Runtime kill switch for a model in the fallback chain |

## How it answers: quality and speed

`src/llm.py` is built for good answers that arrive fast, even on shared free-tier keys:

- **Adaptive prompts.** Each mode's system prompt pushes the model to match format to the question and to skip filler, instead of always emitting tables and headings.
- **Speed-first model order.** The chain leads with a fast, high-quality model (gemini-2.5-flash, ~1s); slower or spikier preview/pro models sit lower as fallbacks.
- **Short-term memory.** The bot keeps recent conversation within a token budget, so it follows context across turns without bloating the prompt — frugal on free-tier quotas.
- **Thinking control.** Newer Gemini flash models reason internally before answering, which adds several seconds and burns quota on a trivial question. Fast modes send `thinkingBudget=0` (roughly 4-5x faster, no quality loss for chat); Math keeps thinking on for step-by-step correctness.
- **Layered fallback.** Direct Gemini API with API-key rotation and per-key cooldown on rate limits, then OpenRouter (free community models first) so a request can still succeed at a zero balance.
- **Truncation guard.** A response that looks cut off (unbalanced code fences, dangling table rows, a sentence ending mid-word) is rejected and the next model is tried.

Delivery (`src/sender.py`) tries Telegram's rich-message API first, then HTML built by a hand-rolled Markdown/LaTeX converter (`src/utils.py`), then plain text.

## Statistics that are never wiped

Tracking growth is a first-class goal, so usage data is permanent. The `users`, `stats`, `nutrition_log`, and `feedback` tables are **never auto-deleted** — lifetime totals stay accurate for as long as the bot runs. Per-request telemetry (model, tokens, latency) is real for every channel, including text.

- `/stats` gives each user their own lifetime activity.
- `/admin` shows the whole picture: total users, new today and over 7 days, active in the last 24h and 7 days, all-time and recent request volume, average latency, meals analyzed, and recent feedback.
- The only cleanup action trims **chat history** older than 90 days (message context only); it never touches statistics or users.

## Architecture

```
Telegram update
      |
src/handlers/        Telegram glue (commands, callbacks, messages, photo, voice, inline)
      |
src/orchestrator.py  Picks the agent for the user's current mode
      |
src/agents/          GenericAgent per mode (+ NutritionAgent for macro logging)
      |
src/llm.py           Gemini + OpenRouter client: key rotation, fallback, thinking control
      |
src/sender.py        Formats and sends the reply back to Telegram
```

Modes live in one place, `src/modes.py`: the keyboard, the `/mode` picker, button routing, the command menu, and help all read from it. Adding a mode is one entry there plus one prompt in `src/llm.py`. `src/database.py` persists everything in SQLite (`aiosqlite`, WAL mode).

## Project structure

```
src/
├── agents/           GenericAgent, NutritionAgent, BaseAgent
├── handlers/         command/callback/message/photo/voice/inline handlers
├── modes.py          Mode registry (labels, titles, taglines)
├── orchestrator.py   Routes a request to the right agent
├── llm.py            Gemini + OpenRouter client, key rotation, fallback, thinking control
├── sender.py         Reply formatting and delivery
├── utils.py          Markdown -> Telegram HTML, nutrition parsing
├── database.py       SQLite persistence (users, messages, nutrition log, stats, feedback)
├── config.py         Environment configuration
└── bot.py            Application entry point
```

## Getting started

### Requirements

- Python 3.13+
- A Telegram bot token
- At least one Gemini API key, or an OpenRouter API key (free-tier models work without billing)

### Configuration

Create a `.env` file in the project root:

```
TELEGRAM_BOT_TOKEN=your_token
GOOGLE_API_KEYS=key1,key2
OPENROUTER_API_KEY=your_key
ADMIN_IDS=123456789

# Optional: durable storage on hosts without a persistent disk (Turso/libSQL)
TURSO_DATABASE_URL=libsql://your-db.turso.io
TURSO_AUTH_TOKEN=your_turso_token
```

`GOOGLE_API_KEYS` is a comma-separated list — the client rotates between keys and skips rate-limited ones. `ADMIN_IDS` is a comma-separated list of Telegram IDs (find yours with `/id`); admin access is fail-closed, so when it is empty the admin commands are disabled for everyone.

### Running locally

```
pip install -r requirements.txt
python -m src.bot
```

### Running with Docker

```
docker compose up --build
```

The database lives in a named volume (`bot_data`) so history and stats survive restarts.

### Deploying on Render (free tier)

The bot supports webhook mode for platforms where an incoming request wakes a sleeping service: set `WEBHOOK_URL` to the public HTTPS URL and `PORT` as required, and it switches from polling automatically.

The free tier spins a service down after ~15 minutes of inactivity. To keep it awake, the bot pings its own `WEBHOOK_URL` every `KEEPALIVE_MINUTES` (default 10, must be under 15). For extra reliability, also point a free uptime monitor (UptimeRobot, cron-job.org) at the URL.

Persistence caveat: the free tier has an ephemeral filesystem and no persistent disk, so the SQLite file is wiped on every **redeploy** (and on the rare platform restart). Keep-alive prevents sleep-restarts, so data survives between your own deploys but not across them. On a paid plan, attach a Persistent Disk at `/data` and set `DB_PATH=/data/bot.db`. On the free tier, use a free **Turso** (libSQL) database: create one at turso.tech, then set `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN` in the environment — the bot stores everything remotely and data survives every redeploy. Left unset, it keeps using the local SQLite file. The schema is SQLite-compatible, so nothing else changes.

---

## Русская версия

Модульный Telegram-бот, который направляет каждое сообщение специализированному AI-агенту, а не одному универсальному ассистенту. Начинался как трекер питания и вырос в небольшой агентный фреймворк, где добавление режима — это одна запись в реестре и системный промпт.

### Режимы

Переключаются пользователем через нижнюю клавиатуру или команду `/mode`:

- **Общение** — умный помощник по делу для любых вопросов.
- **Питание** — оценивает калории и БЖУ по фото или описанию, отвечает на вопросы о еде и диетах, ведёт итоги за день и неделю (`/today`, `/week`).
- **Математика** — решает задачи по шагам, формулы выводятся корректным LaTeX.
- **Тренер** — персональный тренер: программы тренировок, техника упражнений, замены для дома и зала.
- **Текст** — письма, посты, резюме, рерайт, пересказы и переводы, готовые к копированию.
- **Код** — сеньор-разработчик: корректный идиоматичный код с кратким пояснением.

Ключевое поведение во всех режимах — **адаптивное оформление**: на простой вопрос бот отвечает одним-двумя предложениями и использует таблицы, заголовки или списки только когда они реально помогают, а не строит таблицу на каждый ответ.

Кроме текста бот принимает **фото** (блюдо в режиме Питание, скриншот кода в режиме Код, упражнение в режиме Тренер и т. д.) и **голосовые** (распознаются нативным аудио Gemini и обрабатываются как обычный текст). Его можно добавить в **группы** — там он отвечает только при @упоминании или ответе на его сообщение.

### Команды

| Команда | Кому | Описание |
|---|---|---|
| `/start` | все | Регистрация и клавиатура выбора режима |
| `/mode` | все | Переключение между режимами |
| `/week` | все | Питание за 7 дней со средним за день |
| `/settings` | все | Длина ответов, креативность, язык |
| `/stats` | все | Ваша активность: запросы, блюда, задержка, дата регистрации, последняя активность |
| `/clear` | все | Очистить историю и начать заново |
| `/feedback <текст>` | все | Отправить отзыв или идею (сохраняется и пересылается админам) |
| `/admin` | админы | Панель: рост, активные пользователи, задержка, пул ключей, топ, отзывы, экспорт CSV |
| `/broadcast <текст>` | админы | Рассылка всем зарегистрированным пользователям |
| `/disable_model` `/enable_model` | админы | Runtime-выключатель модели в цепочке фоллбеков |

### Как достигается качество и скорость

`src/llm.py` сделан так, чтобы ответы были хорошими и быстрыми даже на общих бесплатных ключах:

- **Адаптивные промпты.** Системный промпт каждого режима требует подбирать формат под вопрос и не лить воду, а не всегда выдавать таблицы и заголовки.
- **Порядок моделей по скорости.** Цепочку возглавляет быстрая и качественная модель (gemini-2.5-flash, ~1с); более медленные preview/pro стоят ниже как запас.
- **Короткая память.** Бот держит недавнюю переписку в пределах токен-бюджета — следит за контекстом между сообщениями, не раздувая запрос (экономно для бесплатных лимитов).
- **Управление размышлением.** Новые модели Gemini flash «думают» перед ответом, что добавляет несколько секунд и тратит квоту даже на пустяк. Быстрые режимы отправляют `thinkingBudget=0` (примерно в 4-5 раз быстрее, без потери качества для общения); в режиме Математика размышление остаётся для пошаговой корректности.
- **Многоуровневые фоллбеки.** Прямой Gemini API с ротацией ключей и паузой ключа при лимите, затем OpenRouter (сначала бесплатные модели) — запрос проходит даже при нулевом балансе.
- **Защита от обрыва.** Ответ с признаками обрезки (незакрытые блоки кода, оборванные таблицы, фраза на полуслове) отклоняется, и пробуется следующая модель.

Отправка (`src/sender.py`) сначала пробует rich-message API Telegram, затем HTML из собственного конвертера Markdown/LaTeX (`src/utils.py`), затем обычный текст.

### Статистика, которая не стирается

Отслеживание роста — приоритет, поэтому данные об использовании постоянны. Таблицы `users`, `stats`, `nutrition_log` и `feedback` **никогда не очищаются автоматически** — итоги остаются точными всё время работы бота. Телеметрия каждого запроса (модель, токены, задержка) реальная для всех каналов, включая текст.

- `/stats` показывает каждому пользователю его личную активность за всё время.
- `/admin` даёт общую картину: всего пользователей, новые за сегодня и за 7 дней, активные за 24 часа и 7 дней, объём запросов за всё время и недавно, средняя задержка, проанализированные блюда, последние отзывы.
- Единственная очистка убирает **историю чатов** старше 90 дней (только контекст переписки) и никогда не трогает статистику и пользователей.

### Архитектура

```
Обновление от Telegram
      |
src/handlers/        Telegram-обвязка (команды, callback, сообщения, фото, голос, inline)
      |
src/orchestrator.py  Выбирает агента под текущий режим пользователя
      |
src/agents/          GenericAgent на каждый режим (+ NutritionAgent для лога КБЖУ)
      |
src/llm.py           Клиент Gemini + OpenRouter: ротация ключей, фоллбеки, управление thinking
      |
src/sender.py        Форматирует и отправляет ответ в Telegram
```

Все режимы описаны в одном месте — `src/modes.py`: оттуда читают клавиатура, выбор `/mode`, маршрутизация кнопок, меню команд и справка. Добавить режим — одна запись там и один промпт в `src/llm.py`. `src/database.py` хранит всё в SQLite (`aiosqlite`, режим WAL).

### Структура проекта

```
src/
├── agents/           GenericAgent, NutritionAgent, BaseAgent
├── handlers/         обработчики команд/callback/сообщений/фото/голоса/inline
├── modes.py          Реестр режимов (метки, названия, описания)
├── orchestrator.py   Маршрутизация запроса к нужному агенту
├── llm.py            Клиент Gemini + OpenRouter, ротация ключей, фоллбеки, управление thinking
├── sender.py         Форматирование и отправка ответа
├── utils.py          Конвертация Markdown в Telegram HTML, парсинг питания
├── database.py       SQLite-хранилище (пользователи, сообщения, лог питания, статистика, отзывы)
├── config.py         Конфигурация из переменных окружения
└── bot.py            Точка входа приложения
```

### Быстрый старт

Требования: Python 3.13+, токен Telegram-бота, хотя бы один Gemini API-ключ или OpenRouter API-ключ (бесплатные модели работают без оплаты).

Создайте `.env` в корне проекта:

```
TELEGRAM_BOT_TOKEN=your_token
GOOGLE_API_KEYS=key1,key2
OPENROUTER_API_KEY=your_key
ADMIN_IDS=123456789

# Optional: durable storage on hosts without a persistent disk (Turso/libSQL)
TURSO_DATABASE_URL=libsql://your-db.turso.io
TURSO_AUTH_TOKEN=your_turso_token
```

`GOOGLE_API_KEYS` — список через запятую, клиент переключается между ключами и пропускает те, что временно превысили лимит. `ADMIN_IDS` — список Telegram ID через запятую (свой узнайте через `/id`); доступ к админке работает по принципу fail-closed: при пустом значении админ-команды отключены для всех.

Запуск локально:

```
pip install -r requirements.txt
python -m src.bot
```

Запуск через Docker:

```
docker compose up --build
```

База лежит в именованном томе (`bot_data`), поэтому история и статистика переживают перезапуски.

### Развёртывание на Render (бесплатный тариф)

Бот поддерживает webhook-режим для платформ, где входящий запрос будит спящий сервис: задайте `WEBHOOK_URL` (публичный HTTPS-адрес) и при необходимости `PORT` — переключение с polling произойдёт автоматически.

Бесплатный тариф усыпляет сервис после ~15 минут без запросов. Чтобы он не засыпал, бот сам пингует свой `WEBHOOK_URL` каждые `KEEPALIVE_MINUTES` (по умолчанию 10, должно быть меньше 15). Для надёжности дополнительно направьте на этот адрес бесплатный аптайм-монитор (UptimeRobot, cron-job.org).

Нюанс с данными: на бесплатном тарифе эфемерная файловая система и нет постоянного диска, поэтому файл SQLite стирается при каждом **передеплое** (и при редких перезапусках платформы). Keep-alive убирает рестарты от засыпания, поэтому данные живут между вашими деплоями, но не переживают сам деплой. На платном тарифе подключите Persistent Disk на `/data` и задайте `DB_PATH=/data/bot.db`. На бесплатном тарифе используйте бесплатную базу **Turso** (libSQL): создайте её на turso.tech и задайте `TURSO_DATABASE_URL` и `TURSO_AUTH_TOKEN` — бот будет хранить всё удалённо, и данные переживут любой передеплой. Если не заданы, используется локальный файл SQLite. Схема SQLite-совместима, так что больше ничего менять не нужно.
