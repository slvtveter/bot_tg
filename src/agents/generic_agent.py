from typing import Dict, List, Optional

from src.agents.base import AgentResult, BaseAgent
from src.database import log_nutrition_entry
from src.llm import ask_llm
from src.utils import extract_nutrition_totals


class GenericAgent(BaseAgent):
    """
    Default agent for every mode: it appends the new user message to the recent
    history and forwards the whole conversation to the LLM for its mode,
    returning the answer plus real telemetry. text is None when every model
    failed, so the handler can show a clean error.

    Nutrition is no longer a separate mode — the smart general prompt analyses
    food on its own. So every reply is checked for the trailing
    [NUTRITION_DATA] marker: the model appends it only when it actually computed
    a meal, so a normal (non-food) turn yields no totals and nothing is logged
    to the diary.
    """

    async def process(
        self,
        user_input: str,
        history: List[Dict[str, str]],
        user_settings: Optional[Dict[str, str]] = None,
        user_id: Optional[int] = None,
        web_context: Optional[str] = None,
    ) -> AgentResult:
        current_history = history + [{"role": "user", "content": user_input}]
        text, model, prompt_tokens, completion_tokens, latency = await ask_llm(
            mode=self.mode,
            history=current_history,
            user_settings=user_settings,
            web_context=web_context,
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
