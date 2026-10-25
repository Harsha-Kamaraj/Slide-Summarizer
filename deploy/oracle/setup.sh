#!/usr/bin/env bash
# One-time setup on a fresh Ubuntu server (Oracle Cloud Always Free VM).
# Run from the repo:  bash deploy/oracle/setup.sh
# The Groq key is read from $LLM_API_KEY if set, otherwise asked for (hidden).
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Installing Docker"
if ! command -v docker >/dev/null; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq docker.io docker-compose-v2
  sudo usermod -aG docker "$USER"
fi

echo "==> Opening ports 80 and 443 in the server's firewall"
# Oracle's Ubuntu images block everything but SSH with iptables, on top of
# the cloud security list (which must also allow 80/443: see docs/DEPLOY.md).
for port in 80 443; do
  if ! sudo iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null; then
    sudo iptables -I INPUT 5 -p tcp --dport "$port" -m state --state NEW -j ACCEPT
  fi
done
sudo apt-get install -y -qq iptables-persistent >/dev/null 2>&1 || true
sudo netfilter-persistent save >/dev/null 2>&1 || true

echo "==> Writing settings (.env)"
if [ ! -f .env ]; then
  cp env.example .env
  chmod 600 .env  # secrets: readable by this user only
  ip=$(curl -fsS https://api.ipify.org)
  domain="${ip//./-}.sslip.io"
  key="${LLM_API_KEY:-}"
  if [ -z "$key" ]; then read -rsp "Groq API key: " key; echo; fi
  sed -i "s|^DOMAIN=.*|DOMAIN=$domain|; s|^LLM_API_KEY=.*|LLM_API_KEY=$key|; s|^FALLBACK_API_KEY=.*|FALLBACK_API_KEY=$key|" .env
fi
domain=$(grep '^DOMAIN=' .env | cut -d= -f2)

echo "==> Building and starting (first build takes a few minutes)"
sudo docker compose up -d --build

echo "==> Waiting for https://$domain/health"
for _ in $(seq 1 60); do
  if curl -fsS "https://$domain/health" 2>/dev/null; then
    echo; echo "Live at https://$domain"; exit 0
  fi
  sleep 5
done
echo "Not reachable yet. Check: sudo docker compose -f deploy/oracle/docker-compose.yml logs" >&2
exit 1
