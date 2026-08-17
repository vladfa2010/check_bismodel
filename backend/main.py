"""FinModel AI — FastAPI entrypoint.

Раздаёт статику прототипа и /api/*: логин (регистрация закрыта),
файлы, SSE-чат, учёт токенов. Один инстанс, данные — SQLite + файлы на DATA_DIR.
"""
import asyncio
import logging

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature

from . import config, db
from .cleanup import cleanup_loop
from .routes_auth import router as auth_router
from .routes_chat import router as chat_router
from .routes_files import router as files_router
from .security import signer

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="FinModel AI", docs_url=None, redoc_url=None)


@app.on_event("startup")
async def startup() -> None:
    await db.init_db()
    await db.seed_users()
    asyncio.create_task(cleanup_loop())


@app.on_event("shutdown")
async def shutdown() -> None:
    await db.close_db()


@app.middleware("http")
async def session_middleware(request: Request, call_next):
    """Читаем подписанную куку → request.state.user. Анонимов больше не создаём."""
    request.state.user = None
    raw = request.cookies.get("fm_uid")
    if raw:
        try:
            uid = signer.loads(raw)
            request.state.user = await db.get_user(uid)
        except BadSignature:
            pass
    return await call_next(request)


@app.get("/healthz")
async def healthz():
    from . import config
    models = config.available_models()
    return {
        "ok": True,
        "models": [{"id": m["id"], "title": m["title"], "mock": m["mock"]} for m in models],
        "default_model": config.default_model(),
        # совместимость со старым фронтом
        "kimi": "mock" if config.MOCK_KIMI else "live",
        "model": config.KIMI_MODEL,
    }


app.include_router(auth_router, prefix="/api")
app.include_router(files_router, prefix="/api")
app.include_router(chat_router, prefix="/api")

# Статика прототипа — последней, чтобы не перехватывать /api
app.mount("/", StaticFiles(directory="static", html=True), name="static")
