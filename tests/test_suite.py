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
    def test_escape_plain_text(self):
        text = "Hello! User-Name_Test. 1+1=2."
        escaped = utils.escape_plain_text(text)
        # Check that markdown special characters are escaped
        self.assertIn("\\!", escaped)
        self.assertIn("\\-", escaped)
        self.assertIn("\\_", escaped)
        self.assertIn("\\.", escaped)
        self.assertIn("\\+", escaped)
        self.assertIn("\\=", escaped)

    def test_escape_code(self):
        code = "print('hello') \\ `backtick`"
        escaped = utils.escape_code(code)
        self.assertEqual(escaped, "print('hello') \\\\ \\`backtick\\`")

    def test_to_telegram_markdown(self):
        # Test bold escaping
        text = "This is **bold and cool.!**"
        res = utils.to_telegram_markdown(text)
        self.assertEqual(res, "This is *bold and cool\\.\\!*")

        # Test italic escaping
        text = "This is *italic - text!*"
        res = utils.to_telegram_markdown(text)
        self.assertEqual(res, "This is _italic \\- text\\!_")

        # Test code block escaping
        text = "```python\nprint('hello.!')\n```"
        res = utils.to_telegram_markdown(text)
        self.assertEqual(res, "```python\nprint('hello.!')\n\n```")

        # Test inline code
        text = "Run `git status`!"
        res = utils.to_telegram_markdown(text)
        self.assertEqual(res, "Run `git status`\\!")

        # Test links
        text = "[Google Link](https://google.com?q=test_run!)"
        res = utils.to_telegram_markdown(text)
        self.assertEqual(res, "[Google Link](https://google.com?q=test_run!)")

        # Test LaTeX inline math conversion
        text = "Solve $a^2 + b^2 = c^2$ for x."
        res = utils.to_telegram_markdown(text)
        self.assertEqual(res, "Solve `a^2 + b^2 = c^2` for x\\.")


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
