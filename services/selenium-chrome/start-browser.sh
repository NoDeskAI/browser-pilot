#!/bin/bash
sleep 3
export DISPLAY=:99.0
rm -f /home/seluser/chrome-data/manual/SingletonLock \
      /home/seluser/chrome-data/manual/SingletonCookie \
      /home/seluser/chrome-data/manual/SingletonSocket 2>/dev/null
W=${SE_SCREEN_WIDTH:-1280}
H=${SE_SCREEN_HEIGHT:-800}
LANG_CODE=$(cat /tmp/browser-lang 2>/dev/null || echo "zh-CN")
LOCALE_ID=$(echo "$LANG_CODE" | sed 's/-/_/')
export LANGUAGE="${LOCALE_ID}"

EXTRA_ARGS=""
if [ -n "${BROWSER_UA:-}" ]; then
  EXTRA_ARGS="${EXTRA_ARGS} --user-agent=${BROWSER_UA}"
fi
if [ -n "${BROWSER_PROXY:-}" ]; then
  EXTRA_ARGS="${EXTRA_ARGS} --proxy-server=${BROWSER_PROXY}"
fi

FP_SEED=${FINGERPRINT_SEED:-$RANDOM}
sed -i "s/__FP_SEED__/${FP_SEED}/" /opt/stealth-ext/stealth.js

exec /usr/lib/chromium/chromium \
  --no-sandbox \
  --test-type \
  --no-default-browser-check \
  --disable-dev-shm-usage \
  --disable-blink-features=AutomationControlled \
  --disable-component-update \
  --disable-features=AutomationControlled,TranslateUI \
  --disable-hang-monitor \
  --disable-popup-blocking \
  --disable-prompt-on-repost \
  --disable-background-networking \
  --disable-sync \
  --disable-session-crashed-bubble \
  --metrics-recording-only \
  --lang=${LANG_CODE} \
  --window-size=${W},${H} \
  --start-maximized \
  --user-data-dir=/home/seluser/chrome-data/manual \
  --no-first-run \
  --disable-gpu \
  --ignore-certificate-errors \
  --load-extension=/opt/stealth-ext \
  --remote-debugging-port=9222 \
  --remote-debugging-address=0.0.0.0 \
  --remote-allow-origins=* \
  ${EXTRA_ARGS}
