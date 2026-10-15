#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=container-env.sh
source "$(dirname -- "${BASH_SOURCE[0]}")/container-env.sh"

tool=$1
shift
prepare_container
SESSION_CONTAINER="${WORKSHOP_ID}-session"
existing=$(docker ps -aq --filter "name=^/${SESSION_CONTAINER}$")
if [[ -n "$existing" ]]; then
    docker rm "$SESSION_CONTAINER" >/dev/null
fi
SESSION_REMOVE=true
session_traps
echo "Starting $tool; home changes will be saved when the session exits."
ram_run docker compose --env-file /dev/null --file "${SCRIPT_DIR}/compose.yaml" \
    run --name "$SESSION_CONTAINER" --pull never "$tool" "$@"
