#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=container-env.sh
source "$(dirname -- "${BASH_SOURCE[0]}")/container-env.sh"

if [[ $# -ne 0 ]]; then
    usage >&2
    exit 2
fi

container="${WORKSHOP_ID}-maintenance"
image=$CODEX_MAINTAINED_IMAGE
prepare_container
if docker container inspect "$container" >/dev/null 2>&1; then
    echo "Maintenance container already exists: $container" >&2
    echo "Resume with: docker start -ai $container" >&2
    echo "See README.md for saving a recovered session." >&2
    exit 1
fi

# Failed/interrupted maintenance containers remain recoverable.
SESSION_CONTAINER="$container"
SESSION_REMOVE=false
session_traps
echo "Use sudo for repairs; exit 0 saves the system image. Home is saved on exit."
# shellcheck disable=SC2016 # This script runs inside the maintenance container.
if ! ram_run docker compose --env-file /dev/null --file "${SCRIPT_DIR}/compose.yaml" run \
    --name "$container" --pull never maintain -c '
        set -euo pipefail
        uid=$1
        gid=$2
        if ! command -v sudo >/dev/null; then
            apt-get update
            apt-get install -y --no-install-recommends sudo
        fi
        getent group "$gid" >/dev/null || groupadd --gid "$gid" "codex-$gid"
        getent passwd "$uid" >/dev/null || useradd --uid "$uid" --gid "$gid" \
            --home-dir /home/codex --shell /bin/bash --no-create-home "codex-$uid"
        printf "#%s ALL=(ALL) NOPASSWD: ALL\n" "$uid" > /etc/sudoers.d/codex-maintenance
        chmod 0440 /etc/sudoers.d/codex-maintenance
        visudo --check --file /etc/sudoers.d/codex-maintenance
        exec sudo --preserve-env --user "#$uid" --group "#$gid" \
            env "HOME=$HOME" "PATH=$PATH" /bin/bash
    ' maintain "$HOST_UID" "$HOST_GID"; then
    echo "Maintenance did not finish successfully; $container was retained." >&2
    exit 1
fi

ram_run docker commit --change 'ENTRYPOINT ["codex"]' --change 'CMD []' \
    --change "USER $HOST_UID:$HOST_GID" "$container" "$image"
SESSION_REMOVE=true
echo "Saved $image. Future Workshop sessions will use it."
