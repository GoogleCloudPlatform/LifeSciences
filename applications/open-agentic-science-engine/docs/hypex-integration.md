# Hypex Integration

Hypex is a DDE Stage 0 execution subgraph, not a separately deployed
application. DDE vendors its deterministic implementation and owns the agent
architecture, provisioning, literature access, artifact publication, and
work-order boundary.

## Ownership Map

| Reference Hypex component | DDE owner |
|---|---|
| `hypex` Go CLI | `tools/vendor/hypex`; built by `tools/install.sh` |
| `elo` Go CLI | `tools/vendor/hypex`; built by `tools/install.sh` |
| `prox` Python CLI | `tools/vendor/hypex`; installed by `tools/install.sh` |
| JSON schemas | Installed to `${DDE_TOOLS_HOME}/share/hypex/schemas` |
| standalone `lit` CLI | Replaced by `dde pubmed`, `dde preprint`, `dde litref`, and `dde cite` |
| reusable protocols | DDE skills under `skills/` |
| agent roles | DDE templates under `templates/hypex-*` |
| external entry point | DDE work order for `hypex-supervisor` |
| final export | `dde hypex ingest`, then `dde hypex analyze` |

The upstream source revision is recorded in `tools/vendor/hypex/README.md`.
Go module dependencies are vendored too, so deployment builds do not fetch Go
source. Compiled binaries and Python environments are deployment artifacts and
must not be committed.

## Provisioning

The bootstrapper runs `tools/bootstrap-preflight.sh`, then `tools/install.sh`.
The preflight requires the Go version declared by the vendored modules. A full
install builds `hypex` and `elo`, installs `prox`, copies the schemas, writes
source-revision markers, and includes each executable in DDE's environment
stamp. `dde doctor --json` reports the Hypex strategy available only when all
three tools are present and runnable.

`requirements-hypex.txt` is installed in its own transaction before the larger
science stack. An unrelated science-package build failure therefore cannot
roll back `prox` and disable Hypex. `--core-only` builds the Go commands but
skips `prox` and its numerical dependencies, so it does not claim a working
Hypex strategy. `--binaries-only` can repair the tools after a full Python
environment already exists.

## Orchestration Boundary

The science lead commits one work order with:

```yaml
requested_role: hypex-supervisor
resource_class: hypex-supervisor
capabilities:
  - hypothesis-exploration
  - tournament-orchestration
```

The controller admits that work only when the Hypex doctor checks pass and
holds one program-wide supervisor lease. The supervisor then launches the DDE
generation, reflection, proximity, tournament, evolution, and meta-review
templates. Those roles are internal to the subgraph and are not independent
DDE work-order targets.

The native run remains append-only on the shared execution volume. At the end,
the supervisor writes termination metadata before ingesting the run. This lets
DDE derive the correct completion state, archive the native corpus, normalize
it as `dde.hypex.v1`, and emit a `dde.hypothesis-assessment.v1` analysis for the
science lead.
