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
    for selection in ("", "codex", "claude", "opencode", "both", "all"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            answers = iter(("", selection, "", "no", "no", "no"))
            with patch.object(Path, "cwd", return_value=root), \
                 patch.object(sys, "argv", ["workshop_init.py"]), \
                 patch("builtins.input", side_effect=lambda _: next(answers)), \
                 contextlib.redirect_stdout(io.StringIO()):
                assert init.main() == 0
            assert {p.name for p in root.iterdir()} == {".workshop", "docker-bridge"}
            assert (root / ".workshop/recipes/generic/Dockerfile").is_file()
            assert (root / ".workshop/ram.lock").is_file()
            settings = config.read_toml(config.project_path(root, "config"))
            assert settings["tools"] == (selection or "both")
            with patch.object(Path, "cwd", return_value=root):
                _, env = config.project_environment()
                for tool in ("codex", "claude", "opencode"):
                    with patch.object(sys, "argv", ["workshop", tool, "a b", "--help"]), \
                         patch.object(os, "chdir"), patch.object(os, "execvpe") as execute:
                        if tool in config.TOOL_SELECTIONS[settings["tools"]]:
                            config.main()
                            assert execute.call_args.args[1] == [
                                "bash", str(config.RUNTIME / "session.sh"), tool, "a b", "--help"
                            ]
                        else:
                            try:
                                config.main()
                            except ValueError as error:
                                assert "not selected" in str(error)
                            else:
                                raise AssertionError("disabled tool launched")
                            execute.assert_not_called()
                # Run the real CLI, Python dispatch and session launcher. Only
                # storage preparation and the final Docker invocation are mocked.
                launcher = r'''
source() { SCRIPT_DIR=${1%/*}; }
prepare_container() { :; }
session_traps() { :; }
ram_run() { python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "$@"; }
export -f source prepare_container session_traps ram_run
exec bash "$@"
'''
                for tool in ("codex", "claude", "opencode"):
                    if tool not in config.TOOL_SELECTIONS[settings["tools"]]:
                        continue
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
                            str(config.RUNTIME / "compose.yaml"), "run", "--name",
                            env["WORKSHOP_ID"] + "-session", "--pull", "never", tool, *forwarded,
                        ]
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
                # Older configurations retain their Codex-only behavior.
                del settings["tools"]
                with patch.object(config, "read_toml", return_value=settings):
                    assert config.project_environment()[1]["WORKSHOP_TOOLS"] == "codex"
            env.update(CODEX_IMAGE="workshop-test", CODEX_RAM_DIR=str(root / "ram"),
                       HOST_UID="1000", HOST_GID="1000")
            result = subprocess.run(
                ["docker", "compose", "--env-file", "/dev/null", "-f",
                 str(config.RUNTIME / "compose.yaml"), "config", "--format", "json"],
                env=env, check=True, capture_output=True, text=True,
            )
            services = json.loads(result.stdout)["services"]
            for tool in ("codex", "claude", "opencode"):
                assert services[tool]["entrypoint"] == [tool]
                assert services[tool]["read_only"] is True
                assert services[tool]["build"]["args"]["WORKSHOP_TOOLS"] == (selection or "both")
            assert services["codex"]["volumes"] == services["claude"]["volumes"] == services["opencode"]["volumes"]

    # Execute the actual Dockerfile selection logic with npm replaced by a logger.
    for recipe in (config.INSTALL / "recipes").iterdir():
        dockerfile = (recipe / "Dockerfile").read_text()
        install = dockerfile.split("RUN set -eu;", 1)[1].split("\n\n", 1)[0]
        for selection in ("codex", "claude", "opencode", "both", "all", "invalid"):
            result = subprocess.run(
                ["sh", "-c", "npm() { printf '%s\\n' \"$*\"; }; set -eu;" + install],
                env={**os.environ, "WORKSHOP_TOOLS": selection, "CODEX_VERSION": "latest"},
                capture_output=True, text=True,
            )
            assert (result.returncode == 0) == (selection != "invalid")
            assert ("@openai/codex@latest" in result.stdout) == (selection in {"codex", "both", "all"})
            assert ("@anthropic-ai/claude-code" in result.stdout) == (selection in {"claude", "both", "all"})
            assert ("opencode-ai" in result.stdout) == (selection in {"opencode", "all"})
    print("Tool selection, dispatch, legacy configuration and Compose checks passed.")


if __name__ == "__main__":
    main()
