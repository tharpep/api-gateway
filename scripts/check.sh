#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
V="${GATEWAY_CHECK_VENV:-$HOME/.cache/gateway-check-venv}"
[ -x "$V/bin/python" ] || uv venv -q -p 3.12 "$V"
VIRTUAL_ENV="$V" uvx -q --from poetry==2.1.4 poetry install -q --no-interaction --no-root
uvx -q ruff@0.9.10 check --select E9,F63,F7,F82 .
"$V/bin/python" -m pytest -q
