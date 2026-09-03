# 1 · Environment setup

**Time: 10 minutes.** You'll finish with BioCompass and PaperBanana on disk, with
dependencies installed and configuration in place.

---

## Get the agents

```bash
./scripts/get-agents.sh
```

This sparse-checkouts just the two agent directories from
[GoogleCloudPlatform/LifeSciences](https://github.com/GoogleCloudPlatform/LifeSciences)
into `agents/`, then applies this kit's fixes.

**Why a script instead of a fork?** The agents live inside a large monorepo
alongside FoldRun, Sentinel and Model Garden. Fetching only what you need keeps
this repo small and — more importantly — keeps you on upstream `main` rather than
a fork that silently rots. The fixes are applied as small, visible patches so you
can always see what this kit changed and why.

If you'd rather have your own writable copy, fork
[GoogleCloudPlatform/LifeSciences](https://github.com/GoogleCloudPlatform/LifeSciences)
and point the script at your fork with `AGENTS_REPO=...`.

---

## Install dependencies

Per agent, from inside its directory:

```bash
cd agents/biocompass
uv sync
```

`uv sync` reads `pyproject.toml` and `uv.lock` and builds `.venv/` with exactly
the pinned versions. First run takes a few minutes; after that it's seconds.

Do the same for `agents/paperbanana`.

---

## Configure it

Each agent reads a `.env` from its own directory. Copy the example and fill it in:

```bash
cp .env.example .env
```

A working BioCompass `.env`:

```bash
GOOGLE_GENAI_USE_ENTERPRISE=1
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=us-central1

# Gemini 3.x is served ONLY from the global endpoint.
# The agent forces this in-process for model calls; the line above is the
# region the agent is DEPLOYED to. They are different things.
MODEL_LOCATION=global

COORDINATOR_MODEL_NAME=gemini-3.1-pro-preview
WORKER_MODEL_NAME=gemini-3.7-flash
IMAGE_MODEL_NAME=gemini-3-pro-image
IMAGE_SIZE=2K
MAX_CRITIC_ROUNDS=2
```

### Three things worth understanding

**`GOOGLE_CLOUD_LOCATION` vs `MODEL_LOCATION`.** The first is where the agent runs.
The second is where model calls go. Gemini 3.x only answers on `global`, so
`MODEL_LOCATION=global` while `GOOGLE_CLOUD_LOCATION` stays regional. Conflating
them produces a 404 that reads like the model was deleted.

**Model IDs move.** `IMAGE_MODEL_NAME` must be `gemini-3-pro-image` — the older
`gemini-3-pro-image-preview` was withdrawn when the model reached GA and now 404s
everywhere. Preview model IDs are scheduled outages; treat any `-preview` in a
config as something to re-verify.

**`.env` is gitignored, and must stay that way.** It holds project IDs and
sometimes keys. `.env.example` is the one that gets committed.

---

## Check a model actually answers

Before running a whole agent, confirm your project can reach the models. This
takes two seconds and saves a lot of confusion:

```bash
curl -s -X POST \
  -H "Authorization: Bearer $(gcloud auth application-default print-access-token)" \
  -H "x-goog-user-project: $PROJECT" -H "Content-Type: application/json" \
  "https://aiplatform.googleapis.com/v1/projects/$PROJECT/locations/global/publishers/google/models/gemini-3.7-flash:generateContent" \
  -d '{"contents":[{"role":"user","parts":[{"text":"hi"}]}],"generationConfig":{"maxOutputTokens":8}}'
```

A 200 means you're ready. A 404 means wrong model ID or wrong location. A 403
usually means the API isn't enabled or ADC isn't set.

Next: **[2 · Run locally](02-run-locally.md)**
