"""
Embedding-based intent router — the "orchestrator brain" that decides WHICH
specialist agent answers a message, before any answer is generated.

It is a nearest-neighbour classifier over a FROZEN embedding model (no training
on our side): every specialist domain is described by a handful of example
phrases; at query time we embed the message and route it to the domain whose
examples are most similar. If nothing is similar enough, we fall back to
`general` (the catch-all chat / nutrition / web-search brain).

Two pluggable embedding backends (swap via config.ROUTER_BACKEND):
  - "local"  → `LocalModel2VecBackend`: a small *static* embedding model
    (model2vec) that runs ON our server, CPU-only, no network, ~0.1 ms/query.
  - "gemini" → `GeminiEmbeddingBackend`: hosted multilingual embeddings via the
    Gemini API — a tiny network call (~80-150 ms), reuses the Google key pool.

Routing math (shared by both backends):
  1. mean-centering: subtract a global "common component" (the mean of all
     example vectors). Embedding spaces are anisotropic — every vector shares a
     big common direction, so raw cosine is ~0.9 for *any* pair and useless.
     Removing it makes cosine discriminative (this single step took our test
     accuracy from ~36% to usable).
  2. L2-normalise, then for each domain take the MAX cosine over its example
     phrases (k-NN, not a blurred centroid average).
  3. if the best score >= ROUTER_THRESHOLD → that domain, else → "general".

Fail-open everywhere: if the backend can't load (missing model, no keys) or a
query errors, `route()` returns "general", so the bot never breaks because of
the router.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol

import numpy as np

from src import config

logger = logging.getLogger(__name__)

# Specialist domains and their example phrases (bilingual — the bot is
# Russian-first but users mix in English). `general` is intentionally NOT here:
# it is the fallback for everything that doesn't clearly match a specialist
# (chit-chat, factual questions, web-search-worthy queries, and nutrition, which
# the general prompt already handles with КБЖУ logging). Add an agent = add an
# entry here plus a matching SYSTEM_PROMPTS entry in src/llm.py.
DOMAIN_EXAMPLES: Dict[str, List[str]] = {
    "nutrition": [
        "сколько калорий в банане", "посчитай кбжу этого блюда",
        "калорийность гречки с курицей", "сколько белка в твороге",
        "что съесть на завтрак при похудении", "сколько углеводов в хлебе",
        "разбери мой рацион по бжу", "это блюдо полезное",
        "how many calories in 100g of rice", "macros of grilled chicken breast",
    ],
    "math": [
        "реши уравнение x^2 - 5x + 6 = 0", "найди производную функции",
        "что такое градиент", "вычисли интеграл", "посчитай предел",
        "докажи теорему", "реши систему уравнений", "чему равна вероятность",
        "solve this integral", "what is the derivative of sin x",
    ],
    "fitness": [
        "составь программу тренировок", "как правильно приседать",
        "сколько подходов для роста мышц", "техника становой тяги",
        "упражнения на пресс дома", "как накачать грудь",
        "чем заменить жим лёжа дома", "программа на массу для новичка",
        "workout plan for weight loss", "how to train legs at home",
    ],
    "writing": [
        "перепиши это письмо вежливее", "сократи текст",
        "исправь грамматику и стиль", "переведи на английский",
        "напиши сопроводительное письмо к резюме", "сделай пост для телеграма",
        "придумай заголовок к статье", "перескажи кратко",
        "rewrite this email to sound professional", "translate this to spanish",
    ],
    "code": [
        "напиши функцию на python", "почему код выдаёт ошибку",
        "исправь баг в этом коде", "что делает этот регэксп",
        "оптимизируй этот sql запрос", "объясни этот код",
        "как развернуть приложение в docker", "напиши скрипт для парсинга сайта",
        "fix this javascript bug", "write a sql query to join tables",
    ],
}

FALLBACK_DOMAIN = "general"


@dataclass
class RouteResult:
    domain: str
    score: float
    backend: str


class EmbeddingBackend(Protocol):
    name: str

    async def embed(self, texts: List[str]) -> np.ndarray:
        """Return an (len(texts), dim) float32 array of embeddings."""
        ...


class LocalModel2VecBackend:
    """Static embedding model loaded from a local path (baked into the Docker
    image) or a HuggingFace id. Pure CPU, no network — runs in our process."""

    name = "local"

    def __init__(self, model_path: str) -> None:
        from model2vec import StaticModel  # imported lazily so the dep is optional

        self._model = StaticModel.from_pretrained(model_path)

    async def embed(self, texts: List[str]) -> np.ndarray:
        # Static-embedding inference is microseconds and pure-CPU, so calling it
        # inline in the event loop is cheaper than offloading to a thread.
        return np.asarray(self._model.encode(texts), dtype=np.float32)


class GeminiEmbeddingBackend:
    """Hosted multilingual embeddings via the Gemini API. A lightweight network
    call; reuses the configured Google API keys. Used when ROUTER_BACKEND=gemini
    (e.g. when a local model can't fit or doesn't understand the language well)."""

    name = "gemini"
    # Tried in order until one returns 200; the winner is cached for the process.
    # text-embedding-004 returned 404 in production (the model id/method wasn't
    # valid for this key's API surface), so probe the current GA model first and
    # fall back. The resolved model is logged.
    _CANDIDATES = ["gemini-embedding-001", "text-embedding-004", "embedding-001"]
    _BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self) -> None:
        if not config.GOOGLE_API_KEYS:
            raise RuntimeError("GeminiEmbeddingBackend needs GOOGLE_API_KEYS")
        self._model: Optional[str] = None  # resolved on first successful embed

    async def embed(self, texts: List[str]) -> np.ndarray:
        import httpx

        # Reuse the process-wide pooled HTTP client (src.llm) instead of
        # creating one per call: the router runs on EVERY message, and a fresh
        # client meant a fresh TLS handshake each time on top of the API call.
        from src.llm import _get_http_client

        key = config.GOOGLE_API_KEYS[0]
        candidates = [self._model] if self._model else self._CANDIDATES
        last_error = "no candidates tried"
        client = _get_http_client()
        for model in candidates:
            payload = {
                "requests": [
                    {"model": f"models/{model}", "content": {"parts": [{"text": t}]}}
                    for t in texts
                ]
            }
            try:
                resp = await client.post(
                    f"{self._BASE}/{model}:batchEmbedContents?key={key}",
                    json=payload,
                    timeout=httpx.Timeout(10.0, connect=5.0),
                )
                if resp.status_code == 404:
                    last_error = f"404 for model {model}"
                    continue
                resp.raise_for_status()
                embs = resp.json().get("embeddings", [])
                vecs = np.asarray([e["values"] for e in embs], dtype=np.float32)
                if self._model is None:
                    self._model = model
                    logger.info("Gemini embedding model resolved to %s", model)
                return vecs
            except Exception as e:
                last_error = f"{model}: {e}"
                continue
        raise RuntimeError(f"no working Gemini embedding model ({last_error})")


def _normalize(v: np.ndarray) -> np.ndarray:
    return v / np.clip(np.linalg.norm(v, axis=-1, keepdims=True), 1e-9, None)


class Router:
    """Picks a specialist domain for a message, or `general`. Lazily builds the
    per-domain example matrices on first use (so model/network init doesn't run
    at import time), and disables itself fail-open on any error."""

    def __init__(
        self,
        backend: Optional[EmbeddingBackend] = None,
        threshold: float = config.ROUTER_THRESHOLD,
    ) -> None:
        self.threshold = threshold
        self.enabled = True
        self._backend = backend
        self._lock = asyncio.Lock()
        self._ready = False
        self._global_mean: Optional[np.ndarray] = None
        self._domain_mats: Dict[str, np.ndarray] = {}

        if self._backend is None:
            try:
                self._backend = _build_backend()
            except Exception as e:  # missing model / keys / dep
                logger.warning("Router disabled (backend init failed): %s", e)
                self.enabled = False

    async def _ensure_ready(self) -> None:
        if self._ready or not self.enabled:
            return
        async with self._lock:
            if self._ready:
                return
            try:
                domains = list(DOMAIN_EXAMPLES.keys())
                flat = [p for d in domains for p in DOMAIN_EXAMPLES[d]]
                vecs = await self._backend.embed(flat)
                self._global_mean = vecs.mean(axis=0)
                i = 0
                for d in domains:
                    n = len(DOMAIN_EXAMPLES[d])
                    block = vecs[i:i + n] - self._global_mean
                    self._domain_mats[d] = _normalize(block)
                    i += n
                self._ready = True
                logger.info(
                    "Router ready (backend=%s, threshold=%.2f, domains=%s)",
                    self._backend.name, self.threshold, domains,
                )
            except Exception as e:
                logger.warning("Router disabled (warm-up failed): %s", e)
                self.enabled = False

    async def warmup(self) -> None:
        """Pre-computes the domain example matrices (one embedding batch). Called
        in the background at startup so the FIRST user message doesn't pay the
        warm-up cost on top of its own routing call. Safe to call anytime."""
        await self._ensure_ready()

    async def route(self, text: str) -> RouteResult:
        """Return the chosen domain. Always returns a valid result; on anything
        unexpected it routes to `general` so a reply is never blocked."""
        if not self.enabled or not text or not text.strip():
            return RouteResult(FALLBACK_DOMAIN, 0.0, "disabled")
        try:
            await self._ensure_ready()
            if not self.enabled:
                return RouteResult(FALLBACK_DOMAIN, 0.0, "disabled")
            q = await self._backend.embed([text])
            q = _normalize(q[0] - self._global_mean)
            best_d, best_s = FALLBACK_DOMAIN, -1.0
            for d, mat in self._domain_mats.items():
                s = float((mat @ q).max())
                if s > best_s:
                    best_d, best_s = d, s
            domain = best_d if best_s >= self.threshold else FALLBACK_DOMAIN
            logger.info("Router: %r -> %s (score=%.3f)", text[:60], domain, best_s)
            return RouteResult(domain, best_s, self._backend.name)
        except Exception as e:
            logger.warning("Router error, falling back to general: %s", e)
            return RouteResult(FALLBACK_DOMAIN, 0.0, "error")


def _build_backend() -> EmbeddingBackend:
    if config.ROUTER_BACKEND == "gemini":
        return GeminiEmbeddingBackend()
    # Require a real local directory (the model baked into the image at build
    # time). This keeps the local backend fully offline — it never tries to
    # resolve a HuggingFace repo at runtime (which would slow/flake CI and cold
    # starts). If the dir is absent, the Router disables itself fail-open.
    import os

    if not os.path.isdir(config.ROUTER_MODEL_PATH):
        raise RuntimeError(
            f"router model dir not found: {config.ROUTER_MODEL_PATH}"
        )
    return LocalModel2VecBackend(config.ROUTER_MODEL_PATH)
