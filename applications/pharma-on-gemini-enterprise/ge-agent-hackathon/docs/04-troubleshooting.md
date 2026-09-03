# 4 · Troubleshooting

Every entry below is a failure that actually happened while building and running
these agents, with the exact error text and the fix. Skim it once before you
start — several of these look like one problem and are actually another.

---

## Deploy and publish

### `Environment variable name 'GOOGLE_APPLICATION_CREDENTIALS' is reserved`

```
400 FAILED_PRECONDITION: Environment variable name 'GOOGLE_APPLICATION_CREDENTIALS'
is reserved. Please rename the variable in `spec.deployment_spec.env`.
```

`agents-cli` copies your agent's `.env` into the deployed runtime, and that name
is reserved there. The deployed agent uses its own identity and doesn't need it.
Remove the line before deploying; `scripts/deploy.sh` handles this automatically.

### `Invalid Agent Runtime ID format`

Use the full resource name, not the bare number:
`projects/{number}/locations/{region}/reasoningEngines/{id}`.
Same for the app ID, which also wants the project **number**.

### `404 ... /assistants/default_assistant/agents`

Your Gemini Enterprise app has no assistant. Apps created via the API don't get
one automatically — see [Deploy](03-deploy.md#3-your-gemini-enterprise-app-needs-a-default_assistant).

### `an active Gemini Enterprise license is not available`

```
The user cannot create an agent since an active Gemini Enterprise license is not
available. Please contact your GCP administrator to allocate an active license to you.
```

A licensing wall, not a permissions problem. No IAM change will fix it. You need a
Gemini Enterprise licence assigned to your user; a trial licence works.

### `Failed to create Agent Runtime due to resource exhaustion in this region`

Per-region cap on Agent Runtime instances. Delete an unused one or deploy to
another region (`--region=us-east4`). Models are served from `global` regardless,
so the runtime's region has little effect on behaviour.

### My redeploy created a NEW engine and Gemini Enterprise still shows the old agent

`agents-cli deploy` matches on `--service-name`. If it doesn't match an existing
deployment exactly — a typo, different case, different region — you get a second
engine, and your GE registration keeps pointing at the first one. Check with
`agents-cli deploy --list --project=$PROJECT --region=$REGION` and redeploy with
the display name exactly as listed. See
[Updating an agent](03-deploy.md#updating-an-agent-youve-already-deployed).

### `403 ... reasoningEngines.get denied` on a resource you can clearly read

Check `GOOGLE_APPLICATION_CREDENTIALS`. If it points at a service-account key for
a *different* project, that identity is used instead of your ADC and the error
names the resource rather than the cause. `unset` it and retry.

---

## Models

### `Publisher model ... was not found or your project does not have access to it`

Three causes, in order of likelihood:

1. **The model ID moved.** Preview IDs are withdrawn at GA.
   `gemini-3-pro-image-preview` → **`gemini-3-pro-image`**. Anything with
   `-preview` in a config is a scheduled outage; re-verify it.
2. **Wrong location.** Gemini 3.x is served from **`global`** only. A regional
   endpoint 404s. Keep `MODEL_LOCATION=global`.
3. **Genuinely no access.** Confirm with a direct `generateContent` call before
   blaming your code — see [Environment](01-environment.md#check-a-model-actually-answers).

### `429 RESOURCE_EXHAUSTED` with no quota metric named

That's **dynamic shared quota**, not a project quota — a quota-increase request
will not help. But you can fix this in code, and the default is working against
you.

**ADK does not retry by default.** `google.adk.models.google_llm.Gemini` accepts
a `retry_options` field, which it passes to the google-genai client. If you give
an agent a model as a plain string — `model="gemini-3.5-flash"` — that field is
`None`, and google-genai then uses:

```python
# google/genai/_api_client.py
if options is None:
    return {'stop': tenacity.stop_after_attempt(1), 'reraise': True}   # NO retries
```

So one transient 429 is fatal. And because ADK surfaces a failed branch as an
`ExceptionGroup` out of the parallel fan-out, that single 429 takes the **whole
invocation** down — which is why you see "the pipeline ran but nothing came back".

google-genai already knows how to retry 408/429/5xx with exponential backoff and
jitter. Nothing was asking it to. Pass a model object instead of a string:

```python
from google.adk.models.google_llm import Gemini
from google.genai import types

WORKER = Gemini(
    model="gemini-3.5-flash",
    retry_options=types.HttpRetryOptions(
        attempts=5, initial_delay=1.0, max_delay=30.0, exp_base=2.0),
)
LlmAgent(model=WORKER, ...)      # not model="gemini-3.5-flash"
```

**Then cap concurrency.** Retry alone makes contention worse when many people
share a project — every failure retries, adding load. `ParallelAgent` has no
concurrency knob, so gate at the model layer with a semaphore. This kit ships
both as `app/model_utils.py` (installed by `scripts/get-agents.sh`), tunable with
`LLM_MAX_CONCURRENCY` and `LLM_RETRY_*`; see the root `.env.example`.

Measured on the deep-research path: **without this, a single 429 killed the run.
With it, the same run hit two 429s and still completed** — 377s instead of dying.
The cap costs latency (a light lookup went 30s → 57s at `LLM_MAX_CONCURRENCY=3`),
which is the trade you want on a shared project.

Other levers: narrow the request so it takes a cheaper path, drop
`MAX_CRITIC_ROUNDS`, or use Provisioned Throughput for a guaranteed demo window.

---

## Dependencies — the slow-burning ones

### `cannot import name 'McpToolset' from 'google.adk.tools.mcp_tool'`

**This error is a red herring.** ADK's `mcp_tool/__init__.py` starts with
`__all__ = []` and a `try:` block that imports every public name. If the optional
`mcp` package fails to import, the block is swallowed and the package
legitimately exports nothing. The real error underneath is usually:

```
ModuleNotFoundError: No module named 'mcp.shared.session'
```

Cause: `google-adk` requires `mcp>=1.24,<2`, but nothing pinned it, so resolution
picked **mcp 2.x**, which removed that module. **Pin `mcp>=1.24,<2`.**

### An agent that worked for months suddenly won't start

Almost always an unpinned transitive dependency. A container re-resolves its
dependencies at boot, so *the build that deployed fine in May is not the build
that restarts today*. Pin optional extras explicitly. When they break, the
framework often swallows the real error and reports a misleading one — see above.

### `'LlmAgent' object has no attribute 'mode'`

A stored cloudpickle built against an older ADK than the one now installed.
Re-package the agent against the current stack; bumping a version pin alone won't
fix it.

---

## Running locally

### A turn in `adk web` hangs forever

There is **no client-side timeout** in the dev UI. We saw a single
`gemini-3.5-flash` call never return and sit for 15 minutes. It will not recover.
Watch the server log — if model calls have stopped appearing, abandon the turn and
resubmit. A fresh turn typically works immediately.

### The same prompt takes a different path every time

Expected. Routing is a model decision, not a rule. If you need a predictable
demo, phrase the request to match the routing instruction's own language for the
lane you want (e.g. an explicit "find recent papers on…" for a light lookup) and
rehearse it a couple of times.

### Long silence mid-run, then a correct answer

Normal for deep-research paths — parallel retrieval, synthesis and a critic pass
take minutes. Keep the trace panel open so the wait is visible work rather than a
blank spinner.

---

## Still stuck?

- **Read the runtime's own logs**, not just the operation error. For a deployed
  agent: filter Cloud Logging on
  `resource.type="aiplatform.googleapis.com/ReasoningEngine"` and the engine ID.
  The control-plane error is often generic while the container log is specific.
- **Check the deployed environment**, not just your source. A deployed agent's
  `spec.deploymentSpec.env` **overrides** the defaults in your code — so fixing
  `.env.example` in git changes nothing until you redeploy or patch the env. This
  is the single most common "but I fixed that" bug.
- [ADK docs](https://google.github.io/adk-docs/) · [agents-cli](https://github.com/google/agents-cli) · [Agent Platform troubleshooting](https://docs.cloud.google.com/gemini-enterprise-agent-platform/troubleshooting/agent-deployment)
