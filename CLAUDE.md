# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Telegram bot (`src/bot.py`, built on `python-telegram-bot`) that proxies user messages to Gemini/OpenRouter LLMs through a small Orchestrator → Agent architecture. Three modes: `general`, `nutrition`, `math`. Originally a single monolithic script; it was refactored into `src/` packages (see `facc296` commit) but some loose ends from that refactor remain (see Known Issues below).

## Commands

- Install deps: `pip install -r requirements.txt`
- Run the bot: `python -m src.bot` (entry point lives at `src/bot.py`; there is no root-level `bot.py` despite the Dockerfile's `CMD ["python", "bot.py"]` — that line is stale and would not work as-is)
- Run tests: `python -m unittest discover -s tests -p "test_*.py"` — **currently broken** (see Known Issues)
- Run the flakiness loop (20x test runs): `python tests/run_checks.py`
- Run the formatting/latency benchmark: `python tests/run_benchmark.py`
- Lint: `flake8` (config in `.flake8`: max-line-length 120, ignores E402/W503/W291)

## Architecture

Request flow for a text message: `handlers/messages.py` → `Orchestrator.route_and_process` (`src/orchestrator.py`) → mode-specific `Agent.process` (`src/agents/`) → `ask_llm` (`src/llm.py`) → `sender.send_response` (`src/sender.py`).

- **`src/orchestrator.py`** — `Orchestrator` holds a dict of `{mode_name: AgentInstance}` (currently `nutrition`, `math`) and dispatches by the user's stored mode. There is no `general` entry in `self.agents`; general-mode messages aren't routed through an agent object — check `src/handlers/messages.py` for how `general` is actually handled before assuming agent-based dispatch covers all modes.
- **`src/agents/base.py`** — `BaseAgent` ABC requiring `process(user_input, history) -> str`. Concrete agents (`NutritionAgent`, `MathAgent`) each duplicate a system prompt that is *also* defined in `SYSTEM_PROMPTS` inside `src/llm.py`. The agent's own `system_prompt` attribute is unused at call time — `ask_llm` re-selects the prompt from its own `SYSTEM_PROMPTS` dict by `mode` string, so the two copies must be kept in sync manually if either is edited.
- **`src/llm.py`** — All actual LLM I/O. Key behaviors to know before touching this file:
  - Tries direct Gemini API first (`KeyPool` rotates over `GOOGLE_API_KEYS`, putting keys on a 300s cooldown on 429/403/500/503 or invalid-key 400 responses), then falls back to OpenRouter models if no direct call succeeds.
  - `is_response_complete()` heuristically detects truncated output (unbalanced ``` ``` ``` / `**`, dangling table rows, trailing conjunctions/no punctuation) and is used to reject a model's response and fall through to the next model/key — this is a real control-flow signal, not just logging.
  - Auto-summarizes history when estimated prompt tokens exceed 6000 and history has more than 10 messages, by recursively calling itself (`is_summarizing=True` mode="general") on the oldest 10 messages and prepending the summary as a synthetic `system` message.
  - `estimate_tokens` is a char-count heuristic (Cyrillic ~2 chars/token, other ~4 chars/token), used only as a fallback when the API doesn't return real usage metadata.
- **`src/database.py`** — async SQLite (`aiosqlite`) with WAL mode and FK constraints. `init_db()` is defensive: it inspects `PRAGMA table_info`/`PRAGMA foreign_key_list` at startup and migrates older schemas in place (renaming tables, recreating with FKs, copying data) rather than assuming a fixed schema — relevant if you add a column or table, follow the same migrate-in-`init_db` pattern. `DB_PATH` defaults to an absolute path on the dev machine but is overridden via the `DB_PATH` env var in Docker (`/data/bot.db`).
- **`src/sender.py`** — three-tier send fallback: a `sendRichMessage` Telegram API call (not yet in `python-telegram-bot`, called directly via `httpx`) → `sendMessage` with HTML built by `src/utils.to_telegram_html` → plain text. `to_telegram_html` is a hand-rolled Markdown/LaTeX-to-Telegram-HTML converter (placeholder-substitution based, to protect code/math/table spans from escaping) — if you need to change formatting behavior, this is the single place to do it.
- **`src/handlers/`** — thin Telegram-event glue; `message_handler` (in `messages.py`) is the main text-message path. Note it logs placeholder telemetry (`model_name = "Agentic-LLM"`, tokens/latency all zero) to `log_usage_stats` because the Orchestrator → Agent path only returns the final text string, discarding the `(model, prompt_tokens, completion_tokens, latency)` tuple that `ask_llm` actually returns. Per-mode bottom-keyboard button labels (e.g. `"🍏 Питание"`) are matched as plain-text comparisons at the top of `message_handler`, not as commands.
- System prompts and user-facing strings are Russian-first; per-user `language` setting (`ru`/`en`) is appended as an instruction suffix to the system prompt rather than swapping prompt language wholesale.

## Known issues

- **Tests are currently broken**: `tests/test_suite.py` and `tests/test_llm_router.py` do `import utils`, `import database`, `import llm`, `import config` (no `src.` prefix), which fails under the current `src/`-based layout (`ModuleNotFoundError`). They were written against the pre-refactor flat layout and haven't been updated. Fix by either adding `src/` to `sys.path` in the tests or changing imports to `from src import ...`/`import src.llm as llm` before relying on `python -m unittest discover`.
- `tests/run_checks.py` and `tests/run_benchmark.py` hardcode the absolute project path `/Users/slvtveter/Desktop/PycharmProjects/bot_tg` and a specific `.venv` location — not portable off this machine.
- `Dockerfile`'s `CMD ["python", "bot.py"]` doesn't match the real entry point (`python -m src.bot`); the README correctly documents the latter.
