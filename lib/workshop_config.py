#!/usr/bin/env python3
"""Validate project configuration and exec the shared Bash runtime."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys

if sys.version_info < (3, 11):
    sys.exit("Workshop requires Python 3.11 or newer.")
import tomllib

INSTALL = Path(__file__).resolve().parent.parent
RUNTIME = INSTALL / "runtime"
DEFAULTS = {
    "checkpoint": "docker-bridge",
    "ram_capacity": "8G",
    "compression": "zstd",
    "memory_limit": "12g",
    "codex_version": "latest",
    "tools": "both",
}
TOOLS = ("codex", "claude", "opencode")
TOOL_SELECTIONS = {**{tool: (tool,) for tool in TOOLS}, "both": TOOLS[:2], "all": TOOLS}


def read_toml(path):
    with path.open("rb") as stream:
        return tomllib.load(stream)


def validate(root, config):
    expected = {"recipe", *DEFAULTS}
    if config.keys() != expected:
        raise ValueError(f"configuration requires exactly: {', '.join(sorted(expected))}")
    for key, value in config.items():
        if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError(f"{key} must be a nonempty string without control characters")
    for key in ("ram_capacity", "memory_limit"):
        if not re.fullmatch(r"[1-9][0-9]*[kKmMgGtT]?", config[key]):
            raise ValueError(f"{key} must be a positive integer with optional K, M, G or T suffix")
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", config["compression"]):
        raise ValueError("invalid compression algorithm name")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.+_-]*", config["codex_version"]):
        raise ValueError("codex_version must be an npm version or tag")
    if config["tools"] not in TOOL_SELECTIONS:
        raise ValueError("tools must be " + ", ".join(TOOL_SELECTIONS))
    recipe = (root / config["recipe"]).resolve()
    if (root / ".workshop").is_dir():
        recipes = root / ".workshop" / "recipes"
        if recipes.is_symlink() or recipes.resolve() not in recipe.parents:
            raise ValueError("recipe must be inside .workshop/recipes")
    checkpoint = (root / config["checkpoint"]).resolve()
    ram = root / ".workshop-ram"
    if any(ord(c) < 32 for path in (root, recipe, checkpoint) for c in str(path)):
        raise ValueError("resolved paths must not contain control characters")
    if str(checkpoint) in {"/", "/home", "/root", "/usr", "/etc", "/var", "/opt", "/work"}:
        raise ValueError(f"refusing unsafe checkpoint directory: {checkpoint}")
    # A checkpoint must never contain the project, recipe or RAM home.
    for protected in (root, INSTALL, recipe, ram):
        if checkpoint == protected or checkpoint in protected.parents:
            raise ValueError(f"checkpoint must not contain {protected}")
    for protected in (recipe, ram):
        if protected in checkpoint.parents:
            raise ValueError(f"checkpoint must not be inside {protected}")
    if recipe == root or recipe in root.parents or recipe == ram or ram in recipe.parents:
        raise ValueError("recipe must be separate from the project root and RAM home")
    for name in (".workshop", ".workshop-generated.json", ".workshop-ram-state", ".workshop-ram-lock"):
        reserved = root / name
        if checkpoint == reserved or reserved in checkpoint.parents:
            raise ValueError(f"checkpoint conflicts with {reserved}")
    if checkpoint.exists() and not checkpoint.is_dir():
        raise ValueError("checkpoint must be a directory")
    return recipe, checkpoint


def project_root():
    cwd = Path.cwd().resolve()
    for root in (cwd, *cwd.parents):
        if (root / ".workshop").exists() or (root / ".workshop").is_symlink():
            break
    else:
        raise ValueError("no .workshop found; run workshop init in the environment directory")
    return root


def project_path(root, kind):
    marker = root / ".workshop"
    if marker.is_symlink():
        raise ValueError(".workshop must not be a symlink")
    if marker.is_file():
        return root / {"config": ".workshop", "metadata": ".workshop-generated.json",
                       "state": ".workshop-ram-state", "lock": ".workshop-ram-lock"}[kind]
    if marker.is_dir():
        path = marker / {"config": "config.toml", "metadata": "generated.json",
                         "state": "ram-state", "lock": "ram.lock"}[kind]
        if path.is_symlink():
            raise ValueError(f"Workshop {kind} must not be a symlink")
        return path
    raise ValueError(".workshop must be a file or directory")


def warn_legacy(root):
    project_path(root, "config")
    if (root / ".workshop").is_file():
        print("Workshop: deprecated legacy layout. This mode is no longer developed "
              "and may not work with newer features. Run workshop update to migrate.", file=sys.stderr)


def check_update(root):
    config = read_toml(project_path(root, "config"))
    flag = config.get("updating", False)
    if not isinstance(flag, bool):
        raise ValueError("updating must be a boolean")
    metadata = project_path(root, "metadata")
    if metadata.is_symlink():
        raise ValueError("generated-file metadata must not be a symlink")
    pending = metadata.exists() and "pending" in json.loads(metadata.read_text())
    if flag or pending:
        raise ValueError("Workshop is updating; resolve files and run workshop update --finish")


def project_environment():
    root = project_root()
    warn_legacy(root)
    check_update(root)
    config = read_toml(project_path(root, "config"))
    config.pop("updating", None)
    config.setdefault("tools", "codex")
    recipe, checkpoint = validate(root, config)
    if not (recipe / "Dockerfile").is_file():
        raise ValueError(f"recipe has no Dockerfile: {recipe}")
    identity = "workshop-" + hashlib.sha256(os.fsencode(root)).hexdigest()[:24]
    env = os.environ.copy()
    env.update({
        "WORKSHOP_ROOT": str(root),
        "WORKSHOP_LAYOUT": "legacy" if (root / ".workshop").is_file() else "current",
        "WORKSHOP_ID": identity,
        "WORKSHOP_RECIPE": str(recipe),
        "CODEX_BRIDGE_DIR": str(checkpoint),
        "ZRAM_SIZE": config["ram_capacity"],
        "ZRAM_ALGORITHM": config["compression"],
        "WORKSHOP_MEMORY_LIMIT": config["memory_limit"],
        "CODEX_VERSION": config["codex_version"],
        "WORKSHOP_TOOLS": config["tools"],
        "CODEX_BASE_IMAGE": f"local/{identity}:base",
        "CODEX_MAINTAINED_IMAGE": f"local/{identity}:maintained",
        "COMPOSE_PROJECT_NAME": identity,
        "COMPOSE_IGNORE_ORPHANS": "true",
    })
    return root, env


def main():
    command, *args = sys.argv[1:]
    if command not in {"build", *TOOLS, "maintain", "ram"}:
        raise ValueError("unknown runtime command")
    if command in {"build", "maintain"} and args:
        raise ValueError(f"workshop {command} takes no arguments")
    if command == "ram" and args != ["stop", "--why"] and (
        len(args) != 1 or args[0] not in {"start", "save", "stop", "reset", "purge", "status"}
    ):
        raise ValueError("usage: workshop ram start|save|stop [--why]|reset|purge|status")
    if command in TOOLS and "--" in args:
        separator = args.index("--")
        if separator:
            raise ValueError("Workshop options before -- are not supported yet; put tool arguments after --")
        args = args[separator + 1:]
    root, env = project_environment()
    if command in TOOLS:
        if command not in TOOL_SELECTIONS[env["WORKSHOP_TOOLS"]]:
            raise ValueError(f"{command} is not selected; update tools in the Workshop configuration and rebuild the recipe")
        args = [command, *args]
        command = "session"
    os.chdir(root)
    os.execvpe("bash", ["bash", str(RUNTIME / f"{command}.sh"), *args], env)


if __name__ == "__main__":
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "--check-update":
            check_update(Path(sys.argv[2]))
        else:
            main()
    except (OSError, ValueError) as error:
        sys.exit(f"Workshop: {error}")
