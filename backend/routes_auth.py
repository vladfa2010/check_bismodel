"""Аутентификация: логин/логаут/me + учёт токенов юзера. Регистрация закрыта."""
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from . import config, db
from .security import signer, verify_password

router = APIRouter()

# --- Анти-брутфорс: 5 неудач за 5 минут с одного IP по одному логину → блок 15 мин ---
_attempts: dict[str, list[float]] = {}
MAX_ATTEMPTS = 5
WINDOW_SEC = 300
LOCK_SEC = 900


def _key(request: Request, username: str) -> str:
    ip = request.client.host if request.client else "?"
    return f"{ip}|{username.lower()}"


def _check_lock(key: str) -> None:
    now = time.time()
    fails = [t for t in _attempts.get(key, []) if now - t < WINDOW_SEC + LOCK_SEC]
    _attempts[key] = fails
    if len(fails) >= MAX_ATTEMPTS:
        wait = int(LOCK_SEC - (now - fails[-MAX_ATTEMPTS])) // 60 + 1
        raise HTTPException(429, f"Слишком много попыток. Повторите через {wait} мин.")


def _record_fail(key: str) -> None:
    _attempts.setdefault(key, []).append(time.time())


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
    key = _key(request, username)
    _check_lock(key)
    user = await db.get_user_by_username(username)
    if not user or not verify_password(password, user.get("password_hash") or ""):
        _record_fail(key)
        raise HTTPException(401, "Неверный логин или пароль")
    _attempts.pop(key, None)  # успешный вход сбрасывает счётчик
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
