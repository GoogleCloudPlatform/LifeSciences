# 5 · Demoing these agents

Written for whoever has to stand up and show this — at a hackathon, to a team, or
to a customer. Every timing here was measured, and every failure mode listed is
one that actually happened.

The short version: **demo BioCompass from the dev UI, and PaperBanana from the
Gemini Enterprise app.** That contrast is the story — one shows how you build,
the other shows what your users get, and `agents-cli` is what moves you between
them.

---

## Before you present

```bash
# terminal 1
cd agents/biocompass   && uv run adk web . --port 8501
# terminal 2 (fallback for the GE demo)
cd agents/paperbanana  && uv run adk web . --port 8502
```

Open `http://127.0.0.1:8501`, pick **`app`**, and **open the trace panel**.

Then three things people forget:

1. **Run one throwaway query on each.** The first call after a restart is slow
   (cold imports) and it warms the auth path. Do it before anyone is watching.
2. **Have your PDF ready** for PaperBanana. Any open-access paper works; one with
   a clear study design demos best.
3. **Decide your concurrency.** If you're the only person on the project, set
   `LLM_MAX_CONCURRENCY=8` for speed. If you're demoing *during* a workshop where
   others are building on the same project, leave it at 3 — see
   [.env.example](../.env.example).

---

## BioCompass — in the dev UI

Why not the app? Because in the dev UI you can **see it route**. A three-minute
run with a visible trace reads as *working hard*; the same three minutes behind a
spinner reads as *broken*. That single difference is worth the whole demo.

### Turn 1 — the light lane · ~35–55s

```
Find recent published trials of sotorasib in KRAS G12C-mutant NSCLC.
```

Expected trace:

```
root_agent              → transfer_to_agent
literature_search_agent → advanced_search
literature_search_agent → search_pubmed
                        → cited answer
```

Narrate it: the coordinator read the question, decided it was a light lookup, and
handed off rather than running the expensive path. Then **land on the citations** —
for a clinical audience, every claim carrying its source is the whole point.

### Turn 2 — the deep lane · 2–6 min · optional

```
My patient is 62 with metastatic KRAS G12C NSCLC, progressed on first-line
pembrolizumab + platinum. Second-line options, and toxicities to counsel on?
```

Trace shows `root_agent → DeepResearchPipeline`. **Start it, then talk over it**
using the architecture. Skip it entirely if you're short on time — turn 1 already
made the point.

> **Use turn 1's wording verbatim.** Routing is a model decision, not a rule. The
> instruction says to default to the deep path when an answer needs more than a
> single page, so anything phrased like clinical advice drifts into the slow lane.
> The same question has taken 31s once and 127s the next time. Rehearse the exact
> phrasing you'll use.

---

## PaperBanana — in Gemini Enterprise

1. Attach a paper PDF in the composer.
2. Ask in one plain sentence:
   *"Draw the study design for this trial — the arms, the dosing, and the primary endpoint."*
   About 90 seconds to a 4K figure.
3. **Do the refine turn:** *"Make the treatment arm clearer."*

Step 3 is the one people remember. It edits that part of the image and leaves the
rest alone — that's the difference between a toy and something a scientist would
use, because the second draft is where the real work happens.

While it renders, make the point that nobody opened a design tool and nobody
wrote a prompt template.

**Fallback:** the same agent on `http://127.0.0.1:8502`. Keep a previously
generated figure on disk as a last resort.

---

## Pacing and honesty about latency

These agents think for a while. Don't apologise for it — explain it.

| Path | Measured | What to say |
| --- | --- | --- |
| Light lookup | 30–57s | "One search, one sub-agent." |
| Deep research | 2–6 min | "Four sources in parallel, a synthesis, then a critic auditing every citation." |
| Figure generation | ~90–250s | "It's planning the figure in words before it draws." |

The concurrency cap trades latency for reliability: a light lookup runs ~30s at
`LLM_MAX_CONCURRENCY=8` and ~57s at 3. On a shared project, take the 57s.

---

## When it breaks

| Symptom | Do this |
| --- | --- |
| A turn sits >60s with **no new trace lines** | Abandon and resubmit. `adk web` has **no client-side timeout** — a hung model call will sit indefinitely and never recover. A fresh turn usually works instantly. |
| Turn 1 took the deep path | You reworded it. Resubmit exactly as written, or narrate over the deep run. |
| `429 RESOURCE_EXHAUSTED` | Shared quota. This kit's retry/backoff should absorb it — if it doesn't, lower `LLM_MAX_CONCURRENCY` and narrow the question. See [Troubleshooting](04-troubleshooting.md#429-resource_exhausted-with-no-quota-metric-named). |
| The agent in Gemini Enterprise returns nothing | Check the runtime logs, not the app. A failed branch surfaces as an `ExceptionGroup` and kills the invocation, so the pipeline "ran" but nothing came back. |
| Everything is on fire | Have a pre-generated figure and the architecture diagrams. A calm walk through the architecture beats a broken live demo. |

---

## Rehearsal checklist

- [ ] Both servers up, one throwaway query each
- [ ] Trace panel open and visible from the back of the room
- [ ] Font size up — people will want to read tool names in the trace
- [ ] Demo PDF downloaded locally, not fetched live
- [ ] A fallback figure saved to disk
- [ ] Exact prompts in a scratch file to paste, not typed live
- [ ] You know which question is fast and which is slow, and you've said so out loud before running the slow one
