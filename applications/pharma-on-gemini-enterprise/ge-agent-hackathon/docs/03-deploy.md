# 3 · Deploy

**Time: 15 minutes**, most of it waiting on a build. You'll finish with your agent
running on the [Gemini Enterprise Agent Platform](https://cloud.google.com/products/gemini-enterprise-agent-platform)
and visible in the [Gemini Enterprise](https://cloud.google.com/gemini-enterprise) app.

Two commands, both from [`agents-cli`](https://github.com/google/agents-cli).

---

## The two commands

### Deploy — laptop to Agent Runtime

```bash
cd agents/biocompass

agents-cli deploy \
  --project=$PROJECT \
  --region=us-central1 \
  --service-name="BioCompass"
```

`agents-cli` reads `agents-cli-manifest.yaml`, packages the agent directory,
derives requirements from `pyproject.toml` + `uv.lock`, and creates a managed
Agent Runtime instance. Five minutes or so. It prints the runtime ID:

```
✅ Deployment successful!
Agent Runtime ID: projects/123456789/locations/us-central1/reasoningEngines/7579568618225008640
```

**Keep that full resource name.** The next command wants all of it, not just the number.

### Publish — Agent Runtime to Gemini Enterprise

```bash
agents-cli publish gemini-enterprise \
  --agent-runtime-id="projects/$NUMBER/locations/us-central1/reasoningEngines/$ENGINE_ID" \
  --gemini-enterprise-app-id="projects/$NUMBER/locations/global/collections/default_collection/engines/$APP_ID" \
  --display-name="BioCompass" \
  --description="Citation-grounded biomedical literature research agent." \
  --registration-type=adk \
  --project-id=$PROJECT
```

Refresh the Gemini Enterprise app and your agent is in the sidebar.

Or use the wrapper, which handles the gotchas below for you:

```bash
./scripts/deploy.sh biocompass $PROJECT
```

---

## Updating an agent you've already deployed

This is the loop you'll actually live in this afternoon: change code, redeploy,
try again. It's the same command.

```bash
agents-cli deploy --project=$PROJECT --region=$REGION --service-name="BioCompass"
```

**Keep `--service-name` identical and it updates the existing engine in place**
rather than creating a new one. You'll see:

```
🚀 Updating agent: BioCompass (this can take a few minutes)...
Agent Runtime ID: projects/.../reasoningEngines/7579568618225008640     ← same ID
```

That matters more than it looks:

- **The engine ID doesn't change, so your Gemini Enterprise registration keeps
  working.** You do *not* need to re-run `agents-cli publish` after a code
  change. Publish once; deploy as often as you like.
- **You don't burn Agent Runtime quota.** Each region caps how many engines you
  can have, and a classroom creating a fresh engine per iteration will hit that
  wall fast. Updating in place costs nothing.

Check what you already have before you deploy:

```bash
agents-cli deploy --list --project=$PROJECT --region=$REGION
```

```
┏━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┓
┃ Display Name        ┃ Resource Name                ┃ Create Time      ┃
┡━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━┩
│ BioCompass          │ projects/.../reasoningEngi…  │ 2026-08-06 11:49 │
└─────────────────────┴──────────────────────────────┴──────────────────┘
```

If the display name in that list matches your `--service-name`, the next deploy
updates it. If it doesn't match — including a typo or a different case — you get
a **new** engine, and your GE app carries on pointing at the old one.

A redeploy takes roughly the same time as the first one, about five minutes.
`--no-wait` returns immediately and `--status` checks on it, which is handy when
you're iterating and don't want a terminal blocked.

> You may see `⚠️ Version mismatch: project was scaffolded with agents-cli 0.6.1,
> running 1.3.1`. It's a warning, not an error — the deploy works. `agents-cli
> scaffold upgrade` clears it if it bothers you.

## Four things that will bite you

These are not hypothetical. Each one cost real time.

### 1. `GOOGLE_APPLICATION_CREDENTIALS` in `.env` breaks the deploy

`agents-cli` sweeps your agent's `.env` into the runtime's environment, and that
variable name is **reserved**:

```
400 FAILED_PRECONDITION: Environment variable name 'GOOGLE_APPLICATION_CREDENTIALS'
is reserved. Please rename the variable in `spec.deployment_spec.env`.
```

The deployed agent doesn't need it — it uses its own agent identity. **Remove the
line before deploying and put it back afterwards** if you need it locally.
`scripts/deploy.sh` does this for you.

### 2. Both IDs must be full resource names

Short IDs are rejected with a helpful-but-easy-to-miss error:

```
Error: Invalid Agent Runtime ID format: 7579568618225008640
Expected: projects/{project}/locations/{location}/reasoningEngines/{id}
```

Same for the app: `projects/{number}/locations/global/collections/default_collection/engines/{app}`.
Note it wants the project **number**, not the ID.

### 3. Your Gemini Enterprise app needs a `default_assistant`

Apps created through the API don't get one automatically, and publish fails with
a 404 on `.../assistants/default_assistant/agents`. Create it:

```bash
curl -X POST -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  "https://discoveryengine.googleapis.com/v1alpha/projects/$NUMBER/locations/global/collections/default_collection/engines/$APP_ID/assistants?assistantId=default_assistant" \
  -d '{"displayName":"Default Assistant"}'
```

Apps created from the Gemini Enterprise **console** already have one.

### 4. Agent Runtime has a per-region quota

```
Failed to create Agent Runtime due to resource exhaustion in this region.
Please delete unused Agent Runtime instances and retry.
```

You've hit the cap for that region. Either delete an unused runtime, or deploy to
a different region — `--region=us-east4` works fine, and since models are served
from `global` anyway, the runtime's region barely matters. List what you have:

```bash
gcloud ai reasoning-engines list --region=us-central1 --project=$PROJECT 2>/dev/null \
  || echo "use the REST API — see docs/04-troubleshooting.md"
```

---

## Verify it worked

```bash
curl -s -X POST \
  -H "Authorization: Bearer $(gcloud auth application-default print-access-token)" \
  -H "x-goog-user-project: $PROJECT" -H "Content-Type: application/json" \
  "https://us-central1-aiplatform.googleapis.com/v1beta1/$RUNTIME_ID:streamQuery?alt=sse" \
  -d '{"class_method":"async_stream_query","input":{"user_id":"u1","message":"Reply with the single word: ready"}}'
```

Then open the app and ask it something. If the app answers but the API doesn't (or
vice versa), you have a registration problem rather than an agent problem.

---

## Then automate it

The same two commands belong in CI. The upstream repo ships a Cloud Build config
that runs exactly `agents-cli deploy` and `agents-cli publish gemini-enterprise` —
so the path you just ran by hand is the path that ships. That's the point of
using the CLI rather than clicking through a console.

Next: **[Build your own](../README.md#build-your-own)** · or **[Troubleshooting](04-troubleshooting.md)**
