"""Check session masks; add --docker --image IMAGE for real-container checks."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import workshop_session as session


PASSWD = "root:x:0:0:root:/root:/bin/bash\ndeveloper:x:1000:1000::/home/developer:/bin/bash\n"
GROUP = "root:x:0:\ndeveloper:x:1000:\nsudo:x:27:developer\n"
SECRETS = {
    "codex": (".codex/auth.json",),
    "claude": (".claude/.credentials.json", ".claude.json", ".claude.json.backup"),
    "opencode": (".config/opencode/config.json", ".local/share/opencode/auth.json",
                 ".local/state/opencode/state.json", ".cache/opencode/cache.json"),
}


def seed(home):
    for tool, paths in SECRETS.items():
        for relative in paths:
            path = home / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("dummy-" + tool)
    for relative in ("work/project.txt", ".local/bin/shared-tool", ".bashrc"):
        path = home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("shared")


def expect_error(callback, message):
    try:
        callback()
    except ValueError as error:
        assert message in str(error), str(error)
    else:
        raise AssertionError("unsafe session preparation succeeded")


def unit_checks(container_home, username="ubuntu"):
    mapped, _ = session.accounts(PASSWD, GROUP, 1000, 1000, container_home, username)
    assert f"{username}:x:1000:1000::{container_home}:/bin/bash\n" in mapped
    passwd, group = session.accounts(PASSWD, GROUP, 1000, 1000)
    assert passwd == "root:x:0:0:root:/root:/bin/bash\nubuntu:x:1000:1000::/home/codex:/bin/bash\n"
    assert "sudo:x:27:ubuntu\n" in group
    assert "developer:x:1000:" in group
    passwd, group = session.accounts(PASSWD, GROUP, 1234, 2345)
    assert "developer:x:1000:1000:" in passwd
    assert "ubuntu:x:1234:2345::/home/codex:/bin/bash\n" in passwd
    assert "ubuntu:x:2345:\n" in group
    passwd, _ = session.accounts(PASSWD + "ubuntu:x:1234:1234::/old:/bin/sh\n", GROUP, 1000, 1000)
    assert sum(line.startswith("ubuntu:") for line in passwd.splitlines()) == 1
    assert "developer:" not in passwd
    expect_error(lambda: session.accounts(PASSWD, GROUP, 0, 0), "non-root")
    expect_error(lambda: session.accounts("invalid", GROUP, 1000, 1000), "invalid")

    with tempfile.TemporaryDirectory(prefix="workshop session ") as directory:
        root = Path(directory).resolve()
        home = root / "home"
        seed(home)
        runtime = root / "runtime"
        for tool in (*session.TOOLS, "maintain"):
            with patch.object(session, "image_accounts", return_value=(PASSWD, GROUP)):
                session.prepare("test-image", home, runtime, 1000, 1000, tool, container_home, username)
            services = json.loads((runtime / "compose.json").read_text())["services"]
            assert list(services) == [tool]
            mounts = services[tool]["volumes"]
            targets = {mount["target"] for mount in mounts}
            private = {container_home + "/" + path for owner, paths in session.PRIVATE_DIRS.items()
                       if owner != tool for path in paths}
            if tool != "claude":
                private.update((container_home + "/.claude.json", container_home + "/.claude.json.backup"))
            account_prefix = "/run/workshop-" if tool == "maintain" else "/etc/"
            assert targets == ({account_prefix + "passwd", account_prefix + "group"}
                               | (private if tool != "maintain" else set()))
            assert all(mount["read_only"] and not mount["bind"]["create_host_path"] for mount in mounts)
            assert all(home not in Path(mount["source"]).parents for mount in mounts)
            assert (runtime / "empty-file").read_bytes() == b""
            assert list((runtime / "empty-dir").iterdir()) == []
            assert (runtime / "passwd").stat().st_mode & 0o777 == 0o644
            assert f"::{container_home}:/bin/bash" in (runtime / "passwd").read_text()
        # Missing agent files are left absent, including Claude's standalone JSON.
        absent = root / "absent"
        absent.mkdir()
        with patch.object(session, "image_accounts", return_value=(PASSWD, GROUP)):
            session.prepare("test-image", absent, runtime, 1000, 1000, "codex")
        assert list(absent.iterdir()) == []
        assert len(json.loads((runtime / "compose.json").read_text())["services"]["codex"]["volumes"]) == 2
        expect_error(lambda: session.prepare("test", home, home / "runtime", 1000, 1000, "codex"), "outside")
        for relative in (".codex", ".local", ".claude.json", ".claude.json.backup"):
            candidate = root / "symlink-home"
            candidate.mkdir()
            (candidate / relative).symlink_to(root / "missing")
            with patch.object(session, "image_accounts") as read_image:
                expect_error(lambda: session.prepare("test", candidate, runtime, 1000, 1000, "codex"), "symlink")
                read_image.assert_not_called()
            (candidate / relative).unlink()
            candidate.rmdir()
        # Maintenance must remain available to repair unsupported private paths.
        (absent / ".codex").symlink_to(root / "missing")
        with patch.object(session, "image_accounts", return_value=(PASSWD, GROUP)):
            session.prepare("test", absent, runtime, 1000, 1000, "maintain")
        linked_runtime = root / "runtime-link"
        linked_runtime.symlink_to(runtime, target_is_directory=True)
        expect_error(lambda: session.prepare("test", home, linked_runtime, 1000, 1000, "codex"), "symlink")
        (runtime / "empty-dir").chmod(0o755)
        (runtime / "empty-dir/leak").write_text("dummy-secret")
        expect_error(lambda: session.prepare("test", home, runtime, 1000, 1000, "codex"), "must be empty")
        (runtime / "empty-dir/leak").unlink()
        (runtime / "empty-file").unlink()
        sentinel = root / "sentinel"
        sentinel.write_text("must not truncate")
        (runtime / "empty-file").hardlink_to(sentinel)
        with patch.object(session, "image_accounts", return_value=(PASSWD, GROUP)):
            session.prepare("test", home, runtime, 1000, 1000, "codex")
        assert sentinel.read_text() == "must not truncate"
        with patch.object(session, "image_accounts", side_effect=subprocess.CalledProcessError(1, "docker")):
            try:
                session.prepare("missing-image", home, runtime, 1000, 1000, "codex")
            except subprocess.CalledProcessError:
                pass
            else:
                raise AssertionError("image inspection failure was ignored")
    print("Session mask and account mapping checks passed.")


def docker_checks(image, container_home, username="ubuntu"):
    if os.getuid() == 0:
        raise SystemExit("Run the Docker checks as a non-root host user.")
    with tempfile.TemporaryDirectory(prefix="workshop session ") as directory:
        root = Path(directory).resolve()
        home = root / "home"
        seed(home)
        env = {**os.environ, "WORKSHOP_ID": "workshop-isolation-" + uuid.uuid4().hex[:12],
               "WORKSHOP_RECIPE": str(Path(__file__).resolve().parents[1] / "recipes/generic"),
               "CODEX_IMAGE": image, "CODEX_RAM_DIR": str(home),
               "HOST_UID": str(os.getuid()), "HOST_GID": str(os.getgid()),
               "WORKSHOP_MEMORY_LIMIT": "1g", "WORKSHOP_HOME": container_home,
               "WORKSHOP_USER": username}
        base = Path(__file__).resolve().parents[1] / "runtime/compose.yaml"
        identity = r'''
set -euo pipefail
[[ $(id -un) == EXPECTED_USER && $(id -u) == "$1" && $(id -g) == "$2" ]]
[[ $HOME == EXPECTED_HOME && $USER == EXPECTED_USER && $LOGNAME == EXPECTED_USER ]]
IFS=: read -r name password uid gid comment account_home shell < <(getent passwd "$(id -u)")
[[ $account_home == "$HOME" ]]
[[ $(cat "$HOME/work/project.txt") == shared ]]
[[ $(cat "$HOME/.local/bin/shared-tool") == shared ]]
'''.replace("EXPECTED_HOME", shlex.quote(container_home)).replace("EXPECTED_USER", shlex.quote(username))
        for tool in (*session.TOOLS, "maintain"):
            runtime = root / ("runtime-" + tool)
            session.prepare(image, home, runtime, os.getuid(), os.getgid(), tool, container_home, username)
            command = ["docker", "compose", "--profile", "maintenance", "--env-file", "/dev/null", "--file", str(base),
                       "--file", str(runtime / "compose.json")]
            config = subprocess.run([*command, "config", "--format", "json"], env=env,
                                    check=True, capture_output=True, text=True)
            service = json.loads(config.stdout)["services"][tool]
            assert service.get("read_only", False) == (tool != "maintain")
            checks = identity
            before = {relative: (home / relative).read_bytes()
                      for owner, paths in SECRETS.items() if owner != tool for relative in paths}
            for owner, paths in SECRETS.items():
                for relative in paths:
                    path = shlex.quote(container_home + "/" + relative)
                    if owner == tool or tool == "maintain":
                        checks += f"[[ -s {path} ]]\nprintf updated-{tool} > {path}\n"
                    else:
                        checks += f"[[ ! -s {path} ]]\n! cat {path} 2>/dev/null | read -r leaked\n"
                        checks += f"! (printf forbidden >> {path}) 2>/dev/null\n"
            if tool != "maintain":
                mounts = json.loads((runtime / "compose.json").read_text())["services"][tool]["volumes"]
                for mount in mounts:
                    target = shlex.quote(mount["target"])
                    checks += f"! rm -rf {target} 2>/dev/null\n! mv {target} {target}-moved 2>/dev/null\n"
                    checks += f"! mount -o remount,rw {target} 2>/dev/null\n! umount {target} 2>/dev/null\n"
                    if mount["source"] == str(runtime / "empty-dir"):
                        checks += f"[[ -z $(ls -A {target}) ]]\n! touch {target}/probe 2>/dev/null\n"
                if tool != "opencode":
                    checks += r'''
mv "$HOME/.local/share" "$HOME/.local/share-renamed"
[[ ! -s "$HOME/.local/share-renamed/opencode/auth.json" ]]
! touch "$HOME/.local/share-renamed/opencode/probe" 2>/dev/null
mv "$HOME/.local/share-renamed" "$HOME/.local/share"
'''
                launch = checks
            else:
                # The maintenance bootstrap copies account mappings into writable
                # system files before dropping to the same interactive identity.
                launch = r'''
set -euo pipefail
install -m 0644 /run/workshop-passwd /etc/passwd
install -m 0644 /run/workshop-group /etc/group
groupadd --gid 27999 workshop-test
useradd --uid 27999 --gid 27999 --no-create-home workshop-test
exec setpriv --reuid="$2" --regid="$3" --clear-groups /bin/bash -c "$1" test "$2" "$3"
'''
            args = ([checks, str(os.getuid()), str(os.getgid())] if tool == "maintain"
                    else [str(os.getuid()), str(os.getgid())])
            subprocess.run([*command, "run", "--rm", "-T", "--no-deps", "--pull", "never",
                            "--entrypoint", "/bin/bash", tool, "-c", launch, "test", *args],
                           env=env, check=True, stdin=subprocess.DEVNULL, capture_output=True, text=True)
            if tool != "maintain":
                assert all((home / relative).read_bytes() == contents for relative, contents in before.items())
            assert all((home / relative).read_text() == "updated-" + tool
                       for owner, paths in SECRETS.items() if owner == tool or tool == "maintain"
                       for relative in paths)
            print(f"Docker isolation and identity checks passed: {tool} ({image}).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", action="store_true")
    parser.add_argument("--image", default="ubuntu:24.04")
    args = parser.parse_args()
    for container_home in ("/home/codex", "/home/ubuntu"):
        unit_checks(container_home)
    unit_checks("/home/developer", "developer")
    if args.docker:
        try:
            for container_home in ("/home/codex", "/home/ubuntu"):
                docker_checks(args.image, container_home)
            docker_checks(args.image, "/home/developer", "developer")
        except subprocess.CalledProcessError as error:
            sys.exit(f"Docker check failed ({error.returncode}): {error.stderr}")
