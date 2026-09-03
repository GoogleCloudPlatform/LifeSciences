# 6 · Interactive agents — giving your agent a real UI with A2UI

Most agents answer in text. Some jobs need more than text: a reviewer picking a
disposition per finding, an approver working a checklist, a triage queue where
each row has its own actions. Typing "accept item 3" is a bad interface for that.

[**A2UI**](https://a2ui.org/) lets an agent return **interactive UI** — cards,
tabs, images, dropdowns, buttons, text fields — that Gemini Enterprise renders
inline in the conversation, and whose interactions come back to your agent as
structured data. You keep one agent and one conversation; the UI is just another
kind of response.

This doc is the generalised version of a working A2UI agent on Gemini
Enterprise. Everything under "Integration gotchas" was learned the hard way —
none of it is in the protocol docs.

---

## When this is worth it

| Use A2UI | Stay with text |
| --- | --- |
| The user disposition **many items**, each with the same few choices | One question, one answer |
| The answer is **visual** — an annotated image, a chart, a compare view | A paragraph does the job |
| You want **structured input back** (a choice, not a sentence to parse) | Free-form conversation is the point |
| The interaction **loops** — resolve, revise, re-check | One shot and done |

If the response is a paragraph, send a paragraph. UI where it doesn't help is
worse than no UI, and it costs you latency.

---

## The architecture that works

```
user turn ──▶ ONE structured LLM call ──▶ typed result ──▶ code builds the UI ──▶ A2UI JSON
                (output_schema=...)         (Pydantic)      (deterministic)
     ▲                                                                          │
     └──────────── action payload (structured, not text) ◀──── user clicks ◀─────┘
```

**The single most important decision: the LLM produces *data*, your code
produces the *UI*.**

Do not ask the model to emit A2UI JSON. It will mostly work, which is worse than
failing — you get layout drift between runs, occasional schema violations, retry
loops, and no way to unit-test the surface. Instead:

1. One Gemini call with `output_schema=YourResultModel` → validated Pydantic
   object.
2. A plain Python function turns that object into A2UI surface JSON.
3. Test the function against the catalog schema like any other code.

Predictable layout, no retries, and a UI bug is a code bug you can fix in
seconds rather than a prompt you have to re-tune.

**Keep the surface builder separate from the agent.** One module that knows the
domain (produces findings, rows, results) and one that knows the UI (turns them
into surfaces). They change for different reasons.

---

## Integration gotchas

These are the ones that cost real time. Every one of them presents as "my agent
works locally and looks broken in Gemini Enterprise."

**GE does not send the `X-A2A-Extensions` header.** The A2A spec has clients
opt into extensions with that header. Gemini Enterprise doesn't send it — it
expects agents to emit A2UI inline by default. If you gate UI on the header, GE
gets plain text and you conclude A2UI isn't working. Default to UI mode, and
keep an env var (`A2UI_DEFAULT=false`) to restore strict header-based opt-in for
other A2A clients.

**A text-only response with `input_required` renders as a raw form widget.** You
get a stray box titled `mock_function_call_for_required_user_input`. Any turn
that returns text and no UI must be `TaskState.completed`. Reserve
`input-required` for turns that actually carry an A2UI part and expect an action
back.

**Use A2UI v0.9, and don't hand-build the part.** v0.9 is GA for Gemini
Enterprise and is closer to the 1.0 spec, so build against it rather than v0.8 —
but be aware the two are not compatible, and the shape of the integration is
what changed, not just a version string.

Under v0.8 you constructed the A2A part yourself and had to get the mime type
right (`application/json+a2ui`; a wrong one produced a blank turn with no error
anywhere). **Under v0.9 you never set a mime type.** You emit the A2UI JSON
wrapped in the SDK's tags inside your response text, and let the SDK convert it
into the A2A `DataPart`s that GE renders:

```python
from a2ui.a2a.parts import parse_response_to_parts
from a2ui.schema.constants import A2UI_CLOSE_TAG, A2UI_OPEN_TAG

a2ui_msg = [{"version": "v0.9", "updateDataModel": {...}}]
raw = f"Filtered findings.\n{A2UI_OPEN_TAG}\n{json.dumps(a2ui_msg)}\n{A2UI_CLOSE_TAG}"
final_parts = parse_response_to_parts(raw)
await updater.add_artifact(final_parts, name="response")
```

**The wire version string is `"v0.9"`, with the `v`.** It is deliberately not
the same as the SDK's own constant `a2ui.schema.constants.VERSION_0_9`, which is
`"0.9"`. Incoming client actions are tagged the same way, so match on `"v0.9"`
when you check the envelope.

The tag constants moved between SDK releases — older code imports them from
`a2ui.parser.parser`, newer from `a2ui.schema.constants`. If the import fails,
try the other path before assuming the SDK is broken.

> Working references, both in this monorepo:
> `sentinel-on-gemini-enterprise/app/app_utils/a2ui_executor.py` and
> `creative-workflow-on-gemini-enterprise/app/app_utils/a2ui_executor.py`, each
> with example surfaces under `app/examples/v0.9/`.

**Composer attachments arrive as A2A `FilePart` / `FileWithBytes`,** accompanied
by `<end_of_user_uploaded_file: ...>` text markers. Handle the file parts and
ignore the markers; don't try to parse the marker text.

**A2UI has no image-overlay primitive.** If you need boxes, highlights or
callouts on an image, render them into the image before you send it — draw with
PIL, upload the PNG, and reference the URL. There is no client-side layer to
position things over an image.

**Serving an image without making a bucket public:** upload to GCS and embed a
**V4 signed URL**, signed keylessly through IAM `signBlob`. The runtime service
account signs as itself — no service-account key file anywhere:

```bash
gcloud iam service-accounts add-iam-policy-binding \
  PROJECT_NUMBER-compute@developer.gserviceaccount.com --project=PROJECT_ID \
  --member="serviceAccount:PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/iam.serviceAccountTokenCreator"
```

**Bind inputs to data-model paths, and read them back by path.** A dropdown and
a text field per row bind to paths in the surface's data model; the submit
button's action context references those paths. Your agent then receives
`{"choice": "accept", "comment": "..."}` — structured input, not a sentence to
parse. This is the whole reason to use A2UI rather than asking a question.

**Gemini 3.x is served from the `global` genai endpoint only.** Set
`GOOGLE_CLOUD_LOCATION=global`. It's the genai API location and is independent
of the region you deploy to — a `us-central1` Agent Runtime (or Cloud Run
service) with a `global` genai location is correct, not a mistake.

**Model IDs get withdrawn at GA.** A `-preview` suffix is a scheduled outage:
the ID stops resolving the day the model goes GA and you get a 404 that reads
like a permissions problem. Keep model IDs in env vars, never in code. (This bit
us: `gemini-3-pro-image-preview` → `gemini-3-pro-image`.)

---

## Deploying it

**Deploy to Agent Runtime, not Cloud Run.** Being an A2A server does not force
you onto Cloud Run — `agents-cli` deploys an A2A agent to Agent Runtime
directly, and that is the pattern to prefer. You get the managed runtime and
agent identity instead of a service you have to keep warm, authorize and patch
with its own URL.

The agent declares this in its `agents-cli-manifest.yaml`:

```yaml
create_params:
  deployment_target: "agent_runtime"
  is_a2a: true
  agent_identity: true
```

Then, from the agent directory:

```bash
gcloud config set project YOUR_PROJECT_ID
agents-cli deploy --agent-identity
```

**Registration with GE has to be over A2A** — that part is not optional. For
production the reference agents provision the Reasoning Engine with Terraform
and deploy via Cloud Build, which can register the agent in the same step by
passing `_REGISTRATION_TYPE="a2a"` and your `_GEMINI_ENTERPRISE_APP_ID`. See
`sentinel-on-gemini-enterprise/README.md` and `../shared/cloudbuild.yaml`.

Note `GOOGLE_GENAI_USE_ENTERPRISE=true` and `GOOGLE_CLOUD_LOCATION=global` in
the deployed environment — the same two settings as everywhere else in this kit.

<details>
<summary><strong>Cloud Run fallback</strong> — only if you have a reason not to use Agent Runtime</summary>

```bash
gcloud run deploy YOUR_SERVICE --source . \
  --project "$PROJECT_ID" --region us-central1 --memory 1Gi \
  --min-instances 1 --max-instances 1 \
  --no-allow-unauthenticated \
  --set-env-vars=GOOGLE_CLOUD_PROJECT="$PROJECT_ID",GOOGLE_CLOUD_LOCATION=global,GOOGLE_GENAI_USE_ENTERPRISE=TRUE,MODEL="$MODEL",GOOGLE_PYTHON_PACKAGE_MANAGER=uv
```

Three flags that are not incidental:

- **`--min-instances 1`** — a cold start (~17s) makes Gemini Enterprise's agent
  fetch time out and trips its throttling protection. The agent then looks dead
  for the rest of the session. Pay for the warm instance.
- **`--max-instances 1`** — only if you hold per-conversation state in memory
  keyed by A2A `contextId`. Fine for a demo; a restart drops open sessions. For
  anything real, move state to Firestore or Redis and drop this flag.
- **`--no-allow-unauthenticated`** — then grant GE's own service agent the
  invoker role (below). Never make the service public.

**The agent needs to know its own URL** for its agent card, which you only learn
after the first deploy. Deploy, read the URL, then patch it in:

```bash
SERVICE_URL=$(gcloud run services describe YOUR_SERVICE --project="$PROJECT_ID" \
  --region=us-central1 --format='value(status.url)')
gcloud run services update YOUR_SERVICE --project="$PROJECT_ID" \
  --region=us-central1 --update-env-vars=AGENT_URL="$SERVICE_URL"
```

**Let Gemini Enterprise invoke it.** The caller is the Discovery Engine service
agent, not your account:

```bash
gcloud run services add-iam-policy-binding YOUR_SERVICE \
  --project="$PROJECT_ID" --region=us-central1 \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-discoveryengine.iam.gserviceaccount.com" \
  --role="roles/run.invoker"
```

</details>

---

## Registering it in Gemini Enterprise

An A2A agent is registered by POSTing an **agent card** to the Discovery Engine
`v1alpha` agents API. The card must declare the A2UI extension, or GE will not
render your surfaces:

```json
{
  "protocolVersion": "0.3.0",
  "name": "Your Agent",
  "url": "https://YOUR_SERVICE-xxxx.us-central1.run.app",
  "version": "1.0.0",
  "capabilities": {
    "streaming": false,
    "preferredTransport": "JSONRPC",
    "extensions": [{
      "uri": "https://a2ui.org/a2a-extension/a2ui/v0.9",
      "required": false,
      "params": {
        "supportedCatalogIds": [
          "https://www.gstatic.com/vertexaisearch/a2ui/v0_9/gemini_enterprise_composite_catalog.json"
        ]
      }
    }]
  },
  "skills": [],
  "defaultInputModes": ["text", "text/plain", "image/png", "image/jpeg", "image/webp"],
  "defaultOutputModes": ["text", "text/plain"]
}
```

**Let the SDK build that extension block rather than typing it.** The catalog ID
is a GE-specific composite catalog on `gstatic`, not the standard a2ui.org one,
and it is easy to get subtly wrong:

```python
from a2ui.a2a.extension import get_a2ui_agent_extension

get_a2ui_agent_extension(
    version="0.9",
    accepts_inline_catalogs=False,
    supported_catalog_ids=[
        "https://www.gstatic.com/vertexaisearch/a2ui/v0_9/gemini_enterprise_composite_catalog.json"
    ],
)
```

Note `version="0.9"` here — the extension takes the bare form, while the
client-to-server message envelope uses `"v0.9"`. Both are correct in their own
place.

**Set `streaming=False` on the agent card.** Agent Runtime currently supports
only non-streaming mode for A2UI.

POST it wrapped in `a2aAgentDefinition.jsonAgentCard` (a **string**, not a
nested object):

```bash
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')

curl -sS -X POST \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  "https://discoveryengine.googleapis.com/v1alpha/projects/$PROJECT_NUMBER/locations/global/collections/default_collection/engines/$APP_ID/assistants/default_assistant/agents" \
  -d "$PAYLOAD"
```

Note it takes the **project number**, not the project ID. Then: Gemini
Enterprise → **Agents** → *From your organization*.

To remove it, `DELETE` the resource name the POST returned.

---

## Testing without the UI

You don't need Gemini Enterprise in the loop to develop. Run the server locally
and drive it with curl — the extension header activates A2UI for a plain A2A
client:

```bash
curl -sS -X POST http://localhost:8080/ \
  -H "Content-Type: application/json" \
  -H "X-A2A-Extensions: https://a2ui.org/a2a-extension/a2ui/v0.9" \
  -d '{"jsonrpc":"2.0","id":"1","method":"message/send","params":{"message":{
        "role":"user",
        "parts":[{"kind":"data","data":{"userAction":{"name":"your_action","context":{}}}}],
        "messageId":"m1","contextId":"c1"}}}'
```

The response contains your surface JSON. Assert against it in tests. Validate
surfaces against the standard-catalog schema in CI — a malformed surface renders
as *nothing at all* in GE, with no error to tell you why.

---

## Checklist

- [ ] LLM emits typed data (`output_schema`); code builds the surface
- [ ] Surfaces validated against the catalog schema in a test
- [ ] A2UI on by default — don't wait for `X-A2A-Extensions`
- [ ] Text-only turns return `completed`, never `input-required`
- [ ] A2UI JSON wrapped in `A2UI_OPEN_TAG`/`A2UI_CLOSE_TAG` and converted with
      `parse_response_to_parts` — no hand-set mime type
- [ ] Message envelopes tagged `"v0.9"`; the agent extension takes `"0.9"`
- [ ] Images pre-rendered with any annotations; served via V4 signed URL
- [ ] `GOOGLE_CLOUD_LOCATION=global` for Gemini 3.x
- [ ] Model IDs in env vars, not code
- [ ] Deployed to Agent Runtime (`deployment_target: agent_runtime`, `is_a2a: true`)
- [ ] Agent card declares the A2UI extension, `streaming: false`, and the GE catalog ID
- [ ] Cloud Run only: `--min-instances 1`, `AGENT_URL` patched after first
      deploy, Discovery Engine service agent granted `run.invoker`

---

## Links

- [A2UI](https://a2ui.org/) · [GE composite catalog v0_9](https://www.gstatic.com/vertexaisearch/a2ui/v0_9/gemini_enterprise_composite_catalog.json)
- [A2A protocol](https://a2a-protocol.org/)
- [ADK](https://github.com/google/adk-python) · [docs](https://google.github.io/adk-docs/)
- [Gemini Enterprise](https://cloud.google.com/gemini-enterprise) · [Agent Platform](https://cloud.google.com/products/gemini-enterprise-agent-platform)
