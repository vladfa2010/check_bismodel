"""FinModel AI — FastAPI entrypoint.

Раздаёт статику прототипа и /api/*: анонимные сессии (подписанная кука),
файлы, SSE-чат. Один инстанс, данные — SQLite + файлы на DATA_DIR.
"""
import uuid

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeSerializer

from . import config, db
from .routes_chat import router as chat_router
from .routes_files import router as files_router

app = FastAPI(title="FinModel AI", docs_url=None, redoc_url=None)
signer = URLSafeSerializer(config.SESSION_SECRET, salt="fm-session")


@app.on_event("startup")
async def startup() -> None:
    await db.init_db()


@app.middleware("http")
async def session_middleware(request: Request, call_next):
    """Анонимная сессия: подписанная кука fm_uid → users в SQLite."""
    uid = None
    raw = request.cookies.get(config.COOKIE_NAME)
    if raw:
        try:
            uid = signer.loads(raw)
        except BadSignature:
            uid = None
    is_new = uid is None
    if is_new:
        uid = uuid.uuid4().hex
        await db.ensure_user(uid)
    request.state.user_id = uid
    response = await call_next(request)
    if is_new:
        response.set_cookie(
            config.COOKIE_NAME, signer.dumps(uid),
            httponly=True, samesite="lax", max_age=config.COOKIE_MAX_AGE,
        )
    return response


@app.get("/healthz")
async def healthz():
    return {
        "ok": True,
        "kimi": "mock" if config.MOCK_KIMI else "live",
        "model": config.KIMI_MODEL,
    }


app.include_router(files_router, prefix="/api")
app.include_router(chat_router, prefix="/api")

# Статика прототипа — последней, чтобы не перехватывать /api
app.mount("/", StaticFiles(directory="static", html=True), name="static")
