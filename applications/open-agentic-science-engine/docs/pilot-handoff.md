# Handoff to the repository maintenance crew

**Audience:** the coordinator of the crew that will take feedback from pilot teams and
revise these materials. **Written:** 2026-08-18, at the end of the build, by the agents
who built it. **Status of the materials handed over:** v0.1. The pilot is expected to
change them.

This document answers one question: **what is the first pilot actually able to do, and
how should a report from it be read?** It does not describe the design — the design is in
[`dde-plan.md`](dde-plan.md), which is the source of truth, with the README as a
high-level recap.

---

## 1. Read the repository, not this document, for what exists

This document is a cache. It was written on a date and revalidated by nobody, and it
decays every time a tool or a skill lands. The repository answers the same questions in
ten seconds and cannot be stale:

```bash
ls skills/                                           # skills that exist
ls templates/                                        # agent templates that exist
grep -c uri: templates/*/scion-agent.yaml            # how many each template grants
grep -h uri: templates/*/scion-agent.yaml | sort -u  # which skills those are
dde doctor                                        # which tools are installed and callable
python3 tools/check_invocations.py                   # do our documented commands exist
```

**If those disagree with anything below, they are right and this is stale.** The same
warning is at the top of the README's status section, for the same reason, and the
`grep -c` form is deliberate: a bare `sort -u` prints nothing if the field is ever
renamed, and *nothing* reads as "no template grants any skill" rather than as "this
command stopped working."

No counts of tools, skills or relay codes appear in this document. That is a rule of the
house (`tool-design-guidance.md` §7) and it was learned the hard way: a hand-written count
of the repository was published in the section that forbids counts, was wrong, was
corrected in eleven minutes, and had already been copied into the title of a tracker issue
by then. The correction never caught the copy.

---

## 2. The shape of the limit: the pilot is one stage deep

The plan describes four stages of pre-clinical work. **The tools built so far serve
Stage 1** — identify and validate the intervention point. Hypothesis ingestion, structure
confidence, pocket detection, tissue expression, genetic constraint, regulatory variant
effect, citation resolution. There is no chemistry, no ADMET, no toxicology and no
regulatory tooling, because nothing downstream of target validation has been built.

This produces three consequences the crew will meet immediately, and only the first is
usually anticipated:

1. **Some roles have no capability at all.** Every template is granted
   `artifact-conventions`, and the program lead also holds `program-state-management`.
   Those are conventions, not capabilities. A template whose grant list contains only
   conventions holds no scientific tool, and the agent will correctly report itself
   blocked when asked to do scientific work. `medicinal-chemist`,
   `admet-dmpk-scientist` and `project-curator` are in that position today. **This is
   designed, not broken.** Do not fix it by granting a skill; fix it by building the
   tool, then the skill, then the grant, in that order.

2. **Coverage is uneven by role rather than uniformly early.** The computational
   biologist holds several capability skills; the experimental biologist holds one. A
   pilot team will read this as favouritism or as an oversight. It is neither — it is
   which tools happen to exist.

3. **A count of granted skills does not measure capability.** `grep -c uri:` counts
   conventions and capabilities alike. Read the URIs and decide per skill.

---

## 3. What "working" looks like, and why it will be reported as breakage

The single most important thing for a crew reading pilot feedback: **this system is built
to refuse, and a refusal is a success.** Several behaviours that look like defects are the
designed output.

- **Refusal has its own exit code.** A tool that refuses is not a tool that failed, and
  neither is a tool that answers "no". The exit codes are defined in the CLI's error
  module — read them there rather than from any table in prose. A pilot report of "the
  tool errored" should always be resolved to *which* code before it is triaged.
- **`dde doctor` ends with a verdict line, not a warning count.** `STOP` means fix
  before running anything; `PROCEED` means work. A healthy install carries warnings.
  "Doctor shows fourteen warnings" is not a bug report.
- **Ambiguity is refused rather than resolved.** A gene symbol matching two HPA entries
  is refused; the tool will not choose between WEE1 and WEE2.
- **Unknown inputs fail loudly rather than resolving to nothing.** HPA silently drops
  unrecognised tissue columns, so the CLI validates them first. The loud failure is the
  feature.
- **Some thresholds are declared `UNRESOLVED`** where no published cutoff exists, and
  requesting one is an error rather than a plausible default. Issue #12 carries two of
  them; the full set is whatever `core/thresholds.py` declares as `UNRESOLVED`, and that
  is the list to read rather than this sentence. A pilot team will ask for a number.
  **The correct answer is a citation or nothing.**
- **AF3 is single-flight.** One replica, so parallel specialists in separate containers
  collide with 429s. Cold starts return 429 for minutes and this is not failure. Plan AF3
  work sequentially; a campaign that fans out will look broken and will not be.
- **Credentials are required for some tools and absent by design for others.** AlphaGenome
  and the Vertex endpoint need credentials; AFDB, HPA and the local analyses do not.
  Credentials resolve through the CLI only. Never print one, and test for presence, never
  for value.

**Triage rule for the crew.** Every incoming report sorts into one of three bins, and the
bins have different owners:

| Bin | Signature | Action |
|---|---|---|
| Designed refusal | The tool declined and said why, in a registered code | Improve the message if it did not explain itself. Do not change the behaviour. |
| Missing capability | The tool does not exist for the question asked | Build order: tool, then skill, then grant. Never grant first. |
| Real defect | The tool answered, and the answer was wrong or unscoped | Fix, and ask what check would have caught it. |

The bin that will be mis-sorted is the first. It arrives worded as the third.

---

## 4. The invariants: what must survive revision

These are load-bearing. Revising them is possible but should be a decision, not a side
effect of an edit. Each is stated in full, with its reasoning, in the guidance documents.

- **The two-phase tool contract.** `fetch`/`run`/`ingest` writes Layer 0 bytes plus a
  `.meta.json` sidecar; `analyze` reads from disk, applies a named threshold set and
  writes `.analysis.json`. The consequence worth protecting: an analysis can be re-run
  against new thresholds without re-querying a paid or rate-limited endpoint, and the raw
  bytes remain reachable for a reviewer. Normalisation is lossy, so the original is kept
  beside it.
- **A relay is an instruction, not a caveat.** Registered relay codes travel with a value
  and impose an obligation on the finding — scope the claim, name the isoform, report a
  count and not a rate. A finding that omits a mandatory relay is *wrong*, not merely
  incomplete. The registry is `provenance.RELAY_CODES`; a relay that is not registered
  does not exist.
- **Thresholds are cited by name, never by value.** Three-level resolution: CLI default,
  program override in `.dde/thresholds.yaml`, invocation flag. The value in force is
  stamped into every `.analysis.json` along with its source. A specialist quoting a number
  quotes it from the analysis being cited. `tools/check_threshold_names.py` enforces the
  names; nothing can enforce a value copied into prose, which is why the rule is absolute.
- **The five artifact layers.** L0 raw tool I/O, L1 specialist findings, L2 program state,
  L3 stage gate documents, L4 executive summary. Everything a tool emitted stays under
  `raw/`. The layer boundary is what lets a reviewer re-derive a conclusion instead of
  re-reading it.
- **Computation in tools, judgment in skills.** A skill never computes a value. If the
  tool did not run, there is no value, and the task is blocked. This is the
  anti-fabrication guard and it is the reason the system is worth building at all.

## 5. The four gates

`tools/check_invocations.py`, `check_artifact_paths.py`, `check_skill_uris.py`,
`check_threshold_names.py`. Each exits 0 clean, 1 found-a-problem, 2 cannot-run. They are
cache-invalidation checks over documentation: do the commands we wrote exist, do the paths
we wrote resolve, do the granted skill URIs exist and match the pushed copy, do the cited
threshold names resolve in the right set.

**Run all four before accepting any documentation change.** They exist because prose about
code is a cache of that code, and every one of them found real breakage on the day it was
written. `check_invocations.py` is deliberately ignorant of relays, layers and phases: an
instrument aimed at the rule its author had in mind confirms what its author expected.

Two of them are worth understanding rather than just running. `check_artifact_paths.py`
exists because `check_invocations.py` passed a file clean while four invented directory
names sat in its tree diagram — an instrument only finds what it is pointed at.
`check_skill_uris.py` checks the pushed copy as well as the working tree, because a
template can grant a skill that exists locally and nowhere else.

---

## 6. What we learned about how defects get found

This is the finding I most want carried forward, because it is a statement about process
rather than about code, and it is supported by the whole build.

**Nobody caught their own defect.** Three authors, three defects in their own work, zero
self-catches:

- The author of the cached-value rule reintroduced a cached value within an hour of
  writing it, and it propagated to eight skills in under a minute. Knowing the rule, and
  having just committed it, did not prevent it.
- I published a wrong count inside the section that forbids counts.
- The author of the duplicate-caveat rule wrote a duplicate caveat, and a pointer that
  did not resolve.

Every one was found by a second party, and in each case by **re-deriving the claim, never
by re-reading it.** Re-reading is done by someone who already believes the work, so it
confirms. The practical instructions that follow:

- **Wire the second reader into the flow.** A specialist checking their own finding is the
  case we have evidence does not work. "Review carefully" is not a control.
- **Do not read the healthy case.** The strongest defect found during the build was a
  preflight check that consulted `import venv` — which succeeds on Debian precisely when
  the thing being checked is broken. It was found by injecting a shim, not by running the
  test that would pass. **A proxy that is true in the failing case is not a weak check; it
  is an inverted one, and it reads green forever.**
- **Ask a third question of every check:** could the thing I am consulting be true for the
  same reason the thing I care about is false?

---

## 7. Open items

The tracker is the list; `gh issue list` is authoritative and this paragraph is not. At
handoff the open set covers: relay coverage gaps and taxonomy (#1, #2, #3, #5, #15),
deferred tool capabilities (#14), unresolved thresholds (#12), bootstrap packaging (#13),
the environment-cost stamp being blind to build-machine cost (#18), stage-gate threshold
provenance (#16), two design decisions needing Preston (#4, #6), a shared-checkout commit
hazard (#11), a documentation-reader calibration point (#8), and a scion messaging bug
that belongs upstream (#17).

**Two things about the tracker itself:**

1. **The owner names in the issue bodies point at agents that no longer exist.** We wrote
   them as a convention because the whole team shared one GitHub token and the tracker
   could not attribute otherwise. They are reliable as *attribution* — who found it — and
   are not assignments. Do not route work by them.
2. **#4 and #6 need Preston, not the crew.** They are design decisions, not defects.

---

## 8. Hazards in the working environment

Practical, cost us time, and none of them are obvious:

- **The workspace may be a shared checkout** (`SCION_WORKSPACE_MODE=shared-plain`). Stage
  explicit paths, never `git add -A`, and commit with `git commit --only <paths>`.
- **`git commit --only <paths>` takes those paths from the working tree, not the index.**
  A partially staged file commits whole. Pre-commit review is `git diff -- <paths>`, not
  `git diff --cached`. This is issue #11.
- **Backticks in a `scion message` body are shell command substitution.** If the command
  fails, words vanish; if it succeeds, its stdout is spliced into the message. The send
  reports success either way. The danger is inverse to the noise: the silent mode is the
  one that inserts plausible text. Issue #17, upstream in scion.
- **`gh issue comment --edit-last` appended a second comment instead of editing**, and
  returned a URL that made it look like an edit. Verify with `gh issue view --json
  comments`, not with the exit code.
- **Clean and pushed are different claims.** Two final commits sat local in a shared tree
  under a report of "tree clean". Check `git status -sb` for the ahead marker.

---

## 9. What we expect the pilot to change

The three guidance documents (`tool-design-guidance.md`, `skill-design-guidance.md`,
`orchestration-design-guidance.md`) are v0.1 and were written before any program ran. The
parts most likely to be wrong are the ones no pilot has stressed yet: the stage-gate
documents, the reasoning cadence, and the assumption that the reader of a failure message
is a human who will report it rather than an agent who will route around it (#8).

The parts least likely to be wrong, because they were each established by a defect rather
than by an opinion, are §4 of this document.

---

## 10. First-pilot data points

The following concrete data points were observed during the first pilot (target
gene: SPDYA) and were originally cited inline in general skill and template
documentation. They are preserved here as reference for anyone revisiting the
first pilot's history.

- **Target gene constraint (gnomAD v4)**: SPDYA showed pLI 0.985 and
  LOEUF 0.496 — a split verdict where pLI indicated intolerance but LOEUF
  indicated tolerance. This was the motivating example for the split-verdict
  documentation in the `target-genetic-evidence` skill.
- **Co-Scientist tournament export**: the first pilot's tournament was a
  partial export — 5 of 72 generated ideas were present. All 16
  deep-verification claims in the export carried negative verdicts
  (12 INACCURATE, 4 LEANING_INACCURATE). These numbers motivated the
  denominator caveat and `claim_denominator` relay in the `tournament-corpus`
  skill.

---

**One request.** When the pilot produces a finding that contradicts a rule in the
guidance, record the *instance* alongside the change. Nearly every rule in those documents
carries the case that produced it, and the examples are load-bearing: a reader recognises
the failure by its example and then applies the rule. Removing an example to tighten a
document looks like an improvement while it happens, and has no detector. Replace them
rather than remove them.
