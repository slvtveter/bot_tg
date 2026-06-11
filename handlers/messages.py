from telegram import Update
from telegram.ext import ContextTypes
from database import (
    get_user_mode,
    set_user_mode,
    log_message,
    log_usage_stats,
    get_chat_history,
    get_user_settings,
)
from llm import ask_llm
from utils import to_telegram_html


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles incoming text messages, routes menu button selections, retrieves chat history,
    queries the LLM, saves interaction details to the database, and sends HTML responses.
    """
    user = update.effective_user
    if (
        not user
        or not update.message
        or not update.message.text
        or not update.effective_chat
    ):
        return

    user_id = user.id
    text = update.message.text.strip()

    # Route Bottom Keyboard Buttons
    if text == "🍏 Питание":
        await set_user_mode(user_id, "nutrition")
        await update.message.reply_html("Режим работы изменен на: <b>🍏 Питание</b>")
        return
    elif text == "🧮 Математика":
        await set_user_mode(user_id, "math")
        await update.message.reply_html("Режим работы изменен на: <b>🧮 Математика</b>")
        return
    elif text == "💬 Общение":
        await set_user_mode(user_id, "general")
        await update.message.reply_html("Режим работы изменен на: <b>💬 Общение</b>")
        return
    elif text == "📊 Статистика":
        from handlers.commands import stats_command

        await stats_command(update, context)
        return
    elif text == "🧹 Очистить чат":
        from handlers.commands import clear_command

        await clear_command(update, context)
        return
    elif text == "⚙️ Настройки":
        from handlers.settings import settings_command

        await settings_command(update, context)
        return

    # Show typing status to user
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action="typing"
    )

    # 1. Log the user's message first
    await log_message(user_id=user_id, role="user", content=text)

    # 2. Query user's current mode
    mode = await get_user_mode(user_id=user_id)

    # 3. Retrieve recent history (including the logged user message)
    history = await get_chat_history(user_id=user_id, limit=15)

    # 3.5. Retrieve settings
    settings = await get_user_settings(user_id=user_id)

    # 4. Query LLM
    response_text, model_name, prompt_tokens, completion_tokens, latency = (
        await ask_llm(mode=mode, history=history, user_settings=settings)
    )

    if response_text:
        # 5. Save bot's reply
        await log_message(user_id=user_id, role="assistant", content=response_text)

        # 6. Save stats to DB
        await log_usage_stats(
            user_id=user_id,
            model=model_name or "unknown",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency=latency,
        )

        # 7. Format using utils.py and reply
        formatted_response = to_telegram_html(response_text)
        await update.message.reply_html(formatted_response)
    else:
        # All models failed
        await update.message.reply_text(
            "⚠️ К сожалению, не удалось получить ответ от ИИ. Пожалуйста, попробуйте позже."
        )
