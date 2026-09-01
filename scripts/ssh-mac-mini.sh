#!/usr/bin/env bash
# SSH to the Mac mini over Tailscale from a Cloud Agent VM.
# If userspace Tailscale is listening on 127.0.0.1:1055, route SSH through SOCKS5.
set -euo pipefail

HOST="${MAC_MINI_SSH_HOST:-billkim@100.124.238.128}"
SOCKS="${TAILSCALE_SOCKS:-127.0.0.1:1055}"
SOCKS_HOST="${SOCKS%:*}"
SOCKS_PORT="${SOCKS##*:}"

ssh_opts=(-o StrictHostKeyChecking=accept-new -o BatchMode=yes)

if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -q ":${SOCKS_PORT} "; then
  if command -v ncat >/dev/null 2>&1; then
    ssh_opts+=(-o "ProxyCommand=ncat --proxy-type socks5 --proxy ${SOCKS} %h %p")
  elif command -v nc >/dev/null 2>&1; then
    ssh_opts+=(-o "ProxyCommand=nc -X 5 -x ${SOCKS} %h %p")
  else
    export ALL_PROXY="socks5://${SOCKS}"
  fi
fi

exec ssh "${ssh_opts[@]}" "${HOST}" "$@"
