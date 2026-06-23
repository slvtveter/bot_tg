import asyncio
import logging

import httpx
from telegram import Update
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
    feedback_command,
    help_command,
    inline_query_handler,
    message_handler,
    mode_callback,
    mode_command,
    photo_handler,
    privacy_command,
    settings_callback,
    settings_command,
    start_command,
    stats_command,
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

    commands_ru = [
        BotCommand("start", "Перезапустить бота / Начать"),
        BotCommand("mode", "Выбрать режим работы"),
        BotCommand("week", "Итоги питания за 7 дней"),
        BotCommand("settings", "Настройки ИИ"),
        BotCommand("stats", "Показать статистику использования"),
        BotCommand("clear", "Очистить историю и начать заново"),
        BotCommand("feedback", "Отправить отзыв или идею"),
        BotCommand("privacy", "Конфиденциальность данных"),
        BotCommand("admin", "Панель администратора (для админов)"),
        BotCommand("help", "Справка и FAQ"),
    ]
    commands_en = [
        BotCommand("start", "Restart / Get started"),
        BotCommand("mode", "Choose a mode"),
        BotCommand("week", "Nutrition over the last 7 days"),
        BotCommand("settings", "AI settings"),
        BotCommand("stats", "Show your usage stats"),
        BotCommand("clear", "Clear history and start fresh"),
        BotCommand("feedback", "Send feedback or an idea"),
        BotCommand("privacy", "Data privacy"),
        BotCommand("admin", "Admin panel (admins only)"),
        BotCommand("help", "Help and FAQ"),
    ]
    # The command-menu language follows the user's Telegram client locale (not
    # the in-bot setting): English is the default, Russian for ru-locale clients.
    await application.bot.set_my_commands(commands_en)
    await application.bot.set_my_commands(commands_ru, language_code="ru")
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


async def error_handler(update: object, context) -> None:
    """
    Catch-all for exceptions raised inside any handler. Without this, a failure
    on the text path (e.g. a transient DB/Turso hiccup on a write) would reach
    the user as silence. Here we log it and best-effort tell the user something
    went wrong, so a request never ends with no reply at all.
    """
    logger.error(
        "Unhandled exception while processing an update", exc_info=context.error
    )
    try:
        if not isinstance(update, Update) or update.effective_chat is None:
            return
        lang = "ru"
        if update.effective_user:
            try:
                from src.database import get_user_language

                lang = await get_user_language(update.effective_user.id)
            except Exception:
                pass
        from src.i18n import t

        await context.bot.send_message(
            chat_id=update.effective_chat.id, text=t("error_no_answer", lang)
        )
    except Exception as e:
        logger.error(f"error_handler failed to notify user: {e}")


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
    app.add_handler(CommandHandler("week", week_command))
    app.add_handler(CommandHandler("mode", mode_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("feedback", feedback_command))
    app.add_handler(CommandHandler("privacy", privacy_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("disable_model", disable_model_command))
    app.add_handler(CommandHandler("enable_model", enable_model_command))
    app.add_handler(CommandHandler("help", help_command))

    # Register Callback Query Handlers
    app.add_handler(
        CallbackQueryHandler(settings_callback, pattern="^settings_")
    )
    app.add_handler(CallbackQueryHandler(mode_callback, pattern="^mode_"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))

    # Register Photo, Voice, Inline and Text Handlers
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.VOICE, voice_handler))
    app.add_handler(InlineQueryHandler(inline_query_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    # Global safety net: any unhandled exception in a handler routes here instead
    # of failing silently, so the user always gets some response.
    app.add_error_handler(error_handler)

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
            # Drop any updates Telegram queued while the service was down or
            # restarting. Without this, a backlog of old button taps from every
            # user gets replayed on startup, making the bot appear to switch
            # modes "by itself" and spam every user with "mode changed".
            drop_pending_updates=True,
        )
    else:
        logger.info("Starting Telegram Bot in polling mode...")
        app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
