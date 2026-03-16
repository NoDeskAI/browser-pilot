#!/bin/bash
set -e

Xvfb :1 -screen 0 1920x1080x24 +extension GLX &
sleep 1

export DISPLAY=:1
fluxbox &
sleep 0.5

chromium-browser \
  --no-sandbox \
  --disable-dev-shm-usage \
  --start-maximized \
  --no-first-run \
  --disable-default-apps \
  --disable-infobars \
  "https://www.baidu.com" &

x11vnc -display :1 -nopw -forever -shared -rfbport 5900 -noxdamage &
python3 /nav-api.py &
sleep 0.5

/opt/novnc/utils/novnc_proxy \
  --vnc localhost:5900 \
  --listen 6080 \
  --web /opt/novnc
