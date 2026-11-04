# Boltz-2 Dependency Management

1.  Direct dependencies are defined in [`requirements.in`](./requirements.in) (without hashes), and security/compatibility overrides for upstream exact pins in `boltz==2.2.1` are defined in [`overrides.txt`](./overrides.txt).
2.  A locked [`requirements.lock`](./requirements.lock) containing SHA-256 hashes is compiled targeting Python 3.12 on Ubuntu 24.04 (`x86_64-manylinux_2_39`).
3.  The [`Dockerfile`](./Dockerfile) installs dependencies using standard `pip` with strict hash verification (`pip3 install --require-hashes --no-deps`), including `kfp` so Vertex AI Pipeline tasks can run with `install_kfp_package=False` in air-gapped Shared VPC environments.

---

## How to Upgrade Packages & Regenerate Hashes

When you need to upgrade packages or add new dependencies, follow these steps to securely regenerate `requirements.lock`.

### Step 1: Update `requirements.in` or `overrides.txt`
Modify [`requirements.in`](./requirements.in) to change direct version pins or [`overrides.txt`](./overrides.txt) to bump transitive security floors.
*Note: Do not add `--extra-index-url` to `requirements.in` to avoid index priority confusion during compilation.*

### Step 2: Delete the old `requirements.lock` [CRITICAL]
> [!WARNING]
> You **MUST** delete the existing `requirements.lock` file before compiling. If the file exists, `uv` will attempt to reuse hashes from it to speed up resolution.

Run from the workspace root:
```bash
rm -f applications/foldrun/src/boltz2-components/requirements.lock
```

### Step 3: Compile Hashes (Targeting Python 3.12 & Ubuntu 24.04 / Glibc 2.39)
To ensure the generated hashes match the `nvidia/cuda:12.6.3-runtime-ubuntu24.04` runtime container (Python 3.12, `glibc 2.39`) and pull CUDA 12.6 PyTorch wheels (`+cu126`), compile inside a Python 3.12 container:

```bash
docker run --rm -v "$(pwd):/workspace" -w /workspace ghcr.io/astral-sh/uv:0.13.5-python3.12-bookworm-slim \
  uv pip compile applications/foldrun/src/boltz2-components/requirements.in \
    --override applications/foldrun/src/boltz2-components/overrides.txt \
    --python-version 3.12 \
    --python-platform x86_64-manylinux_2_39 \
    --index-url https://pypi.org/simple \
    --extra-index-url https://download.pytorch.org/whl/cu126 \
    --index-strategy unsafe-best-match \
    --generate-hashes \
    --output-file applications/foldrun/src/boltz2-components/requirements.lock
```

### Step 4: Commit Changes
Commit `requirements.in`, `overrides.txt`, and the newly generated `requirements.lock` to the repository.

---

## How the Dockerfile Uses It

The [`Dockerfile`](./Dockerfile) copies `requirements.lock` and runs:

```dockerfile
COPY requirements.lock .
RUN pip3 install --no-cache-dir --require-hashes --no-deps \
    --extra-index-url https://download.pytorch.org/whl/cu126 \
    -r requirements.lock
```

> [!IMPORTANT]
> `--extra-index-url https://download.pytorch.org/whl/cu126` is required at install time so `pip` can download the `+cu126` PyTorch wheels referenced and hash-pinned in `requirements.lock`.
