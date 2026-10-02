# Workshop

Workshop generates editable development environments that share this installation's
Bash runtime. Normal Codex and Claude sessions have a read-only system filesystem and a
writable home on compressed RAM; maintenance sessions can save system repairs.

## Installation

The Linux host needs Python **3.11+**, Bash, Docker with the **Docker Compose
plugin**, sudo, util-linux (including zramctl and flock), kmod, e2fsprogs and rsync.
The kernel must support zram and dynamic device allocation. Docker access and
sudo privileges are required for sessions and RAM operations.

Keep this checkout installed, and symlink its executable into a directory on PATH:

```bash
mkdir -p ~/.local/bin
ln -s "$(pwd)/workshop" ~/.local/bin/workshop
export PATH="$HOME/.local/bin:$PATH"
```

Run the symlink command from this checkout. Generated projects contain no runtime
scripts and continue to use this shared installation.

## Create an environment

```bash
mkdir my-environment
cd my-environment
workshop init
```

The questionnaire asks, in order:

1. Recipe: discovered names from the installation's **recipes/** directory, or
   **local** (then a local recipe directory). **generic** is the default when present.
2. Tools to install: **codex**, **claude**, or **both** (default).
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

The generated `.workshop` is TOML:

```toml
recipe = "recipe"
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
| `tools` | Install `codex`, `claude`, or `both` |

All fields are required except `tools`, which defaults to `codex` for older configurations;
unknown fields are errors. Relative paths resolve against
the directory containing `.workshop`. Commands search upward for the nearest
configuration, so they also work from project subdirectories. Configuration is
parsed with Python's standard-library `tomllib`, never evaluated as shell code.

```text
my-environment/
├── .workshop
├── recipe/
│   ├── Dockerfile
│   └── defaults.toml
├── docker-bridge/          disk checkpoint
├── .workshop-ram/          mounted RAM home → container /home/codex
│   └── work/              container working directory
├── .workshop-ram-state     device identity, boot ID, readiness and checkpoint mount ID
└── .workshop-ram-lock      one active session/storage operation
```

Put source files under `docker-bridge/work/` **before** RAM initialization, or
under `.workshop-ram/work/` after initialization. Container settings and login
state live in the RAM home (`.codex/`, `.claude/`, and `.claude.json`) and are checkpointed too. Keep RAM,
checkpoints, state and lock files out of version control. Each environment must
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
Initialization copies that directory to `recipe/`; it rejects symlink assets.
No maintained-image templates or home snapshots are copied.
`defaults.toml` is used only during init; edit `.workshop` afterward.

```bash
workshop build
workshop codex
workshop codex -- --help    # arguments after -- are passed unchanged to Codex
workshop claude
workshop claude -- --help   # arguments after -- are passed unchanged to Claude
workshop maintain
```

The first `--` separates Workshop options from Codex or Claude arguments.
Workshop has no session options yet, so anything before that separator is
rejected. Arguments after it are forwarded unchanged. Calls without a separator
still forward all arguments. For a tool command that needs its own `--`, use
the Workshop separator first: `workshop codex -- exec -- "prompt"`.

The build context is exclusively the configured recipe directory. Project files,
checkpoints and RAM contents outside it are not sent to Docker. Keep credentials
out of the recipe itself. Builds update only the base image. A saved maintained
image continues to take precedence, and build output explains this when present.

Claude is installed using `npm install --global @anthropic-ai/claude-code`.
Both tools share the same image and saved home. Each Compose service explicitly
selects its own executable, including when using a maintained image.
To add Claude to an existing environment, set `tools = "both"` in `.workshop`
and update its copied Dockerfile with the installation logic from the matching
bundled recipe, then run `workshop build`. If a maintained image takes precedence,
install Claude there with `sudo npm install --global @anthropic-ai/claude-code`
inside `workshop maintain` and exit successfully.

Normal and maintenance sessions automatically initialize missing RAM from the
checkpoint, or reuse a validated existing mount. Invalid, stale and incomplete
state is refused. One session or mutating RAM command may run per environment;
different environments can run concurrently. Status remains available during sessions.

All services use a **2 GiB /tmp** and **2048 PID limit**, fixed in the shared
Compose file. These are runtime settings, not Dockerfile or questionnaire options.
The configured memory limit applies to all services.

## RAM and checkpointing

```bash
workshop ram start    # initialize from disk, or validate and reuse existing RAM
workshop ram status   # filesystem capacity and zram consumption
workshop ram save     # checkpoint, with no session or other writer active
workshop ram stop     # checkpoint, unmount and release this environment's device
workshop ram reset    # save and release, like stop; keep RAM if saving fails
workshop ram purge    # discard RAM and stale state; keep disk checkpoint untouched
```

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
for file in workshop *.sh; do bash -n "$file" || exit; done
python3 -B test_workshop.py
python3 -B test_ram.py
shellcheck -x workshop *.sh
python3 -B -c 'import ast,pathlib,tomllib; [ast.parse(p.read_text()) for p in pathlib.Path(".").glob("workshop_*.py")]; [tomllib.loads(p.read_text()) for p in pathlib.Path("recipes").glob("*/defaults.toml")]'
git diff --check
```

For host validation, initialize two temporary environments (including identically
named directories under different parents), exercise default and advanced answers,
and invoke Workshop through its PATH symlink from a subdirectory. Check overwrite
refusal, a deliberately failing build followed by retry, automatic RAM startup,
save/stop, failed checkpoint retention, and repeated maintenance using the saved
image. Docker Compose, Docker access, zram and privilege errors should be explicit.
