#!/usr/bin/env python3
"""Prepare account mappings and private-path masks outside the shared home."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


TOOLS = ("codex", "claude", "opencode")
PRIVATE_DIRS = {
    "codex": (".codex",),
    "claude": (".claude",),
    "opencode": (".config/opencode", ".local/share/opencode",
                 ".local/state/opencode", ".cache/opencode"),
}


def check_path(home, relative, directory):
    path = home / relative
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError(f"private path must not contain symlinks: {part}")
        if part == home:
            break
    if path.exists() and not (path.is_dir() if directory else path.is_file()):
        raise ValueError(f"unexpected private path type: {path}")
    return path


def private_mounts(home, active, empty, empty_dir):
    mounts = []
    for tool, directories in PRIVATE_DIRS.items():
        paths = [(relative, True) for relative in directories]
        if tool == "claude":
            paths.append((".claude.json", False))
            paths.extend((path.name, False) for path in sorted(home.glob(".claude.json.*")))
        for relative, directory in paths:
            path = check_path(home, relative, directory)
            if tool != active and path.exists():
                mounts.append(bind(empty_dir if directory else empty,
                                   "/home/codex/" + relative))
    return mounts


def bind(source, target):
    return {"type": "bind", "source": str(source), "target": target,
            "read_only": True, "bind": {"create_host_path": False}}


def accounts(passwd, group, uid, gid):
    if uid <= 0 or gid < 0:
        raise ValueError("Workshop sessions require a non-root host UID and a nonnegative GID")
    users = [line.split(":") for line in passwd.splitlines() if line]
    groups = [line.split(":") for line in group.splitlines() if line]
    if any(len(row) != 7 for row in users) or any(len(row) != 4 for row in groups):
        raise ValueError("image has invalid passwd or group entries")
    aliases = {row[0] for row in users if row[0] == "ubuntu" or int(row[2]) == uid}
    users = [row for row in users if row[0] not in aliases]
    users.append(["ubuntu", "x", str(uid), str(gid), "", "/home/codex", "/bin/bash"])
    if not any(int(row[2]) == gid for row in groups):
        name = "ubuntu" if not any(row[0] == "ubuntu" for row in groups) else f"ubuntu-{gid}"
        if any(row[0] == name for row in groups):
            raise ValueError(f"image group name conflicts with host GID: {name}")
        groups.append([name, "x", str(gid), ""])
    for row in groups:
        row[3] = ",".join(dict.fromkeys("ubuntu" if name in aliases else name
                                        for name in row[3].split(",") if name))
    return ("".join(":".join(row) + "\n" for row in rows) for rows in (users, groups))


def image_accounts(image):
    command = ["docker", "run", "--rm", "--pull", "never", "--network", "none", "--read-only",
               "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
               "--user", "0:0", "--entrypoint", "/bin/cat", image]
    return [subprocess.run([*command, path], check=True, capture_output=True, text=True).stdout
            for path in ("/etc/passwd", "/etc/group")]


def prepare(image, home, runtime, uid, gid, active):
    if active not in (*TOOLS, "maintain"):
        raise ValueError("unknown session tool")
    if home.is_symlink() or not home.is_dir():
        raise ValueError("session home must be an ordinary directory")
    # Runtime sources must never be reachable through the container's home mount.
    if home.resolve() == runtime.resolve() or home.resolve() in runtime.resolve().parents:
        raise ValueError("session runtime must be outside the shared home")
    for path in (runtime, *runtime.parents):
        if path.is_symlink():
            raise ValueError(f"runtime path must not contain symlinks: {path}")
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    empty = runtime / "empty-file"
    empty_dir = runtime / "empty-dir"
    for path in (empty, empty_dir, runtime / "passwd", runtime / "group", runtime / "compose.json"):
        if path.is_symlink():
            raise ValueError(f"runtime file must not be a symlink: {path}")
    empty_dir.mkdir(mode=0o555, exist_ok=True)
    if any(empty_dir.iterdir()):
        raise ValueError("runtime mask directory must be empty")
    mounts = [] if active == "maintain" else private_mounts(home, active, empty, empty_dir)
    passwd, group = accounts(*image_accounts(image), uid, gid)
    # Replace files rather than following links or truncating a possible hard link.
    for name, contents in (("empty-file", ""), ("passwd", passwd), ("group", group)):
        write_file(runtime / name, contents)
    account_target = "/run/workshop-" if active == "maintain" else "/etc/"
    mounts.extend(bind(runtime / name, account_target + name) for name in ("passwd", "group"))
    write_file(runtime / "compose.json", json.dumps({"services": {active: {"volumes": mounts}}}))


def write_file(path, contents):
    descriptor, filename = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    temporary = Path(filename)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(contents)
            os.fchmod(stream.fileno(), 0o644)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        image, home, runtime, uid, gid, active = sys.argv[1:]
        prepare(image, Path(home), Path(runtime), int(uid), int(gid), active)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f"Workshop session: {error}")
