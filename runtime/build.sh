#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=container-env.sh
source "$(dirname -- "${BASH_SOURCE[0]}")/container-env.sh"
require_compose
ram_lock
export CODEX_IMAGE="$CODEX_BASE_IMAGE"
docker compose --env-file /dev/null --file "$SCRIPT_DIR/compose.yaml" build codex
if docker image inspect "$CODEX_MAINTAINED_IMAGE" >/dev/null 2>&1; then
    echo "Built $CODEX_BASE_IMAGE; $CODEX_MAINTAINED_IMAGE still takes precedence for sessions."
else
    echo "Built $CODEX_BASE_IMAGE."
fi
