"""
sender.py — Rich Message sender for Telegram Bot.

Sends responses via the new sendRichMessage API (supports native Markdown,
LaTeX formulas, HTML tables, headings, lists, etc.).
Falls back to sendMessage + parse_mode=HTML if sendRichMessage fails.
Falls back to plain text if HTML also fails.
"""

import logging

import httpx
from telegram import Bot, Message

from src.utils import normalize_markdown_tables, to_telegram_html

logger = logging.getLogger(__name__)


async def send_response(
    bot: Bot,
    chat_id: int,
    text: str,
    reply_to_message_id: int | None = None,
    reply_markup=None,
) -> Message | None:
    """
    Sends a bot response using the best available method:

    1. sendRichMessage with markdown field (native Markdown/LaTeX/tables)
    2. sendMessage with parse_mode=HTML + to_telegram_html() conversion
    3. sendMessage with no formatting (plain text fallback)

    Args:
        bot: The telegram.Bot instance.
        chat_id: Target chat ID.
        text: Raw response text from LLM (Markdown format).
        reply_to_message_id: Optional message ID to reply to.
        reply_markup: Optional InlineKeyboardMarkup or ReplyKeyboardMarkup.

    Returns:
        The sent Message object, or None if all methods failed.
    """
    # Pre-process and normalize any markdown tables in the response text
    text = normalize_markdown_tables(text)

    # --- 1. Try sendRichMessage (native Markdown with LaTeX, tables, etc.) ---
    try:
        result = await _send_rich_message(
            bot, chat_id, text, reply_to_message_id, reply_markup
        )
        if result:
            logger.info("Sent response via sendRichMessage (markdown)")
            return result
    except Exception as e:
        logger.warning(f"sendRichMessage failed: {e}")

    # --- 2. Fallback: sendMessage with HTML formatting ---
    try:
        formatted_html = to_telegram_html(text)
        msg = await bot.send_message(
            chat_id=chat_id,
            text=formatted_html,
            parse_mode="HTML",
            reply_to_message_id=reply_to_message_id,
            reply_markup=reply_markup,
        )
        logger.info("Sent response via sendMessage (HTML fallback)")
        return msg
    except Exception as e:
        logger.warning(f"sendMessage HTML fallback failed: {e}")

    # --- 3. Last resort: plain text without any formatting ---
    try:
        msg = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_to_message_id=reply_to_message_id,
            reply_markup=reply_markup,
        )
        logger.info("Sent response via sendMessage (plain text fallback)")
        return msg
    except Exception as e:
        logger.error(f"All send methods failed: {e}")
        return None


async def _send_rich_message(
    bot: Bot,
    chat_id: int,
    markdown_text: str,
    reply_to_message_id: int | None = None,
    reply_markup=None,
) -> Message | None:
    """
    Calls the sendRichMessage API endpoint directly via httpx.
    python-telegram-bot v22.7 doesn't have native support for this method yet.
    """
    url = f"https://api.telegram.org/bot{bot.token}/sendRichMessage"

    payload: dict = {
        "chat_id": chat_id,
        "rich_message": {
            "markdown": markdown_text,
        },
    }

    if reply_to_message_id:
        payload["reply_parameters"] = {"message_id": reply_to_message_id}

    if reply_markup:
        # Serialize InlineKeyboardMarkup / ReplyKeyboardMarkup to dict
        if hasattr(reply_markup, "to_dict"):
            payload["reply_markup"] = reply_markup.to_dict()
        elif hasattr(reply_markup, "to_json"):
            import json

            payload["reply_markup"] = json.loads(reply_markup.to_json())

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, json=payload)

    if response.status_code == 200:
        data = response.json()
        if data.get("ok"):
            # Parse the result into a telegram.Message object
            from telegram import Message as TgMessage

            return TgMessage.de_json(data["result"], bot)

    # Log failure details
    error_text = response.text[:300] if response.text else "empty response"
    logger.warning(f"sendRichMessage returned {response.status_code}: {error_text}")
    return None
