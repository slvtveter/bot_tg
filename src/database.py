import os
import hashlib
import aiosqlite
import logging
from typing import Optional, Dict, Any, List
from contextlib import asynccontextmanager

from src import config

# Default database path
DB_PATH = os.getenv("DB_PATH", "/Users/slvtveter/Desktop/PycharmProjects/bot_tg/bot.db")

# Set up logger
logger = logging.getLogger(__name__)


def _conv_id(user_id: int) -> str:
    """
    Pseudonymous, stable identifier for a user's chat history. We store THIS in
    the messages table instead of the raw Telegram user_id, so message content
    can't be casually attributed to a person by reading the database (there's no
    name or numeric id sitting next to the text). It's a salted SHA-256 hash:
    deterministic for a given user (so conversation context, /clear and undo keep
    working) but not reversible by inspection. The salt lives in
    config.PRIVACY_SALT and must stay stable — see the note there.

    Note this is pseudonymity, not absolute anonymity: someone holding both the
    salt and a specific user_id could recompute the hash. It defeats casual/
    accidental re-identification, which is the threat model here.
    """
    digest = hashlib.sha256(f"{config.PRIVACY_SALT}:{user_id}".encode("utf-8"))
    return digest.hexdigest()


# Pseudonymous messages schema: no user_id and no FK to users, so chat content
# is decoupled from identity. The conv_id column holds _conv_id(user_id).
_MESSAGES_DDL = """CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conv_id TEXT,
    role TEXT,
    content TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)"""


async def _migrate_messages_pseudonymous(db) -> None:
    """
    Brings the messages table to the pseudonymous schema and ensures the
    users.message_count column exists. Safe to run on every startup and on both
    backends (only touches portable SQL: PRAGMA table_info, ALTER/RENAME/DROP,
    plain INSERT). A no-op once already migrated.

    On a legacy DB whose messages table still has a user_id column, the existing
    rows are anonymized in place: each user_id is replaced by its _conv_id hash,
    and users.message_count is backfilled from each user's past 'user' messages
    before the old column is dropped.
    """
    # 1. Ensure users.message_count exists (lifetime count of messages a user
    #    has sent). Added nullable with a default; backfilled below on migration.
    async with db.execute("PRAGMA table_info(users);") as cursor:
        user_cols = {row[1] for row in await cursor.fetchall()}
    if "message_count" not in user_cols:
        logger.info("Migrating users table: adding message_count column")
        await db.execute(
            "ALTER TABLE users ADD COLUMN message_count INTEGER DEFAULT 0;"
        )

    # 2. Bring messages to the conv_id schema.
    async with db.execute("PRAGMA table_info(messages);") as cursor:
        msg_cols = {row[1] for row in await cursor.fetchall()}

    if not msg_cols:
        # Fresh database: just create the table.
        await db.execute(_MESSAGES_DDL)
    elif "conv_id" not in msg_cols:
        # Legacy schema with a raw user_id: anonymize existing rows in place.
        logger.info(
            "Migrating messages table to pseudonymous conv_id "
            "(anonymizing existing chat history)..."
        )
        await db.execute("DROP TABLE IF EXISTS messages_old;")
        await db.execute("ALTER TABLE messages RENAME TO messages_old;")
        await db.execute(_MESSAGES_DDL)

        # Backfill message_count from the old rows before user_id is gone.
        async with db.execute(
            "SELECT user_id, COUNT(*) FROM messages_old "
            "WHERE role = 'user' GROUP BY user_id"
        ) as cursor:
            counts = await cursor.fetchall()
        for row in counts:
            uid, cnt = row[0], row[1]
            if uid is not None:
                await db.execute(
                    "UPDATE users SET message_count = ? WHERE user_id = ?",
                    (cnt, uid),
                )

        # Copy every message across, hashing user_id -> conv_id.
        async with db.execute(
            "SELECT id, user_id, role, content, timestamp FROM messages_old"
        ) as cursor:
            old_rows = await cursor.fetchall()
        for row in old_rows:
            rid, uid, role, content, ts = row[0], row[1], row[2], row[3], row[4]
            cid = _conv_id(uid) if uid is not None else None
            await db.execute(
                "INSERT INTO messages (id, conv_id, role, content, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (rid, cid, role, content, ts),
            )
        await db.execute("DROP TABLE messages_old;")

    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_conv_id ON messages(conv_id);"
    )


# --- Turso (libSQL) adapter -------------------------------------------------
# When TURSO_DATABASE_URL is configured the bot talks to a remote libSQL
# database instead of a local SQLite file, so data survives redeploys on hosts
# with an ephemeral filesystem. libsql-client has a different surface than
# aiosqlite, so these thin wrappers expose exactly the methods the rest of this
# module already uses (execute as both an awaitable and an async context
# manager, fetchone/fetchall/rowcount, async iteration), letting every query
# function below stay byte-for-byte identical across both backends.


class _TursoCursor:
    """Wraps a libSQL ResultSet to look like an aiosqlite cursor."""

    def __init__(self, result_set) -> None:
        self._rows = list(result_set.rows)
        self.rowcount = (
            result_set.rows_affected if result_set.rows_affected is not None else -1
        )
        self.lastrowid = result_set.last_insert_rowid
        self._idx = 0

    async def fetchone(self):
        if self._idx < len(self._rows):
            row = self._rows[self._idx]
            self._idx += 1
            return row
        return None

    async def fetchall(self):
        rows = self._rows[self._idx:]
        self._idx = len(self._rows)
        return rows

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._idx < len(self._rows):
            row = self._rows[self._idx]
            self._idx += 1
            return row
        raise StopAsyncIteration

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _TursoExec:
    """
    Return value of _TursoConnection.execute. Mirrors aiosqlite, where execute()
    is both awaitable (`cur = await db.execute(...)`) and an async context
    manager (`async with db.execute(...) as cur:`). The query runs lazily when
    awaited or entered.
    """

    def __init__(self, client, sql: str, params) -> None:
        self._client = client
        self._sql = sql
        self._params = params

    async def _run(self) -> _TursoCursor:
        args = list(self._params) if self._params else None
        result_set = await self._client.execute(self._sql, args)
        return _TursoCursor(result_set)

    def __await__(self):
        return self._run().__await__()

    async def __aenter__(self):
        self._cursor = await self._run()
        return self._cursor

    async def __aexit__(self, *exc):
        return False


class _TursoConnection:
    """aiosqlite-compatible facade over a libsql-client Client."""

    def __init__(self, client) -> None:
        self._client = client
        self.row_factory = None  # accepted for compatibility; libSQL rows are named

    def execute(self, sql: str, params=None) -> _TursoExec:
        return _TursoExec(self._client, sql, params)

    async def commit(self) -> None:
        # libsql-client autocommits each execute, so commit is a no-op.
        return None

    async def close(self) -> None:
        await self._client.close()


def _make_turso_client():
    import libsql_client

    url = config.TURSO_DATABASE_URL
    # libsql:// selects the websocket protocol; https:// uses stateless HTTP,
    # which lets one long-lived client serve every query safely.
    if url.startswith("libsql://"):
        url = "https://" + url[len("libsql://"):]
    return libsql_client.create_client(
        url, auth_token=config.TURSO_AUTH_TOKEN or None
    )


# One shared libSQL client for the process. Creating a client per query (the
# old pattern) meant a fresh TLS handshake for every single DB operation — and
# the text hot path makes ~5-6 of them per message, so that alone added
# hundreds of ms per reply on the remote backend. The stateless-HTTP client is
# safe for concurrent use; it's created lazily and recreated if closed.
_turso_client = None


def _get_turso_client():
    global _turso_client
    if _turso_client is None or _turso_client.closed:
        _turso_client = _make_turso_client()
    return _turso_client


@asynccontextmanager
async def get_db_connection(db_path: str = DB_PATH):
    """
    Asynchronous context manager that yields a configured database connection.

    Uses the remote Turso/libSQL backend when configured (durable across
    redeploys), otherwise a local SQLite file via aiosqlite with a 10s busy
    timeout and foreign keys enforced.
    """
    if config.USE_TURSO:
        # The shared client is deliberately NOT closed here — it lives for the
        # whole process (see _get_turso_client above).
        yield _TursoConnection(_get_turso_client())
        return

    conn = await aiosqlite.connect(db_path, timeout=10.0)
    try:
        await conn.execute("PRAGMA foreign_keys = ON;")
        yield conn
    finally:
        await conn.close()


async def _init_schema_turso() -> None:
    """
    Creates the final schema on the Turso/libSQL backend. No PRAGMA-based
    in-place migrations are needed: a remote Turso database is created fresh with
    the current schema, and CREATE TABLE IF NOT EXISTS is idempotent.
    """
    statements = [
        """CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            current_mode TEXT DEFAULT 'general',
            max_length TEXT DEFAULT 'medium',
            creativity TEXT DEFAULT 'balanced',
            language TEXT DEFAULT 'ru',
            message_count INTEGER DEFAULT 0,
            context_reset_id INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        _MESSAGES_DDL,
        """CREATE TABLE IF NOT EXISTS stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            model TEXT,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            latency REAL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )""",
        """CREATE TABLE IF NOT EXISTS nutrition_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            calories REAL,
            protein REAL,
            fat REAL,
            carbs REAL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )""",
        """CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            content TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )""",
        """CREATE TABLE IF NOT EXISTS search_counter (
            search_date TEXT PRIMARY KEY,
            count INTEGER DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            remind_at TEXT NOT NULL,
            text TEXT NOT NULL,
            sent INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )""",
        "CREATE INDEX IF NOT EXISTS idx_stats_user_id ON stats(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_stats_timestamp ON stats(timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_nutrition_log_user_id ON nutrition_log(user_id)",
    ]
    async with get_db_connection() as db:
        for statement in statements:
            await db.execute(statement)
        # CREATE TABLE IF NOT EXISTS does NOT add columns to a table that already
        # exists from an older schema, and the remote backend has no PRAGMA-based
        # migration path like init_db. So backfill any columns a legacy Turso DB is
        # missing. Critical: without context_reset_id, get_chat_history's lookup
        # throws and returns [] for EVERY call, so the bot loses all chat memory.
        async with db.execute("PRAGMA table_info(users)") as cursor:
            existing = {row[1] for row in await cursor.fetchall()}
        for col, ddl in (
            ("context_reset_id", "INTEGER DEFAULT 0"),
            ("message_count", "INTEGER DEFAULT 0"),
        ):
            if col not in existing:
                logger.info("Turso: backfilling users.%s column", col)
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
        # Migrate any pre-existing remote DB (created before pseudonymization)
        # to the conv_id schema + message_count column, then create its index.
        await _migrate_messages_pseudonymous(db)
        await db.commit()


async def init_db(db_path: str = DB_PATH) -> None:
    """
    Initializes the database tables:
    - users: Information about bot users and their active settings.
    - messages: Log of message history for context management.
    - stats: LLM request metrics (tokens, latency, model).

    Enforces foreign key relationships (on delete cascade) and optimizes queries via indexes.
    If the tables already exist without foreign keys, automatically performs a schema migration.
    On the Turso/libSQL backend the final schema is created directly instead.
    """
    if config.USE_TURSO:
        await _init_schema_turso()
        logger.info("Turso/libSQL database initialized (durable remote backend).")
        return

    try:
        async with get_db_connection(db_path) as db:
            await db.execute("PRAGMA journal_mode = WAL;")
            # 1. Create users table with settings columns
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    current_mode TEXT DEFAULT 'general',
                    max_length TEXT DEFAULT 'medium',
                    creativity TEXT DEFAULT 'balanced',
                    language TEXT DEFAULT 'ru',
                    message_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Schema migration: Add settings columns if they don't exist in existing DB
            async with db.execute("PRAGMA table_info(users);") as cursor:
                columns_info = await cursor.fetchall()

            existing_columns = {col[1] for col in columns_info}
            if "max_length" not in existing_columns:
                logger.info("Migrating users table: adding max_length column")
                await db.execute(
                    "ALTER TABLE users ADD COLUMN max_length TEXT DEFAULT 'medium';"
                )
            if "creativity" not in existing_columns:
                logger.info("Migrating users table: adding creativity column")
                await db.execute(
                    "ALTER TABLE users ADD COLUMN creativity TEXT DEFAULT 'balanced';"
                )
            if "language" not in existing_columns:
                logger.info("Migrating users table: adding language column")
                await db.execute(
                    "ALTER TABLE users ADD COLUMN language TEXT DEFAULT 'ru';"
                )
            if "created_at" not in existing_columns:
                # Pre-dates even the created_at column (very old legacy DBs);
                # add it nullable since SQLite can't add a CURRENT_TIMESTAMP
                # default after the fact.
                logger.info("Migrating users table: adding created_at column")
                await db.execute("ALTER TABLE users ADD COLUMN created_at TIMESTAMP;")
                existing_columns.add("created_at")
            if "last_seen" not in existing_columns:
                # SQLite can't ADD COLUMN with a CURRENT_TIMESTAMP default, so add
                # it nullable and backfill existing rows from created_at.
                logger.info("Migrating users table: adding last_seen column")
                await db.execute("ALTER TABLE users ADD COLUMN last_seen TIMESTAMP;")
                await db.execute(
                    "UPDATE users SET last_seen = created_at WHERE last_seen IS NULL;"
                )
            if "context_reset_id" not in existing_columns:
                logger.info("Migrating users table: adding context_reset_id column")
                await db.execute(
                    "ALTER TABLE users ADD COLUMN context_reset_id INTEGER DEFAULT 0;"
                )

            # 2. Bring messages to the pseudonymous (conv_id) schema and ensure
            # the users.message_count column exists. Anonymizes any legacy rows.
            await _migrate_messages_pseudonymous(db)

            # 3. Check and migrate stats table to include foreign key constraint
            async with db.execute("PRAGMA foreign_key_list(stats);") as cursor:
                stats_fks = await cursor.fetchall()

            if not stats_fks:
                # Check if stats table exists
                async with db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='stats';"
                ) as cursor:
                    has_stats = await cursor.fetchone()

                if has_stats:
                    logger.info("Migrating existing stats table to add foreign key...")
                    await db.execute("ALTER TABLE stats RENAME TO stats_old;")
                    await db.execute("""
                        CREATE TABLE stats (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            user_id INTEGER,
                            model TEXT,
                            prompt_tokens INTEGER,
                            completion_tokens INTEGER,
                            latency REAL,
                            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                        );
                    """)
                    await db.execute("PRAGMA foreign_keys = OFF;")
                    await db.execute("""
                        INSERT INTO stats (id, user_id, model, prompt_tokens, completion_tokens, latency, timestamp)
                        SELECT id, user_id, model, prompt_tokens, completion_tokens, latency, timestamp FROM stats_old;
                    """)
                    await db.execute("DROP TABLE stats_old;")
                    await db.execute("PRAGMA foreign_keys = ON;")
                else:
                    await db.execute("""
                        CREATE TABLE IF NOT EXISTS stats (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            user_id INTEGER,
                            model TEXT,
                            prompt_tokens INTEGER,
                            completion_tokens INTEGER,
                            latency REAL,
                            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                        )
                    """)

            # 4. Create nutrition_log table (per-meal calorie/macro totals, for /today reports)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS nutrition_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    calories REAL,
                    protein REAL,
                    fat REAL,
                    carbs REAL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)

            # 5. Create feedback table (user-submitted feedback, kept permanently
            # so the admin can read it even if no ADMIN_IDS are configured).
            await db.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    username TEXT,
                    content TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)

            # 6. Create search_counter table: one row per calendar day holding a
            # bot-wide count of Tavily web searches, used to cap usage against the
            # free-tier monthly quota (see src/web_search.py). New table, no FK and
            # no legacy data, so a plain CREATE IF NOT EXISTS is enough.
            await db.execute("""
                CREATE TABLE IF NOT EXISTS search_counter (
                    search_date TEXT PRIMARY KEY,
                    count INTEGER DEFAULT 0
                )
            """)

            # 8. Create reminders table for user-set one-time reminders.
            await db.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    remind_at TEXT NOT NULL,
                    text TEXT NOT NULL,
                    sent INTEGER DEFAULT 0,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
            """)

            # 7. Create Indexes if they do not exist (idx_messages_conv_id is
            # created inside _migrate_messages_pseudonymous above).
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_stats_user_id ON stats(user_id);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_stats_timestamp ON stats(timestamp);"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_nutrition_log_user_id ON nutrition_log(user_id);"
            )

            await db.commit()
            logger.info(
                "Database initialized, tables created/migrated, and indexes set up successfully."
            )
    except Exception as e:
        logger.error(f"Error during database initialization: {e}")
        raise


async def upsert_user(
    user_id: int,
    username: Optional[str],
    first_name: Optional[str],
    last_name: Optional[str],
    db_path: str = DB_PATH,
) -> None:
    """
    Inserts a user record or updates existing details (username, first_name, last_name).
    """
    try:
        async with get_db_connection(db_path) as db:
            await db.execute(
                """
                INSERT INTO users (user_id, username, first_name, last_name, last_seen)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    last_name = excluded.last_name,
                    last_seen = CURRENT_TIMESTAMP
            """,
                (user_id, username, first_name, last_name),
            )
            await db.commit()
            logger.info(f"User {user_id} upserted successfully.")
    except Exception as e:
        logger.error(f"Error upserting user {user_id}: {e}")
        raise


async def set_user_mode(user_id: int, mode: str, db_path: str = DB_PATH) -> None:
    """
    Sets the user's active mode ('general', 'math', 'nutrition').
    If the user doesn't exist, they are created with default/empty fields first.
    Optimized to use a single atomic UPSERT statement.
    """
    try:
        async with get_db_connection(db_path) as db:
            await db.execute(
                """
                INSERT INTO users (user_id, current_mode)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET current_mode = excluded.current_mode
            """,
                (user_id, mode),
            )
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
        async with get_db_connection(db_path) as db:
            async with db.execute(
                "SELECT current_mode FROM users WHERE user_id = ?", (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return row[0]
                return "general"
    except Exception as e:
        logger.warning(
            f"Error getting mode for user {user_id}, defaulting to 'general': {e}"
        )
        return "general"


async def get_user_language(user_id: int, db_path: str = DB_PATH) -> str:
    """
    Returns the user's interface/reply language ('ru' or 'en'), defaulting to 'ru'.
    """
    try:
        async with get_db_connection(db_path) as db:
            async with db.execute(
                "SELECT language FROM users WHERE user_id = ?", (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row and row[0] == "en":
                    return "en"
                return "ru"
    except Exception as e:
        logger.warning(f"Error getting language for user {user_id}: {e}")
        return "ru"


async def get_user_settings(user_id: int, db_path: str = DB_PATH) -> Dict[str, str]:
    """
    Retrieves the settings (max_length, creativity, language) of a user.
    """
    default_settings = {
        "max_length": "medium",
        "creativity": "balanced",
        "language": "ru",
    }
    try:
        async with get_db_connection(db_path) as db:
            async with db.execute(
                "SELECT max_length, creativity, language FROM users WHERE user_id = ?",
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return {
                        "max_length": row[0] or "medium",
                        "creativity": row[1] or "balanced",
                        "language": row[2] or "ru",
                    }
                return default_settings
    except Exception as e:
        logger.warning(f"Error getting settings for user {user_id}: {e}")
        return default_settings


async def get_user_context(user_id: int, db_path: str = DB_PATH) -> Dict[str, str]:
    """
    Single-query fetch of everything the message hot path needs about a user:
    current mode plus settings (max_length, creativity, language). Replaces a
    separate get_user_mode + get_user_settings pair with one round trip, which
    matters on the remote Turso backend where each query is an HTTP request.
    Falls back to defaults on any error or missing row.
    """
    defaults = {
        "mode": "general",
        "max_length": "medium",
        "creativity": "balanced",
        "language": "ru",
    }
    try:
        async with get_db_connection(db_path) as db:
            async with db.execute(
                "SELECT current_mode, max_length, creativity, language "
                "FROM users WHERE user_id = ?",
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return {
                        "mode": row[0] or "general",
                        "max_length": row[1] or "medium",
                        "creativity": row[2] or "balanced",
                        "language": row[3] or "ru",
                    }
                return defaults
    except Exception as e:
        logger.warning(f"Error getting user context for {user_id}: {e}")
        return defaults


async def set_user_setting(
    user_id: int, setting_name: str, setting_value: str, db_path: str = DB_PATH
) -> None:
    """
    Updates a specific setting (max_length, creativity, or language) of a user.
    """
    if setting_name not in ("max_length", "creativity", "language"):
        raise ValueError(f"Invalid setting name: {setting_name}")
    try:
        async with get_db_connection(db_path) as db:
            query = f"""
                INSERT INTO users (user_id, {setting_name})
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET {setting_name} = excluded.{setting_name}
            """
            await db.execute(query, (user_id, setting_value))
            await db.commit()
            logger.info(
                f"Setting '{setting_name}' set to '{setting_value}' for user {user_id}."
            )
    except Exception as e:
        logger.error(f"Error setting '{setting_name}' for user {user_id}: {e}")
        raise


async def get_user_activity_summary(user_id: int, db_path: str = DB_PATH) -> Dict[str, Any]:
    """
    Returns lifetime activity counters for a user. Request count comes from the
    stats table and meals from nutrition_log - both are never auto-pruned, so
    these totals are permanent and survive any chat-history cleanup.
    """
    try:
        async with get_db_connection(db_path) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM stats WHERE user_id = ?",
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()
                request_count = row[0] if row else 0

            async with db.execute(
                "SELECT COUNT(*) FROM nutrition_log WHERE user_id = ?",
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()
                meals_analyzed = row[0] if row else 0

            async with db.execute(
                "SELECT created_at, last_seen, message_count FROM users WHERE user_id = ?",
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()
                member_since = row[0] if row else None
                last_seen = row[1] if row else None
                messages_sent = (row[2] if row and row[2] is not None else 0)

            return {
                "request_count": request_count,
                "meals_analyzed": meals_analyzed,
                "messages_sent": messages_sent,
                "member_since": member_since,
                "last_seen": last_seen,
            }
    except Exception as e:
        logger.error(f"Error getting activity summary for user {user_id}: {e}")
        return {
            "request_count": 0,
            "meals_analyzed": 0,
            "messages_sent": 0,
            "member_since": None,
            "last_seen": None,
        }


async def log_message(
    user_id: int, role: str, content: str, db_path: str = DB_PATH
) -> None:
    """
    Saves a chat message (user prompt or bot response) to the database history.

    The message is stored under the pseudonymous _conv_id(user_id) rather than
    the raw user_id, so chat content isn't directly attributable to a person.
    Each 'user'-role message also bumps the lifetime users.message_count (the
    bot's reply isn't counted — this is "messages the user wrote").
    """
    try:
        async with get_db_connection(db_path) as db:
            await db.execute(
                """
                INSERT INTO messages (conv_id, role, content)
                VALUES (?, ?, ?)
            """,
                (_conv_id(user_id), role, content),
            )
            if role == "user":
                await db.execute(
                    "UPDATE users SET message_count = COALESCE(message_count, 0) + 1 "
                    "WHERE user_id = ?",
                    (user_id,),
                )
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
    db_path: str = DB_PATH,
) -> None:
    """
    Logs LLM token usage and latency metrics.
    """
    try:
        async with get_db_connection(db_path) as db:
            await db.execute(
                """
                INSERT INTO stats (user_id, model, prompt_tokens, completion_tokens, latency)
                VALUES (?, ?, ?, ?, ?)
            """,
                (user_id, model, prompt_tokens, completion_tokens, latency),
            )
            await db.commit()
            logger.info(f"Usage stats logged for user {user_id} ({model}).")
    except Exception as e:
        logger.error(f"Error logging usage stats for user {user_id}: {e}")
        raise


async def get_usage_stats(
    user_id: Optional[int] = None, db_path: str = DB_PATH
) -> Dict[str, Any]:
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
        async with get_db_connection(db_path) as db:
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
                    "model_stats": {},
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
                        "total_tokens": (row["prompt_tokens"] or 0)
                        + (row["completion_tokens"] or 0),
                        "avg_latency": row["avg_latency"] or 0.0,
                    }

            return {
                "total_requests": total_requests,
                "total_prompt_tokens": total_prompt,
                "total_completion_tokens": total_completion,
                "total_tokens": total_prompt + total_completion,
                "avg_latency": avg_latency,
                "model_stats": model_stats,
            }
    except Exception as e:
        logger.error(f"Error retrieving usage stats: {e}")
        return {
            "total_requests": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "total_tokens": 0,
            "avg_latency": 0.0,
            "model_stats": {},
        }


async def clear_chat_history(user_id: int, db_path: str = DB_PATH) -> None:
    """
    Deletes all messages for a user from the messages table.
    """
    try:
        async with get_db_connection(db_path) as db:
            await db.execute("DELETE FROM messages WHERE conv_id = ?", (_conv_id(user_id),))
            await db.commit()
            logger.info(f"Chat history cleared for user {user_id}.")
    except Exception as e:
        logger.error(f"Error clearing chat history for user {user_id}: {e}")
        raise


async def reset_chat_context(user_id: int, db_path: str = DB_PATH) -> None:
    """Sets context_reset_id to the current max message id for this user.
    Messages before this point are invisible to the model but stay in the DB."""
    try:
        async with get_db_connection(db_path) as db:
            async with db.execute(
                "SELECT COALESCE(MAX(id), 0) FROM messages WHERE conv_id = ?",
                (_conv_id(user_id),),
            ) as cursor:
                row = await cursor.fetchone()
            max_id = row[0] if row else 0
            await db.execute(
                "UPDATE users SET context_reset_id = ? WHERE user_id = ?",
                (max_id, user_id),
            )
            await db.commit()
    except Exception as e:
        logger.error(f"Error resetting chat context for user {user_id}: {e}")


async def get_chat_history(
    user_id: int, limit: int = 15, db_path: str = DB_PATH
) -> List[Dict[str, str]]:
    """
    Queries the last N messages for a user (ordered chronologically)
    formatted as a list of dicts with 'role' and 'content'.
    Only returns messages after the last context reset (context_reset_id).
    Uses the idx_messages_conv_id index to optimize retrieval.
    """
    try:
        async with get_db_connection(db_path) as db:
            # The context_reset_id lookup is best-effort: on a legacy schema where
            # the column is missing this query throws, but losing the reset filter
            # must NOT cost us the whole history (that silently wiped all chat
            # memory in production). Fall back to reset_id=0 and still return turns.
            reset_id = 0
            try:
                async with db.execute(
                    "SELECT COALESCE(context_reset_id, 0) FROM users WHERE user_id = ?",
                    (user_id,),
                ) as cursor:
                    row = await cursor.fetchone()
                reset_id = row[0] if row else 0
            except Exception as e:
                logger.warning("context_reset_id lookup failed (%s); using 0", e)
            async with db.execute(
                """
                SELECT role, content FROM (
                    SELECT id, role, content FROM messages
                    WHERE conv_id = ? AND id > ?
                    ORDER BY id DESC
                    LIMIT ?
                ) ORDER BY id ASC
            """,
                (_conv_id(user_id), reset_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()
                return [{"role": row[0], "content": row[1]} for row in rows]
    except Exception as e:
        logger.error(f"Error getting chat history for user {user_id}: {e}")
        return []


async def log_nutrition_entry(
    user_id: int,
    calories: float,
    protein: float,
    fat: float,
    carbs: float,
    db_path: str = DB_PATH,
) -> None:
    """
    Logs the parsed macro totals for a single meal analyzed by the nutrition agent.
    """
    try:
        async with get_db_connection(db_path) as db:
            await db.execute(
                """
                INSERT INTO nutrition_log (user_id, calories, protein, fat, carbs)
                VALUES (?, ?, ?, ?, ?)
            """,
                (user_id, calories, protein, fat, carbs),
            )
            await db.commit()
            logger.info(f"Nutrition entry logged for user {user_id}: {calories} kcal.")
    except Exception as e:
        logger.error(f"Error logging nutrition entry for user {user_id}: {e}")
        raise


async def get_today_nutrition_totals(user_id: int, db_path: str = DB_PATH) -> Dict[str, Any]:
    """
    Sums calories/protein/fat/carbs logged today (local server date) for a user.
    """
    try:
        async with get_db_connection(db_path) as db:
            async with db.execute(
                """
                SELECT COUNT(*), SUM(calories), SUM(protein), SUM(fat), SUM(carbs)
                FROM nutrition_log
                WHERE user_id = ? AND date(timestamp) = date('now')
            """,
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()
            return {
                "entries": row[0] or 0,
                "calories": row[1] or 0.0,
                "protein": row[2] or 0.0,
                "fat": row[3] or 0.0,
                "carbs": row[4] or 0.0,
            }
    except Exception as e:
        logger.error(f"Error getting today's nutrition totals for user {user_id}: {e}")
        return {"entries": 0, "calories": 0.0, "protein": 0.0, "fat": 0.0, "carbs": 0.0}


async def get_today_search_count(db_path: str = DB_PATH) -> int:
    """
    Returns how many Tavily web searches the bot has made today (bot-wide), used
    to enforce the daily free-tier cap. Returns 0 if no searches yet today, or on
    any error (fail-open: never block the request because the counter is unreadable).
    """
    try:
        async with get_db_connection(db_path) as db:
            async with db.execute(
                "SELECT count FROM search_counter WHERE search_date = date('now')"
            ) as cursor:
                row = await cursor.fetchone()
            return row[0] if row else 0
    except Exception as e:
        logger.error(f"Error reading today's search count: {e}")
        return 0


async def increment_search_count(db_path: str = DB_PATH) -> None:
    """
    Atomically bumps today's bot-wide web-search counter by one (UPSERT keyed on
    the calendar date). Called only when a search actually fires.
    """
    try:
        async with get_db_connection(db_path) as db:
            await db.execute(
                """
                INSERT INTO search_counter (search_date, count)
                VALUES (date('now'), 1)
                ON CONFLICT(search_date) DO UPDATE SET count = count + 1
            """
            )
            await db.commit()
    except Exception as e:
        logger.error(f"Error incrementing search count: {e}")


async def get_all_user_ids(db_path: str = DB_PATH) -> List[int]:
    """
    Returns the user_id of every registered user, for admin broadcast purposes.
    """
    try:
        async with get_db_connection(db_path) as db:
            async with db.execute("SELECT user_id FROM users") as cursor:
                rows = await cursor.fetchall()
                return [row[0] for row in rows]
    except Exception as e:
        logger.error(f"Error retrieving all user ids: {e}")
        return []


async def get_week_nutrition_totals(user_id: int, db_path: str = DB_PATH) -> Dict[str, Any]:
    """
    Sums calories/protein/fat/carbs logged over the last 7 days for a user,
    plus the number of distinct days that have entries (for a daily average).
    """
    try:
        async with get_db_connection(db_path) as db:
            async with db.execute(
                """
                SELECT COUNT(*),
                       COUNT(DISTINCT date(timestamp)),
                       SUM(calories), SUM(protein), SUM(fat), SUM(carbs)
                FROM nutrition_log
                WHERE user_id = ? AND timestamp >= datetime('now', '-7 days')
                """,
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()
            return {
                "entries": row[0] or 0,
                "days": row[1] or 0,
                "calories": row[2] or 0.0,
                "protein": row[3] or 0.0,
                "fat": row[4] or 0.0,
                "carbs": row[5] or 0.0,
            }
    except Exception as e:
        logger.error(f"Error getting weekly nutrition totals for user {user_id}: {e}")
        return {"entries": 0, "days": 0, "calories": 0.0, "protein": 0.0, "fat": 0.0, "carbs": 0.0}


async def add_feedback(
    user_id: int, username: Optional[str], content: str, db_path: str = DB_PATH
) -> None:
    """Stores a user feedback message permanently."""
    try:
        async with get_db_connection(db_path) as db:
            await db.execute(
                "INSERT INTO feedback (user_id, username, content) VALUES (?, ?, ?)",
                (user_id, username, content),
            )
            await db.commit()
            logger.info(f"Feedback stored from user {user_id}.")
    except Exception as e:
        logger.error(f"Error storing feedback from user {user_id}: {e}")
        raise


async def get_recent_feedback(
    limit: int = 10, db_path: str = DB_PATH
) -> List[Dict[str, Any]]:
    """Returns the most recent feedback entries (newest first)."""
    try:
        async with get_db_connection(db_path) as db:
            async with db.execute(
                "SELECT user_id, username, content, timestamp FROM feedback "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    {
                        "user_id": row[0],
                        "username": row[1],
                        "content": row[2],
                        "timestamp": row[3],
                    }
                    for row in rows
                ]
    except Exception as e:
        logger.error(f"Error retrieving feedback: {e}")
        return []


async def get_admin_overview(db_path: str = DB_PATH) -> Dict[str, Any]:
    """
    All-time and recent metrics for the admin dashboard. Every count is read
    from tables that are never auto-pruned (users, stats, nutrition_log,
    feedback), so growth can be tracked accurately over the whole lifetime
    of the bot.
    """
    overview: Dict[str, Any] = {
        "total_users": 0,
        "new_today": 0,
        "new_7d": 0,
        "active_24h": 0,
        "active_7d": 0,
        "total_requests": 0,
        "requests_today": 0,
        "requests_hour": 0,
        "avg_latency_hour": 0.0,
        "total_meals": 0,
        "feedback_count": 0,
    }
    count_queries = {
        "total_users": "SELECT COUNT(*) FROM users",
        "new_today": "SELECT COUNT(*) FROM users WHERE date(created_at) = date('now')",
        "new_7d": "SELECT COUNT(*) FROM users WHERE created_at >= datetime('now', '-7 days')",
        "active_24h": "SELECT COUNT(DISTINCT user_id) FROM stats WHERE timestamp >= datetime('now', '-1 day')",
        "active_7d": "SELECT COUNT(DISTINCT user_id) FROM stats WHERE timestamp >= datetime('now', '-7 days')",
        "total_requests": "SELECT COUNT(*) FROM stats",
        "requests_today": "SELECT COUNT(*) FROM stats WHERE date(timestamp) = date('now')",
        "requests_hour": "SELECT COUNT(*) FROM stats WHERE timestamp >= datetime('now', '-1 hour')",
        "total_meals": "SELECT COUNT(*) FROM nutrition_log",
        "feedback_count": "SELECT COUNT(*) FROM feedback",
    }
    try:
        async with get_db_connection(db_path) as db:
            for key, query in count_queries.items():
                async with db.execute(query) as cursor:
                    row = await cursor.fetchone()
                    overview[key] = row[0] if row and row[0] is not None else 0

            async with db.execute(
                "SELECT AVG(latency) FROM stats WHERE timestamp >= datetime('now', '-1 hour')"
            ) as cursor:
                row = await cursor.fetchone()
                overview["avg_latency_hour"] = (
                    row[0] if row and row[0] is not None else 0.0
                )
    except Exception as e:
        logger.error(f"Error building admin overview: {e}")
    return overview


async def add_reminder(
    user_id: int, remind_at: str, text: str, db_path: str = DB_PATH
) -> None:
    """Stores a one-time reminder. remind_at is ISO datetime string in UTC."""
    try:
        async with get_db_connection(db_path) as db:
            await db.execute(
                "INSERT INTO reminders (user_id, remind_at, text) VALUES (?, ?, ?)",
                (user_id, remind_at, text),
            )
            await db.commit()
    except Exception as e:
        logger.error(f"Error adding reminder for user {user_id}: {e}")
        raise


async def get_pending_reminders(db_path: str = DB_PATH) -> List[Dict]:
    """Returns unsent reminders whose remind_at <= current UTC time."""
    try:
        async with get_db_connection(db_path) as db:
            async with db.execute(
                """
                SELECT id, user_id, text FROM reminders
                WHERE sent = 0 AND remind_at <= datetime('now')
                """,
            ) as cursor:
                rows = await cursor.fetchall()
        return [{"id": r[0], "user_id": r[1], "text": r[2]} for r in rows]
    except Exception as e:
        logger.error(f"Error fetching pending reminders: {e}")
        return []


async def mark_reminder_sent(reminder_id: int, db_path: str = DB_PATH) -> None:
    """Marks a reminder as sent so it won't fire again."""
    try:
        async with get_db_connection(db_path) as db:
            await db.execute(
                "UPDATE reminders SET sent = 1 WHERE id = ?", (reminder_id,)
            )
            await db.commit()
    except Exception as e:
        logger.error(f"Error marking reminder {reminder_id} as sent: {e}")
