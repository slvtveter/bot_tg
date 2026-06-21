from typing import Dict, List, Optional

from src.agents.base import AgentResult
from src.agents.generic_agent import GenericAgent
from src.database import log_nutrition_entry
from src.llm import ask_llm
from src.utils import extract_nutrition_totals


class NutritionAgent(GenericAgent):
    """
    Nutrition mode behaves like a generic agent but, when the model has
    actually analysed a meal, it parses the trailing [NUTRITION_DATA] marker,
    strips it from the user-visible text and logs the macros so /today and
    /week can report daily/weekly totals.
    """

    async def process(
        self,
        user_input: str,
        history: List[Dict[str, str]],
        user_settings: Optional[Dict[str, str]] = None,
        user_id: Optional[int] = None,
    ) -> AgentResult:
        current_history = history + [{"role": "user", "content": user_input}]
        text, model, prompt_tokens, completion_tokens, latency = await ask_llm(
            mode="nutrition",
            history=current_history,
            user_settings=user_settings,
        )
        prompt_tokens = prompt_tokens or 0
        completion_tokens = completion_tokens or 0
        latency = latency or 0.0

        if not text:
            return (None, model, prompt_tokens, completion_tokens, latency)

        cleaned_text, totals = extract_nutrition_totals(text)
        if totals and user_id is not None:
            await log_nutrition_entry(
                user_id=user_id,
                calories=totals["calories"],
                protein=totals["protein"],
                fat=totals["fat"],
                carbs=totals["carbs"],
            )
        return (cleaned_text, model, prompt_tokens, completion_tokens, latency)
