# The two starting agents

Both come from
[GoogleCloudPlatform/LifeSciences](https://github.com/GoogleCloudPlatform/LifeSciences/tree/main/applications/pharma-on-gemini-enterprise),
Apache-2.0. Run `../scripts/get-agents.sh` to fetch them here.

They were chosen because between them they demonstrate every pattern you'll need
this afternoon.

---

## BioCompass — evidence in

![BioCompass three lanes](../docs/architecture/biocompass-lanes.png)

A citation-grounded literature research agent. A coordinator sorts each question
into one of three lanes:

| Lane | Path | Time |
| --- | --- | --- |
| **Light lookup** | transfers to a sub-agent that searches PubMed | seconds |
| **Entity analysis** | PubTator3 for drug / gene / disease relationships | seconds |
| **Deep research** | PubMed, Europe PMC, bioRxiv/medRxiv and ClinicalTrials.gov **in parallel**, then synthesis, then a citation critic | minutes |

Every lane ends the same way — an answer where each claim carries the study it
came from.

**Patterns to steal:** a coordinator that decides how much machinery a request
deserves · a `ParallelAgent` fan-out where each branch writes to its own state key
· a critic pass that audits the answer before the user sees it · methodology
captured as **skills** loaded on demand rather than stuffed into a prompt.

---

## PaperBanana — evidence out

![PaperBanana pipeline](../docs/architecture/paperbanana-pipeline.png)

Attach a paper PDF, describe the figure you want, get a publication-style 4K image.
Plan → stylize → render → critique → refine. It writes a figure plan in words
before drawing, and critiques its own output before you see a draft.

**Patterns to steal:** wrapping a multi-step pipeline as a **single tool** so the
enterprise app renders one clean answer instead of five agents talking to each
other · feeding the previous image back in so a follow-up **edits** rather than
redraws · a critic loop on a *visual* output.

---

## What this kit changes

**Right now: nothing.** Upstream `main` is already correct, and `get-agents.sh`
gives you a clean copy.

The script does carry two **guards** that run on every fetch. Both are currently
no-ops — they exist because both bugs bit real deployed agents, and they'll catch
the problem if you fetch an older commit, work from a fork, or upstream regresses:

| Guard | The bug it prevents |
| --- | --- |
| Pin `mcp>=1.24,<2` if the agent imports `McpToolset` | Unpinned, resolution picks mcp 2.x, which removed `mcp.shared.session`. ADK swallows that import error and reports a **misleading** `cannot import name 'McpToolset'`. The container then can't start — and only on *restart*, months after a deploy that worked. |
| Rewrite `gemini-3-pro-image-preview` → `gemini-3-pro-image` | The `-preview` ID was withdrawn at GA. A deployed agent pinned to it 404s the first time the image tool is called — typically mid-demo, not at startup. |

The value here isn't the patches, it's knowing *why*. Both are written up in
[Troubleshooting](../docs/04-troubleshooting.md), along with the more general
lesson: **a deployed agent's environment overrides your code**, so fixing a model
ID in git changes nothing until you redeploy.

Run `git -C agents/<name> status` after fetching to confirm nothing was modified.
`UPSTREAM.txt` in each agent records the exact commit you got.

---

## Which should I start from?

- Building something that **answers questions over a corpus** → start from BioCompass.
- Building something whose **output is an artefact** (figure, document, report) → start from PaperBanana.
- Building something that **routes work between specialists** → BioCompass's coordinator is the cleanest example.
- Starting from nothing → `agents-cli create` and borrow patterns from both.

More reference agents in the same upstream repo: **FoldRun** (protein structure
prediction orchestration), **Sentinel** (regulated content review), **Model
Garden** (third-party model bridge into Gemini Enterprise).
