#!/bin/bash
sleep 3
export DISPLAY=:99.0
W=${SE_SCREEN_WIDTH:-1280}
H=${SE_SCREEN_HEIGHT:-800}
LANG_CODE=$(cat /tmp/browser-lang 2>/dev/null || echo "zh-CN")
exec /usr/lib/chromium/chromium \
  --no-sandbox \
  --test-type \
  --disable-gpu \
  --disable-dev-shm-usage \
  --disable-blink-features=AutomationControlled \
  --disable-infobars \
  --lang=${LANG_CODE} \
  --window-size=${W},${H} \
  --start-maximized \
  https://www.baidu.com
