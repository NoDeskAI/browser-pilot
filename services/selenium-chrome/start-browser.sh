#!/bin/bash
sleep 3
export DISPLAY=:99.0

W=${SE_SCREEN_WIDTH:-1280}
H=${SE_SCREEN_HEIGHT:-800}

exec /usr/lib/chromium/chromium \
  --no-sandbox \
  --disable-gpu \
  --disable-dev-shm-usage \
  --disable-blink-features=AutomationControlled \
  --disable-infobars \
  --lang=zh-CN \
  --window-size=${W},${H} \
  --start-maximized \
  https://www.baidu.com
