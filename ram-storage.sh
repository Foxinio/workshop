#!/usr/bin/env bash
# Shared by ram.sh and the container launchers. No commands run on sourcing.

ram_paths() {
    RAM_DIR="${WORKSHOP_ROOT}/.workshop-ram"
    RAM_STATE="${WORKSHOP_ROOT}/.workshop-ram-state"
    RAM_LOCK="${WORKSHOP_ROOT}/.workshop-ram-lock"
}

ram_error() { echo "RAM home: $*" >&2; return 1; }

ram_lock() {
    [[ ! -L "$RAM_LOCK" ]] || { ram_error "lock must not be a symlink"; return 1; }
    exec {RAM_LOCK_FD}>"$RAM_LOCK"
    flock -n "$RAM_LOCK_FD" || { ram_error "another session or storage command is active"; return 1; }
}

ram_read_state() {
    [[ -f "$RAM_STATE" && ! -L "$RAM_STATE" ]] || { ram_error "missing or invalid state; run workshop ram start if absent"; return 1; }
    mapfile -t RAM_RECORD < "$RAM_STATE"
    [[ ${#RAM_RECORD[@]} == 6 && ${RAM_RECORD[0]} == "$(cat /proc/sys/kernel/random/boot_id)" &&
       ${RAM_RECORD[1]} =~ ^/dev/zram[0-9]+$ && ${RAM_RECORD[3]} == "$BRIDGE_DIR" &&
       ${RAM_RECORD[4]} == "$RAM_DIR" ]] || { ram_error "stale or invalid state; see README.md recovery instructions"; return 1; }
    RAM_DEVICE="${RAM_RECORD[1]}"
    RAM_UUID="${RAM_RECORD[2]}"
}

ram_require() {
    ram_read_state || return 1
    [[ ${RAM_RECORD[5]} == ready && ! -L "$RAM_DIR" ]] || { ram_error "initialization is incomplete"; return 1; }
    [[ $(findmnt -rn -M "$RAM_DIR" -o SOURCE) == "$RAM_DEVICE" &&
       $(findmnt -rn -M "$RAM_DIR" -o FSTYPE) == ext4 &&
       $(sudo blkid -s UUID -o value "$RAM_DEVICE") == "$RAM_UUID" ]] || {
        ram_error "expected zram filesystem is not mounted; refusing disk fallback"; return 1;
    }
}

ram_no_writers() {
    local active mounts id
    # Also catch containers started outside the launchers (e.g. recovery).
    active=$(docker ps -q) || return 1
    for id in $active; do
        mounts=$(docker inspect --format '{{range .Mounts}}{{println .Source}}{{end}}' "$id") || return 1
        if printf '%s\n' "$mounts" | grep -Fxq -- "$RAM_DIR"; then
            ram_error "container $id is still using $RAM_DIR"; return 1
        fi
    done
}

ram_save() {
    ram_require || return 1
    ram_no_writers || return 1
    echo "Saving RAM home to $BRIDGE_DIR (host editors must stop writing)."
    # ponytail: file-level checkpoint, not atomic; retain RAM on failure for retry.
    sudo rsync -aHAX --numeric-ids --one-file-system --delete-delay --delay-updates \
        --exclude=/lost+found/ -- "$RAM_DIR/" "$BRIDGE_DIR/" || return 1
    sudo sync -f "$BRIDGE_DIR" || return 1
    echo "Home saved."
}

ram_start() {
    local id uuid boot
    for tool in sudo modprobe zramctl mkfs.ext4 mount umount findmnt rsync flock blkid mountpoint docker; do
        command -v "$tool" >/dev/null || { ram_error "missing host tool: $tool"; return 1; }
    done
    boot=$(cat /proc/sys/kernel/random/boot_id)
    if [[ -e "$RAM_STATE" || -L "$RAM_STATE" ]]; then
        # Never overwrite stale or incomplete records, including after a reboot.
        ram_require || return 1
        echo "RAM home already ready at $RAM_DIR"
        return
    fi
    [[ ! -L "$RAM_DIR" && ! -L "$RAM_STATE" ]] || { ram_error "RAM path/state must not be symlinks"; return 1; }
    if mountpoint -q "$RAM_DIR"; then
        ram_error "mount already exists without matching state: $RAM_DIR"; return 1
    fi
    ram_no_writers
    mkdir -p -- "$BRIDGE_DIR" "$RAM_DIR"
    [[ -z $(find "$RAM_DIR" -mindepth 1 -maxdepth 1 -print -quit) ]] || {
        ram_error "refusing to mount over nonempty $RAM_DIR"; return 1;
    }
    sudo -v || { ram_error "sudo privileges are required"; return 1; }
    sudo modprobe zram || { ram_error "zram kernel support is unavailable"; return 1; }
    id=$(sudo cat /sys/class/zram-control/hot_add) || {
        ram_error "cannot allocate zram device; check kernel support and privileges"; return 1;
    }
    [[ $id =~ ^[0-9]+$ ]] || { ram_error "invalid allocated device ID"; return 1; }
    RAM_DEVICE="/dev/zram$id"
    # Only this newly allocated device may be formatted or removed on failure.
    trap 'echo "Initialization failed; releasing newly allocated $RAM_DEVICE" >&2;
          if ! mountpoint -q "$RAM_DIR" || sudo umount "$RAM_DIR"; then
              sudo zramctl --reset "$RAM_DEVICE"
              rm -f -- "$RAM_STATE"
          fi' EXIT
    sudo zramctl --algorithm "${ZRAM_ALGORITHM:-zstd}" --size "${ZRAM_SIZE:-8G}" "$RAM_DEVICE"
    sudo mkfs.ext4 -q -m 0 "$RAM_DEVICE"
    uuid=$(sudo blkid -s UUID -o value "$RAM_DEVICE")
    printf '%s\n' "$boot" "$RAM_DEVICE" "$uuid" "$BRIDGE_DIR" "$RAM_DIR" loading > "$RAM_STATE"
    sudo mount -o noatime,nodev,nosuid,discard "$RAM_DEVICE" "$RAM_DIR"
    sudo rsync -aHAX --numeric-ids --one-file-system --exclude=/lost+found/ -- "$BRIDGE_DIR/" "$RAM_DIR/"
    sudo chown "$HOST_UID:$HOST_GID" "$RAM_DIR"
    printf '%s\n' "$boot" "$RAM_DEVICE" "$uuid" "$BRIDGE_DIR" "$RAM_DIR" ready > "$RAM_STATE"
    trap - EXIT
    echo "RAM home ready: $RAM_DIR. Use workshop codex or workshop maintain."
}

ram_stop() {
    ram_save || return 1
    sudo umount "$RAM_DIR" || return 1
    sudo zramctl --reset "$RAM_DEVICE" || return 1
    rm -- "$RAM_STATE"
    echo "RAM device released; disk checkpoint retained."
}

session_finish() {
    local result=$? exists
    trap - EXIT
    trap '' INT TERM HUP
    # A lost Docker connection is not evidence that the container stopped.
    if ! exists=$(docker ps -aq --filter "name=^/${SESSION_CONTAINER}$"); then
        ram_error "cannot check container; RAM retained, save manually after stopping it"
        exit 1
    fi
    if [[ -n "$exists" ]]; then
        if ! docker stop "$SESSION_CONTAINER" >/dev/null; then
            ram_error "cannot stop container; RAM retained without checkpointing"
            exit 1
        fi
    fi
    if ! ram_save; then
        ram_error "save failed; RAM retained. Retry with workshop ram save"
        exit 1
    fi
    if [[ -n "$exists" && "$SESSION_REMOVE" == true ]]; then
        docker rm "$SESSION_CONTAINER" >/dev/null || exit 1
    fi
    exit "$result"
}

session_traps() {
    trap session_finish EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    trap 'exit 129' HUP
}
