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

# Verify everything needed before the hackathon. Safe to re-run.
# Usage: ./scripts/preflight.sh YOUR_PROJECT_ID
set -uo pipefail
PROJECT="${1:-$(gcloud config get-value project 2>/dev/null)}"
PASS=0; FAIL=0
ok(){ echo "  ✅ $1"; PASS=$((PASS+1)); }
no(){ echo "  ❌ $1"; echo "       → $2"; FAIL=$((FAIL+1)); }
hdr(){ echo; echo "── $1"; }

[ -z "$PROJECT" ] && { echo "Usage: $0 YOUR_PROJECT_ID"; exit 1; }
echo "Preflight for project: $PROJECT"

hdr "Tools"
command -v gcloud >/dev/null && ok "gcloud $(gcloud version 2>/dev/null | head -1 | awk '{print $NF}')" \
  || no "gcloud not found" "https://cloud.google.com/sdk/docs/install"
command -v uv >/dev/null && ok "uv $(uv --version 2>/dev/null | awk '{print $2}')" \
  || no "uv not found" "curl -LsSf https://astral.sh/uv/install.sh | sh"
if command -v agents-cli >/dev/null; then ok "agents-cli $(agents-cli --version 2>/dev/null | awk '{print $NF}')"
else no "agents-cli not found" "uv tool install google-agents-cli  (then export PATH=\$HOME/.local/bin:\$PATH)"; fi

hdr "Authentication"
gcloud auth print-access-token >/dev/null 2>&1 && ok "gcloud is signed in" \
  || no "gcloud not signed in" "gcloud auth login"
if gcloud auth application-default print-access-token >/dev/null 2>&1; then ok "Application Default Credentials present"
else no "no ADC — your CODE cannot call Google APIs" "gcloud auth application-default login"; fi
if [ -n "${GOOGLE_APPLICATION_CREDENTIALS:-}" ]; then
  echo "  ℹ️  GOOGLE_APPLICATION_CREDENTIALS is set → $GOOGLE_APPLICATION_CREDENTIALS"
  echo "       remember to strip it from an agent .env before deploying (reserved name)"
fi

hdr "APIs"
ENABLED=$(gcloud services list --enabled --project="$PROJECT" --format='value(config.name)' 2>/dev/null)
for api in discoveryengine.googleapis.com aiplatform.googleapis.com storage.googleapis.com cloudbuild.googleapis.com; do
  echo "$ENABLED" | grep -q "^$api$" && ok "$api" \
    || no "$api not enabled" "gcloud services enable $api --project=$PROJECT"
done

hdr "Models answer from this project"
TOK=$(gcloud auth application-default print-access-token 2>/dev/null)
for M in gemini-3.7-flash gemini-3-pro-image; do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST \
    -H "Authorization: Bearer $TOK" -H "x-goog-user-project: $PROJECT" -H "Content-Type: application/json" \
    "https://aiplatform.googleapis.com/v1/projects/$PROJECT/locations/global/publishers/google/models/$M:generateContent" \
    -d '{"contents":[{"role":"user","parts":[{"text":"hi"}]}],"generationConfig":{"maxOutputTokens":8}}' 2>/dev/null)
  [ "$CODE" = "200" ] && ok "$M reachable (global)" \
    || no "$M returned HTTP $CODE" "check the model ID and that MODEL_LOCATION=global"
done

hdr "Gemini Enterprise"
# Discovery Engine accepts the project ID here, but publishing an agent wants the
# NUMBER, so print it — it is the one value participants have to copy by hand.
NUM=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)' 2>/dev/null)
[ -n "$NUM" ] && echo "     ℹ️  project number (use this when publishing): $NUM"
APPS=$(curl -s -H "Authorization: Bearer $TOK" -H "x-goog-user-project: $PROJECT" \
  "https://discoveryengine.googleapis.com/v1alpha/projects/$PROJECT/locations/global/collections/default_collection/engines" 2>/dev/null)
if echo "$APPS" | grep -q '"engines"'; then
  echo "$APPS" | python3 -c "
import sys,json
for e in json.load(sys.stdin).get('engines',[]):
    print('  ✅ app:', e['name'].split('/')[-1], '|', e.get('displayName'))" 2>/dev/null
  ok "at least one Gemini Enterprise app exists"
  echo "     ℹ️  an app is not a licence — publishing still needs an active GE licence on your user"
else
  no "no Gemini Enterprise app found" "create one in the Gemini Enterprise console (it also provisions the assistant)"
fi

echo; echo "──────────────────────────────"
echo "  $PASS passed, $FAIL to fix"
[ "$FAIL" -gt 0 ] && echo "  Fix the ❌ lines before continuing — each one fails later, less obviously." && exit 1
echo "  Ready. Next: docs/01-environment.md"
