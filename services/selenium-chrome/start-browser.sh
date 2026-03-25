#!/bin/bash
sleep 3
export DISPLAY=:99.0

W=${SE_SCREEN_WIDTH:-1280}
H=${SE_SCREEN_HEIGHT:-800}

# Hide fluxbox taskbar
if [ -f "$HOME/.fluxbox/init" ]; then
  sed -i 's/session.screen0.toolbar.visible:.*/session.screen0.toolbar.visible: false/' "$HOME/.fluxbox/init"
  if ! grep -q 'session.screen0.toolbar.visible' "$HOME/.fluxbox/init"; then
    echo 'session.screen0.toolbar.visible: false' >> "$HOME/.fluxbox/init"
  fi
  killall -USR2 fluxbox 2>/dev/null || true
  sleep 0.5
fi

exec /usr/lib/chromium/chromium \
  --no-sandbox \
  --test-type \
  --disable-gpu \
  --disable-dev-shm-usage \
  --disable-blink-features=AutomationControlled \
  --disable-infobars \
  --no-first-run \
  --no-default-browser-check \
  --disable-features=TranslateUI \
  --disable-session-crashed-bubble \
  --hide-crash-restore-bubble \
  --lang=zh-CN \
  --window-size=${W},${H} \
  --start-fullscreen \
  https://www.baidu.com
