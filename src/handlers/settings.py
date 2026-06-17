from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from src.database import get_user_settings, set_user_setting

# Translation maps for display
LENGTH_MAP = {"short": "Краткий ⚡", "medium": "Средний ⚖️", "long": "Подробный 📖"}
CREATIVITY_MAP = {
    "strict": "Строгий 🔒",
    "balanced": "Сбалансированный 🤝",
    "creative": "Креативный 🎨",
}
LANGUAGE_MAP = {"ru": "Русский 🇷🇺", "en": "English 🇬🇧"}


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Sends the settings control panel with inline keyboard buttons.
    """
    user = update.effective_user
    if not user or not update.message:
        return

    user_id = user.id
    settings = await get_user_settings(user_id)

    text = (
        "⚙️ <b>Панель настроек ИИ-ассистента</b>\n\n"
        "Настройте поведение и формат ответов модели под ваши нужды. "
        "Параметры будут применены ко всем текстовым запросам."
    )

    reply_markup = get_settings_keyboard(settings)
    await update.message.reply_html(text, reply_markup=reply_markup)


def get_settings_keyboard(settings: dict) -> InlineKeyboardMarkup:
    """
    Constructs the settings inline keyboard.
    """
    length_val = LENGTH_MAP.get(settings.get("max_length", "medium"), "Средний")
    creativity_val = CREATIVITY_MAP.get(
        settings.get("creativity", "balanced"), "Сбалансированный"
    )
    lang_val = LANGUAGE_MAP.get(settings.get("language", "ru"), "Русский")

    keyboard = [
        [
            InlineKeyboardButton(
                f"📏 Длина: {length_val}", callback_data="settings_toggle_length"
            )
        ],
        [
            InlineKeyboardButton(
                f"🧠 Креативность: {creativity_val}",
                callback_data="settings_toggle_creativity",
            )
        ],
        [
            InlineKeyboardButton(
                f"🌐 Язык ИИ: {lang_val}", callback_data="settings_toggle_language"
            )
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles inline callback queries to toggle user settings.
    """
    query = update.callback_query
    if not query:
        return
    await query.answer()

    user_id = query.from_user.id
    data = query.data

    if not data.startswith("settings_toggle_"):
        return

    # Fetch current settings
    settings = await get_user_settings(user_id)

    # Determine toggle
    if data == "settings_toggle_length":
        current = settings.get("max_length", "medium")
        new_val = (
            "medium"
            if current == "short"
            else ("long" if current == "medium" else "short")
        )
        await set_user_setting(user_id, "max_length", new_val)
        settings["max_length"] = new_val
    elif data == "settings_toggle_creativity":
        current = settings.get("creativity", "balanced")
        new_val = (
            "balanced"
            if current == "strict"
            else ("creative" if current == "balanced" else "strict")
        )
        await set_user_setting(user_id, "creativity", new_val)
        settings["creativity"] = new_val
    elif data == "settings_toggle_language":
        current = settings.get("language", "ru")
        new_val = "en" if current == "ru" else "ru"
        await set_user_setting(user_id, "language", new_val)
        settings["language"] = new_val

    # Update the settings message keyboard
    reply_markup = get_settings_keyboard(settings)
    await query.edit_message_reply_markup(reply_markup=reply_markup)
