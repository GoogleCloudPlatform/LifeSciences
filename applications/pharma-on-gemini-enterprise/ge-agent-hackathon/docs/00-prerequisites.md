# 0 · Getting started

**Time: 20 minutes.** Do this before the hackathon, not during it. The afternoon
only works if this part is already done.

There are two ways to work. **Pick one.** Cloud Shell is faster to start and has
fewer things to go wrong; a local laptop is nicer to iterate in.

| | **Path A · Cloud Shell** | **Path B · Your laptop** |
| --- | --- | --- |
| Setup time | ~5 min | ~20 min |
| Installs anything? | No | gcloud, uv, Python 3.13 |
| Auth | Already signed in | You run two auth commands |
| Editing code | Cloud Shell Editor | Your own editor |
| Best for | Getting going fast, or if local installs are locked down | A full day of building |

Both paths need the same **two things you cannot install**: a project with the
APIs on, and a Gemini Enterprise app with a licence. Those are covered first.

---

## What you need either way

### 1. A Google Cloud project you can deploy into

You need **Owner**, or enough rights to enable APIs and create service accounts.
Note the project ID **and** the project number — some commands want the number.

```bash
gcloud projects describe YOUR_PROJECT --format='value(projectId,projectNumber)'
```

### 2. APIs enabled

Gemini Enterprise is only one of four APIs in play. Each does a distinct job, and
missing any one of them fails at a different, confusing point:

| API | What it's for | You'll notice it's missing when… |
| --- | --- | --- |
| `discoveryengine.googleapis.com` | **Gemini Enterprise itself** — the app, its `default_assistant`, and agent registration | `agents-cli publish` 403s, or the app doesn't exist |
| `aiplatform.googleapis.com` | **Agent Platform / Agent Runtime** — where the agent is deployed and runs, plus all Gemini model calls | Model calls 403 locally; `agents-cli deploy` can't create a runtime |
| `storage.googleapis.com` | The **staging bucket** `agents-cli` packages your agent into, and artifact storage at runtime | Deploy fails while uploading source |
| `cloudbuild.googleapis.com` | Builds the **container image** for the deployed agent | Deploy starts, then dies during build |

```bash
gcloud services enable \
  discoveryengine.googleapis.com \
  aiplatform.googleapis.com \
  storage.googleapis.com \
  cloudbuild.googleapis.com \
  --project=YOUR_PROJECT
```

Confirm all four came back on — enabling is asynchronous and can lag a minute:

```bash
gcloud services list --enabled --project=YOUR_PROJECT \
  | grep -E 'discoveryengine|aiplatform|storage|cloudbuild'
```

> Enabling an API is **not** the same as having access to what's behind it.
> `aiplatform` being on doesn't mean a given model is available to your project,
> and `discoveryengine` being on doesn't grant you a Gemini Enterprise licence.
> `scripts/preflight.sh` checks the APIs *and* actually calls a model, which is
> the only way to know.

### 3. A Gemini Enterprise app **and an active licence**

This is where people get stuck, so read it twice. Publishing an agent into
[Gemini Enterprise](https://cloud.google.com/gemini-enterprise) needs **two
separate things**:

- an **app** — a Discovery Engine engine with a `default_assistant`
- an **active Gemini Enterprise licence assigned to your user**

Without the licence, publishing fails with:

```
The user cannot create an agent since an active Gemini Enterprise license is not
available. Please contact your GCP administrator to allocate an active license to you.
```

That is a **licensing wall, not a permissions problem** — no IAM change fixes it.
A trial licence is enough.

> **Create the app from the [Gemini Enterprise console](https://console.cloud.google.com/gemini-enterprise).**
> It provisions the app, its assistant and the licence association together.
> `scripts/setup-ge.sh` can create an app over the API, but an API-created app has
> **no assistant** (publishing then 404s) and the script cannot grant you a licence.

---

## Path A · Cloud Shell

Nothing to install. [Open Cloud Shell](https://shell.cloud.google.com/) and:

```bash
# 1. point at your project
gcloud config set project YOUR_PROJECT

# 2. credentials your code will use (gcloud itself is already signed in)
gcloud auth application-default login

# 3. tooling. Cloud Shell ships uv, so only install it if it's missing.
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv tool install google-agents-cli

# 4. this kit (shallow — you don't need the history)
git clone --depth 1 https://github.com/GoogleCloudPlatform/LifeSciences.git
cd LifeSciences/applications/pharma-on-gemini-enterprise/ge-agent-hackathon
./scripts/preflight.sh YOUR_PROJECT
```

### Running the dev UI in Cloud Shell

Cloud Shell can only expose a few ports through **Web Preview**, and `8080` is the
default. Use it:

```bash
uv run adk web . --port 8080
```

Then click **Web Preview → Preview on port 8080** (top-right of the Cloud Shell
toolbar). Everywhere else in these docs uses port 8501 — on Cloud Shell, read
`8501` as `8080`.

**Two things to know.** Your `$HOME` persists (5 GB) but the VM itself is
ephemeral, so re-`export PATH` in a new session, and don't leave anything precious
outside `$HOME`. And Cloud Shell disconnects when idle — a long deploy is safer
under `nohup`.

---

## Path B · Your laptop

| Tool | Version | Install |
| --- | --- | --- |
| [gcloud SDK](https://cloud.google.com/sdk/docs/install) | latest | `brew install --cask google-cloud-sdk` or the installer |
| [uv](https://docs.astral.sh/uv/) | latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Python | 3.13 | `uv python install 3.13` |
| [`agents-cli`](https://github.com/google/agents-cli) | 1.3+ | `uv tool install google-agents-cli` |
| ADK | 2.x | installed per agent by `uv sync` |

Then authenticate — **two different credentials, two different jobs**:

```bash
gcloud auth login                      # who you are to the gcloud CLI
gcloud config set project YOUR_PROJECT
gcloud auth application-default login  # what your CODE uses to call Google APIs
```

Skipping the second is the most common cause of confusing 403s. Finally:

```bash
git clone --depth 1 https://github.com/GoogleCloudPlatform/LifeSciences.git
cd LifeSciences/applications/pharma-on-gemini-enterprise/ge-agent-hackathon
./scripts/preflight.sh YOUR_PROJECT
```

> **Why `uv` rather than `pip`?** Every agent pins its dependencies in `uv.lock`,
> and `uv sync` reproduces that exactly. Dependency drift is the single most common
> reason an agent that worked last month doesn't today — see
> [Troubleshooting](04-troubleshooting.md).

> **If your organisation blocks user ADC for Vertex**, point
> `GOOGLE_APPLICATION_CREDENTIALS` at a service-account key instead. It works
> locally, but **strip it from the agent's `.env` before deploying** — that name is
> reserved in the deployed runtime. `scripts/deploy.sh` does this for you.

---

## Verify, whichever path you took

```bash
./scripts/preflight.sh YOUR_PROJECT
```

It checks tools and versions, both kinds of auth, the four APIs, that a Gemini
Enterprise app exists with an assistant, and that the models actually answer from
your project. **Fix every red line before moving on** — each one is something that
would otherwise fail halfway through a deploy.

---

## One thing about models

Gemini 3.x is served from the **`global`** endpoint, not regional ones. The agents
set `MODEL_LOCATION=global` for model calls while still deploying to a regional
Agent Runtime — those are two different settings and conflating them produces a
404 that reads like the model doesn't exist.

Next: **[1 · Environment setup](01-environment.md)**
