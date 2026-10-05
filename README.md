# Workshop

Workshop generates editable development environments that share this installation's
Bash runtime. Normal Codex, Claude and OpenCode sessions have a read-only system filesystem and a
writable home on compressed RAM; maintenance sessions can save system repairs.

## Installation

The Linux host needs Python **3.11+**, Bash, Docker with the **Docker Compose
plugin**, sudo, util-linux (including zramctl and flock), kmod, e2fsprogs and rsync.
The kernel must support zram and dynamic device allocation. Docker access and
sudo privileges are required for sessions and RAM operations.

Keep this checkout installed, and symlink its executable into a directory on PATH:

```bash
mkdir -p ~/.local/bin
ln -s "$(pwd)/bin/workshop" ~/.local/bin/workshop
export PATH="$HOME/.local/bin:$PATH"
```

Run the symlink command from this checkout. Generated projects contain no runtime
scripts and continue to use this shared installation.

The checkout groups the CLI in `bin/`, Python modules in `lib/`, shell runtime
and Compose in `runtime/`, bundled templates in `recipes/`, and checks in `tests/`.
If you installed the previous root executable, repoint your PATH symlink to
`bin/workshop` using the command above (remove the old symlink first).

## Create an environment

```bash
mkdir my-environment
cd my-environment
workshop init
```

The questionnaire asks, in order:

1. Recipe: discovered names from the installation's **recipes/** directory, or
   **local** (then a local recipe directory). **generic** is the default when present.
2. Tools to install: **codex**, **claude**, **opencode**, **both** (Codex + Claude, default),
   or **all** (all three).
3. Disk checkpoint directory: **docker-bridge**.
4. Advanced settings? **no**. If yes: RAM capacity **8G**, compression **zstd**,
   container memory limit **12g**, and Codex build version **latest**.
5. Build now? **yes**.
6. Initialize RAM now? **yes**.

Recipe defaults supply these settings; local recipes may choose different values.
No project name is needed. Docker image names, the Compose project and container
names use a hash of the canonical environment-root path, so equal directory names
at different paths are independent. Symlink aliases of the same path share an identity.

Initialization refuses an existing `.workshop` or `recipe/`, including symlinks.
It writes configuration and copies the recipe before optional setup, then builds
and initializes RAM sequentially. A failed step stops setup, retains the generated
project, and prints the command to retry. Do not rerun init to retry setup.

## Configuration and layout

The generated `.workshop/config.toml` is TOML:

```toml
recipe = ".workshop/recipes/generic"
checkpoint = "docker-bridge"
ram_capacity = "8G"
compression = "zstd"
memory_limit = "12g"
codex_version = "latest"
tools = "both"
```

| Field | Meaning |
| --- | --- |
| `recipe` | Editable recipe directory containing Dockerfile and build assets |
| `checkpoint` | Disk copy of the container home |
| `ram_capacity` | Logical zram device size, positive integer with optional K/M/G/T |
| `compression` | Compression algorithm supported by the host's zram |
| `memory_limit` | Container memory limit, positive integer with optional K/M/G/T |
| `codex_version` | npm version or tag used when building Codex |
| `tools` | Install `codex`, `claude`, `opencode`, `both` (Codex + Claude), or `all` |

All fields are required except `tools`, which defaults to `codex` for older configurations;
the optional boolean `updating` is reserved for updates; unknown fields are errors. Relative paths resolve against
the environment root (the parent of `.workshop/`). Commands search upward for the nearest
configuration, so they also work from project subdirectories. Configuration is
parsed with Python's standard-library `tomllib`, never evaluated as shell code.

```text
my-environment/
├── .workshop/
│   ├── config.toml        project settings
│   ├── generated.json     recipe origin and managed paths; no content snapshots
│   ├── ram-state          device identity, boot ID, readiness and checkpoint mount ID
│   ├── ram.lock           one active session/storage operation
│   └── recipes/
│       └── generic/
│           ├── Dockerfile
│           └── defaults.toml
├── docker-bridge/         disk checkpoint
└── .workshop-ram/         mounted RAM home → container /home/codex
    └── work/             container working directory
```

Put source files under `docker-bridge/work/` **before** RAM initialization, or
under `.workshop-ram/work/` after initialization. Container settings and login
state live in the RAM home and are checkpointed too. Keep RAM, checkpoints, `.workshop/ram-state` and `.workshop/ram.lock` out of version control. Each environment must
use its own checkpoint directory; intentionally sharing one is unsupported.

## Recipes and builds

Both bundled recipes retain Ubuntu 24.04 and provide the selected tools, Node, Python, Git and
general command-line tools. Generic has no Java. Java includes JDKs 17 and 21,
with 21 selected by default. Edit the generated Dockerfile to change packages,
the Ubuntu version or Java defaults.

A local recipe has the same format: a Dockerfile, `defaults.toml` containing
any of the non-recipe configuration defaults above, and optional build assets.
Custom Dockerfiles must honor the `WORKSHOP_TOOLS` build argument to support tool selection.
To add a bundled recipe, add a directory under `recipes/` with both required files;
init discovers it automatically. Incomplete directories are omitted. The name
`local` is reserved for selecting an external recipe directory. If `generic` is
absent, the first discovered name in sorted order is the default (or `local` if none).
Initialization copies that directory to `.workshop/recipes/<selection>/`; it rejects symlink assets.
No maintained-image templates or home snapshots are copied.
`defaults.toml` is used only during init; edit `.workshop/config.toml` afterward.

```bash
workshop build
workshop codex
workshop codex -- --help    # arguments after -- are passed unchanged to Codex
workshop claude
workshop claude -- --help   # arguments after -- are passed unchanged to Claude
workshop opencode
workshop opencode -- --help # arguments after -- are passed unchanged to OpenCode
workshop maintain
```

The first `--` separates Workshop options from Codex, Claude or OpenCode arguments.
Workshop has no session options yet, so anything before that separator is
rejected. Arguments after it are forwarded unchanged. Calls without a separator
still forward all arguments. For a tool command that needs its own `--`, use
the Workshop separator first: `workshop codex -- exec -- "prompt"`.

The build context is exclusively the configured recipe directory. Project files,
checkpoints and RAM contents outside it are not sent to Docker. Keep credentials
out of the recipe itself. Builds update only the base image. A saved maintained
image continues to take precedence, and build output explains this when present.

Claude is installed using `npm install --global @anthropic-ai/claude-code`.
OpenCode is installed using `npm install --global opencode-ai`, as documented in
[the OpenCode installation guide](https://opencode.ai/docs/#install).
All selected tools share the same image and saved home. Each Compose service explicitly
selects its own executable, including when using a maintained image.
To add Claude to an existing environment, run `workshop update` as described below,
set `tools = "both"` in `.workshop/config.toml`, then run `workshop build`. If a maintained image takes precedence,
install Claude there with `sudo npm install --global @anthropic-ai/claude-code`
inside `workshop maintain` and exit successfully.
To add OpenCode, update the environment, use `tools = "all"` (or `"opencode"` for OpenCode alone),
and rebuild. For a maintained
image, install it with `sudo npm install --global opencode-ai` inside
`workshop maintain` and exit successfully.

Normal and maintenance sessions automatically initialize missing RAM from the
checkpoint, or reuse a validated existing mount. Invalid, stale and incomplete
state is refused. One session or mutating RAM command may run per environment;
different environments can run concurrently. Status remains available during sessions.

All services use a **2 GiB /tmp** and **2048 PID limit**, fixed in the shared
Compose file. These are runtime settings, not Dockerfile or questionnaire options.
The configured memory limit applies to all services.

## Update an existing environment

```bash
workshop update          # apply additions, prepare other changes for review
# Edit recipe files to resolve the WORKSHOP CURRENT / WORKSHOP TEMPLATE markers.
workshop update --finish # validate the reviewed result and enable commands
workshop update --force  # replace generated recipe files without review
```

Updates use the newest templates in this installed checkout; update the installation
first to obtain newer templates. They preserve `.workshop/config.toml` settings and comments,
including selected tools, checkpoint paths and limits. Missing configuration fields
are appended; a missing `tools` field becomes `codex` to preserve legacy behavior.
Updating does not build or change images. Rebuild separately after selecting tools.

If all differences are new files or inserted text, Workshop validates and applies
them automatically without setting an updating flag. Without a baseline, this is
an insertion-only heuristic, not proof that a file was never edited. Replacements
and deletions get inline markers around the current and proposed sections. Keep
either version or combine them, removing all marker lines. For a removed template
file, delete it or retain its contents; retained files become user-owned after finish.
Changed binary assets require `--force`.

During review, `.workshop/config.toml` contains `updating = true`; build, sessions, maintenance
and every RAM command (including status) refuse to run. Help and update commands
remain available. Pending metadata also blocks commands, so removing the flag alone
does not complete an update. `--finish` leaves the environment blocked if syntax
checks fail. If writing files was interrupted, use `--force` to complete the update;
it intentionally replaces edits to managed recipe files. Updates refuse active
sessions, storage operations and builds through the existing environment lock.

New environments record the source recipe and copied file paths in
`.workshop/generated.json`; no original file contents or historical templates are
stored. Older environments prompt once for their original bundled recipe or a
local source directory. Initially only their `Dockerfile` and `defaults.toml` are
recognized as generated recipe files. Other existing files remain protected:
even force mode refuses an incoming template that collides with an untracked file.
Updates never touch checkpoints, RAM contents or unrelated user files. Keep the
metadata with the environment; deleting it loses ownership of extra recipe assets.
Updates require a recipe directory inside the environment and refuse symlinks in
managed paths or source assets. Local recipe sources must remain available for
future updates, but finishing a prepared review does not need the source directory.

Validation requires Docker access and **Docker Buildx 0.15+**, using `buildx build
--check` to parse every managed Dockerfile without running its build steps. Checks
may download frontend or image metadata; missing dependencies or failed checks
prevent completion. `.workshop/config.toml` and recipe defaults receive TOML and field checks.
Other managed TOML, JSON and YAML files are parsed; YAML requires `python3-yaml`
(PyYAML). Compose YAML also passes `docker compose config`. Generated shell and
Python files receive syntax checks. Unsupported `.conf`, `.cfg`, `.ini` and
`.config` formats are refused instead of being treated as validated.

## RAM and checkpointing

```bash
workshop ram start    # initialize from disk, or validate and reuse existing RAM
workshop ram status   # filesystem capacity and zram consumption
workshop ram save     # checkpoint, with no session or other writer active
workshop ram stop     # checkpoint, unmount and release this environment's device
workshop ram stop --why # also list filesystem users if unmounting fails
workshop ram reset    # save and release, like stop; keep RAM if saving fails
workshop ram purge    # discard RAM and stale state; keep disk checkpoint untouched
```

`stop --why` uses `sudo lsof +D` to list processes with open files or working
directories under the mount whose unmount failed. Install `lsof` to use this
diagnostic; scanning a large directory tree can take time.

While RAM is active, the disk checkpoint is protected by a read-only bind mount.
Edit `.workshop-ram/work/`; saved file permissions and ACLs remain unchanged.
Saves temporarily make the checkpoint writable, then restore protection, even
on copy failure. Stop, reset or purge removes protection. Existing RAM mounts gain
protection on the next `ram start` or session launch. This discourages accidental
edits through the checkpoint path; it does not protect against privileged
remounts or writes through previously opened files or other mount aliases.

Startup prints each storage stage. Loading and saving show rsync's overall
percentage, bytes, transfer speed and ETA after scanning the file list.
`ram reset` saves RAM before releasing it and reports failures without falling
back to discarding data. Use `ram purge` when you explicitly want to discard
unsaved RAM, clear stale or malformed state, and start from the disk checkpoint.
Purge handles incomplete initialization and partially released storage when
ownership can be verified, and removes leftover files in the unmounted RAM
directory. Both commands refuse active sessions. Purge also refuses unknown live
mounts, devices mounted elsewhere, symlink paths and nested RAM mounts.

All session types stop their container before checkpointing the home, including
on session failure. Checkpoints preserve ownership, links, ACLs and extended
attributes and propagate deletions. Nested mounts and ext4's lost+found are
excluded. Keep host editors idle during saves; Workshop cannot lock arbitrary
programs. Do not edit the disk checkpoint while RAM is active.

Checkpoints are file copies, not atomic snapshots. If a save fails, RAM is
retained; fix the cause and retry `workshop ram save`. A failed save also prevents
`workshop ram stop` from releasing the device. If Docker cannot confirm that the
container stopped, checkpointing is refused.

Capacity is logical storage, not preallocated physical RAM. Compression, filesystem
overhead and page cache determine actual memory use. The container memory limit
does not bound all host zram memory use. To change capacity, compression or
checkpoint location, first successfully stop RAM using the old configuration.
Zram is a filesystem here, not swap; existing host swap is unchanged.

## Maintenance and recovery

Maintenance starts user Bash as the host UID/GID with passwordless sudo and a
writable system filesystem. Exit with `exit 0` to commit system repairs. Later
normal and maintenance sessions prefer that saved image. Home is checkpointed
separately; the RAM mount and temporary /tmp are not included in the image commit.
Ordinary sessions retain a read-only system, dropped capabilities, blocked
privilege escalation, outbound networking, and no Docker socket or exposed ports.

Failed maintenance or failed commits retain the maintenance container. Another
maintenance launch refuses to overwrite it and prints its exact name. To recover,
use that name below, replacing the example identity:

```bash
workshop ram start
container=workshop-REPLACE_WITH_PRINTED_HASH-maintenance
image=local/${container%-maintenance}:maintained
docker start -ai "$container"
# Only after a successful recovered shell, save its system changes:
docker commit --change 'ENTRYPOINT ["codex"]' --change 'CMD []' \
  --change "USER $(id -u):$(id -g)" "$container" "$image"
workshop ram save
docker rm "$container"
```

Direct Docker recovery bypasses Workshop's lock; keep other sessions and host
writers stopped. To discard unwanted system changes, remove the stopped
maintenance container yourself. Home edits remain in RAM until saved.

Power loss, reboot and device reset lose unsaved RAM. SIGKILL also prevents exit
cleanup: stop any remaining session container before saving manually. Never reset
a device to recover from a checkpoint error.

SIGINT, SIGTERM and SIGHUP interrupt copying or launching promptly. Interrupted
initialization cleans up its newly allocated device and checkpoint protection.
Interrupted sessions stop their container and save home before exiting (130 for
SIGINT, 143 for SIGTERM, 129 for SIGHUP). Further signals are ignored while this
cleanup finishes. Interrupted manual saves restore read-only protection and
retain RAM for retry. A failed save keeps RAM mounted. Purge explicitly discards
unsaved RAM; use `ram save` or `ram reset` to retry a failed checkpoint instead.

After reboot, startup deliberately refuses the old state file. Run
`workshop ram purge`, then `workshop ram start` to discard stale RAM state and
reload the disk checkpoint. Purge never touches a device identified only by an
old boot record: another environment may now own its number. If purge refuses
an unknown live mount, inspect `findmnt`, `zramctl` and the state record first;
do not reset an unverified device or mount over data.

Moving an environment changes its Docker identity. Stop RAM and sessions before
moving it, and migrate any maintained image explicitly if needed.

## Legacy project layouts

A regular `.workshop` text file identifies a legacy project; a `.workshop/`
directory identifies the current layout. Commands continue to use legacy paths
and print a deprecation warning: this mode is no longer developed and may not
work with newer features.

`workshop update` first migrates a legacy project, then performs its usual update.
This also applies to `--force` and `--finish`. Migration preserves configuration
settings and comments, recipe assets (including your edits and untracked files),
pending review metadata, RAM state and the existing lock. Recipes move to
`.workshop/recipes/<old-directory-name>/`; configuration moves to
`.workshop/config.toml`. Checkpoint and RAM mount paths stay the same. An active
Workshop session or storage operation blocks migration; retry after it exits.
A later template validation failure leaves the migrated layout available for retry.

## Existing checkout data

This conversion does not import or delete old `docker-bridge` checkpoints,
`docker-bridge-ram` mounts, `*.ram-state` files, images or containers. The old
global `local/codex-sandbox`, `local/codex-maintained` and `codex-maintenance`
names are not reused. Finish and checkpoint old sessions with the previous
runtime before migrating. Generate a new environment and copy a stopped disk
checkpoint into its configured checkpoint directory before starting its RAM.
Do not run old and new environments against the same writable checkpoint.

## Validation

Run the tool-selection checks and basic repository checks:

```bash
for file in bin/workshop runtime/*.sh; do bash -n "$file" || exit; done
python3 -B tests/test_workshop.py
python3 -B tests/test_update.py
python3 -B tests/test_ram.py
shellcheck -x -P runtime bin/workshop runtime/*.sh
python3 -B -c 'import ast,pathlib,tomllib; [ast.parse(p.read_text()) for p in pathlib.Path("lib").glob("workshop_*.py")]; [tomllib.loads(p.read_text()) for p in pathlib.Path("recipes").glob("*/defaults.toml")]'
git diff --check
```

For host validation, initialize two temporary environments (including identically
named directories under different parents), exercise default and advanced answers,
and invoke Workshop through its PATH symlink from a subdirectory. Check overwrite
refusal, a deliberately failing build followed by retry, automatic RAM startup,
save/stop, failed checkpoint retention, and repeated maintenance using the saved
image. Docker Compose, Docker access, zram and privilege errors should be explicit.
