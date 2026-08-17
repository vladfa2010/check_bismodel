"""Файлы: приём, локальный парсинг текста, кеш, удаление."""
import asyncio
import uuid

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi import File as FileParam

from . import config, db, docparse, storage
from .kimi import kimi
from .routes_auth import require_user

router = APIRouter()


async def parse_pipeline(fid: str, uid: str, path: str, filename: str) -> None:
    """Фон: локальный парсинг → кеш на диск. Kimi Files API — запасной путь для PDF-сканов."""
    try:
        try:
            text = docparse.extract_text(path, filename)
            kimi_id = None
        except Exception:
            if config.MOCK_KIMI:
                raise  # ни локально, ни через Kimi — честная ошибка
            kimi_id = await kimi.upload_file(path, filename)
            text = await kimi.file_content(kimi_id)
        parsed_path = storage.save_parsed(uid, fid, text)
        await db.set_file_parsed(fid, kimi_id, parsed_path, "ready")
    except Exception:
        await db.set_file_parsed(fid, None, None, "error")


@router.post("/files")
async def upload_file(request: Request, file: UploadFile = FileParam(...)):
    uid = require_user(request)["id"]
    form = await request.form()
    chat_id = form.get("chat_id") or None
    if chat_id and not await db.get_chat(uid, str(chat_id)):
        raise HTTPException(404, "Диалог не найден")
    data = await file.read()
    if not data:
        raise HTTPException(400, "Пустой файл")
    if len(data) > config.MAX_FILE_SIZE:
        raise HTTPException(413, f"Файл больше {config.MAX_FILE_SIZE // 1024 // 1024} МБ")
    if await db.user_usage(uid) + len(data) > config.USER_QUOTA:
        raise HTTPException(413, "Превышена квота хранилища (200 МБ)")

    fid = uuid.uuid4().hex[:12]
    path = storage.save_upload(uid, fid, file.filename or "file", data)
    await db.add_file(fid, uid, file.filename or "file", file.content_type, len(data), path,
                     chat_id=chat_id)

    asyncio.create_task(parse_pipeline(fid, uid, path, file.filename or "file"))
    return {"id": fid, "name": file.filename, "size": len(data), "status": "processing"}


@router.get("/chats/{chat_id}/files")
async def list_chat_files_ep(chat_id: str, request: Request):
    """Файлы, прикреплённые к диалогу (правая колонка на десктопе)."""
    uid = require_user(request)["id"]
    if not await db.get_chat(uid, chat_id):
        raise HTTPException(404, "Диалог не найден")
    return {"files": await db.list_chat_files(uid, chat_id)}


@router.get("/files")
async def list_files(request: Request):
    return {"files": await db.list_files(require_user(request)["id"])}


@router.get("/files/{fid}")
async def file_status(fid: str, request: Request):
    row = await db.get_file(fid, require_user(request)["id"])
    if not row:
        raise HTTPException(404, "Файл не найден")
    return {"id": row["id"], "name": row["orig_name"], "size": row["size"],
            "status": row["parse_status"]}


@router.delete("/files/{fid}")
async def delete_file(fid: str, request: Request):
    """Мягкое удаление: файл уходит в корзину на TRASH_TTL_DAYS, чистит cleanup."""
    row = await db.soft_delete_file(fid, require_user(request)["id"])
    if not row:
        raise HTTPException(404, "Файл не найден")
    return {"ok": True, "trash_ttl_days": config.TRASH_TTL_DAYS}
