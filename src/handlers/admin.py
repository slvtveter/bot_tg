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
from src.database import DB_PATH, get_all_user_ids, get_db_connection
from src.llm import disabled_models, key_pool

logger = logging.getLogger(__name__)


def _is_admin(user_id: int) -> bool:
    return not config.ADMIN_IDS or user_id in config.ADMIN_IDS


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Sends the administrator dashboard with inline controls.
    """
    user = update.effective_user
    if not user or not update.message:
        return

    # Authorization Check
    # If ADMIN_IDS is defined, restrict access. If empty, allow for development.
    if config.ADMIN_IDS and user.id not in config.ADMIN_IDS:
        await update.message.reply_html(
            "⛔ <b>Доступ запрещен:</b> Вы не являетесь администратором бота."
        )
        return

    text, reply_markup = await get_admin_dashboard_data()
    await update.message.reply_html(text, reply_markup=reply_markup)


async def get_admin_dashboard_data() -> tuple[str, InlineKeyboardMarkup]:
    """
    Queries database and constructs the admin dashboard text and keyboard.
    """
    total_users = 0
    active_today = 0
    requests_hour = 0
    avg_latency = 0.0

    try:
        async with get_db_connection() as db:
            # Total registered users
            async with db.execute("SELECT COUNT(*) FROM users") as cursor:
                row = await cursor.fetchone()
                if row:
                    total_users = row[0]

            # Active users in the last 24 hours
            async with db.execute(
                "SELECT COUNT(DISTINCT user_id) FROM stats WHERE timestamp >= datetime('now', '-24 hours')"
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    active_today = row[0]

            # Requests in last hour
            async with db.execute(
                "SELECT COUNT(*) FROM stats WHERE timestamp >= datetime('now', '-1 hour')"
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    requests_hour = row[0]

            # Average latency in last hour
            async with db.execute(
                "SELECT AVG(latency) FROM stats WHERE timestamp >= datetime('now', '-1 hour')"
            ) as cursor:
                row = await cursor.fetchone()
                if row and row[0] is not None:
                    avg_latency = row[0]
    except Exception as e:
        logger.error(f"Error querying admin stats: {e}")

    # Database file size
    db_size_kb = 0.0
    if os.path.exists(DB_PATH):
        db_size_kb = os.path.getsize(DB_PATH) / 1024.0

    # Key pool status
    total_keys = len(config.GOOGLE_API_KEYS)
    active_keys = len(key_pool.get_active_keys())
    cooldown_keys = total_keys - active_keys

    disabled_text = ", ".join(sorted(disabled_models)) if disabled_models else "нет"

    text = (
        "👑 <b>Панель управления администратора</b>\n\n"
        f"👥 <b>Пользователи:</b>\n"
        f"• Всего зарегистрировано: <code>{total_users}</code>\n"
        f"• Активных за 24 часа: <code>{active_today}</code>\n\n"
        f"📈 <b>Нагрузка & Производительность:</b>\n"
        f"• Запросов за последний час: <code>{requests_hour}</code>\n"
        f"• Среднее время ответа (1ч): <code>{avg_latency:.2f} сек</code>\n\n"
        f"🗄 <b>База данных:</b>\n"
        f"• Размер файла: <code>{db_size_kb:.2f} KB</code>\n"
        f"• Путь: <code>{os.path.basename(DB_PATH)}</code>\n\n"
        f"🔑 <b>API Ключи (Gemini Pool):</b>\n"
        f"• Всего ключей: <code>{total_keys}</code>\n"
        f"• Активно: <code>{active_keys}</code>\n"
        f"• В режиме ожидания (cooldown): <code>{cooldown_keys}</code>\n\n"
        f"🚫 <b>Отключённые модели:</b> <code>{html.escape(disabled_text)}</code>\n"
        f"<i>Управление: /disable_model &lt;id&gt; и /enable_model &lt;id&gt;</i>"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "🧹 Очистить старые логи (>30 дн)", callback_data="admin_logs_cleanup"
            ),
        ],
        [
            InlineKeyboardButton(
                "⚡ Сжать БД (VACUUM)", callback_data="admin_db_optimize"
            ),
        ],
        [
            InlineKeyboardButton(
                "🔑 Статус API ключей", callback_data="admin_keys_status"
            ),
        ],
        [
            InlineKeyboardButton(
                "🏆 Топ пользователей", callback_data="admin_top_users"
            ),
        ],
        [
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
    if config.ADMIN_IDS and user_id not in config.ADMIN_IDS:
        await query.answer("⛔ Доступ запрещен.", show_alert=True)
        return

    await query.answer()
    data = query.data

    if data == "admin_db_optimize":
        start_time = time.time()
        try:
            async with get_db_connection() as db:
                await db.execute("VACUUM;")
                await db.commit()
            elapsed = time.time() - start_time
            db_size_kb = os.path.getsize(DB_PATH) / 1024.0

            # Refresh dashboard
            text, reply_markup = await get_admin_dashboard_data()
            opt_msg = (
                f"\n\n✅ <b>БД успешно оптимизирована!</b> (VACUUM за {elapsed:.2f}с).\n"
                f"Новый размер: <code>{db_size_kb:.2f} KB</code>"
            )
            await query.edit_message_text(
                text=text + opt_msg,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Error running VACUUM: {e}")
            await query.message.reply_text(f"❌ Ошибка при оптимизации БД: {e}")

    elif data == "admin_logs_cleanup":
        try:
            async with get_db_connection() as db:
                cursor = await db.execute(
                    "DELETE FROM messages WHERE timestamp < datetime('now', '-30 days');"
                )
                deleted_messages = cursor.rowcount
                cursor2 = await db.execute(
                    "DELETE FROM stats WHERE timestamp < datetime('now', '-30 days');"
                )
                deleted_stats = cursor2.rowcount
                await db.commit()

            text, reply_markup = await get_admin_dashboard_data()
            cleanup_msg = (
                f"\n\n✅ <b>Очистка завершена!</b>\n"
                f"Удалено сообщений: <code>{deleted_messages}</code>\n"
                f"Удалено логов статистики: <code>{deleted_stats}</code>"
            )
            await query.edit_message_text(
                text=text + cleanup_msg,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Error during logs cleanup: {e}")
            await query.message.reply_text(f"❌ Ошибка при очистке логов: {e}")

    elif data == "admin_keys_status":
        now = time.time()
        cooldown_statuses = []

        for i, key in enumerate(config.GOOGLE_API_KEYS):
            cooldown_until = key_pool.cooldowns.get(key, 0.0)
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
