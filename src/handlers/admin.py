import asyncio
import csv
import html
import io
import logging
import os
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from src import config
from src.database import (
    DB_PATH,
    get_admin_overview,
    get_all_user_ids,
    get_db_connection,
    get_recent_feedback,
)
from src.llm import disabled_models, key_pool

logger = logging.getLogger(__name__)


def _is_admin(user_id: int) -> bool:
    # Fail closed: admin features require an explicit ADMIN_IDS allowlist. If it
    # is empty, nobody is an admin (rather than everybody) - so the panel is
    # never accidentally exposed to all users.
    return user_id in config.ADMIN_IDS


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Sends the administrator dashboard with inline controls.
    """
    user = update.effective_user
    if not user or not update.message:
        return

    # Authorization check (fail closed - only ADMIN_IDS may enter).
    if not _is_admin(user.id):
        await update.message.reply_html(
            "⛔ <b>Доступ запрещён:</b> вы не являетесь администратором бота."
        )
        return

    text, reply_markup = await get_admin_dashboard_data()
    await update.message.reply_html(text, reply_markup=reply_markup)


async def get_admin_dashboard_data() -> tuple[str, InlineKeyboardMarkup]:
    """
    Queries the database and constructs the admin dashboard text and keyboard.
    All counts are lifetime/permanent (users, stats, nutrition_log and feedback
    are never auto-pruned), so growth can be tracked over the whole lifetime.
    """
    overview = await get_admin_overview()

    # Database line. On Turso the DB is remote (no local file), so os.path.getsize
    # would always report 0 KB — show the backend instead of a misleading zero.
    if config.USE_TURSO:
        db_line = "• Бэкенд: <code>Turso (удалённая БД)</code>"
    else:
        db_size_kb = 0.0
        if os.path.exists(DB_PATH):
            db_size_kb = os.path.getsize(DB_PATH) / 1024.0
        db_line = (
            f"• Размер: <code>{db_size_kb:.2f} KB</code> · "
            f"файл: <code>{os.path.basename(DB_PATH)}</code>"
        )

    # Key pool status
    total_keys = len(config.GOOGLE_API_KEYS)
    active_keys = len(key_pool.get_active_keys())
    cooldown_keys = total_keys - active_keys

    text = (
        "👑 <b>Панель администратора</b>\n\n"
        "👥 <b>Пользователи</b>\n"
        f"• Всего: <code>{overview['total_users']}</code>\n"
        f"• Новых сегодня: <code>{overview['new_today']}</code> · "
        f"за 7 дней: <code>{overview['new_7d']}</code>\n"
        f"• Активных за 24ч: <code>{overview['active_24h']}</code> · "
        f"за 7 дней: <code>{overview['active_7d']}</code>\n\n"
        "📈 <b>Запросы</b> <i>(история сохраняется навсегда)</i>\n"
        f"• Всего обработано: <code>{overview['total_requests']}</code>\n"
        f"• Сегодня: <code>{overview['requests_today']}</code> · "
        f"за час: <code>{overview['requests_hour']}</code>\n"
        f"• Средняя задержка (1ч): <code>{overview['avg_latency_hour']:.2f} сек</code>\n\n"
        "🍽 <b>Питание</b>\n"
        f"• Проанализировано блюд: <code>{overview['total_meals']}</code>\n\n"
        "🗄 <b>База данных</b>\n"
        f"{db_line}\n\n"
        "🔑 <b>API ключи (Gemini Pool)</b>\n"
        f"• Всего: <code>{total_keys}</code> · активно: <code>{active_keys}</code> · "
        f"cooldown: <code>{cooldown_keys}</code>"
    )

    # Only surface the model kill switch when something is actually disabled,
    # so the panel stays uncluttered the rest of the time.
    if disabled_models:
        disabled_text = ", ".join(sorted(disabled_models))
        text += (
            f"\n\n🚫 <b>Отключённые модели:</b> <code>{html.escape(disabled_text)}</code>\n"
            f"<i>Включить обратно: /enable_model &lt;id&gt;</i>"
        )

    keyboard = [
        [
            InlineKeyboardButton(
                f"📨 Отзывы ({overview['feedback_count']})",
                callback_data="admin_feedback",
            ),
            InlineKeyboardButton(
                "🏆 Топ пользователей", callback_data="admin_top_users"
            ),
        ],
        [
            InlineKeyboardButton(
                "🔑 Статус API ключей", callback_data="admin_keys_status"
            ),
            InlineKeyboardButton(
                "📁 Экспорт stats в CSV", callback_data="admin_export_csv"
            ),
        ],
    ]
    return text, InlineKeyboardMarkup(keyboard)


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles admin inline keyboard button clicks.
    """
    query = update.callback_query
    if not query:
        return

    user_id = query.from_user.id
    if not _is_admin(user_id):
        await query.answer("⛔ Доступ запрещён.", show_alert=True)
        return

    await query.answer()
    data = query.data

    if data == "admin_keys_status":
        now = time.time()
        cooldown_statuses = []

        for i, key in enumerate(config.GOOGLE_API_KEYS):
            # cooldowns are keyed by (key, model) pairs — a key can be cooling
            # down on one model while still serving others — so scan every entry
            # for this key and surface its latest active cooldown.
            cooldown_until = max(
                [until for (k, _m), until in key_pool.cooldowns.items() if k == key],
                default=0.0,
            )
            if cooldown_until > now:
                remaining = cooldown_until - now
                cooldown_statuses.append(
                    f"Ключ #{i + 1} [{key[:8]}...]: ⏳ Cooldown ещё {remaining:.1f} сек"
                )
            else:
                cooldown_statuses.append(f"Ключ #{i + 1} [{key[:8]}...]: ✅ Активен")

        status_text = "🔑 <b>Детализация пула Google API ключей:</b>\n\n" + "\n".join(
            cooldown_statuses
        )

        text, reply_markup = await get_admin_dashboard_data()
        await query.edit_message_text(
            text=text + f"\n\n{status_text}",
            reply_markup=reply_markup,
            parse_mode="HTML",
        )

    elif data == "admin_top_users":
        top_text = "🏆 <b>Топ-5 пользователей по запросам и токенам:</b>\n\n"
        try:
            async with get_db_connection() as db:
                async with db.execute(
                    """
                    SELECT s.user_id, u.username, COUNT(*) as requests,
                           SUM(s.prompt_tokens + s.completion_tokens) as total_tokens
                    FROM stats s
                    LEFT JOIN users u ON u.user_id = s.user_id
                    GROUP BY s.user_id
                    ORDER BY total_tokens DESC
                    LIMIT 5
                    """
                ) as cursor:
                    rows = await cursor.fetchall()
            if not rows:
                top_text += "Пока нет данных."
            else:
                for i, row in enumerate(rows, 1):
                    uid, username, requests, total_tokens = row
                    uname = html.escape(username) if username else f"id{uid}"
                    top_text += (
                        f"{i}. <b>{uname}</b> — "
                        f"<code>{requests}</code> запросов, "
                        f"<code>{total_tokens or 0}</code> токенов\n"
                    )
        except Exception as e:
            logger.error(f"Error querying top users: {e}")
            top_text += "Ошибка при получении данных."

        text, reply_markup = await get_admin_dashboard_data()
        await query.edit_message_text(
            text=text + f"\n\n{top_text}",
            reply_markup=reply_markup,
            parse_mode="HTML",
        )

    elif data == "admin_export_csv":
        try:
            async with get_db_connection() as db:
                async with db.execute(
                    """
                    SELECT id, user_id, model, prompt_tokens, completion_tokens, latency, timestamp
                    FROM stats ORDER BY id ASC
                    """
                ) as cursor:
                    rows = await cursor.fetchall()

            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(
                ["id", "user_id", "model", "prompt_tokens", "completion_tokens", "latency", "timestamp"]
            )
            writer.writerows(rows)
            csv_bytes = buf.getvalue().encode("utf-8")

            await query.message.reply_document(
                document=io.BytesIO(csv_bytes),
                filename="stats_export.csv",
                caption=f"Экспорт таблицы stats ({len(rows)} строк).",
            )
        except Exception as e:
            logger.error(f"Error exporting stats CSV: {e}")
            await query.message.reply_text(f"❌ Ошибка при экспорте: {e}")

    elif data == "admin_feedback":
        feedback = await get_recent_feedback(limit=10)
        if not feedback:
            fb_text = "📨 <b>Отзывы пользователей</b>\n\nПока нет отзывов."
        else:
            lines = ["📨 <b>Последние отзывы пользователей:</b>\n"]
            for fb in feedback:
                ts = (fb["timestamp"] or "").split(" ")[0]
                uname = (
                    html.escape(fb["username"])
                    if fb["username"]
                    else f"id{fb['user_id']}"
                )
                content = html.escape((fb["content"] or "")[:300])
                lines.append(f"• <i>{ts}</i> <b>{uname}</b>: {content}")
            fb_text = "\n".join(lines)

        text, reply_markup = await get_admin_dashboard_data()
        await query.edit_message_text(
            text=text + f"\n\n{fb_text}",
            reply_markup=reply_markup,
            parse_mode="HTML",
        )


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin-only command: sends a message to every registered user.
    Usage: /broadcast <text>
    """
    user = update.effective_user
    if not user or not update.message:
        return
    if not _is_admin(user.id):
        await update.message.reply_html("⛔ <b>Доступ запрещен:</b> Вы не являетесь администратором бота.")
        return

    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_html("Использование: <code>/broadcast текст сообщения</code>")
        return

    user_ids = await get_all_user_ids()
    escaped_text = html.escape(text)
    delivered = 0
    failed = 0
    for uid in user_ids:
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=f"📢 <b>Объявление от администрации:</b>\n\n{escaped_text}",
                parse_mode="HTML",
            )
            delivered += 1
        except Exception as e:
            logger.warning(f"Broadcast failed for user {uid}: {e}")
            failed += 1
        await asyncio.sleep(0.05)

    await update.message.reply_html(
        f"📢 <b>Рассылка завершена.</b>\nДоставлено: <code>{delivered}</code>\nНе доставлено: <code>{failed}</code>"
    )


async def disable_model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin-only command: disables a model id from the LLM fallback chain at runtime.
    Usage: /disable_model <model_id>
    """
    user = update.effective_user
    if not user or not update.message:
        return
    if not _is_admin(user.id):
        await update.message.reply_html("⛔ <b>Доступ запрещен:</b> Вы не являетесь администратором бота.")
        return

    if not context.args:
        await update.message.reply_html("Использование: <code>/disable_model имя_модели</code>")
        return

    model_id = context.args[0]
    disabled_models.add(model_id)
    await update.message.reply_html(
        f"🚫 Модель <code>{html.escape(model_id)}</code> отключена из цепочки фоллбеков."
    )


async def enable_model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin-only command: re-enables a previously disabled model id.
    Usage: /enable_model <model_id>
    """
    user = update.effective_user
    if not user or not update.message:
        return
    if not _is_admin(user.id):
        await update.message.reply_html("⛔ <b>Доступ запрещен:</b> Вы не являетесь администратором бота.")
        return

    if not context.args:
        await update.message.reply_html("Использование: <code>/enable_model имя_модели</code>")
        return

    model_id = context.args[0]
    disabled_models.discard(model_id)
    await update.message.reply_html(
        f"✅ Модель <code>{html.escape(model_id)}</code> снова включена в цепочку фоллбеков."
    )
