import logging
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    InlineQueryHandler,
    filters,
)
import config
from database import init_db
from handlers import (
    start_command,
    clear_command,
    mode_command,
    mode_callback,
    stats_command,
    help_command,
    message_handler,
    photo_handler,
    settings_command,
    settings_callback,
    inline_query_handler,
    admin_command,
    admin_callback,
)

# Set up logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


async def post_init(application) -> None:
    """
    Perform database initialization and register persistent bot commands menu on application startup.
    """
    logger.info("Initializing database...")
    await init_db()
    logger.info("Database initialized successfully.")

    # Register Command Menu in Telegram UI
    from telegram import BotCommand

    commands = [
        BotCommand("start", "Перезапустить бота / Начать"),
        BotCommand("mode", "Выбрать режим работы"),
        BotCommand("stats", "Показать статистику использования"),
        BotCommand("clear", "Очистить историю сообщений"),
        BotCommand("settings", "Настройки параметров ИИ"),
        BotCommand("admin", "Панель администратора (доступно админам)"),
        BotCommand("help", "Справка и FAQ"),
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Bot commands menu registered successfully.")


def main():
    # Make sure we have a valid token (already validated in config.py, but safe guard check)
    if not config.TELEGRAM_BOT_TOKEN:
        logger.critical("TELEGRAM_BOT_TOKEN is missing!")
        return

    # Create the application and set up database initialization
    app = (
        ApplicationBuilder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Register Command Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("mode", mode_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("help", help_command))

    # Register Callback Query Handlers
    app.add_handler(
        CallbackQueryHandler(settings_callback, pattern="^settings_toggle_")
    )
    app.add_handler(CallbackQueryHandler(mode_callback, pattern="^mode_"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))

    # Register Photo, Inline and Text Handlers
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(InlineQueryHandler(inline_query_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    logger.info("Starting Telegram Bot with modular architecture...")
    app.run_polling()


if __name__ == "__main__":
    main()
