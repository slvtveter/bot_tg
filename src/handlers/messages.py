import asyncio
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
    # 1. Fetch user context (mode + settings in ONE query) and recent history
    # concurrently — they're independent reads, and on the remote Turso backend
    # each one is an HTTP round trip, so running them in parallel halves that
    # part of the per-message latency. History is fetched BEFORE the new message
    # is logged, so the model gets the prior turns as context and the current
    # message isn't duplicated (the agent appends it once).
    ctx, history = await asyncio.gather(
        get_user_context(user_id=user_id),
        get_chat_history(user_id=user_id, limit=20),
    )
    mode = ctx["mode"]
    settings = {
        "max_length": ctx["max_length"],
        "creativity": ctx["creativity"],
        "language": ctx["language"],
    }

    # 2. Query the Orchestrator (returns the answer plus real LLM telemetry),
    # logging the user's message concurrently — the history snapshot above is
    # already taken, so the write doesn't need to finish first, and this hides
    # one more DB round trip behind the (much slower) LLM call. For general
    # mode this runs the web_search tool path: the model itself decides whether
    # to search, runs Tavily if so, and the source footer is already on the
    # returned text.
    (response_text, model_name, prompt_tokens, completion_tokens, latency), _ = (
        await asyncio.gather(
            orchestrator.route_and_process(
                mode=mode,
                user_input=text,
                history=history,
                user_settings=settings,
                user_id=user_id,
            ),
            log_message(user_id=user_id, role="user", content=text),
        )
    )

    if response_text:
        # 3. Send the reply to the user FIRST (rich message with fallbacks) —
        # the two bookkeeping writes below used to run before this, adding two
        # remote-DB round trips to the user's perceived response time.
        await send_response(
            bot=bot,
            chat_id=chat_id,
            text=response_text,
            reply_to_message_id=reply_to_message_id,
        )

        # 4. Save the bot's reply and the usage stats (independent writes).
        await asyncio.gather(
            log_message(user_id=user_id, role="assistant", content=response_text),
            log_usage_stats(
                user_id=user_id,
                model=model_name or "unknown",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency=latency,
            ),
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
