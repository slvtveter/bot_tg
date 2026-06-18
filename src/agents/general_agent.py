from src.agents.base import BaseAgent
from src.llm import ask_llm
from typing import List, Dict, Optional


class GeneralAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="General",
            system_prompt=(
                "Ты — вежливый, структурированный и полезный ИИ-ассистент. "
                "Отвечай четко, по делу и в структурированной форме. "
                "ОБЯЗАТЕЛЬНО используй Markdown-форматирование: заголовки (## Тема), **жирный текст** для важного, "
                "маркированные списки (- пункт), `код`, таблицы где уместно (всегда используй правильный "
                "markdown-формат с символами '|' по бокам и строкой-разделителем '|---|---|'). "
                "Помогай пользователю во всем, о чем он тебя попросит."
            )
        )

    async def process(
        self,
        user_input: str,
        history: List[Dict[str, str]],
        user_settings: Optional[Dict[str, str]] = None,
        user_id: Optional[int] = None,
    ) -> str:
        current_history = history + [{"role": "user", "content": user_input}]

        response, _, _, _, _ = await ask_llm(
            mode="general",
            history=current_history,
            user_settings=user_settings
        )
        return response or "Извините, я не смог получить ответ."
