"""
Turso (libSQL) MCP server for the Nela AI bot — a small READ-ONLY window into the
production database, so an assistant (Claude Code) can inspect real conversations,
the bot's answers, usage stats and the nutrition diary while debugging. Without
this, the DB on Turso is a blind spot (it's remote, and the dev machine is
geo-blocked from the bot's LLM APIs anyway).

Read-only by design: the only SQL it runs is SELECT (anything else is rejected).
Connects to the same Turso DB the bot writes to, using TURSO_DATABASE_URL /
TURSO_AUTH_TOKEN from the environment (loaded from .env). It's project-local:
registered in the repo's .mcp.json. Run it: `python -m mcp_server.turso_mcp`
(needs `pip install -r requirements-dev.txt`).

Tools:
  - list_tables()              tables + row counts
  - table_schema(table)        column definitions
  - recent_messages(limit)     latest chat turns across all users (pseudonymous)
  - conversation(user_id, ...) one user's turns in order — hashes user_id to the
                               same conv_id the bot stores (needs PRIVACY_SALT to
                               match production)
  - recent_stats(limit)        per-request model / tokens / latency
  - read_sql(query)            arbitrary read-only SELECT
"""

import hashlib
import json
import os
from typing import Any, List, Optional

import libsql_client
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

TURSO_URL = os.getenv("TURSO_DATABASE_URL", "").strip()
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "").strip()
PRIVACY_SALT = os.getenv("PRIVACY_SALT", "nela-ai-default-privacy-salt-v1")

mcp = FastMCP("turso-nela")


def _conv_id(user_id: int) -> str:
    """Same salted SHA-256 the bot stores in messages.conv_id (src.database._conv_id)."""
    return hashlib.sha256(f"{PRIVACY_SALT}:{user_id}".encode("utf-8")).hexdigest()


def _connect():
    if not TURSO_URL:
        raise RuntimeError(
            "TURSO_DATABASE_URL is not set. Add TURSO_DATABASE_URL and "
            "TURSO_AUTH_TOKEN to .env (copy them from Render → service → Environment)."
        )
    url = TURSO_URL
    if url.startswith("libsql://"):  # https:// = stateless HTTP, matches the bot
        url = "https://" + url[len("libsql://"):]
    return libsql_client.create_client(url, auth_token=TURSO_TOKEN or None)


async def _rows(sql: str, args: Optional[List[Any]] = None) -> List[dict]:
    client = _connect()
    try:
        rs = await client.execute(sql, args or [])
        return [{c: row[i] for i, c in enumerate(rs.columns)} for row in rs.rows]
    finally:
        await client.close()


def _dump(rows: List[dict]) -> str:
    if not rows:
        return "(no rows)"
    return json.dumps(rows, ensure_ascii=False, indent=2, default=str)


@mcp.tool()
async def list_tables() -> str:
    """List the database tables with their row counts."""
    tables = await _rows(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    out = []
    for t in tables:
        name = t["name"]
        cnt = await _rows(f"SELECT COUNT(*) AS n FROM {name}")
        out.append({"table": name, "rows": cnt[0]["n"] if cnt else 0})
    return _dump(out)


@mcp.tool()
async def table_schema(table: str) -> str:
    """Show the column definitions for one table."""
    if not table.isidentifier():
        return "Invalid table name."
    return _dump(await _rows(f"PRAGMA table_info({table})"))


@mcp.tool()
async def recent_messages(limit: int = 20) -> str:
    """Most recent chat turns across all users (content truncated). Pseudonymous:
    shows conv_id, not a user id."""
    limit = max(1, min(limit, 200))
    return _dump(
        await _rows(
            "SELECT conv_id, role, substr(content, 1, 800) AS content, timestamp "
            "FROM messages ORDER BY id DESC LIMIT ?",
            [limit],
        )
    )


@mcp.tool()
async def conversation(user_id: int, limit: int = 40) -> str:
    """One user's chat history in chronological order — exactly the turns the bot
    sees as context. Hashes user_id to conv_id the way the bot does, so this only
    matches if PRIVACY_SALT here equals production's."""
    limit = max(1, min(limit, 200))
    rows = await _rows(
        "SELECT role, content, timestamp FROM messages WHERE conv_id = ? "
        "ORDER BY id DESC LIMIT ?",
        [_conv_id(user_id), limit],
    )
    rows.reverse()  # back to chronological
    return _dump(rows)


@mcp.tool()
async def recent_stats(limit: int = 20) -> str:
    """Most recent per-request telemetry: model, tokens, latency, timestamp."""
    limit = max(1, min(limit, 200))
    return _dump(
        await _rows(
            "SELECT user_id, model, prompt_tokens, completion_tokens, latency, "
            "timestamp FROM stats ORDER BY id DESC LIMIT ?",
            [limit],
        )
    )


@mcp.tool()
async def read_sql(query: str) -> str:
    """Run an arbitrary READ-ONLY SQL query (must be a single SELECT). Rejects
    anything that could modify data."""
    q = query.strip().rstrip(";").strip()
    if not q.lower().startswith("select"):
        return "Only single SELECT statements are allowed."
    if ";" in q:
        return "Only a single statement is allowed (no ';')."
    return _dump(await _rows(q))


if __name__ == "__main__":
    mcp.run()
