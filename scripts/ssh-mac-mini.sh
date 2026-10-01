#!/usr/bin/env bash
# SSH to the Mac mini over Tailscale from a Cloud Agent VM.
# Prefer `tailscale nc` (works with userspace networking). Fall back to SOCKS5
# on 127.0.0.1:1055 via ncat/nc. OpenSSH does not honor ALL_PROXY.
set -euo pipefail

HOST="${MAC_MINI_SSH_HOST:-billkim@100.124.238.128}"
SOCKS="${TAILSCALE_SOCKS:-127.0.0.1:1055}"
SOCKS_PORT="${SOCKS##*:}"
TAILSCALE_BIN="${TAILSCALE_BIN:-}"
if [[ -z "${TAILSCALE_BIN}" ]]; then
  TAILSCALE_BIN="$(command -v tailscale || true)"
fi

ssh_opts=(-o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=15)

if [[ -n "${TAILSCALE_BIN}" && -x "${TAILSCALE_BIN}" ]]; then
  ssh_opts+=(-o "ProxyCommand=${TAILSCALE_BIN} nc %h %p")
elif command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -q ":${SOCKS_PORT} "; then
  if command -v ncat >/dev/null 2>&1; then
    ssh_opts+=(-o "ProxyCommand=ncat --proxy-type socks5 --proxy ${SOCKS} %h %p")
  elif command -v nc >/dev/null 2>&1; then
    ssh_opts+=(-o "ProxyCommand=nc -X 5 -x ${SOCKS} %h %p")
  else
    echo "ssh-mac-mini: userspace SOCKS is on ${SOCKS} but neither tailscale nc, ncat, nor nc is available" >&2
    exit 1
  fi
fi

exec ssh "${ssh_opts[@]}" "${HOST}" "$@"
