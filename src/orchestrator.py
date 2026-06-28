from typing import Dict, List, Optional

from src import config
from src.agents.base import AgentResult, BaseAgent
from src.agents.generic_agent import GenericAgent
from src.i18n import DEFAULT_MODE, MODE_KEYS
from src.router import DOMAIN_EXAMPLES, Router


class Orchestrator:
    """
    The orchestrator: an embedding `Router` chooses which specialist agent
    answers each message, then dispatches to that agent. Every agent is a
    `GenericAgent` (general uses the web-search tool path; specialists use their
    focused system prompt). `GenericAgent` also logs parsed macros when a meal
    was analysed.

    Agents exist for `general` + every routable domain (math, fitness, writing,
    code, nutrition) — note `nutrition` is a routing target with its own prompt
    even though it isn't a keyboard mode. When the router is disabled (no model
    present, e.g. in CI), we fall back to the caller's stored `mode`, so the bot
    behaves exactly as before routing existed.
    """

    def __init__(self, router: Optional[Router] = None) -> None:
        # Build one agent per general + MODE_KEYS + every routable domain, so the
        # router can dispatch to any of them (dict.fromkeys dedups, keeps order).
        agent_keys = list(
            dict.fromkeys([DEFAULT_MODE, *MODE_KEYS, *DOMAIN_EXAMPLES.keys()])
        )
        self.agents: Dict[str, BaseAgent] = {
            key: GenericAgent(key, key) for key in agent_keys
        }
        if router is not None:
            self.router = router
        elif config.ROUTER_ENABLED:
            self.router = Router()
        else:
            self.router = None

    async def route_and_process(
        self,
        mode: str,
        user_input: str,
        history: List[Dict[str, str]],
        user_settings: Optional[Dict[str, str]] = None,
        user_id: Optional[int] = None,
    ) -> AgentResult:
        # Embedding router decides the specialist; fall back to the stored mode
        # when routing is off/unavailable (keeps old behaviour, never blocks).
        agent_key = mode
        if self.router is not None and self.router.enabled:
            result = await self.router.route(user_input)
            agent_key = result.domain

        agent = self.agents.get(agent_key) or self.agents[DEFAULT_MODE]
        return await agent.process(user_input, history, user_settings, user_id)
