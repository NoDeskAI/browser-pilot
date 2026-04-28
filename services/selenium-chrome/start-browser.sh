#!/bin/bash
sleep 3
export DISPLAY=:99.0
export FONTCONFIG_FILE=/opt/browser-fontconfig/fonts.conf
mkdir -p /tmp/browser-fontconfig-cache 2>/dev/null || true
CHROME_BIN="${CHROME_BIN:-}"
if [ -z "$CHROME_BIN" ]; then
  if command -v google-chrome >/dev/null 2>&1; then
    CHROME_BIN="$(command -v google-chrome)"
  elif command -v chromium >/dev/null 2>&1; then
    CHROME_BIN="$(command -v chromium)"
  elif [ -x /usr/lib/chromium/chromium ]; then
    CHROME_BIN="/usr/lib/chromium/chromium"
  else
    echo "ERROR: no Chrome/Chromium binary found" >&2
    exit 127
  fi
fi
rm -f /home/seluser/chrome-data/manual/SingletonLock \
      /home/seluser/chrome-data/manual/SingletonCookie \
      /home/seluser/chrome-data/manual/SingletonSocket 2>/dev/null
PREF_PATH="/home/seluser/chrome-data/manual/Default/Preferences"
LOCAL_STATE_PATH="/home/seluser/chrome-data/manual/Local State"
mkdir -p "$(dirname "$PREF_PATH")"
PREF_PATH="$PREF_PATH" LOCAL_STATE_PATH="$LOCAL_STATE_PATH" /usr/bin/python3 - <<'PY'
import json
import os

def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def write_json(path, data):
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(tmp_path, path)

def mark_clean_exit(data):
    profile = data.get("profile")
    if not isinstance(profile, dict):
        profile = {}
        data["profile"] = profile
    profile["exit_type"] = "Normal"
    profile["exited_cleanly"] = True
    return profile

path = os.environ["PREF_PATH"]
prefs = load_json(path)

prefs["credentials_enable_service"] = False
profile = mark_clean_exit(prefs)
profile["password_manager_enabled"] = False
write_json(path, prefs)

local_state_path = os.environ["LOCAL_STATE_PATH"]
if os.path.exists(local_state_path):
    local_state = load_json(local_state_path)
    mark_clean_exit(local_state)
    write_json(local_state_path, local_state)
PY
W=${SE_SCREEN_WIDTH:-1280}
H=${SE_SCREEN_HEIGHT:-800}
LANG_CODE=$(cat /tmp/browser-lang 2>/dev/null || echo "${BROWSER_LANG:-zh-CN}")
LOCALE_ID=$(echo "$LANG_CODE" | sed 's/-/_/')
export LANGUAGE="${LOCALE_ID}"

EXTRA_ARGS=()
if [ -n "${BROWSER_UA:-}" ]; then
  EXTRA_ARGS+=("--user-agent=${BROWSER_UA}")
fi
if [ -n "${BROWSER_PROXY:-}" ]; then
  EXTRA_ARGS+=("--proxy-server=${BROWSER_PROXY}")
  EXTRA_ARGS+=("--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1")
fi

if [ -n "${FINGERPRINT_PROFILE:-}" ]; then
  FP_JSON=$(printf '%s' "$FINGERPRINT_PROFILE" | base64 -d)
  REAL_VER=$("$CHROME_BIN" --version 2>/dev/null | grep -oP '[\d.]+' | head -1)
  PATCHED=$(printf '%s' "$FP_JSON" | REAL_VER="$REAL_VER" LANG_CODE="$LANG_CODE" /usr/bin/python3 -c '
import json
import os
import re
import sys

fp = json.load(sys.stdin)
lang = (os.environ.get("LANG_CODE") or "zh-CN").strip() or "zh-CN"
base = lang.split("-", 1)[0]
languages = [lang]
if base != lang:
    languages.append(base)
if lang != "en" and base != "en":
    languages.append("en")
nav = fp.setdefault("navigator", {})
nav["language"] = lang
nav["languages"] = languages

rv = (os.environ.get("REAL_VER") or "").strip()
version_changed = bool(rv and fp.get("chromeVersion") != rv)
if rv:
    fp["chromeVersion"] = rv
    for key in ("userAgent", "appVersion"):
        value = nav.get(key)
        if isinstance(value, str) and value:
            nav[key] = re.sub(r"Chrome/[\d.]+", "Chrome/" + rv, value)

cv = str(fp.get("chromeVersion") or rv or "124.0.0.0")
major = cv.split(".")[0]
ch = fp.setdefault("clientHints", {})
if version_changed or not isinstance(ch.get("brands"), list) or not ch.get("brands"):
    ch["brands"] = [
        {"brand": "Chromium", "version": major},
        {"brand": "Google Chrome", "version": major},
        {"brand": "Not=A?Brand", "version": "99"},
    ]
if version_changed or not isinstance(ch.get("fullVersionList"), list) or not ch.get("fullVersionList"):
    ch["fullVersionList"] = [
        {"brand": "Chromium", "version": cv},
        {"brand": "Google Chrome", "version": cv},
        {"brand": "Not=A?Brand", "version": "99.0.0.0"},
    ]
if version_changed or not ch.get("fullVersion"):
    ch["fullVersion"] = cv
if not ch.get("uaFullVersion"):
    ch["uaFullVersion"] = ch["fullVersion"]

print(json.dumps(fp, separators=(",", ":")))
' 2>/dev/null)
  if [ -n "$PATCHED" ]; then
    FP_JSON="$PATCHED"
  fi
  printf '%s' "$FP_JSON" > /tmp/fingerprint-profile.json
  printf 'var __FP__=%s;\n' "$FP_JSON" > /opt/stealth-ext/fp-profile.js

  FP_TZ=$(printf '%s' "$FP_JSON" | /usr/bin/python3 -c "import sys,json; print(json.load(sys.stdin).get('timezone','UTC'))" 2>/dev/null)
  if [ -n "$FP_TZ" ] && [ "$FP_TZ" != "UTC" ]; then
    export TZ="$FP_TZ"
    echo "TZ set to $FP_TZ"
  fi
  if [ -n "$REAL_VER" ]; then
    FP_VER=$(printf '%s' "$FP_JSON" | /usr/bin/python3 -c "import sys,json; print(json.load(sys.stdin).get('chromeVersion',''))" 2>/dev/null)
    if [ -n "$FP_VER" ] && [ "$FP_VER" != "$REAL_VER" ]; then
      echo "WARN: profile chromeVersion=$FP_VER but real=$REAL_VER, profile patch failed"
    fi
  fi
fi

exec "$CHROME_BIN" \
  --no-sandbox \
  --test-type \
  --no-default-browser-check \
  --disable-dev-shm-usage \
  --disable-blink-features=AutomationControlled \
  --disable-component-update \
  --disable-features=AutomationControlled,TranslateUI,AsyncDns,DnsOverHttpsTemplates,UseDnsHttpsSvcb,UseDnsHttpsSvcbAlpn \
  --disable-async-dns \
  --dns-prefetch-disable \
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
  --use-gl=angle --use-angle=gl-egl \
  --ignore-gpu-blocklist \
  --disable-gpu-driver-bug-workarounds \
  --enable-unsafe-swiftshader \
  --ignore-certificate-errors \
  --load-extension=/opt/stealth-ext \
  --remote-debugging-port=9222 \
  --remote-debugging-address=0.0.0.0 \
  --remote-allow-origins=* \
  "${EXTRA_ARGS[@]}"
