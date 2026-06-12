import logging
import random
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

import config

logger = logging.getLogger(__name__)

SYSTEM_PROMPTS = {
    "nutrition": (
        "Ты — квалифицированный нутрициолог и эксперт по питанию. "
        "Описывай состав, пользу, вред и калорийность блюд. "
        "ОБЯЗАТЕЛЬНО используй правильный Markdown-формат в ответах. "
        "Для таблиц КБЖУ всегда используй СТРОГИЙ формат markdown-таблиц: "
        "каждая строка таблицы должна начинаться и заканчиваться символом '|', "
        "и обязательно должна содержать строку-разделитель '|---|---|' после строки заголовка. "
        "Пример таблицы:\n"
        "| Продукт | Калории | Белки | Жиры | Углеводы |\n"
        "|:---|---|---|---|---|\n"
        "| Яблоко | 52 | 0.3 | 0.2 | 13.8 |\n\n"
        "Используй заголовки (## Заголовок), **жирный текст** для ключевых данных, "
        "маркированные списки (- пункт). Давай полезные рекомендации по питанию."
    ),
    "math": (
        "Ты — подробный и терпеливый преподаватель математики. Объясняй формулы и математические концепции. "
        "ОБЯЗАТЕЛЬНО используй Markdown-форматирование: заголовки (## Тема), **жирный текст**, списки. "
        "ОБЯЗАТЕЛЬНО оборачивай абсолютно все математические переменные, символы, буквы и формулы в $...$ "
        "для встроенных (inline) формул (например, пиши $x$, $f(x)$, $\\nabla f$, а не просто x, f(x), \\nabla f) "
        "и в $$...$$ для блочных формул на отдельной строке. "
        "Никогда не оставляй LaTeX-символы или переменные без разметки $, иначе они не отобразятся. "
        "Никогда не используй LaTeX окружения вроде \\begin{align} или \\begin{matrix}. "
        "Твои объяснения должны быть пошаговыми, понятными и на русском языке."
    ),
    "general": (
        "Ты — вежливый, структурированный и полезный ИИ-ассистент. Отвечай четко, по делу и в структурированной форме. "
        "ОБЯЗАТЕЛЬНО используй Markdown-форматирование: заголовки (## Тема), **жирный текст** для важного, "
        "маркированные списки (- пункт), `код`, таблицы где уместно (всегда используй правильный "
        "markdown-формат с символами '|' по бокам и строкой-разделителем '|---|---|'). "
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

    # Detect if user explicitly requested a brief response in their latest text message
    is_explicit_short = False
    if history:
        user_msgs = [m for m in history if m.get("role") == "user"]
        if user_msgs:
            last_user_text = user_msgs[-1].get("content", "").lower()
            if any(
                w in last_user_text
                for w in [
                    "кратко",
                    "коротко",
                    "сжато",
                    "по-быстрому",
                    "brief",
                    "short",
                    "summarize",
                ]
            ):
                is_explicit_short = True

    # For vision prompt, check if the user's caption contains briefness keywords
    if vision_prompt and "пользователя:" in vision_prompt:
        user_part = vision_prompt.split("пользователя:")[-1].lower()
        if any(
            w in user_part
            for w in [
                "кратко",
                "коротко",
                "сжато",
                "по-быстрому",
                "brief",
                "short",
                "summarize",
            ]
        ):
            is_explicit_short = True

    if is_explicit_short:
        max_length = "short"

    # Map creativity to temperature
    temp_map = {"strict": 0.1, "balanced": 0.4, "creative": 0.9}
    temperature = temp_map.get(creativity, 0.4)

    # Map max_length to maxOutputTokens
    tokens_map = {"short": 600, "medium": 1500, "long": 3000}
    max_tokens = tokens_map.get(max_length, 1500)

    # Ensure nutrition mode has enough tokens to avoid truncating tables (unless short length is requested)
    if mode == "nutrition" and max_length != "short":
        max_tokens = max(max_tokens, 1500)

    # Construct the appropriate system prompt with language instruction
    system_prompt = SYSTEM_PROMPTS.get(mode, SYSTEM_PROMPTS["general"])
    if language == "en":
        system_prompt += "\nIMPORTANT: You MUST reply in English language only."
    else:
        system_prompt += "\nВАЖНО: Вы ДОЛЖНЫ отвечать только на русском языке."

    # Append length constraint instructions to the system prompt
    if max_length == "short":
        if language == "en":
            system_prompt += (
                "\nIMPORTANT: Reply as BRIEF, CONCISE and SHORT as possible. "
                "Avoid long intros, greetings, detailed explanations, general reasoning and conclusions. "
                "Only output the core answer. "
                "If analyzing food (nutrition mode), provide only the essentials: "
                "a very short nutrition table and a brief recommendation in 1-2 sentences."
            )
        else:
            system_prompt += (
                "\nВАЖНО: Отвечай максимально КРАТКО, КОНЦИЗНЫМ и СЖАТЫМ текстом. "
                "Избегай длинных вступлений, приветствий, подробных объяснений, общих рассуждений и выводов. "
                "Только самая суть вопроса. "
                "Если анализируешь еду (режим питания), предоставь только самое главное: "
                "очень короткую таблицу КБЖУ и краткий вывод/рекомендацию в 1-2 предложениях."
            )
    elif max_length == "long":
        if language == "en":
            system_prompt += (
                "\nIMPORTANT: Provide a very detailed, comprehensive and in-depth answer, "
                "covering all nuances, reasons, consequences and recommendations."
            )
        else:
            system_prompt += (
                "\nВАЖНО: Давай максимально подробный, развернутый и глубокий ответ, "
                "детально описывая все нюансы, причины, последствия и рекомендации."
            )
    else:  # medium
        if language == "en":
            system_prompt += "\nIMPORTANT: Answer in a moderate, balanced length."
        else:
            system_prompt += "\nВАЖНО: Отвечай в умеренном, сбалансированном объеме."

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
    if key_pool.get_active_keys():
        async with httpx.AsyncClient(timeout=30.0) as client:
            for model in direct_models:
                active_keys = key_pool.get_active_keys()
                if not active_keys:
                    continue
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

                        # Log payload details for debugging (hiding base64 data)
                        payload_log = payload.copy()
                        if image_base64 and "contents" in payload_log:
                            payload_log["contents"] = [
                                {
                                    "parts": [
                                        (
                                            p
                                            if "inlineData" not in p
                                            else {
                                                "inlineData": {
                                                    "mimeType": p["inlineData"][
                                                        "mimeType"
                                                    ],
                                                    "data": "<base64_hidden>",
                                                }
                                            }
                                        )
                                        for p in payload_log["contents"][0]["parts"]
                                    ]
                                }
                            ]
                        logger.info(f"Gemini API request payload: {payload_log}")

                        response = await client.post(url, json=payload)

                        if response.status_code == 200:
                            data = response.json()
                            candidates = data.get("candidates", [])
                            if candidates:
                                # Validate finish reason (check for safety/other blocks)
                                finish_reason = candidates[0].get("finishReason")
                                if finish_reason and finish_reason not in (
                                    "STOP",
                                    "MAX_TOKENS",
                                ):
                                    logger.warning(
                                        f"Direct Gemini ({model}) candidate finishReason is '{finish_reason}', "
                                        "treating as failure to trigger fallback."
                                    )
                                    continue

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
                                    if prompt_tokens is None:
                                        prompt_tokens = (
                                            estimate_history_tokens(
                                                history, system_prompt
                                            )
                                            if not image_base64
                                            else estimate_tokens(prompt_text)
                                        )
                                    if completion_tokens is None:
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
                        if response.status_code in (429, 403, 500, 503) or (
                            response.status_code == 400 and "API key not valid" in response.text
                        ):
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
                            if prompt_tokens is None:
                                prompt_tokens = (
                                    estimate_history_tokens(history, system_prompt)
                                    if not image_base64
                                    else estimate_tokens(prompt_text)
                                )
                            if completion_tokens is None:
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
