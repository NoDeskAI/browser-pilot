#!/bin/sh
set -eu

if [ ! -f /config/client.ovpn ]; then
  echo "missing /config/client.ovpn" >&2
  exit 2
fi

auth_arg=""
if [ -f /config/auth.txt ]; then
  auth_arg="--auth-user-pass /config/auth.txt"
fi

openvpn --config /config/client.ovpn $auth_arg &
openvpn_pid="$!"

tinyproxy -d -c /etc/tinyproxy/tinyproxy.conf &
proxy_pid="$!"

cleanup() {
  kill "$openvpn_pid" "$proxy_pid" 2>/dev/null || true
  wait "$openvpn_pid" "$proxy_pid" 2>/dev/null || true
}
trap cleanup INT TERM

while true; do
  if ! kill -0 "$openvpn_pid" 2>/dev/null; then
    echo "openvpn exited" >&2
    cleanup
    exit 1
  fi
  if ! kill -0 "$proxy_pid" 2>/dev/null; then
    echo "tinyproxy exited" >&2
    cleanup
    exit 1
  fi
  sleep 2
done
