"""Файловое хранилище: раскладка по юзерам, атомарные записи, безопасные имена."""
import os
import re
import uuid

from . import config

_SAFE = re.compile(r"[^\w.\- ()]+", re.UNICODE)


def safe_name(name: str) -> str:
    """Убирает путь и опасные символы, оставляет читаемое имя."""
    base = os.path.basename(name).strip() or "file"
    return _SAFE.sub("_", base)[:120]


def user_dir(uid: str) -> str:
    d = os.path.join(config.DATA_DIR, "users", uid)
    os.makedirs(os.path.join(d, "uploads"), exist_ok=True)
    os.makedirs(os.path.join(d, "reports"), exist_ok=True)
    return d


def save_upload(uid: str, fid: str, filename: str, data: bytes) -> str:
    """Атомарно: пишем во .tmp, затем os.replace."""
    path = os.path.join(user_dir(uid), "uploads", f"{fid}__{safe_name(filename)}")
    tmp = path + f".{uuid.uuid4().hex[:8]}.tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return path


def save_parsed(uid: str, fid: str, text: str) -> str:
    path = os.path.join(user_dir(uid), "uploads", f"{fid}.parsed.txt")
    tmp = path + f".{uuid.uuid4().hex[:8]}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
    return path


def remove_quiet(path: str | None) -> None:
    if path:
        try:
            os.remove(path)
        except OSError:
            pass
