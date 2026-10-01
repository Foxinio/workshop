"""Run with python3 -B test_workshop.py; no Docker daemon or sudo needed."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

import workshop_config as config
import workshop_init as init


def main():
    for selection in ("", "codex", "claude", "both"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            answers = iter(("", selection, "", "no", "no", "no"))
            with patch.object(Path, "cwd", return_value=root), \
                 patch.object(sys, "argv", ["workshop_init.py"]), \
                 patch("builtins.input", side_effect=lambda _: next(answers)), \
                 contextlib.redirect_stdout(io.StringIO()):
                assert init.main() == 0
            settings = config.read_toml(root / ".workshop")
            assert settings["tools"] == (selection or "both")
            with patch.object(Path, "cwd", return_value=root):
                _, env = config.project_environment()
                for tool in ("codex", "claude"):
                    with patch.object(sys, "argv", ["workshop", tool, "a b", "--help"]), \
                         patch.object(os, "chdir"), patch.object(os, "execvpe") as execute:
                        if settings["tools"] in {tool, "both"}:
                            config.main()
                            assert execute.call_args.args[1] == [
                                "bash", str(config.INSTALL / "session.sh"), tool, "a b", "--help"
                            ]
                        else:
                            try:
                                config.main()
                            except ValueError as error:
                                assert "not selected" in str(error)
                            else:
                                raise AssertionError("disabled tool launched")
                            execute.assert_not_called()
                with patch.object(sys, "argv", ["workshop", "ram", "reset"]), \
                     patch.object(os, "chdir"), patch.object(os, "execvpe") as execute:
                    config.main()
                    assert execute.call_args.args[1] == [
                        "bash", str(config.INSTALL / "ram.sh"), "reset"
                    ]
                # Older configurations retain their Codex-only behavior.
                del settings["tools"]
                with patch.object(config, "read_toml", return_value=settings):
                    assert config.project_environment()[1]["WORKSHOP_TOOLS"] == "codex"
            env.update(CODEX_IMAGE="workshop-test", CODEX_RAM_DIR=str(root / "ram"),
                       HOST_UID="1000", HOST_GID="1000")
            result = subprocess.run(
                ["docker", "compose", "--env-file", "/dev/null", "-f",
                 str(config.INSTALL / "compose.yaml"), "config", "--format", "json"],
                env=env, check=True, capture_output=True, text=True,
            )
            services = json.loads(result.stdout)["services"]
            for tool in ("codex", "claude"):
                assert services[tool]["entrypoint"] == [tool]
                assert services[tool]["read_only"] is True
                assert services[tool]["build"]["args"]["WORKSHOP_TOOLS"] == (selection or "both")
            assert services["codex"]["volumes"] == services["claude"]["volumes"]

    # Execute the actual Dockerfile selection logic with npm replaced by a logger.
    for recipe in (config.INSTALL / "recipes").iterdir():
        dockerfile = (recipe / "Dockerfile").read_text()
        install = dockerfile.split("RUN set -eu;", 1)[1].split("\n\n", 1)[0]
        for selection in ("codex", "claude", "both", "invalid"):
            result = subprocess.run(
                ["sh", "-c", "npm() { printf '%s\\n' \"$*\"; }; set -eu;" + install],
                env={**os.environ, "WORKSHOP_TOOLS": selection, "CODEX_VERSION": "latest"},
                capture_output=True, text=True,
            )
            assert (result.returncode == 0) == (selection != "invalid")
            assert ("@openai/codex@latest" in result.stdout) == (selection in {"codex", "both"})
            assert ("@anthropic-ai/claude-code" in result.stdout) == (selection in {"claude", "both"})
    print("Tool selection, dispatch, legacy configuration and Compose checks passed.")


if __name__ == "__main__":
    main()
