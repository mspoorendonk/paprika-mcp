#!/bin/bash
# Deploy paprika-mcp on its host and verify it (shared MCP standard, section 5.3).
# Usage (from the project folder, any machine with ssh access):  bash scripts/deploy.sh [--test]
# This server is a git clone on the host: pull, install dependencies, optionally run the tests,
# restart the unit, then check is-active and the unauthenticated /health endpoint.
# HOST and LAN_IP come from the environment so no hostnames live in this (possibly public) repo.
set -euo pipefail
: "${HOST:?set HOST to the ssh alias of the MCP host}"
: "${LAN_IP:?set LAN_IP to the host LAN IP the primary listener binds}"
DEST=project/paprika-mcp

ssh "$HOST" "cd $DEST && git pull --ff-only && ~/.local/bin/uv sync -q"
if [ "${1:-}" = "--test" ]; then
  ssh "$HOST" "cd $DEST && ~/.local/bin/uv run pytest -q"
fi
ssh "$HOST" "sudo -n /usr/bin/systemctl restart paprika-mcp && sleep 3 && sudo -n /usr/bin/systemctl is-active paprika-mcp && curl -fsS http://$LAN_IP:8000/health && echo"
