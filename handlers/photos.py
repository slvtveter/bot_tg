import base64
import logging
from telegram import Update
from telegram.ext import ContextTypes
from database import get_user_mode, log_message, log_usage_stats
from llm import ask_llm
from utils import to_telegram_html

logger = logging.getLogger(__name__)

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles photo messages. Works only in 'nutrition' mode.
    Downloads the photo, converts to base64, runs LLM Vision, 
    logs interaction and stats in DB, and replies using HTML.
    """
    user = update.effective_user
    if not user or not update.message or not update.message.photo:
        return

    user_id = user.id
    mode = await get_user_mode(user_id)

    # 1. Check if the active mode is nutrition
    if mode != "nutrition":
        await update.message.reply_html(
            "📸 Отправка фото поддерживается только в режиме 🍏 <b>Питание</b>.\n"
            "Переключите режим с помощью команды /mode."
        )
        return

    # Show typing status to user
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        # Get the highest resolution photo
        photo_file = await update.message.photo[-1].get_file()
        image_bytes = await photo_file.download_as_bytearray()
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")
        
        caption = update.message.caption or ""
        
        # Prepare the vision prompt
        if caption:
            vision_prompt = f"Определи еду на этом фото и ответь на запрос: {caption}"
        else:
            vision_prompt = "Определи еду на этом фото. Опиши кратко: состав, пользу и калорийность блюда."

        # 2. Log user action
        user_log_content = f"[Фото] {caption}".strip()
        await log_message(user_id=user_id, role="user", content=user_log_content)

        # 3. Call LLM with base64 image
        response_text, model_name, prompt_tokens, completion_tokens, latency = await ask_llm(
            mode=mode,
            history=[],  # Vision is usually a single-turn query
            image_base64=image_base64,
            vision_prompt=vision_prompt
        )

        if response_text:
            # 4. Log assistant message
            await log_message(user_id=user_id, role="assistant", content=response_text)

            # 5. Log usage stats
            await log_usage_stats(
                user_id=user_id,
                model=model_name or "unknown",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency=latency
            )

            # 6. Format and reply
            formatted_response = to_telegram_html(response_text)
            await update.message.reply_html(formatted_response)
        else:
            await update.message.reply_html(
                "⚠️ Не удалось проанализировать изображение. Попробуйте еще раз позже."
            )
            
    except Exception as e:
        logger.error(f"Error handling photo: {e}")
        await update.message.reply_html(
            f"❌ Произошла ошибка при обработке фотографии: <code>{e}</code>"
        )
