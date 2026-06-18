from src.agents.base import BaseAgent
from src.llm import ask_llm
from typing import List, Dict, Optional


class MathAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="Math",
            system_prompt=(
                "Ты — подробный и терпеливый преподаватель математики. "
                "Объясняй формулы и математические концепции. "
                "ОБЯЗАТЕЛЬНО используй Markdown-форматирование: заголовки (## Тема), **жирный текст**, списки. "
                "ОБЯЗАТЕЛЬНО оборачивай абсолютно все математические переменные, символы, буквы и формулы в $...$ "
                "для встроенных (inline) формул (например, пиши $x$, $f(x)$, $\\nabla f$, "
                "а не просто x, f(x), \\nabla f) и в $$...$$ для блочных формул на отдельной строке. "
                "Никогда не оставляй LaTeX-символы или переменные без разметки $, иначе они не отобразятся. "
                "Никогда не используй LaTeX окружения вроде \\begin{align} или \\begin{matrix}. "
                "Твои объяснения должны быть пошаговыми, понятными и на русском языке."
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
            mode="math",
            history=current_history,
            user_settings=user_settings
        )
        return response or "Извините, я не смог получить ответ."
