#!/usr/bin/env bash
# Join the user's Tailscale tailnet from a Cursor Cloud Agent VM.
# Uses userspace networking so the kernel TUN device is not required.
#
# Secret (Cursor environment secrets UI — never commit this value):
#   TAILSCALE_AUTH_KEY
#
# After join, SSH to the Mac mini (see scripts/ssh-mac-mini.sh):
#   ALL_PROXY=socks5://127.0.0.1:1055 ssh billkim@100.124.238.128
set -euo pipefail

AUTH_KEY="${TAILSCALE_AUTH_KEY:-}"
SOCKS_PORT="${TAILSCALE_SOCKS_PORT:-1055}"
HOSTNAME="${TAILSCALE_HOSTNAME:-cursor-openclaw-fix}"
TAILSCALE_BIN="${TAILSCALE_BIN:-}"
TAILSCALED_BIN="${TAILSCALED_BIN:-}"

if [[ -z "${TAILSCALE_BIN}" ]]; then
  TAILSCALE_BIN="$(command -v tailscale || true)"
fi
if [[ -z "${TAILSCALED_BIN}" ]]; then
  TAILSCALED_BIN="$(command -v tailscaled || true)"
fi
if [[ -z "${TAILSCALED_BIN}" && -x /usr/sbin/tailscaled ]]; then
  TAILSCALED_BIN=/usr/sbin/tailscaled
fi
if [[ -z "${TAILSCALE_BIN}" && -x /usr/bin/tailscale ]]; then
  TAILSCALE_BIN=/usr/bin/tailscale
fi

if [[ -z "${AUTH_KEY}" ]]; then
  echo "cloud-agent-start-tailscale: TAILSCALE_AUTH_KEY is unset; skipping Tailscale."
  echo "Add it in Cursor Dashboard → Cloud Agents → Secrets as TAILSCALE_AUTH_KEY."
  exit 0
fi

if [[ -z "${TAILSCALE_BIN}" || ! -x "${TAILSCALE_BIN}" ]]; then
  echo "cloud-agent-start-tailscale: tailscale CLI not found; skipping."
  exit 0
fi

backend=""
if timeout 2 "${TAILSCALE_BIN}" status --json >/dev/null 2>&1; then
  backend="$(timeout 2 "${TAILSCALE_BIN}" status --json 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin).get('BackendState',''))" 2>/dev/null || true)"
fi
if [[ "${backend}" == "Running" ]]; then
  echo "cloud-agent-start-tailscale: already Running as ${HOSTNAME}."
  exit 0
fi

if [[ -z "${TAILSCALED_BIN}" || ! -x "${TAILSCALED_BIN}" ]]; then
  echo "cloud-agent-start-tailscale: tailscaled not found; cannot start userspace networking."
  exit 0
fi

# Default LocalAPI socket is /var/run/tailscale/tailscaled.sock. /var/run is
# tmpfs, so the directory must be created on every boot (not during install).
SOCKET_DIR="/var/run/tailscale"
if [[ ! -d "${SOCKET_DIR}" || ! -w "${SOCKET_DIR}" ]]; then
  if [[ "$(id -u)" -ne 0 ]] && command -v sudo >/dev/null 2>&1; then
    sudo mkdir -p "${SOCKET_DIR}"
    sudo chown "$(id -u):$(id -g)" "${SOCKET_DIR}"
  else
    mkdir -p "${SOCKET_DIR}"
  fi
fi

if ! pgrep -x tailscaled >/dev/null 2>&1; then
  # Userspace: SOCKS5 on loopback. Future Cloud Agents should use this, not kernel TUN.
  nohup "${TAILSCALED_BIN}" \
    --tun=userspace-networking \
    --socks5-server="127.0.0.1:${SOCKS_PORT}" \
    --outbound-http-proxy-listen="127.0.0.1:${SOCKS_PORT}" \
    >/tmp/tailscaled-userspace.log 2>&1 &
fi

for _ in $(seq 1 30); do
  if [[ -S "${SOCKET_DIR}/tailscaled.sock" ]] && timeout 2 "${TAILSCALE_BIN}" status >/dev/null 2>&1; then
    break
  fi
  if ! pgrep -x tailscaled >/dev/null 2>&1; then
    echo "cloud-agent-start-tailscale: tailscaled exited; see /tmp/tailscaled-userspace.log"
    exit 1
  fi
  sleep 1
done

if ! timeout 2 "${TAILSCALE_BIN}" status >/dev/null 2>&1; then
  echo "cloud-agent-start-tailscale: tailscaled did not become ready; see /tmp/tailscaled-userspace.log"
  exit 1
fi

# Do not print AUTH_KEY. It may still appear in this process's argv.
"${TAILSCALE_BIN}" up \
  --auth-key="${AUTH_KEY}" \
  --hostname="${HOSTNAME}" \
  --accept-routes \
  --ssh=false

echo "cloud-agent-start-tailscale: up as ${HOSTNAME}; SOCKS5 127.0.0.1:${SOCKS_PORT}"
