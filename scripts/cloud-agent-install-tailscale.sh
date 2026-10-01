#!/usr/bin/env bash
# Idempotent Tailscale CLI install for Cursor Cloud Agent VMs.
set -euo pipefail

if command -v tailscale >/dev/null 2>&1 && command -v tailscaled >/dev/null 2>&1; then
  echo "cloud-agent-install-tailscale: already installed ($(tailscale version 2>/dev/null | head -1))"
  exit 0
fi

if [[ "$(id -u)" -ne 0 ]] && command -v sudo >/dev/null 2>&1; then
  curl -fsSL https://tailscale.com/install.sh | sudo sh
else
  curl -fsSL https://tailscale.com/install.sh | sh
fi

echo "cloud-agent-install-tailscale: installed ($(tailscale version 2>/dev/null | head -1))"
