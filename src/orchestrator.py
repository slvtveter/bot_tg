from src.agents.nutrition_agent import NutritionAgent
from src.agents.math_agent import MathAgent
from src.agents.general_agent import GeneralAgent
from typing import List, Dict, Optional


class Orchestrator:
    def __init__(self):
        self.agents = {
            "nutrition": NutritionAgent(),
            "math": MathAgent(),
            "general": GeneralAgent(),
        }

    async def route_and_process(
        self,
        mode: str,
        user_input: str,
        history: List[Dict[str, str]],
        user_settings: Optional[Dict[str, str]] = None,
        user_id: Optional[int] = None,
    ) -> str:
        agent = self.agents.get(mode, self.agents["general"])
        return await agent.process(user_input, history, user_settings, user_id)
