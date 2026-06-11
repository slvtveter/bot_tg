import logging
from telegram.ext import (
    ApplicationBuilder, 
    CommandHandler, 
    CallbackQueryHandler, 
    MessageHandler, 
    filters
)
import config
from database import init_db
from handlers import (
    start_command,
    clear_command,
    mode_command,
    mode_callback,
    stats_command,
    message_handler,
    photo_handler
)

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def post_init(application) -> None:
    """
    Perform database initialization on application startup.
    """
    logger.info("Initializing database...")
    await init_db()
    logger.info("Database initialized successfully.")

def main():
    # Make sure we have a valid token (already validated in config.py, but safe guard check)
    if not config.TELEGRAM_BOT_TOKEN:
        logger.critical("TELEGRAM_BOT_TOKEN is missing!")
        return

    # Create the application and set up database initialization
    app = ApplicationBuilder().token(config.TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    # Register Command Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("mode", mode_command))
    app.add_handler(CommandHandler("stats", stats_command))

    # Register Callback Query Handler for inline mode buttons
    app.add_handler(CallbackQueryHandler(mode_callback))

    # Register Photo and Text Handlers
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    logger.info("Starting Telegram Bot with modular architecture...")
    app.run_polling()

if __name__ == "__main__":
    main()