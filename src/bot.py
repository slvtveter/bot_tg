import logging

from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    InlineQueryHandler,
    MessageHandler,
    filters,
)

from src import config
from src.database import init_db
from src.handlers import (
    admin_callback,
    admin_command,
    broadcast_command,
    clear_command,
    disable_model_command,
    enable_model_command,
    export_command,
    help_command,
    inline_query_handler,
    message_handler,
    mode_callback,
    mode_command,
    photo_handler,
    settings_callback,
    settings_command,
    start_command,
    stats_command,
    today_command,
    undo_command,
    voice_handler,
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
        BotCommand("undo", "Отменить последний обмен сообщениями"),
        BotCommand("export", "Экспортировать историю сообщений в файл"),
        BotCommand("today", "Итоги питания за сегодня"),
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
    app.add_handler(CommandHandler("undo", undo_command))
    app.add_handler(CommandHandler("export", export_command))
    app.add_handler(CommandHandler("today", today_command))
    app.add_handler(CommandHandler("mode", mode_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("disable_model", disable_model_command))
    app.add_handler(CommandHandler("enable_model", enable_model_command))
    app.add_handler(CommandHandler("help", help_command))

    # Register Callback Query Handlers
    app.add_handler(
        CallbackQueryHandler(settings_callback, pattern="^settings_toggle_")
    )
    app.add_handler(CallbackQueryHandler(mode_callback, pattern="^mode_"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))

    # Register Photo, Voice, Inline and Text Handlers
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.VOICE, voice_handler))
    app.add_handler(InlineQueryHandler(inline_query_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    if config.WEBHOOK_URL:
        # Webhook mode: Telegram pushes updates to us over HTTPS. Used on
        # platforms like Render's free tier, where an incoming HTTP request
        # is what wakes a sleeping service back up - a polling loop would
        # never get the chance to call getUpdates while asleep.
        webhook_path = config.TELEGRAM_BOT_TOKEN
        logger.info(f"Starting Telegram Bot in webhook mode on port {config.PORT}...")
        app.run_webhook(
            listen="0.0.0.0",
            port=config.PORT,
            url_path=webhook_path,
            webhook_url=f"{config.WEBHOOK_URL}/{webhook_path}",
        )
    else:
        logger.info("Starting Telegram Bot in polling mode...")
        app.run_polling()


if __name__ == "__main__":
    main()
