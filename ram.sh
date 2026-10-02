#!/usr/bin/env bash
set -euo pipefail

action=${1:-help}
case "$action" in
    start|save|stop|reset|purge|status) shift ;;
    *) echo "Usage: workshop ram {start|save|stop|reset|purge|status}"; exit 2 ;;
esac
# shellcheck source=container-env.sh
source "$(dirname -- "${BASH_SOURCE[0]}")/container-env.sh"
[[ $# == 0 ]] || { usage >&2; exit 2; }
# Status is read-only and remains available while a session holds the lock.
if [[ "$action" != status ]]; then
    ram_traps
    ram_lock
fi

case "$action" in
    start) ram_start ;;
    save) ram_save ;;
    stop) ram_stop ;;
    reset) ram_reset ;;
    purge) ram_purge ;;
    status)
        ram_require
        df -h "$RAM_DIR"
        zramctl "$RAM_DEVICE"
        ;;
esac
