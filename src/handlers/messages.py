import re

from telegram import Bot, Update
from telegram.ext import ContextTypes

from src.database import (
    get_chat_history,
    get_user_mode,
    get_user_settings,
    log_message,
    log_usage_stats,
    set_user_mode,
    upsert_user,
)
from src.orchestrator import Orchestrator
from src.sender import send_response

# Initialize the orchestrator globally for now
orchestrator = Orchestrator()


def resolve_group_addressing(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> "tuple[bool, str]":
    """
    In private chats the bot always responds. In group/supergroup chats it
    only responds when explicitly addressed, to avoid spamming every message
    in the group and burning through the shared LLM quota on chatter that
    wasn't meant for it. "Addressed" means either an @mention of the bot's
    username anywhere in the text, or a reply to one of the bot's own
    messages. Returns (should_respond, text_with_mention_stripped).
    """
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return True, text

    bot_username = context.bot.username
    mention = f"@{bot_username}" if bot_username else None
    is_mentioned = bool(mention) and mention.lower() in text.lower()

    reply_to = update.message.reply_to_message if update.message else None
    is_reply_to_bot = bool(
        reply_to and reply_to.from_user and reply_to.from_user.id == context.bot.id
    )

    if not (is_mentioned or is_reply_to_bot):
        return False, text

    if is_mentioned and mention:
        text = re.sub(re.escape(mention), "", text, flags=re.IGNORECASE).strip()

    return True, text


async def process_text_message(
    bot: Bot, chat_id: int, user_id: int, text: str, reply_to_message_id: int
) -> None:
    """
    Shared pipeline for any message that resolves to plain text input (typed
    messages, or a transcription of a voice message): logs the message,
    routes it through the Orchestrator for the user's current mode, logs the
    reply and usage stats, and sends the response back to Telegram.
    """
    # 1. Log the user's message first
    await log_message(user_id=user_id, role="user", content=text)

    # 2. Query user's current mode
    mode = await get_user_mode(user_id=user_id)

    # 3. Retrieve recent history (including the logged user message)
    history = await get_chat_history(user_id=user_id, limit=15)

    # 3.5. Retrieve settings
    settings = await get_user_settings(user_id=user_id)

    # 4. Query Orchestrator
    response_text = await orchestrator.route_and_process(
        mode=mode, user_input=text, history=history, user_settings=settings, user_id=user_id
    )

    # Placeholder telemetry
    model_name = "Agentic-LLM"
    prompt_tokens = 0
    completion_tokens = 0
    latency = 0.0

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

        # 7. Send reply using Rich Message API (with fallback)
        await send_response(
            bot=bot,
            chat_id=chat_id,
            text=response_text,
            reply_to_message_id=reply_to_message_id,
        )
    else:
        # All models failed
        await bot.send_message(
            chat_id=chat_id,
            text="⚠️ К сожалению, не удалось получить ответ от ИИ. Пожалуйста, попробуйте позже.",
            reply_to_message_id=reply_to_message_id,
        )


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles incoming text messages, routes menu button selections, then
    delegates to process_text_message for the actual LLM round-trip.
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

    should_respond, text = resolve_group_addressing(update, context, text)
    if not should_respond:
        return

    # Defensive: guarantee the user row exists before any FK-constrained
    # write (log_message/log_usage_stats), in case this user reaches the
    # handler without ever triggering /start (e.g. a fresh database).
    await upsert_user(
        user_id=user_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
    )

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
        from src.handlers.commands import stats_command

        await stats_command(update, context)
        return
    elif text == "🧹 Очистить чат":
        from src.handlers.commands import clear_command

        await clear_command(update, context)
        return
    elif text == "⚙️ Настройки":
        from src.handlers.settings import settings_command

        await settings_command(update, context)
        return

    # Show typing status to user
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action="typing"
    )

    await process_text_message(
        bot=context.bot,
        chat_id=update.effective_chat.id,
        user_id=user_id,
        text=text,
        reply_to_message_id=update.message.message_id,
    )
