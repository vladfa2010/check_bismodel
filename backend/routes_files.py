"""Файлы: приём, отправка в Kimi Files API, кеш распарсенного текста, удаление."""
import asyncio
import uuid

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi import File as FileParam

from . import config, db, storage
from .kimi import kimi
from .routes_auth import require_user

router = APIRouter()


async def parse_pipeline(fid: str, uid: str, path: str, filename: str) -> None:
    """Фон: Kimi upload → получение распарсенного текста → кеш на диск."""
    try:
        kimi_id = await kimi.upload_file(path, filename)
        text = await kimi.file_content(kimi_id)
        parsed_path = storage.save_parsed(uid, fid, text)
        await db.set_file_parsed(fid, kimi_id, parsed_path, "ready")
    except Exception:
        await db.set_file_parsed(fid, None, None, "error")


@router.post("/files")
async def upload_file(request: Request, file: UploadFile = FileParam(...)):
    uid = require_user(request)["id"]
    data = await file.read()
    if not data:
        raise HTTPException(400, "Пустой файл")
    if len(data) > config.MAX_FILE_SIZE:
        raise HTTPException(413, f"Файл больше {config.MAX_FILE_SIZE // 1024 // 1024} МБ")
    if await db.user_usage(uid) + len(data) > config.USER_QUOTA:
        raise HTTPException(413, "Превышена квота хранилища (200 МБ)")

    fid = uuid.uuid4().hex[:12]
    path = storage.save_upload(uid, fid, file.filename or "file", data)
    await db.add_file(fid, uid, file.filename or "file", file.content_type, len(data), path)

    asyncio.create_task(parse_pipeline(fid, uid, path, file.filename or "file"))
    return {"id": fid, "name": file.filename, "size": len(data), "status": "processing"}


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
