import base64
import html
import logging
from telegram import Update
from telegram.ext import ContextTypes
from database import get_user_mode, log_message, log_usage_stats, get_user_settings
from llm import ask_llm
from utils import to_telegram_html

logger = logging.getLogger(__name__)


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles photo messages. Works in all modes (Nutrition, Math, General) with dynamic vision prompts.
    Downloads the photo, converts to base64, runs LLM Vision,
    logs interaction and stats in DB, and replies using HTML.
    """
    user = update.effective_user
    if (
        not user
        or not update.message
        or not update.message.photo
        or not update.effective_chat
    ):
        return

    user_id = user.id
    mode = await get_user_mode(user_id)

    # Show typing status to user
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action="typing"
    )

    try:
        # Get the highest resolution photo
        photo_file = await update.message.photo[-1].get_file()
        image_bytes = await photo_file.download_as_bytearray()
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")

        caption = update.message.caption or ""

        # Prepare the vision prompt based on the user's active mode
        if mode == "nutrition":
            vision_prompt = (
                "Ты — эксперт по питанию. Проанализируй еду на этой фотографии. "
                "Обязательно составь подробную раскладку по ингредиентам/продуктам и "
                "выведи её СТРОГО в виде markdown-таблицы с колонками:\n"
                "| Блюдо / Продукт | Вес (г) | Белки (г) | Жиры (г) | Углеводы (г) | Калории (ккал) |\n"
                "После таблицы обязательно добавь общую сумму КБЖУ в отдельной строке, "
                "а затем напиши краткий вывод о пользе блюда и рекомендации."
            )
            if caption:
                vision_prompt += (
                    f"\nУчти дополнительный контекст/запрос от пользователя: {caption}"
                )
        elif mode == "math":
            vision_prompt = (
                f"Реши математическую задачу на изображении. Дополнительный запрос: {caption}"
                if caption
                else "Реши математическую задачу на этом изображении пошагово."
            )
        else:  # general mode
            vision_prompt = (
                f"Ответь на запрос касательно этого изображения: {caption}"
                if caption
                else "Подробно опиши, что изображено на этой фотографии."
            )

        # 2. Log user action
        user_log_content = f"[{mode.upper()} PHOTO] {caption}".strip()
        await log_message(user_id=user_id, role="user", content=user_log_content)

        # 3. Retrieve settings
        settings = await get_user_settings(user_id)

        # 4. Call LLM with base64 image and settings
        response_text, model_name, prompt_tokens, completion_tokens, latency = (
            await ask_llm(
                mode=mode,
                history=[],  # Vision is usually a single-turn query
                image_base64=image_base64,
                vision_prompt=vision_prompt,
                user_settings=settings,
            )
        )

        if response_text:
            # 5. Log assistant message
            await log_message(user_id=user_id, role="assistant", content=response_text)

            # 6. Log usage stats
            await log_usage_stats(
                user_id=user_id,
                model=model_name or "unknown",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency=latency,
            )

            # 7. Format and reply
            formatted_response = to_telegram_html(response_text)
            await update.message.reply_html(formatted_response)
        else:
            await update.message.reply_text(
                "⚠️ Не удалось проанализировать изображение. Попробуйте еще раз позже."
            )

    except Exception as e:
        logger.error(f"Error handling photo: {e}")
        escaped_error = html.escape(str(e))
        await update.message.reply_html(
            f"❌ Произошла ошибка при обработке фотографии: <code>{escaped_error}</code>"
        )
