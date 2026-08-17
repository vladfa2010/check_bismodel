# FinModel AI — прототип

Кликабельный прототип чата для генерации финансовых моделей: загрузка файлов, Data Extraction Report, сборка и скачивание настоящего `.xlsx` (генерируется в браузере). Ответы ИИ сымитированы — контур Kimi API подключается в бэкенде (см. `docs/architecture-kimi-api.md`).

## Структура

```
index.html            # весь прототип (чат, вложения, эмбиент-фон, генерация xlsx)
render.yaml           # blueprint для деплоя на Render (Static Site)
docs/
  architecture-kimi-api.md   # архитектура v2 под Kimi API
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
