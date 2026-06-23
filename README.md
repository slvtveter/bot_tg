# Nela AI

*Русская версия ниже. — [English version below](#english-version).*

Модульный Telegram-бот, который направляет каждое сообщение специализированному AI-агенту, а не одному универсальному ассистенту. Начинался как трекер питания и вырос в небольшой агентный фреймворк, где добавление режима — это одна запись в реестре плюс системный промпт.

## Режимы

На нижней клавиатуре всего две кнопки — продукт намеренно простой:

- **Общение (умный режим по умолчанию)** — отвечает на любые вопросы и сам справляется с математикой, кодом, текстами и объяснениями, не заставляя выбирать режим.
- **Питание** — оценивает калории и БЖУ по фото или описанию блюда, отвечает на вопросы о еде и диетах, ведёт дневник и показывает итоги за день и неделю (`/today`, `/week`).

Под капотом есть и другие специализированные режимы (математика, тренер, тексты, код) — они работают, если пользователя в них переключить, но в обычном сценарии всё это закрывает умный режим «Общение».

Ключевое поведение во всех режимах — **адаптивное оформление**: на простой вопрос бот отвечает одним-двумя предложениями и использует таблицы, заголовки или списки только когда они реально проясняют ответ, а не строит таблицу на каждый ответ.

Кроме текста бот принимает **фото** (блюдо в режиме Питание, скриншот кода и т. д.) и **голосовые** (распознаются нативным аудио Gemini и обрабатываются как обычный текст). Его можно добавить в **группы** — там он отвечает только при @упоминании или ответе на его сообщение.

## Команды

| Команда | Кому | Описание |
|---|---|---|
| `/start` | все | Регистрация и клавиатура выбора режима |
| `/mode` | все | Переключение между режимами |
| `/today` | все | Итоги питания за сегодня |
| `/week` | все | Питание за 7 дней со средним за день |
| `/settings` | все | Длина ответов, креативность, язык |
| `/stats` | все | Ваша активность: запросы, сообщения, блюда, задержка, дата регистрации |
| `/clear` | все | Очистить историю и начать заново |
| `/privacy` | все | Как хранятся данные (анонимно) |
| `/feedback <текст>` | все | Отправить отзыв или идею (сохраняется и пересылается админам) |
| `/admin` | админы | Панель: рост, активные пользователи, задержка, пул ключей, топ, отзывы, экспорт CSV |
| `/broadcast <текст>` | админы | Рассылка всем зарегистрированным пользователям |
| `/disable_model` `/enable_model` | админы | Runtime-выключатель модели в цепочке фоллбеков |

## Приватность

Содержимое переписки хранится **без привязки к личности**: вместо реального Telegram ID в таблице сообщений лежит псевдоним — солёный SHA-256-хэш (`conv_id`). Открыв базу, нельзя глазами увидеть, кто что написал. Профиль (имя, язык, режим) и обезличенный счётчик сообщений остаются для работы бота и аналитики. Подробности — в команде `/privacy`. Соль задаётся через переменную окружения `PRIVACY_SALT` и должна оставаться неизменной.

## Как достигается качество и скорость

`src/llm.py` сделан так, чтобы ответы были хорошими и быстрыми даже на общих бесплатных ключах:

- **Адаптивные промпты.** Системный промпт каждого режима требует подбирать формат под вопрос и не лить воду, а не всегда выдавать таблицы и заголовки.
- **Порядок моделей по реальной квоте.** Цепочку возглавляет модель с наибольшей наблюдаемой бесплатной дневной квотой (`gemini-3.1-flash-lite` — ~500 запросов в день на ключ), чтобы реже упираться в дневные лимиты; остальные Gemini-модели идут следом как запас.
- **Короткий таймаут на вызов.** У каждого обращения жёсткий таймаут (~10 с), чтобы одна перегруженная модель не подвешивала всю цепочку фоллбеков.
- **Короткая память.** Бот держит недавнюю переписку в пределах токен-бюджета — следит за контекстом между сообщениями, не раздувая запрос (экономно для бесплатных лимитов).
- **Управление размышлением.** Новые модели Gemini flash «думают» перед ответом, что добавляет несколько секунд и тратит квоту даже на пустяк. Быстрые режимы отправляют `thinkingBudget=0` (примерно в 4-5 раз быстрее, без потери качества для общения); режим Математика оставляет размышление включённым для пошаговой корректности.
- **Многоуровневые фоллбеки.** Прямой Gemini API с ротацией ключей и пер-ключевой паузой при лимите → Groq → OpenRouter (сначала бесплатные модели) — запрос проходит даже при нулевом балансе. Для каждой модели перебираются все активные ключи, прежде чем перейти к следующей.
- **Защита от обрыва.** Ответ с признаками обрезки (незакрытые блоки кода, оборванные таблицы, фраза на полуслове) отклоняется, и пробуется следующая модель.

Отправка (`src/sender.py`) сначала пробует rich-message API Telegram, затем HTML из собственного конвертера Markdown/LaTeX (`src/utils.py`), затем обычный текст.

## Статистика, которая не стирается

Отслеживание роста — приоритет, поэтому данные об использовании постоянны. Таблицы `users`, `stats`, `nutrition_log` и `feedback` **никогда не очищаются автоматически** — итоги остаются точными всё время работы бота. Телеметрия каждого запроса (модель, токены, задержка) реальная для всех каналов, включая текст.

- `/stats` показывает каждому пользователю его личную активность за всё время.
- `/admin` даёт общую картину: всего пользователей, новые за сегодня и за 7 дней, активные за 24 часа и 7 дней, объём запросов за всё время и недавно, средняя задержка, проанализированные блюда, последние отзывы.

## Архитектура

```
Обновление от Telegram
      |
src/handlers/        Telegram-обвязка (команды, callback, сообщения, фото, голос, inline)
      |
src/orchestrator.py  Выбирает агента под текущий режим пользователя
      |
src/agents/          GenericAgent на каждый режим (+ NutritionAgent для лога КБЖУ)
      |
src/llm.py           Клиент Gemini + Groq + OpenRouter: ротация ключей, фоллбеки, управление thinking
      |
src/sender.py        Форматирует и отправляет ответ в Telegram
```

Все режимы и пользовательские строки описаны в одном месте — `src/i18n.py`: оттуда читают клавиатура, выбор `/mode`, маршрутизация кнопок, меню команд и справка. Добавить режим — одна запись там и один промпт в `src/llm.py`. `src/database.py` хранит всё в SQLite (`aiosqlite`, режим WAL) или в удалённой Turso/libSQL.

## Структура проекта

```
src/
├── agents/           GenericAgent, NutritionAgent, BaseAgent
├── handlers/         обработчики команд/callback/сообщений/фото/голоса/inline
├── i18n.py           Реестр режимов и все пользовательские строки (ru/en)
├── orchestrator.py   Маршрутизация запроса к нужному агенту
├── llm.py            Клиент Gemini + Groq + OpenRouter, ротация ключей, фоллбеки, thinking
├── sender.py         Форматирование и отправка ответа
├── utils.py          Конвертация Markdown в Telegram HTML, парсинг питания
├── database.py       SQLite/Turso-хранилище (пользователи, сообщения, лог питания, статистика, отзывы)
├── config.py         Конфигурация из переменных окружения
└── bot.py            Точка входа приложения
```

## CI/CD

В `.github/workflows/ci.yml` настроен GitHub Actions: на каждый push в `main` и на каждый Pull Request прогоняются `flake8 src/` и весь набор тестов (Python 3.12). Ветка `main` защищена правилом, требующим зелёной проверки `test` и работы через Pull Request, поэтому непроверенный код в прод не попадёт. После merge в `main` Render автоматически деплоит изменения.

Рабочий процесс: ветка → правки → push → Pull Request → зелёный CI → merge → авто-деплой.

## Быстрый старт

Требования: Python 3.12+, токен Telegram-бота, хотя бы один Gemini API-ключ или OpenRouter API-ключ (бесплатные модели работают без оплаты).

Создайте `.env` в корне проекта:

```
TELEGRAM_BOT_TOKEN=your_token
GOOGLE_API_KEYS=key1,key2
GROQ_API_KEY=your_groq_key
OPENROUTER_API_KEY=your_key
ADMIN_IDS=123456789

# Приватность: соль для псевдонимизации сообщений (держать неизменной!)
PRIVACY_SALT=your_long_random_salt

# Опционально: durable-хранилище на хостингах без постоянного диска (Turso/libSQL)
TURSO_DATABASE_URL=libsql://your-db.turso.io
TURSO_AUTH_TOKEN=your_turso_token
```

`GOOGLE_API_KEYS` — список через запятую, клиент переключается между ключами и пропускает те, что временно превысили лимит. `ADMIN_IDS` — список Telegram ID через запятую; доступ к админке fail-closed: при пустом значении админ-команды отключены для всех.

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

База лежит в именованном томе (`bot_data`), поэтому история и статистика переживают перезапуски.

## Развёртывание на Render (бесплатный тариф)

Бот поддерживает webhook-режим для платформ, где входящий запрос будит спящий сервис: задайте `WEBHOOK_URL` (публичный HTTPS-адрес) и при необходимости `PORT` — переключение с polling произойдёт автоматически.

Бесплатный тариф усыпляет сервис после ~15 минут без запросов. Чтобы он не засыпал, бот сам пингует свой `WEBHOOK_URL` каждые `KEEPALIVE_MINUTES` (по умолчанию 10, должно быть меньше 15). Для надёжности дополнительно направьте на этот адрес бесплатный аптайм-монитор (UptimeRobot, cron-job.org).

Нюанс с данными: на бесплатном тарифе эфемерная файловая система, поэтому файл SQLite стирается при каждом **передеплое**. На платном тарифе подключите Persistent Disk на `/data` и задайте `DB_PATH=/data/bot.db`. На бесплатном тарифе используйте бесплатную базу **Turso** (libSQL): создайте её на turso.tech и задайте `TURSO_DATABASE_URL` и `TURSO_AUTH_TOKEN` — бот будет хранить всё удалённо, и данные переживут любой передеплой. Схема SQLite-совместима, так что больше ничего менять не нужно.

---

<a name="english-version"></a>

# English version

A modular Telegram bot that routes each message to a specialized AI agent instead of one generic assistant. It began as a nutrition tracker and grew into a small agent framework where adding a new mode costs a single registry entry plus a system prompt.

## Modes

The bottom keyboard has just two buttons — the product is deliberately simple:

- **General (smart default)** — answers any question and handles math, code, writing and explanations on its own, without making you pick a mode.
- **Nutrition** — estimates calories and macros from a photo or a text description, answers food/diet questions, keeps a diary and reports daily and weekly totals (`/today`, `/week`).

Under the hood there are other specialized modes (math, fitness, writing, code) — they work if a user is switched into them, but in normal use the smart General mode covers all of that.

The defining behavior across all modes is **adaptive formatting**: the bot answers a simple question in a sentence or two and only reaches for tables, headings or lists when they genuinely make the answer clearer — no report-style table for every reply.

Beyond text the bot accepts **photos** (a meal in Nutrition, a code screenshot, and so on) and **voice messages** (transcribed via Gemini's native audio understanding, then handled like typed text). It can also join **group chats**, where it only responds when @mentioned or replied to.

## Commands

| Command | Who | Description |
|---|---|---|
| `/start` | everyone | Register and show the mode keyboard |
| `/mode` | everyone | Switch between modes |
| `/today` | everyone | Today's nutrition totals |
| `/week` | everyone | Last 7 days of nutrition, with a daily average |
| `/settings` | everyone | Response length, creativity, language |
| `/stats` | everyone | Your activity: requests, messages, meals, latency, join date |
| `/clear` | everyone | Clear history and start a fresh conversation |
| `/privacy` | everyone | How your data is stored (anonymously) |
| `/feedback <text>` | everyone | Send feedback or an idea (stored and forwarded to admins) |
| `/admin` | admins | Dashboard: growth, active users, latency, key pool, top users, feedback, CSV export |
| `/broadcast <text>` | admins | Message every registered user |
| `/disable_model` `/enable_model` | admins | Runtime kill switch for a model in the fallback chain |

## Privacy

Chat content is stored **with no link to identity**: instead of the raw Telegram ID, the messages table holds a pseudonym — a salted SHA-256 hash (`conv_id`). Reading the database, you can't tell who wrote what. The profile (name, language, mode) and an anonymized message counter stay for the bot to function and for analytics. See the `/privacy` command for details. The salt is set via the `PRIVACY_SALT` env var and must stay stable.

## How it answers: quality and speed

`src/llm.py` is built for good answers that arrive fast, even on shared free-tier keys:

- **Adaptive prompts.** Each mode's system prompt pushes the model to match format to the question and skip filler, instead of always emitting tables and headings.
- **Model order by real quota.** The chain leads with the model that has the highest observed free-tier daily quota (`gemini-3.1-flash-lite`, ~500 requests/day per key), to hit daily limits less often; the other Gemini models follow as fallbacks.
- **Short per-call timeout.** Each call has a hard timeout (~10s) so one overloaded model can't stall the whole fallback chain.
- **Short-term memory.** The bot keeps recent conversation within a token budget, so it follows context across turns without bloating the prompt — frugal on free-tier quotas.
- **Thinking control.** Newer Gemini flash models reason internally before answering, which adds seconds and burns quota on a trivial question. Fast modes send `thinkingBudget=0` (roughly 4-5x faster, no quality loss for chat); Math keeps thinking on for step-by-step correctness.
- **Layered fallback.** Direct Gemini API with key rotation and per-key cooldown on rate limits → Groq → OpenRouter (free models first), so a request can still succeed at a zero balance. Every active key is tried for a model before moving to the next.
- **Truncation guard.** A response that looks cut off (unbalanced code fences, dangling table rows, a sentence ending mid-word) is rejected and the next model is tried.

Delivery (`src/sender.py`) tries Telegram's rich-message API first, then HTML built by a hand-rolled Markdown/LaTeX converter (`src/utils.py`), then plain text.

## Statistics that are never wiped

Tracking growth is a first-class goal, so usage data is permanent. The `users`, `stats`, `nutrition_log`, and `feedback` tables are **never auto-deleted** — lifetime totals stay accurate for as long as the bot runs. Per-request telemetry (model, tokens, latency) is real for every channel, including text.

- `/stats` gives each user their own lifetime activity.
- `/admin` shows the whole picture: total users, new today and over 7 days, active in the last 24h and 7 days, all-time and recent request volume, average latency, meals analyzed, and recent feedback.

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
src/llm.py           Gemini + Groq + OpenRouter client: key rotation, fallback, thinking control
      |
src/sender.py        Formats and sends the reply back to Telegram
```

Modes and all user-facing strings live in one place, `src/i18n.py`: the keyboard, the `/mode` picker, button routing, the command menu, and help all read from it. Adding a mode is one entry there plus one prompt in `src/llm.py`. `src/database.py` persists everything in SQLite (`aiosqlite`, WAL mode) or remote Turso/libSQL.

## Project structure

```
src/
├── agents/           GenericAgent, NutritionAgent, BaseAgent
├── handlers/         command/callback/message/photo/voice/inline handlers
├── i18n.py           Mode registry and all user-facing strings (ru/en)
├── orchestrator.py   Routes a request to the right agent
├── llm.py            Gemini + Groq + OpenRouter client, key rotation, fallback, thinking control
├── sender.py         Reply formatting and delivery
├── utils.py          Markdown -> Telegram HTML, nutrition parsing
├── database.py       SQLite/Turso persistence (users, messages, nutrition log, stats, feedback)
├── config.py         Environment configuration
└── bot.py            Application entry point
```

## CI/CD

`.github/workflows/ci.yml` runs GitHub Actions on every push to `main` and every pull request: `flake8 src/` plus the full test suite (Python 3.12). The `main` branch is protected by a rule requiring the green `test` check and a pull request, so unreviewed code can't reach production. After a merge to `main`, Render auto-deploys.

Workflow: branch → changes → push → pull request → green CI → merge → auto-deploy.

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
ADMIN_IDS=123456789

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

The database lives in a named volume (`bot_data`) so history and stats survive restarts.

### Deploying on Render (free tier)

The bot supports webhook mode for platforms where an incoming request wakes a sleeping service: set `WEBHOOK_URL` to the public HTTPS URL and `PORT` as required, and it switches from polling automatically.

The free tier spins a service down after ~15 minutes of inactivity. To keep it awake, the bot pings its own `WEBHOOK_URL` every `KEEPALIVE_MINUTES` (default 10, must be under 15). For extra reliability, also point a free uptime monitor (UptimeRobot, cron-job.org) at the URL.

Persistence caveat: the free tier has an ephemeral filesystem, so the SQLite file is wiped on every **redeploy**. On a paid plan, attach a Persistent Disk at `/data` and set `DB_PATH=/data/bot.db`. On the free tier, use a free **Turso** (libSQL) database: create one at turso.tech, then set `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN` — the bot stores everything remotely and data survives every redeploy. The schema is SQLite-compatible, so nothing else changes.
