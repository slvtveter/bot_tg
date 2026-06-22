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

# Groq (OpenAI-compatible, very fast, generous free tier). Used as a fallback
# tier between direct Gemini and OpenRouter. Left empty disables that tier.
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# Validate that at least one LLM provider is configured
if not GOOGLE_API_KEYS and not OPENROUTER_API_KEY and not GROQ_API_KEY:
    raise ValueError(
        "No LLM API keys configured! Please set GOOGLE_API_KEYS, GROQ_API_KEY "
        "or OPENROUTER_API_KEY in the environment variables or .env file."
    )

# Upper bound on concurrent outbound LLM calls. Bounds how hard a burst of users
# can hit the shared Gemini key pool at once (see src/llm.py _LLM_SEMAPHORE).
LLM_MAX_CONCURRENCY = int(os.getenv("LLM_MAX_CONCURRENCY", "6"))

# Telegram send format. The native-Markdown 'sendRichMessage' API renders tables
# and LaTeX beautifully on up-to-date clients, but on OLDER Telegram versions the
# call SUCCEEDS while the message shows up BLANK (the user sees nothing, and the
# bot can't tell). So the default send path is HTML, which every client renders.
# Set USE_RICH_MESSAGE=true only if your whole audience is on current Telegram.
USE_RICH_MESSAGE = os.getenv("USE_RICH_MESSAGE", "false").lower() in (
    "1",
    "true",
    "yes",
    "on",
)

# Parse admin IDs list
raw_admin_ids = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(x.strip()) for x in raw_admin_ids.split(",") if x.strip().isdigit()]

# If WEBHOOK_URL is set (e.g. on Render), the bot runs in webhook mode instead
# of long-polling, so an incoming Telegram update is the request that wakes a
# free-tier service back up from sleep. Left empty for local/Docker use, where
# polling is simpler and doesn't require a public HTTPS endpoint.
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").rstrip("/")
PORT = int(os.getenv("PORT", "8443"))

# Keep-alive: free hosting (e.g. Render's free tier) spins a web service down
# after ~15 min without inbound requests. In webhook mode the bot pings its own
# WEBHOOK_URL every KEEPALIVE_MINUTES (must be < 15) so it stays awake and the
# in-memory state / ephemeral SQLite file isn't lost to a sleep-restart. Set to
# 0 to disable. Has no effect in polling mode (no public URL to ping).
KEEPALIVE_MINUTES = int(os.getenv("KEEPALIVE_MINUTES", "10"))

# Turso (libSQL) durable backend. On free hosting with an ephemeral filesystem
# (no persistent disk), the local SQLite file is wiped on every redeploy. Set
# TURSO_DATABASE_URL (and TURSO_AUTH_TOKEN) to a free Turso database and all
# data is stored remotely instead, surviving redeploys forever. Left empty, the
# bot uses the local SQLite file as before.
TURSO_DATABASE_URL = os.getenv("TURSO_DATABASE_URL", "").strip()
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "").strip()
USE_TURSO = bool(TURSO_DATABASE_URL)
