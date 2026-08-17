# Продакшн-деплой FinModel AI (Beget VDS)

Это документ про **боевой** стенд. Первичная установка сервера с нуля описана в
`deploy-vds.md` (скрипт `deploy/server-bootstrap.sh` делает шаги 1–4 автоматически).

---

## 1. Стенд

| Параметр | Значение |
|---|---|
| Хостер | Beget VDS («Captivating Liliya») |
| IP | `46.173.27.26` |
| ОС | Ubuntu 26.04 LTS, 2 ГБ RAM + 2 ГБ swap, 14 ГБ диск |
| Адреса | `https://46.173.27.26.sslip.io` (основной, Let's Encrypt); `http://46.173.27.26` (legacy, plain HTTP) |
| Код | `/opt/finmodel/site` (копия репозитория через rsync) |
| Данные | docker volume `deploy_appdata` → `/var/data` внутри app: `app.db` + файлы юзеров |
| Стек | Caddy 2 (80/443) → app (FastAPI/uvicorn :8000) → SQLite (WAL) |

Контейнеры: `deploy-app-1` (healthcheck каждые 30 с), `deploy-caddy-1`.
Оба `restart: unless-stopped` — переживают ребут сервера.

## 2. Сетевая и TLS-логика

Caddyfile правила (важно понимать, прежде чем править):

1. **`{$DOMAIN::80}`** — основной сайт. `DOMAIN` задан (сейчас `46.173.27.26.sslip.io`)
   → Caddy сам выпускает и продлевает Let's Encrypt, http→https редирект включён.
   `DOMAIN` пуст → сайт слушает `:80` без TLS.
2. **`http://46.173.27.26`** — отдельный блок для голого IP, **plain HTTP без редиректа**.
   Нельзя редиректить IP на `https://IP`: сертификат выдан домену, браузер покажет
   ошибку. После переезда на свой домен этот блок удаляется.
3. **`Cache-Control: no-cache` на HTML** — иначе юзеры после деплоя сидят на старом JS
   (был инцидент: «кнопки не работают» из-за кеша).
4. **`flush_interval -1`** в reverse_proxy — обязательно для SSE: без этого Caddy
   буферизует стрим и ответы приходят одним куском в конце.

Файрвол (ufw): открыты только 22/80/443. fail2ban сторожит SSH.

## 3. Секреты

`/opt/finmodel/site/deploy/.env` (chmod 600, **никогда не коммитится**):

| Ключ | Назначение |
|---|---|
| `MOONSHOT_API_KEY` | Kimi K3 (platform.kimi.ai) |
| `MINIMAX_API_KEY` | MiniMax M3 (platform.minimax.io, формат `sk-cp-…`) |
| `SESSION_SECRET` | подпись сессионных кук; смена = разлогин всех |
| `DOMAIN` | домен для TLS |

Правила:
- **Ключи появляются в модельном реестре автоматически**: добавил ключ в `.env` +
  `docker compose up -d` → модель появилась в селекторе фронта. Ключ пуст/невалиден —
  модель скрыта (проверка валидности: `curl -H "Authorization: Bearer $KEY"
  https://api.minimax.io/v1/models` → 200).
- Ключи и пароли не светим в git; в логах чата токены маскируются (`sed`).
- root-пароль сервера после передачи в чате меняется (`passwd`).

## 4. Обновление (ручной деплой)

Стандартный цикл из песочницы разработки:

```bash
# 1. код на сервер (только изменённые файлы)
rsync -az -e ssh backend/ static/ tests/ requirements.txt root@46.173.27.26:/opt/finmodel/site/

# 2. пересборка и перезапуск (код запечён в образ)
cd /opt/finmodel/site/deploy && docker compose up -d --build app

# 3. проверка
curl -s https://46.173.27.26.sslip.io/healthz
```

Правила:
- **rsync — только перечисленные пути, НИКОГДА не весь корень с `--delete`.**
  `deploy/.env` существует только на сервере: синхронизация всего дерева с
  `--delete` стирает его, и следующий `compose up` пересоздаёт контейнер
  с пустыми ключами и дефолтным `SESSION_SECRET` (разлогин всех + mock-режим).
  Случилось один раз при аудите безопасности — `.env` восстанавливали вручную.
- **`docker compose up -d --build`, не `restart`** — код копируется в образ при сборке,
  restart поднимет старый код.
- Долгие сборки (LibreOffice в образе) запускать `nohup ... &` с логом —
  SSH-сессия может оборваться, сборка должна дожить.
- Менялся только `Caddyfile`/`docker-compose.yml` → `docker compose restart caddy`
  (config смонтирован, пересборка не нужна; но `up -d` без изменений контейнер
  **не перечитывает** mount — нужен именно restart).
- После деплоя — смоук: `/healthz`, логин, одно тестовое сообщение. Полный e2e
  гоняется локально до деплоя.

## 5. Данные и миграции

- SQLite `app.db` в WAL-режиме, один writer через `asyncio.Lock` — для двух юзеров
  с запасом. Миграции — `CREATE TABLE IF NOT EXISTS` + точечные `ALTER TABLE ADD COLUMN`
  из списка `MIGRATIONS` в `db.py`; применяются при старте контейнера, отката нет
  (правило: миграции только аддитивные).
- Файлы юзеров: `/var/data/users/<uid>/…`, распарсенные тексты — рядом `.parsed.txt`.
- **Бэкапы сейчас не настроены.** План: litestream (закомментирован в compose) или
  cron + `sqlite3 .backup` в tar.gz на второй диск. До подключения бэкапов —
  периодически: `docker exec deploy-app-1 sqlite3 /var/data/app.db ".backup /var/data/backup.db"`.

## 6. Эксплуатация: частые операции

| Задача | Команда |
|---|---|
| Логи приложения | `docker logs -f deploy-app-1` |
| Логи Caddy (TLS, редиректы) | `docker logs -f deploy-caddy-1` |
| Перезапуск app | `cd /opt/finmodel/site/deploy && docker compose restart app` |
| Добавить/сменить LLM-ключ | правка `.env` → `docker compose up -d` |
| Завести юзера | `SEED_USERS` в `.env` (`{"nick":"pass"}`) → recreate; существующие не перетираются |
| Заглянуть в БД | `docker exec -it deploy-app-1 python -c "import sqlite3; …"` |
| Место на диске | `df -h /` + `docker system df`; чистка `docker system prune` |

## 7. Автодеплой (GitHub Actions) — пока отключён

Workflow `.github/workflows/deploy-vds.yml` написан (rsync + `compose up -d --build`
по пушу в main), но **не запушен**: PAT не имеет scope `workflow`/`Actions`.
Файл лежит в репо-рабочей копии untracked. Чтобы включить: выдать токену
«Actions: Read and write» (или добавить файл через веб-UI GitHub) + секреты
`VDS_HOST`, `VDS_USER`, `VDS_SSH_KEY` в Settings → Secrets.

## 8. Известные ограничения стенда

1. **IP-домен sslip.io** — бесплатный костыль: нет контроля над DNS, теоретический
   rate-limit LE на домен. Решение: свой домен (~200 ₽/год) → A-запись →
   `DOMAIN=…` в `.env` → Caddy перевыпустит сертификат сам.
2. **Один инстанс, без отказоустойчивости** — при ребилде ~1–2 мин простоя.
3. **Диск 14 ГБ** — за образами Docker нужен периодический `prune`; квота юзера
   (200 МБ) защищает от разрастания файлов.
4. **Регион РФ**: `api.moonshot.ai` и `api.minimax.io` с сервера доступны
   (проверено curl при установке), registry Docker — через зеркала в daemon.json.
