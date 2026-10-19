"""Run with python3 -B tests/test_workshop.py; no Docker daemon or sudo needed."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import workshop_config as config
import workshop_init as init


def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        answers = iter(("verilator", "", "no", "no", "no"))
        with patch.object(Path, "cwd", return_value=root), \
             patch.object(sys, "argv", ["workshop_init.py"]), \
             patch("builtins.input", side_effect=lambda _: next(answers)), \
             contextlib.redirect_stdout(io.StringIO()):
            assert init.main() == 0
        settings = config.read_toml(config.project_path(root, "config"))
        recipe, _ = config.validate(root, settings)
        assert settings["recipe"] == ".workshop/recipes/verilator"
        assert (recipe / "Dockerfile").read_bytes() == (
            config.INSTALL / "recipes/verilator/Dockerfile"
        ).read_bytes()
        assert json.loads(config.project_path(root, "metadata").read_text())["source"] == {
            "kind": "bundled", "value": "verilator"
        }
    for selection in ("", "codex", "claude", "opencode", "both", "all"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            answers = iter(("", "", "no", "no", "no"))
            with patch.object(Path, "cwd", return_value=root), \
                 patch.object(sys, "argv", ["workshop_init.py"]), \
                 patch("builtins.input", side_effect=lambda _: next(answers)), \
                 contextlib.redirect_stdout(io.StringIO()):
                assert init.main() == 0
            assert {p.name for p in root.iterdir()} == {".workshop", "docker-bridge"}
            assert (root / ".workshop/recipes/generic/Dockerfile").is_file()
            assert (root / ".workshop/ram.lock").is_file()
            settings = config.read_toml(config.project_path(root, "config"))
            assert "tools" not in settings
            # Old selections remain readable, but never disable a launcher.
            if selection:
                settings["tools"] = selection
                config.project_path(root, "config").write_text("".join(
                    f"{key} = {json.dumps(value)}\n" for key, value in settings.items()))
            with patch.object(Path, "cwd", return_value=root):
                _, env = config.project_environment()
                for tool in ("codex", "claude", "opencode"):
                    with patch.object(sys, "argv", ["workshop", tool, "a b", "--help"]), \
                         patch.object(os, "chdir"), patch.object(os, "execvpe") as execute:
                        config.main()
                        assert execute.call_args.args[1] == [
                            "bash", str(config.RUNTIME / "session.sh"), tool, "a b", "--help"
                        ]
                # Run the real CLI, Python dispatch and session launcher. Only
                # storage preparation and the final Docker invocation are mocked.
                launcher = r'''
source() { SCRIPT_DIR=${1%/*}; }
prepare_container() { export WORKSHOP_SESSION_CONFIG="$WORKSHOP_ROOT/.workshop/session/$1/compose.json"; }
session_traps() { :; }
docker() {
    case "$1" in
        ps)
            [[ ${TEST_DOCKER_FAILURE:-} != yes ]] || return 1
            [[ ${TEST_EXISTING_SESSION:-} != yes ]] || echo leftover
            ;;
        rm)
            echo "removing $2" >&2
            [[ ${TEST_RUNNING_SESSION:-} != yes ]] || return 1
            ;;
    esac
    return 0
}
ram_run() { python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "$@"; }
export -f source prepare_container session_traps docker ram_run
exec bash "$@"
'''
                for tool in ("codex", "claude", "opencode"):
                    for passed, forwarded in (
                        (["--yolo"], ["--yolo"]),
                        (["--", "--yolo"], ["--yolo"]),
                        (["--help"], ["--help"]),
                        (["--", "--help"], ["--help"]),
                        (["--"], []),
                        (["--", "exec", "--", "a b", ""], ["exec", "--", "a b", ""]),
                    ):
                        result = subprocess.run(
                            ["bash", "-c", launcher, "test", str(config.INSTALL / "bin/workshop"), tool, *passed],
                            cwd=root, capture_output=True, text=True,
                        )
                        assert result.returncode == 0, result.stderr
                        docker_args = json.loads(result.stdout.splitlines()[-1])
                        assert docker_args == [
                            "docker", "compose", "--env-file", "/dev/null", "--file",
                            str(config.RUNTIME / "compose.yaml"), "--file",
                            str(root / ".workshop/session" / tool / "compose.json"), "run", "--name",
                            env["WORKSHOP_ID"] + "-session", "--pull", "never", tool, *forwarded,
                        ]
                    for extra_env, succeeds, removes in (
                        ({"TEST_EXISTING_SESSION": "yes"}, True, True),
                        ({"TEST_EXISTING_SESSION": "yes", "TEST_RUNNING_SESSION": "yes"}, False, True),
                        ({"TEST_DOCKER_FAILURE": "yes"}, False, False),
                    ):
                        result = subprocess.run(
                            ["bash", "-c", launcher, "test", str(config.INSTALL / "bin/workshop"), tool],
                            cwd=root, capture_output=True, text=True, env={**os.environ, **extra_env},
                        )
                        assert (result.returncode == 0) == succeeds, result.stderr
                        assert ("removing " + env["WORKSHOP_ID"] + "-session" in result.stderr) == removes
                        assert ("Starting " in result.stdout) == succeeds
                    result = subprocess.run(
                        [str(config.INSTALL / "bin/workshop"), tool, "--unknown", "--", "--help"],
                        cwd=root, capture_output=True, text=True,
                    )
                    assert result.returncode != 0 and "before --" in result.stderr
                for args in (["reset"], ["purge"], ["stop", "--why"]):
                    with patch.object(sys, "argv", ["workshop", "ram", *args]), \
                         patch.object(os, "chdir"), patch.object(os, "execvpe") as execute:
                        config.main()
                        assert execute.call_args.args[1] == [
                            "bash", str(config.RUNTIME / "ram.sh"), *args
                        ]
                ram_launcher = r'''
source() { :; }
ram_traps() { :; }
ram_lock() { :; }
ram_stop() { printf '%s\n' "$*"; }
export -f source ram_traps ram_lock ram_stop
exec bash "$@"
'''
                result = subprocess.run(
                    ["bash", "-c", ram_launcher, "test", str(config.INSTALL / "bin/workshop"),
                     "ram", "stop", "--why"], cwd=root, capture_output=True, text=True,
                )
                assert result.returncode == 0 and result.stdout == "--why\n", result.stderr
                for args in (["save", "--why"], ["stop", "--why", "--why"], ["stop", "--unknown"]):
                    result = subprocess.run(
                        [str(config.INSTALL / "bin/workshop"), "ram", *args],
                        cwd=root, capture_output=True, text=True,
                    )
                    assert result.returncode != 0 and "usage:" in result.stderr
            env.update(CODEX_IMAGE="workshop-test", CODEX_RAM_DIR=str(root / "ram"),
                       HOST_UID="1000", HOST_GID="1000")
            result = subprocess.run(
                ["docker", "compose", "--env-file", "/dev/null", "-f",
                 str(config.RUNTIME / "compose.yaml"), "config", "--format", "json"],
                env=env, check=True, capture_output=True, text=True,
            )
            services = json.loads(result.stdout)["services"]
            for service in services.values():
                assert service["tmpfs"] == ["/tmp:rw,exec,nosuid,nodev,size=2g"]
            for tool in ("codex", "claude", "opencode"):
                assert services[tool]["entrypoint"] == [tool]
                assert services[tool]["read_only"] is True
                assert services[tool]["build"]["args"]["WORKSHOP_TOOLS"] == "all"
            assert services["codex"]["volumes"] == services["claude"]["volumes"] == services["opencode"]["volumes"]

    # Execute each recipe's actual install command with npm replaced by a logger.
    for recipe in (config.INSTALL / "recipes").iterdir():
        dockerfile = (recipe / "Dockerfile").read_text()
        install = "npm install --global " + dockerfile.split("RUN npm install --global ", 1)[1].split("\n\n", 1)[0]
        result = subprocess.run(
            ["sh", "-c", "npm() { printf '%s\\n' \"$*\"; }; set -eu;" + install],
            env={**os.environ, "WORKSHOP_TOOLS": "claude", "CODEX_VERSION": "latest"},
            check=True, capture_output=True, text=True,
        )
        assert all(package in result.stdout for package in
                   ("@openai/codex@latest", "@anthropic-ai/claude-code", "opencode-ai"))
    print("Agent installation, dispatch, legacy configuration and Compose checks passed.")


if __name__ == "__main__":
    main()
