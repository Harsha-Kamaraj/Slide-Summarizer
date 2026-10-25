#!/usr/bin/env bash
# Deploy the latest code from GitHub: run on the server from the repo.
set -euo pipefail
cd "$(dirname "$0")"
git pull --ff-only
sudo docker compose up -d --build
sudo docker image prune -f >/dev/null  # drop old builds to save disk
echo "Updated. Health: $(curl -fsS "https://$(grep '^DOMAIN=' .env | cut -d= -f2)/health")"
