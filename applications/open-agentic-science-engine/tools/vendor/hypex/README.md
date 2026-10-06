# Vendored Hypex Sources

This directory contains the complete deterministic Hypex runtime used by DDE,
including all build dependencies, from
`scion-frontiers/hypex` at upstream revision
`22316b2db118ab3f3f175a05faa75d74c42a698c`. The upstream
`hypothesis-explorer/` layout is preserved so its own tests and schema paths
remain valid:

- `hypothesis-explorer/tools/hypex/`: datastore lifecycle and schema validation CLI (Go)
- `hypothesis-explorer/tools/elo/`: tournament pairing and rating CLI (Go)
- `hypothesis-explorer/tools/prox/`: proximity and clustering CLI (Python)
- `hypothesis-explorer/schemas/`: datastore JSON schemas required by `hypex`

`tools/install.sh` builds the Go commands and installs the Python source into
the deployment tools volume. Generated binaries, Python environments, and
runtime data must not be committed here.

Go module dependencies are fetched at build time via `go mod download`;
`go.mod` and `go.sum` in each tool directory pin the exact dependency versions
and checksums. Network access is already required during provisioning.

The DDE patch level is `dde.2`: the small change in
`hypothesis-explorer/tools/hypex/cmd/root.go` makes the schema default resolve
from the DDE tools installation while retaining the upstream source-tree
fallback. The DDE-installed `prox` launcher calls the upstream Click entry
point directly because `prox.cli` is not an executable Python module.
Literature tooling is intentionally not vendored: that capability is owned by
the `dde` CLI and DDE's literature skills.
