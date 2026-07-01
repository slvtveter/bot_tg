import logging
from datetime import datetime, timedelta

from telegram import Update
from telegram.error import Forbidden
from telegram.ext import ContextTypes

from src.database import add_reminder, get_pending_reminders, mark_reminder_sent, get_user_language
from src.i18n import t

logger = logging.getLogger(__name__)


async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /remind HH:MM текст — sets a one-time reminder.
    Time is interpreted as UTC. If the time has already passed today, fires tomorrow.
    """
    user = update.effective_user
    if not user or not update.message:
        return

    lang = await get_user_language(user.id)
    args = context.args or []

    if len(args) < 2:
        await update.message.reply_html(t("remind_usage", lang))
        return

    time_str = args[0]
    text = " ".join(args[1:])

    try:
        hour, minute = map(int, time_str.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except ValueError:
        await update.message.reply_html(t("remind_usage", lang))
        return

    now = datetime.utcnow()
    remind_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if remind_at <= now:
        remind_at += timedelta(days=1)

    await add_reminder(user.id, remind_at.isoformat(), text)
    await update.message.reply_html(
        t("remind_set", lang, time=remind_at.strftime("%H:%M"), text=text)
    )


async def check_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job that runs every 60 seconds and fires any due reminders."""
    reminders = await get_pending_reminders()
    for reminder in reminders:
        try:
            lang = await get_user_language(reminder["user_id"])
            await context.bot.send_message(
                chat_id=reminder["user_id"],
                text=t("remind_fired", lang, text=reminder["text"]),
            )
            await mark_reminder_sent(reminder["id"])
        except Forbidden:
            # The user blocked the bot — this send will never succeed, so mark
            # the reminder sent instead of retrying it forever every 60s.
            logger.info(
                f"Reminder {reminder['id']}: user {reminder['user_id']} blocked "
                "the bot; marking as sent."
            )
            await mark_reminder_sent(reminder["id"])
        except Exception as e:
            logger.error(f"Failed to send reminder {reminder['id']}: {e}")
