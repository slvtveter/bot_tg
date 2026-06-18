from src.agents.base import BaseAgent
from src.database import log_nutrition_entry
from src.llm import ask_llm
from src.utils import extract_nutrition_totals
from typing import List, Dict, Optional


class NutritionAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="Nutrition",
            system_prompt=(
                "Ты — квалифицированный нутрициолог и эксперт по питанию. "
                "Описывай состав, пользу, вред и калорийность блюд. "
                "ОБЯЗАТЕЛЬНО используй правильный Markdown-формат в ответах. "
                "Для таблиц КБЖУ всегда используй СТРОГИЙ формат markdown-таблиц: "
                "каждая строка таблицы должна начинаться и заканчиваться символом '|', "
                "и обязательно должна содержать строку-разделитель '|---|---|' после строки заголовка. "
                "Пример таблицы:\n"
                "| Продукт | Калории | Белки | Жиры | Углеводы |\n"
                "|:---|---|---|---|---|\n"
                "| Яблоко | 52 | 0.3 | 0.2 | 13.8 |\n\n"
                "Используй заголовки (## Заголовок), **жирный текст** для ключевых данных, "
                "маркированные списки (- пункт). Давай полезные рекомендации по питанию."
            )
        )

    async def process(
        self,
        user_input: str,
        history: List[Dict[str, str]],
        user_settings: Optional[Dict[str, str]] = None,
        user_id: Optional[int] = None,
    ) -> str:
        # Add the current user input to history for the LLM
        current_history = history + [{"role": "user", "content": user_input}]

        response, _, _, _, _ = await ask_llm(
            mode="nutrition",
            history=current_history,
            user_settings=user_settings
        )
        if not response:
            return "Извините, я не смог получить ответ."

        cleaned_text, totals = extract_nutrition_totals(response)
        if totals and user_id is not None:
            await log_nutrition_entry(
                user_id=user_id,
                calories=totals["calories"],
                protein=totals["protein"],
                fat=totals["fat"],
                carbs=totals["carbs"],
            )
        return cleaned_text
