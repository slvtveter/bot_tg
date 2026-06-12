import os
import unittest
import httpx
from unittest.mock import patch, MagicMock

# Configure temporary test DB path before importing database
TEST_DB_DIR = "/Users/slvtveter/Desktop/PycharmProjects/bot_tg/tests_tmp"
os.makedirs(TEST_DB_DIR, exist_ok=True)
TEST_DB_PATH = os.path.join(TEST_DB_DIR, "test_bot.db")
os.environ["DB_PATH"] = TEST_DB_PATH

# Import codebase modules
import utils
import database
import llm
import config


class TestFormatting(unittest.TestCase):
    def test_html_escaping(self):
        text = "Hello & Welcome! <User> > Test."
        res = utils.to_telegram_html(text)
        self.assertEqual(res, "Hello &amp; Welcome! &lt;User&gt; &gt; Test.")

    def test_basic_formatting(self):
        # Test bold
        self.assertEqual(
            utils.to_telegram_html("This is **bold**"), "This is <b>bold</b>"
        )
        # Test italic
        self.assertEqual(
            utils.to_telegram_html("This is *italic*"), "This is <i>italic</i>"
        )
        # Test underline
        self.assertEqual(
            utils.to_telegram_html("This is __underline__"), "This is <u>underline</u>"
        )
        # Test strikethrough
        self.assertEqual(
            utils.to_telegram_html("This is ~~strike~~"), "This is <s>strike</s>"
        )
        # Test spoiler
        self.assertEqual(
            utils.to_telegram_html("This is ||spoiler||"),
            "This is <tg-spoiler>spoiler</tg-spoiler>",
        )

    def test_code_blocks(self):
        # Inline code
        self.assertEqual(
            utils.to_telegram_html("Run `git status`"), "Run <code>git status</code>"
        )
        # Fenced code block
        text = "```python\nprint(123)\n```"
        self.assertEqual(
            utils.to_telegram_html(text),
            '<pre><code class="language-python">print(123)\n</code></pre>',
        )

    def test_math_blocks(self):
        # Inline math
        self.assertEqual(
            utils.to_telegram_html("Solve $a^2 + b^2 = c^2$"),
            "Solve <code>a^2 + b^2 = c^2</code>",
        )
        # Block math
        self.assertEqual(
            utils.to_telegram_html("$$e = mc^2$$"),
            '<pre><code class="language-math">e = mc^2</code></pre>',
        )

    def test_unordered_lists(self):
        text = "* Item 1\n- Item 2\n+ Item 3"
        self.assertEqual(utils.to_telegram_html(text), "• Item 1\n• Item 2\n• Item 3")

    def test_tables(self):
        table = "| Header 1 | Header 2 |\n|---|---|\n| Val 1 | Val 2 |"
        res = utils.to_telegram_html(table)
        self.assertIn(
            "<pre>Header 1 | Header 2\n---------+---------\nVal 1    | Val 2   </pre>",
            res,
        )

    def test_table_normalization(self):
        table_raw = "Показатель | Значение\n" "Белки | 0.3 г\n" "Жиры | 0.2 г"
        normalized = utils.normalize_markdown_tables(table_raw)
        expected = (
            "| Показатель | Значение |\n"
            "| --- | --- |\n"
            "| Белки | 0.3 г |\n"
            "| Жиры | 0.2 г |"
        )
        self.assertEqual(normalized, expected)

        table_ok = "| Col1 | Col2 |\n" "|---|---|\n" "| Val1 | Val2 |"
        normalized_ok = utils.normalize_markdown_tables(table_ok)
        expected_ok = "| Col1 | Col2 |\n" "| --- | --- |\n" "| Val1 | Val2 |"
        self.assertEqual(normalized_ok, expected_ok)

        single_pipe = "Option A | Option B"
        self.assertEqual(utils.normalize_markdown_tables(single_pipe), single_pipe)


class TestDatabaseOperations(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import uuid

        # Use a completely unique database file name for each test method to prevent locks/WAL residue issues
        self.db_name = f"test_bot_{uuid.uuid4().hex}.db"
        self.db_path = os.path.join(TEST_DB_DIR, self.db_name)
        await database.init_db(self.db_path)

    async def asyncTearDown(self):
        # Clean up database files for this test
        for suffix in ["", "-wal", "-shm"]:
            path = self.db_path + suffix
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    async def test_user_creation_and_mode(self):
        # Test upsert_user
        await database.upsert_user(
            user_id=12345,
            username="testuser",
            first_name="Test",
            last_name="User",
            db_path=self.db_path,
        )

        mode = await database.get_user_mode(12345, db_path=self.db_path)
        self.assertEqual(mode, "general")  # Default mode

        # Change user mode
        await database.set_user_mode(12345, "math", db_path=self.db_path)
        mode = await database.get_user_mode(12345, db_path=self.db_path)
        self.assertEqual(mode, "math")

        # Set mode for non-existing user (should upsert)
        await database.set_user_mode(99999, "nutrition", db_path=self.db_path)
        mode = await database.get_user_mode(99999, db_path=self.db_path)
        self.assertEqual(mode, "nutrition")

    async def test_chat_history(self):
        user_id = 456
        # Create user record first to satisfy FOREIGN KEY constraint
        await database.upsert_user(
            user_id=user_id,
            username="test_history_user",
            first_name="Test",
            last_name="History",
            db_path=self.db_path,
        )

        # Log some messages
        await database.log_message(user_id, "user", "Hello bot!", db_path=self.db_path)
        await database.log_message(
            user_id, "assistant", "Hello user!", db_path=self.db_path
        )
        await database.log_message(
            user_id, "user", "How are you?", db_path=self.db_path
        )

        history = await database.get_chat_history(
            user_id, limit=5, db_path=self.db_path
        )
        self.assertEqual(len(history), 3)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[0]["content"], "Hello bot!")
        self.assertEqual(history[1]["role"], "assistant")
        self.assertEqual(history[1]["content"], "Hello user!")
        self.assertEqual(history[2]["role"], "user")
        self.assertEqual(history[2]["content"], "How are you?")

        # Clear chat history
        await database.clear_chat_history(user_id, db_path=self.db_path)
        history = await database.get_chat_history(
            user_id, limit=5, db_path=self.db_path
        )
        self.assertEqual(len(history), 0)

    async def test_usage_stats(self):
        user_id = 789
        # Create user record first to satisfy FOREIGN KEY constraint
        await database.upsert_user(
            user_id=user_id,
            username="test_stats_user",
            first_name="Test",
            last_name="Stats",
            db_path=self.db_path,
        )

        await database.log_usage_stats(
            user_id=user_id,
            model="Gemini-Test",
            prompt_tokens=10,
            completion_tokens=20,
            latency=0.5,
            db_path=self.db_path,
        )
        await database.log_usage_stats(
            user_id=user_id,
            model="Gemini-Test",
            prompt_tokens=15,
            completion_tokens=25,
            latency=0.7,
            db_path=self.db_path,
        )

        stats = await database.get_usage_stats(user_id=user_id, db_path=self.db_path)
        self.assertEqual(stats["total_requests"], 2)
        self.assertEqual(stats["total_prompt_tokens"], 25)
        self.assertEqual(stats["total_completion_tokens"], 45)
        self.assertEqual(stats["total_tokens"], 70)
        self.assertAlmostEqual(stats["avg_latency"], 0.6)
        self.assertIn("Gemini-Test", stats["model_stats"])
        self.assertEqual(stats["model_stats"]["Gemini-Test"]["requests"], 2)


class TestLLMUtilities(unittest.TestCase):
    def test_estimate_tokens(self):
        text_en = "Hello world!"  # 12 chars -> ~3 tokens
        self.assertGreaterEqual(llm.estimate_tokens(text_en), 1)

        text_ru = "Привет мир!"  # 11 chars -> ~5 tokens
        self.assertGreaterEqual(llm.estimate_tokens(text_ru), 1)

    def test_format_history_for_gemini(self):
        # 1. Test normal alternating history
        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "how are you"},
        ]
        formatted = llm.format_history_for_gemini(history)
        self.assertEqual(len(formatted), 3)
        self.assertEqual(formatted[0]["role"], "user")
        self.assertEqual(formatted[1]["role"], "model")
        self.assertEqual(formatted[2]["role"], "user")

        # 2. Test consecutive user messages (should merge)
        history = [
            {"role": "user", "content": "hello"},
            {"role": "user", "content": "are you there?"},
            {"role": "assistant", "content": "yes"},
        ]
        formatted = llm.format_history_for_gemini(history)
        self.assertEqual(len(formatted), 2)
        self.assertEqual(formatted[0]["role"], "user")
        self.assertEqual(formatted[0]["parts"][0]["text"], "hello\nare you there?")
        self.assertEqual(formatted[1]["role"], "model")

        # 3. Test assistant-first message (should pop/slice model-first role)
        history = [
            {"role": "assistant", "content": "hello user"},
            {"role": "user", "content": "hi bot"},
        ]
        formatted = llm.format_history_for_gemini(history)
        self.assertEqual(len(formatted), 1)
        self.assertEqual(formatted[0]["role"], "user")
        self.assertEqual(formatted[0]["parts"][0]["text"], "hi bot")

    def test_key_pool(self):
        # Inject custom key pool keys
        config.GOOGLE_API_KEYS = ["key1", "key2", "key3"]
        pool = llm.KeyPool()

        active = pool.get_active_keys()
        self.assertEqual(set(active), {"key1", "key2", "key3"})

        # Fail key1
        pool.fail_key("key1", duration=10)
        active = pool.get_active_keys()
        self.assertEqual(set(active), {"key2", "key3"})

        # Fail all keys
        pool.fail_key("key2", duration=10)
        pool.fail_key("key3", duration=10)
        active = pool.get_active_keys()
        # If all keys are on cooldown, return all of them to prevent complete block
        self.assertEqual(set(active), {"key1", "key2", "key3"})


class TestConfig(unittest.TestCase):
    def test_config_validation(self):
        import importlib
        original_env = os.environ.copy()
        
        # Test missing TELEGRAM_BOT_TOKEN
        with patch("os.getenv") as mock_getenv:
            mock_getenv.side_effect = lambda key, default=None: None if key == "TELEGRAM_BOT_TOKEN" else original_env.get(key, default)
            with self.assertRaises(ValueError):
                importlib.reload(config)

        # Test missing LLM keys
        with patch("os.getenv") as mock_getenv:
            def getenv_mock(key, default=None):
                if key == "TELEGRAM_BOT_TOKEN":
                    return "dummy_token"
                if key in ("GOOGLE_API_KEYS", "OPENROUTER_API_KEY"):
                    return default if default is not None else ""
                return original_env.get(key, default)
            mock_getenv.side_effect = getenv_mock
            with self.assertRaises(ValueError):
                importlib.reload(config)
                
        # Reload one final time with original environment to keep config in clean state
        importlib.reload(config)


class TestUtilsAdditional(unittest.TestCase):
    def test_normalize_markdown_tables_empty(self):
        self.assertEqual(utils.normalize_markdown_tables(""), "")
        self.assertEqual(utils.normalize_markdown_tables(None), "")

    def test_normalize_markdown_tables_with_protected_blocks(self):
        text = (
            "Some text here\n"
            "```python\n"
            "| not | a | table | inside | code |\n"
            "```\n"
            "$$x | y$$ and \\[a | b\\]\n"
            "$i | j$ and \\(c | d\\)\n"
            "`inline | code | block`\n"
            "Here is the actual table:\n"
            "Col1 | Col2\n"
            "Val1 | Val2"
        )
        res = utils.normalize_markdown_tables(text)
        self.assertIn("| Col1 | Col2 |", res)
        self.assertIn("`inline | code | block`", res)
        self.assertIn("| not | a | table | inside | code |", res)

    def test_normalize_markdown_tables_borders_and_separators(self):
        # Table where rows don't start/end with pipe
        text = (
            " Col1 | Col2 \n"
            " --- | --- \n"
            " Val1 | Val2 "
        )
        res = utils.normalize_markdown_tables(text)
        self.assertEqual(res, "| Col1 | Col2 |\n| --- | --- |\n| Val1 | Val2 |")

        # Table header starts/ends with pipe, but other lines don't
        text2 = (
            "| Col1 | Col2 |\n"
            " Val1 | Val2 "
        )
        res2 = utils.normalize_markdown_tables(text2)
        self.assertEqual(res2, "| Col1 | Col2 |\n| --- | --- |\n| Val1 | Val2 |")

    def test_format_markdown_tables_edge_cases(self):
        # Empty cells/separator only table
        text = "|---|---|"
        res = utils.to_telegram_html(text)
        self.assertEqual(res, "|---|---|")

        # Row with fewer columns than header
        text_fewer = (
            "| Header 1 | Header 2 |\n"
            "|---|---|\n"
            "| Val 1 |"
        )
        res_fewer = utils.to_telegram_html(text_fewer)
        self.assertIn("Val 1    |         ", res_fewer)

        # Transition in_table from True to False
        text_transition = (
            "| Col1 | Col2 |\n"
            "|---|---|\n"
            "| Val1 | Val2 |\n"
            "Normal line\n"
            "| Col3 | Col4 |\n"
            "|---|---|\n"
            "| Val3 | Val4 |"
        )
        res_transition = utils.to_telegram_html(text_transition)
        self.assertIn("<pre>", res_transition)
        self.assertIn("Normal line", res_transition)

    def test_to_telegram_html_edge_cases(self):
        # None or empty input
        self.assertEqual(utils.to_telegram_html(""), "")
        self.assertEqual(utils.to_telegram_html(None), "")

        # Code block without language class
        code_text = "```\ncode block\n```"
        self.assertEqual(
            utils.to_telegram_html(code_text),
            "<pre><code>code block\n</code></pre>"
        )

        # Headers
        self.assertEqual(utils.to_telegram_html("# Header 1"), "<b>Header 1</b>")
        self.assertEqual(utils.to_telegram_html("## Header 2"), "<b>Header 2</b>")

        # Blockquotes
        self.assertEqual(
            utils.to_telegram_html("> This is a quote"),
            "<blockquote>This is a quote</blockquote>"
        )

        # Links with escaping/unescaping
        self.assertEqual(
            utils.to_telegram_html("[Google](https://google.com?a=1&b=2)"),
            '<a href="https://google.com?a=1&amp;b=2">Google</a>'
        )

        # Nested formatting
        nested = "This is **bold *italic* bold**"
        self.assertEqual(
            utils.to_telegram_html(nested),
            "This is <b>bold <i>italic</i> bold</b>"
        )


class TestDatabaseAdditional(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import uuid
        self.db_name = f"test_bot_{uuid.uuid4().hex}.db"
        self.db_path = os.path.join(TEST_DB_DIR, self.db_name)

    async def asyncTearDown(self):
        for suffix in ["", "-wal", "-shm"]:
            path = self.db_path + suffix
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    async def test_migration_users_missing_columns(self):
        import aiosqlite
        # Create user table missing the settings columns
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT
                )
            """)
            await db.commit()

        # Run init_db which should perform migrations
        await database.init_db(self.db_path)

        # Retrieve table info and verify columns exist
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("PRAGMA table_info(users);") as cursor:
                columns = {col[1] for col in await cursor.fetchall()}
        self.assertTrue({"max_length", "creativity", "language"}.issubset(columns))

    async def test_migration_messages_missing_foreign_keys(self):
        import aiosqlite
        # Create users and messages tables without foreign keys
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY);")
            await db.execute("INSERT INTO users (user_id) VALUES (1);")
            await db.execute("""
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    role TEXT,
                    content TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await db.execute("INSERT INTO messages (user_id, role, content) VALUES (1, 'user', 'hello');")
            await db.commit()

        # Run init_db to trigger migration
        await database.init_db(self.db_path)

        # Verify foreign keys are present now
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("PRAGMA foreign_key_list(messages);") as cursor:
                fks = await cursor.fetchall()
        self.assertTrue(len(fks) > 0)

    async def test_migration_stats_missing_foreign_keys(self):
        import aiosqlite
        # Create users and stats tables without foreign keys
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY);")
            await db.execute("INSERT INTO users (user_id) VALUES (1);")
            await db.execute("""
                CREATE TABLE stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    model TEXT,
                    prompt_tokens INTEGER,
                    completion_tokens INTEGER,
                    latency REAL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await db.execute("INSERT INTO stats (user_id, model, prompt_tokens, completion_tokens, latency) VALUES (1, 'model', 10, 10, 0.1);")
            await db.commit()

        # Run init_db to trigger migration
        await database.init_db(self.db_path)

        # Verify foreign keys are present now
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("PRAGMA foreign_key_list(stats);") as cursor:
                fks = await cursor.fetchall()
        self.assertTrue(len(fks) > 0)

    async def test_invalid_setting_name(self):
        await database.init_db(self.db_path)
        with self.assertRaises(ValueError):
            await database.set_user_setting(123, "invalid_setting", "value", db_path=self.db_path)

    async def test_aggregate_usage_stats(self):
        await database.init_db(self.db_path)
        await database.upsert_user(1, "u1", "f1", "l1", db_path=self.db_path)
        await database.upsert_user(2, "u2", "f2", "l2", db_path=self.db_path)
        await database.log_usage_stats(1, "model1", 10, 20, 0.5, db_path=self.db_path)
        await database.log_usage_stats(2, "model2", 15, 25, 0.7, db_path=self.db_path)

        stats = await database.get_usage_stats(db_path=self.db_path)
        self.assertEqual(stats["total_requests"], 2)
        self.assertEqual(stats["total_prompt_tokens"], 25)
        self.assertEqual(stats["total_completion_tokens"], 45)

    async def test_empty_usage_stats(self):
        await database.init_db(self.db_path)
        stats = await database.get_usage_stats(db_path=self.db_path)
        self.assertEqual(stats["total_requests"], 0)

    @patch("database.get_db_connection")
    async def test_database_exceptions(self, mock_conn):
        mock_conn.side_effect = Exception("Database connection failed")

        with self.assertRaises(Exception):
            await database.init_db(self.db_path)

        with self.assertRaises(Exception):
            await database.upsert_user(1, "u", "f", "l", db_path=self.db_path)

        with self.assertRaises(Exception):
            await database.set_user_mode(1, "mode", db_path=self.db_path)

        mode = await database.get_user_mode(1, db_path=self.db_path)
        self.assertEqual(mode, "general")

        settings = await database.get_user_settings(1, db_path=self.db_path)
        self.assertEqual(settings["max_length"], "medium")

        with self.assertRaises(Exception):
            await database.set_user_setting(1, "language", "en", db_path=self.db_path)

        with self.assertRaises(Exception):
            await database.log_message(1, "user", "msg", db_path=self.db_path)

        with self.assertRaises(Exception):
            await database.log_usage_stats(1, "m", 1, 1, 0.5, db_path=self.db_path)

        stats = await database.get_usage_stats(1, db_path=self.db_path)
        self.assertEqual(stats["total_requests"], 0)

        with self.assertRaises(Exception):
            await database.clear_chat_history(1, db_path=self.db_path)

        history = await database.get_chat_history(1, db_path=self.db_path)
        self.assertEqual(history, [])

    @patch("os.remove")
    async def test_teardown_os_error(self, mock_remove):
        mock_remove.side_effect = OSError("mock error")
        import uuid
        self.db_name = f"test_bot_{uuid.uuid4().hex}.db"
        self.db_path = os.path.join(TEST_DB_DIR, self.db_name)
        with open(self.db_path, "w") as f:
            f.write("")


class TestLLMAdditional(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_google_keys = config.GOOGLE_API_KEYS
        self.original_openrouter_key = config.OPENROUTER_API_KEY
        llm.key_pool.cooldowns.clear()

    def tearDown(self):
        config.GOOGLE_API_KEYS = self.original_google_keys
        config.OPENROUTER_API_KEY = self.original_openrouter_key
        llm.key_pool.cooldowns.clear()

    @patch("httpx.AsyncClient.post")
    async def test_ask_llm_settings_mappings(self, mock_post):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "Response text"}]},
                    "finishReason": "STOP"
                }
            ],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 5}
        }
        mock_post.return_value = mock_response

        config.GOOGLE_API_KEYS = ["dummy_key"]

        settings_list = [
            {"creativity": "strict", "max_length": "short", "language": "en"},
            {"creativity": "creative", "max_length": "long", "language": "ru"},
            {"creativity": "balanced", "max_length": "medium", "language": "en"},
        ]

        for settings in settings_list:
            res = await llm.ask_llm(
                mode="general",
                history=[{"role": "user", "content": "Hi"}],
                user_settings=settings
            )
            self.assertEqual(res[0], "Response text")

    @patch("httpx.AsyncClient.post")
    async def test_ask_llm_explicit_short(self, mock_post):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "Brief response"}]}}]
        }
        mock_post.return_value = mock_response

        config.GOOGLE_API_KEYS = ["dummy_key"]

        # 1. Text message with short keyword
        res = await llm.ask_llm(
            mode="general",
            history=[{"role": "user", "content": "Расскажи кратко про солнце"}]
        )
        self.assertEqual(res[0], "Brief response")

        # 2. Vision prompt with short keyword
        res_vision = await llm.ask_llm(
            mode="general",
            history=[],
            image_base64="dummy_base64",
            vision_prompt="пользователя: расскажи коротко"
        )
        self.assertEqual(res_vision[0], "Brief response")

    @patch("httpx.AsyncClient.post")
    async def test_ask_llm_nutrition_mode_tokens(self, mock_post):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "Nutrition table response"}]}}]
        }
        mock_post.return_value = mock_response

        config.GOOGLE_API_KEYS = ["dummy_key"]

        await llm.ask_llm(
            mode="nutrition",
            history=[{"role": "user", "content": "яблоко"}],
            user_settings={"max_length": "medium"}
        )
        called_payload = mock_post.call_args[1]["json"]
        self.assertGreaterEqual(called_payload["generationConfig"]["maxOutputTokens"], 1500)

    @patch("httpx.AsyncClient.post")
    async def test_ask_llm_history_summarization(self, mock_post):
        mock_resp_summary = MagicMock(spec=httpx.Response)
        mock_resp_summary.status_code = 200
        mock_resp_summary.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "Summary of conversation."}]}}]
        }

        mock_resp_main = MagicMock(spec=httpx.Response)
        mock_resp_main.status_code = 200
        mock_resp_main.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "Final response"}]}}]
        }

        mock_post.side_effect = [mock_resp_summary, mock_resp_main]

        config.GOOGLE_API_KEYS = ["dummy_key"]

        history = []
        for i in range(12):
            role = "user" if i % 2 == 0 else "assistant"
            history.append({"role": role, "content": "Привет! " * 200})

        res = await llm.ask_llm(
            mode="general",
            history=history
        )
        self.assertEqual(res[0], "Final response")
        self.assertEqual(mock_post.call_count, 2)

    @patch("httpx.AsyncClient.post")
    async def test_ask_llm_vision_direct_and_openrouter(self, mock_post):
        mock_resp_gemini = MagicMock(spec=httpx.Response)
        mock_resp_gemini.status_code = 200
        mock_resp_gemini.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "Direct Vision Response"}]}}]
        }
        mock_post.return_value = mock_resp_gemini

        config.GOOGLE_API_KEYS = ["gemini_key"]
        config.OPENROUTER_API_KEY = "openrouter_key"

        res = await llm.ask_llm(
            mode="general",
            history=[],
            image_base64="encodedimagebase64string",
            vision_prompt="Describe this image"
        )
        self.assertEqual(res[0], "Direct Vision Response")
        self.assertIn("inlineData", mock_post.call_args[1]["json"]["contents"][0]["parts"][1])

        mock_post.reset_mock()
        mock_resp_fail = MagicMock(spec=httpx.Response)
        mock_resp_fail.status_code = 500
        mock_resp_fail.text = "Error"
        
        mock_resp_or = MagicMock(spec=httpx.Response)
        mock_resp_or.status_code = 200
        mock_resp_or.json.return_value = {
            "choices": [{"message": {"content": "OpenRouter Vision Response"}}]
        }
        mock_post.side_effect = [mock_resp_fail, mock_resp_or]

        res_or = await llm.ask_llm(
            mode="general",
            history=[],
            image_base64="encodedimagebase64string",
            vision_prompt="Describe this image"
        )
        self.assertEqual(res_or[0], "OpenRouter Vision Response")
        self.assertEqual(mock_post.call_count, 2)

    @patch("httpx.AsyncClient.post")
    async def test_ask_llm_no_google_keys(self, mock_post):
        config.GOOGLE_API_KEYS = []
        config.OPENROUTER_API_KEY = "openrouter_key"

        mock_resp_or = MagicMock(spec=httpx.Response)
        mock_resp_or.status_code = 200
        mock_resp_or.json.return_value = {
            "choices": [{"message": {"content": "Direct OpenRouter Response"}}]
        }
        mock_post.return_value = mock_resp_or

        res = await llm.ask_llm(
            mode="general",
            history=[{"role": "user", "content": "Hi"}]
        )
        self.assertEqual(res[0], "Direct OpenRouter Response")
        self.assertEqual(mock_post.call_count, 1)
        self.assertIn("openrouter.ai", mock_post.call_args[0][0])

    @patch("httpx.AsyncClient.post")
    async def test_ask_llm_empty_gemini_contents(self, mock_post):
        config.GOOGLE_API_KEYS = ["dummy_key"]
        config.OPENROUTER_API_KEY = "openrouter_key"

        mock_resp_or = MagicMock(spec=httpx.Response)
        mock_resp_or.status_code = 200
        mock_resp_or.json.return_value = {
            "choices": [{"message": {"content": "OpenRouter reply"}}]
        }
        mock_post.return_value = mock_resp_or

        res = await llm.ask_llm(
            mode="general",
            history=[{"role": "assistant", "content": "hello"}]
        )
        self.assertEqual(res[0], "OpenRouter reply")
        self.assertEqual(mock_post.call_count, 1)
        self.assertIn("openrouter.ai", mock_post.call_args[0][0])

    @patch("httpx.AsyncClient.post")
    async def test_openrouter_failure_handling(self, mock_post):
        config.GOOGLE_API_KEYS = []
        config.OPENROUTER_API_KEY = "openrouter_key"

        mock_resp_fail = MagicMock(spec=httpx.Response)
        mock_resp_fail.status_code = 500
        mock_resp_fail.text = "Internal Server Error"
        mock_post.return_value = mock_resp_fail

        res = await llm.ask_llm(
            mode="general",
            history=[{"role": "user", "content": "Hi"}]
        )
        self.assertIsNone(res[0])


if __name__ == "__main__":
    unittest.main()
