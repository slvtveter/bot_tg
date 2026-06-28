import unittest

import numpy as np

from src.orchestrator import Orchestrator
from src.router import DOMAIN_EXAMPLES, RouteResult, Router

DOMAINS = list(DOMAIN_EXAMPLES)
DIM = len(DOMAINS)
_PHRASE_DOMAIN = {p: d for d in DOMAINS for p in DOMAIN_EXAMPLES[d]}


class FakeBackend:
    """Deterministic stand-in for an embedding model: a known example phrase maps
    to its domain's one-hot vector; anything else maps to an equidistant vector
    (which, after the router's mean-centering, collapses to ~0 → routes general).
    Lets us test the routing math with no model download."""

    name = "fake"

    async def embed(self, texts):
        out = []
        for t in texts:
            d = _PHRASE_DOMAIN.get(t)
            if d is not None:
                v = np.zeros(DIM, dtype=np.float32)
                v[DOMAINS.index(d)] = 1.0
            else:
                v = np.full(DIM, 0.2, dtype=np.float32)
            out.append(v)
        return np.asarray(out, dtype=np.float32)


class RaisingBackend:
    name = "raising"

    async def embed(self, texts):
        raise RuntimeError("backend down")


class TestRouterLogic(unittest.IsolatedAsyncioTestCase):
    async def test_routes_each_specialist_domain(self):
        router = Router(backend=FakeBackend(), threshold=0.55)
        for domain, phrases in DOMAIN_EXAMPLES.items():
            res = await router.route(phrases[0])
            self.assertEqual(res.domain, domain, f"{phrases[0]!r} should route to {domain}")
            self.assertGreaterEqual(res.score, 0.55)

    async def test_unknown_text_falls_back_to_general(self):
        router = Router(backend=FakeBackend(), threshold=0.55)
        res = await router.route("абсолютно случайная фраза ни о чём конкретном")
        self.assertEqual(res.domain, "general")

    async def test_empty_text_is_general(self):
        router = Router(backend=FakeBackend(), threshold=0.55)
        self.assertEqual((await router.route("")).domain, "general")
        self.assertEqual((await router.route("   ")).domain, "general")

    async def test_high_threshold_forces_general(self):
        # No cosine can reach 1.5, so even exact matches fall back.
        router = Router(backend=FakeBackend(), threshold=1.5)
        res = await router.route(DOMAIN_EXAMPLES["code"][0])
        self.assertEqual(res.domain, "general")

    async def test_backend_failure_disables_router_fail_open(self):
        router = Router(backend=RaisingBackend(), threshold=0.55)
        res = await router.route(DOMAIN_EXAMPLES["math"][0])
        self.assertEqual(res.domain, "general")
        self.assertFalse(router.enabled)


class _StubRouter:
    enabled = True

    def __init__(self, domain):
        self._domain = domain

    async def route(self, text):
        return RouteResult(self._domain, 0.9, "stub")


class _RecordingAgent:
    def __init__(self, key):
        self.key = key

    async def process(self, user_input, history, user_settings=None, user_id=None):
        return (f"answer:{self.key}", self.key, 1, 1, 0.1)


class TestOrchestratorRouting(unittest.IsolatedAsyncioTestCase):
    def _orch_with(self, router):
        orch = Orchestrator(router=router)
        orch.agents = {k: _RecordingAgent(k) for k in orch.agents}
        return orch

    async def test_dispatches_to_routed_agent(self):
        orch = self._orch_with(_StubRouter("code"))
        text, model, *_ = await orch.route_and_process("general", "напиши класс", [], None, 1)
        self.assertEqual(model, "code")

    async def test_nutrition_is_a_routable_agent(self):
        orch = self._orch_with(_StubRouter("nutrition"))
        _, model, *_ = await orch.route_and_process("general", "ккал омлета", [], None, 1)
        self.assertEqual(model, "nutrition")

    async def test_unknown_domain_falls_back_to_general(self):
        orch = self._orch_with(_StubRouter("does_not_exist"))
        _, model, *_ = await orch.route_and_process("general", "hi", [], None, 1)
        self.assertEqual(model, "general")

    async def test_disabled_router_uses_stored_mode(self):
        orch = self._orch_with(None)  # ROUTER_ENABLED may be true, but None wins
        orch.router = None
        _, model, *_ = await orch.route_and_process("math", "2+2", [], None, 1)
        self.assertEqual(model, "math")


if __name__ == "__main__":
    unittest.main()
