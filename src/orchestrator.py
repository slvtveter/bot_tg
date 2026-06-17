from src.agents.nutrition_agent import NutritionAgent
from src.agents.math_agent import MathAgent
from src.llm import ask_llm
from typing import List, Dict, Optional

class Orchestrator:
    def __init__(self):
        self.agents = {
            "nutrition": NutritionAgent(),
            "math": MathAgent(),
        }

    async def route_and_process(
        self,
        mode: str,
        user_input: str,
        history: List[Dict[str, str]],
        user_settings: Optional[Dict[str, str]] = None
    ) -> str:
        agent = self.agents.get(mode)
        if not agent:
            # Fallback to general if mode is unknown
            return f"Agent {mode} not found. Routing to general..."

        # Here we will eventually delegate to the agent's process method
        # and integrate with the LLM API call
        return await agent.process(user_input, history, user_settings)
