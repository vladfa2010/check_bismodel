"""SQLite-хранилище: одно соединение, WAL, asyncio.Lock на запись.

Миграции — примитивные: CREATE TABLE IF NOT EXISTS + ALTER TABLE ADD COLUMN
для новых колонок (dev-стадия, продакшн-данных ещё нет).
"""
import asyncio
import json
import os
import time
import uuid

import aiosqlite

from . import config
from .security import hash_password

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  username TEXT,               -- NULL = анонимный (legacy)
  password_hash TEXT,
  created_at REAL, last_seen_at REAL,
  quota_bytes INTEGER DEFAULT 209715200
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username);
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
  parse_status TEXT DEFAULT 'uploaded',   -- uploaded | processing | ready | error
  deleted_at REAL,                        -- мягкое удаление (корзина)
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
CREATE TABLE IF NOT EXISTS usage_events (
  id TEXT PRIMARY KEY, user_id TEXT, chat_id TEXT,
  kind TEXT,                 -- chat | extract | build
  model TEXT,
  prompt_tokens INTEGER, completion_tokens INTEGER, total_tokens INTEGER,
  created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_usage_user ON usage_events(user_id, created_at);
"""

# Колонки, появившиеся после первой версии: (таблица, колонка, DDL)
MIGRATIONS = [
    ("users", "username", "username TEXT"),
    ("users", "password_hash", "password_hash TEXT"),
    ("files", "deleted_at", "deleted_at REAL"),
    ("chats", "pinned", "pinned INTEGER NOT NULL DEFAULT 0"),
    ("chats", "model", "model TEXT"),
]

_conn: aiosqlite.Connection | None = None
_lock = asyncio.Lock()


async def init_db() -> None:
    global _conn
    os.makedirs(config.DATA_DIR, exist_ok=True)
    _conn = await aiosqlite.connect(os.path.join(config.DATA_DIR, "app.db"))
    _conn.row_factory = aiosqlite.Row
    await _conn.execute("PRAGMA journal_mode=WAL")
    await _conn.executescript(SCHEMA)
    for table, column, ddl in MIGRATIONS:
        cols = [r["name"] for r in await _fetch(f"PRAGMA table_info({table})")]
        if column not in cols:
            await _exec(f"ALTER TABLE {table} ADD COLUMN {ddl}")
    await _conn.commit()


async def close_db() -> None:
    """Закрывает соединение (иначе фоновый поток aiosqlite не даёт процессу завершиться)."""
    global _conn
    if _conn is not None:
        await _conn.close()
        _conn = None


def _now() -> float:
    return time.time()


async def _exec(query: str, params: tuple = ()):
    async with _lock:
        cur = await _conn.execute(query, params)
        await _conn.commit()
        return cur


async def _fetch(query: str, params: tuple = ()) -> list[dict]:
    async with _lock:
        cur = await _conn.execute(query, params)
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ---------- users / auth ----------
async def seed_users() -> None:
    """Создаёт предустановленных пользователей из конфига (идемпотентно)."""
    for username, password in config.SEED_USERS.items():
        rows = await _fetch("SELECT id FROM users WHERE username = ?", (username,))
        if not rows:
            await _exec(
                "INSERT INTO users (id, username, password_hash, created_at, last_seen_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, username, hash_password(password), _now(), _now()),
            )


async def get_user(uid: str) -> dict | None:
    rows = await _fetch(
        "SELECT id, username, created_at, last_seen_at, quota_bytes FROM users WHERE id = ?",
        (uid,),
    )
    return rows[0] if rows else None


async def get_user_by_username(username: str) -> dict | None:
    rows = await _fetch("SELECT * FROM users WHERE username = ?", (username,))
    return rows[0] if rows else None


async def touch_user(uid: str) -> None:
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


async def list_chats(uid: str) -> list[dict]:
    """Диалоги юзера, свежие сверху; last_at — по последнему сообщению."""
    return await _fetch(
        """
        SELECT c.id, c.title, c.pinned, c.model, c.created_at,
               (SELECT MAX(m.created_at) FROM messages m WHERE m.chat_id = c.id) AS last_at
        FROM chats c WHERE c.user_id = ?
        ORDER BY c.pinned DESC, COALESCE(last_at, c.created_at) DESC
        """,
        (uid,),
    )


async def create_chat(uid: str, model: str | None = None) -> dict:
    cid = uuid.uuid4().hex
    model = model or config.default_model()
    await _exec(
        "INSERT INTO chats (id, user_id, title, model, created_at) VALUES (?, ?, 'Новый диалог', ?, ?)",
        (cid, uid, model, _now()),
    )
    return {"id": cid, "title": "Новый диалог", "model": model, "created_at": _now()}


async def set_chat_model(uid: str, chat_id: str, model: str) -> bool:
    cur = await _exec(
        "UPDATE chats SET model = ? WHERE id = ? AND user_id = ?", (model, chat_id, uid))
    return cur.rowcount > 0


async def get_chat(uid: str, chat_id: str) -> dict | None:
    rows = await _fetch("SELECT * FROM chats WHERE id = ? AND user_id = ?", (chat_id, uid))
    return rows[0] if rows else None


async def set_chat_title_if_new(chat_id: str, content: str) -> None:
    """Авто-название из первого сообщения — только если чат ещё «Новый диалог»."""
    title = content.strip().replace("\n", " ")[:48]
    if not title:
        return
    if len(content.strip()) > 48:
        title += "…"
    await _exec("UPDATE chats SET title = ? WHERE id = ? AND title = 'Новый диалог'", (title, chat_id))


async def list_messages_full(chat_id: str, limit: int = 200) -> list[dict]:
    """Полная история для отрисовки (старые сверху)."""
    return await _fetch(
        "SELECT role, content, created_at FROM messages WHERE chat_id = ? ORDER BY created_at ASC LIMIT ?",
        (chat_id, limit),
    )


async def rename_chat(uid: str, chat_id: str, title: str) -> bool:
    title = title.strip()[:80]
    if not title:
        return False
    cur = await _exec(
        "UPDATE chats SET title = ? WHERE id = ? AND user_id = ?", (title, chat_id, uid))
    return cur.rowcount > 0


async def set_chat_pinned(uid: str, chat_id: str, pinned: bool) -> bool:
    cur = await _exec(
        "UPDATE chats SET pinned = ? WHERE id = ? AND user_id = ?",
        (1 if pinned else 0, chat_id, uid),
    )
    return cur.rowcount > 0


async def delete_chat(uid: str, chat_id: str) -> bool:
    """Диалог + сообщения + его файлы (в корзину). Файлы без привязки не трогаем."""
    if not await get_chat(uid, chat_id):
        return False
    await _exec("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
    await soft_delete_chat_files(chat_id)
    await _exec("DELETE FROM chats WHERE id = ?", (chat_id,))
    return True


# ---------- files ----------
async def add_file(fid: str, uid: str, name: str, mime: str, size: int, path: str,
                   chat_id: str | None = None) -> None:
    await _exec(
        "INSERT INTO files (id, user_id, chat_id, orig_name, mime, size, path, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (fid, uid, chat_id, name, mime or "application/octet-stream", size, path, _now()),
    )


async def attach_file_to_chat(fid: str, uid: str, chat_id: str) -> None:
    """Привязать ранее загруженный без чата файл (legacy) — один раз, не перебивать."""
    await _exec(
        "UPDATE files SET chat_id = ? WHERE id = ? AND user_id = ? AND chat_id IS NULL",
        (chat_id, fid, uid),
    )


async def list_chat_files(uid: str, chat_id: str) -> list[dict]:
    """Файлы, прикреплённые к диалогу (живые, не в корзине)."""
    return await _fetch(
        "SELECT id, orig_name, size, parse_status, created_at FROM files"
        " WHERE user_id = ? AND chat_id = ? AND deleted_at IS NULL ORDER BY created_at DESC",
        (uid, chat_id),
    )


async def soft_delete_chat_files(chat_id: str) -> int:
    """Каскад при удалении диалога: его файлы — в корзину (TTL чистит cleanup)."""
    cur = await _exec(
        "UPDATE files SET deleted_at = ? WHERE chat_id = ? AND deleted_at IS NULL",
        (_now(), chat_id),
    )
    return cur.rowcount


async def set_file_parsed(fid: str, kimi_file_id: str | None, parsed_path: str | None, status: str) -> None:
    await _exec(
        "UPDATE files SET kimi_file_id = ?, parsed_path = ?, parse_status = ? WHERE id = ?",
        (kimi_file_id, parsed_path, status, fid),
    )


async def list_files(uid: str) -> list[dict]:
    return await _fetch(
        "SELECT id, orig_name, size, parse_status, created_at FROM files"
        " WHERE user_id = ? AND deleted_at IS NULL ORDER BY created_at DESC",
        (uid,),
    )


async def get_file(fid: str, uid: str) -> dict | None:
    rows = await _fetch(
        "SELECT * FROM files WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
        (fid, uid),
    )
    return rows[0] if rows else None


async def soft_delete_file(fid: str, uid: str) -> dict | None:
    """Корзина: помечаем deleted_at, файл на диске доживёт до чистильщика."""
    row = await get_file(fid, uid)
    if row:
        await _exec("UPDATE files SET deleted_at = ? WHERE id = ?", (_now(), fid))
    return row


async def trashed_files(cutoff: float) -> list[dict]:
    return await _fetch("SELECT * FROM files WHERE deleted_at IS NOT NULL AND deleted_at < ?", (cutoff,))


async def hard_delete_files(ids: list[str]) -> None:
    for fid in ids:
        await _exec("DELETE FROM files WHERE id = ?", (fid,))


async def user_usage(uid: str) -> int:
    """Физическое занятое место (включая корзину — она тоже занимает диск)."""
    rows = await _fetch("SELECT COALESCE(SUM(size), 0) AS s FROM files WHERE user_id = ?", (uid,))
    return rows[0]["s"] if rows else 0


def get_parsed_text(row: dict) -> str | None:
    pp = row.get("parsed_path")
    if pp and os.path.exists(pp):
        with open(pp, encoding="utf-8", errors="replace") as f:
            return f.read()
    return None


# ---------- usage (токены Kimi по юзерам) ----------
async def add_usage(uid: str, chat_id: str, kind: str, model: str, usage: dict) -> None:
    await _exec(
        "INSERT INTO usage_events"
        " (id, user_id, chat_id, kind, model, prompt_tokens, completion_tokens, total_tokens, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            uuid.uuid4().hex, uid, chat_id, kind, model,
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
            int(usage.get("total_tokens") or 0),
            _now(),
        ),
    )


async def usage_summary(uid: str, days: int = 30) -> dict:
    totals = await _fetch(
        "SELECT COUNT(*) AS requests, COALESCE(SUM(prompt_tokens),0) AS prompt,"
        " COALESCE(SUM(completion_tokens),0) AS completion,"
        " COALESCE(SUM(total_tokens),0) AS total"
        " FROM usage_events WHERE user_id = ?",
        (uid,),
    )
    by_day = await _fetch(
        "SELECT strftime('%Y-%m-%d', created_at, 'unixepoch') AS day,"
        " SUM(total_tokens) AS total, COUNT(*) AS requests"
        " FROM usage_events WHERE user_id = ? AND created_at > ?"
        " GROUP BY day ORDER BY day DESC",
        (uid, _now() - days * 86400),
    )
    by_model = await _fetch(
        "SELECT model, COUNT(*) AS requests, COALESCE(SUM(prompt_tokens),0) AS prompt,"
        " COALESCE(SUM(completion_tokens),0) AS completion,"
        " COALESCE(SUM(total_tokens),0) AS total"
        " FROM usage_events WHERE user_id = ?"
        " GROUP BY model ORDER BY total DESC",
        (uid,),
    )
    return {"totals": totals[0], "by_day": by_day, "by_model": by_model}


# ---------- retention: чистильщик ----------
async def stale_anonymous_users(cutoff: float) -> list[str]:
    rows = await _fetch(
        "SELECT id FROM users WHERE username IS NULL AND last_seen_at < ?", (cutoff,)
    )
    return [r["id"] for r in rows]


async def user_all_file_paths(uid: str) -> list[str]:
    rows = await _fetch("SELECT path, parsed_path FROM files WHERE user_id = ?", (uid,))
    return [p for r in rows for p in (r["path"], r["parsed_path"]) if p]


async def delete_user_cascade(uid: str) -> None:
    await _exec("DELETE FROM files WHERE user_id = ?", (uid,))
    await _exec("DELETE FROM usage_events WHERE user_id = ?", (uid,))
    chat_rows = await _fetch("SELECT id FROM chats WHERE user_id = ?", (uid,))
    for c in chat_rows:
        await _exec("DELETE FROM messages WHERE chat_id = ?", (c["id"],))
        await _exec("DELETE FROM jobs WHERE chat_id = ?", (c["id"],))
    await _exec("DELETE FROM chats WHERE user_id = ?", (uid,))
    await _exec("DELETE FROM users WHERE id = ?", (uid,))
