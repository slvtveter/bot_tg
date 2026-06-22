import html

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import ContextTypes

from src import config
from src.database import (
    add_feedback,
    clear_chat_history,
    get_usage_stats,
    get_user_activity_summary,
    get_user_language,
    get_user_mode,
    get_week_nutrition_totals,
    set_user_mode,
    upsert_user,
)
from src.i18n import (
    DEFAULT_MODE,
    VISIBLE_MODES,
    mode_label,
    mode_overview,
    mode_title,
    t,
    util_label,
)


def build_main_keyboard(lang: str) -> ReplyKeyboardMarkup:
    """
    Bottom reply keyboard in the user's language: all modes (3 per row) followed
    by a row of utility shortcuts.
    """
    labels = [mode_label(m, lang) for m in VISIBLE_MODES]
    rows = [labels]
    rows.append(
        [util_label("settings", lang), util_label("stats", lang), util_label("clear", lang)]
    )
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def build_mode_inline_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Inline keyboard for /mode, built from the registry (2 per row)."""
    rows = [
        [
            InlineKeyboardButton(mode_title(m, lang), callback_data=f"mode_{m}")
            for m in VISIBLE_MODES[i:i + 2]
        ]
        for i in range(0, len(VISIBLE_MODES), 2)
    ]
    return InlineKeyboardMarkup(rows)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Registers the user (without resetting their mode/settings) and greets them
    with an overview of every mode and their current one.
    """
    user = update.effective_user
    if not user or not update.message:
        return

    await upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
    )
    lang = await get_user_language(user.id)
    current_mode = await get_user_mode(user.id)

    welcome_text = t(
        "welcome",
        lang,
        name=html.escape(user.first_name or ""),
        modes=mode_overview(lang),
        current=mode_title(current_mode, lang),
    )
    await update.message.reply_html(welcome_text, reply_markup=build_main_keyboard(lang))


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clears the chat history for the user and confirms."""
    user = update.effective_user
    if not user or not update.message:
        return

    await clear_chat_history(user.id)
    lang = await get_user_language(user.id)
    await update.message.reply_html(t("clear_done", lang))


async def week_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows nutrition totals over the last 7 days, with a daily average."""
    user = update.effective_user
    if not user or not update.message:
        return

    lang = await get_user_language(user.id)
    totals = await get_week_nutrition_totals(user.id)
    if totals["entries"] == 0:
        await update.message.reply_html(t("week_none", lang))
        return

    days = totals["days"] or 1
    await update.message.reply_html(
        t(
            "week_body",
            lang,
            entries=totals["entries"],
            days=totals["days"],
            cal=f"{totals['calories']:.0f}",
            avg=f"{totals['calories'] / days:.0f}",
            p=f"{totals['protein']:.0f}",
            f=f"{totals['fat']:.0f}",
            c=f"{totals['carbs']:.0f}",
        )
    )


async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends an inline keyboard to choose the bot mode."""
    user = update.effective_user
    if not user or not update.message:
        return

    lang = await get_user_language(user.id)
    await update.message.reply_html(
        t("mode_pick", lang), reply_markup=build_mode_inline_keyboard(lang)
    )


async def mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Processes the inline keyboard mode selection."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    user_id = query.from_user.id
    data = query.data or ""
    mode_name = data[len("mode_"):] if data.startswith("mode_") else DEFAULT_MODE
    if mode_name not in MODE_KEYS:
        mode_name = DEFAULT_MODE

    await set_user_mode(user_id, mode_name)
    lang = await get_user_language(user_id)
    await query.edit_message_text(
        text=t("mode_changed", lang, title=mode_title(mode_name, lang)),
        parse_mode="HTML",
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the user's lifetime activity stats and per-model breakdown."""
    user = update.effective_user
    if not user or not update.message:
        return

    lang = await get_user_language(user.id)
    stats = await get_usage_stats(user_id=user.id)
    activity = await get_user_activity_summary(user_id=user.id)

    if stats["total_requests"] == 0 and activity["request_count"] == 0:
        await update.message.reply_html(t("stats_none", lang))
        return

    member_since = (activity["member_since"] or "").split(" ")[0]
    last_seen = (activity["last_seen"] or "").split(" ")[0]

    response = t("stats_title", lang)
    response += t("stats_requests", lang, n=activity["request_count"])
    response += t("stats_meals", lang, n=activity["meals_analyzed"])
    response += t("stats_latency", lang, x=f"{stats['avg_latency']:.2f}")
    if member_since:
        response += t("stats_since", lang, d=member_since)
    if last_seen:
        response += t("stats_lastseen", lang, d=last_seen)

    if stats.get("model_stats"):
        response += t("stats_models", lang)
        for model, m_data in stats["model_stats"].items():
            response += f"- <b>{html.escape(model)}</b>: <code>{m_data['requests']}</code>\n"

    await update.message.reply_html(response)


async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lets a user send feedback; stored permanently and forwarded to admins."""
    user = update.effective_user
    if not user or not update.message:
        return

    lang = await get_user_language(user.id)
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_html(t("feedback_usage", lang))
        return

    await upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
    )
    await add_feedback(user.id, user.username, text)

    sender = f"@{user.username}" if user.username else (user.first_name or f"id{user.id}")
    notice = (
        f"📨 <b>Новый отзыв</b> от {html.escape(sender)} "
        f"(<code>{user.id}</code>):\n\n{html.escape(text)}"
    )
    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id, text=notice, parse_mode="HTML"
            )
        except Exception:
            pass

    await update.message.reply_html(t("feedback_thanks", lang))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays compact, localized help."""
    user = update.effective_user
    if not update.message:
        return

    lang = await get_user_language(user.id) if user else "ru"
    await update.message.reply_html(t("help", lang, modes=mode_overview(lang)))
