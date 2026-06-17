import os

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError(
        "TELEGRAM_BOT_TOKEN is missing from the environment variables or .env file."
    )

# Extract multiple Google API keys (supports comma-separated list for key pool/rotation)
raw_google_keys = os.getenv("GOOGLE_API_KEYS", "")
GOOGLE_API_KEYS = [k.strip() for k in raw_google_keys.split(",") if k.strip()]

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

# Validate that at least one LLM key is configured
if not GOOGLE_API_KEYS and not OPENROUTER_API_KEY:
    raise ValueError(
        "No LLM API keys configured! Please set GOOGLE_API_KEYS or "
        "OPENROUTER_API_KEY in the environment variables or .env file."
    )

# Parse admin IDs list
raw_admin_ids = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(x.strip()) for x in raw_admin_ids.split(",") if x.strip().isdigit()]
