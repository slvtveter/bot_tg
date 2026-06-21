from typing import Dict, List, Optional

from src.agents.base import AgentResult, BaseAgent
from src.agents.generic_agent import GenericAgent
from src.agents.nutrition_agent import NutritionAgent
from src.i18n import DEFAULT_MODE, MODE_KEYS


class Orchestrator:
    """
    Holds one agent per mode (from the central mode registry) and dispatches a
    request to the agent for the user's current mode. Every mode uses
    GenericAgent except nutrition, which also logs parsed macros.
    """

    def __init__(self) -> None:
        self.agents: Dict[str, BaseAgent] = {}
        for mode in MODE_KEYS:
            if mode == "nutrition":
                self.agents[mode] = NutritionAgent(mode, mode)
            else:
                self.agents[mode] = GenericAgent(mode, mode)

    async def route_and_process(
        self,
        mode: str,
        user_input: str,
        history: List[Dict[str, str]],
        user_settings: Optional[Dict[str, str]] = None,
        user_id: Optional[int] = None,
    ) -> AgentResult:
        agent = self.agents.get(mode) or self.agents[DEFAULT_MODE]
        return await agent.process(user_input, history, user_settings, user_id)
