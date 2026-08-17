"""Чистильщик: корзина файлов (7 дней) и забытые анонимные юзеры (30 дней)."""
import asyncio
import logging
import time

from . import config, db, storage

log = logging.getLogger("cleanup")


async def run_cleanup() -> None:
    now = time.time()

    # 1. Корзина: файлы, удалённые юзером более TRASH_TTL_DAYS назад
    trashed = await db.trashed_files(now - config.TRASH_TTL_DAYS * 86400)
    for f in trashed:
        storage.remove_quiet(f["path"])
        storage.remove_quiet(f["parsed_path"])
    if trashed:
        await db.hard_delete_files([f["id"] for f in trashed])
        log.info("trash purge: %d files", len(trashed))

    # 2. Анонимные юзеры без активности ANON_TTL_DAYS (сидевшие без логина)
    for uid in await db.stale_anonymous_users(now - config.ANON_TTL_DAYS * 86400):
        for path in await db.user_all_file_paths(uid):
            storage.remove_quiet(path)
        await db.delete_user_cascade(uid)
        log.info("stale anonymous user purged: %s", uid)


async def cleanup_loop() -> None:
    while True:
        try:
            await run_cleanup()
        except Exception:  # noqa: BLE001 — чистильщик не должен убивать процесс
            log.exception("cleanup failed")
        await asyncio.sleep(config.CLEANUP_INTERVAL_SEC)
