import os
import aiosqlite
import logging
from typing import Optional, Dict, Any, List

# Default database path
DB_PATH = os.getenv("DB_PATH", "/Users/slvtveter/Desktop/PycharmProjects/bot_tg/bot.db")

# Set up logger
logger = logging.getLogger(__name__)


async def init_db(db_path: str = DB_PATH) -> None:
    """
    Initializes the database tables:
    - users: Information about bot users and their active settings.
    - messages: Log of message history for context management.
    - stats: LLM request metrics (tokens, latency, model).
    """
    try:
        async with aiosqlite.connect(db_path) as db:
            # Create users table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    current_mode TEXT DEFAULT 'general',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create messages table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    role TEXT,
                    content TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create stats table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    model TEXT,
                    prompt_tokens INTEGER,
                    completion_tokens INTEGER,
                    latency REAL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.commit()
            logger.info("Database initialized and tables created successfully.")
    except Exception as e:
        logger.error(f"Error during database initialization: {e}")
        raise


async def upsert_user(
    user_id: int,
    username: Optional[str],
    first_name: Optional[str],
    last_name: Optional[str],
    db_path: str = DB_PATH
) -> None:
    """
    Inserts a user record or updates existing details (username, first_name, last_name).
    """
    try:
        async with aiosqlite.connect(db_path) as db:
            await db.execute("""
                INSERT INTO users (user_id, username, first_name, last_name)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    last_name = excluded.last_name
            """, (user_id, username, first_name, last_name))
            await db.commit()
            logger.info(f"User {user_id} upserted successfully.")
    except Exception as e:
        logger.error(f"Error upserting user {user_id}: {e}")
        raise


async def set_user_mode(user_id: int, mode: str, db_path: str = DB_PATH) -> None:
    """
    Sets the user's active mode ('general', 'math', 'nutrition').
    If the user doesn't exist, they are created with default/empty fields first.
    """
    try:
        async with aiosqlite.connect(db_path) as db:
            # Attempt to update the mode
            async with db.execute(
                "UPDATE users SET current_mode = ? WHERE user_id = ?",
                (mode, user_id)
            ) as cursor:
                # If no row was updated, insert a new user with this mode
                if cursor.rowcount == 0:
                    await db.execute("""
                        INSERT INTO users (user_id, current_mode)
                        VALUES (?, ?)
                        ON CONFLICT(user_id) DO UPDATE SET current_mode = excluded.current_mode
                    """, (user_id, mode))
            await db.commit()
            logger.info(f"Mode set to '{mode}' for user {user_id}.")
    except Exception as e:
        logger.error(f"Error setting mode for user {user_id}: {e}")
        raise


async def get_user_mode(user_id: int, db_path: str = DB_PATH) -> str:
    """
    Retrieves the active mode of a user.
    Returns 'general' if the user is not found or an error occurs.
    """
    try:
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                "SELECT current_mode FROM users WHERE user_id = ?",
                (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return row[0]
                return 'general'
    except Exception as e:
        logger.warning(f"Error getting mode for user {user_id}, defaulting to 'general': {e}")
        return 'general'


async def log_message(user_id: int, role: str, content: str, db_path: str = DB_PATH) -> None:
    """
    Saves a chat message (user prompt or bot response) to the database history.
    """
    try:
        async with aiosqlite.connect(db_path) as db:
            await db.execute("""
                INSERT INTO messages (user_id, role, content)
                VALUES (?, ?, ?)
            """, (user_id, role, content))
            await db.commit()
            logger.info(f"Message logged for user {user_id}.")
    except Exception as e:
        logger.error(f"Error logging message for user {user_id}: {e}")
        raise


async def log_usage_stats(
    user_id: int,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency: float,
    db_path: str = DB_PATH
) -> None:
    """
    Logs LLM token usage and latency metrics.
    """
    try:
        async with aiosqlite.connect(db_path) as db:
            await db.execute("""
                INSERT INTO stats (user_id, model, prompt_tokens, completion_tokens, latency)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, model, prompt_tokens, completion_tokens, latency))
            await db.commit()
            logger.info(f"Usage stats logged for user {user_id} ({model}).")
    except Exception as e:
        logger.error(f"Error logging usage stats for user {user_id}: {e}")
        raise


async def get_usage_stats(user_id: Optional[int] = None, db_path: str = DB_PATH) -> Dict[str, Any]:
    """
    Retrieves statistics. If user_id is provided, returns stats for that specific user.
    Otherwise, returns aggregate statistics across all users.
    
    Returns a dictionary of:
    - total_requests: Total number of requests.
    - total_prompt_tokens: Cumulative prompt tokens.
    - total_completion_tokens: Cumulative completion tokens.
    - total_tokens: Sum of prompt and completion tokens.
    - avg_latency: Average request latency.
    - model_stats: Breakdown by model.
    """
    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row

            total_query = """
                SELECT 
                    COUNT(*) as total_requests,
                    SUM(prompt_tokens) as total_prompt_tokens,
                    SUM(completion_tokens) as total_completion_tokens,
                    AVG(latency) as avg_latency
                FROM stats
            """
            
            model_query = """
                SELECT 
                    model,
                    COUNT(*) as requests,
                    SUM(prompt_tokens) as prompt_tokens,
                    SUM(completion_tokens) as completion_tokens,
                    AVG(latency) as avg_latency
                FROM stats
            """
            
            params = []
            if user_id is not None:
                total_query += " WHERE user_id = ?"
                model_query += " WHERE user_id = ?"
                params.append(user_id)
                
            model_query += " GROUP BY model"

            async with db.execute(total_query, params) as cursor:
                total_row = await cursor.fetchone()

            if not total_row or total_row["total_requests"] == 0:
                return {
                    "total_requests": 0,
                    "total_prompt_tokens": 0,
                    "total_completion_tokens": 0,
                    "total_tokens": 0,
                    "avg_latency": 0.0,
                    "model_stats": {}
                }

            total_requests = total_row["total_requests"]
            total_prompt = total_row["total_prompt_tokens"] or 0
            total_completion = total_row["total_completion_tokens"] or 0
            avg_latency = total_row["avg_latency"] or 0.0

            model_stats = {}
            async with db.execute(model_query, params) as cursor:
                async for row in cursor:
                    m_name = row["model"] or "unknown"
                    model_stats[m_name] = {
                        "requests": row["requests"],
                        "prompt_tokens": row["prompt_tokens"] or 0,
                        "completion_tokens": row["completion_tokens"] or 0,
                        "total_tokens": (row["prompt_tokens"] or 0) + (row["completion_tokens"] or 0),
                        "avg_latency": row["avg_latency"] or 0.0
                    }

            return {
                "total_requests": total_requests,
                "total_prompt_tokens": total_prompt,
                "total_completion_tokens": total_completion,
                "total_tokens": total_prompt + total_completion,
                "avg_latency": avg_latency,
                "model_stats": model_stats
            }
    except Exception as e:
        logger.error(f"Error retrieving usage stats: {e}")
        return {
            "total_requests": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "total_tokens": 0,
            "avg_latency": 0.0,
            "model_stats": {}
        }


async def clear_chat_history(user_id: int, db_path: str = DB_PATH) -> None:
    """
    Deletes all messages for a user from the messages table.
    """
    try:
        async with aiosqlite.connect(db_path) as db:
            await db.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
            await db.commit()
            logger.info(f"Chat history cleared for user {user_id}.")
    except Exception as e:
        logger.error(f"Error clearing chat history for user {user_id}: {e}")
        raise


async def get_chat_history(user_id: int, limit: int = 15, db_path: str = DB_PATH) -> List[Dict[str, str]]:
    """
    Queries the last N messages for a user (ordered chronologically)
    formatted as a list of dicts with 'role' and 'content'.
    """
    try:
        async with aiosqlite.connect(db_path) as db:
            async with db.execute("""
                SELECT role, content FROM (
                    SELECT id, role, content FROM messages 
                    WHERE user_id = ? 
                    ORDER BY id DESC 
                    LIMIT ?
                ) ORDER BY id ASC
            """, (user_id, limit)) as cursor:
                rows = await cursor.fetchall()
                return [{"role": row[0], "content": row[1]} for row in rows]
    except Exception as e:
        logger.error(f"Error getting chat history for user {user_id}: {e}")
        return []

