from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple

# Every agent returns the answer together with the real telemetry from the LLM
# call, so the handler can log accurate per-request stats instead of
# placeholders: (text, model_name, prompt_tokens, completion_tokens, latency).
# text is None when the whole fallback chain failed, so the caller can show a
# clean error instead of recording an empty turn.
AgentResult = Tuple[Optional[str], Optional[str], int, int, float]


class BaseAgent(ABC):
    def __init__(self, mode: str, name: str):
        self.mode = mode
        self.name = name

    @abstractmethod
    async def process(
        self,
        user_input: str,
        history: List[Dict[str, str]],
        user_settings: Optional[Dict[str, str]] = None,
        user_id: Optional[int] = None,
        web_context: Optional[str] = None,
    ) -> AgentResult:
        """Process input and return (answer, model, prompt_tokens, completion_tokens, latency).

        ``web_context`` is optional web-search grounding (RAG) injected into the
        LLM's system prompt; None when no search ran for this turn.
        """
        ...
