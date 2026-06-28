# Nela AI

*English version below — [jump to English](#english-version).*

Telegram-бот на `python-telegram-bot`, который проксирует сообщения в Gemini / Groq / OpenRouter через небольшую архитектуру Orchestrator → Agent. Начинался как трекер питания с набором отдельных режимов, а вырос в **один умный ассистент**: режим «Общение» сам определяет, что от него хотят — посчитать КБЖУ по фото еды, решить задачу, написать код, отредактировать текст — и отвечает соответствующе, не заставляя пользователя выбирать режим.

## Что умеет

Нижняя клавиатура намеренно минимальна — это всего одна кнопка **⚙️ Настройки**. Выбирать режим не нужно: умный «general» закрывает всё сам.

- **Умное общение.** Любые вопросы, объяснения, код, тексты, математика. Формат подбирается под вопрос (см. ниже).
- **Питание по фото и тексту.** Если пользователь прислал фото еды или описал блюдо, бот ведёт себя как нутрициолог: оценивает КБЖУ (таблицей, если продуктов несколько), а итог по приёму пищи логирует в дневник. Команды `/today` и `/week` показывают сводку за день и за 7 дней.
- **Веб-поиск без отдельной кнопки.** Когда для точного ответа нужны свежие факты (новости, цены, погода), модель сама вызывает инструмент `web_search` (Tavily) в том же запросе, где и отвечает, и добавляет ссылку на источник. Если ключа нет или дневной лимит исчерпан — просто отвечает по памяти.
- **Голосовые сообщения.** Распознаются нативным аудио Gemini и обрабатываются как обычный текст.
- **Напоминания.** `/remind HH:MM текст` — разовое напоминание (время в UTC).
- **Группы.** Бота можно добавить в группу — там он отвечает только на @упоминание или ответ на его сообщение.

Под капотом в реестре остаются и другие специализированные промпты (математика, тренер, тексты, код) — они работают, если пользователя в них переключить, но в обычном сценарии всё закрывает «Общение».

Ключевое поведение — **адаптивное оформление**: на простой вопрос бот отвечает одним-двумя предложениями, а таблицы, заголовки и списки использует только когда они реально проясняют ответ.

## Команды

| Команда | Кому | Описание |
|---|---|---|
| `/start` | все | Регистрация и приветствие |
| `/mode` | все | Выбор режима (по умолчанию доступен только «Общение») |
| `/today` | все | Итоги питания за сегодня |
| `/week` | все | Питание за 7 дней со средним за день |
| `/remind HH:MM текст` | все | Разовое напоминание (UTC) |
| `/settings` | все | Длина ответов, креативность, язык |
| `/stats` | все | Ваша активность: запросы, сообщения, блюда, задержка, дата регистрации |
| `/clear` | все | Очистить историю и начать заново |
| `/privacy` | все | Как хранятся данные (псевдонимно) |
| `/feedback <текст>` | все | Отправить отзыв или идею (сохраняется и пересылается админам) |
| `/admin` | админы | Панель: рост, активные пользователи, задержка, пул ключей, топ, отзывы, экспорт CSV |
| `/broadcast <текст>` | админы | Рассылка всем зарегистрированным пользователям |
| `/disable_model` `/enable_model` | админы | Runtime-выключатель модели в цепочке фоллбеков |

«Статистика» и «Новый чат» также доступны кнопками внутри панели **⚙️ Настройки**.

## Приватность

Содержимое переписки хранится **без привязки к личности**: вместо реального Telegram ID в таблице сообщений лежит псевдоним — солёный SHA-256-хэш (`conv_id`). Открыв базу, нельзя глазами увидеть, кто что написал. Профиль (имя, язык, режим) и обезличенный счётчик сообщений остаются для работы бота и аналитики. Подробности — в команде `/privacy`. Соль задаётся через `PRIVACY_SALT` и должна оставаться неизменной (иначе вся история станет недоступной).

## Как достигается качество и скорость

`src/llm.py` сделан так, чтобы ответы были хорошими и быстрыми даже на общих бесплатных ключах:

- **Адаптивные промпты.** Системный промпт требует подбирать формат под вопрос и не лить воду, а не всегда выдавать таблицы и заголовки.
- **Порядок моделей по реальной квоте.** Цепочку возглавляет модель с наибольшей наблюдаемой бесплатной дневной квотой (`gemini-3.1-flash-lite` — ~500 запросов в день на ключ); остальные Gemini-модели идут следом как запас.
- **Пул ключей с пер-(ключ, модель) паузами.** Ключ, упёршийся в дневной лимит на одной модели, продолжает обслуживать другие — `KeyPool` отслеживает cooldown по паре `(ключ, модель)`, а не по ключу целиком.
- **Короткий таймаут на вызов** (~10 с), чтобы одна перегруженная модель не подвешивала всю цепочку фоллбеков.
- **Короткая память.** Недавняя переписка держится в пределах токен-бюджета — контекст между сообщениями есть, запрос не раздувается.
- **Управление размышлением.** Быстрые режимы отправляют `thinkingBudget=0` (примерно в 4-5 раз быстрее, без потери качества для общения); Математика оставляет размышление включённым для пошаговой корректности.
- **Многоуровневые фоллбеки.** Прямой Gemini API с ротацией ключей → Groq → OpenRouter (сначала бесплатные модели) — запрос проходит даже при нулевом балансе. Для каждой модели перебираются все активные ключи, прежде чем перейти к следующей.
- **Защита от обрыва.** Ответ с признаками обрезки (незакрытые блоки кода, оборванные таблицы) отклоняется, и пробуется следующая модель.

Отправка (`src/sender.py`) по умолчанию идёт через Telegram **sendRichMessage** — он рендерит Markdown, LaTeX и таблицы нативно на актуальных клиентах (включается флагом `USE_RICH_MESSAGE`, по умолчанию `true`). Если rich-режим выключен или не сработал, ответ конвертируется в HTML собственным конвертером (`src/utils.py`), а в крайнем случае отправляется обычным текстом.

## Статистика, которая не стирается

Таблицы `users`, `stats`, `nutrition_log` и `feedback` **никогда не очищаются автоматически** — итоги остаются точными всё время работы бота. Телеметрия каждого запроса (модель, токены, задержка) реальная для всех каналов, включая текст, фото и голос.

- `/stats` показывает каждому пользователю его личную активность за всё время.
- `/admin` даёт общую картину: всего пользователей, новые и активные за периоды, объём запросов, средняя задержка, проанализированные блюда, статус пула ключей и последние отзывы.

## Архитектура

```
Обновление от Telegram
      │
src/handlers/        Telegram-обвязка (команды, callback, сообщения, фото, голос, inline, напоминания)
      │
src/router.py        Embedding-роутер: по смыслу выбирает специалиста (nutrition/math/fitness/writing/code или general)
      │
src/orchestrator.py  Диспатчит сообщение в выбранного агента-специалиста
      │
src/agents/          GenericAgent на каждый домен; парсит маркер [NUTRITION_DATA] и пишет КБЖУ в дневник
      │
src/llm.py           Клиент Gemini + Groq + OpenRouter: ротация ключей, фоллбеки, thinking, web_search (function calling)
      │
src/sender.py        Форматирует и отправляет ответ в Telegram
```

**Как работает роутинг (оркестратор → саб-агенты).** Каждое сообщение сначала проходит через embedding-роутер (`src/router.py`): текст превращается в вектор, и по cosine similarity выбирается домен-специалист с самым близким набором примеров, иначе — `general` (универсальный мозг с веб-поиском и питанием). Это nearest-neighbour-классификатор над замороженной embedding-моделью, без обучения. Бэкенд сменный (`ROUTER_BACKEND`): по умолчанию **Gemini embeddings** (мультиязычный, понимает русский, 0 RAM), либо локальный **model2vec** на CPU без сети. Всё fail-open: при любом сбое роутер возвращает `general`, ответ никогда не блокируется.

Все режимы и пользовательские строки описаны в одном месте — `src/i18n.py` (реестр режимов + строки ru/en). Добавить режим — одна запись там и один промпт в `src/llm.py`. `src/database.py` хранит всё в SQLite (`aiosqlite`, WAL) или в удалённой Turso/libSQL.

## Структура проекта

```
src/
├── agents/           BaseAgent (ABC) + GenericAgent (один на домен)
├── handlers/         команды/callback/сообщения/фото/голос/inline/напоминания
├── i18n.py           реестр режимов и все строки интерфейса (ru/en)
├── router.py         embedding-роутер: выбор специалиста (Gemini/local model2vec backend)
├── orchestrator.py   dispatch запроса к выбранному агенту-специалисту
├── llm.py            клиент Gemini + Groq + OpenRouter, ротация ключей, фоллбеки, thinking, web_search
├── web_search.py     веб-поиск Tavily как инструмент модели + рендер ссылок на источники
├── sender.py         форматирование и отправка ответа
├── utils.py          конвертер Markdown/LaTeX → Telegram HTML, парсинг КБЖУ
├── database.py       хранилище SQLite/Turso (пользователи, сообщения, питание, статистика, отзывы, напоминания)
├── config.py         конфигурация из переменных окружения
└── bot.py            точка входа приложения
```

## CI/CD

В `.github/workflows/ci.yml` на каждый push в `main` и на каждый Pull Request в `main` прогоняются `flake8 src/` и весь набор тестов (Python 3.12, с фиктивными ключами — LLM-вызовы в тестах замоканы). Рабочий процесс: ветка → правки → push → Pull Request → зелёный CI → merge → Render авто-деплоит `main`. Коммитить напрямую в `main` не нужно — это обходит проверку.

## Быстрый старт

Требования: Python 3.12+, токен Telegram-бота, хотя бы один Gemini-ключ или OpenRouter-ключ (бесплатные модели работают без оплаты).

Создайте `.env` в корне проекта:

```
TELEGRAM_BOT_TOKEN=your_token
GOOGLE_API_KEYS=key1,key2
GROQ_API_KEY=your_groq_key
OPENROUTER_API_KEY=your_key
TAVILY_API_KEY=your_tavily_key      # опционально: включает веб-поиск
ADMIN_IDS=123456789

# Опционально: роутер агентов (по умолчанию backend=gemini, переиспользует GOOGLE_API_KEYS).
# ROUTER_BACKEND=local задействует локальную model2vec-модель; ROUTER_THRESHOLD тюньте по логам.

# Приватность: соль для псевдонимизации сообщений (держать неизменной!)
PRIVACY_SALT=your_long_random_salt

# Опционально: durable-хранилище на хостингах без постоянного диска (Turso/libSQL)
TURSO_DATABASE_URL=libsql://your-db.turso.io
TURSO_AUTH_TOKEN=your_turso_token
```

`GOOGLE_API_KEYS` — список через запятую; клиент переключается между ключами и пропускает временно залимиченные. `ADMIN_IDS` — список Telegram ID через запятую; админ-доступ fail-closed: при пустом значении админ-команды отключены для всех.

Запуск локально:

```
pip install -r requirements.txt
python -m src.bot
```

Тесты и линтер:

```
python -m unittest discover -s tests -p "test_*.py"
flake8 src/
```

Запуск через Docker:

```
docker compose up --build
```

## Развёртывание на Render (бесплатный тариф)

Бот поддерживает webhook-режим: задайте `WEBHOOK_URL` (публичный HTTPS-адрес) и при необходимости `PORT` — переключение с polling произойдёт автоматически. Бесплатный тариф усыпляет сервис после ~15 минут без запросов; чтобы он не засыпал, бот сам пингует свой `WEBHOOK_URL` каждые `KEEPALIVE_MINUTES` (по умолчанию 10, должно быть меньше 15).

Нюанс с данными: на бесплатном тарифе эфемерная файловая система, и файл SQLite стирается при каждом передеплое. Поэтому используйте бесплатную базу **Turso** (libSQL): задайте `TURSO_DATABASE_URL` и `TURSO_AUTH_TOKEN`, и данные переживут любой передеплой. На платном тарифе можно вместо этого подключить Persistent Disk на `/data` и задать `DB_PATH=/data/bot.db`.

---

<a name="english-version"></a>

# English version

A Telegram bot (`python-telegram-bot`) that proxies messages to Gemini / Groq / OpenRouter through a small Orchestrator → Agent architecture. It began as a nutrition tracker with separate modes and converged into **one smart assistant**: the "general" mode figures out what you need — estimate macros from a food photo, solve a problem, write code, edit text — and responds accordingly, without making you pick a mode.

## What it does

The bottom keyboard is deliberately minimal — a single **⚙️ Settings** button. There's no mode to pick: the smart "general" brain covers everything.

- **Smart chat.** Any question, explanation, code, writing, math. Format adapts to the question (see below).
- **Nutrition from photo and text.** If you send a food photo or describe a meal, the bot acts as a nutritionist: it estimates calories and macros (as a table when there are several items) and logs the meal totals to a diary. `/today` and `/week` show daily and 7-day summaries.
- **Web search with no extra button.** When a precise answer needs fresh facts (news, prices, weather), the model itself calls the `web_search` tool (Tavily) in the same call where it answers, and adds a source link. With no key or an exhausted daily budget, it just answers from memory.
- **Voice messages.** Transcribed via Gemini's native audio understanding, then handled like typed text.
- **Reminders.** `/remind HH:MM text` sets a one-time reminder (time in UTC).
- **Groups.** Add the bot to a group and it only responds when @mentioned or replied to.

Other specialized prompts (math, fitness, writing, code) stay in the registry and work if a user is switched into them, but in normal use the "general" mode covers all of it.

The defining behavior is **adaptive formatting**: a simple question gets a sentence or two; tables, headings and lists appear only when they genuinely make the answer clearer.

## Commands

| Command | Who | Description |
|---|---|---|
| `/start` | everyone | Register and greet |
| `/mode` | everyone | Pick a mode (only "General" is exposed by default) |
| `/today` | everyone | Today's nutrition totals |
| `/week` | everyone | Last 7 days of nutrition, with a daily average |
| `/remind HH:MM text` | everyone | One-time reminder (UTC) |
| `/settings` | everyone | Response length, creativity, language |
| `/stats` | everyone | Your activity: requests, messages, meals, latency, join date |
| `/clear` | everyone | Clear history and start a fresh conversation |
| `/privacy` | everyone | How your data is stored (pseudonymously) |
| `/feedback <text>` | everyone | Send feedback or an idea (stored and forwarded to admins) |
| `/admin` | admins | Dashboard: growth, active users, latency, key pool, top users, feedback, CSV export |
| `/broadcast <text>` | admins | Message every registered user |
| `/disable_model` `/enable_model` | admins | Runtime kill switch for a model in the fallback chain |

"Stats" and "New chat" are also available as buttons inside the **⚙️ Settings** panel.

## Privacy

Chat content is stored **with no link to identity**: instead of the raw Telegram ID, the messages table holds a pseudonym — a salted SHA-256 hash (`conv_id`). Reading the database, you can't tell who wrote what. The profile (name, language, mode) and an anonymized message counter stay for the bot to function and for analytics. See `/privacy` for details. The salt is set via `PRIVACY_SALT` and must stay stable (changing it makes all history unreachable).

## How it answers: quality and speed

`src/llm.py` is built for good answers that arrive fast, even on shared free-tier keys:

- **Adaptive prompts.** The system prompt pushes the model to match format to the question and skip filler, instead of always emitting tables and headings.
- **Model order by real quota.** The chain leads with the highest observed free-tier daily quota (`gemini-3.1-flash-lite`, ~500 requests/day per key); the other Gemini models follow as fallbacks.
- **Per-(key, model) cooldowns.** A key that hits its daily limit on one model keeps serving others — `KeyPool` tracks cooldown per `(key, model)` pair, not per whole key.
- **Short per-call timeout** (~10s) so one overloaded model can't stall the whole fallback chain.
- **Short-term memory.** Recent conversation is kept within a token budget — context across turns without bloating the prompt.
- **Thinking control.** Fast modes send `thinkingBudget=0` (roughly 4-5x faster, no quality loss for chat); Math keeps thinking on for step-by-step correctness.
- **Layered fallback.** Direct Gemini API with key rotation → Groq → OpenRouter (free models first), so a request can still succeed at a zero balance. Every active key is tried for a model before moving to the next.
- **Truncation guard.** A response that looks cut off (unbalanced code fences, dangling table rows) is rejected and the next model is tried.

Delivery (`src/sender.py`) defaults to Telegram's **sendRichMessage**, which renders Markdown, LaTeX and tables natively on up-to-date clients (gated by `USE_RICH_MESSAGE`, default `true`). If rich mode is off or fails, the reply is converted to HTML by a hand-rolled converter (`src/utils.py`), and as a last resort sent as plain text.

## Statistics that are never wiped

The `users`, `stats`, `nutrition_log`, and `feedback` tables are **never auto-deleted** — lifetime totals stay accurate for as long as the bot runs. Per-request telemetry (model, tokens, latency) is real for every channel, including text, photo and voice.

- `/stats` gives each user their own lifetime activity.
- `/admin` shows the whole picture: total users, new and active over periods, request volume, average latency, meals analyzed, key-pool status, and recent feedback.

## Architecture

```
Telegram update
      │
src/handlers/        Telegram glue (commands, callbacks, messages, photo, voice, inline, reminders)
      │
src/router.py        Embedding router: picks a specialist by meaning (nutrition/math/fitness/writing/code or general)
      │
src/orchestrator.py  Dispatches the message to the chosen specialist agent
      │
src/agents/          GenericAgent per domain; parses the [NUTRITION_DATA] marker and logs macros to the diary
      │
src/llm.py           Gemini + Groq + OpenRouter client: key rotation, fallback, thinking, web_search (function calling)
      │
src/sender.py        Formats and sends the reply back to Telegram
```

**How routing works (orchestrator → sub-agents).** Every message first passes through an embedding router (`src/router.py`): the text is turned into a vector and routed by cosine similarity to the specialist domain whose example phrases are closest, or to `general` (the catch-all brain with web search and nutrition). It's a nearest-neighbour classifier over a frozen embedding model — no training. The backend is pluggable (`ROUTER_BACKEND`): the default is **Gemini embeddings** (multilingual, understands Russian, zero RAM), or a local **model2vec** model on CPU with no network. Everything is fail-open: on any error the router returns `general`, so a reply is never blocked.

Modes and all user-facing strings live in one place, `src/i18n.py` (mode registry + ru/en strings). Adding a mode is one entry there plus one prompt in `src/llm.py`. `src/database.py` persists everything in SQLite (`aiosqlite`, WAL mode) or remote Turso/libSQL.

## Project structure

```
src/
├── agents/           BaseAgent (ABC) + GenericAgent (one per domain)
├── handlers/         command/callback/message/photo/voice/inline/reminder handlers
├── i18n.py           mode registry and all user-facing strings (ru/en)
├── router.py         embedding router: specialist selection (Gemini / local model2vec backend)
├── orchestrator.py   dispatches a request to the chosen specialist agent
├── llm.py            Gemini + Groq + OpenRouter client, key rotation, fallback, thinking, web_search
├── web_search.py     Tavily web search as a model tool + source-link rendering
├── sender.py         reply formatting and delivery
├── utils.py          Markdown/LaTeX → Telegram HTML converter, nutrition parsing
├── database.py       SQLite/Turso persistence (users, messages, nutrition, stats, feedback, reminders)
├── config.py         environment configuration
└── bot.py            application entry point
```

## CI/CD

`.github/workflows/ci.yml` runs `flake8 src/` plus the full test suite (Python 3.12, with dummy keys — LLM calls are mocked in tests) on every push to `main` and every pull request targeting `main`. Workflow: branch → changes → push → pull request → green CI → merge → Render auto-deploys `main`. Don't commit straight to `main` — it bypasses the gate.

## Getting started

### Requirements

- Python 3.12+
- A Telegram bot token
- At least one Gemini API key, or an OpenRouter API key (free-tier models work without billing)

### Configuration

Create a `.env` file in the project root:

```
TELEGRAM_BOT_TOKEN=your_token
GOOGLE_API_KEYS=key1,key2
GROQ_API_KEY=your_groq_key
OPENROUTER_API_KEY=your_key
TAVILY_API_KEY=your_tavily_key      # optional: enables web search
ADMIN_IDS=123456789

# Optional: agent router (backend=gemini by default, reuses GOOGLE_API_KEYS).
# ROUTER_BACKEND=local uses a local model2vec model; tune ROUTER_THRESHOLD from the router logs.

# Privacy: salt for message pseudonymization (keep it stable!)
PRIVACY_SALT=your_long_random_salt

# Optional: durable storage on hosts without a persistent disk (Turso/libSQL)
TURSO_DATABASE_URL=libsql://your-db.turso.io
TURSO_AUTH_TOKEN=your_turso_token
```

`GOOGLE_API_KEYS` is a comma-separated list — the client rotates between keys and skips rate-limited ones. `ADMIN_IDS` is a comma-separated list of Telegram IDs; admin access is fail-closed, so when it is empty the admin commands are disabled for everyone.

### Running locally

```
pip install -r requirements.txt
python -m src.bot
```

### Tests and lint

```
python -m unittest discover -s tests -p "test_*.py"
flake8 src/
```

### Running with Docker

```
docker compose up --build
```

### Deploying on Render (free tier)

The bot supports webhook mode: set `WEBHOOK_URL` to the public HTTPS URL (and `PORT` if needed) and it switches from polling automatically. The free tier spins a service down after ~15 minutes of inactivity, so the bot pings its own `WEBHOOK_URL` every `KEEPALIVE_MINUTES` (default 10, must be under 15).

Persistence caveat: the free tier has an ephemeral filesystem, so the SQLite file is wiped on every redeploy. Use a free **Turso** (libSQL) database — set `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN` and data survives every redeploy. On a paid plan you can instead attach a Persistent Disk at `/data` and set `DB_PATH=/data/bot.db`.
