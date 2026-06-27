"""
Internationalization: the single source of truth for the bot's UI language.

The per-user ``language`` setting (ru/en) drives both the LLM reply language and
the whole interface - keyboards, mode names, settings, stats, help and error
messages. Adding a mode means one entry in ``_MODES`` (plus a system prompt in
``src.llm``); adding a string means one entry in ``STRINGS``.
"""

from typing import Dict, List, Optional, Tuple

DEFAULT_LANG = "ru"

# Mode registry: order matters (keyboard / pickers follow it). Labels keep their
# emoji across languages; only the words change.
_MODES: Dict[str, Dict[str, str]] = {
    "general": {
        "ru": "💬 Общение", "en": "💬 Chat",
        "tag_ru": "Универсальный помощник по любым вопросам, включая питание",
        "tag_en": "A versatile assistant for any question, nutrition included",
    },
    "math": {
        "ru": "🧮 Математика", "en": "🧮 Math",
        "tag_ru": "Решение задач по шагам с формулами",
        "tag_en": "Step-by-step problem solving with formulas",
    },
    "fitness": {
        "ru": "💪 Тренер", "en": "💪 Trainer",
        "tag_ru": "Программы тренировок и техника упражнений",
        "tag_en": "Workout plans and exercise technique",
    },
    "writing": {
        "ru": "✍️ Текст", "en": "✍️ Writing",
        "tag_ru": "Письма, посты, резюме, переводы, рерайт",
        "tag_en": "Emails, posts, resumes, translations, rewrites",
    },
    "code": {
        "ru": "💻 Код", "en": "💻 Code",
        "tag_ru": "Помощь с программированием и кодом",
        "tag_en": "Help with programming and code",
    },
}

MODE_KEYS: List[str] = list(_MODES.keys())
DEFAULT_MODE = "general"

# Modes surfaced in the UI (bottom keyboard + /mode picker). The remaining
# prompts (math/fitness/writing/code) still exist and work internally, but the
# product is now "one smart default assistant": the general brain handles
# math/code/writing AND nutrition on its own (a food photo or meal description is
# auto-analysed — see SYSTEM_PROMPTS["general"]), so the user never picks a mode.
# Add a key here to re-expose another mode as a button.
VISIBLE_MODES: List[str] = ["general"]

# Bottom-keyboard utility buttons (not modes).
_UTIL = {
    "settings": {"ru": "⚙️ Настройки", "en": "⚙️ Settings"},
    "stats": {"ru": "📊 Статистика", "en": "📊 Stats"},
    "clear": {"ru": "🆕 Новый чат", "en": "🆕 New chat"},
}


def normalize_lang(lang: Optional[str]) -> str:
    return "en" if lang == "en" else "ru"


def mode_label(mode: str, lang: str) -> str:
    """Localized label (with emoji) for a mode."""
    cfg = _MODES.get(mode, _MODES[DEFAULT_MODE])
    return cfg.get(normalize_lang(lang), cfg["ru"])


# The picker/confirmation title is the same as the label here.
mode_title = mode_label


def mode_tagline(mode: str, lang: str) -> str:
    cfg = _MODES.get(mode, _MODES[DEFAULT_MODE])
    return cfg.get("tag_" + normalize_lang(lang), cfg["tag_ru"])


def util_label(action: str, lang: str) -> str:
    return _UTIL[action].get(normalize_lang(lang), _UTIL[action]["ru"])


# Reverse map: every label in every language -> what it does. Lets the bottom
# keyboard keep working right after the user flips the interface language.
def _build_button_index() -> Dict[str, Tuple[str, Optional[str]]]:
    index: Dict[str, Tuple[str, Optional[str]]] = {}
    for mode, cfg in _MODES.items():
        index[cfg["ru"]] = ("mode", mode)
        index[cfg["en"]] = ("mode", mode)
    for action, cfg in _UTIL.items():
        index[cfg["ru"]] = (action, None)
        index[cfg["en"]] = (action, None)
    return index


_BUTTON_INDEX = _build_button_index()


def resolve_button(text: str) -> Optional[Tuple[str, Optional[str]]]:
    """
    Maps a bottom-keyboard button (in either language) to an action:
    ("mode", <mode_key>) or ("settings"|"stats"|"clear", None). None if not a button.
    """
    return _BUTTON_INDEX.get(text)


# Settings display values.
LENGTH_VALUES = {
    "short": {"ru": "Краткий ⚡", "en": "Short ⚡"},
    "medium": {"ru": "Средний ⚖️", "en": "Medium ⚖️"},
    "long": {"ru": "Подробный 📖", "en": "Long 📖"},
}
CREATIVITY_VALUES = {
    "strict": {"ru": "Строгий 🔒", "en": "Strict 🔒"},
    "balanced": {"ru": "Сбалансированный 🤝", "en": "Balanced 🤝"},
    "creative": {"ru": "Креативный 🎨", "en": "Creative 🎨"},
}
LANGUAGE_VALUES = {
    "ru": {"ru": "Русский 🇷🇺", "en": "Русский 🇷🇺"},
    "en": {"ru": "English 🇬🇧", "en": "English 🇬🇧"},
}


def _val(table: Dict[str, Dict[str, str]], key: str, lang: str) -> str:
    cfg = table.get(key, {})
    return cfg.get(normalize_lang(lang), cfg.get("ru", key))


def length_value(key: str, lang: str) -> str:
    return _val(LENGTH_VALUES, key, lang)


def creativity_value(key: str, lang: str) -> str:
    return _val(CREATIVITY_VALUES, key, lang)


def language_value(key: str, lang: str) -> str:
    return _val(LANGUAGE_VALUES, key, lang)


STRINGS: Dict[str, Dict[str, str]] = {
    "welcome": {
        "ru": (
            "Привет, {name}! 👋\n\n"
            "Я <b>Nela AI</b> — умный ИИ-ассистент. Просто пиши, что нужно — посчитать, объяснить, "
            "переписать текст, помочь с кодом, разобраться в чём угодно. Понимаю "
            "фото и голосовые.\n\n"
            "🍏 <b>Питание</b> — просто пришли фото блюда или опиши его: сам посчитаю "
            "калории и БЖУ и буду вести дневник (/today — за сегодня, /week — за "
            "неделю). Ничего переключать не нужно.\n\n"
            "Справка — /help."
        ),
        "en": (
            "Hi, {name}! 👋\n\n"
            "I'm <b>Nela AI</b> — your smart AI assistant. Just tell me what you need — calculate, explain, "
            "rewrite text, help with code, figure anything out. I understand photos "
            "and voice messages.\n\n"
            "🍏 <b>Nutrition</b> — just send a photo of your meal or describe it: I'll "
            "count calories and macros and keep a diary on my own (/today for today, "
            "/week for the week). No mode to switch.\n\n"
            "Help — /help."
        ),
    },
    "mode_changed": {
        "ru": "Режим работы изменён на: <b>{title}</b>",
        "en": "Mode switched to: <b>{title}</b>",
    },
    "mode_pick": {
        "ru": "Выбери режим работы бота:",
        "en": "Choose the bot mode:",
    },
    "clear_done": {
        "ru": "Начинаем с чистого листа. 🆕",
        "en": "Starting fresh. 🆕",
    },
    "remind_set": {
        "ru": "⏰ Напомню в {time} (UTC). Напоминание: «{text}»",
        "en": "⏰ I'll remind you at {time} (UTC). Reminder: «{text}»",
    },
    "remind_usage": {
        "ru": "Использование: /remind ЧЧ:ММ текст напоминания\nПример: /remind 18:00 покушать",
        "en": "Usage: /remind HH:MM reminder text\nExample: /remind 18:00 eat lunch",
    },
    "remind_fired": {
        "ru": "⏰ Напоминание: {text}",
        "en": "⏰ Reminder: {text}",
    },
    "week_none": {
        "ru": (
            "За последние 7 дней вы не отправляли блюда на анализ. "
            "Пришлите фото или описание еды — я посчитаю КБЖУ."
        ),
        "en": (
            "You haven't sent any meals for analysis in the last 7 days. "
            "Send a food photo or description — I'll count the macros."
        ),
    },
    "week_body": {
        "ru": (
            "📈 <b>Итоги питания за 7 дней</b>\n\n"
            "• Приёмов пищи: <code>{entries}</code> за <code>{days}</code> дн.\n"
            "• Калорий всего: <code>{cal}</code> ккал\n"
            "• В среднем в день: <code>{avg}</code> ккал\n"
            "• Белки: <code>{p}</code> г · Жиры: <code>{f}</code> г · Углеводы: <code>{c}</code> г\n"
        ),
        "en": (
            "📈 <b>Nutrition over 7 days</b>\n\n"
            "• Meals: <code>{entries}</code> over <code>{days}</code> day(s)\n"
            "• Total calories: <code>{cal}</code> kcal\n"
            "• Daily average: <code>{avg}</code> kcal\n"
            "• Protein: <code>{p}</code> g · Fat: <code>{f}</code> g · Carbs: <code>{c}</code> g\n"
        ),
    },
    "stats_none": {
        "ru": "У вас пока нет статистики. Отправьте мне несколько сообщений!",
        "en": "You don't have any stats yet. Send me a few messages!",
    },
    "stats_title": {"ru": "📊 <b>Ваша статистика</b>\n\n", "en": "📊 <b>Your stats</b>\n\n"},
    "stats_requests": {
        "ru": "• Запросов к ИИ: <code>{n}</code>\n",
        "en": "• AI requests: <code>{n}</code>\n",
    },
    "stats_meals": {
        "ru": "• Проанализировано блюд: <code>{n}</code>\n",
        "en": "• Meals analyzed: <code>{n}</code>\n",
    },
    "stats_latency": {
        "ru": "• Средняя задержка ответа: <code>{x} сек</code>\n",
        "en": "• Average response latency: <code>{x} s</code>\n",
    },
    "stats_since": {
        "ru": "• С нами с: <code>{d}</code>\n",
        "en": "• Member since: <code>{d}</code>\n",
    },
    "stats_lastseen": {
        "ru": "• Последняя активность: <code>{d}</code>\n",
        "en": "• Last active: <code>{d}</code>\n",
    },
    "stats_models": {
        "ru": "\n🤖 <b>Запросов по моделям:</b>\n",
        "en": "\n🤖 <b>Requests by model:</b>\n",
    },
    "feedback_usage": {
        "ru": (
            "Напишите отзыв или идею после команды, например:\n"
            "<code>/feedback добавьте режим перевода</code>"
        ),
        "en": (
            "Write your feedback or idea after the command, e.g.:\n"
            "<code>/feedback add a translation mode</code>"
        ),
    },
    "feedback_thanks": {
        "ru": "Спасибо! Ваш отзыв сохранён. 🙏",
        "en": "Thank you! Your feedback has been saved. 🙏",
    },
    "error_no_answer": {
        "ru": "⚠️ К сожалению, не удалось получить ответ от ИИ. Пожалуйста, попробуйте позже.",
        "en": "⚠️ Sorry, I couldn't get a response from the AI. Please try again later.",
    },
    "photo_failed": {
        "ru": "⚠️ Не удалось проанализировать изображение. Попробуйте еще раз позже.",
        "en": "⚠️ Couldn't analyze the image. Please try again later.",
    },
    "photo_error": {
        "ru": "❌ Произошла ошибка при обработке фотографии: <code>{e}</code>",
        "en": "❌ An error occurred while processing the photo: <code>{e}</code>",
    },
    "voice_failed": {
        "ru": "⚠️ Не удалось распознать голосовое сообщение. Попробуйте ещё раз.",
        "en": "⚠️ Couldn't transcribe the voice message. Please try again.",
    },
    "voice_error": {
        "ru": "❌ Произошла ошибка при обработке голосового сообщения: <code>{e}</code>",
        "en": "❌ An error occurred while processing the voice message: <code>{e}</code>",
    },
    "settings_title": {
        "ru": (
            "⚙️ <b>Панель настроек ИИ-ассистента</b>\n\n"
            "Настройте поведение и формат ответов модели под ваши нужды. "
            "Параметры применяются ко всем текстовым запросам."
        ),
        "en": (
            "⚙️ <b>AI assistant settings</b>\n\n"
            "Tune the model's behavior and response format. These settings apply "
            "to all text requests."
        ),
    },
    "settings_length": {"ru": "📏 Длина: {v}", "en": "📏 Length: {v}"},
    "settings_creativity": {"ru": "🧠 Креативность: {v}", "en": "🧠 Creativity: {v}"},
    "settings_language": {"ru": "🌐 Язык: {v}", "en": "🌐 Language: {v}"},
    "language_switched": {
        "ru": "Язык интерфейса переключён на русский.",
        "en": "Interface language switched to English.",
    },
    "help": {
        "ru": (
            "❓ <b>Справка</b>\n\n"
            "Просто напиши сообщение — я помогу с вопросами, текстом, кодом, "
            "математикой и объяснениями. Понимаю фото и голосовые.\n\n"
            "🍏 <b>Питание</b> — просто пришли фото блюда или опиши его: сам дам КБЖУ "
            "и буду вести дневник. Переключать режимы не нужно.\n\n"
            "<b>Команды</b>\n"
            "/today — итоги питания за сегодня\n"
            "/week — итоги питания за 7 дней\n"
            "/settings — длина ответов, креативность, язык\n"
            "/stats — ваша статистика\n"
            "/clear — очистить историю и начать заново\n"
            "/feedback — отправить отзыв или идею\n"
            "/privacy — как мы храним данные (анонимно)\n\n"
            "<b>Группы</b>: добавьте бота в чат и обращайтесь через @упоминание или "
            "ответом на его сообщение."
        ),
        "en": (
            "❓ <b>Help</b>\n\n"
            "Just send a message — I'll help with questions, writing, code, math and "
            "explanations. I understand photos and voice messages.\n\n"
            "🍏 <b>Nutrition</b> — just send a photo of your meal or describe it: "
            "I'll give calories/macros and keep a diary on my own. No mode to switch.\n\n"
            "<b>Commands</b>\n"
            "/today — today's nutrition totals\n"
            "/week — nutrition over the last 7 days\n"
            "/settings — response length, creativity, language\n"
            "/stats — your stats\n"
            "/clear — clear history and start fresh\n"
            "/feedback — send feedback or an idea\n"
            "/privacy — how we store your data (anonymously)\n\n"
            "<b>Groups</b>: add the bot to a chat and address it with an @mention or "
            "by replying to its message."
        ),
    },
    "privacy": {
        "ru": (
            "🔒 <b>Приватность</b>\n\n"
            "Nela AI не читает ваши переписки и не знает, кто что спросил. "
            "Сообщения хранятся без привязки к имени или аккаунту — под "
            "анонимным идентификатором.\n\n"
            "Профиль (имя в Telegram, язык, режим) нужен только для работы бота. "
            "Мы не продаём данные и не связываем переписки с вашей личностью.\n\n"
            "/clear — удалить всю историю в любой момент."
        ),
        "en": (
            "🔒 <b>Privacy</b>\n\n"
            "Nela AI doesn't read your chats and doesn't know who asked what. "
            "Messages are stored with no link to your name or account — under an "
            "anonymous identifier.\n\n"
            "Your profile (Telegram name, language, mode) is only used to run the "
            "bot. We don't sell your data or tie chats to your identity.\n\n"
            "/clear — wipe your entire history anytime."
        ),
    },
    "today_none": {
        "ru": (
            "Сегодня вы ещё не отправляли блюда на анализ. "
            "Пришлите фото или описание еды — я посчитаю КБЖУ."
        ),
        "en": (
            "You haven't sent any meals for analysis today. "
            "Send a food photo or description — I'll count the macros."
        ),
    },
    "today_body": {
        "ru": (
            "🍽 <b>Итоги питания за сегодня</b>\n\n"
            "• Приёмов пищи: <code>{entries}</code>\n"
            "• Калорий всего: <code>{cal}</code> ккал\n"
            "• Белки: <code>{p}</code> г · Жиры: <code>{f}</code> г · Углеводы: <code>{c}</code> г\n"
        ),
        "en": (
            "🍽 <b>Today's nutrition</b>\n\n"
            "• Meals: <code>{entries}</code>\n"
            "• Total calories: <code>{cal}</code> kcal\n"
            "• Protein: <code>{p}</code> g · Fat: <code>{f}</code> g · Carbs: <code>{c}</code> g\n"
        ),
    },
}


def t(key: str, lang: str, **kwargs) -> str:
    """Localized string for key, formatted with kwargs if given."""
    table = STRINGS.get(key, {})
    text = table.get(normalize_lang(lang)) or table.get(DEFAULT_LANG, "")
    return text.format(**kwargs) if kwargs else text


def mode_overview(lang: str) -> str:
    """Newline-joined 'label — tagline' for every mode, for welcome/help."""
    return "\n".join(
        f"{mode_label(m, lang)} — {mode_tagline(m, lang)}" for m in MODE_KEYS
    )
