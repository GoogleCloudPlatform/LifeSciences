---
name: hypex-tool-setup
description: Activate and verify the DDE-provisioned Hypex tools before running a hypothesis-exploration workflow.
---

# Hypex Tool Setup in DDE

The DDE bootstrapper provisions `hypex`, `elo`, and `prox` from the source
vendored in `applications/DDE/tools/vendor/hypex`. Literature access is part
of the `dde` CLI; there is no separate `lit` executable in DDE.

## Activate

Source the generated environment file, not the venv activation script:

```bash
source /scion-volumes/tools/env.sh
```

This activates the DDE venv, adds `/scion-volumes/tools/bin` to `PATH`, sets
`DDE_TOOLS_HOME`, and selects the provisioned environment stamp.

## Verify

```bash
dde doctor --json
hypex --help
elo --help
prox --help
```

`dde doctor` must report `binary hypex`, `binary elo`, `binary prox`, and
`hypothesis strategy: hypex` as `ok`. A missing or non-runnable command is a
capability failure. Report it to the supervisor; do not build tools ad hoc in
a worker container or install packages into the shared venv.

## Tool Inventory

| Tool | Provisioning | Purpose |
|---|---|---|
| `dde` | DDE Python package | Literature, citations, Hypex ingest/analyze, and DDE artifacts |
| `hypex` | Vendored Go source | Hypothesis datastore lifecycle and schema validation |
| `elo` | Vendored Go source | Tournament pairings, ratings, and standings |
| `prox` | Vendored Python source | Similarity, clustering, and near-duplicate detection |

The bootstrapper is the only owner of provisioning. Repair a missing tool by
re-running `applications/DDE/tools/install.sh`; `--binaries-only` is suitable
when the full Python environment, including prox dependencies, already exists.
