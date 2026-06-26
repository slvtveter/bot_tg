from typing import Dict, List, Optional

from src.agents.base import AgentResult, BaseAgent
from src.database import log_nutrition_entry
from src.llm import answer_with_web_tool, ask_llm
from src.utils import extract_nutrition_totals
from src.web_search import sources_footer


class GenericAgent(BaseAgent):
    """
    Default agent for every mode. It appends the new user message to the recent
    history and forwards the conversation to the LLM, returning the answer plus
    real telemetry. text is None when every model failed, so the handler can
    show a clean error.

    General mode answers via the web_search tool path (`answer_with_web_tool`):
    the model decides, in the same call where it answers, whether it needs fresh
    web facts — so plain chat costs one LLM call and only a search adds a second.
    On any failure that path returns None and we fall back to the plain ask_llm
    chain (no search), so a reply is never blocked. Other modes use ask_llm
    directly.

    Either way the reply is checked for the trailing [NUTRITION_DATA] marker (the
    smart general prompt emits it only after computing a meal) and logged to the
    diary when present. A web-grounded answer also gets a tiny source footer.
    """

    async def process(
        self,
        user_input: str,
        history: List[Dict[str, str]],
        user_settings: Optional[Dict[str, str]] = None,
        user_id: Optional[int] = None,
    ) -> AgentResult:
        current_history = history + [{"role": "user", "content": user_input}]
        sources: List[str] = []

        if self.mode == "general":
            text, model, p_tok, c_tok, latency, sources = await answer_with_web_tool(
                current_history, user_settings
            )
            if text is None:  # tool path failed → robust fallback, no search
                text, model, p_tok, c_tok, latency = await ask_llm(
                    mode=self.mode,
                    history=current_history,
                    user_settings=user_settings,
                )
                sources = []
        else:
            text, model, p_tok, c_tok, latency = await ask_llm(
                mode=self.mode,
                history=current_history,
                user_settings=user_settings,
            )

        p_tok = p_tok or 0
        c_tok = c_tok or 0
        latency = latency or 0.0

        if not text:
            return (None, model, p_tok, c_tok, latency)

        cleaned_text, totals = extract_nutrition_totals(text)
        if totals and user_id is not None:
            await log_nutrition_entry(
                user_id=user_id,
                calories=totals["calories"],
                protein=totals["protein"],
                fat=totals["fat"],
                carbs=totals["carbs"],
            )

        if sources:
            footer = sources_footer(sources)
            if footer:
                cleaned_text = f"{cleaned_text}\n\n{footer}"

        return (cleaned_text, model, p_tok, c_tok, latency)
