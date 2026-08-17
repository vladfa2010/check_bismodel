"""SQLite-хранилище: одно соединение, WAL, asyncio.Lock на запись."""
import asyncio
import os
import time
import uuid

import aiosqlite

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  created_at REAL, last_seen_at REAL,
  quota_bytes INTEGER DEFAULT 209715200
);
CREATE TABLE IF NOT EXISTS chats (
  id TEXT PRIMARY KEY, user_id TEXT,
  title TEXT, extraction_json TEXT, model_spec_json TEXT,
  status TEXT DEFAULT 'dialog', created_at REAL
);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY, chat_id TEXT,
  role TEXT, content TEXT, attachments_json TEXT, created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, created_at);
CREATE TABLE IF NOT EXISTS files (
  id TEXT PRIMARY KEY, user_id TEXT, chat_id TEXT,
  orig_name TEXT, mime TEXT, size INTEGER,
  path TEXT, parsed_path TEXT, kimi_file_id TEXT,
  parse_status TEXT DEFAULT 'uploaded',   -- uploaded | ready | error
  created_at REAL
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, chat_id TEXT, type TEXT,
  status TEXT, progress INTEGER DEFAULT 0,
  result_json TEXT, error TEXT,
  created_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS reports (
  id TEXT PRIMARY KEY, chat_id TEXT, path TEXT,
  qa_json TEXT, kind TEXT DEFAULT 'simple', created_at REAL
);
"""

_conn: aiosqlite.Connection | None = None
_lock = asyncio.Lock()


async def init_db() -> None:
    global _conn
    os.makedirs(config.DATA_DIR, exist_ok=True)
    _conn = await aiosqlite.connect(os.path.join(config.DATA_DIR, "app.db"))
    _conn.row_factory = aiosqlite.Row
    await _conn.execute("PRAGMA journal_mode=WAL")
    await _conn.executescript(SCHEMA)
    await _conn.commit()


def _now() -> float:
    return time.time()


async def _exec(query: str, params: tuple = ()) -> None:
    async with _lock:
        await _conn.execute(query, params)
        await _conn.commit()


async def _fetch(query: str, params: tuple = ()) -> list[dict]:
    async with _lock:
        cur = await _conn.execute(query, params)
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ---------- users ----------
async def ensure_user(uid: str) -> None:
    await _exec(
        "INSERT OR IGNORE INTO users (id, created_at, last_seen_at) VALUES (?, ?, ?)",
        (uid, _now(), _now()),
    )
    await _exec("UPDATE users SET last_seen_at = ? WHERE id = ?", (_now(), uid))


# ---------- chats ----------
async def get_or_create_chat(uid: str, chat_id: str) -> dict:
    rows = await _fetch("SELECT * FROM chats WHERE id = ? AND user_id = ?", (chat_id, uid))
    if rows:
        return rows[0]
    await _exec(
        "INSERT INTO chats (id, user_id, title, created_at) VALUES (?, ?, ?, ?)",
        (chat_id, uid, "Новый диалог", _now()),
    )
    return (await _fetch("SELECT * FROM chats WHERE id = ?", (chat_id,)))[0]


async def add_message(chat_id: str, role: str, content: str, attachments: list) -> str:
    mid = uuid.uuid4().hex
    import json
    await _exec(
        "INSERT INTO messages (id, chat_id, role, content, attachments_json, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (mid, chat_id, role, content, json.dumps(attachments, ensure_ascii=False), _now()),
    )
    return mid


async def list_messages(chat_id: str, limit: int = 20) -> list[dict]:
    rows = await _fetch(
        "SELECT role, content FROM messages WHERE chat_id = ? ORDER BY created_at DESC LIMIT ?",
        (chat_id, limit),
    )
    return list(reversed(rows))


# ---------- files ----------
async def add_file(fid: str, uid: str, name: str, mime: str, size: int, path: str) -> None:
    await _exec(
        "INSERT INTO files (id, user_id, orig_name, mime, size, path, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (fid, uid, name, mime or "application/octet-stream", size, path, _now()),
    )


async def set_file_parsed(fid: str, kimi_file_id: str | None, parsed_path: str | None, status: str) -> None:
    await _exec(
        "UPDATE files SET kimi_file_id = ?, parsed_path = ?, parse_status = ? WHERE id = ?",
        (kimi_file_id, parsed_path, status, fid),
    )


async def list_files(uid: str) -> list[dict]:
    return await _fetch(
        "SELECT id, orig_name, size, parse_status, created_at FROM files"
        " WHERE user_id = ? ORDER BY created_at DESC",
        (uid,),
    )


async def get_file(fid: str, uid: str) -> dict | None:
    rows = await _fetch("SELECT * FROM files WHERE id = ? AND user_id = ?", (fid, uid))
    return rows[0] if rows else None


async def delete_file(fid: str, uid: str) -> dict | None:
    row = await get_file(fid, uid)
    if row:
        await _exec("DELETE FROM files WHERE id = ?", (fid,))
    return row


async def user_usage(uid: str) -> int:
    rows = await _fetch("SELECT COALESCE(SUM(size), 0) AS s FROM files WHERE user_id = ?", (uid,))
    return rows[0]["s"] if rows else 0


def get_parsed_text(row: dict) -> str | None:
    """Читает кеш распарсенного текста с диска."""
    pp = row.get("parsed_path")
    if pp and os.path.exists(pp):
        with open(pp, encoding="utf-8", errors="replace") as f:
            return f.read()
    return None
