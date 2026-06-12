import os
import unittest

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
        table_raw = (
            "Показатель | Значение\n"
            "Белки | 0.3 г\n"
            "Жиры | 0.2 г"
        )
        normalized = utils.normalize_markdown_tables(table_raw)
        expected = (
            "| Показатель | Значение |\n"
            "| --- | --- |\n"
            "| Белки | 0.3 г |\n"
            "| Жиры | 0.2 г |"
        )
        self.assertEqual(normalized, expected)

        table_ok = (
            "| Col1 | Col2 |\n"
            "|---|---|\n"
            "| Val1 | Val2 |"
        )
        normalized_ok = utils.normalize_markdown_tables(table_ok)
        expected_ok = (
            "| Col1 | Col2 |\n"
            "| --- | --- |\n"
            "| Val1 | Val2 |"
        )
        self.assertEqual(normalized_ok, expected_ok)

        single_pipe = "Option A | Option B"
        self.assertEqual(utils.normalize_markdown_tables(single_pipe), single_pipe)


class TestDatabaseOperations(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Make sure the test DB is clean for each test
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except OSError:
                pass
        await database.init_db(TEST_DB_PATH)

    async def asyncTearDown(self):
        # Clean up database files
        for suffix in ["", "-wal", "-shm"]:
            path = TEST_DB_PATH + suffix
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
            db_path=TEST_DB_PATH,
        )

        mode = await database.get_user_mode(12345, db_path=TEST_DB_PATH)
        self.assertEqual(mode, "general")  # Default mode

        # Change user mode
        await database.set_user_mode(12345, "math", db_path=TEST_DB_PATH)
        mode = await database.get_user_mode(12345, db_path=TEST_DB_PATH)
        self.assertEqual(mode, "math")

        # Set mode for non-existing user (should upsert)
        await database.set_user_mode(99999, "nutrition", db_path=TEST_DB_PATH)
        mode = await database.get_user_mode(99999, db_path=TEST_DB_PATH)
        self.assertEqual(mode, "nutrition")

    async def test_chat_history(self):
        user_id = 456
        # Create user record first to satisfy FOREIGN KEY constraint
        await database.upsert_user(
            user_id=user_id,
            username="test_history_user",
            first_name="Test",
            last_name="History",
            db_path=TEST_DB_PATH,
        )

        # Log some messages
        await database.log_message(user_id, "user", "Hello bot!", db_path=TEST_DB_PATH)
        await database.log_message(
            user_id, "assistant", "Hello user!", db_path=TEST_DB_PATH
        )
        await database.log_message(
            user_id, "user", "How are you?", db_path=TEST_DB_PATH
        )

        history = await database.get_chat_history(
            user_id, limit=5, db_path=TEST_DB_PATH
        )
        self.assertEqual(len(history), 3)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[0]["content"], "Hello bot!")
        self.assertEqual(history[1]["role"], "assistant")
        self.assertEqual(history[1]["content"], "Hello user!")
        self.assertEqual(history[2]["role"], "user")
        self.assertEqual(history[2]["content"], "How are you?")

        # Clear chat history
        await database.clear_chat_history(user_id, db_path=TEST_DB_PATH)
        history = await database.get_chat_history(
            user_id, limit=5, db_path=TEST_DB_PATH
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
            db_path=TEST_DB_PATH,
        )

        await database.log_usage_stats(
            user_id=user_id,
            model="Gemini-Test",
            prompt_tokens=10,
            completion_tokens=20,
            latency=0.5,
            db_path=TEST_DB_PATH,
        )
        await database.log_usage_stats(
            user_id=user_id,
            model="Gemini-Test",
            prompt_tokens=15,
            completion_tokens=25,
            latency=0.7,
            db_path=TEST_DB_PATH,
        )

        stats = await database.get_usage_stats(user_id=user_id, db_path=TEST_DB_PATH)
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


if __name__ == "__main__":
    unittest.main()
