"""
Central registry of the bot's agent modes.

This is the single source of truth for which modes exist and how they are
labelled in the UI. Adding a new mode is intentionally cheap: add one entry
here and one matching system prompt in ``src.llm.SYSTEM_PROMPTS`` and the
keyboard, the /mode picker, button routing, the command menu and help all pick
it up automatically.

Each entry:
- ``label``    bottom-keyboard button text (also matched on incoming messages).
- ``title``    human name used in confirmations and the /mode picker.
- ``tagline``  one-line description of what the mode is for (help / picker).
"""

from typing import Dict

MODES: Dict[str, Dict[str, str]] = {
    "general": {
        "label": "💬 Общение",
        "title": "💬 Общение",
        "tagline": "Универсальный помощник по любым вопросам",
    },
    "nutrition": {
        "label": "🍏 Питание",
        "title": "🍏 Питание",
        "tagline": "Калорийность и БЖУ блюд по фото или описанию",
    },
    "math": {
        "label": "🧮 Математика",
        "title": "🧮 Математика",
        "tagline": "Решение задач по шагам с формулами",
    },
    "fitness": {
        "label": "💪 Тренер",
        "title": "💪 Тренер",
        "tagline": "Программы тренировок и техника упражнений",
    },
    "writing": {
        "label": "✍️ Текст",
        "title": "✍️ Текст",
        "tagline": "Письма, посты, резюме, переводы, рерайт",
    },
    "code": {
        "label": "💻 Код",
        "title": "💻 Код",
        "tagline": "Помощь с программированием и кодом",
    },
}

DEFAULT_MODE = "general"

# Reverse lookup: bottom-keyboard button label -> mode key.
LABEL_TO_MODE: Dict[str, str] = {cfg["label"]: mode for mode, cfg in MODES.items()}


def mode_title(mode: str) -> str:
    """Human-readable title for a mode, falling back to the default."""
    return MODES.get(mode, MODES[DEFAULT_MODE])["title"]
