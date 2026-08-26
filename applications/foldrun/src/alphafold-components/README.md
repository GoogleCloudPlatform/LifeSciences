# AlphaFold Dependency Management

1.  Dependencies are defined in `requirements.in` (without hashes).
2.  A secure, locked `requirements.lock` containing hashes is generated on the host/CI.
3.  The `Dockerfile` installs dependencies using standard `pip` but enforces strict hash verification via `pip install --require-hashes`.

---

## How to Upgrade Packages & Regenerate Hashes

When you need to upgrade packages or add new dependencies, follow these steps to securely regenerate `requirements.lock`.

### Step 1: Update `requirements.in`
Modify [requirements.in](./requirements.in) to change version pins or add new packages.
*   **Glibc Compatibility Note**: If you add or upgrade packages that contain binary wheels (like `openmm-cuda-12`), you may need to explicitly pin them to a version compatible with the container's base OS. The AlphaFold container runs on **Ubuntu 20.04 (glibc 2.31)**. Newer versions (like `openmm-cuda-12==8.5.2`) might require glibc 2.34 (manylinux_2_34) and will fail to install. We have pinned `openmm-cuda-12==8.2.0` which uses compatible `manylinux_2_28` wheels.

### Step 2: Delete the old `requirements.lock` [CRITICAL]
> [!WARNING]
> You **MUST** delete the existing `requirements.lock` file before compiling. If the file exists, `uv` will attempt to reuse the hashes from it to speed up resolution. If you are upgrading a package, this "lazy" behavior can result in `uv` carrying over stale hashes from the old version, causing immediate build failures.

Run from the workspace root:
```bash
rm -f applications/foldrun/src/alphafold-components/requirements.lock
```

### Step 3: Compile Hashes (Targeting Python 3.11 & Ubuntu 20.04/Glibc 2.31)
To ensure the generated hashes are compatible with the target runtime container (which runs Python 3.11 on Ubuntu 20.04), we must compile them in a matching Python 3.11 environment and explicitly constrain the platform to `manylinux_2_31`.

Run this command from the **workspace root** (it spins up a temporary Python 3.11 container to run the compilation):
```bash
docker run --rm -v $(pwd):/workspace -w /workspace python:3.11-slim bash -c \
  "pip install uv==0.8.15 && uv pip compile applications/foldrun/src/alphafold-components/requirements.in \
   --index-url https://pypi.org/simple \
   --index-strategy unsafe-best-match \
   --generate-hashes \
   --python-platform x86_64-manylinux_2_31 \
   --output-file applications/foldrun/src/alphafold-components/requirements.lock"
```

### Step 4: Commit Changes
Commit both `requirements.in` and the newly generated `requirements.lock` to the repository.

---

## How the Dockerfile Uses It

The [Dockerfile](./Dockerfile) copies the generated `requirements.lock` and runs:

```dockerfile
COPY requirements.lock /app/alphafold/requirements_resolved.txt
RUN pip3 install --no-cache-dir --require-hashes \
    -f https://storage.googleapis.com/jax-releases/jax_cuda_releases.html \
    -r /app/alphafold/requirements_resolved.txt
```

> [!IMPORTANT]
> We must include the `-f https://storage.googleapis.com/jax-releases/jax_cuda_releases.html` flag in the Dockerfile's `pip install` command so `pip` knows where to find the custom JAX CUDA wheels at install time, even though they are already resolved and hashed in `requirements.lock`.
