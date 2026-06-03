#!/bin/bash
set -euo pipefail

export DISPLAY="${DISPLAY:-:99}"
W="${SE_SCREEN_WIDTH:-1280}"
H="${SE_SCREEN_HEIGHT:-800}"
D="${SE_SCREEN_DEPTH:-24}"

rm -f "/tmp/.X${DISPLAY#:}-lock" "/tmp/.X11-unix/X${DISPLAY#:}" 2>/dev/null || true
Xvfb "$DISPLAY" -screen 0 "${W}x${H}x${D}" -nolisten tcp &

for _ in $(seq 1 50); do
  if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done

FLUXBOX_HOME="${HOME:-/root}"
mkdir -p "$FLUXBOX_HOME/.fluxbox"
cat > "$FLUXBOX_HOME/.fluxbox/apps" <<'EOF'
[app] (class=Chromium-browser)
  [Maximized] {yes}
[end]
[app] (class=Google-chrome)
  [Maximized] {yes}
[end]
[app] (name=chromium-browser)
  [Maximized] {yes}
[end]
[app] (name=google-chrome)
  [Maximized] {yes}
[end]
EOF
cat > "$FLUXBOX_HOME/.fluxbox/init" <<'EOF'
session.screen0.toolbar.visible: false
EOF

fluxbox >/tmp/fluxbox.log 2>&1 &
# Some fluxbox defaults can spawn an xmessage warning when no wallpaper setter
# is available. It is harmless, but it covers the noVNC screen and looks like a
# failed browser start, so remove it if the base image still emits one.
(sleep 1; pkill -f "fbsetbg: I can't find an app" 2>/dev/null || true) &
VNC_PASSWORD="${BP_VNC_PASSWORD:-${SE_VNC_PASSWORD:-}}"
if [[ -z "$VNC_PASSWORD" ]]; then
  echo "BP_VNC_PASSWORD or SE_VNC_PASSWORD is required" >&2
  exit 1
fi
x11vnc -storepasswd "$VNC_PASSWORD" /tmp/x11vnc.pass >/dev/null 2>&1
chmod 600 /tmp/x11vnc.pass
x11vnc -display "$DISPLAY" -forever -shared -rfbauth /tmp/x11vnc.pass -listen 0.0.0.0 -rfbport 5900 >/tmp/x11vnc.log 2>&1 &
websockify --web=/usr/share/novnc 0.0.0.0:7900 localhost:5900 >/tmp/websockify.log 2>&1 &

exec /usr/local/bin/bp-cloak-driver
