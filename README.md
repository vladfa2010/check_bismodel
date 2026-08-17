# FinModel AI

Чат для генерации финансовых моделей: загрузка файлов, обсуждение бизнес-модели с ИИ, на выходе — Excel-модель. Бэкенд: FastAPI + SQLite + Kimi API (SSE-стриминг). Без ключа Kimi работает в mock-режиме, без бэкенда фронт сам переходит в демо-режим.

## Структура

```
static/index.html     # фронт: чат, вложения, эмбиент-фон, генерация xlsx
backend/              # FastAPI: main, config, db (SQLite+WAL), storage, kimi (KimiGateway),
                      #         routes_files (upload→Kimi→кеш парсинга), routes_chat (SSE)
Dockerfile            # all-in-one образ (python 3.12 + LibreOffice для QA-движка)
requirements.txt
.env.example          # MOONSHOT_API_KEY, SESSION_SECRET, DOMAIN…
deploy/               # VDS (Beget): docker-compose, Caddyfile, bootstrap скрипт
render.yaml           # статический фолбэк-деплой на Render (только фронт)
docs/
  architecture-kimi-api.md   # архитектура v2 под Kimi API
  deploy-vds.md              # пошаговый деплой на VDS Beget
```

## Локальный запуск

```bash
pip install -r requirements.txt
MOONSHOT_API_KEY=sk-... python -m uvicorn backend.main:app --port 8000
# без ключа — mock-режим:  python -m uvicorn backend.main:app --port 8000
```

## Деплой на Render (5 минут)

### Вариант А — через Blueprint (рекомендуется)

1. Зайдите на [dashboard.render.com](https://dashboard.render.com) → **New** → **Blueprint**.
2. Подключите GitHub-аккаунт, выберите репозиторий **`vladfa2010/check_bismodel`**.
3. Render сам найдёт `render.yaml` и покажет сервис `finmodel-prototype` → **Apply**.
4. Через ~1 минуту сайт будет доступен по адресу вида `https://finmodel-prototype.onrender.com`.

### Вариант Б — вручную

1. **New** → **Static Site** → выбрать репозиторий `check_bismodel`.
2. **Build Command** — оставить пустым.
3. **Publish Directory** — `.` (точка: `index.html` в корне).
4. **Create Static Site**.

### После деплоя

- Каждый `git push` в `main` автоматически передеплоит сайт (Auto-Deploy включён по умолчанию).
- `pullRequestPreviewsEnabled: true` — для каждого PR Render поднимет отдельное превью.
- Свой домен: Dashboard → сервис → **Settings** → **Custom Domain**.

## Дальше

Бэкенд (FastAPI + Kimi API + SQLite) разворачивается в этом же репозитории по срезам из `docs/architecture-kimi-api.md` — тогда `render.yaml` получит второй сервис (Docker Web Service с Persistent Disk), а статика переедет в отдачу бэкендом.
