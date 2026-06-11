import re
import time
import random
import logging
import asyncio
import requests
from typing import List, Dict, Any, Tuple, Optional
import config

logger = logging.getLogger(__name__)

SYSTEM_PROMPTS = {
    "nutrition": (
        "Ты — краткий и емкий помощник по питанию. Отвечай ОЧЕНЬ кратко, вкратце и простыми словами. "
        "Пиши в формате обычного сообщения Telegram. Описывай состав, пользу и калорийность блюд."
    ),
    "math": (
        "Ты — подробный и терпеливый преподаватель математики. Объясняй формулы и математические концепции, "
        "используя LaTeX разметку (для inline формул используй $...$, для блочных формул используй $$...$$ или \\[...\\]) "
        "и Markdown разметку. Твои объяснения должны быть пошаговыми, понятными и на русском языке."
    ),
    "general": (
        "Ты — вежливый, структурированный и полезный ИИ-ассистент. Отвечай четко, по делу и в структурированной форме. "
        "Помогай пользователю во всем, о чем он тебя попросит."
    )
}

def estimate_tokens(text: str) -> int:
    """
    Estimates token count based on character count.
    Rule: 1 token ~= 4 chars for English/symbols, 2 chars for Russian (Cyrillic).
    """
    if not text:
        return 0
    cyrillic_chars = len(re.findall(r'[а-яА-ЯёЁ]', text))
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

async def ask_llm(
    mode: str, 
    history: List[Dict[str, str]], 
    image_base64: Optional[str] = None, 
    vision_prompt: Optional[str] = None
) -> Tuple[Optional[str], Optional[str], int, int, float]:
    """
    Queries Gemini API directly with key rotation, falling back to OpenRouter.
    Returns: (response_text, model_name, prompt_tokens, completion_tokens, latency_seconds)
    """
    system_prompt = SYSTEM_PROMPTS.get(mode, SYSTEM_PROMPTS["general"])
    start_time = time.time()

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
            "openai/gpt-oss-20b:free"
        ]

    # --- 1. Direct Gemini API calling with key rotation ---
    if config.GOOGLE_API_KEYS:
        for model in direct_models:
            shuffled_keys = config.GOOGLE_API_KEYS.copy()
            random.shuffle(shuffled_keys)
            
            for key in shuffled_keys:
                try:
                    logger.info(f"Trying direct Gemini ({model}) with key: {key[:8]}...")
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                    
                    if image_base64:
                        # Construct vision request payload
                        payload = {
                            "contents": [{
                                "parts": [
                                    {"text": prompt_text},
                                    {
                                        "inlineData": {
                                            "mimeType": "image/jpeg",
                                            "data": image_base64
                                        }
                                    }
                                ]
                            }],
                            "systemInstruction": {
                                "parts": [{"text": system_prompt}]
                            },
                            "generationConfig": {
                                "maxOutputTokens": 1000,
                                "temperature": 0.3
                            }
                        }
                    else:
                        # Construct chat text request payload
                        gemini_contents = []
                        for msg in history:
                            role = msg.get("role")
                            g_role = "model" if role == "assistant" else "user"
                            gemini_contents.append({
                                "role": g_role,
                                "parts": [{"text": msg.get("content", "")}]
                            })
                        
                        payload = {
                            "contents": gemini_contents,
                            "systemInstruction": {
                                "parts": [{"text": system_prompt}]
                            },
                            "generationConfig": {
                                "maxOutputTokens": 1000,
                                "temperature": 0.3
                            }
                        }

                    # Make blocking requests in a separate thread so we don't block the async loop
                    response = await asyncio.to_thread(
                        requests.post, url, json=payload, timeout=30
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        candidates = data.get("candidates", [])
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts", [])
                            if parts and "text" in parts[0]:
                                text_response = parts[0]["text"]
                                latency = time.time() - start_time
                                
                                # Retrieve tokens from usageMetadata
                                usage_meta = data.get("usageMetadata", {})
                                prompt_tokens = usage_meta.get("promptTokenCount")
                                completion_tokens = usage_meta.get("candidatesTokenCount")
                                
                                # Estimate if missing
                                if prompt_tokens is None or completion_tokens is None:
                                    prompt_tokens = estimate_history_tokens(history, system_prompt) if not image_base64 else estimate_tokens(prompt_text)
                                    completion_tokens = estimate_tokens(text_response)

                                logger.info(f"Direct Gemini success with {model}. Latency: {latency:.2f}s")
                                return text_response, f"Gemini API ({model})", prompt_tokens, completion_tokens, latency
                    
                    logger.warning(f"Direct Gemini ({model}) returned status {response.status_code}: {response.text[:200]}")
                except Exception as e:
                    logger.error(f"Error calling direct Gemini ({model}): {e}")

    # --- 2. Fallback to OpenRouter ---
    if config.OPENROUTER_API_KEY:
        for model in openrouter_models:
            try:
                logger.info(f"Trying OpenRouter fallback ({model})...")
                url = "https://openrouter.ai/api/v1/chat/completions"
                headers = {
                    "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
                    "Content-Type": "application/json"
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
                                    }
                                }
                            ]
                        }
                    ]
                else:
                    # Construct OpenRouter Text messages
                    messages = [{"role": "system", "content": system_prompt}] + history

                payload = {
                    "model": model,
                    "messages": messages,
                    "max_tokens": 1000,
                    "temperature": 0.3
                }

                response = await asyncio.to_thread(
                    requests.post, url, headers=headers, json=payload, timeout=30
                )

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
                            prompt_tokens = estimate_history_tokens(history, system_prompt) if not image_base64 else estimate_tokens(prompt_text)
                            completion_tokens = estimate_tokens(text_response)

                        logger.info(f"OpenRouter success with {model}. Latency: {latency:.2f}s")
                        return text_response, f"OpenRouter ({model})", prompt_tokens, completion_tokens, latency
                
                logger.warning(f"OpenRouter ({model}) returned status {response.status_code}: {response.text[:200]}")
            except Exception as e:
                logger.error(f"Error calling OpenRouter ({model}): {e}")

    # All attempts failed
    latency = time.time() - start_time
    logger.error("All direct Gemini API and OpenRouter fallback attempts failed.")
    return None, None, 0, 0, latency
