# ESMFold2 & ESMFold2-Fast Dependency Management

1. Direct dependencies are defined in [`requirements.in`](./requirements.in) (without hashes), and security/compatibility overrides for upstream exact pins in `esm==3.4.2` are defined in [`overrides.txt`](./overrides.txt).
2. A locked [`requirements.lock`](./requirements.lock) containing SHA-256 hashes is compiled targeting Python 3.12 on Ubuntu 24.04 (`x86_64-manylinux_2_39`).
3. The [`Dockerfile`](./Dockerfile) installs dependencies using standard `pip` with strict hash verification (`pip3 install --require-hashes --no-deps`), including `kfp` so Vertex AI Pipeline tasks can run with `install_kfp_package=False` in air-gapped Shared VPC environments.

---

## How to Upgrade Packages & Regenerate Hashes

```bash
rm -f applications/foldrun/src/esmfold2-components/requirements.lock
uv pip compile applications/foldrun/src/esmfold2-components/requirements.in \
  --override applications/foldrun/src/esmfold2-components/overrides.txt \
  --python-version 3.12 \
  --python-platform x86_64-manylinux_2_39 \
  --default-index https://pypi.org/simple \
  --extra-index-url https://download.pytorch.org/whl/cu126 \
  --index-strategy unsafe-best-match \
  --generate-hashes \
  --output-file applications/foldrun/src/esmfold2-components/requirements.lock
```
