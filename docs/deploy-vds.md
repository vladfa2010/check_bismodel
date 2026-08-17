# Деплой на VDS Beget — пошаговая инструкция

От заказа сервера до работающего https-прототипа. Время: ~40 минут.

## Что понадобится

- Аккаунт Beget, заказанный VPS (KVM): минимум **1 CPU / 2 ГБ RAM / 25 ГБ SSD**, ОС **Ubuntu 24.04**;
- домен (можно купить у Beget же — тогда DNS настраивается в их панели автоматически);
- ваш SSH-ключ на локальной машине (`~/.ssh/id_ed25519.pub`; если нет — `ssh-keygen -t ed25519`).

## Шаг 0 — КРИТИЧНО: проверка доступа к Kimi API

Сервер в РФ, API Moonshot — снаружи. Доступность из региона не гарантирована провайдером, поэтому проверяем **до** всего остального:

```bash
ssh root@<IP_сервера>
curl -s -o /dev/null -w "%{http_code}\n" https://api.moonshot.ai/v1/models \
  -H "Authorization: Bearer <ваш_api_ключ>"
```

- `200` — всё отлично, едем дальше;
- `401` — сеть в порядке, проблема в ключе/регионе ключа (platform.kimi.ai ≠ platform.kimi.com);
- таймаут/`000` — **стоп**: с этого сервера Kimi API недоступен. Варианты: другой регион VDS (Европа), либо проксирование API-запросов. Решаем до покупки домена.

## Шаг 1. Заказ и первичный доступ

1. Beget → раздел **VPS** → заказать сервер, Ubuntu 24.04.
2. В панели Beget получите IP и root-пароль (или задайте свой SSH-ключ при создании — удобнее).
3. Войдите: `ssh root@<IP>`.

## Шаг 2. Подготовка сервера (bootstrap)

```bash
# с локальной машины:
scp deploy/server-bootstrap.sh root@<IP>:/root/
ssh root@<IP> "bash /root/server-bootstrap.sh"
```

Скрипт ставит Docker (с зеркалами Docker Hub — с российских IP прямые pull'ы иногда отваливаются), ufw (открыты только 22/80/443), fail2ban, автообновления безопасности, создаёт пользователя `deploy` и каталог `/opt/finmodel/site`.

Затем выдайте `deploy` ваш SSH-ключ:

```bash
ssh root@<IP>
cp ~/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys
chown -R deploy:deploy /home/deploy/.ssh
chmod 700 /home/deploy/.ssh && chmod 600 /home/deploy/.ssh/authorized_keys
exit
# проверка: ssh deploy@<IP>  → должно пустить без пароля
```

После этого отключите вход root по паролю: в `/etc/ssh/sshd_config` → `PermitRootLogin prohibit-password`, `PasswordAuthentication no`, `systemctl restart ssh`. **Сначала убедитесь, что вход по ключу работает.**

## Шаг 3. Домен

1. В DNS-панели (Beget или регистратор): A-запись `finmodel.example.com` → `<IP_сервера>`.
2. Проверка: `ping finmodel.example.com` должен резолвиться в IP.
3. TLS выпустит Caddy автоматически при первом обращении — ничего настраивать не нужно.

## Шаг 4. Первый деплой вручную (проверка)

```bash
ssh deploy@<IP>
cd /opt/finmodel/site
git clone https://github.com/vladfa2010/check_bismodel.git .   # если репо приватный — настройте deploy key
cd deploy
DOMAIN=finmodel.example.com docker compose up -d
```

Откройте `https://finmodel.example.com` — должен показаться прототип чата. Сертификат появится через 10–30 секунд после первого захода.

> Репозиторий приватный? GitHub → репо → Settings → Deploy keys → добавить публичный ключ с сервера (создать: `ssh-keygen -t ed25519` от deploy). Либо сделать репо публичным — код прототипа это позволяет.

## Шаг 5. Автодеплой по пушу (GitHub Actions)

Workflow уже лежит в `.github/workflows/deploy-vds.yml`. Осталось задать секреты:

1. На локальной машине создайте **отдельный** ключ для CI: `ssh-keygen -t ed25519 -f ci_deploy` (без пароля).
2. Публичную часть (`ci_deploy.pub`) добавьте на сервер в `/home/deploy/.ssh/authorized_keys` новой строкой.
3. GitHub → репозиторий → **Settings → Secrets and variables → Actions → New repository secret**:
   - `VDS_HOST` = IP сервера (или домен);
   - `VDS_USER` = `deploy`;
   - `VDS_SSH_KEY` = содержимое **приватного** `ci_deploy`.
4. Готово: каждый пуш в `main`, трогающий `index.html` или `deploy/**`, раскатывается на сервер за ~30 секунд. Ручной запуск — вкладка Actions → deploy-vds → Run workflow.

## Шаг 6. Когда появится бэкенд

1. В `deploy/docker-compose.yml` раскомментировать сервисы `app` (и `litestream` для бэкапов SQLite в Backblaze B2).
2. В `deploy/Caddyfile` раскомментировать блок `handle /api/*`.
3. Создать `deploy/.env` на сервере с `MOONSHOT_API_KEY=...` (в git не коммитить!).
4. Дописать в workflow шаг `docker compose up -d --build`.
5. Бэкап файлов: cron на сервере — ночной `tar` `/var/lib/docker/volumes/...appdata` → B2 (`rclone` удобнее всего).

## Эксплуатация — минимум

- Логи: `cd /opt/finmodel/site/deploy && docker compose logs -f caddy`
- Диск под контролем: `df -h` + алерт в UptimeRobot (бесплатно) на `https://домен`
- Обновления ОС: unattended-upgrades уже включены; раз в месяц `apt upgrade` руками
- Если контейнер умер — `restart: unless-stopped` поднимет; если умер сервер — Beget панель → перезагрузка
