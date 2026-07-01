import asyncio
import base64
import html
import logging

from telegram import Update
from telegram.ext import ContextTypes

from src.database import (
    get_user_context,
    log_message,
    log_nutrition_entry,
    log_usage_stats,
    upsert_user,
)
from src.handlers.messages import resolve_group_addressing
from src.i18n import t
from src.llm import ask_llm
from src.sender import send_response
from src.utils import extract_nutrition_totals

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
    caption = update.message.caption or ""
    should_respond, caption = resolve_group_addressing(update, context, caption)
    if not should_respond:
        return

    # Defensive: guarantee the user row exists before any FK-constrained write.
    await upsert_user(
        user_id=user_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
    )

    # Single combined fetch (mode + settings + language) instead of three reads.
    ctx = await get_user_context(user_id)
    mode = ctx["mode"]
    lang = ctx["language"]

    # Show typing status to user
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action="typing"
    )

    try:
        # Get the highest resolution photo
        photo_file = await update.message.photo[-1].get_file()
        image_bytes = await photo_file.download_as_bytearray()
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")

        # Prepare the vision prompt based on the user's active mode. The default
        # (general) path auto-detects food: a meal photo gets the full КБЖУ
        # nutritionist analysis (table + NUTRITION_DATA marker for the diary),
        # anything else is just described — so the user never has to pick a mode.
        if mode == "math":
            vision_prompt = (
                f"Реши математическую задачу на изображении. Дополнительный запрос: {caption}"
                if caption
                else "Реши математическую задачу на этом изображении пошагово."
            )
        elif mode == "fitness":
            base = (
                "На фото может быть тренажёр, упражнение или человек в движении. "
                "Определи, что это, разбери технику или назначение и дай практичный "
                "совет тренера."
            )
            vision_prompt = f"{base} Запрос пользователя: {caption}" if caption else base
        elif mode == "writing":
            base = (
                "На фото, скорее всего, текст. Распознай его и помоги по запросу "
                "(переписать, исправить, сократить, перевести). Если запроса нет — "
                "аккуратно распознай текст и приведи его."
            )
            vision_prompt = f"{base} Запрос пользователя: {caption}" if caption else base
        elif mode == "code":
            base = (
                "На фото код или сообщение об ошибке. Распознай его, объясни проблему "
                "и предложи исправление с корректным кодом."
            )
            vision_prompt = f"{base} Запрос пользователя: {caption}" if caption else base
        else:  # general (smart default): food → nutritionist, else describe
            vision_prompt = (
                "Посмотри на фотографию и сначала определи, еда ли это (блюдо, "
                "продукты, приём пищи).\n\n"
                "ЕСЛИ ЭТО ЕДА — действуй как эксперт-нутрициолог: составь подробную "
                "раскладку по ингредиентам/продуктам строго в правильном Markdown. "
                "Пример таблицы:\n\n"
                "## 📊 Таблица КБЖУ\n\n"
                "| Продукт | Вес (г) | Белки (г) | Жиры (г) | Углеводы (г) | Калории (ккал) |\n"
                "|:--------|--------:|----------:|---------:|-------------:|---------------:|\n"
                "| Яблоко  | 180     | 0.5       | 0.4      | 24.8         | 94             |\n\n"
                "ВАЖНО: у таблицы ОБЯЗАТЕЛЬНО должна быть строка-разделитель "
                "|---|---| после заголовка!\n"
                "После таблицы добавь:\n"
                "## 📋 Итого\n"
                "Общую сумму КБЖУ **жирным текстом**.\n"
                "## 💡 Рекомендации\n"
                "Краткий вывод о пользе и рекомендации.\n"
                "В САМОМ КОНЦЕ ответа, на отдельной последней строке, ОБЯЗАТЕЛЬНО "
                "выведи итоговые цифры по всему приёму пищи в строго следующем "
                "техническом формате (без дополнительных слов на этой строке): "
                "[NUTRITION_DATA] calories=ЧИСЛО protein=ЧИСЛО fat=ЧИСЛО carbs=ЧИСЛО\n\n"
                "ЕСЛИ ЭТО НЕ ЕДА — просто ответь на запрос об изображении или "
                "подробно опиши, что на нём, и НЕ добавляй строку [NUTRITION_DATA]."
            )
            if caption:
                vision_prompt += (
                    f"\n\nДополнительный контекст/запрос от пользователя: {caption}"
                )

        # 2. Log user action
        user_log_content = f"[{mode.upper()} PHOTO] {caption}".strip()
        await log_message(user_id=user_id, role="user", content=user_log_content)

        # 3. Settings come from the same combined context fetch above.
        settings = {
            "max_length": ctx["max_length"],
            "creativity": ctx["creativity"],
            "language": ctx["language"],
        }

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

        # Always strip/parse the marker: the model appends it only when it
        # actually analysed a meal (works in the smart general mode now), so a
        # non-food photo simply yields no totals and nothing is logged.
        totals = None
        if response_text:
            response_text, totals = extract_nutrition_totals(response_text)

        if response_text:
            # 5. Send the reply to the user FIRST (rich message with fallback) —
            # the bookkeeping writes below shouldn't add remote-DB round trips
            # to the user's perceived response time.
            await send_response(
                bot=context.bot,
                chat_id=update.effective_chat.id,
                text=response_text,
                reply_to_message_id=update.message.message_id,
            )

            # 6. Log the diary entry (if a meal was analysed), the assistant
            # message and the usage stats — independent writes, in parallel.
            writes = [
                log_message(user_id=user_id, role="assistant", content=response_text),
                log_usage_stats(
                    user_id=user_id,
                    model=model_name or "unknown",
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    latency=latency,
                ),
            ]
            if totals:
                writes.append(
                    log_nutrition_entry(
                        user_id=user_id,
                        calories=totals["calories"],
                        protein=totals["protein"],
                        fat=totals["fat"],
                        carbs=totals["carbs"],
                    )
                )
            await asyncio.gather(*writes)
        else:
            await update.message.reply_text(t("photo_failed", lang))

    except Exception as e:
        logger.error(f"Error handling photo: {e}")
        escaped_error = html.escape(str(e))
        await update.message.reply_html(t("photo_error", lang, e=escaped_error))
