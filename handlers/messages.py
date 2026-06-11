from telegram import Update
from telegram.ext import ContextTypes
from database import get_user_mode, log_message, log_usage_stats, get_chat_history
from llm import ask_llm
from utils import to_telegram_html

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles incoming text messages, retrieves chat history, queries the LLM, 
    saves interaction details to the database, and sends formatted HTML responses.
    """
    user = update.effective_user
    if not user or not update.message or not update.message.text:
        return

    user_id = user.id
    text = update.message.text

    # Show typing status to user
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # 1. Log the user's message first
    await log_message(user_id=user_id, role="user", content=text)

    # 2. Query user's current mode
    mode = await get_user_mode(user_id=user_id)

    # 3. Retrieve recent history (including the logged user message)
    history = await get_chat_history(user_id=user_id, limit=15)

    # 4. Query LLM
    response_text, model_name, prompt_tokens, completion_tokens, latency = await ask_llm(
        mode=mode, 
        history=history
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
            latency=latency
        )

        # 7. Format using utils.py and reply
        formatted_response = to_telegram_html(response_text)
        await update.message.reply_html(formatted_response)
    else:
        # All models failed
        await update.message.reply_html(
            "⚠️ К сожалению, не удалось получить ответ от ИИ. Пожалуйста, попробуйте позже."
        )
