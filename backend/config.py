"""Конфигурация FinModel AI. Всё через переменные окружения."""
import json
import os

# --- хранилище ---
DATA_DIR = os.getenv("DATA_DIR") or (
    "/var/data" if os.path.isdir("/var/data") else os.path.join(os.getcwd(), "data")
)
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", str(25 * 1024 * 1024)))   # 25 МБ
USER_QUOTA = int(os.getenv("USER_QUOTA", str(200 * 1024 * 1024)))        # 200 МБ

# --- Kimi API (Moonshot) ---
MOONSHOT_API_KEY = os.getenv("MOONSHOT_API_KEY", "")
MOONSHOT_BASE_URL = os.getenv("MOONSHOT_BASE_URL", "https://api.moonshot.ai/v1").rstrip("/")
KIMI_MODEL = os.getenv("KIMI_MODEL", "kimi-k3")

# --- MiniMax API (OpenAI-совместимый) ---
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")
MINIMAX_BASE_URL = os.getenv("MINIMAX_BASE_URL", "https://api.minimax.io/v1").rstrip("/")
MINIMAX_MODEL = os.getenv("MINIMAX_MODEL", "MiniMax-M3")

# mock-режим: MOCK_KIMI=1 — все модели отвечают заглушкой (разработка без ключей)
_MOCK_FORCED = os.getenv("MOCK_KIMI", "").lower() in ("1", "true", "yes")
MOCK_KIMI = _MOCK_FORCED or not MOONSHOT_API_KEY  # совместимость со старым кодом


def available_models() -> list[dict]:
    """Модели, видимые юзеру: с ключом — live, без — только в mock-режиме разработки."""
    out = []
    if _MOCK_FORCED or MOONSHOT_API_KEY:
        out.append({
            "id": KIMI_MODEL, "title": "Kimi K3", "provider": "moonshot",
            "base_url": MOONSHOT_BASE_URL, "key": MOONSHOT_API_KEY,
            "mock": _MOCK_FORCED or not MOONSHOT_API_KEY,
        })
    if _MOCK_FORCED or MINIMAX_API_KEY:
        out.append({
            "id": MINIMAX_MODEL, "title": "MiniMax M3", "provider": "minimax",
            "base_url": MINIMAX_BASE_URL, "key": MINIMAX_API_KEY,
            "mock": _MOCK_FORCED or not MINIMAX_API_KEY,
        })
    return out


def model_spec(model_id: str | None) -> dict | None:
    for m in available_models():
        if m["id"] == model_id:
            return m
    return None


def default_model() -> str:
    """Первая живая модель; если ключей нет — первая из списка (mock)."""
    models = available_models()
    for m in models:
        if not m["mock"]:
            return m["id"]
    return models[0]["id"] if models else KIMI_MODEL

# --- сессии ---
SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-secret-change-me-in-prod")
COOKIE_NAME = "fm_uid"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365   # год

# --- предустановленные пользователи (регистрация закрыта) ---
# Формат env: SEED_USERS='{"vlad": "pass1", "victor": "pass2"}'
# ВАЖНО: дефолт ниже — только для локальной разработки, пароль заведомо слабый.
# На проде SEED_USERS задаётся обязательно (deploy/.env).
SEED_USERS = json.loads(os.getenv("SEED_USERS", "")) if os.getenv("SEED_USERS") else {
    "vlad": "dev-only-change-me",
    "victor": "dev-only-change-me",
}

# --- retention ---
TRASH_TTL_DAYS = 7        # корзина: файлы после удаления живут на диске 7 дней
ANON_TTL_DAYS = 30        # анонимные юзеры без активности — под удаление
CLEANUP_INTERVAL_SEC = 3600

# --- контекст диалога ---
HISTORY_LIMIT = 20              # последних реплик в контексте
DOC_TEXT_LIMIT = 15_000         # символов распарсенного текста на документ
