# Agentic AI Platform

*[Русская версия](README.ru.md)*

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
| `/stats` | everyone | Your own usage stats (requests, tokens, latency) |
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
