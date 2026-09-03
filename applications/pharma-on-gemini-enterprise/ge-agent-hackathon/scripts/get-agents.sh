#!/usr/bin/env bash
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Fetch BioCompass and PaperBanana from the public LifeSciences repo and apply
# this kit's fixes. Safe to re-run — it re-fetches cleanly.
set -euo pipefail
REPO="${AGENTS_REPO:-https://github.com/GoogleCloudPlatform/LifeSciences.git}"
BASE="applications/pharma-on-gemini-enterprise"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

echo "==> Fetching agents from $REPO"
git clone --depth=1 --filter=blob:none --sparse "$REPO" "$TMP/src" -q
git -C "$TMP/src" sparse-checkout set \
  "$BASE/biocompass-on-gemini-enterprise" \
  "$BASE/paperbanana-on-gemini-enterprise"
UPSTREAM_SHA=$(git -C "$TMP/src" rev-parse --short HEAD)

for pair in "biocompass-on-gemini-enterprise:biocompass" "paperbanana-on-gemini-enterprise:paperbanana"; do
  SRC="${pair%%:*}"; DST="${pair##*:}"
  echo "==> $DST"
  rm -rf "$HERE/agents/$DST"
  cp -R "$TMP/src/$BASE/$SRC" "$HERE/agents/$DST"
  rm -rf "$HERE/agents/$DST/.git"
  echo "    from $REPO@$UPSTREAM_SHA" > "$HERE/agents/$DST/UPSTREAM.txt"
done

echo "==> Applying this kit's fixes"

# Fix 1 — pin mcp below 2.x wherever an agent depends on it.
# Unpinned, resolution picks mcp 2.x which removed mcp.shared.session; ADK then
# swallows the import error and reports a misleading "cannot import name 'McpToolset'".
for d in "$HERE"/agents/*/; do
  if grep -rqs "mcp_tool\|McpToolset" "$d" --include='*.py' 2>/dev/null; then
    PYPROJECT="$d/pyproject.toml"
    if [ -f "$PYPROJECT" ] && ! grep -q 'mcp>=1.24,<2' "$PYPROJECT"; then
      python3 - "$PYPROJECT" <<'PY'
import re,sys
p=sys.argv[1]; t=open(p).read()
if 'dependencies = [' in t and 'mcp>=1.24,<2' not in t:
    t=t.replace('dependencies = [', 'dependencies = [\n    "mcp>=1.24,<2",',1)
    open(p,'w').write(t); print("    pinned mcp>=1.24,<2 in", p)
PY
    fi
  fi
done

# Fix 2 — the image model ID. gemini-3-pro-image-preview was withdrawn at GA.
for f in $(grep -rl 'gemini-3-pro-image-preview' "$HERE"/agents/*/app "$HERE"/agents/*/*.example 2>/dev/null || true); do
  sed -i.bak 's/gemini-3-pro-image-preview/gemini-3-pro-image/g' "$f" && rm -f "$f.bak"
  echo "    gemini-3-pro-image-preview -> gemini-3-pro-image in ${f#$HERE/}"
done

# Fix 3 — turn on retry/backoff and cap concurrency for every Gemini call.
#
# ADK's Gemini wrapper accepts retry_options, but when a model is passed as a
# plain string it is None -- and google-genai then uses stop_after_attempt(1),
# i.e. NO RETRIES. One transient 429 from dynamic shared quota kills the whole
# invocation, because ADK surfaces it as an ExceptionGroup out of the parallel
# fan-out. On a shared classroom project that happens constantly.
#
# This installs app/model_utils.py (retry + a per-process concurrency cap) and
# rewrites the model assignments to use it. Tunable via LLM_MAX_CONCURRENCY and
# LLM_RETRY_* -- see .env.example.
echo "==> Enabling retry/backoff + concurrency cap"
for d in "$HERE"/agents/*/; do
  [ -d "$d/app" ] || continue
  cp "$HERE/scripts/_model_utils.py.tmpl" "$d/app/model_utils.py"
  python3 - "$d" <<'PYEOF'
import ast, pathlib, sys
root = pathlib.Path(sys.argv[1])
subs = [
  ('_COORDINATOR_MODEL = os.getenv("COORDINATOR_MODEL_NAME", "gemini-3.1-pro-preview")',
   '_COORDINATOR_MODEL = resilient_model(\n    os.getenv("COORDINATOR_MODEL_NAME", "gemini-3.1-pro-preview")\n)'),
  ('_WORKER_MODEL = os.getenv("WORKER_MODEL_NAME", "gemini-3.5-flash")',
   '_WORKER_MODEL = resilient_model(os.getenv("WORKER_MODEL_NAME", "gemini-3.5-flash"))'),
  ('_MODEL = os.getenv("WORKER_MODEL_NAME", "gemini-3.5-flash")',
   '_MODEL = resilient_model(os.getenv("WORKER_MODEL_NAME", "gemini-3.5-flash"))'),
  ('_PLANNER_MODEL = os.getenv("PLANNER_MODEL_NAME", "gemini-3.1-pro-preview")',
   '_PLANNER_MODEL = resilient_model(\n    os.getenv("PLANNER_MODEL_NAME", "gemini-3.1-pro-preview")\n)'),
  ('_IMAGE_MODEL = os.getenv("IMAGE_MODEL_NAME", "gemini-3-pro-image")',
   '_IMAGE_MODEL = resilient_model(os.getenv("IMAGE_MODEL_NAME", "gemini-3-pro-image"))'),
]
for f in sorted(root.glob("app/**/*.py")):
    if f.name == "model_utils.py":
        continue
    src = f.read_text(); hit = False
    if "LlmAgent(" not in src and "Agent(" not in src:
        continue          # not an agent definition -- e.g. a raw genai tool call
    for old, new in subs:
        if old in src:
            src = src.replace(old, new, 1); hit = True
    if not hit:
        continue
    depth = len(f.relative_to(root / "app").parts) - 1
    imp = "from " + "." * (depth + 1) + "model_utils import resilient_model"
    lines = [l for l in src.split("\n") if l.strip() != imp]
    tree = ast.parse("\n".join(lines))
    last = max((n.end_lineno for n in tree.body
                if isinstance(n, (ast.Import, ast.ImportFrom))), default=0)
    lines.insert(last, imp)
    out = "\n".join(lines)
    ast.parse(out)
    f.write_text(out)
    print(f"    retry+cap wired into {f.relative_to(root)}")
PYEOF
done

# Fix 4 — make the resilience knobs discoverable in each agent's .env.example.
for d in "$HERE"/agents/*/; do
  EX="$d/.env.example"
  [ -f "$EX" ] || continue
  grep -q LLM_MAX_CONCURRENCY "$EX" || cat >> "$EX" <<'ENVEOF'

# --- resilience (added by this kit; see the repo root .env.example) ---
# Quota is shared. Raising LLM_MAX_CONCURRENCY causes contention for everyone
# else on the project -- your burst is someone else's 429. Leave it at 3 on a
# shared classroom project; 8 only if you are certain you are alone on it.
LLM_MAX_CONCURRENCY=3
LLM_RETRY_ATTEMPTS=5
LLM_RETRY_INITIAL_DELAY=1.0
LLM_RETRY_MAX_DELAY=30.0
ENVEOF
done

echo
echo "Done. Agents are in agents/ (upstream $UPSTREAM_SHA)."
echo "Next:  cd agents/biocompass && uv sync && cp .env.example .env"
