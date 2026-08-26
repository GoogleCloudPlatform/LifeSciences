# Boltz-2 Dependency Management

1.  Dependencies are defined in `requirements.in` (without hashes).
2.  A secure, locked `requirements.lock` containing hashes is generated on the host/CI.
3.  The `Dockerfile` installs dependencies using standard `pip` but enforces strict hash verification via `pip install --require-hashes`.

---

## How to Upgrade Packages & Regenerate Hashes

When you need to upgrade packages or add new dependencies, follow these steps to securely regenerate `requirements.lock`.

### Step 1: Update `requirements.in`
Modify [requirements.in](./requirements.in) to change version pins or add new packages. 
*Note: Do not add `--extra-index-url` to this file to avoid index priority confusion during compilation.*

### Step 2: Delete the old `requirements.lock` [CRITICAL]
> [!WARNING]
> You **MUST** delete the existing `requirements.lock` file before compiling. If the file exists, `uv` will attempt to reuse the hashes from it to speed up resolution. If you are upgrading a package, this "lazy" behavior can result in `uv` carrying over stale hashes from the old version, causing immediate build failures.

Run from the workspace root:
```bash
rm -f applications/foldrun/src/boltz2-components/requirements.lock
```

### Step 3: Compile Hashes (Targeting Python 3.10)
To ensure the generated hashes are compatible with the target runtime container (which runs Python 3.10), we must compile them in a matching Python 3.10 environment.

Run this command from the **workspace root** (it spins up a temporary Python 3.10 container to run the compilation):
```bash
docker run --rm -v $(pwd):/workspace -w /workspace python:3.10-slim bash -c \
  "pip install uv==0.8.15 && uv pip compile applications/foldrun/src/boltz2-components/requirements.in \
   --index-url https://pypi.org/simple \
   --extra-index-url https://download.pytorch.org/whl/cu121 \
   --index-strategy unsafe-best-match \
   --generate-hashes \
   --output-file applications/foldrun/src/boltz2-components/requirements.lock"
```

### Step 4: Commit Changes
Commit both `requirements.in` and the newly generated `requirements.lock` to the repository.

---

## How the Dockerfile Uses It

The [Dockerfile](./Dockerfile) copies the generated `requirements.lock` and runs:

```dockerfile
COPY requirements.lock .
RUN pip3 install --no-cache-dir --require-hashes \
    --extra-index-url https://download.pytorch.org/whl/cu121 \
    -r requirements.lock
```

> [!IMPORTANT]
> We must include `--extra-index-url https://download.pytorch.org/whl/cu121` in the Dockerfile's `pip install` command so `pip` knows where to find the custom PyTorch CUDA wheels at install time, even though they are already resolved and hashed in `requirements.lock`.
