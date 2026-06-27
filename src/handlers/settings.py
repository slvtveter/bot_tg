from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from src.database import (
    get_user_language,
    get_user_mode,
    get_user_settings,
    reset_chat_context,
    set_user_setting,
)
from src.handlers.commands import build_main_keyboard
from src.i18n import creativity_value, language_value, length_value, t, util_label


def get_settings_keyboard(settings: dict, lang: str) -> InlineKeyboardMarkup:
    """Constructs the settings inline keyboard in the given language."""
    length_v = length_value(settings.get("max_length", "medium"), lang)
    creativity_v = creativity_value(settings.get("creativity", "balanced"), lang)
    language_v = language_value(settings.get("language", "ru"), lang)

    keyboard = [
        [
            InlineKeyboardButton(
                t("settings_length", lang, v=length_v),
                callback_data="settings_toggle_length",
            )
        ],
        [
            InlineKeyboardButton(
                t("settings_creativity", lang, v=creativity_v),
                callback_data="settings_toggle_creativity",
            )
        ],
        [
            InlineKeyboardButton(
                t("settings_language", lang, v=language_v),
                callback_data="settings_toggle_language",
            )
        ],
        [
            InlineKeyboardButton(
                util_label("stats", lang), callback_data="settings_stats"
            ),
            InlineKeyboardButton(
                util_label("clear", lang), callback_data="settings_clear"
            ),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends the settings control panel in the user's language."""
    user = update.effective_user
    if not user or not update.message:
        return

    settings = await get_user_settings(user.id)
    lang = settings.get("language", "ru")
    await update.message.reply_html(
        t("settings_title", lang), reply_markup=get_settings_keyboard(settings, lang)
    )


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles inline callbacks to toggle user settings."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    user_id = query.from_user.id
    data = query.data or ""

    # Stats and Clear-chat now live inside the Settings panel.
    if data == "settings_stats":
        from src.handlers.commands import build_stats_text

        lang = await get_user_language(user_id)
        await query.message.reply_html(await build_stats_text(user_id, lang))
        return
    if data == "settings_clear":
        lang = await get_user_language(user_id)
        await reset_chat_context(user_id)
        await query.message.reply_html(t("clear_done", lang))
        return

    if not data.startswith("settings_toggle_"):
        return

    settings = await get_user_settings(user_id)
    language_changed = False

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
        language_changed = True

    lang = settings.get("language", "ru")
    # Re-render the panel text + keyboard in the (possibly new) language.
    await query.edit_message_text(
        text=t("settings_title", lang),
        parse_mode="HTML",
        reply_markup=get_settings_keyboard(settings, lang),
    )

    # The bottom reply keyboard can't be edited in place, so on a language switch
    # send a fresh one so the whole interface follows the new language.
    if language_changed:
        current_mode = await get_user_mode(user_id)
        await context.bot.send_message(
            chat_id=user_id,
            text=t("language_switched", lang),
            reply_markup=build_main_keyboard(lang, current_mode),
        )
