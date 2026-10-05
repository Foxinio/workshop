#!/usr/bin/env python3
"""Update generated files using source/path metadata, without content snapshots."""
import argparse
import ast
import difflib
import fcntl
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
import tomllib

from workshop_config import DEFAULTS, INSTALL, project_root, read_toml, validate

METADATA = ".workshop-generated.json"
MARKERS = re.compile(rb"(?m)^(?:<<<<<<< WORKSHOP CURRENT|======= WORKSHOP TEMPLATE|>>>>>>> WORKSHOP TEMPLATE)\r?$")
FLAG = re.compile(r"(?m)^updating\s*=\s*(?:true|false)[ \t]*(?:#[^\n]*)?\n?")


def safe_path(base, name):
    relative = PurePosixPath(name)
    if not name or relative.is_absolute() or ".." in relative.parts or str(relative) != name or name == ".":
        raise ValueError(f"unsafe generated path: {name}")
    path = base
    if base.is_symlink():
        raise ValueError(f"symlink is not allowed: {base}")
    for index, part in enumerate(relative.parts):
        path = path / part
        if path.is_symlink():
            raise ValueError(f"symlink is not allowed: {path}")
        if index < len(relative.parts) - 1 and path.exists() and not path.is_dir():
            raise ValueError(f"expected a directory: {path}")
    if path.exists() and not path.is_file():
        raise ValueError(f"expected a regular file: {path}")
    return path


def atomic_write(path, data, mode=None):
    if path.is_symlink():
        raise ValueError(f"symlink is not allowed: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode is None:
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    fd, temporary = tempfile.mkstemp(prefix=".workshop-write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_metadata(root, metadata):
    atomic_write(safe_path(root, METADATA), (json.dumps(metadata, indent=2) + "\n").encode())


def source_files(source):
    files = {}
    if source.is_symlink() or not source.is_dir():
        raise ValueError(f"recipe source is not a directory: {source}")
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"recipe source contains a symlink: {path}")
        if path.is_file():
            files[path.relative_to(source).as_posix()] = path
        elif not path.is_dir():
            raise ValueError(f"recipe source contains a nonregular asset: {path}")
    if not {"Dockerfile", "defaults.toml"} <= files.keys():
        raise ValueError("recipe source requires Dockerfile and defaults.toml")
    return files


def record_generated(root, recipe, source, bundled=None):
    write_metadata(root, {
        "source": {"kind": "bundled" if bundled else "local", "value": bundled or str(source.resolve())},
        "recipe": recipe, "files": sorted(source_files(source)),
    })


def read_metadata(root, recipe):
    path = safe_path(root, METADATA)
    if not path.exists():
        return None
    metadata = json.loads(path.read_text())
    if not isinstance(metadata, dict) or metadata.get("recipe") != recipe:
        raise ValueError("generated-file metadata does not match the configured recipe")
    source = metadata.get("source")
    if not isinstance(source, dict) or source.get("kind") not in {"bundled", "local"} or not isinstance(source.get("value"), str):
        raise ValueError("invalid recipe source metadata")
    pending = metadata.get("pending", {})
    if not isinstance(pending, dict):
        raise ValueError("invalid pending update metadata")
    for names in (metadata.get("files"), pending.get("files", [])):
        if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
            raise ValueError("invalid generated-file list")
        for name in names:
            safe_path(root / recipe, name)
    if "pending" in metadata and "files" not in pending:
        raise ValueError("pending update requires a generated-file list")
    return metadata


def choose_source(root, recipe, metadata):
    if metadata is None:
        choices = sorted(p.name for p in (INSTALL / "recipes").iterdir()
                         if p.name != "local" and (p / "Dockerfile").is_file() and (p / "defaults.toml").is_file())
        answer = input(f"Original recipe ({'/'.join([*choices, 'local'])}): ").strip()
        if answer in choices:
            source = {"kind": "bundled", "value": answer}
        elif answer == "local":
            source = {"kind": "local", "value": str(Path(input("Local recipe source directory: ").strip()).expanduser().resolve())}
        else:
            raise ValueError("select a listed recipe or local")
        metadata = {"source": source, "recipe": recipe, "files": ["Dockerfile", "defaults.toml"]}
    source = metadata["source"]
    if source["kind"] == "bundled":
        if Path(source["value"]).name != source["value"] or source["value"] in {".", "..", "local"}:
            raise ValueError("invalid bundled recipe name")
        directory = INSTALL / "recipes" / source["value"]
    else:
        directory = Path(source["value"])
        if not directory.is_absolute():
            raise ValueError("local source must be absolute")
    if directory.is_symlink():
        raise ValueError("recipe source must not be a symlink")
    directory = directory.resolve()
    destination = (root / recipe).resolve()
    if directory == root or directory in root.parents or directory == destination or destination in directory.parents or directory in destination.parents:
        raise ValueError("recipe source must be separate from the generated recipe and must not contain the environment")
    return metadata, source_files(directory)


def merge_text(current, newest):
    if current == newest:
        return current, False
    try:
        if b"\0" in current or b"\0" in newest:
            raise UnicodeError()
        old = current.decode("utf-8").splitlines(keepends=True)
        new = newest.decode("utf-8").splitlines(keepends=True)
    except UnicodeError:
        raise ValueError("changed binary asset requires workshop update --force") from None
    merged = []
    review = False
    for tag, a, b, c, d in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == "equal":
            merged.extend(old[a:b])
        elif tag == "insert":
            merged.extend(new[c:d])
        else:
            review = True
            if merged and not merged[-1].endswith("\n"):
                merged.append("\n")
            merged.append("<<<<<<< WORKSHOP CURRENT\n")
            for section, lines in (("", old[a:b]), ("======= WORKSHOP TEMPLATE\n", new[c:d])):
                merged.append(section)
                merged.extend(lines)
                if lines and not lines[-1].endswith("\n"):
                    merged.append("\n")
            merged.append(">>>>>>> WORKSHOP TEMPLATE\n")
    return "".join(merged).encode(), review


def checked(args, **kwargs):
    try:
        result = subprocess.run(args, capture_output=True, text=True, **kwargs)
    except FileNotFoundError:
        raise ValueError(f"validation command is unavailable: {args[0]}") from None
    if result.returncode:
        raise ValueError(f"validation failed ({' '.join(map(str, args))}):\n{result.stderr or result.stdout}")
    return result.stdout


def validate_files(root, config, recipe, names):
    validate(root, config)
    names = set(names) | {"Dockerfile", "defaults.toml"}
    for name in sorted(set(names)):
        path = safe_path(recipe, name)
        if path.exists() and MARKERS.search(path.read_bytes()):
            raise ValueError(f"unresolved Workshop review markers: {name}")
    for required in ("Dockerfile", "defaults.toml"):
        if not safe_path(recipe, required).is_file():
            raise ValueError(f"recipe requires {required}")
    defaults = read_toml(recipe / "defaults.toml")
    if defaults.keys() - DEFAULTS.keys():
        raise ValueError("unknown field in recipe defaults.toml")
    validate(root, {**config, **defaults})
    # Official frontend parsing, without running RUN commands or updating images.
    version = checked(["docker", "buildx", "version"])
    match = re.search(r"\bv?(\d+)\.(\d+)\.(\d+)", version)
    if not match or tuple(map(int, match.groups())) < (0, 15, 0):
        raise ValueError("Dockerfile validation requires Docker Buildx 0.15 or newer")
    env = os.environ.copy()
    env.update(WORKSHOP_ROOT=str(root), WORKSHOP_ID="workshop-update-check",
               WORKSHOP_RECIPE=str(recipe), CODEX_IMAGE="workshop-update-check",
               CODEX_RAM_DIR=str(root / ".workshop-ram"), HOST_UID=str(os.getuid()),
               HOST_GID=str(os.getgid()), WORKSHOP_MEMORY_LIMIT=config["memory_limit"],
               CODEX_VERSION=config["codex_version"], WORKSHOP_TOOLS=config["tools"])
    for name in sorted(set(names)):
        path = safe_path(recipe, name)
        if not path.exists():
            continue
        suffix = path.suffix.lower()
        if path.name == "Dockerfile" or path.name.startswith("Dockerfile.") or suffix == ".dockerfile":
            checked(["docker", "buildx", "build", "--check", "--file", str(path),
                     "--build-arg", f"CODEX_VERSION={config['codex_version']}",
                     "--build-arg", f"WORKSHOP_TOOLS={config['tools']}", str(recipe)])
        elif suffix == ".toml":
            read_toml(path)
        elif suffix == ".json":
            json.loads(path.read_bytes())
        elif suffix in {".yaml", ".yml"}:
            try:
                import yaml
            except ImportError:
                raise ValueError("managed YAML validation requires PyYAML (python3-yaml)") from None
            try:
                list(yaml.safe_load_all(path.read_bytes()))
            except yaml.YAMLError as error:
                raise ValueError(f"invalid YAML in {name}: {error}") from None
            if path.stem in {"compose", "docker-compose"}:
                checked(["docker", "compose", "--env-file", "/dev/null", "--file", str(path), "config", "--quiet"], env=env)
        elif suffix in {".conf", ".cfg", ".ini", ".config"}:
            raise ValueError(f"unsupported generated configuration format: {name}")
        elif suffix == ".sh":
            checked(["bash", "-n", str(path)])
        elif suffix == ".py":
            ast.parse(path.read_bytes(), filename=name)


def clean_config(root):
    path = safe_path(root, ".workshop")
    text = path.read_text()
    config = read_toml(path)
    flag = config.pop("updating", False)
    if not isinstance(flag, bool):
        raise ValueError("updating must be a boolean")
    cleaned = FLAG.sub("", text)
    if "updating" in tomllib.loads(cleaned):
        raise ValueError("updating must appear as a top-level updating = true/false line")
    for key, value in DEFAULTS.items():
        if key not in config:
            config[key] = "codex" if key == "tools" else value
            cleaned = cleaned.rstrip("\n") + f"\n{key} = {json.dumps(config[key])}\n"
    validate(root, config)
    if tomllib.loads(cleaned) != config:
        raise ValueError("cannot append configuration fields inside a TOML table")
    recipe_path = Path(config["recipe"])
    if recipe_path.is_absolute() or ".." in recipe_path.parts:
        raise ValueError("updating requires a recipe directory within the environment")
    directory = root
    for part in recipe_path.parts:
        directory = directory / part
        if directory.is_symlink():
            raise ValueError("recipe path must not contain symlinks")
    if not directory.is_dir():
        raise ValueError("recipe directory is missing")
    return config, cleaned.encode(), flag


def finish(root, config, clean, metadata, validated=False):
    if not metadata or "pending" not in metadata:
        raise ValueError("no pending update to finish")
    if metadata["pending"].get("prepared") is not True:
        raise ValueError("update was interrupted while writing files; run workshop update --force")
    names = set(metadata["files"]) | set(metadata["pending"]["files"])
    if not validated:
        validate_files(root, config, root / config["recipe"], names)
    metadata["files"] = metadata["pending"]["files"]
    del metadata["pending"]
    # Pending metadata keeps runtime blocked if clearing the flag is interrupted.
    atomic_write(root / ".workshop", clean)
    write_metadata(root, metadata)


def run_update(root, force=False, finishing=False):
    lock = safe_path(root, ".workshop-ram-lock")
    with lock.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("another session or storage command is active") from None
        config, clean, flag = clean_config(root)
        metadata = read_metadata(root, config["recipe"])
        if finishing:
            finish(root, config, clean, metadata)
        else:
            if (flag or metadata and "pending" in metadata) and not force:
                raise ValueError("update already in progress; run workshop update --finish or --force")
            metadata, templates = choose_source(root, config["recipe"], metadata)
            owned = set(metadata["files"]) | set(metadata.get("pending", {}).get("files", []))
            destination = root / config["recipe"]
            outputs, modes = {}, {}
            review = False
            for name in sorted(owned | templates.keys()):
                path = safe_path(destination, name)
                if name not in owned and path.exists():
                    raise ValueError(f"refusing to overwrite untracked recipe file: {name}")
                current = path.read_bytes() if path.exists() else b""
                newest = templates[name].read_bytes() if name in templates else b""
                if force or not path.exists():
                    outputs[name] = newest if name in templates else None
                else:
                    outputs[name], needs_review = merge_text(current, newest)
                    review |= needs_review
                    if name not in templates and not needs_review:
                        outputs[name] = None
                modes[name] = stat.S_IMODE(templates[name].stat().st_mode) if name in templates else None
            # Validate automatic/forced results before changing generated content.
            if not review:
                with tempfile.TemporaryDirectory(prefix="workshop-update-") as directory:
                    staged = Path(directory)
                    for name, data in outputs.items():
                        if data is not None:
                            path = safe_path(staged, name)
                            path.parent.mkdir(parents=True, exist_ok=True)
                            path.write_bytes(data)
                    validate_files(root, config, staged, outputs)
            metadata["pending"] = {"files": sorted(templates), "prepared": False}
            write_metadata(root, metadata)
            if review or force:
                atomic_write(root / ".workshop", b"updating = true\n" + clean)
            for name, data in outputs.items():
                path = safe_path(destination, name)
                if data is None:
                    path.unlink(missing_ok=True)
                elif not path.exists() or path.read_bytes() != data:
                    atomic_write(path, data, modes[name])
            metadata["pending"]["prepared"] = True
            write_metadata(root, metadata)
            if review:
                print("Update prepared. Resolve Workshop markers in recipe files, then run workshop update --finish.")
                return
            finish(root, config, clean, metadata, validated=True)
        print('Update complete. To enable Claude/OpenCode, set tools = "both"/"all" in .workshop, then run workshop build.')
        print("A saved maintained image still takes precedence; update its tools through workshop maintain if needed.")


def main():
    parser = argparse.ArgumentParser(prog="workshop update")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--finish", action="store_true", help="validate reviewed files and enable Workshop commands")
    modes.add_argument("--force", action="store_true", help="replace managed recipe files, preserving settings")
    args = parser.parse_args()
    run_update(project_root(), force=args.force, finishing=args.finish)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, SyntaxError, EOFError, KeyboardInterrupt) as error:
        sys.exit(f"Workshop update: {error or 'input cancelled'}")
