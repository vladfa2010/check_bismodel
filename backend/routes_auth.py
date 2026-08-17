"""Аутентификация: логин/логаут/me + учёт токенов юзера. Регистрация закрыта."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from . import config, db
from .security import signer, verify_password

router = APIRouter()


def require_user(request: Request) -> dict:
    """Гард для защищённых роутов: юзер из сессии или 401."""
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Нужен вход")
    return user


@router.post("/auth/login")
async def login(request: Request):
    body = await request.json()
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    user = await db.get_user_by_username(username)
    if not user or not verify_password(password, user.get("password_hash") or ""):
        raise HTTPException(401, "Неверный логин или пароль")
    await db.touch_user(user["id"])
    resp = JSONResponse({"ok": True, "username": username})
    resp.set_cookie(
        config.COOKIE_NAME, signer.dumps(user["id"]),
        httponly=True, samesite="lax", max_age=config.COOKIE_MAX_AGE,
    )
    return resp


@router.post("/auth/logout")
async def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(config.COOKIE_NAME)
    return resp


@router.get("/auth/me")
async def me(request: Request):
    user = require_user(request)
    return {"id": user["id"], "username": user["username"]}


@router.get("/usage")
async def usage(request: Request):
    """Расход токенов текущего юзера: итоги + по дням за 30 суток."""
    user = require_user(request)
    return await db.usage_summary(user["id"])
