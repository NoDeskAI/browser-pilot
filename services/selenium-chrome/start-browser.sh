#!/bin/bash
sleep 3
export DISPLAY=:99.0
rm -f /home/seluser/chrome-data/manual/SingletonLock \
      /home/seluser/chrome-data/manual/SingletonCookie \
      /home/seluser/chrome-data/manual/SingletonSocket 2>/dev/null
W=${SE_SCREEN_WIDTH:-1280}
H=${SE_SCREEN_HEIGHT:-800}
LANG_CODE=$(cat /tmp/browser-lang 2>/dev/null || echo "${BROWSER_LANG:-zh-CN}")
LOCALE_ID=$(echo "$LANG_CODE" | sed 's/-/_/')
export LANGUAGE="${LOCALE_ID}"

EXTRA_ARGS=""
if [ -n "${BROWSER_UA:-}" ]; then
  EXTRA_ARGS="${EXTRA_ARGS} --user-agent=${BROWSER_UA}"
fi
if [ -n "${BROWSER_PROXY:-}" ]; then
  EXTRA_ARGS="${EXTRA_ARGS} --proxy-server=${BROWSER_PROXY}"
fi

if [ -n "${FINGERPRINT_PROFILE:-}" ]; then
  FP_JSON=$(echo "$FINGERPRINT_PROFILE" | base64 -d)
  echo "var __FP__=${FP_JSON};" > /opt/stealth-ext/fp-profile.js

  FP_TZ=$(echo "$FP_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('timezone','UTC'))" 2>/dev/null)
  if [ -n "$FP_TZ" ] && [ "$FP_TZ" != "UTC" ]; then
    export TZ="$FP_TZ"
    echo "TZ set to $FP_TZ"
  fi

  REAL_VER=$(/usr/lib/chromium/chromium --version 2>/dev/null | grep -oP '[\d.]+' | head -1)
  if [ -n "$REAL_VER" ]; then
    FP_VER=$(echo "$FP_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('chromeVersion',''))" 2>/dev/null)
    if [ -n "$FP_VER" ] && [ "$FP_VER" != "$REAL_VER" ]; then
      echo "WARN: profile chromeVersion=$FP_VER but real=$REAL_VER, patching fp-profile.js"
      PATCHED=$(echo "$FP_JSON" | python3 -c "
import sys,json,re
fp=json.load(sys.stdin)
rv='$REAL_VER'
fp['chromeVersion']=rv
nav=fp.get('navigator',{})
for k in ('userAgent','appVersion'):
    if k in nav:
        nav[k]=re.sub(r'Chrome/[\d.]+','Chrome/'+rv,nav[k])
print(json.dumps(fp,separators=(',',':')))
" 2>/dev/null)
      if [ -n "$PATCHED" ]; then
        echo "var __FP__=${PATCHED};" > /opt/stealth-ext/fp-profile.js
      fi
    fi
  fi
fi

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
  --use-gl=angle \
  --ignore-certificate-errors \
  --load-extension=/opt/stealth-ext \
  --remote-debugging-port=9222 \
  --remote-debugging-address=0.0.0.0 \
  --remote-allow-origins=* \
  ${EXTRA_ARGS}
