# Bootstrapping a dde tools environment

For an agent standing in a blank directory in a fresh container, told to
make the `dde` CLI work. It covers what has to be installed in *your
own container* before `install.sh` can run, which is the part that is
easy to discover the expensive way.

Read the [Fast path](#fast-path) if the container is already equipped.
Read the rest if the preflight says it is not, or if anything fails.

---

> **The DDE tools live on the `DDE` branch, not `main`.**
> After cloning, you must check out `DDE` — the `main` branch does not
> contain DDE tools content.
>
> ```bash
> git clone -b DDE https://github.com/GoogleCloudPlatform/LifeSciences.git
> ```
>
> Or, if you have already cloned:
>
> ```bash
> git checkout DDE
> ```

---

## Fast path

```bash
cd tools
./bootstrap-preflight.sh        # auto-installs missing packages when sudo is available
./install.sh                    # ~10-20 min, mostly pip and the fpocket build
source /scion-volumes/tools/env.sh
dde doctor; echo "doctor exit=$?"
```

Four commands, and **each exit code matters**:

| command | 0 | non-zero |
|---|---|---|
| `bootstrap-preflight.sh` | ready (auto-remediated if needed) | 1 = missing prerequisites that could not be auto-installed, 2 = could not check |
| `install.sh` | complete | 3 = science stack failed, 4 = a declared binary is missing |
| `dde doctor` | usable | something is wrong; read the output, it names what |

`install.sh` exiting 3 or 4 still leaves a **working CLI**. That is
deliberate and it is also the trap: the environment is usable and
incomplete, and nothing downstream will notice unless you read the code.
Do not record provisioning as complete on a non-zero exit.

---

## What you are building

One directory containing four things:

```
/scion-volumes/tools/
├── .venv/               Python venv: the CLI, Hypex deps, and science stack
├── bin/                 external tools plus source-built Hypex commands
├── env.sh               the file agents source — GENERATED, do not edit
└── env-manifest.txt     what ENV_VERSION is the hash of
```

The normal case is **one environment on a shared volume that several
agents run out of**, not a copy each. `ENV_VERSION` is the sha256 of
`env-manifest.txt` — interpreter, every installed package, and every
file in `bin/` with its own digest — and it is stamped into the
provenance sidecar of every artifact any agent produces. One environment
means one `ENV_VERSION` means artifacts that are comparable to each
other.

This is also why **specialists never install packages**. A package added
to the shared venv for one agent changes the `ENV_VERSION` recorded by
every other agent, and partitions the program's artifacts into
before-and-after sets that cannot be directly compared. Provisioning is
the tooling lead's job. If you are bootstrapping, you are doing that job
for the duration.

---

## Prerequisites in your own container

`bootstrap-preflight.sh` checks all of these and prints the exact
`apt-get` line for whatever is absent. The table is here so you can see
*why* each one is needed — a list of package names tells you what to
install and not what to do when you cannot.

Names are Debian 12 (bookworm), which is what this has been run on.

| Need | Debian package | Why | Without it |
|---|---|---|---|
| `python3` ≥ 3.10 | `python3` | The CLI uses PEP 604 annotations at runtime. Tested only on 3.11.2. | Nothing runs |
| working `python3 -m venv` | `python3-venv` | venv creation | `install.sh` fails on its first command |
| `Python.h` | `python3-dev` | `prody` publishes no CPython 3.11 wheel and compiles C extensions | Science stack fails; **CLI still works**; exit 3 |
| `curl` | `curl` | vina release, fpocket source tarball | No binaries |
| CA certificates | `ca-certificates` | every download is https | Every download fails, as a TLS error rather than a missing-package error |
| `tar`, `sha256sum`, `install` | `tar`, `coreutils` | unpack, verify the pin, place the binary | fpocket cannot be built or verified |
| `gcc`, `g++`, `make` | `build-essential` | **fpocket is compiled here, not downloaded** | No fpocket; exit 4 |
| `libc.a` | `libc6-dev` | fpocket is linked `-static` | Build succeeds, install **refuses** |
| `libstdc++.a` | `libstdc++-N-dev` | vendored molfile plugin is C++ | Static link fails |
| `ldd` | `libc-bin` | verifying the link really is static | Cannot verify; install refuses |
| `git` | `git` | provenance, not construction | Every artifact records an environment nobody can reproduce |
| Go 1.26.1+ | deployment image | build vendored `hypex` and `elo` source | Hypex unavailable; exit 4 |
| `obabel` *(optional)* | `openbabel` | format conversion for chemistry skills | A warning; exit stays 0 |

On a Debian container with none of it:

```bash
sudo apt-get update && sudo apt-get install -y \
    python3 python3-venv python3-dev \
    build-essential libc6-dev libstdc++-12-dev libc-bin \
    curl ca-certificates tar coreutils git
```

`libstdc++-12-dev` tracks the gcc major version. On a different base
image it is a different number — run the preflight and use the name it
derives rather than pasting this one.

### Three that are easy to miss

**`libc6-dev` and `libstdc++-N-dev`.** `gcc` and `make` being present
does not mean a static link will work. Those `.a` archives live in
separate `-dev` packages, and a container can compile perfectly well
without them. `install.sh` links fpocket `-static` on purpose — a
dynamically linked fpocket runs in the container that built it and fails
in every other one, surfacing as a missing `.so` at the moment a
specialist needs an answer. The install step checks with `ldd` and
**refuses to install a dynamically linked binary**, so the symptom is a
build that appears to succeed followed by an install that declines.

**`ca-certificates`.** Minimal images ship `curl` without a CA store.
Every https URL then fails with a certificate error that reads like a
network problem.

**`git`.** Not needed to build anything. Needed for the environment to
be *accountable*: `dde env stamp` records the commit the environment
was provisioned from, and `dde doctor` checks that commit is
reachable from a remote. Without git, everything installs, everything
runs, and every artifact carries an environment that cannot be
reproduced by anyone.

### Fail-stop precedence: when to stop, when to fix and continue

**Rule: fail-stop applies to conditions the agent _cannot_ remediate,
not to conditions with a documented remedy in hand.**

The preflight script distinguishes three cases:

1. **Missing packages + root or passwordless sudo (Debian-family):**
   The preflight auto-remediates — it runs `apt-get install` for the
   missing packages and re-runs itself (`--no-remediate`) to verify.
   This is the default behavior (`--remediate` flag, on by default).
   The agent continues if the re-check passes.

2. **Missing packages + no sudo / no root:**
   This is a **blocked task**. The preflight prints the exact package
   list and exits 1. Report it and name the packages. Do not work
   around it.

3. **Re-check still fails after remediation:**
   Also blocked. The auto-install ran but something is still wrong —
   possibly a non-package issue (architecture, network, disk). The
   preflight's output names what is still missing.

The available workarounds — build fpocket dynamically, skip the
science stack quietly, fetch a wheel from elsewhere — each produce an
environment that is not the one its `ENV_VERSION` describes, and the
`ENV_VERSION` is the only claim anyone downstream can check. A missing
tool is a blocked task; a misrepresented environment is bad data with
provenance attached.

The same precedence rule applies to every other fail-stop step in
this bootstrap:

| Step | Remediable? | Action |
|---|---|---|
| Missing OS packages | Yes, with sudo | Auto-remediate, re-check |
| Wrong architecture (not x86_64) | No | Blocked — report it |
| Network unreachable (pypi, github) | No (from inside the container) | Blocked — report it |
| Insufficient disk | No (from inside the container) | Blocked — report it |
| Python too old (< 3.10) | Sometimes (if the right version is available to install) | Blocked unless another Python can be installed |
| `install.sh` exits 3 or 4 | Depends on cause | Re-run preflight, fix what it finds, retry |

To suppress auto-remediation (detect-only mode):

```bash
./bootstrap-preflight.sh --no-remediate
```

---

## Running the install

```bash
cd tools
DDE_VENV=/scion-volumes/tools/.venv \
DDE_BIN=/scion-volumes/tools/bin \
DDE_TOOLS_HOME=/scion-volumes/tools \
  ./install.sh
```

Omit all three and everything lands under `tools/` for a local checkout.

Expect 10–20 minutes; most of it is pip resolving the science stack, and
a few minutes compiling fpocket. Needs ~2.5 GB free.

Useful flags:

| flag | for |
|---|---|
| `--update` | existing venv; skip creation, refresh packages and binaries |
| `--core-only` | CLI and Go tools only; skip Hypex Python deps, `prox`, and the science stack |
| `--binaries-only` | `bin/` and the stamp; touch no Python package |

**Re-running is free and does not churn `ENV_VERSION`.** That is a
property worth relying on: an environment you cannot re-provision
without changing its identity is one nobody will re-provision.

### fpocket is compiled, and what it verifies

There is no upstream binary release, so `install.sh` builds
fpocket 4.2.2 from a pinned source tarball. The build is not the
interesting part — the four things it refuses on are:

1. **Checksum.** The tarball's sha256 is compared to a pin. A pinned URL
   without a checksum records a version number and guarantees nothing
   about the bytes.
2. **Static.** `ldd` must report "not a dynamic executable". If the link
   went dynamic anyway, the binary works here and breaks elsewhere.
3. **Correct.** It runs upstream's own reference input (1UYD) and
   diffs against upstream's reference output. Volume is excluded —
   fpocket estimates pocket volume by Monte Carlo seeded from the clock,
   so it moves between runs, and upstream's own test suite excludes it.
4. **Present.** If the binary is missing at the end, `install.sh`
   exits 4.

Any of these failing leaves the binary uninstalled and the script
exiting non-zero, and the environment is still stamped for what it
actually is. A stamp that waits for a perfect install is a stamp that is
absent exactly when artifacts are being produced by a partial one.

### Hypex is vendored and built during provisioning

DDE owns the Hypex deployment source under `tools/vendor/hypex/`. The
bootstrapper does not clone the standalone Hypex repository and no binary is
committed to this repository. `install.sh` uses the deployment's Go 1.26.1+
toolchain to build `hypex` and `elo` from source (dependencies resolved via
`go mod` at build time), copies the JSON schemas into `share/hypex/schemas`,
and installs the vendored `prox` Python source with a launcher in `bin/`.

`prox` depends on scikit-learn, scipy, NumPy, NetworkX, and Click. They are
installed from `requirements-hypex.txt` in a transaction separate from the
larger science stack. An unrelated source-build failure in that stack cannot
roll back `prox`. `--core-only` deliberately skips `prox` and does not provide
the Hypex strategy; the normal bootstrapper path performs a full install.
`--binaries-only` can refresh the Hypex tools only after a full environment
already contains those dependencies.

Provisioning smoke-tests all three commands with `--help`. `dde doctor`
repeats those executable checks and reports the Hypex strategy as available
only when the complete toolchain is runnable.

---

## Activating it

```bash
source /scion-volumes/tools/env.sh
```

**Source `env.sh`, not the venv's `activate`.** `env.sh` sets three
things `activate` does not:

- `bin/` on `PATH`, so `fpocket`, `vina`, `hypex`, `elo`, and `prox` resolve — and resolve to the
  binaries that are hashed into `ENV_VERSION`, rather than to something
  else on the system that provenance cannot account for.
- `DDE_TOOLS_HOME`, so the CLI reads *this* environment's stamp
  rather than a compiled-in default.
- `PYTHONDONTWRITEBYTECODE=1`, because agents share one `__pycache__` on
  a shared volume, and an agent has already been served stale bytecode
  from before a commit it had pulled.

Activating the venv alone gives you a working `dde` and a silently
degraded environment: missing binaries, and `unpinned-dev` written into
the provenance of every artifact you produce.

`env.sh` is **generated by `install.sh`**. Edit the script, not the
file; the next provisioning run overwrites it. It was hand-maintained
once and had drifted — `bin/` was never on `PATH`, so `dde doctor`
reported fpocket present and unreachable in the same line.

Then point it at somewhere to write:

```bash
dde init ~/my-program
export DDE_PROJECT=~/my-program
```

The CLI refuses to guess a project from the working directory, so an
unset `DDE_PROJECT` gives you a clear error rather than files in a
surprising place.

---

## Verifying

```bash
dde doctor; echo "exit=$?"
```

**Read the exit code, not the warning count.** Warnings are expected on
a healthy install — `doctor` groups them and tells you what each group
does and does not affect, and ends in a `PROCEED` or `STOP` verdict.
Judge by the verdict.

`doctor` is what turns a missing tool into a blocked task instead of an
invented number. It also fails when the environment has drifted from its
own stamp, or was provisioned from a commit nobody else can fetch.

Then confirm the environment is identified and the tools resolve:

```bash
dde env show          # the ENV_VERSION and whether it still matches
fpocket -h 2>&1 | head -1
vina --version
```

Optionally run the repository's gates, which check the tree rather than
the environment:

```bash
cd /workspace/tools
for g in check_invocations check_artifact_paths check_skill_uris check_threshold_names; do
    python3 $g.py >/dev/null 2>&1; echo "$g exit=$?"
done
```

Exit 0 is clean, 1 is a real finding, and **2 means the checker could
not run** — which is not the same as clean, and is the answer you get if
the environment is not activated.

---

## Changing the environment afterwards

An environment change partitions the program's artifacts: results either
side of it carry different `ENV_VERSION` values and are not directly
comparable. **Price the change before making it.**

```bash
dde env plan     # what ENV_VERSION would become, and what differs
dde env show     # what it is now, and whether it still matches
dde env diff <before> [after]    # explain a partition after the fact
```

`dde env plan` reporting "unchanged" means the change is free. That
is worth checking first every time, because roughly half of them are.

---

## When something fails

| symptom | cause | do |
|---|---|---|
| `install.sh` exits 3 | science stack did not build | Usually `python3-dev` + `build-essential`. The CLI works meanwhile. |
| `install.sh` exits 4 | a declared binary is missing | Read the warnings above it: no toolchain, no network, or the static link failed. `--binaries-only` retries without touching pip. |
| fpocket built, "refusing to install" | linked dynamically | Missing `libc6-dev` / `libstdc++-N-dev` |
| fpocket "does not match upstream's reference" | build produced wrong answers | Do not install it. Report it. This is the check working. |
| `dde: command not found` after install | sourced `activate`, not `env.sh` | `source <tools-home>/env.sh` |
| every artifact says `unpinned-dev` | `DDE_TOOLS_HOME` unset, or no stamp | Same fix. Check `dde env show`. |
| `doctor` says fpocket present and unreachable | `bin/` not on `PATH` | Same fix. |
| a checker exits 2 | it could not run | Not clean. Activate the environment and re-run. |

If the answer is not here, `dde doctor` names what is wrong more
precisely than this table can, and it is designed to be read by whoever
is about to rely on the environment rather than by whoever built it.

---

## Notes for whoever automates this

Preston has floated a `tool-bootstrapper` role template. Until that
exists, this document is the procedure. Two things worth carrying into
it:

- **The preflight and the install are separate on purpose.** `install.sh`
  is a long operation that fails in the middle, and its failures are
  informative only if you are watching. The preflight is cheap, needs no
  privilege, writes nothing, and answers "can this container do the job"
  before anyone has waited fifteen minutes to find out.
- **Every non-zero exit here means something specific**, and the useful
  distinction is 1 versus 2: "I checked and it is wrong" versus "I could
  not check". An automated bootstrapper that collapses those will report
  an unexamined container as a broken one, or worse, the reverse.
