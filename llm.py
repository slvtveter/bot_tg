import re
import time
import random
import logging
from typing import List, Dict, Any, Tuple, Optional
import httpx
import config

logger = logging.getLogger(__name__)

SYSTEM_PROMPTS = {
    "nutrition": (
        "Ты — квалифицированный нутрициолог и эксперт по питанию. "
        "Описывай состав, пользу, вред и калорийность блюд. "
        "Предоставляй структурированные раскладки КБЖУ (включая подробные таблицы, когда это необходимо) "
        "и давай полезные рекомендации по питанию."
    ),
    "math": (
        "Ты — подробный и терпеливый преподаватель математики. Объясняй формулы и математические концепции. "
        "ОБЯЗАТЕЛЬНО оборачивай абсолютно все математические переменные, символы, буквы и формулы в $...$ "
        "для встроенных (inline) формул (например, пиши $x$, $f(x)$, $\\nabla f$, а не просто x, f(x), \\nabla f) "
        "и в $$...$$ для блочных формул на отдельной строке. "
        "Никогда не оставляй LaTeX-символы или переменные без разметки $, иначе они не отобразятся в Telegram. "
        "Никогда не используй LaTeX окружения вроде \\begin{align} или \\begin{matrix}. "
        "Твои объяснения должны быть пошаговыми, понятными и на русском языке."
    ),
    "general": (
        "Ты — вежливый, структурированный и полезный ИИ-ассистент. Отвечай четко, по делу и в структурированной форме. "
        "Помогай пользователю во всем, о чем он тебя попросит."
    ),
}


class KeyPool:
    def __init__(self) -> None:
        self.cooldowns: Dict[str, float] = {}

    def get_active_keys(self) -> List[str]:
        keys = config.GOOGLE_API_KEYS
        now = time.time()
        active = [k for k in keys if self.cooldowns.get(k, 0.0) < now]
        return active if active else keys

    def fail_key(self, key: str, duration: int = 300) -> None:
        self.cooldowns[key] = time.time() + duration


key_pool = KeyPool()


def estimate_tokens(text: str) -> int:
    """
    Estimates token count based on character count.
    Rule: 1 token ~= 4 chars for English/symbols, 2 chars for Russian (Cyrillic).
    """
    if not text:
        return 0
    cyrillic_chars = len(re.findall(r"[а-яА-ЯёЁ]", text))
    total_chars = len(text)
    other_chars = total_chars - cyrillic_chars
    return max(1, int(cyrillic_chars / 2 + other_chars / 4))


def estimate_history_tokens(history: List[Dict[str, str]], system_prompt: str) -> int:
    """
    Estimates prompt token usage based on system prompt and history list.
    """
    total_text = system_prompt
    for msg in history:
        total_text += msg.get("content", "")
    return estimate_tokens(total_text)


def format_history_for_gemini(history: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    gemini_contents = []
    for msg in history:
        role = msg.get("role")
        g_role = "model" if role == "assistant" else "user"

        # Merge consecutive messages of the same role
        if gemini_contents and gemini_contents[-1]["role"] == g_role:
            gemini_contents[-1]["parts"][0]["text"] += "\n" + msg.get("content", "")
        else:
            gemini_contents.append(
                {"role": g_role, "parts": [{"text": msg.get("content", "")}]}
            )

    # Ensure the history starts with a 'user' message
    while gemini_contents and gemini_contents[0]["role"] == "model":
        gemini_contents.pop(0)

    return gemini_contents


async def ask_llm(
    mode: str,
    history: List[Dict[str, str]],
    image_base64: Optional[str] = None,
    vision_prompt: Optional[str] = None,
    user_settings: Optional[Dict[str, str]] = None,
    is_summarizing: bool = False,
) -> Tuple[Optional[str], Optional[str], int, int, float]:
    """
    Queries Gemini API directly with key rotation, falling back to OpenRouter.
    Applies user settings for creativity, response length, and language.
    Features automatic chat memory summarization when prompt tokens exceed 6000.
    Returns: (response_text, model_name, prompt_tokens, completion_tokens, latency_seconds)
    """
    # 1. Extract and map user settings
    creativity = "balanced"
    max_length = "medium"
    language = "ru"
    if user_settings:
        creativity = user_settings.get("creativity", "balanced")
        max_length = user_settings.get("max_length", "medium")
        language = user_settings.get("language", "ru")

    # Map creativity to temperature
    temp_map = {"strict": 0.1, "balanced": 0.4, "creative": 0.9}
    temperature = temp_map.get(creativity, 0.4)

    # Map max_length to maxOutputTokens
    tokens_map = {"short": 500, "medium": 1000, "long": 2500}
    max_tokens = tokens_map.get(max_length, 1000)

    # Ensure nutrition mode has enough tokens to avoid truncating tables
    if mode == "nutrition":
        max_tokens = max(max_tokens, 1000)

    # Construct the appropriate system prompt with language instruction
    system_prompt = SYSTEM_PROMPTS.get(mode, SYSTEM_PROMPTS["general"])
    if language == "en":
        system_prompt += "\nIMPORTANT: You MUST reply in English language only."
    else:
        system_prompt += "\nВАЖНО: Вы ДОЛЖНЫ отвечать только на русском языке."

    start_time = time.time()

    # 2. Chat history summarization if size is excessive (only for text chat, when not already summarizing)
    if not image_base64 and not is_summarizing and history:
        current_tokens = estimate_history_tokens(history, system_prompt)
        if current_tokens > 6000 and len(history) > 10:
            logger.info(
                f"History size {current_tokens} exceeds 6000 tokens. Summarizing oldest 10 messages."
            )
            oldest_10 = history[:10]
            remaining = history[10:]

            # Construct system summarization prompt
            summary_prompt = (
                "Сжато и тезисно обобщи следующий диалог между пользователем (user) и ассистентом (assistant), "
                "сохранив все важные факты, формулы, предпочтения или контекст. Ответь кратко на том же языке:\n\n"
            )
            for msg in oldest_10:
                role_label = (
                    "Пользователь" if msg.get("role") == "user" else "Ассистент"
                )
                summary_prompt += f"{role_label}: {msg.get('content')}\n"

            summary_history = [{"role": "user", "content": summary_prompt}]

            # Query LLM recursively for the summary, passing is_summarizing=True to avoid loops
            summary_text, _, _, _, _ = await ask_llm(
                mode="general",
                history=summary_history,
                user_settings=user_settings,
                is_summarizing=True,
            )

            if summary_text:
                summary_msg = {
                    "role": "system",
                    "content": f"[Предыдущий контекст: {summary_text.strip()}]",
                }
                history = [summary_msg] + remaining
                logger.info(
                    "Successfully summarized history and prepended to message log."
                )

    # Define models to try
    if image_base64:
        # Vision models
        direct_models = ["gemini-2.5-flash"]
        openrouter_models = ["google/gemini-2.5-flash"]
        prompt_text = vision_prompt or "Describe this image."
    else:
        # Text models
        direct_models = ["gemini-2.5-flash-lite", "gemini-2.5-flash"]
        openrouter_models = [
            "google/gemini-2.5-flash-lite",
            "google/gemini-2.5-flash",
            "openai/gpt-oss-120b:free",
            "openai/gpt-oss-20b:free",
        ]

    # --- 1. Direct Gemini API calling with key rotation ---
    active_keys = key_pool.get_active_keys()
    if active_keys:
        async with httpx.AsyncClient(timeout=30.0) as client:
            for model in direct_models:
                shuffled_keys = active_keys.copy()
                random.shuffle(shuffled_keys)

                for key in shuffled_keys:
                    try:
                        logger.info(
                            f"Trying direct Gemini ({model}) with key: {key[:8]}..."
                        )
                        url = (
                            "https://generativelanguage.googleapis.com"
                            f"/v1beta/models/{model}:generateContent?key={key}"
                        )

                        if image_base64:
                            # Construct vision request payload
                            payload = {
                                "contents": [
                                    {
                                        "parts": [
                                            {"text": prompt_text},
                                            {
                                                "inlineData": {
                                                    "mimeType": "image/jpeg",
                                                    "data": image_base64,
                                                }
                                            },
                                        ]
                                    }
                                ],
                                "systemInstruction": {
                                    "parts": [{"text": system_prompt}]
                                },
                                "generationConfig": {
                                    "maxOutputTokens": max_tokens,
                                    "temperature": temperature,
                                },
                            }
                        else:
                            # Construct chat text request payload
                            gemini_contents = format_history_for_gemini(history)
                            if not gemini_contents:
                                continue

                            payload = {
                                "contents": gemini_contents,
                                "systemInstruction": {
                                    "parts": [{"text": system_prompt}]
                                },
                                "generationConfig": {
                                    "maxOutputTokens": max_tokens,
                                    "temperature": temperature,
                                },
                            }

                        response = await client.post(url, json=payload)

                        if response.status_code == 200:
                            data = response.json()
                            candidates = data.get("candidates", [])
                            if candidates:
                                parts = (
                                    candidates[0].get("content", {}).get("parts", [])
                                )
                                if parts and "text" in parts[0]:
                                    text_response = parts[0]["text"]
                                    latency = time.time() - start_time

                                    # Retrieve tokens from usageMetadata
                                    usage_meta = data.get("usageMetadata", {})
                                    prompt_tokens = usage_meta.get("promptTokenCount")
                                    completion_tokens = usage_meta.get(
                                        "candidatesTokenCount"
                                    )

                                    # Estimate if missing
                                    if (
                                        prompt_tokens is None
                                        or completion_tokens is None
                                    ):
                                        prompt_tokens = (
                                            estimate_history_tokens(
                                                history, system_prompt
                                            )
                                            if not image_base64
                                            else estimate_tokens(prompt_text)
                                        )
                                        completion_tokens = estimate_tokens(
                                            text_response
                                        )

                                    logger.info(
                                        f"Direct Gemini success with {model}. Latency: {latency:.2f}s"
                                    )
                                    return (
                                        text_response,
                                        f"Gemini API ({model})",
                                        prompt_tokens,
                                        completion_tokens,
                                        latency,
                                    )

                        logger.warning(
                            f"Direct Gemini ({model}) returned status {response.status_code}: {response.text[:200]}"
                        )
                        if response.status_code in (429, 403, 500, 503):
                            logger.warning(
                                f"Putting key {key[:8]} on cooldown due to status {response.status_code}"
                            )
                            key_pool.fail_key(key)

                    except Exception as e:
                        logger.error(f"Error calling direct Gemini ({model}): {e}")
                        logger.warning(
                            f"Putting key {key[:8]} on cooldown due to exception"
                        )
                        key_pool.fail_key(key)

    # --- 2. Fallback to OpenRouter ---
    if config.OPENROUTER_API_KEY:
        async with httpx.AsyncClient(timeout=30.0) as client:
            for model in openrouter_models:
                try:
                    logger.info(f"Trying OpenRouter fallback ({model})...")
                    url = "https://openrouter.ai/api/v1/chat/completions"
                    headers = {
                        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
                        "Content-Type": "application/json",
                    }

                    if image_base64:
                        # Construct OpenRouter Vision message
                        messages = [
                            {"role": "system", "content": system_prompt},
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": prompt_text},
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": f"data:image/jpeg;base64,{image_base64}"
                                        },
                                    },
                                ],
                            },
                        ]
                    else:
                        # Construct OpenRouter Text messages
                        messages = [
                            {"role": "system", "content": system_prompt}
                        ] + history

                    payload = {
                        "model": model,
                        "messages": messages,
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                    }

                    response = await client.post(url, headers=headers, json=payload)

                    if response.status_code == 200:
                        data = response.json()
                        choices = data.get("choices", [])
                        if choices and choices[0].get("message", {}).get("content"):
                            text_response = choices[0]["message"]["content"]
                            latency = time.time() - start_time

                            # Retrieve tokens from usage metadata
                            usage = data.get("usage", {})
                            prompt_tokens = usage.get("prompt_tokens")
                            completion_tokens = usage.get("completion_tokens")

                            # Estimate if missing
                            if prompt_tokens is None or completion_tokens is None:
                                prompt_tokens = (
                                    estimate_history_tokens(history, system_prompt)
                                    if not image_base64
                                    else estimate_tokens(prompt_text)
                                )
                                completion_tokens = estimate_tokens(text_response)

                            logger.info(
                                f"OpenRouter success with {model}. Latency: {latency:.2f}s"
                            )
                            return (
                                text_response,
                                f"OpenRouter ({model})",
                                prompt_tokens,
                                completion_tokens,
                                latency,
                            )

                    logger.warning(
                        f"OpenRouter ({model}) returned status {response.status_code}: {response.text[:200]}"
                    )
                except Exception as e:
                    logger.error(f"Error calling OpenRouter ({model}): {e}")

    # All attempts failed
    latency = time.time() - start_time
    logger.error("All direct Gemini API and OpenRouter fallback attempts failed.")
    return None, None, 0, 0, latency
