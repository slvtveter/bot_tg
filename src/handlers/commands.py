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
    get_week_nutrition_totals,
    set_user_mode,
    upsert_user,
)
from src.modes import DEFAULT_MODE, MODES, mode_title


def build_main_keyboard() -> ReplyKeyboardMarkup:
    """
    Bottom reply keyboard: all modes (3 per row, from the registry) followed by
    a row of utility shortcuts. New modes appear here automatically.
    """
    labels = [cfg["label"] for cfg in MODES.values()]
    rows = [labels[i:i + 3] for i in range(0, len(labels), 3)]
    rows.append(["⚙️ Настройки", "📊 Статистика", "🧹 Очистить чат"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def build_mode_inline_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard for /mode, built from the registry (2 per row)."""
    items = list(MODES.items())
    rows = [
        [
            InlineKeyboardButton(cfg["title"], callback_data=f"mode_{mode}")
            for mode, cfg in items[i:i + 2]
        ]
        for i in range(0, len(items), 2)
    ]
    return InlineKeyboardMarkup(rows)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Registers the user, sets their mode to the default, and greets them with an
    overview of every available mode.
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
    await set_user_mode(user.id, DEFAULT_MODE)

    escaped_first_name = html.escape(user.first_name or "")
    mode_lines = "\n".join(
        f"{cfg['label']} — {cfg['tagline']}" for cfg in MODES.values()
    )
    welcome_text = (
        f"Привет, {escaped_first_name}! 👋\n\n"
        "Я твой персональный ИИ-ассистент. Доступные режимы:\n\n"
        f"{mode_lines}\n\n"
        f"По умолчанию активно {MODES[DEFAULT_MODE]['title']}. Переключай режимы "
        "кнопками снизу или командой /mode. Можно присылать фото и голосовые — "
        "я их пойму. Полная справка — /help."
    )

    await update.message.reply_html(welcome_text, reply_markup=build_main_keyboard())


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Clears the chat history for the user and confirms deletion.
    """
    user = update.effective_user
    if not user or not update.message:
        return

    await clear_chat_history(user.id)
    await update.message.reply_html(
        "История очищена — начинаем с чистого листа. 🧹"
    )


async def week_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Shows nutrition totals over the last 7 days, with a daily average.
    """
    user = update.effective_user
    if not user or not update.message:
        return

    totals = await get_week_nutrition_totals(user.id)
    if totals["entries"] == 0:
        await update.message.reply_html(
            "За последние 7 дней вы не отправляли блюда на анализ в режиме 🍏 Питание."
        )
        return

    days = totals["days"] or 1
    avg_calories = totals["calories"] / days
    response = (
        f"📈 <b>Итоги питания за 7 дней</b>\n\n"
        f"• Приёмов пищи: <code>{totals['entries']}</code> за <code>{totals['days']}</code> дн.\n"
        f"• Калорий всего: <code>{totals['calories']:.0f}</code> ккал\n"
        f"• В среднем в день: <code>{avg_calories:.0f}</code> ккал\n"
        f"• Белки: <code>{totals['protein']:.0f}</code> г · "
        f"Жиры: <code>{totals['fat']:.0f}</code> г · "
        f"Углеводы: <code>{totals['carbs']:.0f}</code> г\n"
    )
    await update.message.reply_html(response)


async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Sends an inline keyboard to choose the bot mode.
    """
    if not update.message:
        return

    await update.message.reply_html(
        "Выбери режим работы бота:", reply_markup=build_mode_inline_keyboard()
    )


async def mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Processes the inline keyboard mode selection.
    """
    query = update.callback_query
    if not query:
        return
    await query.answer()

    user_id = query.from_user.id
    data = query.data or ""
    mode_name = data[len("mode_"):] if data.startswith("mode_") else DEFAULT_MODE
    if mode_name not in MODES:
        mode_name = DEFAULT_MODE

    await set_user_mode(user_id, mode_name)
    await query.edit_message_text(
        text=f"Режим работы изменён на: <b>{html.escape(mode_title(mode_name))}</b>",
        parse_mode="HTML",
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Displays the user's lifetime activity stats (requests, meals, latency,
    join date, last activity) plus a per-model request breakdown.
    """
    user = update.effective_user
    if not user or not update.message:
        return

    stats = await get_usage_stats(user_id=user.id)
    activity = await get_user_activity_summary(user_id=user.id)

    if stats["total_requests"] == 0 and activity["request_count"] == 0:
        await update.message.reply_html(
            "У вас пока нет статистики. Отправьте мне несколько сообщений!"
        )
        return

    member_since = (activity["member_since"] or "").split(" ")[0]
    last_seen = (activity["last_seen"] or "").split(" ")[0]

    response = (
        f"📊 <b>Ваша статистика</b>\n\n"
        f"• Запросов к ИИ: <code>{activity['request_count']}</code>\n"
        f"• Проанализировано блюд: <code>{activity['meals_analyzed']}</code>\n"
        f"• Средняя задержка ответа: <code>{stats['avg_latency']:.2f} сек</code>\n"
    )
    if member_since:
        response += f"• С нами с: <code>{member_since}</code>\n"
    if last_seen:
        response += f"• Последняя активность: <code>{last_seen}</code>\n"

    if stats.get("model_stats"):
        response += "\n🤖 <b>Запросов по моделям:</b>\n"
        for model, m_data in stats["model_stats"].items():
            escaped_model = html.escape(model)
            response += f"- <b>{escaped_model}</b>: <code>{m_data['requests']}</code>\n"

    await update.message.reply_html(response)


async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Lets a user send feedback or an idea. Stored permanently and forwarded to
    any configured admins.
    """
    user = update.effective_user
    if not user or not update.message:
        return

    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_html(
            "Напишите отзыв или идею после команды, например:\n"
            "<code>/feedback добавьте режим перевода</code>"
        )
        return

    await upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
    )
    await add_feedback(user.id, user.username, text)

    # Forward to admins (if any are configured) so they see it immediately.
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

    await update.message.reply_html("Спасибо! Ваш отзыв сохранён. 🙏")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Displays compact help: modes, how to use, and the command list.
    """
    if not update.message:
        return

    mode_section = "\n".join(
        f"{cfg['label']} — {cfg['tagline']}" for cfg in MODES.values()
    )
    help_text = (
        "❓ <b>Справка</b>\n\n"
        "<b>Режимы</b> (переключаются кнопками снизу или командой /mode):\n"
        f"{mode_section}\n\n"
        "<b>Как пользоваться</b>\n"
        "Просто напишите сообщение — бот ответит в текущем режиме. Можно "
        "присылать фото (например, блюдо в режиме Питание или скриншот кода в "
        "режиме Код) и голосовые — они распознаются автоматически.\n\n"
        "<b>Команды</b>\n"
        "/mode — выбрать режим\n"
        "/week — итоги питания за 7 дней\n"
        "/settings — длина ответов, креативность, язык\n"
        "/stats — ваша статистика\n"
        "/clear — очистить историю и начать заново\n"
        "/feedback — отправить отзыв или идею\n\n"
        "<b>Группы</b>: добавьте бота в чат и обращайтесь через @упоминание или "
        "ответом на его сообщение."
    )
    await update.message.reply_html(help_text)
