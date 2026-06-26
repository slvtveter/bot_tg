"""
Web search via Tavily (https://tavily.com), used as a tool the LLM can call.

There's no "search" button or mode. The general-mode answer is generated with a
`web_search` function-calling tool (see `llm.answer_with_web_tool`): the model
itself decides, in the same call where it answers, whether it needs fresh facts.
When it asks to search, `run_search` runs Tavily and the results are fed back so
the model grounds its answer; otherwise nothing here runs at all.

Everything is fail-open: missing key, exhausted daily budget, or a Tavily
error/timeout/empty result all return None, and the model just answers from its
own knowledge.

Budget: `config.TAVILY_DAILY_LIMIT` caps bot-wide searches per calendar day
(stored in the `search_counter` table) to stay inside Tavily's free tier.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional
from urllib.parse import urlparse

import httpx

from src import config
from src.database import get_today_search_count, increment_search_count

logger = logging.getLogger(__name__)

_TAVILY_URL = "https://api.tavily.com/search"
_TAVILY_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_MAX_RESULTS = 4
_MAX_SNIPPET = 600  # chars per result, to bound prompt tokens


@dataclass
class SearchResult:
    """Outcome of a web search: grounding text for the LLM + source URLs."""

    context: str
    sources: List[str]


async def _tavily(query: str) -> List[Dict[str, str]]:
    """Call Tavily and return a list of {title, url, content}. [] on any error."""
    headers = {"Authorization": f"Bearer {config.TAVILY_API_KEY}"}
    payload = {
        "query": query,
        "search_depth": "basic",   # 1 credit/request (vs 2 for "advanced")
        "max_results": _MAX_RESULTS,
        "include_answer": False,    # we let the model synthesize the answer
        "topic": "general",
    }
    try:
        async with httpx.AsyncClient(timeout=_TAVILY_TIMEOUT) as client:
            resp = await client.post(_TAVILY_URL, json=payload, headers=headers)
        if resp.status_code != 200:
            logger.warning(f"Tavily returned {resp.status_code}: {resp.text[:200]}")
            return []
        results = resp.json().get("results", []) or []
    except Exception as e:
        logger.warning(f"Tavily request failed: {e}")
        return []

    out: List[Dict[str, str]] = []
    for r in results[:_MAX_RESULTS]:
        content = (r.get("content") or "").strip()
        if not content:
            continue
        out.append(
            {
                "title": (r.get("title") or "").strip(),
                "url": (r.get("url") or "").strip(),
                "content": content[:_MAX_SNIPPET],
            }
        )
    return out


def _build_context(results: List[Dict[str, str]]) -> str:
    """Render search results as grounding text fed back to the model."""
    header = (
        "Результаты веб-поиска. Опирайся на них как на факты, не выдумывай и не "
        "противоречь им; если нерелевантны — игнорируй."
    )
    blocks = [
        f"[{i}] {r.get('title', '')}\n{r.get('content', '')}"
        for i, r in enumerate(results, 1)
    ]
    return header + "\n\n" + "\n\n".join(blocks)


def sources_footer(sources: List[str]) -> str:
    """A tiny markdown footer like ``(источник: [example.com](https://…))`` for
    the top 1-2 sources. Empty string if there are none."""
    seen: List[str] = []
    for u in sources:
        if u and u not in seen:
            seen.append(u)
        if len(seen) >= 2:
            break
    if not seen:
        return ""
    links = []
    for u in seen:
        domain = urlparse(u).netloc
        if domain.startswith("www."):
            domain = domain[4:]
        links.append(f"[{domain or u}]({u})")
    label = "источник" if len(links) == 1 else "источники"
    return f"({label}: " + ", ".join(links) + ")"


async def run_search(query: str) -> Optional[SearchResult]:
    """
    Execute one web search for the model's tool call: enforce the daily budget,
    hit Tavily, and return grounding text + sources. Returns None when the
    feature is off, the budget is spent, or there are no usable results — the
    caller then lets the model answer without grounding. Never raises.
    """
    if not config.TAVILY_API_KEY:
        return None
    try:
        if await get_today_search_count() >= config.TAVILY_DAILY_LIMIT:
            logger.info("Web search skipped: daily Tavily limit reached")
            return None
        results = await _tavily(query)
        if not results:
            return None
        await increment_search_count()
        sources = [r["url"] for r in results if r.get("url")]
        logger.info(f"Web search used: query={query!r}, {len(results)} results")
        return SearchResult(context=_build_context(results), sources=sources)
    except Exception as e:
        logger.warning(f"run_search failed, answering without web: {e}")
        return None
