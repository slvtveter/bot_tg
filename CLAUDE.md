# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Telegram bot (`src/bot.py`, built on `python-telegram-bot`) that proxies user messages to Gemini/Groq/OpenRouter LLMs through a small Orchestrator → Agent architecture, branded "Nela AI". Six internal modes (`general`, `nutrition`, `math`, `fitness`, `writing`, `code`), but the product surfaces only two on the bottom keyboard (`general`, `nutrition` — see `VISIBLE_MODES` in `src/i18n.py`); the rest still work if a user is switched into them, but the default "smart" general mode is expected to handle math/code/writing without the user picking a mode.

## Commands

- Install deps: `pip install -r requirements.txt`
- Run the bot: `python -m src.bot` (entry point lives at `src/bot.py`; Dockerfile's `CMD` matches this)
- Run tests: `python -m unittest discover -s tests -p "test_*.py"`
- Run the flakiness loop (20x test runs): `python tests/run_checks.py`
- Run the formatting/latency benchmark: `python tests/run_benchmark.py`
- Lint: `flake8` (config in `.flake8`: max-line-length 120, ignores E402/W503/W291)

## Architecture

Request flow for a text message: `handlers/messages.py` → `Orchestrator.route_and_process` (`src/orchestrator.py`) → mode-specific `Agent.process` (`src/agents/`) → `ask_llm` (`src/llm.py`) → `sender.send_response` (`src/sender.py`).

- **`src/i18n.py`** — single source of truth for the mode registry (`_MODES`/`MODE_KEYS`/`VISIBLE_MODES`/`DEFAULT_MODE`) and all user-facing strings (`STRINGS`, keyed by string name then `ru`/`en`). Adding a mode means one entry in `_MODES` plus a matching system prompt in `src/llm.py`'s `SYSTEM_PROMPTS`; adding a UI string means one entry in `STRINGS`.
- **`src/orchestrator.py`** — `Orchestrator` builds one agent per `MODE_KEYS` entry: `NutritionAgent` for `nutrition` (logs parsed macros to the diary), `GenericAgent` for every other mode. Dispatches by the user's stored mode, falling back to `DEFAULT_MODE` ("general") if the stored mode is unknown.
- **`src/agents/base.py`** — `BaseAgent` ABC requiring `process(user_input, history, user_settings, user_id) -> AgentResult`, where `AgentResult = (text, model, prompt_tokens, completion_tokens, latency)`. `text` is `None` if the whole LLM fallback chain failed, so the caller can show a clean error instead of logging an empty turn. This tuple carries real telemetry all the way to `log_usage_stats` — there's no placeholder/dummy data in the handler path.
- **`src/llm.py`** — All actual LLM I/O. Key behaviors to know before touching this file:
  - Fallback chain: direct Gemini API first (`KeyPool` tracks cooldowns per `(key, model)` pair, not just per key, since a key exhausting its daily quota on one Gemini model still has quota left on others), then Groq, then OpenRouter (free models first, then paid as a last resort).
  - Direct Gemini's model list is ordered by known free-tier quota size (highest-quota models first, e.g. `gemini-2.0-flash-lite`/`gemini-2.0-flash` before `gemini-2.5-flash`), to minimize how often a request has to cascade through several models before finding one with quota left.
  - `KeyPool.fail_key` differentiates failure types with different cooldown scopes/durations: invalid key (global to the key, 600s), daily quota exhaustion detected via `"perday"` in the error text (per-model, 21600s/6h), per-minute 429 (per-model, 60s), other 403/500/503 (per-model, 300s).
  - `is_response_complete()` heuristically detects truncated output (unbalanced ``` ``` ``` / `**`, dangling table rows) and is used to reject a model's response and fall through to the next model/key — this is a real control-flow signal, not just logging.
  - History is trimmed locally and cheaply via `trim_history` (pure truncation, no extra LLM call) rather than summarized — there is no LLM-based auto-summarization step.
  - `estimate_tokens` is a char-count heuristic (Cyrillic ~2 chars/token, other ~4 chars/token), used only as a fallback when the API doesn't return real usage metadata.
- **`src/database.py`** — async SQLite (`aiosqlite`) with WAL mode and FK constraints. `init_db()` is defensive: it inspects `PRAGMA table_info`/`PRAGMA foreign_key_list` at startup and migrates older schemas in place (renaming tables, recreating with FKs, copying data, backfilling new columns) rather than assuming a fixed schema — relevant if you add a column or table, follow the same migrate-in-`init_db` pattern, and don't assume any single column (e.g. `created_at`) is present on every legacy schema you migrate from. `DB_PATH` defaults to an absolute path on the dev machine but is overridden via the `DB_PATH` env var in Docker (`/data/bot.db`).
- **`src/sender.py`** — three-tier send fallback: a `sendRichMessage` Telegram API call (not yet in `python-telegram-bot`, called directly via `httpx`) → `sendMessage` with HTML built by `src/utils.to_telegram_html` → plain text. `to_telegram_html` is a hand-rolled Markdown/LaTeX-to-Telegram-HTML converter (placeholder-substitution based, to protect code/math/table spans from escaping) — if you need to change formatting behavior, this is the single place to do it.
- **`src/handlers/`** — thin Telegram-event glue; `message_handler` (in `messages.py`) is the main text-message path. Bottom-keyboard button text is resolved via `src/i18n.resolve_button`, which maps either-language button labels back to a `("mode", key)` or `("settings"|"stats"|"clear", None)` action, so button matching survives a user switching UI language mid-session.
- System prompts and user-facing strings are Russian-first; per-user `language` setting (`ru`/`en`) is appended as an instruction suffix to the system prompt rather than swapping prompt language wholesale.

## Known issues

- `tests/run_checks.py` and `tests/run_benchmark.py` previously hardcoded the absolute project path and a `.venv` location; both now derive the project root from `__file__` and fall back to `sys.executable`/the current interpreter, so they're portable across machines.
