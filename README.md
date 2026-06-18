# Agentic AI Platform

*[Русская версия](README.ru.md) — или прокрутите вниз / scroll down*

A modular Telegram bot platform that routes user requests to specialized AI agents instead of a single general-purpose assistant. It started as a straightforward nutrition-tracking bot and was rebuilt into a small agent-based framework so new capabilities can be added without touching the core dispatch logic.

## What it does

The bot currently ships with three modes, switchable per user from a persistent keyboard or the `/mode` command:

- **General** — a structured, markdown-formatted conversational assistant.
- **Nutrition** — analyzes meals (by text description or photo), estimates calories/protein/fat/carbs, and gives practical dietary feedback. Daily totals are tracked automatically and available via `/today`.
- **Math** — walks through problems step by step, with all formulas rendered as proper LaTeX rather than plain text.

Each mode has its own system prompt and response-formatting rules, but they all share the same infrastructure underneath: chat history, per-user settings, and the LLM client.

Beyond text and photos, the bot also accepts **voice messages** (transcribed via Gemini's native audio understanding before being routed through the same pipeline as typed text) and can be added to **group chats**, where it only responds when @mentioned or replied to, so it doesn't talk over every message in the group.

### Commands

| Command | Who | Description |
|---|---|---|
| `/start` | everyone | Register and show the mode keyboard |
| `/mode` | everyone | Switch between General / Nutrition / Math |
| `/today` | everyone | Today's calorie/macro totals from the Nutrition agent |
| `/settings` | everyone | Response length, creativity, language |
| `/stats` | everyone | Your own activity stats (messages, meals analyzed, latency) |
| `/undo` | everyone | Remove the last exchange from history |
| `/export` | everyone | Download your chat history as a file |
| `/clear` | everyone | Wipe your chat history |
| `/admin` | admins | Dashboard: active users, latency, API key pool, top users, CSV export |
| `/broadcast <text>` | admins | Message every registered user |
| `/disable_model` `/enable_model` | admins | Runtime kill switch for a model in the fallback chain |

## Architecture

```
Telegram update
      |
src/handlers/        -> Telegram-specific glue (commands, callbacks, message routing)
      |
src/orchestrator.py   -> picks the agent for the user's current mode
      |
src/agents/           -> mode-specific prompt + behavior (NutritionAgent, MathAgent, ...)
      |
src/llm.py             -> provider-agnostic LLM client
      |
src/sender.py          -> formats and sends the reply back to Telegram
```

`src/database.py` sits alongside this flow, persisting chat history, per-user settings, daily nutrition totals, and usage statistics in SQLite (via `aiosqlite`, with WAL mode enabled for concurrent access).

### LLM resilience

Reliability was the main design constraint here, not raw model quality — free-tier API quotas run out fast, especially when several projects share the same keys. `src/llm.py` handles this with a layered fallback chain:

1. **Direct Gemini API**, trying a list of models ordered from highest remaining daily quota to lowest, with API keys rotated and put on a cooldown when they hit rate limits or errors.
2. **OpenRouter**, as a second tier, trying free-tier community models first (Llama, Qwen, Nemotron, Gemma, GPT-OSS) before any paid models, so a request can still succeed even with zero account balance.

A response is also checked for signs of truncation (unbalanced code fences, dangling table rows, sentences cut off mid-word) before being accepted — if it looks cut off, the client moves on to the next model rather than returning a broken answer.

### Message formatting

LLM output is plain Markdown internally, but Telegram doesn't render Markdown tables or LaTeX reliably. `src/sender.py` and `src/utils.py` convert it through a few stages: tables get normalized and re-rendered as monospaced blocks, LaTeX gets wrapped for Telegram's math rendering, and if Telegram's richer message API isn't available the bot falls back to HTML and then to plain text.

## Project structure

```
src/
├── agents/           Mode-specific agents (GeneralAgent, NutritionAgent, MathAgent, BaseAgent)
├── handlers/         Telegram command/callback/message/photo/voice handlers
├── orchestrator.py   Routes a request to the right agent
├── llm.py            Gemini + OpenRouter client, key rotation, fallback chain
├── sender.py         Reply formatting and delivery
├── utils.py          Markdown -> Telegram HTML conversion, nutrition data parsing
├── database.py       SQLite persistence (users, messages, nutrition log, usage stats)
├── config.py         Environment configuration
└── bot.py            Application entry point
```

## Getting started

### Requirements

- Python 3.13+
- A Telegram bot token
- At least one Gemini API key, or an OpenRouter API key (free-tier models work without billing set up)

### Configuration

Create a `.env` file in the project root:

```
TELEGRAM_BOT_TOKEN=your_token
GOOGLE_API_KEYS=key1,key2
OPENROUTER_API_KEY=your_key
ADMIN_IDS=123456789
```

`GOOGLE_API_KEYS` accepts a comma-separated list — the client rotates between them and skips ones that are temporarily rate-limited.

### Running locally

```
pip install -r requirements.txt
python -m src.bot
```

### Running with Docker

```
docker compose up --build
```

The container persists the SQLite database in a named volume (`bot_data`) so history and stats survive restarts.

---

## Русская версия

Модульная платформа для Telegram-бота, которая распределяет запросы пользователя между специализированными AI-агентами, а не отдаёт всё одному универсальному ассистенту. Начинался проект как простой бот для подсчёта КБЖУ, а затем был переработан в небольшой агентный фреймворк, чтобы новые возможности можно было добавлять без изменения основной логики маршрутизации.

### Что умеет бот

Сейчас доступно три режима, переключаемых пользователем через клавиатуру или команду `/mode`:

- **Общение** — структурированный ассистент с markdown-форматированием ответов.
- **Питание** — анализирует приём пищи (по текстовому описанию или фото), оценивает калории/белки/жиры/углеводы и даёт практические рекомендации по питанию. Дневные итоги считаются автоматически и доступны через команду `/today`.
- **Математика** — разбирает задачи по шагам, все формулы выводятся в виде корректного LaTeX, а не обычного текста.

У каждого режима свой системный промпт и правила форматирования ответа, но все они работают на одной и той же инфраструктуре: истории сообщений, пользовательских настройках и LLM-клиенте.

Кроме текста и фото, бот принимает **голосовые сообщения** (распознаются через нативное понимание аудио в Gemini, после чего обрабатываются по тому же пайплайну, что и обычный текст) и может быть добавлен в **групповые чаты** — там он отвечает только при упоминании через @ или ответе на его сообщение, не вмешиваясь в остальную переписку группы.

### Команды

| Команда | Кому | Описание |
|---|---|---|
| `/start` | все | Регистрация и клавиатура выбора режима |
| `/mode` | все | Переключение между Общение / Питание / Математика |
| `/today` | все | Итоги по калориям/БЖУ за сегодня (режим Питание) |
| `/settings` | все | Длина ответов, креативность, язык |
| `/stats` | все | Личная статистика активности (сообщения, проанализированные блюда, задержка) |
| `/undo` | все | Удалить последний обмен сообщениями из истории |
| `/export` | все | Выгрузить историю переписки в файл |
| `/clear` | все | Очистить историю переписки |
| `/admin` | админы | Панель: активные пользователи, задержка, пул API-ключей, топ пользователей, экспорт в CSV |
| `/broadcast <текст>` | админы | Рассылка всем зарегистрированным пользователям |
| `/disable_model` `/enable_model` | админы | Runtime-выключатель модели в цепочке фоллбеков |

### Архитектура

```
Обновление от Telegram
      |
src/handlers/         -> обработка команд, callback-ов и сообщений
      |
src/orchestrator.py   -> выбирает агента под текущий режим пользователя
      |
src/agents/            -> промпт и поведение конкретного режима (NutritionAgent, MathAgent, ...)
      |
src/llm.py              -> провайдеро-независимый клиент для LLM
      |
src/sender.py            -> форматирует и отправляет ответ обратно в Telegram
```

Рядом с этим потоком работает `src/database.py`, который хранит историю сообщений, настройки пользователей, дневные итоги по питанию и статистику использования в SQLite (через `aiosqlite`, с включённым WAL-режимом для параллельного доступа).

#### Отказоустойчивость LLM

Главным критерием при проектировании была надёжность, а не максимальное качество одной конкретной модели — бесплатные квоты у провайдеров заканчиваются быстро, особенно когда несколько проектов используют одни и те же ключи. `src/llm.py` решает это через многоуровневую цепочку фоллбеков:

1. **Прямой Gemini API** — список моделей перебирается в порядке от наибольшего оставшегося суточного лимита к наименьшему, а API-ключи переключаются по кругу и временно ставятся "на паузу" при превышении лимита или ошибке.
2. **OpenRouter** как второй уровень — сначала пробуются бесплатные модели сообщества (Llama, Qwen, Nemotron, Gemma, GPT-OSS), а уже потом платные, поэтому запрос может пройти даже при нулевом балансе аккаунта.

Перед тем как принять ответ, он проверяется на признаки обрыва (незакрытые блоки кода, оборванные строки таблиц, предложения, обрезанные на полуслове) — если ответ похож на обрезанный, клиент переходит к следующей модели вместо того, чтобы вернуть пользователю "сломанный" текст.

#### Форматирование сообщений

Внутри LLM отвечает обычным Markdown, но Telegram не умеет надёжно рендерить markdown-таблицы и LaTeX. `src/sender.py` и `src/utils.py` прогоняют текст через несколько этапов: таблицы нормализуются и перерисовываются как моноширинные блоки, LaTeX оборачивается для математического рендеринга Telegram, а если расширенный API сообщений недоступен — бот откатывается на HTML, а затем на обычный текст.

### Структура проекта

```
src/
├── agents/           Агенты под конкретные режимы (GeneralAgent, NutritionAgent, MathAgent, BaseAgent)
├── handlers/         Обработчики команд/callback-ов/сообщений/фото/голоса
├── orchestrator.py   Маршрутизация запроса к нужному агенту
├── llm.py            Клиент Gemini + OpenRouter, ротация ключей, цепочка фоллбеков
├── sender.py         Форматирование и отправка ответа
├── utils.py          Конвертация Markdown в Telegram HTML, парсинг данных по питанию
├── database.py       SQLite-хранилище (пользователи, сообщения, лог питания, статистика)
├── config.py         Конфигурация из переменных окружения
└── bot.py            Точка входа приложения
```

### Быстрый старт

#### Требования

- Python 3.13+
- Токен Telegram-бота
- Хотя бы один Gemini API-ключ или OpenRouter API-ключ (бесплатные модели работают без подключения оплаты)

#### Конфигурация

Создайте файл `.env` в корне проекта:

```
TELEGRAM_BOT_TOKEN=your_token
GOOGLE_API_KEYS=key1,key2
OPENROUTER_API_KEY=your_key
ADMIN_IDS=123456789
```

`GOOGLE_API_KEYS` принимает список через запятую — клиент переключается между ключами и пропускает те, что временно превысили лимит.

#### Запуск локально

```
pip install -r requirements.txt
python -m src.bot
```

#### Запуск через Docker

```
docker compose up --build
```

Контейнер хранит базу SQLite в именованном томе (`bot_data`), поэтому история и статистика сохраняются между перезапусками.
