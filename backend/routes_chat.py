"""Чат: SSE-стрим ответов Kimi, история в SQLite, документы из кеша."""
import json
from datetime import date

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from . import config, db
from .kimi import kimi
from .routes_auth import require_user

router = APIRouter()

# Стабильный префикс системного промпта — НЕ менять без необходимости:
# байт-в-байт одинаковое начало messages попадает в Context Caching Kimi.
SYSTEM_PROMPT = (
    "Ты — FinModel AI, ассистент для построения финансовых моделей бизнеса. "
    "Твоя задача — обсудить с пользователем его бизнес-модель: выручку, цены, "
    "клиентов, затраты, налоги, горизонт планирования. Задавай уточняющие вопросы "
    "по одному-два за раз, коротко и по делу. Отвечай по-русски. "
    "Никогда не выдумывай данные: если параметр неизвестен — спроси или явно пометь "
    "его как допущение. Если пользователь прислал документы — опирайся на них и "
    "указывай, из какого документа взята цифра. Финансовые расчёты не выполняй "
    "в уме: модель строит отдельный детерминированный движок."
)


@router.get("/chats")
async def list_chats_ep(request: Request):
    """Список диалогов юзера (свежие сверху)."""
    uid = require_user(request)["id"]
    return {"chats": await db.list_chats(uid)}


@router.post("/chats")
async def create_chat_ep(request: Request):
    """Создать пустой диалог."""
    uid = require_user(request)["id"]
    return await db.create_chat(uid)


@router.patch("/chats/{chat_id}")
async def update_chat_ep(chat_id: str, request: Request):
    """Переименовать или закрепить/открепить диалог."""
    uid = require_user(request)["id"]
    body = await request.json()
    ok = True
    if "title" in body:
        ok = await db.rename_chat(uid, chat_id, str(body["title"]))
    if "pinned" in body:
        ok = await db.set_chat_pinned(uid, chat_id, bool(body["pinned"])) and ok
    if not ok:
        raise HTTPException(404, "Диалог не найден")
    return {"ok": True}


@router.delete("/chats/{chat_id}")
async def delete_chat_ep(chat_id: str, request: Request):
    """Удалить диалог вместе с сообщениями (файлы юзера остаются)."""
    uid = require_user(request)["id"]
    if not await db.delete_chat(uid, chat_id):
        raise HTTPException(404, "Диалог не найден")
    return {"ok": True}


@router.get("/chats/{chat_id}/messages")
async def get_messages_ep(chat_id: str, request: Request):
    """Полная история диалога (только своего)."""
    uid = require_user(request)["id"]
    chat = await db.get_chat(uid, chat_id)
    if not chat:
        raise HTTPException(404, "Диалог не найден")
    return {"id": chat["id"], "title": chat["title"],
            "messages": await db.list_messages_full(chat_id)}


@router.post("/chats/{chat_id}/messages")
async def send_message(chat_id: str, request: Request):
    body = await request.json()
    content = (body.get("content") or "").strip()
    file_ids = body.get("file_ids") or []
    if not content and not file_ids:
        raise HTTPException(400, "Пустое сообщение")

    uid = require_user(request)["id"]
    if not await db.get_chat(uid, chat_id):
        raise HTTPException(404, "Диалог не найден")
    await db.add_message(chat_id, "user", content, file_ids)
    await db.set_chat_title_if_new(chat_id, content)

    # документы — из кеша распарсенных текстов (не дёргаем Kimi повторно)
    doc_blocks = []
    for fid in file_ids:
        row = await db.get_file(fid, uid)
        if not row:
            continue
        text = db.get_parsed_text(row)
        if text:
            doc_blocks.append(f"Документ «{row['orig_name']}»:\n{text[:config.DOC_TEXT_LIMIT]}")
        else:
            doc_blocks.append(f"Документ «{row['orig_name']}» загружен, текст ещё обрабатывается.")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.append({"role": "system", "content": f"Текущая дата: {date.today().isoformat()}"})
    for block in doc_blocks:
        messages.append({"role": "system", "content": block})
    for m in await db.list_messages(chat_id, config.HISTORY_LIMIT):
        if m["role"] in ("user", "assistant"):
            messages.append({"role": m["role"], "content": m["content"]})

    async def event_stream():
        full: list[str] = []
        usage: dict = {}
        try:
            async for kind, payload in kimi.chat_stream(messages):
                if kind == "delta":
                    full.append(payload)
                    yield "data: " + json.dumps(
                        {"type": "delta", "text": payload}, ensure_ascii=False
                    ) + "\n\n"
                elif kind == "usage":
                    usage = payload or {}
            await db.add_message(chat_id, "assistant", "".join(full), [])
            await db.add_usage(uid, chat_id, "chat", config.KIMI_MODEL, usage)
            yield "data: " + json.dumps({"type": "done", "usage": usage}) + "\n\n"
        except Exception as e:  # noqa: BLE001 — ошибку честно показываем в чате
            yield "data: " + json.dumps(
                {"type": "error", "message": f"Ошибка генерации: {e}"}, ensure_ascii=False
            ) + "\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
