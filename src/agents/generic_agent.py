from typing import Dict, List, Optional

from src.agents.base import AgentResult, BaseAgent
from src.llm import ask_llm


class GenericAgent(BaseAgent):
    """
    Default agent for any mode without special side effects: it appends the new
    user message to the recent history and forwards the whole conversation to
    the LLM for its mode, returning the answer plus real telemetry. text is None
    when every model failed, so the handler can show a clean error.
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
            mode=self.mode,
            history=current_history,
            user_settings=user_settings,
        )
        return (text, model, prompt_tokens or 0, completion_tokens or 0, latency or 0.0)
