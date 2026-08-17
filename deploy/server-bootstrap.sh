#!/usr/bin/env bash
# FinModel AI — первичная подготовка VDS (Beget, Ubuntu 24.04)
# Запуск один раз от root: bash server-bootstrap.sh
set -euo pipefail

echo "==> 1/7 Обновление системы"
apt-get update -qq && apt-get upgrade -y -qq

echo "==> 2/7 Базовые пакеты"
apt-get install -y -qq curl git ufw fail2ban unattended-upgrades

echo "==> 3/7 Docker + compose plugin"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi

# Зеркала Docker Hub — с российских IP прямые pull'ы иногда не проходят
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'EOF'
{
  "registry-mirrors": [
    "https://dockerhub.timeweb.cloud",
    "https://mirror.gcr.io"
  ],
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
EOF
systemctl restart docker
systemctl enable docker

echo "==> 4/7 Файрвол: только SSH, HTTP, HTTPS"
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "==> 5/7 fail2ban (защита SSH от перебора)"
systemctl enable --now fail2ban

echo "==> 6/7 Пользователь deploy (деплой без root)"
if ! id deploy >/dev/null 2>&1; then
  useradd -m -s /bin/bash deploy
  usermod -aG docker deploy
  mkdir -p /home/deploy/.ssh
  # Скопируйте сюда свой публичный ключ после первого входа:
  # cp ~/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys
  # chown -R deploy:deploy /home/deploy/.ssh && chmod 700 /home/deploy/.ssh && chmod 600 /home/deploy/.ssh/authorized_keys
fi
mkdir -p /opt/finmodel/site
chown -R deploy:deploy /opt/finmodel

echo "==> 7/7 Автообновления безопасности"
dpkg-reconfigure -f noninteractive unattended-upgrades || true

echo
echo "Готово. Дальше по docs/deploy-vds.md: ключи для deploy, GitHub Secrets, первый деплой."
echo "Проверка Docker:  docker run --rm hello-world"
