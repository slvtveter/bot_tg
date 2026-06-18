from abc import ABC, abstractmethod
from typing import Dict, List, Optional


class BaseAgent(ABC):
    def __init__(self, name: str, system_prompt: str):
        self.name = name
        self.system_prompt = system_prompt

    @abstractmethod
    async def process(
        self,
        user_input: str,
        history: List[Dict[str, str]],
        user_settings: Optional[Dict[str, str]] = None,
        user_id: Optional[int] = None,
    ) -> str:
        """Process input and return assistant response."""
        pass

    def get_context(self) -> str:
        return self.system_prompt
