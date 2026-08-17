"""Конфигурация FinModel AI. Всё через переменные окружения."""
import os

# --- хранилище ---
DATA_DIR = os.getenv("DATA_DIR") or (
    "/var/data" if os.path.isdir("/var/data") else os.path.join(os.getcwd(), "data")
)
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", str(25 * 1024 * 1024)))   # 25 МБ
USER_QUOTA = int(os.getenv("USER_QUOTA", str(200 * 1024 * 1024)))        # 200 МБ

# --- Kimi API ---
MOONSHOT_API_KEY = os.getenv("MOONSHOT_API_KEY", "")
MOONSHOT_BASE_URL = os.getenv("MOONSHOT_BASE_URL", "https://api.moonshot.ai/v1").rstrip("/")
KIMI_MODEL = os.getenv("KIMI_MODEL", "kimi-k3")
# mock-режим: явно через MOCK_KIMI=1 или когда ключа нет — прототип работает без API
MOCK_KIMI = os.getenv("MOCK_KIMI", "").lower() in ("1", "true", "yes") or not MOONSHOT_API_KEY

# --- сессии ---
SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-secret-change-me-in-prod")
COOKIE_NAME = "fm_uid"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365   # год

# --- контекст диалога ---
HISTORY_LIMIT = 20              # последних реплик в контексте
DOC_TEXT_LIMIT = 15_000         # символов распарсенного текста на документ
