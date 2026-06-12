import logging
import uuid

from telegram import InlineQueryResultArticle, InputTextMessageContent, Update
from telegram.ext import ContextTypes

from llm import ask_llm
from utils import to_telegram_html

logger = logging.getLogger(__name__)


async def inline_query_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handles inline queries (@botname <query>).
    Supports:
    - @botname calc <expr> -> Fast mathematical computation
    - @botname food <product> -> Quick macronutrient breakdown
    - @botname <question> -> Quick general AI search
    """
    inline_query = update.inline_query
    if not inline_query:
        return

    query = inline_query.query.strip()
    results = []

    # 1. Empty query - Show usage instructions
    if not query:
        instructions = [
            ("🧮 Математика", "calc 5 * (10 + 2)", "Быстрые вычисления и формулы"),
            ("🍏 Питание & БЖУ", "food Банан 1 шт", "Калорийность и состав продуктов"),
            (
                "💬 Быстрый вопрос",
                "Почему небо синее?",
                "Быстрый ответ на любой вопрос",
            ),
        ]

        for title, command, desc in instructions:
            results.append(
                InlineQueryResultArticle(
                    id=str(uuid.uuid4()),
                    title=title,
                    description=desc,
                    input_message_content=InputTextMessageContent(
                        message_text=(
                            "Использование inline-режима:\n"
                            f"Отправьте <code>@{context.bot.username} {command}</code>"
                        ),
                        parse_mode="HTML",
                    ),
                )
            )
        await inline_query.answer(results, cache_time=3600)
        return

    # Set default short settings for inline responses to keep them super fast
    inline_settings = {
        "max_length": "short",
        "creativity": "balanced",
        "language": "ru",
    }

    # 2. Mathematical Calculations
    if query.lower().startswith("calc "):
        expression = query[5:].strip()
        if not expression:
            return

        prompt = (
            f"Реши математическое выражение и кратко выведи результат: {expression}"
        )
        response_text, _, _, _, _ = await ask_llm(
            mode="math",
            history=[{"role": "user", "content": prompt}],
            user_settings=inline_settings,
        )

        if response_text:
            formatted = to_telegram_html(response_text)
            results.append(
                InlineQueryResultArticle(
                    id=str(uuid.uuid4()),
                    title=f"🧮 Результат вычисления: {expression}",
                    description=response_text[:100],
                    input_message_content=InputTextMessageContent(
                        message_text=formatted, parse_mode="HTML"
                    ),
                )
            )

    # 3. Nutrition lookup
    elif query.lower().startswith("food "):
        food_item = query[5:].strip()
        if not food_item:
            return

        prompt = (
            f"Определи БЖУ и калорийность для следующего продукта/блюда: {food_item}. "
            "Обязательно составь краткую markdown-таблицу с КБЖУ."
        )
        response_text, _, _, _, _ = await ask_llm(
            mode="nutrition",
            history=[{"role": "user", "content": prompt}],
            user_settings=inline_settings,
        )

        if response_text:
            formatted = to_telegram_html(response_text)
            results.append(
                InlineQueryResultArticle(
                    id=str(uuid.uuid4()),
                    title=f"🍏 КБЖУ для: {food_item}",
                    description="Показать пищевую ценность продукта",
                    input_message_content=InputTextMessageContent(
                        message_text=formatted, parse_mode="HTML"
                    ),
                )
            )

    # 4. General quick question
    else:
        response_text, _, _, _, _ = await ask_llm(
            mode="general",
            history=[{"role": "user", "content": query}],
            user_settings=inline_settings,
        )

        if response_text:
            formatted = to_telegram_html(response_text)
            results.append(
                InlineQueryResultArticle(
                    id=str(uuid.uuid4()),
                    title=f"💬 Быстрый ответ на: {query}",
                    description=response_text[:100],
                    input_message_content=InputTextMessageContent(
                        message_text=formatted, parse_mode="HTML"
                    ),
                )
            )

    # If we couldn't get any response, return a fallback card
    if not results:
        results.append(
            InlineQueryResultArticle(
                id=str(uuid.uuid4()),
                title="⚠️ Ошибка",
                description="Не удалось сгенерировать быстрый ответ. Попробуйте позже.",
                input_message_content=InputTextMessageContent(
                    message_text="Извините, не удалось выполнить запрос в данный момент."
                ),
            )
        )

    await inline_query.answer(results, cache_time=60)
