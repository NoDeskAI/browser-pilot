#!/bin/bash
sleep 3
export DISPLAY=:99.0
exec /usr/lib/chromium/chromium \
  --no-sandbox \
  --disable-gpu \
  --disable-dev-shm-usage \
  --disable-blink-features=AutomationControlled \
  --start-maximized \
  https://www.baidu.com
