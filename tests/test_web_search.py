import os
import tempfile
import unittest
from unittest.mock import patch, AsyncMock, MagicMock

import httpx

from src import config
from src import database
from src import llm
from src import web_search


class TestRouterParsing(unittest.IsolatedAsyncioTestCase):
    """_route turns the fast classifier output into a query (or None)."""

    @patch("src.web_search.quick_complete", new_callable=AsyncMock)
    async def test_no_means_no_search(self, mock_quick):
        mock_quick.return_value = "NO"
        self.assertIsNone(await web_search._route("привет, как дела"))

    @patch("src.web_search.quick_complete", new_callable=AsyncMock)
    async def test_search_line_is_parsed(self, mock_quick):
        mock_quick.return_value = 'SEARCH: погода в Москве сегодня'
        self.assertEqual(
            await web_search._route("какая погода"), "погода в Москве сегодня"
        )

    @patch("src.web_search.quick_complete", new_callable=AsyncMock)
    async def test_quotes_and_extra_lines_stripped(self, mock_quick):
        mock_quick.return_value = 'SEARCH: "bitcoin price"\nsome noise'
        self.assertEqual(await web_search._route("цена битка"), "bitcoin price")

    @patch("src.web_search.quick_complete", new_callable=AsyncMock)
    async def test_router_failure_is_no_search(self, mock_quick):
        mock_quick.return_value = None
        self.assertIsNone(await web_search._route("что-то"))

    @patch("src.web_search.quick_complete", new_callable=AsyncMock)
    async def test_malformed_output_is_no_search(self, mock_quick):
        mock_quick.return_value = "maybe you should look it up"
        self.assertIsNone(await web_search._route("что-то"))


class TestTavilyClient(unittest.IsolatedAsyncioTestCase):
    """_tavily parses results on 200 and fails open ([]) on anything else."""

    def setUp(self):
        self._orig_key = config.TAVILY_API_KEY
        config.TAVILY_API_KEY = "test-key"

    def tearDown(self):
        config.TAVILY_API_KEY = self._orig_key

    @patch("httpx.AsyncClient.post")
    async def test_parses_results(self, mock_post):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {"title": "T1", "url": "https://a.com/x", "content": "snippet 1"},
                {"title": "T2", "url": "https://b.com/y", "content": "snippet 2"},
                {"title": "T3", "url": "https://c.com/z", "content": ""},  # dropped
            ]
        }
        mock_post.return_value = mock_response

        results = await web_search._tavily("query")
        self.assertEqual(len(results), 2)  # empty-content one dropped
        self.assertEqual(results[0]["url"], "https://a.com/x")
        self.assertEqual(results[0]["content"], "snippet 1")

    @patch("httpx.AsyncClient.post")
    async def test_non_200_returns_empty(self, mock_post):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 429
        mock_response.text = "rate limited"
        mock_post.return_value = mock_response
        self.assertEqual(await web_search._tavily("query"), [])

    @patch("httpx.AsyncClient.post", side_effect=httpx.TimeoutException("slow"))
    async def test_exception_returns_empty(self, mock_post):
        self.assertEqual(await web_search._tavily("query"), [])


class TestSourcesFooter(unittest.TestCase):
    def test_single_source(self):
        footer = web_search.sources_footer(["https://www.example.com/page"])
        self.assertEqual(footer, "(источник: [example.com](https://www.example.com/page))")

    def test_two_sources_dedup_and_cap(self):
        footer = web_search.sources_footer(
            [
                "https://a.com/1",
                "https://a.com/1",  # dup
                "https://b.com/2",
                "https://c.com/3",  # capped out (max 2)
            ]
        )
        self.assertIn("источники:", footer)
        self.assertIn("a.com", footer)
        self.assertIn("b.com", footer)
        self.assertNotIn("c.com", footer)

    def test_empty(self):
        self.assertEqual(web_search.sources_footer([]), "")


class TestMaybeSearch(unittest.IsolatedAsyncioTestCase):
    """End-to-end decision logic, each dependency mocked."""

    def setUp(self):
        self._orig_key = config.TAVILY_API_KEY
        self._orig_limit = config.TAVILY_DAILY_LIMIT
        config.TAVILY_API_KEY = "test-key"
        config.TAVILY_DAILY_LIMIT = 50

    def tearDown(self):
        config.TAVILY_API_KEY = self._orig_key
        config.TAVILY_DAILY_LIMIT = self._orig_limit

    async def test_no_key_disables_feature(self):
        config.TAVILY_API_KEY = ""
        self.assertIsNone(await web_search.maybe_search("какие новости сегодня"))

    async def test_trivial_text_skipped(self):
        self.assertIsNone(await web_search.maybe_search("ок"))

    @patch("src.web_search.get_today_search_count", new_callable=AsyncMock)
    async def test_over_budget_skipped(self, mock_count):
        mock_count.return_value = 50  # == limit
        with patch("src.web_search.quick_complete", new_callable=AsyncMock) as mq:
            self.assertIsNone(await web_search.maybe_search("какие новости сегодня"))
            mq.assert_not_called()  # router not even called when over budget

    @patch("src.web_search.get_today_search_count", new_callable=AsyncMock)
    @patch("src.web_search.quick_complete", new_callable=AsyncMock)
    async def test_router_no_means_no_search(self, mock_quick, mock_count):
        mock_count.return_value = 0
        mock_quick.return_value = "NO"
        self.assertIsNone(await web_search.maybe_search("перепиши этот текст красиво"))

    @patch("src.web_search.increment_search_count", new_callable=AsyncMock)
    @patch("src.web_search.get_today_search_count", new_callable=AsyncMock)
    @patch("src.web_search._tavily", new_callable=AsyncMock)
    @patch("src.web_search.quick_complete", new_callable=AsyncMock)
    async def test_empty_results_no_increment(
        self, mock_quick, mock_tavily, mock_count, mock_incr
    ):
        mock_count.return_value = 0
        mock_quick.return_value = "SEARCH: news"
        mock_tavily.return_value = []
        self.assertIsNone(await web_search.maybe_search("какие новости сегодня"))
        mock_incr.assert_not_called()  # nothing logged if search yielded nothing

    @patch("src.web_search.increment_search_count", new_callable=AsyncMock)
    @patch("src.web_search.get_today_search_count", new_callable=AsyncMock)
    @patch("src.web_search._tavily", new_callable=AsyncMock)
    @patch("src.web_search.quick_complete", new_callable=AsyncMock)
    async def test_happy_path(
        self, mock_quick, mock_tavily, mock_count, mock_incr
    ):
        mock_count.return_value = 0
        mock_quick.return_value = "SEARCH: погода москва"
        mock_tavily.return_value = [
            {"title": "Погода", "url": "https://weather.com/m", "content": "+20°C"},
        ]
        result = await web_search.maybe_search("какая сейчас погода в москве")
        self.assertIsNotNone(result)
        self.assertIn("ВЕБ-ПОИСК", result.context)
        self.assertIn("+20°C", result.context)
        self.assertEqual(result.sources, ["https://weather.com/m"])
        mock_incr.assert_awaited_once()  # counter bumped on a real search


class TestSearchCounterDB(unittest.IsolatedAsyncioTestCase):
    """The bot-wide daily counter persists and increments per calendar day."""

    async def asyncSetUp(self):
        self._tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self._tmp, "counter_test.db")
        await database.init_db(self.db_path)

    async def test_starts_at_zero_then_increments(self):
        self.assertEqual(await database.get_today_search_count(self.db_path), 0)
        await database.increment_search_count(self.db_path)
        await database.increment_search_count(self.db_path)
        self.assertEqual(await database.get_today_search_count(self.db_path), 2)


class TestAskLlmWebContext(unittest.IsolatedAsyncioTestCase):
    """ask_llm injects web_context into the provider payload (Gemini path)."""

    def setUp(self):
        self._orig_keys = config.GOOGLE_API_KEYS
        llm.key_pool.cooldowns.clear()

    def tearDown(self):
        config.GOOGLE_API_KEYS = self._orig_keys
        llm.key_pool.cooldowns.clear()

    @patch("httpx.AsyncClient.post")
    async def test_web_context_in_system_instruction(self, mock_post):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]
        }
        mock_post.return_value = mock_response
        config.GOOGLE_API_KEYS = ["dummy_key"]

        await llm.ask_llm(
            mode="general",
            history=[{"role": "user", "content": "вопрос"}],
            web_context="ВЕБ-ПОИСК: свежий факт из интернета",
        )
        payload = mock_post.call_args[1]["json"]
        sys_text = payload["systemInstruction"]["parts"][0]["text"]
        self.assertIn("ВЕБ-ПОИСК: свежий факт из интернета", sys_text)


if __name__ == "__main__":
    unittest.main()
