#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/steam-price-bot"
REPO_URL="${REPO_URL:-https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git}"
SERVICE_FILE="/etc/systemd/system/steam-price-bot.service"

sudo apt-get update
sudo apt-get install -y git python3

if [ ! -d "$APP_DIR/.git" ]; then
  sudo git clone "$REPO_URL" "$APP_DIR"
else
  cd "$APP_DIR"
  sudo git pull --ff-only
fi

sudo chown -R ubuntu:ubuntu "$APP_DIR"

if [ ! -f "$APP_DIR/.env" ]; then
  sudo cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  echo "Created $APP_DIR/.env. Fill it before starting the service."
fi

sudo cp "$APP_DIR/deploy/steam-price-bot.service" "$SERVICE_FILE"
sudo systemctl daemon-reload
sudo systemctl enable steam-price-bot

echo "Setup complete."
echo "Edit $APP_DIR/.env, then run:"
echo "  sudo systemctl restart steam-price-bot"
echo "  sudo systemctl status steam-price-bot"
