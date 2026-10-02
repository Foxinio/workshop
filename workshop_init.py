#!/usr/bin/env python3
"""Generate an editable recipe and project configuration, then optional setup."""
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from workshop_config import DEFAULTS, INSTALL, TOOL_SELECTIONS, read_toml, validate


def ask(label, default):
    answer = input(f"{label} [{default}]: ").strip()
    return answer or default


def yes(label, default=True):
    while True:
        answer = ask(label + " (yes/no)", "yes" if default else "no").lower()
        if answer in {"y", "yes", "n", "no"}:
            return answer in {"y", "yes"}
        print("Please enter yes or no.")


def is_recipe(directory):
    return directory.is_dir() and all(
        (directory / name).is_file() for name in ("Dockerfile", "defaults.toml")
    )


def main():
    if len(sys.argv) != 1:
        raise ValueError("workshop init takes no arguments")
    root = Path.cwd().resolve()
    for target in (root / ".workshop", root / "recipe"):
        if target.exists() or target.is_symlink():
            raise ValueError(f"refusing to overwrite {target}")
    recipes = {
        directory.name: directory
        for directory in sorted((INSTALL / "recipes").iterdir())
        if directory.name != "local" and is_recipe(directory)
    }
    default = "generic" if "generic" in recipes else next(iter(recipes), "local")
    selection = ask(f"Recipe ({'/'.join([*recipes, 'local'])})", default)
    if selection in recipes:
        source = recipes[selection]
    elif selection == "local":
        source = Path(input("Local recipe directory: ").strip()).expanduser().resolve()
    else:
        raise ValueError(f"recipe must be one of: {', '.join([*recipes, 'local'])}")
    if not is_recipe(source):
        raise ValueError(f"recipe requires Dockerfile and defaults.toml: {source}")
    if source == root or source in root.parents:
        raise ValueError("local recipe cannot contain the generated environment")
    # Do not pull unrelated files into the build context through asset symlinks.
    if any(path.is_symlink() for path in source.rglob("*")):
        raise ValueError("recipe assets must not be symbolic links")
    defaults = read_toml(source / "defaults.toml")
    if defaults.keys() - DEFAULTS.keys():
        raise ValueError("unknown field in recipe defaults.toml")
    config = {"recipe": "recipe", **DEFAULTS, **defaults}
    while True:
        config["tools"] = ask(f"Install tools ({'/'.join(TOOL_SELECTIONS)})", config["tools"]).lower()
        if config["tools"] in TOOL_SELECTIONS:
            break
        print("Please enter " + ", ".join(TOOL_SELECTIONS) + ".")
    config["checkpoint"] = ask("Disk checkpoint directory", config["checkpoint"])
    if yes("Configure advanced settings?", False):
        for key, label in (("ram_capacity", "Zram logical capacity"),
                           ("compression", "Compression algorithm"),
                           ("memory_limit", "Container memory limit"),
                           ("codex_version", "Codex build version")):
            config[key] = ask(label, config[key])
    _, checkpoint = validate(root, config)
    build = yes("Build now?")
    ram = yes("Initialize RAM now?")
    # Exclusive creation and copytree both refuse existing destinations.
    with (root / ".workshop").open("x", encoding="utf-8") as stream:
        for key, value in config.items():
            stream.write(f"{key} = {json.dumps(value, ensure_ascii=False)}\n")
    shutil.copytree(source, root / "recipe")
    # ram start expects these; create them up front so the layout is complete.
    checkpoint.mkdir(parents=True, exist_ok=True)
    (root / ".workshop-ram").mkdir(exist_ok=True)
    print(f"Created Workshop environment in {root}", flush=True)
    for enabled, args in ((build, ["build"]), (ram, ["ram", "start"])):
        if not enabled:
            continue
        result = subprocess.run([str(INSTALL / "workshop"), *args], cwd=root)
        if result.returncode:
            print("Setup incomplete; generated configuration and recipe were retained.", file=sys.stderr)
            print(f"Retry: cd {shlex.quote(str(root))} && workshop {shlex.join(args)}", file=sys.stderr)
            if args == ["build"] and ram:
                print("Then run: workshop ram start", file=sys.stderr)
            return 1
    print("Setup complete." if build and ram else "Environment created; optional setup was skipped.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, EOFError, KeyboardInterrupt) as error:
        sys.exit(f"Workshop init: {error or 'input cancelled'}")
