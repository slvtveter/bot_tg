import html
import io

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import ContextTypes

from src.database import (
    clear_chat_history,
    delete_last_exchange,
    get_all_chat_history,
    get_today_nutrition_totals,
    get_usage_stats,
    set_user_mode,
    upsert_user,
)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Registers the user, sets their mode to 'general', and greets them.
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
    await set_user_mode(user.id, "general")

    escaped_first_name = html.escape(user.first_name or "")
    welcome_text = (
        f"Привет, {escaped_first_name}! 👋\n\n"
        "Я ваш персональный суперассистент, поддерживающий три продвинутых режима работы:\n\n"
        "🍏 <b>Питание</b> — отправляйте фото ваших блюд для мгновенного анализа калорийности и БЖУ.\n"
        "🧮 <b>Математика</b> — пошаговое обучение, решение задач и native-формулы LaTeX.\n"
        "💬 <b>Общение</b> — вежливый и структурированный ИИ-помощник по любым вопросам.\n\n"
        "По умолчанию активен режим 💬 <b>Общение</b>.\n\n"
        "Используйте кнопки нижнего меню для удобного управления и смены режимов!"
    )

    reply_keyboard = [
        ["🍏 Питание", "🧮 Математика", "💬 Общение"],
        ["⚙️ Настройки", "📊 Статистика", "🧹 Очистить чат"],
    ]
    reply_markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True)

    await update.message.reply_html(welcome_text, reply_markup=reply_markup)


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Clears the chat history for the user and confirms deletion.
    """
    user = update.effective_user
    if not user or not update.message:
        return

    await clear_chat_history(user.id)
    await update.message.reply_html("История сообщений успешно очищена! 🧹")


async def undo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Deletes the last user/assistant exchange from the chat history.
    """
    user = update.effective_user
    if not user or not update.message:
        return

    deleted = await delete_last_exchange(user.id)
    if deleted:
        await update.message.reply_html("Последний обмен сообщениями удалён из истории.")
    else:
        await update.message.reply_html("История пуста, нечего отменять.")


async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Sends the user's full chat history as a downloadable plain-text file.
    """
    user = update.effective_user
    if not user or not update.message:
        return

    history = await get_all_chat_history(user.id)
    if not history:
        await update.message.reply_html("История пуста, нечего экспортировать.")
        return

    role_labels = {"user": "Пользователь", "assistant": "Ассистент", "system": "Система"}
    lines = []
    for msg in history:
        label = role_labels.get(msg["role"], msg["role"])
        lines.append(f"[{msg['timestamp']}] {label}: {msg['content']}")
    file_content = "\n\n".join(lines).encode("utf-8")

    await update.message.reply_document(
        document=io.BytesIO(file_content),
        filename=f"chat_history_{user.id}.txt",
        caption=f"Экспорт истории сообщений ({len(history)} записей).",
    )


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Shows the sum of calories/protein/fat/carbs logged today via the nutrition agent.
    """
    user = update.effective_user
    if not user or not update.message:
        return

    totals = await get_today_nutrition_totals(user.id)
    if totals["entries"] == 0:
        await update.message.reply_html(
            "Сегодня вы ещё не отправляли блюда на анализ в режиме 🍏 Питание."
        )
        return

    response = (
        f"📅 <b>Итоги питания за сегодня</b> ({totals['entries']} приём(а/ов) пищи):\n\n"
        f"• Калории: <code>{totals['calories']:.0f}</code> ккал\n"
        f"• Белки: <code>{totals['protein']:.1f}</code> г\n"
        f"• Жиры: <code>{totals['fat']:.1f}</code> г\n"
        f"• Углеводы: <code>{totals['carbs']:.1f}</code> г\n"
    )
    await update.message.reply_html(response)


async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Sends an inline keyboard to choose the bot mode.
    """
    if not update.message:
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "🍏 Питание (Nutrition)", callback_data="mode_nutrition"
            ),
            InlineKeyboardButton("🧮 Математика (Math)", callback_data="mode_math"),
        ],
        [InlineKeyboardButton("💬 Общий (General)", callback_data="mode_general")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_html(
        "Выберите режим работы бота:", reply_markup=reply_markup
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
    data = query.data

    if data == "mode_nutrition":
        mode_name = "nutrition"
        friendly_name = "🍏 Питание"
    elif data == "mode_math":
        mode_name = "math"
        friendly_name = "🧮 Математика"
    else:
        mode_name = "general"
        friendly_name = "💬 Общий"

    await set_user_mode(user_id, mode_name)

    # Update message text
    await query.edit_message_text(
        text=f"Режим работы успешно изменен на: <b>{friendly_name}</b>",
        parse_mode="HTML",
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Displays aggregated stats for the user (total queries, tokens, average latency).
    """
    user = update.effective_user
    if not user or not update.message:
        return

    stats = await get_usage_stats(user_id=user.id)

    if stats["total_requests"] == 0:
        await update.message.reply_html(
            "У вас пока нет статистики использования. Отправьте мне несколько сообщений!"
        )
        return

    response = (
        f"📊 <b>Ваша статистика использования:</b>\n\n"
        f"• Всего запросов: <code>{stats['total_requests']}</code>\n"
        f"• Потрачено токенов (всего): <code>{stats['total_tokens']}</code>\n"
        f"  - Промпт: <code>{stats['total_prompt_tokens']}</code>\n"
        f"  - Ответ: <code>{stats['total_completion_tokens']}</code>\n"
        f"• Средняя задержка (latency): <code>{stats['avg_latency']:.2f} сек</code>\n"
    )

    if stats.get("model_stats"):
        response += "\n🤖 <b>По моделям:</b>\n"
        for model, m_data in stats["model_stats"].items():
            escaped_model = html.escape(model)
            response += (
                f"- <b>{escaped_model}</b>:\n"
                f"  Запросы: <code>{m_data['requests']}</code>, "
                f"Токены: <code>{m_data['total_tokens']}</code>, "
                f"Задержка: <code>{m_data['avg_latency']:.2f} сек</code>\n"
            )

    await update.message.reply_html(response)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Displays the help message with explanation of modes and commands.
    """
    if not update.message:
        return

    help_text = (
        "❓ <b>Справка и FAQ по использованию бота:</b>\n\n"
        "🍏 <b>Режим Питание (Nutrition):</b>\n"
        "Отправьте фотографию вашего блюда, и ИИ распознает ингредиенты, рассчитает "
        "примерный вес, калорийность, белки, жиры и углеводы (КБЖУ), оформив их в виде "
        "красивой таблицы. Можно добавить текстовое описание к фото "
        "(например, <i>'это на завтрак'</i>).\n\n"
        "🧮 <b>Режим Математика (Math):</b>\n"
        "Задавайте любые математические вопросы, формулы, просите объяснить теоремы. "
        "Бот выведет формулы в нативном формате LaTeX, который красиво отображается "
        "прямо в приложении Telegram.\n\n"
        "💬 <b>Режим Общение (General):</b>\n"
        "Универсальный помощник. Поддерживает контекст предыдущей беседы. "
        "Подходит для обычного общения, написания кода, переводов текстов и "
        "любых других вопросов.\n\n"
        "🎤 <b>Голосовые сообщения:</b>\n"
        "Можно просто наговорить вопрос голосом в любом режиме — бот распознает речь "
        "и ответит так же, как на обычное текстовое сообщение.\n\n"
        "👥 <b>Групповые чаты:</b>\n"
        "Добавьте бота в группу и обращайтесь к нему через @упоминание или ответом "
        "(reply) на его сообщение — в остальных сообщениях группы бот не участвует.\n\n"
        "🛠 <b>Полезные команды:</b>\n"
        "/today — итоги по питанию за сегодня (калории/БЖУ)\n"
        "/undo — отменить последний обмен сообщениями\n"
        "/export — выгрузить историю переписки в файл\n"
        "/settings — настроить длину ответов и креативность (температуру) ИИ"
    )
    await update.message.reply_html(help_text)
