#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=container-env.sh
source "$(dirname -- "${BASH_SOURCE[0]}")/container-env.sh"

prepare_container
SESSION_CONTAINER="${WORKSHOP_ID}-session"
SESSION_REMOVE=true
session_traps
# What is teh --env-file flat?
docker compose --env-file /dev/null --file "${SCRIPT_DIR}/compose.yaml" \
    run --name "$SESSION_CONTAINER" --no-build --pull never codex "$@"
