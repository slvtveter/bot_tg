import asyncio
import logging

import httpx
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
    feedback_command,
    help_command,
    id_command,
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
    week_command,
)

# Set up logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Hold a reference to the keep-alive task so it isn't garbage-collected.
_background_tasks: set = set()


async def _keepalive_loop(url: str, interval_seconds: int) -> None:
    """
    Periodically GET the bot's own public URL so free hosting that spins a web
    service down after ~15 min of inactivity keeps it awake. Any response counts
    as inbound traffic, so the status code doesn't matter (a 404 is fine).
    """
    async with httpx.AsyncClient(timeout=20.0) as client:
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                resp = await client.get(url)
                logger.info(f"Keep-alive ping -> {resp.status_code}")
            except Exception as e:
                logger.warning(f"Keep-alive ping failed: {e}")


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
        BotCommand("today", "Итоги питания за сегодня"),
        BotCommand("week", "Итоги питания за 7 дней"),
        BotCommand("settings", "Настройки параметров ИИ"),
        BotCommand("stats", "Показать статистику использования"),
        BotCommand("undo", "Отменить последний обмен сообщениями"),
        BotCommand("export", "Экспортировать историю сообщений в файл"),
        BotCommand("clear", "Очистить историю сообщений"),
        BotCommand("feedback", "Отправить отзыв или идею"),
        BotCommand("id", "Узнать свой Telegram ID"),
        BotCommand("admin", "Панель администратора (доступно админам)"),
        BotCommand("help", "Справка и FAQ"),
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Bot commands menu registered successfully.")

    if not config.ADMIN_IDS:
        logger.warning(
            "ADMIN_IDS is empty: admin commands (/admin, /broadcast, model "
            "toggles) are DISABLED for everyone. Set ADMIN_IDS to your Telegram "
            "ID (use /id to find it) to enable them."
        )

    # Keep-alive ping to prevent free-tier sleep (webhook mode only).
    if config.WEBHOOK_URL and config.KEEPALIVE_MINUTES > 0:
        interval = config.KEEPALIVE_MINUTES * 60
        task = asyncio.create_task(_keepalive_loop(config.WEBHOOK_URL, interval))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        logger.info(
            f"Keep-alive enabled: pinging {config.WEBHOOK_URL} every "
            f"{config.KEEPALIVE_MINUTES} min to prevent free-tier sleep."
        )


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
    app.add_handler(CommandHandler("week", week_command))
    app.add_handler(CommandHandler("mode", mode_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("feedback", feedback_command))
    app.add_handler(CommandHandler("id", id_command))
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
