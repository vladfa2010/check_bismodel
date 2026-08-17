# Runbook: операционные сценарии

Пошаговые инструкции под стрессом. Формат: симптом → быстрая диагностика →
действия → что записать после. Всё выполняется по SSH на VDS
(`ssh root@46.173.27.26`), рабочий каталог `/opt/finmodel/site/deploy`.
Общая справка — в docs/deploy.md; здесь только «что делать, когда горит».

## 0. Базовый набор диагностики

```bash
docker ps --format '{{.Names}} {{.Status}}'     # оба контейнера Up, app — healthy
curl -s http://127.0.0.1/healthz                # локально в обход Caddy
curl -s https://46.173.27.26.sslip.io/healthz   # через TLS, как видит юзер
docker compose logs --tail 50 app               # логи бэкенда
docker compose logs --tail 50 caddy             # логи прокси/TLS
df -h /                                         # диск
free -m                                         # память (2 ГБ + swap 2 ГБ)
```

`/healthz` отвечает JSON: `ok`, список моделей с флагом `mock`,
`default_model`. `mock: true` у живой модели = ключ потерян/пустой.

## 1. «Сайт не открывается»

1. `docker ps` — контейнеры живы? Нет → `docker compose up -d`, смотреть логи.
2. Живы → `curl http://127.0.0.1/healthz`. Отвечает → проблема в Caddy/TLS:
   `docker compose logs --tail 100 caddy` (типичное: Let's Encrypt rate-limit
   после частых перезапусков — ждать час, НЕ дёргать restart в цикле).
3. Локально тоже молчит → `docker compose logs --tail 100 app`. Упал при
   старте — обычно БД залочена или диск полон (сценарий 4).

## 2. «Чат не отвечает / ответы-заглушки»

Симптом «принял! это ответ бэкенда в mock-режиме» у живого юзера = LLM недоступна.

1. `/healthz`: `mock: true` у модели с ключом → ключ не доехал до контейнера:
   `docker exec deploy-app-1 sh -c 'echo ${#MOONSHOT_API_KEY}'` — 0 значит
   `.env` пуст/отсутствует. Восстановить из таблицы секретов
   (docs/security.md, раздел 7), `docker compose up -d`.
2. `mock: false`, но ответы падают с ошибкой в UI → логи app:
   - `moonshot API 401` → ключ отозван/просрочен — перевыпуск на платформе.
   - `429` → rate-limit провайдера; шлюз сам ретраит, частые 429 = превышение
     тарифа, смотреть billing провайдера.
   - `TransportError/Timeout` → сеть VDS↔провайдер; проверить
     `curl -s -o /dev/null -w '%{http_code}' https://api.moonshot.ai/v1/models`
     (без ключа ждём 401 — значит, сеть есть).
3. **Ключи и секреты в чат/лог не пастить** — только в `.env` на сервере.

## 3. «Всех разлогинило / не пускает»

- Всех разлогинило разом → кто-то пересоздал контейнер с другим
  `SESSION_SECRET` (типично после потери `.env` — берётся compose-дефолт).
  Проверка: `docker exec deploy-app-1 sh -c 'echo ${#SESSION_SECRET}'`
  должно быть 64. Если 20 — это дефолт `dev-secret-change-me`, восстановить
  `.env`, `compose up -d`. Юзерам: просто войти заново.
- Один юзер не может войти, 429 «Слишком много попыток» → анти-брутфорс,
  ждать 15 минут; срочно — рестарт app обнуляет счётчики
  (`docker compose restart app`, это безопасно: код в образе не меняется).
- Один юзер, «неверный пароль» уверенно-верного → хеш в БД не совпал
  (пересидирование?). Сброс: сгенерировать хеш локально
  (`python3 -c "from backend.security import hash_password; print(hash_password('NEW'))"`)
  и UPDATE в БД (сценарий 5).

## 4. «Диск полон»

```bash
df -h /
du -sh /var/lib/docker /opt/finmodel /var/data 2>/dev/null
docker system df
```

Порядок освобождения:
1. `docker system prune -f` (старые образы после пересборок — обычно самый
   жирный пункт; dangling-слои LibreOffice-образа по 1+ ГБ).
2. Корзина файлов чистится сама раз в час (7 дней TTL); форс —
   `docker compose restart app` (cleanup стартует вместе с приложением).
3. Логи контейнеров: `truncate -s 0 $(docker inspect -f '{{.LogPath}}' deploy-app-1)`.
4. Дешёвая страховка на будущее: алерт `df` в cron — пока не настроен
   (см. ограничения в docs/security.md).

## 5. Работа с БД напрямую

БД: `/var/data/app.db` в volume; удобнее из контейнера:

```bash
docker exec -it deploy-app-1 python3 -c "
import sqlite3; c = sqlite3.connect('/var/data/app.db')
print(c.execute('SELECT username, created_at FROM users').fetchall())"
```

Правила:
- **Только чтение без крайней нужды.** Запись руками — последняя мера;
  приложение держит одно соединение и не ждёт внешних изменений.
- WAL: рядом лежат `app.db-wal`/`app.db-shm` — копировать БД = копировать
  все три файла (или `sqlite3 app.db .backup`).
- Перед любой записью: остановить app (`docker compose stop app`),
  сделать копию, записать, стартовать, проверить.

## 6. Откат на прошлый коммит

Код запечён в образ — откат = checkout + пересборка:

```bash
cd /opt/finmodel/site
git log --oneline -5                    # выбрать точку
git checkout <sha> -- backend static    # только код, НЕ трогая deploy/.env
cd deploy && nohup docker compose up -d --build > /tmp/fm-build.log 2>&1 &
tail -f /tmp/fm-build.log               # дождаться Started
curl -s http://127.0.0.1/healthz
```

Если на сервере нет git-истории (rsync-деплой): откат из песочницы —
checkout нужного коммита локально, rsync по whitelist-путям (docs/deploy.md),
`compose up -d --build`. `.env` не трогать никогда.

## 7. Подозрение на инцидент безопасности

Следуем docs/security.md, раздел 11 («Инцидент: что делать»). Здесь только
напоминание о порядке: сначала отсечь доступ (смена SESSION_SECRET /
блокировка юзера), потом расследовать, потом ротация. Не наоборот.

## 8. После инцидента

Короткая запись в коммит или в этот файл: симптом, причина, что починило,
что меняем, чтобы не повторилось. Так здесь появились: правило nohup для
долгих сборок (SSH оборвался mid-build), whitelist rsync (стёрт `.env`),
`restart caddy` для Caddyfile (compose up не перечитывал маунт).
