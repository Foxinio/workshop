#!/usr/bin/env bash
# Shared by ram.sh and the container launchers. No commands run on sourcing.

ram_paths() {
    RAM_DIR="${WORKSHOP_ROOT}/.workshop-ram"
    if [[ -d "$WORKSHOP_ROOT/.workshop" && ! -L "$WORKSHOP_ROOT/.workshop" ]]; then
        RAM_STATE="$WORKSHOP_ROOT/.workshop/ram-state"
        RAM_LOCK="$WORKSHOP_ROOT/.workshop/ram.lock"
    else
        RAM_STATE="$WORKSHOP_ROOT/.workshop-ram-state"
        RAM_LOCK="$WORKSHOP_ROOT/.workshop-ram-lock"
    fi
}

ram_error() { echo "RAM home: $*" >&2; return 1; }

ram_interrupt() {
    local result=$1 signal=$2
    trap '' INT TERM HUP
    echo "Received $signal; finishing safely..." >&2
    if [[ -n ${RAM_CHILD_PID:-} ]]; then
        kill -TERM "$RAM_CHILD_PID" 2>/dev/null || true
        wait "$RAM_CHILD_PID" 2>/dev/null || true
        RAM_CHILD_PID=
    fi
    exit "$result"
}

# Waiting for a background command lets Bash handle signals immediately.
# Explicit stdin keeps interactive Docker sessions attached to the terminal.
ram_run() {
    local result=0
    "$@" <&0 &
    RAM_CHILD_PID=$!
    wait "$RAM_CHILD_PID" || result=$?
    RAM_CHILD_PID=
    return "$result"
}

ram_finish() {
    local result=$?
    trap - EXIT
    trap '' INT TERM HUP
    ram_restore_checkpoint || result=1
    if [[ ${RAM_CHECKPOINT_PENDING:-false} == true ]] && mountpoint -q "$BRIDGE_DIR"; then
        sudo umount "$BRIDGE_DIR" || result=1
    fi
    exit "$result"
}

ram_traps() {
    trap ram_finish EXIT
    trap 'ram_interrupt 130 SIGINT' INT
    trap 'ram_interrupt 143 SIGTERM' TERM
    trap 'ram_interrupt 129 SIGHUP' HUP
}

ram_lock() {
    [[ ! -L "$RAM_LOCK" ]] || { ram_error "lock must not be a symlink"; return 1; }
    exec {RAM_LOCK_FD}>"$RAM_LOCK"
    flock -n "$RAM_LOCK_FD" || { ram_error "another session or storage command is active"; return 1; }
    if [[ ${WORKSHOP_LAYOUT:-} == legacy && -d "$WORKSHOP_ROOT/.workshop" ]]; then
        ram_error "project layout changed; retry the Workshop command"; return 1
    fi
    if [[ -e "$WORKSHOP_ROOT/.workshop" ]]; then
        python3 "$SCRIPT_DIR/../lib/workshop_config.py" --check-update "$WORKSHOP_ROOT" || return 1
    fi
    ram_paths
}

ram_read_state() {
    [[ -f "$RAM_STATE" && ! -L "$RAM_STATE" ]] || { ram_error "missing or invalid state; run workshop ram start if absent"; return 1; }
    mapfile -t RAM_RECORD < "$RAM_STATE"
    [[ ( ${#RAM_RECORD[@]} == 6 || ${#RAM_RECORD[@]} == 7 ) && ${RAM_RECORD[0]} == "$(cat /proc/sys/kernel/random/boot_id)" &&
       ${RAM_RECORD[1]} =~ ^/dev/zram[0-9]+$ && ${RAM_RECORD[3]} == "$BRIDGE_DIR" &&
       ${RAM_RECORD[4]} == "$RAM_DIR" ]] || { ram_error "stale or invalid state; see README.md recovery instructions"; return 1; }
    RAM_DEVICE="${RAM_RECORD[1]}"
    RAM_UUID="${RAM_RECORD[2]}"
    RAM_CHECKPOINT_ID="${RAM_RECORD[6]:-}"
    [[ -z "$RAM_CHECKPOINT_ID" || $RAM_CHECKPOINT_ID =~ ^[0-9]+$ ]] || {
        ram_error "invalid checkpoint mount identity"; return 1;
    }
}

ram_checkpoint_require() {
    [[ $(findmnt -rn -M "$BRIDGE_DIR" -o ID) == "$RAM_CHECKPOINT_ID" &&
       ,$(findmnt -rn -M "$BRIDGE_DIR" -o VFS-OPTIONS), == *,ro,* ]] || {
        ram_error "checkpoint protection changed; refusing to continue"; return 1;
    }
}

ram_protect_checkpoint() {
    if [[ -n "$RAM_CHECKPOINT_ID" ]]; then
        ram_checkpoint_require || return 1
        return 0
    fi
    if mountpoint -q "$BRIDGE_DIR"; then
        ram_error "checkpoint already has a mount; refusing to replace it"; return 1
    fi
    RAM_CHECKPOINT_PENDING=true
    sudo mount --bind "$BRIDGE_DIR" "$BRIDGE_DIR" || return 1
    RAM_CHECKPOINT_CREATED=true
    if ! sudo mount -o remount,bind,ro "$BRIDGE_DIR"; then
        sudo umount "$BRIDGE_DIR" || return 1
        RAM_CHECKPOINT_CREATED=false
        return 1
    fi
    RAM_CHECKPOINT_ID=$(findmnt -rn -M "$BRIDGE_DIR" -o ID)
    if [[ ! $RAM_CHECKPOINT_ID =~ ^[0-9]+$ ]] || ! printf '%s\n' "$RAM_CHECKPOINT_ID" >> "$RAM_STATE"; then
        sudo umount "$BRIDGE_DIR" || return 1
        RAM_CHECKPOINT_CREATED=false
        ram_error "could not record checkpoint protection"; return 1
    fi
    RAM_CHECKPOINT_PENDING=false
    echo "Disk checkpoint is read-only: $BRIDGE_DIR. Edit $RAM_DIR/work instead."
}

ram_restore_checkpoint() {
    if [[ ${RAM_CHECKPOINT_WRITABLE:-false} == true ]]; then
        sudo mount -o remount,bind,ro "$BRIDGE_DIR" || {
            ram_error "could not restore read-only checkpoint; stop host writers and retry ram save"; return 1;
        }
        RAM_CHECKPOINT_WRITABLE=false
    fi
}

ram_require() {
    ram_read_state || return 1
    [[ ${RAM_RECORD[5]} == ready && ! -L "$RAM_DIR" ]] || { ram_error "initialization is incomplete"; return 1; }
    ram_device_require || return 1
    if [[ -n "$RAM_CHECKPOINT_ID" ]]; then ram_checkpoint_require; fi
}

ram_device_require() {
    [[ -n "$RAM_UUID" && $(findmnt -rn -M "$RAM_DIR" -o SOURCE) == "$RAM_DEVICE" &&
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
    ram_protect_checkpoint || return 1
    echo "Saving RAM home to $BRIDGE_DIR (host editors must stop writing)."
    # Set before remounting so exit cleanup covers an interrupted remount.
    RAM_CHECKPOINT_WRITABLE=true
    if ! sudo mount -o remount,bind,rw "$BRIDGE_DIR"; then
        ram_restore_checkpoint || return 1
        return 1
    fi
    local result=0
    # ponytail: file-level checkpoint, not atomic; retain RAM on failure for retry.
    ram_run sudo rsync -aHAX --numeric-ids --one-file-system --delete-delay --delay-updates \
        --info=progress2,stats1 --human-readable --no-inc-recursive \
        --exclude=/lost+found/ -- "$RAM_DIR/" "$BRIDGE_DIR/" || result=$?
    if [[ $result == 0 ]]; then
        echo "Flushing the checkpoint to disk..."
        ram_run sudo sync -f "$BRIDGE_DIR" || result=$?
    fi
    ram_restore_checkpoint || return 1
    [[ $result == 0 ]] || return "$result"
    echo "Home saved; disk checkpoint is read-only again."
}

ram_start() {
    local id uuid boot tool
    echo "[1/5] Checking RAM storage and required host tools..."
    for tool in sudo modprobe zramctl mkfs.ext4 mount umount findmnt rsync flock blkid mountpoint docker; do
        command -v "$tool" >/dev/null || { ram_error "missing host tool: $tool"; return 1; }
    done
    boot=$(cat /proc/sys/kernel/random/boot_id)
    if [[ -e "$RAM_STATE" || -L "$RAM_STATE" ]]; then
        # Never overwrite stale or incomplete records, including after a reboot.
        ram_require || return 1
        ram_protect_checkpoint || return 1
        echo "RAM home already ready at $RAM_DIR; no copy needed."
        echo "Disk checkpoint is read-only: $BRIDGE_DIR. Edit $RAM_DIR/work instead."
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
    echo "[2/5] Requesting sudo and allocating ${ZRAM_SIZE:-8G} of compressed RAM (${ZRAM_ALGORITHM:-zstd})..."
    ram_run sudo -v || { ram_error "sudo privileges are required"; return 1; }
    ram_run sudo modprobe zram || { ram_error "zram kernel support is unavailable"; return 1; }
    id=$(sudo cat /sys/class/zram-control/hot_add) || {
        ram_error "cannot allocate zram device; check kernel support and privileges"; return 1;
    }
    [[ $id =~ ^[0-9]+$ ]] || { ram_error "invalid allocated device ID"; return 1; }
    RAM_DEVICE="/dev/zram$id"
    RAM_CHECKPOINT_CREATED=false
    # Only this newly allocated device may be formatted or removed on failure.
    trap 'trap "" INT TERM HUP; echo "Initialization failed; releasing newly allocated $RAM_DEVICE" >&2;
          if ! mountpoint -q "$RAM_DIR" || sudo umount "$RAM_DIR"; then
              if [[ $RAM_CHECKPOINT_CREATED == true ]]; then sudo umount "$BRIDGE_DIR" || exit 1; fi
              if sudo zramctl --reset "$RAM_DEVICE"; then rm -f -- "$RAM_STATE"; fi
          fi' EXIT
    echo "[3/5] Formatting $RAM_DEVICE and protecting the disk checkpoint..."
    ram_run sudo zramctl --algorithm "${ZRAM_ALGORITHM:-zstd}" --size "${ZRAM_SIZE:-8G}" "$RAM_DEVICE"
    ram_run sudo mkfs.ext4 -q -m 0 "$RAM_DEVICE"
    uuid=$(sudo blkid -s UUID -o value "$RAM_DEVICE")
    printf '%s\n' "$boot" "$RAM_DEVICE" "$uuid" "$BRIDGE_DIR" "$RAM_DIR" loading > "$RAM_STATE"
    ram_read_state
    ram_protect_checkpoint
    sudo mount -o noatime,nodev,nosuid,discard "$RAM_DEVICE" "$RAM_DIR"
    echo "[4/5] Loading $BRIDGE_DIR into $RAM_DIR (scanning, then copying)..."
    ram_run sudo rsync -aHAX --numeric-ids --one-file-system \
        --info=progress2,stats1 --human-readable --no-inc-recursive \
        --exclude=/lost+found/ -- "$BRIDGE_DIR/" "$RAM_DIR/"
    sudo chown "$HOST_UID:$HOST_GID" "$RAM_DIR"
    printf '%s\n' "$boot" "$RAM_DEVICE" "$uuid" "$BRIDGE_DIR" "$RAM_DIR" ready "$RAM_CHECKPOINT_ID" > "$RAM_STATE"
    trap ram_finish EXIT
    echo "[5/5] RAM home ready: $RAM_DIR. Use workshop codex, workshop claude, workshop opencode or workshop maintain."
}

ram_stop() {
    ram_save || return 1
    ram_release
}

ram_reset() {
    ram_stop || {
        ram_error "reset could not complete; retry after fixing the error, or use workshop ram purge to discard RAM"; return 1;
    }
}

ram_purge() {
    local targets target ram_mounted=false checkpoint_mounted=false device_targets
    [[ ! -L "$RAM_DIR" && ( ! -e "$RAM_DIR" || -d "$RAM_DIR" ) && ! -L "$RAM_STATE" &&
       ( ! -e "$RAM_STATE" || -f "$RAM_STATE" ) ]] || {
        ram_error "purge requires ordinary RAM and state paths"; return 1;
    }
    ram_no_writers || return 1
    targets=$(findmnt -rn -o TARGET) || return 1
    while IFS= read -r target; do
        # findmnt raw output hex-escapes spaces and other special characters.
        printf -v target '%b' "$target"
        case "$target" in
            "$RAM_DIR") ram_mounted=true ;;
            "$BRIDGE_DIR") checkpoint_mounted=true ;;
            "$RAM_DIR/"*) ram_error "unmount nested RAM filesystem first: $target"; return 1 ;;
        esac
    done <<< "$targets"

    RAM_DEVICE=
    RAM_CHECKPOINT_ID=
    if ram_read_state 2>/dev/null; then
        if [[ $ram_mounted == true ]]; then
            ram_device_require || return 1
        elif [[ -z "$RAM_UUID" || $(sudo blkid -s UUID -o value "$RAM_DEVICE") != "$RAM_UUID" ]]; then
            # The recorded device may have been reset or reassigned. Leave it alone.
            RAM_DEVICE=
        else
            device_targets=$(findmnt -rn -S "$RAM_DEVICE" -o TARGET) || {
                [[ $? == 1 ]] || return 1
                device_targets=
            }
            [[ -z "$device_targets" ]] || {
                ram_error "recorded device is mounted elsewhere; refusing purge"; return 1;
            }
        fi
    else
        # Stale/malformed records cannot establish ownership of a live device.
        RAM_DEVICE=
        RAM_CHECKPOINT_ID=
        if [[ $ram_mounted == true || $checkpoint_mounted == true ]]; then
            ram_error "cannot identify live mounts from this state; refusing to purge an unknown filesystem"; return 1
        fi
    fi
    if [[ $checkpoint_mounted == true ]]; then
        [[ -n "$RAM_CHECKPOINT_ID" && $(findmnt -rn -M "$BRIDGE_DIR" -o ID) == "$RAM_CHECKPOINT_ID" ]] || {
            ram_error "checkpoint protection changed; refusing purge"; return 1;
        }
    fi
    echo "Discarding unsaved RAM changes; keeping the disk checkpoint untouched."
    ram_release || return 1
    if [[ -d "$RAM_DIR" ]]; then
        sudo find "$RAM_DIR" -mindepth 1 -delete || return 1
    fi
}

ram_release() {
    # Finish the short release sequence after saving or explicitly discarding RAM.
    trap '' INT TERM HUP
    echo "Unmounting RAM and restoring writable disk access..."
    if mountpoint -q "$RAM_DIR"; then sudo umount "$RAM_DIR" || return 1; fi
    if [[ -n "$RAM_CHECKPOINT_ID" ]] && mountpoint -q "$BRIDGE_DIR"; then
        sudo umount "$BRIDGE_DIR" || return 1
    fi
    if [[ -n "$RAM_DEVICE" ]]; then sudo zramctl --reset "$RAM_DEVICE" || return 1; fi
    rm -f -- "$RAM_STATE"
    echo "RAM device released; disk checkpoint retained. You can run workshop ram start again."
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
        echo "Stopping $SESSION_CONTAINER before saving its home..."
        if ! ram_run docker stop "$SESSION_CONTAINER" >/dev/null; then
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
    ram_traps
    trap session_finish EXIT
}
