# OpenFold3 Dependency Management

1.  The base image `openfoldconsortium/openfold3:0.5-pixi` manages its CUDA 12 runtime (`openfold3-cuda12`) with [Pixi](https://pixi.sh).
2.  [`pixi.toml`](./pixi.toml) and [`pixi.lock`](./pixi.lock) pin every Conda and PyPI artifact in `openfold3-cuda12` (`linux-64-cuda12`) by URL and SHA-256 digest, applied deterministically at build time via `pixi install --locked`.
3.  [`requirements.lock`](./requirements.lock) exports the exact frozen Python package versions and SHA-256 digests from `pixi.lock` so `osv-scanner` can continuously audit `openfold3-components` in CI (`security/cloudbuild-security.yaml`).
4.  [`verify_lockfile_sync.py`](./verify_lockfile_sync.py) runs during `docker build` to assert `python3 -m pip list --format=json` matches `requirements.lock` 1:1.

---

## How to Upgrade Packages & Regenerate Lockfiles

1. Edit version constraints in [`pixi.toml`](./pixi.toml).
2. Re-solve `pixi.lock`, install `openfold3-cuda12`, and regenerate `requirements.lock` inside the pinned upstream base container:

```bash
docker run --rm \
  -v "$(pwd)/applications/foldrun/src/openfold3-components:/work" \
  openfoldconsortium/openfold3:0.5-pixi@sha256:8764f7320ff12c52e9b9ec6bb0047f71df0ab197d401d04a78a51f48e9913335 \
  bash -euo pipefail -c '
    cp /work/pixi.toml /opt/pixi-project/pixi.toml
    rm -f /opt/pixi-project/pixi.lock
    CONDA_OVERRIDE_CUDA=12.0 /root/.pixi/bin/pixi lock --manifest-path /opt/pixi-project/pixi.toml
    CONDA_OVERRIDE_CUDA=12.0 /root/.pixi/bin/pixi install --locked --manifest-path /opt/pixi-project/pixi.toml -e openfold3-cuda12
    cp /opt/pixi-project/pixi.lock /work/pixi.lock
  '
```

3. Export `requirements.lock` and verify `docker build` passes [`verify_lockfile_sync.py`](./verify_lockfile_sync.py) and `osv-scanner` reports `0` issues.
