"""
"Invisible" web search via Tavily (https://tavily.com).

The bot never shows a "search" button or mode. Instead, for each text turn a
lightweight router (one fast LLM call) decides whether the question needs fresh
web facts and, if so, rewrites it into a search query. Tavily results are then
injected into the LLM's system prompt (RAG) so the answer is grounded, and a
tiny "(источник: …)" footer is appended.

Everything here is fail-open: missing key, exhausted daily budget, a trivial
message, a router that says "no", a Tavily error/timeout, or empty results all
return None, and the caller simply answers from the model without web grounding.
Search never blocks or breaks a reply.

Budget: ``config.TAVILY_DAILY_LIMIT`` caps bot-wide searches per calendar day
(stored in the ``search_counter`` table) to stay inside the free monthly quota.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional
from urllib.parse import urlparse

import httpx

from src import config
from src.database import get_today_search_count, increment_search_count
from src.llm import quick_complete

logger = logging.getLogger(__name__)

_TAVILY_URL = "https://api.tavily.com/search"
_TAVILY_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_MAX_RESULTS = 4
_MAX_SNIPPET = 600  # chars per result, to bound prompt tokens
_MIN_TEXT_LEN = 8   # shorter messages (greetings, "ок") never need a search

# Router prompt: a fast yes/no classifier + query rewrite. Kept in English (it's
# internal, model-facing) but it must emit the query in the user's language.
_ROUTER_PROMPT = (
    "You are a routing classifier for an assistant. Decide whether answering the "
    "user's message REQUIRES fresh, real-time or factual information from the web "
    "(current events, news, prices, exchange rates, weather, sports scores, "
    "recently released or updated things, anything 'latest'/'current', or facts "
    "dated after your training cutoff).\n"
    "Do NOT search for: general knowledge, math, coding, writing/rewriting, "
    "translation, opinions, advice, or casual conversation the model already "
    "handles well.\n"
    "Reply with EXACTLY one line:\n"
    "- If no web search is needed: NO\n"
    "- If needed: SEARCH: <a concise web search query, in the user's language>\n\n"
    'User message:\n"""\n{text}\n"""'
)


@dataclass
class SearchContext:
    """Result of a web search: grounding text for the LLM + source URLs."""

    context: str
    sources: List[str]


async def _route(user_text: str) -> Optional[str]:
    """Ask the fast router model whether to search; return the (rewritten) query
    or None. Any failure / "NO" / malformed output → None (no search)."""
    out = await quick_complete(_ROUTER_PROMPT.format(text=user_text[:1000]))
    if not out:
        return None
    out = out.strip()
    if out.upper().startswith("NO"):
        return None
    idx = out.upper().find("SEARCH:")
    if idx == -1:
        return None
    query = out[idx + len("SEARCH:"):].strip()
    query = query.splitlines()[0].strip().strip('"').strip()
    return query or None


async def _tavily(query: str) -> List[Dict[str, str]]:
    """Call Tavily and return a list of {title, url, content}. [] on any error."""
    headers = {"Authorization": f"Bearer {config.TAVILY_API_KEY}"}
    payload = {
        "query": query,
        "search_depth": "basic",   # 1 credit/request (vs 2 for "advanced")
        "max_results": _MAX_RESULTS,
        "include_answer": False,    # we let our own LLM synthesize the answer
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
    """Render search results as grounding text appended to the system prompt."""
    header = (
        "Ниже — свежие результаты веб-поиска по запросу пользователя. Опирайся на "
        "них как на факты, не выдумывай и не противоречь им; если они нерелевантны "
        "— игнорируй."
    )
    blocks = [
        f"[{i}] {r.get('title', '')}\n{r.get('content', '')}"
        for i, r in enumerate(results, 1)
    ]
    return "ВЕБ-ПОИСК:\n" + header + "\n\n" + "\n\n".join(blocks)


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


async def maybe_search(user_text: str) -> Optional[SearchContext]:
    """
    Decide whether to search and, if so, run it. Returns grounding context +
    sources, or None when no search ran (feature off, budget exhausted, trivial
    message, router said no, or no usable results). Never raises.
    """
    if not config.TAVILY_API_KEY:
        return None
    text = (user_text or "").strip()
    if len(text) < _MIN_TEXT_LEN:
        return None
    try:
        # Check the budget BEFORE the router call: if we can't search anyway,
        # don't waste a Gemini call deciding to.
        if await get_today_search_count() >= config.TAVILY_DAILY_LIMIT:
            logger.info("Web search skipped: daily Tavily limit reached")
            return None

        query = await _route(text)
        if not query:
            return None

        results = await _tavily(query)
        if not results:
            return None

        await increment_search_count()
        sources = [r["url"] for r in results if r.get("url")]
        logger.info(f"Web search used: query={query!r}, {len(results)} results")
        return SearchContext(context=_build_context(results), sources=sources)
    except Exception as e:
        logger.warning(f"maybe_search failed, answering without web: {e}")
        return None
