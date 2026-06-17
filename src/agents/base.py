from abc import ABC, abstractmethod
from typing import Any, Dict, List

class BaseAgent(ABC):
    def __init__(self, name: str, system_prompt: str):
        self.name = name
        self.system_prompt = system_prompt

    @abstractmethod
    async def process(self, user_input: str, history: List[Dict[str, str]]) -> str:
        """Process input and return assistant response."""
        pass

    def get_context(self) -> str:
        return self.system_prompt
