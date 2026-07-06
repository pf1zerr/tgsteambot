#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/steam-price-bot"

cd "$APP_DIR"
git pull --ff-only
sudo systemctl restart steam-price-bot
sudo systemctl status steam-price-bot --no-pager
