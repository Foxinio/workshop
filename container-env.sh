#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(
    cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
    pwd
)"

: "${WORKSHOP_ROOT:?Run this script through workshop}"
: "${WORKSHOP_ID:?Run this script through workshop}"
BRIDGE_DIR=$CODEX_BRIDGE_DIR

usage() {
    echo "Usage: workshop ${0##*/} (see workshop help)"
}

HOST_UID="$(id -u)"
HOST_GID="$(id -g)"

# shellcheck source=ram-storage.sh
source "$SCRIPT_DIR/ram-storage.sh"
ram_paths

# Compose mounts the RAM working copy, never the disk checkpoint.
export CODEX_RAM_DIR="$RAM_DIR"
export HOST_UID
export HOST_GID

require_compose() {
    command -v docker >/dev/null || { echo "Workshop requires Docker and Docker Compose." >&2; return 1; }
    docker compose version >/dev/null 2>&1 || { echo "Workshop requires the Docker Compose plugin (docker compose)." >&2; return 1; }
    docker info >/dev/null || { echo "Workshop cannot access the Docker daemon; check service and permissions." >&2; return 1; }
}

prepare_container() {
    require_compose
    ram_lock
    ram_start
    ram_no_writers
    sudo -v || { ram_error "sudo privileges are required"; return 1; }
    mkdir -p "$RAM_DIR"/{work,.codex,.cache,.npm,.gradle,.local}
    sudo chown -h -- "$HOST_UID:$HOST_GID" "$RAM_DIR" "$RAM_DIR"/{work,.codex,.cache,.npm,.gradle,.local}
    CODEX_IMAGE=$CODEX_BASE_IMAGE
    if docker image inspect "$CODEX_MAINTAINED_IMAGE" >/dev/null 2>&1; then
        CODEX_IMAGE=$CODEX_MAINTAINED_IMAGE
    fi
    export CODEX_IMAGE
}
