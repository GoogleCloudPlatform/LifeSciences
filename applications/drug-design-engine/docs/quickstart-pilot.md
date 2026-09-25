# Pilot Bootstrapping Quickstart

Start a dde program from a blank environment. One user action; everything else
is automated by the controller.

---

## 1. Prerequisites

Before starting, confirm:

| Requirement | Check |
|---|---|
| Repo cloned | `ls /workspace/tools/install.sh` |
| Scion CLI available | `scion version` |
| Hub configured and broker running | `scion list` returns without error |
| Docker / container runtime | Required by scion for agent containers |
| Python 3 with `venv` module | `python3 -m venv --help` |

### Environment variables

| Variable | Purpose | Notes |
|---|---|---|
| `DDE_VENV` | Override venv location | Default: `tools/.venv` |
| `DDE_TOOLS_HOME` | Where the env stamp and `env.sh` land | Default: `/scion-volumes/tools` |
| `GOOGLE_APPLICATION_CREDENTIALS` | AlphaFold 3 (Vertex) access | Required for AF3 predictions |
| `ALPHAGENOME_API_KEY` | AlphaGenome access | Required for AlphaGenome skills |

> **Note:** The volume path `/scion-volumes/tools/` is an environment
> default. Your deployment may use a different path — set `DDE_TOOLS_HOME`
> to override. See [environment-specific notes](#7-environment-specific-notes).

---

## 2. Starting a program run

Start the Research Operations Controller with a program directive:

```bash
scion start controller --type research-operations-controller "Read and follow <brief path>"
```

Or pass the directive inline:

```bash
scion start controller --type research-operations-controller \
  "Program directive: Investigate <target> as a therapeutic target for <indication>. \
   Modality: small molecule. Program directory: /workspace/<program-name>"
```

**This is the only step you perform.** The controller handles everything else:
environment setup, verification, template sync, program directory creation, and
starting the Science Program Lead.

### What a program directive includes

- Scientific objective (target, indication, hypothesis)
- Modality (small molecule, biologic, etc.)
- Program directory path
- Any configuration overrides or resource constraints

---

## 3. What the controller does (automated)

You do not need to run these steps — the controller executes them automatically
(ROC template §0). Understanding the sequence helps with troubleshooting.

### 3a. Environment setup

```bash
cd /workspace/tools && ./install.sh        # fresh install
cd /workspace/tools && ./install.sh --update  # update existing
```

Installs the Python venv, pip dependencies, and non-pip binaries (AutoDock Vina,
fpocket). Then activates via:

```bash
source /scion-volumes/tools/env.sh
```

> `env.sh` — not the venv's `activate` directly. `env.sh` also adds provisioned
> binaries to `PATH` and sets `DDE_TOOLS_HOME`.

### 3b. Doctor verification

```bash
dde doctor --json
```

Checks that all tools, credentials, and dependencies are present. Any `"fail"`
check stops bootstrap. Capability warnings (`"warn"`) are recorded for the
session but do not block startup.

### 3c. Template sync

```bash
scion template sync --all
```

Pushes all agent templates to the hub. Must complete before starting any agent.

### 3d. Program directory

```bash
dde init <directory>
```

Creates the program root with `.dde/` marker and `raw/` tree. If continuing
from a prior phase, the controller detects the existing `.dde/` and skips
init.

### 3e. Start the Science Program Lead

The controller writes a brief containing the scientific objective, doctor
findings, and capability limitations, then starts the science lead:

```bash
scion start science-program-lead --type science-program-lead "Read and follow <brief path>"
```

After the science lead acknowledges the brief, bootstrap is complete and normal
operations begin.

---

## 4. Verifying the program is running

### Check agent status

```bash
scion list
```

After bootstrap completes, expect:

| Agent | Status | Role |
|---|---|---|
| `controller` | running | Research Operations Controller |
| `science-program-lead` | running | Science Program Lead |

### Check environment health

```bash
source /scion-volumes/tools/env.sh
dde doctor
```

- **`STOP`** verdict: a prerequisite is broken — fix before proceeding.
- **`PROCEED`** verdict: environment is healthy. Warnings are expected and are
  not failures.

### Check program state

```bash
ls -la <program-directory>/.dde/
```

The `.dde/` directory should contain:

```
.dde/
├── thresholds.yaml
├── program.yaml
└── control/
    ├── work-orders/
    ├── contexts/
    ├── runs/
    ├── validations/
    ├── leases/
    ├── events.ndjson
    └── publish-state.json
```

---

## 5. Providing pre-existing data

### Continuing from a prior phase

If continuing from a prior phase, the existing program directory is the source of
truth. Point the controller at it — do not re-run `dde init`. The controller
detects the existing `.dde/` marker and preserves the control state.

### External data (e.g., co-scientist results)

Place external data under the program's `raw/` tree before or during the program
run:

```
<program-directory>/raw/
├── structures/       # PDB files, AlphaFold predictions
├── docking/          # docking results
├── assays/           # screening data, dose-response
├── compounds/        # computed molecular descriptors
├── literature/       # literature references
└── hypotheses/       # co-scientist exports, adopted hypothesis sets
```

For co-scientist tournament exports specifically:

1. Place the export under `raw/hypotheses/` or the location specified in the
   program directive.
2. Include this path in the controller's program directive so the science lead
   knows the data exists.
3. Note the export scope — partial exports (e.g., 5 of 72 ideas) must be
   flagged so downstream analysis accounts for the denominator.

---

## 6. Troubleshooting

### Controller stops during bootstrap

```bash
scion list                    # check controller status
scion output controller       # check last output for which step failed
```

The controller stops at the first failing step and reports the failure and remedy.
Check the output against the bootstrap sequence (§3 above) to identify which step
failed.

### install.sh failure

```bash
cd /workspace/tools && ./install.sh
```

Common causes:
- Missing build tools: `apt-get install -y build-essential python3-dev`
- Network access required for binary downloads (Vina, fpocket)
- Exit code 3: science stack failed (CLI still works; specialist skills affected)
- Exit code 4: a binary did not install (e.g., fpocket build failed)

### Doctor FAIL

```bash
dde doctor --json
```

Each failed check includes a `remedy` field. Common issues:

| Check | Remedy |
|---|---|
| Missing binary (fpocket, vina) | Re-run `install.sh` or install system package |
| Missing credential (GOOGLE_APPLICATION_CREDENTIALS) | Set the environment variable |
| Missing Python package (rdkit, biopython) | Re-run `install.sh`; check build toolchain |

### Template sync failure

```bash
scion template sync --all
```

Verify hub connectivity. If the hub is unreachable, check `scion` configuration
and network access.

### Science lead doesn't start

Check the controller's last action:

```bash
scion list                    # is the controller still running?
scion output controller       # what was the last step it completed?
```

If the controller is blocked, it is waiting for the science lead to acknowledge
the brief. If the controller has stopped, check its output for the failure point.

---

## 7. Environment-specific notes

### Volume paths

The default tools home is `/scion-volumes/tools/`. This path is set by:

1. The `DDE_TOOLS_HOME` environment variable (highest precedence)
2. The default compiled into `install.sh`

If your deployment uses a different volume layout, set `DDE_TOOLS_HOME` before
starting the controller, or ensure the controller's environment includes it.

### Venv location

The venv defaults to `tools/.venv` but can be placed on a shared volume via
`DDE_VENV` so multiple agents use one environment without each building their
own copy.

### Pilot processes

For retrospective cadence, friction logging, and documentation procedures during
a pilot deployment, see [`docs/pilot-handoff.md`](pilot-handoff.md).

### Container requirements

Agents run in containers managed by scion. Ensure:
- The container runtime is available
- Shared volumes are mounted at the expected paths
- Network access is available for template sync, binary downloads, and any
  external API calls (AlphaFold, AlphaGenome)

---

## Reference

- ROC template (full bootstrap sequence): `templates/research-operations-controller/agents.md` §0
- Science Program Lead template: `templates/science-program-lead/agents.md`
- Orchestration design: `docs/orchestration-design-guidance.md` §2–§5
- Install script flags: `tools/install.sh --help`
- Pilot handoff notes: `docs/pilot-handoff.md`
