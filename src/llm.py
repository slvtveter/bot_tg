import logging
import random
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from src import config

logger = logging.getLogger(__name__)

# Shared formatting philosophy, appended to every mode's prompt. This is the
# single most important behavioral rule in the bot: format ADAPTIVELY. Earlier
# prompts ordered the model to always emit markdown tables and heavy headings,
# so even a one-line answer came back looking like a report (the "why is there
# a table for everything?" problem). Structure is now opt-in - used only when
# it genuinely helps the reader - which also makes answers shorter and snappier.
_FORMATTING_PHILOSOPHY = (
    "\n\nОФОРМЛЕНИЕ (важно):\n"
    "- Подстраивай формат под вопрос. На простой вопрос — короткий живой ответ "
    "в 1–3 предложениях, без заголовков и списков. Структуру разворачивай только "
    "когда тема реально сложная и это помогает понять.\n"
    "- Таблицы — ТОЛЬКО когда сравниваешь несколько объектов по нескольким "
    "параметрам или приводишь данные, которые иначе читать неудобно. Не делай "
    "таблицу ради таблицы.\n"
    "- Списки, заголовки и **жирный** — лишь когда они делают ответ понятнее.\n"
    "- Без пустых вступлений и воды («Отличный вопрос!», «Конечно, давайте…»). "
    "Сразу по сути.\n"
    "- Не знаешь или данных не хватает — честно скажи, не выдумывай.\n"
    "- Если используешь markdown-таблицу, делай её строго корректной: каждая "
    "строка начинается и заканчивается '|', после заголовка идёт строка-"
    "разделитель '|---|---|'."
)

SYSTEM_PROMPTS = {
    "general": (
        "Ты — первоклассный ИИ-ассистент: умный, внимательный и по-настоящему полезный. "
        "Сначала пойми, что человеку на самом деле нужно, затем дай точный, понятный и "
        "честный ответ. Рассуждай аккуратно, проверяй логику и факты. Помогаешь с чем "
        "угодно — объяснить, посоветовать, написать, посчитать, разобраться. Общайся "
        "по-человечески и тепло, но без лишней болтовни."
    )
    + _FORMATTING_PHILOSOPHY,
    "nutrition": (
        "Ты — опытный нутрициолог. Помогаешь с питанием: оцениваешь калорийность и БЖУ "
        "блюд, отвечаешь на вопросы о еде, диетах и здоровом рационе простым языком и "
        "даёшь реалистичные практичные советы. "
        "Если человек описал конкретное блюдо или приём пищи с продуктами/количеством — "
        "оцени калории, белки, жиры и углеводы. Когда продуктов несколько, приведи "
        "разбивку таблицей (Продукт | Калории | Белки | Жиры | Углеводы); если продукт "
        "один или вопрос общий — отвечай обычным текстом, без таблицы. Цифры — это "
        "оценка, так и говори, не выдавай их за аптечно точные. Не ставь диагнозов и не "
        "назначай лечение; при заболеваниях советуй обратиться к врачу.\n"
        "ТЕХНИЧЕСКОЕ: ТОЛЬКО если ты реально посчитал калорийность конкретного приёма "
        "пищи, добавь самой последней строкой ответа (без других слов на этой строке) "
        "итог по всему приёму пищи в формате: "
        "[NUTRITION_DATA] calories=ЧИСЛО protein=ЧИСЛО fat=ЧИСЛО carbs=ЧИСЛО. "
        "Для общих вопросов о питании этот маркер НЕ добавляй."
    )
    + _FORMATTING_PHILOSOPHY,
    "math": (
        "Ты — терпеливый и понятный преподаватель математики. Объясняешь так, чтобы "
        "человек действительно понял, а не просто увидел ответ. Веди решение по шагам, "
        "коротко поясняя каждый, и в конце дай чёткий итоговый ответ. Простые вычисления "
        "не раздувай — на лёгкий вопрос короткое решение.\n"
        "ФОРМУЛЫ: оборачивай ВСЕ математические переменные, символы и формулы в $...$ для "
        "строчных формул (например, $x$, $f(x)$, $\\nabla f$, а не x, f(x)) и в $$...$$ "
        "для формул на отдельной строке. Никогда не оставляй LaTeX без $-разметки и не "
        "используй окружения вроде \\begin{align} или \\begin{matrix} — они не отобразятся."
    )
    + _FORMATTING_PHILOSOPHY,
    "fitness": (
        "Ты — опытный персональный тренер. Помогаешь с тренировками: составляешь программы "
        "под цель (похудеть, набрать массу, поддерживать форму), объясняешь технику "
        "упражнений, подбираешь замены для дома и зала, советуешь по нагрузке, прогрессии "
        "и восстановлению. Учитывай уровень человека; если данных мало — задай одно "
        "короткое уточнение (цель, опыт, инвентарь). Программу тренировки оформляй "
        "структурно (упражнение — подходы×повторы), общий совет давай обычным текстом. "
        "Будь мотивирующим, но честным. При боли, травмах или болезнях рекомендуй "
        "обратиться к врачу и не давай медицинских назначений."
    )
    + _FORMATTING_PHILOSOPHY,
    "writing": (
        "Ты — сильный редактор и копирайтер. Помогаешь писать и улучшать тексты: письма и "
        "сообщения, посты, резюме и сопроводительные, объявления; переписываешь под нужный "
        "тон, сокращаешь, исправляешь грамматику и стиль, делаешь краткие пересказы, "
        "переводишь. Сразу выдавай готовый текст, который можно скопировать и отправить, "
        "без описания процесса и воды — максимум одна короткая пометка при необходимости. "
        "Если тон или длина неочевидны — выбери уместный вариант сам, уточняй только когда "
        "без этого никак. Сохраняй смысл и голос автора."
    )
    + _FORMATTING_PHILOSOPHY,
    "code": (
        "Ты — сеньор-разработчик. Пишешь корректный, идиоматичный и читаемый код и кратко "
        "объясняешь решение. Код давай в блоках ``` с указанием языка. Поясняй ключевые "
        "места, подводные камни и крайние случаи. Если требований не хватает — задай один "
        "точный уточняющий вопрос, иначе сделай разумное допущение и обозначь его. Не "
        "переусложняй: простую задачу решай просто."
    )
    + _FORMATTING_PHILOSOPHY,
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

# Runtime kill switch: model ids placed here are skipped by both the direct
# Gemini and OpenRouter fallback loops below, without needing a redeploy.
# Toggled from the admin panel (src/handlers/admin.py).
disabled_models: set = set()

# Newer Gemini "flash" models reason internally ("thinking") before answering.
# That improves hard reasoning but, with a generous output budget, balloons to
# hundreds of thought tokens and ~5-11s latency even on a trivial question. For
# fast conversational modes we set thinkingBudget=0, which cuts latency roughly
# 4-5x and stops burning quota on thought tokens, with no quality loss for chat.
# These are the model ids verified to accept thinkingBudget=0; 2.0 models (no
# thinking) and the pro model (deep fallback) are deliberately left untouched.
THINKING_CONTROL_MODELS = {
    "gemini-3-flash-preview",
    "gemini-3.5-flash",
    "gemini-2.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
}

# Modes that keep the model's default thinking on, because step-by-step internal
# reasoning measurably improves correctness and users accept a short wait there.
THINKING_MODES = {"math"}


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


def is_response_complete(text: str) -> bool:
    """
    Checks if the generated response appears complete and is not cut off.
    Returns True if complete, False if truncated.
    """
    if not text:
        return False

    stripped = text.strip()

    # 1. Check for unclosed fenced code blocks
    if stripped.count("```") % 2 != 0:
        return False

    # 2. Check for unclosed bold markers
    if stripped.count("**") % 2 != 0:
        return False

    # 3. Check for obvious trailing cutoff symbols (like commas, colons, or dashes/conjunctions)
    if stripped.endswith((",", ":", "-", "—", "and", "or", "with", "для", "и", "в", "на", "с")):
        return False

    # 4. Check for cut-off markdown table rows
    lines = [line.strip() for line in stripped.split("\n") if line.strip()]
    pipe_lines = [line for line in lines if "|" in line]
    if pipe_lines:
        for line in reversed(pipe_lines):
            # If a line looks like a table row (has at least 2 pipes)
            if line.count("|") >= 2:
                # Table rows should end with a pipe
                if not line.endswith("|"):
                    return False
                break

    # 5. Check if the text is longer than 100 characters and ends with a word and no punctuation,
    # excluding common unit abbreviations
    last_word_match = re.search(r"\b([а-яА-ЯёЁa-zA-Z]+)\s*$", stripped)
    if last_word_match:
        last_word = last_word_match.group(1).lower()
        allowed_units = {
            "г", "г.", "ккал", "мл", "шт", "g", "kcal", "ml", "pcs",
            "белки", "жиры", "углеводы", "калории",
            "proteins", "fats", "carbs", "calories"
        }
        if last_word not in allowed_units:
            # If no sentence-ending punctuation or markdown markers are at the end
            if len(stripped) > 100 and not re.search(r"[.\!?*`_\)~\]\}\-\|>\b]\s*$", stripped):
                return False

    return True


async def ask_llm(
    mode: str,
    history: List[Dict[str, str]],
    image_base64: Optional[str] = None,
    vision_prompt: Optional[str] = None,
    user_settings: Optional[Dict[str, str]] = None,
    is_summarizing: bool = False,
    audio_base64: Optional[str] = None,
    audio_mime_type: str = "audio/ogg",
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

    # Map max_length to maxOutputTokens (increased for safety buffer)
    tokens_map = {"short": 1000, "medium": 2000, "long": 4000}
    max_tokens = tokens_map.get(max_length, 2000)

    # Ensure nutrition mode has enough tokens to avoid truncating tables (unless short length is requested)
    if mode == "nutrition" and max_length != "short":
        max_tokens = max(max_tokens, 2000)

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

    # Models to try, ordered quality-first: strong, fast "flash" models lead
    # (best understanding for a chat bot, while still quick), then the cheaper
    # "lite" and older tiers as quota fallbacks, then a "-latest" alias as a
    # safety net, and finally the heavier "pro" model as a last direct resort.
    # A request that exhausts the daily quota on one model still has a long
    # chain to fall through before giving up (several projects share the pool).
    media_base64 = image_base64 or audio_base64
    media_mime_type = "image/jpeg" if image_base64 else audio_mime_type

    if media_base64:
        # Vision/audio models (all support multimodal input)
        direct_models = [
            "gemini-3-flash-preview",
            "gemini-2.5-flash",
            "gemini-3.1-flash-lite",
            "gemini-2.5-flash-lite",
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
            "gemini-flash-latest",
            "gemini-2.5-pro",
        ]
        openrouter_models = [
            # Free vision-capable models first (account currently has $0
            # OpenRouter credits, so paid models below would just fail).
            "google/gemma-4-31b-it:free",
            "google/gemma-4-26b-a4b-it:free",
            # Paid Google models, kept as a last resort in case credits are
            # ever added to the OpenRouter account.
            "google/gemini-3.1-flash-lite",
            "google/gemini-2.5-flash-lite",
            "google/gemini-3-flash-preview",
            "google/gemini-3.5-flash",
            "google/gemini-2.5-flash",
        ]
        prompt_text = vision_prompt or "Describe this image."
        if audio_base64 and not image_base64:
            # OpenRouter's image_url content type doesn't apply to audio, and
            # none of the configured free models reliably accept raw audio
            # input through that schema, so audio only goes through direct
            # Gemini (which supports audio natively via inlineData).
            openrouter_models = []
    else:
        # Text models
        direct_models = [
            "gemini-3-flash-preview",
            "gemini-2.5-flash",
            "gemini-3.1-flash-lite",
            "gemini-2.5-flash-lite",
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
            "gemini-flash-latest",
            "gemini-2.5-pro",
        ]
        openrouter_models = [
            # Free models first (account currently has $0 OpenRouter
            # credits, so paid models below would just fail).
            "openai/gpt-oss-120b:free",
            "openai/gpt-oss-20b:free",
            "meta-llama/llama-3.3-70b-instruct:free",
            "qwen/qwen3-next-80b-a3b-instruct:free",
            "nousresearch/hermes-3-llama-3.1-405b:free",
            "google/gemma-4-31b-it:free",
            "nvidia/nemotron-3-super-120b-a12b:free",
            # Paid Google models, kept as a last resort in case credits are
            # ever added to the OpenRouter account.
            "google/gemini-3.1-flash-lite",
            "google/gemini-2.5-flash-lite",
            "google/gemini-3-flash-preview",
            "google/gemini-3.5-flash",
            "google/gemini-2.5-flash",
        ]

    # Apply the runtime kill switch (admin panel) before trying any model.
    if disabled_models:
        direct_models = [m for m in direct_models if m not in disabled_models]
        openrouter_models = [m for m in openrouter_models if m not in disabled_models]

    # --- 1. Direct Gemini API calling with key rotation ---
    if key_pool.get_active_keys():
        async with httpx.AsyncClient(timeout=30.0) as client:
            for model in direct_models:
                active_keys = key_pool.get_active_keys()
                if not active_keys:
                    continue
                shuffled_keys = active_keys.copy()
                random.shuffle(shuffled_keys)

                generation_config: Dict[str, Any] = {
                    "maxOutputTokens": max_tokens,
                    "temperature": temperature,
                }
                # Turn off "thinking" for fast modes on models that support it.
                if mode not in THINKING_MODES and model in THINKING_CONTROL_MODELS:
                    generation_config["thinkingConfig"] = {"thinkingBudget": 0}

                for key in shuffled_keys:
                    try:
                        logger.info(
                            f"Trying direct Gemini ({model}) with key: {key[:8]}..."
                        )
                        url = (
                            "https://generativelanguage.googleapis.com"
                            f"/v1beta/models/{model}:generateContent?key={key}"
                        )

                        if media_base64:
                            # Construct vision/audio request payload
                            payload = {
                                "contents": [
                                    {
                                        "parts": [
                                            {"text": prompt_text},
                                            {
                                                "inlineData": {
                                                    "mimeType": media_mime_type,
                                                    "data": media_base64,
                                                }
                                            },
                                        ]
                                    }
                                ],
                                "systemInstruction": {
                                    "parts": [{"text": system_prompt}]
                                },
                                "generationConfig": generation_config,
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
                                "generationConfig": generation_config,
                            }

                        # Log payload details for debugging (hiding base64 data)
                        payload_log = payload.copy()
                        if media_base64 and "contents" in payload_log:
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
                                # Join all text parts to get the full response content
                                text_response = "".join(
                                    part.get("text", "")
                                    for part in parts
                                    if "text" in part
                                )
                                if text_response:
                                    if not is_response_complete(text_response):
                                        logger.warning(
                                            f"Direct Gemini ({model}) returned incomplete/truncated response: "
                                            f"'{text_response[:100]}...', treating as failure to trigger fallback."
                                        )
                                        continue

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
                                            if not media_base64
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

                            # If this is not the last model in the list, and the response is incomplete, try next model
                            if model != openrouter_models[-1] and not is_response_complete(text_response):
                                logger.warning(
                                    f"OpenRouter ({model}) returned incomplete/truncated response, trying next model."
                                )
                                continue

                            latency = time.time() - start_time

                            # Retrieve tokens from usage metadata
                            usage = data.get("usage", {})
                            prompt_tokens = usage.get("prompt_tokens")
                            completion_tokens = usage.get("completion_tokens")

                            # Estimate if missing
                            if prompt_tokens is None:
                                prompt_tokens = (
                                    estimate_history_tokens(history, system_prompt)
                                    if not media_base64
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
