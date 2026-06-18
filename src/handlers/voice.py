import base64
import html
import logging

from telegram import Update
from telegram.ext import ContextTypes

from src.handlers.messages import process_text_message, resolve_group_addressing
from src.llm import ask_llm

logger = logging.getLogger(__name__)

TRANSCRIBE_PROMPT = (
    "Transcribe this voice message verbatim, in the language it was spoken in. "
    "Output ONLY the raw transcription text - no commentary, no quotes, no "
    "translation, no additional remarks."
)


async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles voice messages: downloads the Opus/OGG audio, transcribes it via
    Gemini's native audio understanding, then feeds the transcription through
    the exact same pipeline as a typed text message (so mode routing, history,
    settings and nutrition logging all behave identically to text input).
    """
    user = update.effective_user
    if (
        not user
        or not update.message
        or not update.message.voice
        or not update.effective_chat
    ):
        return

    user_id = user.id
    chat_id = update.effective_chat.id

    # Voice messages have no inline text to scan for a mention before
    # transcription, so in groups we only respond to voice notes sent as a
    # reply to one of the bot's own messages (mirrors text/photo gating).
    should_respond, _ = resolve_group_addressing(update, context, "")
    if not should_respond:
        return

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        voice_file = await update.message.voice.get_file()
        voice_bytes = await voice_file.download_as_bytearray()
        audio_base64 = base64.b64encode(voice_bytes).decode("utf-8")

        transcript, _, _, _, _ = await ask_llm(
            mode="general",
            history=[],
            audio_base64=audio_base64,
            audio_mime_type="audio/ogg",
            vision_prompt=TRANSCRIBE_PROMPT,
        )

        if not transcript or not transcript.strip():
            await update.message.reply_text(
                "⚠️ Не удалось распознать голосовое сообщение. Попробуйте ещё раз."
            )
            return

        await process_text_message(
            bot=context.bot,
            chat_id=chat_id,
            user_id=user_id,
            text=transcript.strip(),
            reply_to_message_id=update.message.message_id,
        )

    except Exception as e:
        logger.error(f"Error handling voice message: {e}")
        escaped_error = html.escape(str(e))
        await update.message.reply_html(
            f"❌ Произошла ошибка при обработке голосового сообщения: <code>{escaped_error}</code>"
        )
