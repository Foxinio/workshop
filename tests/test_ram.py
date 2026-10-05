"""Run with python3 -B tests/test_ram.py; real rsync, mocked mounts/sudo/Docker."""
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time


INSTALL = Path(__file__).resolve().parents[1] / "runtime"
SETUP = r'''
set -euo pipefail
source "$INSTALL/ram-storage.sh"
ram_paths
BRIDGE_DIR="$WORKSHOP_ROOT/docker-bridge"
HOST_UID=$(id -u)
HOST_GID=$(id -g)
findmnt() {
    if [[ $2 == -o ]]; then
        echo /
        [[ ! -f "$WORKSHOP_ROOT/ram-mounted" ]] || echo "$RAM_DIR"
        [[ ! -f "$WORKSHOP_ROOT/bridge-mounted" ]] || echo "$BRIDGE_DIR"
        [[ ! -f "$WORKSHOP_ROOT/nested-mounted" ]] || echo "$RAM_DIR/nested"
        return 0
    fi
    if [[ $2 == -S ]]; then
        [[ -f "$WORKSHOP_ROOT/device-elsewhere" ]] || return 1
        echo /another-environment
        return 0
    fi
    if [[ $3 == "$BRIDGE_DIR" ]]; then
        [[ -f "$WORKSHOP_ROOT/bridge-mounted" ]] || return 1
        case $5 in
            ID) echo "${TEST_MOUNT_ID:-42}" ;;
            VFS-OPTIONS) cat "$WORKSHOP_ROOT/bridge-mode" ;;
        esac
    else
        [[ -f "$WORKSHOP_ROOT/ram-mounted" ]] || return 1
        case $5 in SOURCE) echo /dev/zram0 ;; FSTYPE) echo ext4 ;; esac
    fi
}
mountpoint() {
    if [[ $2 == "$BRIDGE_DIR" ]]; then
        [[ -f "$WORKSHOP_ROOT/bridge-mounted" ]]
    else
        [[ -f "$WORKSHOP_ROOT/ram-mounted" ]]
    fi
}
block() {
    exec python3 -c 'import os,pathlib,time; pathlib.Path(os.environ["WORKSHOP_ROOT"], "waiting").touch(); time.sleep(60)'
}
sudo() {
    printf '%s\n' "$*" >> "$WORKSHOP_ROOT/trace"
    local action=$1
    shift
    case $action in
        mount)
            case $* in
                --bind*) touch "$WORKSHOP_ROOT/bridge-mounted"; echo rw > "$WORKSHOP_ROOT/bridge-mode" ;;
                *remount,bind,ro*) echo ro > "$WORKSHOP_ROOT/bridge-mode" ;;
                *remount,bind,rw*) echo rw > "$WORKSHOP_ROOT/bridge-mode" ;;
                *) touch "$WORKSHOP_ROOT/ram-mounted" ;;
            esac ;;
        umount)
            if [[ $1 == "${TEST_BUSY:-}" ]]; then
                echo "umount: $1: target is busy" >&2
                return 32
            fi
            if [[ $1 == "$BRIDGE_DIR" ]]; then
                rm "$WORKSHOP_ROOT/bridge-mounted"
            else
                rm "$WORKSHOP_ROOT/ram-mounted"
                # Unmounting exposes the empty directory beneath the RAM filesystem.
                command find "$RAM_DIR" -mindepth 1 -delete
            fi ;;
        cat) echo 0 ;;
        blkid) echo "${TEST_UUID:-uuid}" ;;
        rsync)
            [[ ${TEST_BLOCK:-} != rsync ]] || block
            [[ ${TEST_FAIL_COPY:-false} != true ]] || return 23
            command rsync "$@" ;;
        sync) return "${TEST_SYNC_RESULT:-0}" ;;
        lsof) echo "COMMAND PID USER FD TYPE NAME: bash 1234 developer cwd DIR work"; return 1 ;;
        chown) command chown "$@" ;;
        find) command find "$@" ;;
        -v|modprobe|zramctl|mkfs.ext4) : ;;
        *) echo "Unexpected sudo: $action $*" >&2; return 1 ;;
    esac
}
docker() {
    printf 'docker %s\n' "$*" >> "$WORKSHOP_ROOT/trace"
    case $1 in
        ps) if [[ -f "$WORKSHOP_ROOT/running" ]]; then echo container; fi ;;
        inspect) echo "$RAM_DIR" ;;
        compose) touch "$WORKSHOP_ROOT/running"; block ;;
        stop) rm "$WORKSHOP_ROOT/running" ;;
        rm) : ;;
        *) return 1 ;;
    esac
}
lsof() { :; }
ram_traps
'''


def fixture(root, ready=True, legacy=False):
    bridge = root / "docker-bridge"
    ram = root / ".workshop-ram"
    (root / ".workshop").mkdir()
    bridge.mkdir()
    (bridge / "original").write_text("disk checkpoint")
    (bridge / "original").chmod(0o640)
    if ready:
        ram.mkdir()
        (ram / "new").write_text("RAM changes")
        (root / "ram-mounted").touch()
        record = [Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                  "/dev/zram0", "uuid", str(bridge), str(ram), "ready"]
        if not legacy:
            record.append("42")
            (root / "bridge-mounted").touch()
            (root / "bridge-mode").write_text("ro\n")
        (root / ".workshop/ram-state").write_text("\n".join(record) + "\n")
    return {**os.environ, "INSTALL": str(INSTALL), "WORKSHOP_ROOT": str(root)}


def run(script, env, **kwargs):
    return subprocess.run(["bash", "-c", SETUP + script], env=env,
                          capture_output=True, text=True, timeout=10, **kwargs)


def main():
    for existing_directory in (False, True):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = fixture(root, ready=False)
            if existing_directory:
                (root / ".workshop-ram").mkdir()
            result = run('ram_start; ram_require; ram_start', env)
            assert result.returncode == 0, result.stderr
            assert (root / ".workshop-ram/original").read_text() == "disk checkpoint"
            assert (root / "docker-bridge/original").stat().st_mode & 0o777 == 0o640
            assert (root / "bridge-mode").read_text().strip() == "ro"
            assert len((root / ".workshop/ram-state").read_text().splitlines()) == 7
            assert "[5/5]" in result.stdout and "100%" in result.stdout
            assert "already ready" in result.stdout
            assert "--info=progress2,stats1" in (root / "trace").read_text()

    for legacy in (False, True):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = run('ram_save', fixture(root, legacy=legacy))
            assert result.returncode == 0, result.stderr
            assert (root / "docker-bridge/new").read_text() == "RAM changes"
            assert not (root / "docker-bridge/original").exists()
            assert (root / "bridge-mode").read_text().strip() == "ro"

    # The compatibility runtime still reads and writes the legacy state paths.
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        env = fixture(root)
        (root / ".workshop/ram-state").rename(root / ".workshop-ram-state")
        (root / ".workshop").rmdir()
        (root / ".workshop").write_text("# legacy marker\n")
        result = run('ram_save', env)
        assert result.returncode == 0, result.stderr
        assert (root / ".workshop-ram-state").is_file()
        assert (root / "docker-bridge/new").read_text() == "RAM changes"

    for failure in ({"TEST_FAIL_COPY": "true"}, {"TEST_SYNC_RESULT": "1"}):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = run('ram_reset', {**fixture(root), **failure})
            assert result.returncode != 0
            assert (root / "ram-mounted").exists()
            assert (root / ".workshop/ram-state").exists()
            assert (root / "bridge-mode").read_text().strip() == "ro"
            assert "umount" not in (root / "trace").read_text()

    for action in ("stop", "reset", "purge"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = run('ram_' + action, fixture(root))
            assert result.returncode == 0, result.stderr
            assert not (root / "ram-mounted").exists()
            assert not (root / "bridge-mounted").exists()
            assert not (root / ".workshop/ram-state").exists()
            assert not (root / ".workshop-ram").exists()
            trace = (root / "trace").read_text()
            assert ("rsync " in trace) == (action != "purge")
            if action == "purge":
                assert (root / "docker-bridge/original").read_text() == "disk checkpoint"

    for target in (".workshop-ram", "docker-bridge", None):
        for why in (False, True):
            with tempfile.TemporaryDirectory(prefix="workshop ram ") as directory:
                root = Path(directory)
                env = fixture(root)
                if target:
                    env["TEST_BUSY"] = str(root / target)
                result = run('ram_stop' + (' --why' if why else ''), env)
                assert (result.returncode != 0) == bool(target), result.stderr
                trace = (root / "trace").read_text()
                assert ("lsof +D " in trace) == bool(target and why)
                assert ("bash 1234 developer cwd DIR work" in result.stderr) == bool(target and why)
                assert (root / ".workshop/ram-state").exists() == bool(target)
                if target:
                    assert "target is busy" in result.stderr
                    assert "zramctl --reset" not in trace
                    assert (root / "bridge-mode").read_text().strip() == "ro"
                    if why:
                        assert "lsof +D " + str(root / target) in trace

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        result = run('''
unset -f lsof
sudo() { if [[ $1 == umount ]]; then return 32; fi; "$@"; }
exec 3< "$RAM_DIR/new"
ram_unmount "$RAM_DIR" --why
''', fixture(root))
        assert result.returncode == 32, result.stderr
        assert "COMMAND" in result.stderr, result.stderr
        assert str(root / ".workshop-ram/new") in result.stderr, result.stderr

    for action in ("save", "reset", "purge"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = fixture(root)
            (root / "running").touch()
            result = run('ram_' + action, env)
            assert result.returncode != 0 and "still using" in result.stderr
            assert (root / "ram-mounted").exists()
            assert "umount" not in (root / "trace").read_text()

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        result = run('ram_reset', {**fixture(root), "TEST_MOUNT_ID": "99"})
        assert result.returncode != 0 and "protection changed" in result.stderr
        assert "umount" not in (root / "trace").read_text()

    for scenario in ("old-boot", "malformed", "missing", "loading", "writable", "released"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = fixture(root)
            state = root / ".workshop/ram-state"
            record = state.read_text().splitlines()
            if scenario in {"old-boot", "malformed", "missing", "released"}:
                (root / "ram-mounted").unlink()
            if scenario in {"old-boot", "malformed", "missing"}:
                (root / "bridge-mounted").unlink()
            if scenario == "old-boot":
                record[0] = "previous-boot"
                state.write_text("\n".join(record) + "\n")
            elif scenario == "malformed":
                state.write_text("broken\n")
            elif scenario == "missing":
                state.unlink()
            elif scenario == "loading":
                record[5] = "loading"
                state.write_text("\n".join(record) + "\n")
            elif scenario == "writable":
                (root / "bridge-mode").write_text("rw\n")
            result = run('ram_purge', env)
            assert result.returncode == 0, (scenario, result.stderr)
            assert not state.exists()
            assert not (root / ".workshop-ram").exists()
            trace = (root / "trace").read_text()
            assert "rsync " not in trace
            if scenario in {"old-boot", "malformed", "missing"}:
                assert "zramctl --reset" not in trace
            assert (root / "docker-bridge/original").read_text() == "disk checkpoint"
            result = run('ram_start; ram_require', env)
            assert result.returncode == 0, (scenario, result.stderr)

    for scenario in ("unknown-live", "nested", "elsewhere", "wrong-device"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = fixture(root)
            if scenario == "unknown-live":
                (root / ".workshop/ram-state").write_text("broken\n")
            elif scenario == "nested":
                (root / "nested-mounted").touch()
            elif scenario == "elsewhere":
                (root / "ram-mounted").unlink()
                (root / "device-elsewhere").touch()
            else:
                env["TEST_UUID"] = "another-device"
            result = run('ram_purge', env)
            assert result.returncode != 0, scenario
            trace = (root / "trace").read_text()
            assert "umount" not in trace and "zramctl --reset" not in trace
            assert (root / ".workshop-ram/new").read_text() == "RAM changes"

    for sig in (signal.SIGINT, signal.SIGTERM):
        for action in ("start", "save", "session"):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                env = fixture(root, ready=action != "start")
                env["TEST_BLOCK"] = "rsync" if action != "session" else "compose"
                script = 'ram_' + action
                if action == "session":
                    script = 'SESSION_CONTAINER=test; SESSION_REMOVE=true; session_traps; ram_run docker compose'
                process = subprocess.Popen(["bash", "-c", SETUP + script], env=env,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           text=True, start_new_session=True)
                try:
                    deadline = time.monotonic() + 5
                    while not (root / "waiting").exists() and process.poll() is None:
                        assert time.monotonic() < deadline, "worker did not start"
                        time.sleep(0.01)
                    # SIGINT tests terminal-style delivery; SIGTERM targets Workshop alone.
                    if sig == signal.SIGINT:
                        os.killpg(process.pid, sig)
                    else:
                        process.send_signal(sig)
                    stdout, stderr = process.communicate(timeout=5)
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                assert process.returncode == 128 + sig, (action, sig, stdout, stderr)
                assert "finishing safely" in stderr
                if action == "start":
                    assert not (root / ".workshop/ram-state").exists()
                    assert not (root / "ram-mounted").exists()
                    assert not (root / "bridge-mounted").exists()
                else:
                    assert (root / "ram-mounted").exists()
                    assert (root / "bridge-mode").read_text().strip() == "ro"
                if action == "session":
                    trace = (root / "trace").read_text()
                    assert trace.index("docker stop") < trace.index("rsync ")
                    assert (root / "docker-bridge/new").read_text() == "RAM changes"
                    assert not (root / "running").exists()
                else:
                    assert (root / "docker-bridge/original").read_text() == "disk checkpoint"

    result = run('ram_run cat', {**os.environ, "INSTALL": str(INSTALL),
                                "WORKSHOP_ROOT": "/unused"}, input="attached\n")
    assert result.returncode == 0 and result.stdout == "attached\n"
    print("RAM protection, reset/save failures, purge/restart, ownership, signals and stdin checks passed.")


if __name__ == "__main__":
    main()
