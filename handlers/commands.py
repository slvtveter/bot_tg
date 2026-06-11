from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import upsert_user, set_user_mode, clear_chat_history, get_usage_stats

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Registers the user, sets their mode to 'general', and greets them.
    """
    user = update.effective_user
    if not user:
        return

    await upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name
    )
    await set_user_mode(user.id, "general")
    
    welcome_text = (
        f"Привет, {user.first_name}! 👋\n\n"
        "Я умный бот-помощник, поддерживающий три режима работы:\n"
        "🍏 <b>Питание</b> (Nutrition) — отправьте фото еды для анализа состава и калорийности.\n"
        "🧮 <b>Математика</b> (Math) — подробный разбор формул и концепций с поддержкой LaTeX.\n"
        "💬 <b>Общий</b> (General) — вежливый ИИ-помощник по любым вопросам.\n\n"
        "По умолчанию установлен Общий режим.\n\n"
        "<b>Команды:</b>\n"
        "/mode — Выбрать режим работы\n"
        "/clear — Очистить историю сообщений\n"
        "/stats — Показать статистику использования\n"
        "/start — Начать заново"
    )
    await update.message.reply_html(welcome_text)


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Clears the chat history for the user and confirms deletion.
    """
    user = update.effective_user
    if not user:
        return

    await clear_chat_history(user.id)
    await update.message.reply_html("История сообщений успешно очищена! 🧹")


async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Sends an inline keyboard to choose the bot mode.
    """
    keyboard = [
        [
            InlineKeyboardButton("🍏 Питание (Nutrition)", callback_data="mode_nutrition"),
            InlineKeyboardButton("🧮 Математика (Math)", callback_data="mode_math"),
        ],
        [
            InlineKeyboardButton("💬 Общий (General)", callback_data="mode_general")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_html(
        "Выберите режим работы бота:", 
        reply_markup=reply_markup
    )


async def mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Processes the inline keyboard mode selection.
    """
    query = update.callback_query
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
        parse_mode="HTML"
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Displays aggregated stats for the user (total queries, tokens, average latency).
    """
    user = update.effective_user
    if not user:
        return

    stats = await get_usage_stats(user_id=user.id)
    
    if stats["total_requests"] == 0:
        await update.message.reply_html("У вас пока нет статистики использования. Отправьте мне несколько сообщений!")
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
            response += (
                f"- <b>{model}</b>:\n"
                f"  Запросы: <code>{m_data['requests']}</code>, "
                f"Токены: <code>{m_data['total_tokens']}</code>, "
                f"Задержка: <code>{m_data['avg_latency']:.2f} сек</code>\n"
            )
            
    await update.message.reply_html(response)
