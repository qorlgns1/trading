#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/quant-trend-lab}"
sudo install -m 0644 "$APP_DIR/infra/systemd/quant-trend-lab.service" /etc/systemd/system/
sudo install -m 0644 "$APP_DIR/infra/systemd/quant-trend-backup.service" /etc/systemd/system/
sudo install -m 0644 "$APP_DIR/infra/systemd/quant-trend-backup.timer" /etc/systemd/system/
sudo install -m 0644 "$APP_DIR/infra/systemd/quant-trend-retention.service" /etc/systemd/system/
sudo install -m 0644 "$APP_DIR/infra/systemd/quant-trend-retention.timer" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable quant-trend-lab.service quant-trend-backup.timer quant-trend-retention.timer
sudo systemctl start quant-trend-backup.timer quant-trend-retention.timer
