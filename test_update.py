"""Run python3 -B test_update.py; external Docker validation is mocked."""
import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

import workshop_config as config
import workshop_init as init
import workshop_update as update


def fails(callback, message):
    try:
        callback()
    except (ValueError, OSError) as error:
        assert message in str(error), str(error)
    else:
        raise AssertionError(f"expected failure: {message}")


def main():
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory).resolve()
        source = base / "templates"
        source.mkdir()
        (source / "Dockerfile").write_text("FROM ubuntu:24.04\nRUN echo old\n")
        (source / "defaults.toml").write_text('tools = "both"\n')
        (source / "settings.json").write_text('{"value": 1}\n')
        (source / "asset.bin").write_bytes(b"\0old")
        (source / "obsolete.txt").write_text("keep or delete me\n")
        root = base / "environment"
        root.mkdir()
        settings = {"recipe": "recipe", **config.DEFAULTS, "tools": "codex", "checkpoint": "custom-checkpoint"}
        original_config = "# Keep my settings and comments\n" + "".join(
            f"{key} = {json.dumps(value)}\n" for key, value in settings.items())

        def reset(legacy=False):
            shutil.rmtree(root / "recipe", ignore_errors=True)
            shutil.copytree(source, root / "recipe")
            (root / ".workshop").write_text(original_config)
            (root / update.METADATA).unlink(missing_ok=True)
            if not legacy:
                update.record_generated(root, "recipe", source)

        def docker_check(args, **kwargs):
            if args[:3] == ["docker", "buildx", "version"]:
                return "github.com/docker/buildx v0.15.1 abc\n"
            if args[:4] == ["docker", "buildx", "build", "--check"]:
                dockerfile = Path(args[args.index("--file") + 1]).read_text()
                if "BROKEN" in dockerfile:
                    raise ValueError("invalid Dockerfile")
                assert "WORKSHOP_TOOLS=codex" in args
                assert "CODEX_VERSION=latest" in args
                return ""
            raise AssertionError(args)

        with patch.object(update, "checked", side_effect=docker_check) as checks, \
                contextlib.redirect_stdout(io.StringIO()):
            reset()
            # Only insertions, including a new nested file, are automatic.
            (root / "recipe/Dockerfile").write_text("FROM ubuntu:24.04\n")
            (source / "new/note.txt").parent.mkdir()
            (source / "new/note.txt").write_text("new asset\n")
            (root / "recipe/user.txt").write_bytes(b"user data\0")
            update.run_update(root)
            assert (root / "recipe/Dockerfile").read_bytes() == (source / "Dockerfile").read_bytes()
            assert (root / "recipe/new/note.txt").read_text() == "new asset\n"
            assert (root / ".workshop").read_text() == original_config
            assert (root / "recipe/user.txt").read_bytes() == b"user data\0"
            assert "pending" not in json.loads((root / update.METADATA).read_text())
            config.check_update(root)
            update.run_update(root)  # repeated update is safe
            assert checks.call_count >= 4

            # Replacements require review, even for otherwise untouched files.
            reset()
            (root / "recipe/Dockerfile").write_text("FROM ubuntu:24.04\nRUN echo customized\n")
            update.run_update(root)
            assert config.read_toml(root / ".workshop")["updating"] is True
            assert update.MARKERS.search((root / "recipe/Dockerfile").read_bytes())
            fails(lambda: update.run_update(root), "already in progress")
            fails(lambda: update.run_update(root, finishing=True), "unresolved")
            for command in ("build", "codex", "claude", "opencode", "maintain", "ram"):
                args = ["workshop", command] + (["status"] if command == "ram" else [])
                with patch.object(Path, "cwd", return_value=root), patch.object(sys, "argv", args), \
                        patch.object(os, "execvpe") as execute:
                    fails(config.main, "Workshop is updating")
                    execute.assert_not_called()
            # A manually removed flag cannot bypass pending metadata.
            (root / ".workshop").write_text(original_config)
            fails(lambda: config.check_update(root), "Workshop is updating")
            (root / "recipe/Dockerfile").write_text("BROKEN\n")
            fails(lambda: update.run_update(root, finishing=True), "invalid Dockerfile")
            # Keeping the customized version is a valid review outcome.
            (root / "recipe/Dockerfile").write_text("FROM ubuntu:24.04\nRUN echo customized\n")
            update.run_update(root, finishing=True)
            assert (root / ".workshop").read_text() == original_config
            config.check_update(root)
            fails(lambda: update.run_update(root, finishing=True), "no pending")

            # Removed template files may be retained and become untracked.
            reset()
            obsolete = (source / "obsolete.txt").read_bytes()
            (source / "obsolete.txt").unlink()
            update.run_update(root)
            assert update.MARKERS.search((root / "recipe/obsolete.txt").read_bytes())
            (root / "recipe/obsolete.txt").write_bytes(obsolete)
            update.run_update(root, finishing=True)
            assert "obsolete.txt" not in json.loads((root / update.METADATA).read_text())["files"]
            update.run_update(root, force=True)
            assert (root / "recipe/obsolete.txt").read_bytes() == obsolete
            (source / "obsolete.txt").write_bytes(obsolete)

            # Force handles binary changes and removes tracked obsolete files.
            reset()
            (root / "recipe/asset.bin").write_bytes(b"\0custom")
            fails(lambda: update.run_update(root), "binary asset")
            assert (root / ".workshop").read_text() == original_config
            (source / "obsolete.txt").unlink()
            (root / "recipe/user.txt").write_text("untouched")
            update.run_update(root, force=True)
            assert (root / "recipe/asset.bin").read_bytes() == b"\0old"
            assert not (root / "recipe/obsolete.txt").exists()
            assert (root / "recipe/user.txt").read_text() == "untouched"
            assert (root / ".workshop").read_text() == original_config

            # Legacy origins are prompted once; missing tools preserve Codex.
            reset(legacy=True)
            for extra in ("settings.json", "asset.bin", "new/note.txt"):
                (root / "recipe" / extra).unlink()
            (root / ".workshop").write_text(original_config.replace('tools = "codex"\n', ""))
            answers = iter(("local", str(source)))
            with patch("builtins.input", side_effect=lambda _: next(answers)):
                update.run_update(root)
            assert config.read_toml(root / ".workshop")["tools"] == "codex"
            with patch("builtins.input", side_effect=AssertionError("unexpected prompt")):
                update.run_update(root)

            # Unknown-file collisions, parent-file collisions and symlinks fail
            # before any managed content or update state changes.
            reset()
            (source / "collision.txt").write_text("template")
            (root / "recipe/collision.txt").write_text("user")
            for force in (False, True):
                fails(lambda: update.run_update(root, force=force), "untracked")
            assert (root / "recipe/collision.txt").read_text() == "user"
            assert (root / ".workshop").read_text() == original_config
            (source / "collision.txt").unlink()
            (source / "parent/child.txt").parent.mkdir()
            (source / "parent/child.txt").write_text("template")
            (root / "recipe/parent").write_text("user file")
            fails(lambda: update.run_update(root), "expected a directory")
            shutil.rmtree(source / "parent")
            (root / "recipe/parent").unlink()
            outside = base / "outside"
            outside.write_text("outside data")
            (root / "recipe/settings.json").unlink()
            (root / "recipe/settings.json").symlink_to(outside)
            fails(lambda: update.run_update(root, force=True), "symlink")
            assert outside.read_text() == "outside data"

            # Invalid proposals and unavailable validation never commit edits.
            reset()
            valid_json = (source / "settings.json").read_text()
            (source / "settings.json").write_text("invalid JSON")
            fails(lambda: update.run_update(root, force=True), "Expecting value")
            assert (root / "recipe/settings.json").read_text() == valid_json
            (source / "settings.json").write_text(valid_json)
            with patch.object(update, "checked", side_effect=ValueError("Buildx unavailable")):
                fails(lambda: update.run_update(root, force=True), "Buildx unavailable")
            assert (root / ".workshop").read_text() == original_config
            with patch.object(update, "checked", return_value="v0.14.0"):
                fails(lambda: update.run_update(root), "0.15")

            # A failed final write retains pending state and can be finished.
            reset()
            real_write = update.atomic_write
            def interrupt_config(path, data, mode=None):
                if path == root / ".workshop" and not data.startswith(b"updating"):
                    raise OSError("interrupted commit")
                real_write(path, data, mode)
            with patch.object(update, "atomic_write", side_effect=interrupt_config):
                fails(lambda: update.run_update(root, force=True), "interrupted commit")
            fails(lambda: config.check_update(root), "Workshop is updating")
            update.run_update(root, finishing=True)
            config.check_update(root)

            # A partial recipe write cannot be silently approved by --finish.
            reset()
            (root / "recipe/Dockerfile").write_text("FROM ubuntu:24.04\nRUN echo customized\n")
            def interrupt_recipe(path, data, mode=None):
                if path == root / "recipe/Dockerfile":
                    raise OSError("interrupted recipe write")
                real_write(path, data, mode)
            with patch.object(update, "atomic_write", side_effect=interrupt_recipe):
                fails(lambda: update.run_update(root), "interrupted recipe write")
            fails(lambda: config.check_update(root), "Workshop is updating")
            fails(lambda: update.run_update(root, finishing=True), "interrupted while writing")
            update.run_update(root, force=True)
            config.check_update(root)

            with (root / ".workshop-ram-lock").open("a") as stream:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fails(lambda: update.run_update(root), "active")
            subdirectory = root / "recipe/new"
            with patch.object(Path, "cwd", return_value=subdirectory):
                assert config.project_root() == root

            # init records origin and all copied asset paths.
            fresh = base / "fresh"
            fresh.mkdir()
            answers = iter(("local", str(source), "codex", "", "no", "no", "no"))
            with patch.object(Path, "cwd", return_value=fresh), patch.object(sys, "argv", ["workshop_init.py"]), \
                    patch("builtins.input", side_effect=lambda _: next(answers)):
                assert init.main() == 0
            metadata = json.loads((fresh / update.METADATA).read_text())
            assert metadata["source"] == {"kind": "local", "value": str(source)}
            assert metadata["files"] == sorted(update.source_files(source))

        # Exercise real CLI dispatch and mutually exclusive options.
        result = subprocess.run([str(config.INSTALL / "workshop"), "update", "--help"], capture_output=True, text=True)
        assert result.returncode == 0 and "--finish" in result.stdout
        result = subprocess.run([str(config.INSTALL / "workshop"), "update", "--finish", "--force"], capture_output=True, text=True)
        assert result.returncode == 2 and "not allowed" in result.stderr
    print("Update, review, force, validation, ownership, recovery and locking checks passed.")


if __name__ == "__main__":
    main()
