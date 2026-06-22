import os
import time
import unittest
from unittest.mock import patch, AsyncMock, MagicMock
import httpx

from src import config
from src import llm


class TestLLMRouter(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Save original config variables to restore after tests
        self.original_google_keys = config.GOOGLE_API_KEYS
        self.original_openrouter_key = config.OPENROUTER_API_KEY

        # Reset cooldowns in key pool before each test
        llm.key_pool.cooldowns.clear()

    def tearDown(self):
        # Restore original config variables
        config.GOOGLE_API_KEYS = self.original_google_keys
        config.OPENROUTER_API_KEY = self.original_openrouter_key
        llm.key_pool.cooldowns.clear()

    def test_token_estimation_accuracy(self):
        # Test Cyrillic text estimation (2 chars per token)
        cyrillic_text = "Привет"  # 6 characters
        self.assertEqual(llm.estimate_tokens(cyrillic_text), 3)

        # Test Latin text estimation (4 chars per token)
        latin_text = "Hello!"  # 6 characters
        self.assertEqual(llm.estimate_tokens(latin_text), 1)  # max(1, 6//4) => 1

        # Test mix of Cyrillic and Latin
        mixed_text = "Привет Hello"  # 6 Cyrillic, 6 Latin/spaces/symbols => 6/2 + 6/4 = 3 + 1.5 = 4.5 -> 4
        self.assertEqual(llm.estimate_tokens(mixed_text), 4)

        # Test empty text
        self.assertEqual(llm.estimate_tokens(""), 0)

        # Test history token estimation
        history = [
            {"role": "user", "content": "Привет"},
            {"role": "assistant", "content": "Hello!"},
        ]
        system_prompt = "System"  # 6 characters (Latin) -> 1 token
        # Total text = "SystemПриветHello!" => Cyrillic: 6 ("Привет"), Other: 12 ("System", "Hello!")
        # Expected = 6/2 + 12/4 = 3 + 3 = 6
        self.assertEqual(llm.estimate_history_tokens(history, system_prompt), 6)

    @patch("httpx.AsyncClient.post")
    async def test_gemini_direct_success(self, mock_post):
        # Mock Gemini response
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.text = "Success response"
        mock_response.json.return_value = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "Direct Gemini response text"}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 15, "candidatesTokenCount": 25},
        }
        mock_post.return_value = mock_response

        config.GOOGLE_API_KEYS = ["dummy_key"]

        response_text, model_name, prompt_tokens, completion_tokens, latency = (
            await llm.ask_llm(
                mode="general",
                history=[{"role": "user", "content": "Test prompt"}],
            )
        )

        self.assertEqual(response_text, "Direct Gemini response text")
        self.assertIn("Gemini API", model_name)
        self.assertEqual(prompt_tokens, 15)
        self.assertEqual(completion_tokens, 25)
        self.assertGreater(latency, 0.0)

    @patch("httpx.AsyncClient.post")
    async def test_gemini_direct_success_missing_tokens_estimation(self, mock_post):
        # Mock Gemini response without usageMetadata
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.text = "Success response without metadata"
        mock_response.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": "Hello world!"
                            }  # 12 chars (Latin) -> 3 tokens estimated
                        ]
                    },
                    "finishReason": "STOP",
                }
            ]
        }
        mock_post.return_value = mock_response

        config.GOOGLE_API_KEYS = ["dummy_key"]

        response_text, model_name, prompt_tokens, completion_tokens, latency = (
            await llm.ask_llm(
                mode="general",
                history=[{"role": "user", "content": "Hi"}],
            )
        )

        self.assertEqual(response_text, "Hello world!")
        # Prompt tokens are estimated based on history and system prompt
        self.assertGreater(prompt_tokens, 0)
        # 12 characters Latin should estimate to 3 tokens (12 // 4)
        self.assertEqual(completion_tokens, 3)

    @patch("random.shuffle", lambda x: None)  # Prevent shuffle to guarantee order
    @patch("httpx.AsyncClient.post")
    async def test_gemini_key_rotation_on_failure(self, mock_post):
        config.GOOGLE_API_KEYS = ["bad_key", "good_key"]

        # We want to mock post to fail on bad_key, succeed on good_key.
        # The URL contains the key.
        async def side_effect(url, **kwargs):
            resp = MagicMock(spec=httpx.Response)
            if "key=bad_key" in url:
                resp.status_code = 403
                resp.text = "Forbidden key"
            elif "key=good_key" in url:
                resp.status_code = 200
                resp.text = "Success response"
                resp.json.return_value = {
                    "candidates": [
                        {
                            "content": {"parts": [{"text": "Good response"}]},
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 5},
                }
            else:
                resp.status_code = 500
                resp.text = "Unknown key"
            return resp

        mock_post.side_effect = side_effect

        response_text, model_name, prompt_tokens, completion_tokens, latency = (
            await llm.ask_llm(
                mode="general",
                history=[{"role": "user", "content": "Test"}],
            )
        )

        # Verification
        self.assertEqual(response_text, "Good response")
        # Ensure it tried bad_key first (since it's not shuffled), failed it, then tried good_key
        call_urls = [args[0][0] for args in mock_post.call_args_list]
        self.assertTrue(any("key=bad_key" in url for url in call_urls))
        self.assertTrue(any("key=good_key" in url for url in call_urls))

        # Check that bad_key was placed on cooldown (cooldowns are keyed by
        # (key, model) pairs now, so look for any entry for "bad_key")
        bad_key_cooldowns = [v for (k, _m), v in llm.key_pool.cooldowns.items() if k == "bad_key"]
        self.assertTrue(bad_key_cooldowns)
        self.assertGreater(bad_key_cooldowns[0], time.time())
        # good_key should NOT be on cooldown
        good_key_cooldowns = [k for (k, _m) in llm.key_pool.cooldowns if k == "good_key"]
        self.assertFalse(good_key_cooldowns)

    @patch("httpx.AsyncClient.post")
    async def test_gemini_safety_block_fallback_to_openrouter(self, mock_post):
        config.GOOGLE_API_KEYS = ["gemini_key"]
        config.OPENROUTER_API_KEY = "openrouter_key"

        # Mock side effect: Gemini URL returns safety block, OpenRouter URL succeeds
        async def side_effect(url, **kwargs):
            resp = MagicMock(spec=httpx.Response)
            if "generativelanguage" in url:
                resp.status_code = 200
                resp.text = "Safety Blocked Response"
                resp.json.return_value = {"candidates": [{"finishReason": "SAFETY"}]}
            elif "openrouter.ai" in url:
                resp.status_code = 200
                resp.text = "OpenRouter OK Response"
                resp.json.return_value = {
                    "choices": [
                        {"message": {"content": "Clean response from OpenRouter"}}
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 15},
                }
            return resp

        mock_post.side_effect = side_effect

        response_text, model_name, prompt_tokens, completion_tokens, latency = (
            await llm.ask_llm(
                mode="general",
                history=[{"role": "user", "content": "Sensitive question"}],
            )
        )

        # Verification
        self.assertEqual(response_text, "Clean response from OpenRouter")
        self.assertIn("OpenRouter", model_name)
        self.assertEqual(prompt_tokens, 10)
        self.assertEqual(completion_tokens, 15)

        # Ensure gemini_key is NOT put on cooldown since it returned a 200 status code
        # (safety block should not cooldown keys so other users' clean queries can still use Gemini)
        self.assertNotIn("gemini_key", llm.key_pool.cooldowns)

    @patch("httpx.AsyncClient.post")
    async def test_gemini_empty_candidates_fallback_to_openrouter(self, mock_post):
        config.GOOGLE_API_KEYS = ["gemini_key"]
        config.OPENROUTER_API_KEY = "openrouter_key"

        async def side_effect(url, **kwargs):
            resp = MagicMock(spec=httpx.Response)
            if "generativelanguage" in url:
                resp.status_code = 200
                resp.text = "Empty Candidates Response"
                resp.json.return_value = {"candidates": []}
            elif "openrouter.ai" in url:
                resp.status_code = 200
                resp.text = "OpenRouter Response"
                resp.json.return_value = {
                    "choices": [{"message": {"content": "Fallback response"}}]
                }
            return resp

        mock_post.side_effect = side_effect

        response_text, model_name, prompt_tokens, completion_tokens, latency = (
            await llm.ask_llm(
                mode="general",
                history=[{"role": "user", "content": "Empty candidates trigger"}],
            )
        )

        self.assertEqual(response_text, "Fallback response")
        self.assertIn("OpenRouter", model_name)
        self.assertNotIn("gemini_key", llm.key_pool.cooldowns)

    @patch("httpx.AsyncClient.post")
    async def test_gemini_missing_text_fallback_to_openrouter(self, mock_post):
        config.GOOGLE_API_KEYS = ["gemini_key"]
        config.OPENROUTER_API_KEY = "openrouter_key"

        async def side_effect(url, **kwargs):
            resp = MagicMock(spec=httpx.Response)
            if "generativelanguage" in url:
                resp.status_code = 200
                resp.text = "Missing Text Response"
                resp.json.return_value = {
                    "candidates": [{"content": {"parts": []}, "finishReason": "STOP"}]
                }
            elif "openrouter.ai" in url:
                resp.status_code = 200
                resp.text = "OpenRouter Response"
                resp.json.return_value = {
                    "choices": [
                        {"message": {"content": "Text missing fallback success"}}
                    ]
                }
            return resp

        mock_post.side_effect = side_effect

        response_text, model_name, prompt_tokens, completion_tokens, latency = (
            await llm.ask_llm(
                mode="general",
                history=[{"role": "user", "content": "Missing text part trigger"}],
            )
        )

        self.assertEqual(response_text, "Text missing fallback success")
        self.assertIn("OpenRouter", model_name)

    @patch("httpx.AsyncClient.post")
    async def test_gemini_all_fail_openrouter_success(self, mock_post):
        config.GOOGLE_API_KEYS = ["gemini_key1", "gemini_key2"]
        config.OPENROUTER_API_KEY = "openrouter_key"

        async def side_effect(url, **kwargs):
            resp = MagicMock(spec=httpx.Response)
            if "generativelanguage" in url:
                resp.status_code = 500
                resp.text = "Internal Server Error"
            elif "openrouter.ai" in url:
                resp.status_code = 200
                resp.text = "OpenRouter Response"
                resp.json.return_value = {
                    "choices": [{"message": {"content": "OpenRouter output"}}]
                }
            return resp

        mock_post.side_effect = side_effect

        response_text, model_name, prompt_tokens, completion_tokens, latency = (
            await llm.ask_llm(
                mode="general",
                history=[{"role": "user", "content": "Trigger fallback"}],
            )
        )

        self.assertEqual(response_text, "OpenRouter output")
        self.assertIn("OpenRouter", model_name)
        # Verify both keys are placed on cooldown for at least one model
        cooled_keys = {k for (k, _m) in llm.key_pool.cooldowns}
        self.assertIn("gemini_key1", cooled_keys)
        self.assertIn("gemini_key2", cooled_keys)

    @patch("httpx.AsyncClient.post")
    async def test_openrouter_missing_tokens_estimation(self, mock_post):
        config.GOOGLE_API_KEYS = ["gemini_key"]
        config.OPENROUTER_API_KEY = "openrouter_key"

        async def side_effect(url, **kwargs):
            resp = MagicMock(spec=httpx.Response)
            if "generativelanguage" in url:
                resp.status_code = 500
                resp.text = "Failed"
            elif "openrouter.ai" in url:
                resp.status_code = 200
                resp.text = "OpenRouter OK"
                resp.json.return_value = {
                    "choices": [
                        {
                            "message": {
                                "content": "OpenRouter missing tokens!!!!"  # 30 chars -> 7 tokens estimated (30 // 4)
                            }
                        }
                    ]
                }
            return resp

        mock_post.side_effect = side_effect

        response_text, model_name, prompt_tokens, completion_tokens, latency = (
            await llm.ask_llm(
                mode="general",
                history=[{"role": "user", "content": "Estimate token usage"}],
            )
        )

        self.assertEqual(response_text, "OpenRouter missing tokens!!!!")
        self.assertEqual(completion_tokens, 7)

    @patch("httpx.AsyncClient.post")
    async def test_all_fail_returns_none(self, mock_post):
        config.GOOGLE_API_KEYS = ["gemini_key"]
        config.OPENROUTER_API_KEY = "openrouter_key"

        mock_post.side_effect = Exception("Network connection lost")

        response_text, model_name, prompt_tokens, completion_tokens, latency = (
            await llm.ask_llm(
                mode="general",
                history=[{"role": "user", "content": "Fail everything"}],
            )
        )

        self.assertIsNone(response_text)
        self.assertIsNone(model_name)
        self.assertEqual(prompt_tokens, 0)
        self.assertEqual(completion_tokens, 0)
        self.assertGreater(latency, 0.0)

    @patch("httpx.AsyncClient.post")
    async def test_gemini_cooldown_on_invalid_key_400(self, mock_post):
        config.GOOGLE_API_KEYS = ["bad_gemini_key"]
        config.OPENROUTER_API_KEY = ""
        
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 400
        mock_response.text = '{"error": {"message": "API key not valid. Please pass a valid API key."}}'
        mock_post.return_value = mock_response
        
        response_text, model_name, prompt_tokens, completion_tokens, latency = await llm.ask_llm(
            mode="general",
            history=[{"role": "user", "content": "Test text"}],
        )
        
        self.assertIsNone(response_text)
        # An invalid API key is bad for every model, so it's cooled down
        # globally under (key, None).
        self.assertIn(("bad_gemini_key", None), llm.key_pool.cooldowns)

    @patch("httpx.AsyncClient.post")
    async def test_gemini_key_cooldown_is_scoped_per_model(self, mock_post):
        # A key that gets cooled down on one model must still be tried on the
        # NEXT model - cooldowns are per (key, model), not per key globally
        # (see KeyPool docstring in src/llm.py). gemini-2.0-flash-lite is
        # first in the quota-priority model order.
        config.GOOGLE_API_KEYS = ["key_A", "key_B"]
        config.OPENROUTER_API_KEY = ""

        calls = []
        async def side_effect(url, json=None, **kwargs):
            parts = url.split("/models/")
            model_part = parts[1].split(":")[0]
            key_part = url.split("key=")[1]
            calls.append((model_part, key_part))

            resp = MagicMock(spec=httpx.Response)
            if model_part == "gemini-2.0-flash-lite":
                resp.status_code = 403
                resp.text = "Forbidden"
            else:
                resp.status_code = 200
                resp.json.return_value = {
                    "candidates": [{"content": {"parts": [{"text": "Flash Success"}]}, "finishReason": "STOP"}]
                }
            return resp

        mock_post.side_effect = side_effect

        with patch("random.shuffle", lambda x: None):
            response_text, model_name, _, _, _ = await llm.ask_llm(
                mode="general",
                history=[{"role": "user", "content": "Hello"}],
            )

        self.assertEqual(response_text, "Flash Success")
        self.assertIn("gemini-2.0-flash", model_name)

        # Both keys were cooled down on gemini-2.0-flash-lite specifically...
        self.assertIn(("key_A", "gemini-2.0-flash-lite"), llm.key_pool.cooldowns)
        self.assertIn(("key_B", "gemini-2.0-flash-lite"), llm.key_pool.cooldowns)
        # ...but key_A (tried first, shuffle disabled) still succeeded on the
        # very next model instead of being skipped as globally dead.
        self.assertIn(("gemini-2.0-flash", "key_A"), calls)

    @patch("httpx.AsyncClient.post")
    async def test_individual_token_estimation(self, mock_post):
        config.GOOGLE_API_KEYS = ["gemini_key"]
        config.OPENROUTER_API_KEY = ""
        
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "Hello"}]
                    },
                    "finishReason": "STOP"
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 42
            }
        }
        mock_post.return_value = mock_response
        
        response_text, model_name, prompt_tokens, completion_tokens, latency = await llm.ask_llm(
            mode="general",
            history=[{"role": "user", "content": "Test message"}],
        )
        
        self.assertEqual(prompt_tokens, 42)
        self.assertEqual(completion_tokens, 1)


if __name__ == "__main__":
    unittest.main()
