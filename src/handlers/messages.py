import re

from telegram import Bot, Update
from telegram.ext import ContextTypes

from src.database import (
    get_chat_history,
    get_user_context,
    get_user_language,
    log_message,
    log_usage_stats,
    set_user_mode,
    upsert_user,
)
from src.i18n import mode_title, resolve_button, t
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
    # 1. Fetch mode + settings (length/creativity/language) in ONE query to cut
    # per-message round-trips to the remote DB.
    ctx = await get_user_context(user_id=user_id)
    mode = ctx["mode"]
    settings = {
        "max_length": ctx["max_length"],
        "creativity": ctx["creativity"],
        "language": ctx["language"],
    }

    # 2. Retrieve recent history BEFORE logging the new message, so the model
    # gets the prior turns as context and the current message isn't duplicated
    # (the agent appends it once). The history is token-trimmed inside ask_llm.
    history = await get_chat_history(user_id=user_id, limit=20)

    # 4. Log the user's message now that prior history has been captured
    await log_message(user_id=user_id, role="user", content=text)

    # 5. Query Orchestrator (returns the answer plus real LLM telemetry)
    response_text, model_name, prompt_tokens, completion_tokens, latency = (
        await orchestrator.route_and_process(
            mode=mode,
            user_input=text,
            history=history,
            user_settings=settings,
            user_id=user_id,
        )
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
            text=t("error_no_answer", settings.get("language", "ru")),
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

    # Single mode-toggle button: its label shows the CURRENT mode; tapping it
    # switches between the smart default (general) and Nutrition, then resends
    # the keyboard so the label updates.
    if text.startswith("🔄"):
        from src.handlers.commands import build_main_keyboard

        ctx = await get_user_context(user_id)
        new_mode = "general" if ctx["mode"] == "nutrition" else "nutrition"
        await set_user_mode(user_id, new_mode)
        await update.message.reply_html(
            t("mode_changed", ctx["language"], title=mode_title(new_mode, ctx["language"])),
            reply_markup=build_main_keyboard(ctx["language"], new_mode),
        )
        return

    # Route bottom-keyboard buttons (matched in either language) via the
    # registry, so switching the interface language never breaks the buttons.
    button = resolve_button(text)
    if button:
        kind, mode = button
        if kind == "mode":
            await set_user_mode(user_id, mode)
            lang = await get_user_language(user_id)
            from src.handlers.commands import build_main_keyboard

            await update.message.reply_html(
                t("mode_changed", lang, title=mode_title(mode, lang)),
                reply_markup=build_main_keyboard(lang, mode),
            )
            return
        if kind == "stats":
            from src.handlers.commands import stats_command

            await stats_command(update, context)
            return
        if kind == "clear":
            from src.handlers.commands import clear_command

            await clear_command(update, context)
            return
        if kind == "settings":
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
