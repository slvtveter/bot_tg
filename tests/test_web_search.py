import os
import tempfile
import unittest
from unittest.mock import patch, AsyncMock, MagicMock

import httpx

from src import config
from src import database
from src import llm
from src import web_search
from src.agents.generic_agent import GenericAgent

# Never let a developer's real TURSO_DATABASE_URL (.env) route DB tests at the
# remote production backend — always use the local SQLite test DB.
config.USE_TURSO = False


def _gemini_text_response(text):
    r = MagicMock(spec=httpx.Response)
    r.status_code = 200
    r.json.return_value = {
        "candidates": [
            {"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}
        ],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
    }
    return r


def _gemini_toolcall_response(query):
    r = MagicMock(spec=httpx.Response)
    r.status_code = 200
    r.json.return_value = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"functionCall": {"name": "web_search", "args": {"query": query}}}],
                }
            }
        ],
        "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 3},
    }
    return r


class TestTavilyClient(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._orig_key = config.TAVILY_API_KEY
        config.TAVILY_API_KEY = "test-key"

    def tearDown(self):
        config.TAVILY_API_KEY = self._orig_key

    @patch("httpx.AsyncClient.post")
    async def test_parses_results(self, mock_post):
        r = MagicMock(spec=httpx.Response)
        r.status_code = 200
        r.json.return_value = {
            "results": [
                {"title": "T1", "url": "https://a.com/x", "content": "snippet 1"},
                {"title": "T2", "url": "https://b.com/y", "content": ""},  # dropped
            ]
        }
        mock_post.return_value = r
        results = await web_search._tavily("q")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://a.com/x")

    @patch("httpx.AsyncClient.post")
    async def test_non_200_returns_empty(self, mock_post):
        r = MagicMock(spec=httpx.Response)
        r.status_code = 429
        r.text = "rate limited"
        mock_post.return_value = r
        self.assertEqual(await web_search._tavily("q"), [])

    @patch("httpx.AsyncClient.post", side_effect=httpx.TimeoutException("slow"))
    async def test_exception_returns_empty(self, mock_post):
        self.assertEqual(await web_search._tavily("q"), [])


class TestSourcesFooter(unittest.TestCase):
    def test_single(self):
        self.assertEqual(
            web_search.sources_footer(["https://www.example.com/p"]),
            "(источник: [example.com](https://www.example.com/p))",
        )

    def test_two_dedup_and_cap(self):
        footer = web_search.sources_footer(
            ["https://a.com/1", "https://a.com/1", "https://b.com/2", "https://c.com/3"]
        )
        self.assertIn("источники:", footer)
        self.assertIn("a.com", footer)
        self.assertIn("b.com", footer)
        self.assertNotIn("c.com", footer)

    def test_empty(self):
        self.assertEqual(web_search.sources_footer([]), "")


class TestRunSearch(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._orig_key = config.TAVILY_API_KEY
        self._orig_limit = config.TAVILY_DAILY_LIMIT
        config.TAVILY_API_KEY = "test-key"
        config.TAVILY_DAILY_LIMIT = 50

    def tearDown(self):
        config.TAVILY_API_KEY = self._orig_key
        config.TAVILY_DAILY_LIMIT = self._orig_limit

    async def test_no_key(self):
        config.TAVILY_API_KEY = ""
        self.assertIsNone(await web_search.run_search("news"))

    @patch("src.web_search.get_today_search_count", new_callable=AsyncMock)
    async def test_over_budget(self, mock_count):
        mock_count.return_value = 50
        self.assertIsNone(await web_search.run_search("news"))

    @patch("src.web_search.increment_search_count", new_callable=AsyncMock)
    @patch("src.web_search.get_today_search_count", new_callable=AsyncMock)
    @patch("src.web_search._tavily", new_callable=AsyncMock)
    async def test_empty_no_increment(self, mock_tav, mock_count, mock_incr):
        mock_count.return_value = 0
        mock_tav.return_value = []
        self.assertIsNone(await web_search.run_search("news"))
        mock_incr.assert_not_called()

    @patch("src.web_search.increment_search_count", new_callable=AsyncMock)
    @patch("src.web_search.get_today_search_count", new_callable=AsyncMock)
    @patch("src.web_search._tavily", new_callable=AsyncMock)
    async def test_happy(self, mock_tav, mock_count, mock_incr):
        mock_count.return_value = 0
        mock_tav.return_value = [
            {"title": "Погода", "url": "https://weather.com/m", "content": "+20"}
        ]
        result = await web_search.run_search("погода москва")
        self.assertIsNotNone(result)
        self.assertIn("+20", result.context)
        self.assertEqual(result.sources, ["https://weather.com/m"])
        mock_incr.assert_awaited_once()


class TestGeminiParsing(unittest.TestCase):
    def test_function_call_extracted(self):
        cand = {"content": {"parts": [{"functionCall": {"name": "web_search", "args": {"query": "q"}}}]}}
        self.assertEqual(llm._gemini_function_call(cand), "q")

    def test_text_not_a_function_call(self):
        cand = {"content": {"parts": [{"text": "hello"}]}}
        self.assertIsNone(llm._gemini_function_call(cand))
        self.assertEqual(llm._gemini_text(cand), "hello")


class TestAnswerWithWebTool(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._orig_keys = config.GOOGLE_API_KEYS
        self._orig_tav = config.TAVILY_API_KEY
        self._orig_limit = config.TAVILY_DAILY_LIMIT
        config.GOOGLE_API_KEYS = ["dummy_key"]
        config.TAVILY_DAILY_LIMIT = 50
        llm.key_pool.cooldowns.clear()

    def tearDown(self):
        config.GOOGLE_API_KEYS = self._orig_keys
        config.TAVILY_API_KEY = self._orig_tav
        config.TAVILY_DAILY_LIMIT = self._orig_limit
        llm.key_pool.cooldowns.clear()

    async def test_no_keys(self):
        config.GOOGLE_API_KEYS = []
        result = await llm.answer_with_web_tool([{"role": "user", "content": "hi"}])
        text, sources = result[0], result[5]
        self.assertIsNone(text)
        self.assertEqual(sources, [])

    @patch("httpx.AsyncClient.post")
    async def test_direct_answer_no_search(self, mock_post):
        config.TAVILY_API_KEY = ""  # tool not attached → single call, no search
        mock_post.return_value = _gemini_text_response("Меня зовут Nela.")
        text, model, p, c, latency, sources = await llm.answer_with_web_tool(
            [{"role": "user", "content": "как тебя зовут"}]
        )
        self.assertEqual(text, "Меня зовут Nela.")
        self.assertEqual(sources, [])
        self.assertEqual(mock_post.call_count, 1)  # only one model call

    @patch("src.web_search.run_search", new_callable=AsyncMock)
    @patch("src.database.get_today_search_count", new_callable=AsyncMock)
    @patch("httpx.AsyncClient.post")
    async def test_tool_call_triggers_search(self, mock_post, mock_count, mock_run):
        config.TAVILY_API_KEY = "test-key"
        mock_count.return_value = 0
        mock_run.return_value = web_search.SearchResult(
            context="USD/RUB ~ 79", sources=["https://cbr.ru"]
        )
        # 1st call: model asks to search; 2nd call: model answers with results.
        mock_post.side_effect = [
            _gemini_toolcall_response("курс доллара"),
            _gemini_text_response("Курс примерно 79 рублей."),
        ]
        text, model, p, c, latency, sources = await llm.answer_with_web_tool(
            [{"role": "user", "content": "какой курс доллара"}]
        )
        self.assertEqual(text, "Курс примерно 79 рублей.")
        self.assertEqual(sources, ["https://cbr.ru"])
        self.assertEqual(mock_post.call_count, 2)  # decide + answer
        mock_run.assert_awaited_once_with("курс доллара")

    @patch("httpx.AsyncClient.post")
    async def test_thinking_disabled_in_payload(self, mock_post):
        # general is a fast mode: the tool path must send thinkingBudget=0 for
        # models that support thinking control, just like ask_llm does —
        # thinking left on was costing 5-11s per reply.
        config.TAVILY_API_KEY = ""
        mock_post.return_value = _gemini_text_response("быстрый ответ")
        text, *_ = await llm.answer_with_web_tool(
            [{"role": "user", "content": "привет"}]
        )
        self.assertEqual(text, "быстрый ответ")
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(
            payload["generationConfig"]["thinkingConfig"], {"thinkingBudget": 0}
        )

    @patch("httpx.AsyncClient.post")
    async def test_daily_quota_429_cools_key_per_model(self, mock_post):
        # A quota-exhausted key must be put on a per-model cooldown so the NEXT
        # message skips it, instead of re-hitting the dead key every time.
        config.TAVILY_API_KEY = ""
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 429
        resp.text = "Quota exceeded for metric generate_requests_per_model_per_day (perDay)"
        mock_post.return_value = resp

        text, *_ = await llm.answer_with_web_tool(
            [{"role": "user", "content": "привет"}]
        )
        self.assertIsNone(text)
        for model in llm._TOOL_MODELS:
            self.assertIn(("dummy_key", model), llm.key_pool.cooldowns)
        # Global (key-wide) scope must NOT be cooled — other models stay usable.
        self.assertNotIn(("dummy_key", None), llm.key_pool.cooldowns)

    @patch("httpx.AsyncClient.post")
    async def test_safety_block_falls_through_to_next_model(self, mock_post):
        config.TAVILY_API_KEY = ""
        blocked = MagicMock(spec=httpx.Response)
        blocked.status_code = 200
        blocked.json.return_value = {
            "candidates": [{"finishReason": "SAFETY"}],
        }
        mock_post.side_effect = [blocked, _gemini_text_response("чистый ответ")]

        text, model, *_ = await llm.answer_with_web_tool(
            [{"role": "user", "content": "вопрос"}]
        )
        self.assertEqual(text, "чистый ответ")
        self.assertEqual(mock_post.call_count, 2)
        # A safety block is not the key's fault — no cooldown.
        self.assertEqual(llm.key_pool.cooldowns, {})


class TestGenericAgentGeneral(unittest.IsolatedAsyncioTestCase):
    @patch("src.agents.generic_agent.answer_with_web_tool", new_callable=AsyncMock)
    async def test_general_appends_source_footer(self, mock_tool):
        mock_tool.return_value = ("ответ", "gemini-x", 10, 5, 0.5, ["https://example.com/a"])
        agent = GenericAgent("general", "general")
        text, model, p, c, latency = await agent.process("вопрос", [])
        self.assertIn("ответ", text)
        self.assertIn("(источник: [example.com](https://example.com/a))", text)

    @patch("src.agents.generic_agent.ask_llm", new_callable=AsyncMock)
    @patch("src.agents.generic_agent.answer_with_web_tool", new_callable=AsyncMock)
    async def test_general_falls_back_when_tool_path_fails(self, mock_tool, mock_ask):
        mock_tool.return_value = (None, None, 0, 0, 0.0, [])
        mock_ask.return_value = ("резерв", "gemini-y", 1, 1, 0.1)
        agent = GenericAgent("general", "general")
        text, *_ = await agent.process("вопрос", [])
        self.assertEqual(text, "резерв")
        mock_ask.assert_awaited_once()

    @patch("src.agents.generic_agent.ask_llm", new_callable=AsyncMock)
    @patch("src.agents.generic_agent.answer_with_web_tool", new_callable=AsyncMock)
    async def test_non_general_skips_tool_path(self, mock_tool, mock_ask):
        mock_ask.return_value = ("math answer", "gemini-z", 1, 1, 0.1)
        agent = GenericAgent("math", "math")
        text, *_ = await agent.process("2+2", [])
        self.assertEqual(text, "math answer")
        mock_tool.assert_not_called()


class TestSearchCounterDB(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self._tmp, "counter_test.db")
        await database.init_db(self.db_path)

    async def test_starts_at_zero_then_increments(self):
        self.assertEqual(await database.get_today_search_count(self.db_path), 0)
        await database.increment_search_count(self.db_path)
        await database.increment_search_count(self.db_path)
        self.assertEqual(await database.get_today_search_count(self.db_path), 2)


if __name__ == "__main__":
    unittest.main()
