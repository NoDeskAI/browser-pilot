#!/bin/bash
sleep 3
export DISPLAY=:99.0
exec /usr/lib/chromium/chromium \
  --no-sandbox \
  --test-type \
  --disable-gpu \
  --disable-dev-shm-usage \
  --disable-blink-features=AutomationControlled \
  --disable-infobars \
  --lang=zh-CN \
  --window-size=1366,768 \
  --start-maximized \
  https://www.baidu.com
