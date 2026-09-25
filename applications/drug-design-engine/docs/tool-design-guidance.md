# DDE Tool Design Guidance

**Status**: v0.1. Expect revision after the pilot conversion (co-scientist, AlphaFold, AlphaGenome).
**Purpose**: the standard for the dde tools environment and the `dde` CLI — bootstrapping, the two artifact layers, output placement, thresholds, and stdout discipline.
**Companion**: [`skill-design-guidance.md`](skill-design-guidance.md). This document is **normative for the artifact contract**. The skill document references it rather than restating it.
**Context**: [`dde-plan.md`](dde-plan.md) §5 and §7.

The CLI also enforces work-order schemas, legal run-state transitions, mechanical
artifact intake, and deterministic presentation builds. Those operational commands
follow the same rule used here — deterministic mechanism in code, judgment in the
responsible agent skill — while their semantics are normative in
[`orchestration-design-guidance.md`](orchestration-design-guidance.md).

---

## 1. Four tiers, with clean ownership

| Tier | Owns | Produces |
|---|---|---|
| **1. Environment** | Dependencies, binaries, reference data, credential storage | Shared venv + lockfile; `dde doctor` |
| **2. CLI (`dde`)** | Execution, rate limits and leases, retry, provenance stamping, artifact naming, threshold values, control-state validation, presentation builds | Layer 0 artifacts, sidecars and analyses; validated control records and presentation output |
| **3. Skill** | Routing, preconditions, invocation table, interpretation, anti-fabrication guard | Prose only |
| **4. Template** | The capability grant (`skills:`), the questions a role owns, role-specific next actions and handoffs | `scion-agent.yaml` + `agents.md` |

Tier 4 is easy to forget, and forgetting it is what pushes role-specific content into skills. One capability serves several specialists, so the skill must stay specialist-neutral. See `skill-design-guidance.md` §3.

One rule holds the whole thing together: **the CLI is the execution surface, not the routing surface.** Forty tools behind one `--help` is not navigable, and a CLI has no progressive disclosure. An agent either knows the subcommand or it does not. Routing stays in skill descriptions, which are always in context and cost little. Never assume an agent will read `dde --help` to find its way.

### 1.1 Choosing the source: a canonical model, or a primary measurement?

Before any of the below applies, something has to decide **what a tool should
wrap**. A standing steer to favour the canonical models needs a tie-breaker for
the common case where a database and a model both appear to answer the same
question. These five tests were derived in `EVIDENCE_TOOLS_BRIEF.md` §2.2 and
applied to the pilot's candidate list.

**They are adopted as tie-breakers, not as gates** (Preston, 2026-08-18, Group B
item 5, option B). The reason is a limit on the evidence behind them: the pilot
exercises a small subset of the program types dde will run, so a test that
looks decisive across the pilot's candidates may be decisive only about the
pilot. A tie-breaker that turns out to be wrong for a new program type is
argued with. A gate that turns out to be wrong for a new program type blocks
work and gets routed around, and a rule that is routed around stops being
read for the cases where it was right. Apply the five in order, expect to
argue with them, and record the argument where the decision is recorded.

**The standing steer decides what a tie-breaker cannot: all other things equal,
choose the DeepMind option** (Preston, same decision). This is the tie-breaker
of last resort and it fires only where the five tests are **indecisive** —
where two candidates answer the same question and no test separates them. It
does not overrule a test that has separated them. In particular it does not
overrule Test 1: a canonical model is not preferred over an existing primary
measurement, because that is not a case of all other things being equal.

**It is equally bounded against the sixth question below** (tooling-lead, who
pays this cost). The steer chooses **between candidates**; it is not a reason
to **admit** one. Read unbounded in that direction it would say that a DeepMind
tool justifies an environment event an equivalent non-DeepMind tool would not,
and the sixth question is expensive precisely because the cost is program-wide
and indifferent to whose model it is. The steer fires after we have decided a
tool should exist, and is silent on whether it should.

**Test 1 — Has somebody already measured this?** If a primary source answers
the question directly, use it. A model trained to predict a quantity is not a
better source for that quantity than the measurement. *Sharper, and this is the
form that does the work:* if the measurement is likely **inside the model's
training data**, calling the model **launders a database lookup through an
inference step** — the same information with weaker provenance and no citation.

**Test 2 — Is the question counterfactual or prospective?** *What happens if we
knock this out. What would this unobserved substitution do. What will this
compound's pIC50 be.* No measurement can exist. A model is the only instrument;
use the canonical one.

**Test 3 — Does the primary source cover this entity?** **Coverage, not
existence, decides.** A database holding nothing for our target does not answer
the question, and a model is then the honest fallback, labelled as prediction.
This is the same rule as *an instrument must state its coverage, not only its
findings* (§3) applied one level up, to the choice of instrument.

**Test 4 — Is the output reproducible?** Layer 0 must be re-analysable. A
pinned database release re-reads identically forever; a stochastic generative
model does not unless seed and version are controlled. Where reproducibility
cannot be guaranteed, the artifact is weaker and **the sidecar must record
why** — not as a caveat, but because a later reader otherwise cannot tell an
irreproducible artifact from a reproducible one.

**Test 5 — What is the decision weight?** For a claim that will gate a stage
decision, prefer the measurement even at a cost in coverage or convenience.

**Two standing rules, independent of the tests — and not softened by adopting
the tests as tie-breakers.** These are not selection criteria. They govern what
Layer 1 may say once a source has been chosen, whichever source that is, so
they bind in every case including the ones the five tests decline to settle.

- **Never merge the two classes into one number.** A measured value and a
  predicted value are different kinds of evidence, and Layer 1 must keep them
  distinguishable. Where one tool emits both, a relay code enforces it.
- **A prediction never overrides a measurement.** When a model and a primary
  source disagree, *the disagreement is the finding.* It is reported, not
  resolved by preference. Same principle as a second analysis that contradicts
  the first: two verdicts that disagree are evidence, and one of them silently
  replaced is not.

**What makes this procedure worth keeping is that it produced a falsifiable
claim rather than a preference.** Test 3 is where AlphaMissense would have
earned a place, and it failed on a specific, checkable prediction: gnomAD
covers the initial candidate targets. That was verifiable, was verified, and would
have reversed the decision had it been false. A selection rule that only ever
yields "prefer X" cannot be wrong and therefore teaches nothing. State the
coverage claim the decision rests on, and go and check it.

**A sixth question sits above all five: should there be a tool at all?** The
cost of adding one is not the build — it is the partition (§2, Rule 4). A new
dependency changes the shared environment, an environment change is a
program-wide event, and every artifact stamped before it is now stamped
differently from every artifact stamped after. Five tests that all say "build
it" have still not established that the answer is worth that.

---

## 2. Bootstrapping: the shared environment

The environment lives on a scion shared volume, not baked into container images:

```
/scion-volumes/tools/
├── .venv/                 # Python environment (RDKit, numpy, requests, click, …)
├── bin/                   # Non-Python binaries: vina, fpocket, obabel wrappers
├── data/                  # Reference data too large to fetch per run
├── requirements.lock      # Fully pinned, hash-locked
└── ENV_VERSION            # Hash of the lockfile + bin manifest
```

This removes the image rebuild cycle from the tool inventory. Adding a tool becomes additive instead of release-managed.

But a mutable environment shared by every agent is shared mutable state. Four rules make it safe.

### Rule 1 — read-only at run time, single writer

Specialists never install. No `pip install`, no `uv add`, no `apt`. Provisioning happens through one path only: a bootstrap agent or a deliberate `make env` target. Never as a side effect of a specialist hitting a missing import.

This matters more than it looks. An agent that can install packages can silently change the scientific behaviour of every other agent mid-program. That is the same class of risk as fabrication — an undetectable change to what a number means.

### Rule 2 — pin the interpreter and architecture

A venv carries compiled extensions built against a specific Python minor version, glibc, and CPU architecture. If agent containers diverge on any of those, the shared venv fails. Sometimes loudly, sometimes not.

Either every dde template uses one base image, or the venv path is keyed: `/scion-volumes/tools/py3.12-x86_64/.venv`.

### Rule 3 — know what the venv cannot carry

Pure-Python and self-contained wheels are fine: RDKit, numpy, requests. Static binaries drop into `bin/` fine: Vina.

What does not work is anything needing system shared libraries the container lacks — OpenBabel, some PyMOL builds, anything wanting `libGL`, `libXrender`, or boost. For those, use a self-contained wheel variant, or accept a small documented set of `apt` packages in the base image. Enumerate that set explicitly. Do not discover it as a runtime failure.

### Rule 4 — stamp the environment version into every artifact

The environment is mutable, so reproducibility requires knowing which version produced a result. Write `ENV_VERSION` into every `.meta.json` sidecar.

A finding produced before a scoring-function upgrade is then distinguishable from one produced after. Otherwise the program silently mixes them.

**Which makes an environment change a program-wide event, not a tool-level one.** Adding
one binary to `bin/` bumps `ENV_VERSION`, and every sidecar already written now records a
version the environment no longer has. That is the mechanism working — the alternative is
a program that mixes them silently — but it means the cost of adding a tool is not the
build. It is the partition. Schedule environment changes at program boundaries, do them
deliberately and once, and do not add a tool mid-program because a specialist wanted it.

### `dde doctor`

Run it at agent start, from every template. It asserts that each declared tool is present and executable, that each required credential resolves, and that the environment version matches what the CLI expects. It reports what is missing and exits non-zero.

This is the highest-value component in the toolkit. It converts a missing tool from *"the agent invents a plausible number"* into *"the agent reports a blocked task."* The shared-volume approach makes it **more** important, not less, because the environment can now change under a running agent.

Credentials resolve through the CLI only. Never printed, never echoed. Test for presence with `grep -sq "^NAME=" ~/.env`. Never test for value.

**A rule an agent cannot check is not a rule.** "Schedule environment changes at program
boundaries" was unenforceable as written: `ENV_VERSION` was stamped into every sidecar,
each one individually correct, but nothing ever compared them — so the partition existed
only *across* files nobody read together, and a specialist could not see which side of a
boundary it was standing on. `doctor` now groups a project's sidecars by `env_version`
and reports when a project spans more than one. It found a real split on its first run:
four artifacts written before the shared volume was provisioned, including the ingest
that everything downstream derives from. `doctor` is where a rule of this kind acquires
an instrument, and running it at agent start is what makes the answer cheap — the
alternative is discovering the split when two findings disagree.

Two things that check generalises:

- **Make the remedy narrow, because a vague one invites the wrong repair.** Here the
  wrong repair is re-stamping a sidecar, which destroys the only record of what produced
  the bytes. The two-phase split gives the right one, and it is asymmetric: an
  **analysis** can be regenerated under the current environment, because `analyze` reads
  the stored Layer 0 bytes; a **phase-1 payload** cannot be re-dated and must be
  re-fetched to cross the boundary.
- **A partial scan reported as a whole one makes the checker itself a cache.** Unreadable
  sidecars are reported separately rather than skipped, and the scan cap is reported when
  it is hit.

**Keep provenance beside the identifier, not inside it.** `ENV_SOURCE` records the commit
an environment was provisioned from, and `doctor` checks that commit is reachable on
`origin` — not merely that it matches the local tree, because a commit that exists only
locally does not exist to anyone provisioning from the repository (tooling-lead,
`61cdb25`). The design point is where the commit is *not*: it is not folded into the
hashed manifest that produces `ENV_VERSION`. Folding it in would bump the version on every
documentation commit and partition the program's artifacts over a change no artifact
depends on — the mirror image of the defect the stamp was written to fix.

**An identifier that fires on what does not matter is as useless as one that misses what
does, and worse in practice, because people learn to ignore it.** The practical test:
re-stamping the live volume to add the provisioning commit left `ENV_VERSION` unchanged,
so adding provenance cost zero partition. Had the commit been inside the manifest,
improving traceability would have invalidated the comparability of every artifact written
that day. **When the cost of an improvement is paid by everyone except the person making
it, the improvement does not get made** — so check where a new field sits before checking
whether it is correct.

Three outcomes again, and the third is the one that would have been missed: reachability
is unknown, not false, when there is no repository to ask. A specialist running from the
volume alone cannot observe it. Reporting OK for having examined nothing is the vacuous
pass in §8; `doctor` reports *reachability not checked* instead, and all four branches
were negative-tested against synthetic records rather than assumed.

---

## 3. Two phases, two artifact layers

Every tool that produces interpretable output splits into two subcommands.

```
dde alphafold fetch P00520
  → raw/structures/AF-P00520-F1.cif
  → raw/structures/AF-P00520-F1.pae.json
  → raw/structures/AF-P00520-F1.meta.json      # provenance sidecar
  stdout: the three paths, nothing else

dde alphafold analyze P00520
  → raw/structures/AF-P00520-F1.analysis.json  # structured verdict
  stdout: bounded summary (see §6)
```

**Phase 1 (`fetch` / `run` / `submit`)** performs the expensive or non-deterministic act: a network call, a model inference, a docking run. It writes the artifact and its sidecar. It makes no judgment.

**Phase 2 (`analyze`)** reads the artifact from disk, applies thresholds, and emits a structured verdict. It is deterministic, cheap, and repeatable.

### Why the split is non-negotiable

- **Layer 0 exists and is auditable.** Collapse the phases and the raw payload becomes a side effect nobody validates. That removes the anchor for the whole provenance architecture.
- **Re-analysis without re-querying.** Critical when a program changes a threshold. Valuable for expensive calls — AF3 at 5–10 minutes cold, co-scientist at about 2 hours.
- **The `scientific-reviewer` role becomes implementable.** A reviewer re-runs `analyze` against the stored Layer 0 artifact and diffs the result against what the specialist claimed in Layer 1. That is a mechanical fabrication check. It exists only if the phases are separable.
- **The counter-example is in the scratchpad.** The `pharma_skills` CLI has the collapsed shape: one call emitting raw payload and interpreted finding together. That interface is a large part of why its fabrication is invisible. There is no point at which raw data exists independently of the claim made about it. Do not inherit it.

### Phase 2 is offline, and that is enforced rather than observed

Two of the dividends above — free re-analysis under a changed threshold, and the
reviewer's mechanical fabrication check — hold only while `analyze` never touches the
network. A convenience lookup added to a phase-2 command would remove both, silently, and
leave every artifact still looking correct. The rule was true only because every author
so far had happened to write it that way.

It is now enforced: the shared HTTP client latches offline for the remainder of any
phase-2 invocation and raises `PhaseContractError` naming the blocked call.

**Enforce at the root of the command tree, not per command.** A decorator on each
`analyze` would have to be remembered by exactly the author who forgot the rule — an
opt-in guard, solicited from the person most likely to opt out. Walking the registered
tree once at the root means a tool added next month inherits the guarantee without its
author knowing it exists. Every `analyze` command in the tree is guarded today, including ones the
change did not touch, and the count rises on its own as commands land. Generalise this: **an instrument that must be re-added at
each call site is not an instrument, for the same reason that a rule an agent cannot
check is not a rule.**

**Key the guard on the contract, not on a heuristic.** The latch triggers on the command
name `analyze`, which this section names normatively — so a phase-2 command called
something else is already outside the contract and should fail review on that basis. Walk
the registered command tree rather than `argv`, so that an argument value cannot trip it.

The name rule takes the prefix, not the exact string: `analyze-prediction` and
`analyze-ism` are phase 2 as squarely as `analyze` is, and matching the bare name left
both unguarded — the guard held for the commands whose names someone had remembered,
which is the defect it was written to remove. A tool needs more than one analyser as soon
as it has more than one kind of phase-1 output, so this is the normal case, not an
exception to it.

**Which qualifies the rule above, and the qualification is the more useful half.** Moving
a guard from the call site to the root removes the failure where *someone must remember
to apply it*. It does not remove the failure where *someone must have anticipated the
shape*. The second is much quieter, because an instrument reports on what it covers and
says nothing at all about what it misses: the latch passed every test, guarded six
commands, and was silent about the two it did not recognise. So the root-level guard
needs one more property — **an instrument must state its coverage, not only its
findings.** *N of N* commands guarded is a claim that can be checked and can be
wrong — and it is written here as *N* on purpose, because a literal count copied into
prose goes stale in the one direction nobody guards against: it makes a healthy system
look broken, so the reader raises a discrepancy against a correct run. Have the
instrument print the number; do not cache it in a document. "No violations found" is not. `doctor` reporting its scan cap and its unreadable
sidecars is the same discipline, and the relay check reporting *19 codes, no orphans*
rather than *no orphans* is a third instance.

**And the coverage question cannot be answered by the author at the moment of writing.**
Writing the enforcement is when a property feels most complete, which is exactly when one
is least curious about what else it needs — the offline latch was read as sufficient for
the reviewer's check *by the person implementing it*, and keyed on the bare name
`analyze` by the person who had just argued against per-name guards. Neither was
carelessness, and neither would have been caught by looking harder at the thing being
built.

What has actually worked is crossing the axes on purpose: **the instrument that finds a
coverage gap is almost never the one aimed at it.** The environment partition surfaced
while costing an unrelated tool build. A stale claim in the pilot plan surfaced while
auditing which rules had instruments. A missing guard surfaced by running `doctor`
instead of re-reading the list it was derived from. So the practice is not *review your
own coverage* — it is to check a rule with an instrument built for a different rule, and
to treat any audit that confirms what its author expected as not yet having been tested.

### Phase 2 reads and writes different directories

Offline is not sufficient for the reviewer's check. `analyze` originally took one
directory for both its input and its output, so the reviewer's re-run overwrote the
`.analysis.json` it was auditing, and any diff after that compared their own output with
itself. Redirecting the output with `--out` did not help, because `--out` redirected the
lookup too and the input was then not found. Both halves were verified before the fix: the
sha of the audited artifact changed under a plain re-run, and the `--out` form exited 3.

So the read path and the write path are separate flags:

- Commands that take an **identifier** carry `--from` (where phase 1 wrote) and `--out`
  (where this verdict goes). Neither is searched and neither falls back — an input
  directory that does not hold the artifact is an error, not a cue to look elsewhere.
- Commands that take a **path** need no `--from`; the input is already named. They still
  take `--out`, defaulting to beside the source, because writing the verdict next to the
  artifact it read is the destructive half on its own.

A second opinion must be producible without destroying the first one. Any phase-2 command
whose only output location is its input location cannot deliver that, however offline it
is.

The reviewer role then has an invariant it can check rather than a property it must
trust. Stated in two clauses, because the obvious single clause is too weak:

1. **No file that existed under `raw/` before the review has changed.** Snapshot the
   checksums before and after, and compare.
2. **The only new files under `raw/` are under `raw/reanalysis/`.** Without this, a
   reviewer writing directly into `raw/genomics/` satisfies clause 1 and still
   contaminates the evidence directory.

A re-analysis is Layer 0 whoever produced it, so it stays under `raw/` and nobody has to
decide which layer it belongs to. Naming the directory for the bytes rather than for the
role means a specialist re-running under a changed threshold writes to the same place
instead of inventing a fourth convention.

**Partition that directory by something two opinions cannot share.** A path keyed only on
the date, holding a filename derived only from the query, gives two reviewers of the same
artifact on the same day one path — and the second silently overwrites the first. That is
the defect this whole subsection exists to fix, displaced one level up: a location built
to hold independent opinions must not be keyed on an attribute independent opinions have
in common. Add the agent identity or a run identifier to the path, and have the write
refuse rather than clobber. **A phase-2 command should never overwrite an existing
analysis without being told to** — the destructive case is always the one nobody
predicted.

**An artifact under audit is temporarily immutable in a way it is not otherwise.**
This is a class of its own, and it caught a proposal of mine. When `analyze` finds an
existing record it agrees with, the informative thing to do looks like appending the
confirming agent to a `confirmed_by` list: an independent confirmation is real evidence,
and simply not writing discards it. That reasoning is sound about the general case and
wrong here, because for the duration of a review the file is subject to a
checksum invariant, and **an integrity sweep cannot distinguish a benign append from
tampering.** So the append does not merely fail to help — it destroys the guarantee the
review exists to provide, in exchange for information obtainable by other means.
Generalise it: during a review, operations that are safe and even desirable in normal
use become unsafe for the duration, and the test is not "does this corrupt the data" but
"can a checker tell this apart from corruption". `dd4d65b` resolves it correctly by
writing nothing at all.

That fix then creates the shape below, which is worth seeing as a shape.

This belongs in the reviewer's own procedure as well as in the CLI, because a guarantee
the reviewer verifies is worth more than one the reviewer inherits.

**Implemented at the write, not at the path.** `write_analysis` refuses to replace an
existing `.analysis.json` that says something different, exiting 9 with a remedy naming
both `--out` and `--overwrite`; `--overwrite` is injected into every phase-2 command by
the same root walk that latches the network, so no command author has to remember it.
Putting the guarantee at the write means it holds whatever the path scheme turns out to
be — a convention can be mistyped, forgotten, or superseded, and the refusal still fires.
The path convention (`raw/reanalysis/<date>-<agent>/`) then serves the reader rather than
carrying the guarantee.

Two details that make it live with rather than fight the workflow:

- **Agreement is not a conflict.** A re-run producing the same verdict rewrites silently.
  Only `timestamp` and `written_by` are excluded from the comparison, so the question
  asked is exactly *would this change the verdict on record* — the case where somebody's
  citation is about to stop matching the file it cites.
- **Unreadable counts as conflicting.** A file that cannot be parsed cannot be shown to
  agree, and "could not check" must never resolve to "went ahead".

The record also carries `written_by`, so *whose* second opinion a file holds is answerable
from the artifact rather than from the directory it happens to sit in.

### Not every tool has a phase 2

This is guidance, not a hard rule. Where multiple phases genuinely exist, split them and never collapse them. Where they do not, do not manufacture them.

`analyze` earns its place when there is real threshold or heuristic judgment to apply. For a pure metadata lookup — retrieving a UniProt record — phase 1 alone is correct, and the interpretation is the agent reading a small JSON. A no-op analyzer costs a subcommand, a doc line, and a place for drift, and buys nothing.

The test: *would a program ever want to re-derive this conclusion without re-fetching, or with different thresholds?* Yes → phase 2. No → phase 1 only.

Some tools have more than two phases. A docking campaign is plausibly `prepare` → `run` → `analyze`, where `prepare` produces a protonated, minimized receptor that is itself a citable Layer 0 artifact. The principle generalizes: every phase boundary that produces something worth auditing separately should be a separate invocation.

### Everything a tool emits is Layer 0

Model output formats and `.analysis.json` are both raw. `findings/` is for the prose conclusion of a decision step.

`.analysis.json` is deterministic machine output. It is derived, but it is not interpreted. No professional judgment produced it, and re-running the same analyzer on the same artifact yields the same file. `findings/` is Layer 1: what a specialist concluded, in prose, having weighed that output against the program's context.

The boundary matters because the whole provenance scheme rests on it. If script output can land in `findings/`, then "computed" and "judged" become indistinguishable in the artifact tree, and a reviewer can no longer tell which claims a tool derived and which a model did.

Keep every byte a tool wrote under `raw/`. The reviewer's check is then mechanical: re-run `analyze`, diff against the stored artifact, then read the finding and ask whether the prose is supported by it.

In practice: `raw/` accumulates the `.cif`, the `.pae.json`, the `.meta.json`, and the `.analysis.json`. `findings/structural-biology/target-tractability.md` is one markdown file that links to all four.

---

## 4. Output paths: resolve the project root, do not trust CWD

Agents are unreliable about working directory. Upstream hit this and solved it with prose: *"Always specify `-o` with an absolute path or a path relative to the user's project root, never a path relative to the skill directory."* Solve it in code instead.

The CLI discovers the project root the way git does:

1. `$DDE_PROJECT` if set, else
2. walk up from CWD looking for a `.dde/` marker directory, else
3. fail with a clear error. Never silently write into CWD.

**Prefer `.dde/` walk-up discovery over hardcoding `DDE_PROJECT`.** When each program gets its own scion project and the agent's workspace *is* the program directory, the `.dde/` marker in `/workspace` is found automatically — no `env:` block needed. Templates should omit `DDE_PROJECT` and let walk-up handle resolution:

```yaml
# DDE_PROJECT is resolved by .dde/ walk-up discovery by default.
# Set it explicitly only when the agent's workspace is not the program
# directory — e.g. when /workspace is the dde repo:
#   env:
#     DDE_PROJECT: /workspace/program-hr-mbc
```

Walk-up also covers: a specialist working in a subdirectory, a reviewer operating on an archived program, or local development.

**When `/workspace` is the dde repo itself** — as it is during maintenance or development of dde — it is not a program directory, and writing `raw/` into it would pollute the repo. The CLI detects this (via the `.dde-repo` marker) and refuses. In this case, set `DDE_PROJECT` to the program subdirectory (e.g. `/workspace/program-hr-mbc`). Add the program directory names to `.gitignore` as a second line of defence.

### Default directories

All default output paths are relative to the project root, with one directory per artifact class:

| Class | Default directory |
|---|---|
| Structures | `raw/structures/` |
| Docking | `raw/docking/` |
| Compounds | `raw/compounds/` |
| Assays | `raw/assays/` |
| Literature | `raw/literature/` |

`--out` overrides. The default should be right often enough that skills rarely need to pass it. Defaults are what keep artifacts from scattering. Every path an agent has to choose is a path it can choose wrong.

Naming is deterministic and derived from the query, not from a timestamp or a counter: `AF-P00520-F1.cif`, not `structure_3.cif`. A deterministic name makes idempotent re-runs and cache hits free, and makes a path in a Layer 1 finding resolvable by a reviewer later.

---

## 5. The provenance sidecar

Every phase-1 artifact gets `<name>.meta.json` alongside it:

```json
{
  "tool": "alphafold-db",
  "subcommand": "fetch",
  "cli_version": "0.2.0",
  "env_version": "sha256:8f3a…",
  "endpoint": "https://alphafold.ebi.ac.uk/api/prediction/P00520",
  "parameters": {"uniprot": "P00520"},
  "timestamp": "2026-08-18T14:02:11Z",
  "outputs": [
    {"path": "AF-P00520-F1.cif", "sha256": "…", "bytes": 481203}
  ],
  "warnings": ["Fell back to the secondary endpoint after one 503"],
  "mandatory_relays": [
    {
      "code": "afdb.isoform_substituted",
      "message": "Analysed isoform 1; the canonical sequence differs at 3 positions"
    }
  ]
}
```

`warnings` holds anything the operator may want to know. `mandatory_relays` holds the subset that must reach the Layer 1 report unchanged, because a finding that omits one is wrong rather than merely incomplete.

Phase 2 emits `<name>.analysis.json`, which cites its inputs and the thresholds applied, and carries forward any relay that still applies:

```json
{
  "source": "AF-P00520-F1.meta.json",
  "cli_version": "0.2.0",
  "env_version": "sha256:8f3a…",
  "threshold_set": "default@1.2",
  "thresholds_applied": {"plddt_confident": 0.7, "plddt_moderate": 0.4},
  "metrics": {"mean_plddt": 0.83, "fraction_below_50": 0.11},
  "mandatory_relays": [
    {
      "code": "afdb.partial_coverage",
      "message": "Model covers 1725 of 8797 aa; metrics describe the modelled region"
    }
  ],
  "assessment": {
    "verdict": "confident",
    "ordered_domains": [[62, 121], [242, 493]],
    "advisories": []
  }
}
```

### 5.1 Relay codes

A relay carries a stable code as well as prose. The code is what makes the reviewer
check mechanical. Prose is reworded between builds and cannot be matched on. A skill
names a code; a reviewer enumerates the codes in an artifact and confirms each one was
addressed in the finding.

Four rules keep the mechanism honest:

1. **`mandatory_relays` is top-level** on both the sidecar and `.analysis.json`. A
   reviewer must be able to enumerate relays without knowing the shape of any
   particular tool's `assessment` block.
2. **Codes are registered centrally**, in `provenance.RELAY_CODES`. The emitting helper
   rejects an unregistered code. A code invented at a call site would escape the list
   that skill authors work from, and a registry is only useful if it is provably
   complete.
3. **`dde relays` prints the registry.** Skill authors and reviewers work from one
   list, not from prose that has since changed.
4. **The empty case is meaningful.** A clean input emits no relays. A check that fires
   on everything is ignored.

**Rule 4 has a trap that only shows when you sort the registry by trigger** (tooling-lead,
audit of all seven tools). Relays divide into two kinds, and the division is not
positive-verdict against negative-verdict — that was the shape of the first tool anyone
looked at. It is **defect-triggered** against **claim-triggered**. A defect-triggered
relay fires on a fault in the data: coverage is partial, cells are missing, an isoform was
substituted. A claim-triggered relay fires on the answer itself, because the answer has a
well-known wrong reading: `gnomad.constraint_is_not_safety` on an intolerant gene,
`litref.resolved_not_verified` on a resolved reference, `fpocket.druggability_is_not_
affinity` on a druggable pocket.

**A defect-triggered-only tool emits no relays at all on a clean run, and the clean run is
the one whose number is most quotable.** A full-coverage high-pLDDT model and a complete
quantile set are the common case, not the edge. So the artifact goes out barest exactly
when it is most over-readable, and the tool looks best-behaved in the case it guards
least. Rule 4 is still right — a relay that fires on everything is ignored — but it was
being read as *fire only on faults*, which is a different rule and a worse one.

**The test, and it needs no knowledge of the tool's science: is there an input for which
this tool emits a quotable number and no relays?** If yes, ask what the wrong reading of
that number is, and whether it is well-known enough to name. If it is, the guard belongs
on the claim, conditional on the claim.

Two tools currently answer yes: **`afdb` and `alphagenome`** — and they are a pair rather
than a coincidence, which is the part a count would have hidden. They are the structure
predictor and the variant-effect predictor: the only two tools whose outputs are model
predictions presented as numbers, and the two most substitutable quantities in the pilot.
`pLDDT 92` stands in for *this structure is right*; a variant score stands in for *this
variant is pathogenic*. **No arithmetic here on purpose.** The first version of this
passage said *three of seven* and *four*; re-derivation from the emission conditions gave
five and two (template-builder), because a relay registered that same day had already
moved the figure. A count of the repository written by hand into prose is the §7 cached
value in its most self-defeating position — inside the rule against it. The test above is
the derivation and needs no maintenance.

**Claim-triggered splits again, and the second half is where a claim guard goes to die.**
A conditional claim relay fires on the verdict that invites the over-read — `fpocket`'s on
a druggable pocket, `gnomad`'s on an intolerant gene — and is silent otherwise, so its
appearance is information. An unconditional one fires on every run:
`coscientist.claim_denominator` did, and `expression`'s resolution pair does as an
if/else where exactly one branch always fires. (`claim_denominator` was moved to a
sidecar field per this rule — see #2; `expression`'s pair remains open
as #5.) By the rule directly above, those are
quoted and then ignored, and they train the reader past the conditional relays beside
them. **An unconditional claim guard is a standing property of the source wearing a
relay's costume**, and it takes the two exits of the always-true rule in §8: rescope it to
the case where the over-read is actually available, or relabel and move it out of the
relay channel into the artifact's own description, where it keeps its reader and loses its
claim on everyone else's attention. Which exit applies is the tool owner's call, not a
matter of wording.

**Why these rules keep taking the same shape: attention lands on the payload, not on the
annotation.** A reader who came for a number takes the number. Anything attached
alongside it — a caveat, a banner, a warning that fires every time — is skipped, and it
is skipped by competent readers acting reasonably, so it cannot be fixed by asking
people to read more carefully. Three rules in this corpus are the same rule seen from
different angles:

- *A relay is an instruction, not a caveat* (`skill-design-guidance.md` §4.5). An
  instruction changes what the reader must write. A caveat sits beside what they were
  going to write anyway.
- *A check that fires on everything is ignored.* Confirmed the hard way: standing relays
  on AlphaGenome were skimmed for days, while the first conditional one caught a real
  bug within the hour.
- *Superseded content gets deleted, not annotated.* A `SUPERSEDED` banner above text
  that still reads as authoritative is a caveat attached to a payload. One cost an agent
  a wrong report to the user about a tool it had itself repaired that afternoon.

- *stdout is the payload and stderr is the annotation, across every tool at once.*
  `dd4d65b` made `analyze` leave an agreeing record alone rather than rewrite it, which
  is right. But stdout still prints the existing artifact's path, and the fact that its
  `written_by` is somebody else appears only on stderr. A reviewer who forgot `--out`
  reads a path where a path was expected and copies the specialist's record into their
  report as their own re-analysis (template-builder, `d772596`).

The design consequence is the same in all four: **do not attach the warning to the
thing — change the thing.** Remove the stale text, make the relay conditional, phrase it
as the obligation it creates.

**Defect-vs-claim is one axis. There is a second: Kind.** Kind answers what the reader
must *do* when a relay fires:

- **Qualifier** — carry the caveat into the finding. The result is still reported,
  scoped. `afdb.partial_coverage` is a qualifier: the confidence metrics describe the
  modelled region, so any claim about the protein must be scoped to that region or
  withheld.
- **Stop** — block a substitution. Adding a disclaimer does not satisfy a stop relay; it
  makes the substitution look diligent. `fpocket.druggability_is_not_affinity` is a stop:
  if the question is about binding, the answer is that the question is untooled — not
  that the pocket scored high.

The two axes are orthogonal. Defect-vs-claim answers *what fires the relay*. Kind
answers *what the reader must do about it*. A relay can sit in any cell of the 2×2:

| | Defect-triggered | Claim-triggered |
|---|---|---|
| **Qualifier** | `afdb.partial_coverage` | `expression.absent_is_not_evidence` |
| **Stop** | `litref.ambiguous_name` | `fpocket.druggability_is_not_affinity` |

`pocket-druggability/SKILL.md` §4 is the first skill to define Kind with worked
examples — qualifier relays that scope a pocket score versus a stop relay that blocks
reading a cavity shape as binding evidence. That section is the reference for how Kind
governs a relay table in practice; the axis definition here is normative.

The fourth example — stdout as payload — shows what "change the thing" costs when the annotation is a whole
stream. Moving the warning to stdout does not fix it — it is still an annotation beside
a path, and the path is what the reader came for. **Guard at the point of damage
instead, and the damage is not the printed line. It is a review report claiming an
independent re-analysis that never happened.** So require every review report to quote
the `written_by` and the digest of the record it audits. A report whose cited
`written_by` is the specialist under review is then self-evidently not independent, on
the face of the report, with no reader obliged to have seen a stderr line minutes
earlier — and it is mechanically checkable, which a habit of reading stderr is not.

Register a code when the warning changes what the numbers *mean*, not when it is merely
interesting. Partial coverage, isoform substitution, and a count taken from an exception
list rather than a census all qualify: each one makes an otherwise correct-looking metric
describe something other than what the reader assumes. A retried request does not.

---

## 6. Stdout discipline

Tools write outputs and share paths. They do not stream content into context.

Budget per phase:

| Phase | stdout |
|---|---|
| `fetch` / `run` | The output paths. One line each. Nothing else. |
| `analyze` | A bounded human summary. Hard-capped, target 20 lines or about 2 KB. |
| Anything larger | To file, with the path printed. |

Two refinements.

**Bounded, not zero.** An `analyze` command printing a short verdict is useful. It is what the agent reasons over, and forcing a file read for three numbers is friction. The failure mode is unbounded output, not output. Enforce the cap in shared CLI code so no subcommand can exceed it. If a result would exceed the budget, print the headline and the path.

**The path is the primary output.** Print it in a stable, parseable position so the next invocation can chain without the agent reconstructing it. This is what makes the skill's invocation table honest: the table says where results land, and the CLI confirms it at run time.

Provide `--json` on every subcommand for machine consumption, and `--quiet` for path-only.

Upstream's own `analyze_plddt.py` violates the output-to-file convention by printing a full formatted report. That is a good illustration of why this belongs in shared CLI code rather than in a convention repeated across 63 independent scripts.

---

## 7. Thresholds live in code, not prose

Today the numbers that decide things — pLDDT > 75, LOEUF > 0.35, hERG > 30 µM, LE ≥ 0.30 — live as prose in `agents.md` and the README gate table. Prose thresholds cannot be enforced, versioned, tested, or varied per program.

**One of those four numbers is already wrong, which is the argument in miniature.** gnomAD recommends `LOEUF < 0.45`; `0.35` is a v2-era figure. gnomAD states that o/e values are higher in v4 and that "any `LOEUF` thresholds used on v2 will not give an equivalent number of genes when applied to v4". Nobody edited that line into error. It went stale because the database moved underneath it, and prose cannot go stale out loud. A named constant tagged `gnomad_v4` with a citation beside it would have raised the question at the next release.

Three-level resolution, most specific wins:

1. **Defaults in CLI code**, as named constants with a version tag (`default@1.2`). This is where the five module constants in `analyze_plddt.py` land.
2. **Program overrides** in `.dde/thresholds.yaml` at the project root. A CNS program and an oncology program should not share a hERG cutoff.
3. **Invocation override** via flag, for sensitivity analysis. Recorded in the sidecar when used.

Every `.analysis.json` names the `threshold_set` it applied. A Layer 1 finding can then cite not just a value but the criterion it was judged against. A stage gate can verify that the criterion was the program's, not a default somebody forgot to override.

Skills cite thresholds **by name**, never by value.

**The general form, and it is worth stating because the narrow one did not generalise on
its own: prose beside a tool should carry the tool's rule, never its current values**
(tooling-lead, stated in review; the fix that established it is `653bd0c`, and the
reasoning is restated in full here rather than left behind the pointer). A rule holds
until someone changes the design. A value changes underneath
the prose, silently, and the two sentences are indistinguishable in review — *"a healthy
install has about fourteen warnings"* against *"warnings are expected on a healthy
install; judge by the verdict, not by the count"*. Identical in tone, opposite in
lifespan.

**And a cached value in a title is not a cached value, it is a headline** (tooling-lead).
A count written into this document — *three of seven tools carry a claim-triggered relay* —
was wrong, was corrected eleven minutes later, and had already been copied into the title
of an issue in the meantime. The correction never caught the copy. A body can be corrected
in place and reads as corrected; **a title is quoted in every reference, appears in every
list view, and is read by everyone who never opens the thing.** So the ranking is: do not
write the value; if it is written, delete rather than correct it, because a correction
races the copies; and never put one in a name, a title or a heading, where it is
propagated by readers who never see the text that would have corrected them. The middle
step is the one that gets skipped, because it is where the rule is most tempting to break:
you have the right number *now*, so writing it feels like an improvement on writing
nothing. It is an improvement only until the next revision.

The evidence that the narrow statement was not enough is that its author reintroduced the
defect. Having written the cached-value rule into this document, tooling-lead put a count
into a `doctor` sentence within the hour, and it propagated to eight skills in under a
minute before being fixed at source (`653bd0c`). Knowing the rule, and having just
committed it, did not prevent it. What caught it was skills-lead reporting back the text
they had been given — which is the same finding as everywhere else today: **carefulness is
not the faculty that catches this class; a second party reading the output is.**

**A prohibition is only actionable once the replacement source is named.** "Never write
the value" tells an author what not to do and leaves them nowhere to get the number,
which is why correct-on-the-day values keep reappearing in skill prose. Every
`.analysis.json` carries `threshold_set`, `thresholds_applied`, `threshold_sources`
(`default` | `program` | `flag`, per key) and `threshold_provenance`. So the instruction
is not "omit the number" but **"quote it from the analysis you are citing"** — where the
value is stamped with the run that actually used it, and cannot disagree with the run
it describes.

### A cited threshold carries the use it was cited for

Prefer the source's own published cutoff to one recalled from the literature, and
record three things with it rather than one:

- **The number.**
- **The data version it applies to.** `LOEUF < 0.45` is a statement about gnomAD v4.
- **The purpose the source recommended it for.** gnomAD offers `< 0.45` "for the
  interpretation of Mendelian disease cases". DDE uses constraint to reason about
  target safety, which is a different question. The threshold is still the right one
  to use; what must not happen is the transposition going unrecorded. That is the
  work `gnomad.constraint_is_not_safety` does, and why it is a relay rather than a
  footnote.

**Where the source publishes an interval, do not replace it with a cutoff.** gnomAD is
explicit that `oe` and LOEUF "should be used as continuous values rather than a
cutoff", and that reading them without the 90% CI is a mistake. A tool that reports
only `lof_intolerant` / `lof_tolerant` has discarded the source's own account of how
certain it is. Report the interval, and let the classification sit beside it rather
than in place of it.

**An unresolved threshold is a legitimate terminal state.** Where a source publishes no
cutoff for a quantity, the tool must not supply one. It reports the value, reports the
threshold as unresolved, and declines to classify; `dde doctor` lists what is
unresolved so the gap stays visible instead of being quietly filled. Two of the first
five tools now have one — `low_confidence_ntpm` in `expression`, and
`loeuf_unreliable_min_expected_lof` in `genetics`, where gnomAD publishes no
expected-LoF floor and directs the reader to the CI instead. In a finished artifact, a
number invented to complete a schema looks exactly like a number with a citation behind
it.

**Prefer the source's own unreliability criterion to one of our own.** gnomAD states
that "intermediate `pLI` scores (0.1-0.9) are typically an indication that the gene was
too small to be confidently categorized". That is a better trigger for
`gnomad.constraint_unreliable` than a gene-length rule we invented, on two counts: it is
citable, and it is maintained by the people who maintain the data.

**Name the class: a correct citation supporting a claim narrower than the one it is
being used for.** The three rules above are all instances of it — a version, a purpose,
an interval. It deserves naming on its own because it is the failure this project
should expect to hit most often, and it is *worse* than a fabricated citation. A
fabricated citation fails a check we already run. A real one, correctly quoted and
genuinely load-bearing for a narrower claim, passes every check we have and still
licenses a wrong conclusion.

**And the citation is the mechanism, not merely the cover.** The fpocket 0.5 boundary
came with a real paper and a real fitted model, correctly cited — and that is precisely
what stopped anyone measuring the variance beneath it. A number with a paper behind it
reads as already-validated, so the validation nobody performs is the one that matters:
whether the source's boundary transfers to *our* inputs. The citation is load-bearing
for the model and silent about the transfer, and it gets read as covering both
(tooling-lead). Treat a citation as licensing exactly the claim it was made for and
nothing adjacent — and treat the arrival of a well-cited number as the moment to
calibrate, not as the reason not to.

### Threshold the quantity, not the coordinates: measure the variance first

> Before trusting a threshold, measure the variance of the thing being
> thresholded under transformations that should not matter. If that variance
> spans the threshold, the threshold is not the problem and moving it will not
> help.

A threshold with a defensible source can still be unusable, and the reason is
usually not the line. The source answers *where does the boundary fall*; the
question nobody asks is *how much does this quantity move when the underlying
fact does not*. Where that movement is larger than the distance to the line,
the cutoff is not classifying the thing. It is classifying the input you
happened to choose.

**State it at the level of the quantity, not the cutoff, because that changes
the repair.** Faced with 0.17 / 0.29 / 0.94 for one site, the tempting fixes
are to move the boundary or widen a borderline band. Neither helps. No cutoff
classifies that series, because the series is not measuring one thing — the
fpocket drug score is a function of the coordinates, not of the site, and the
coordinates vary for reasons unrelated to druggability. The 0.5 boundary itself
is sound: Schmidtke and Barril fitted a logistic model and 0.5 is genuinely
where it separates their training set (tooling-lead's correction to an earlier
draft of this section, which had blamed the cutoff).

`dde pocket` is the worked example (tooling-lead, `0a594be`). The fpocket
drug score, measured across crystal structures of two sites that carry marketed
drugs:

| Site | Structure | Drug score |
|---|---|---|
| CDK2 ATP | 1HCK (ATP) | 0.939 |
| CDK2 ATP | 2W1D (inhibitor) | 0.293 |
| CDK2 ATP | 1AQ1 (staurosporine) | 0.172 |
| HSP90 ATP | 1UYD (inhibitor) | 0.855 |

One site, three crystals, a spread of 0.77, crossing the 0.5 cutoff twice. Two
of those three would report *no druggable pocket* for a site that is drugged in
the clinic. So the tool cannot return a druggable/not verdict at all — and this
was true before anyone built it, discoverable by four measurements.

Three things follow, and they are the general procedure:

1. **Run the calibration before adopting the cutoff.** Pick inputs whose true
   answer is known and identical, vary only what should not matter, and measure.
   Four structures were enough here. This is cheap, and it is the only way to
   learn that a threshold cannot be applied *before* it is applied.
2. **Where the spread crosses the line, do not emit the classification.** Not
   as a hedged classification either. Rename the outcome for what it actually
   describes — `no-druggable-pocket-in-this-conformation`, not
   `not-druggable` — and attach the calibration numbers as a mandatory relay.
   Changing the name is the §5.1 rule again: do not attach the warning to the
   thing, change the thing.
3. **Record the calibration next to the threshold**, so the next reader inherits
   the measurement rather than the conclusion.

**The reason this matters more than it looks: a number is harder to challenge
than prose and no better founded.** The original tournament failed by answering
a druggability question from prose. The failure now available is answering it
from a score — with an artifact, a provenance chain and a citation behind it. A
specialist reporting *0.2, and that fact is about this conformation* has done
the right thing; one reporting *not druggable* has repeated the tournament's
error with better paperwork.

That is the same shape as §1.1's Test 1, where calling a model for a quantity
sitting in its training data **launders a database lookup through an inference
step**. Both are laundering: an operation that improves the *appearance* of
provenance without improving the evidence. It is worth naming as a class,
because it is the failure this architecture is least protected against — every
guard here is built to catch a missing citation, and laundering produces one.

### The pattern generalizes

Any dimension along which a skill would otherwise have to fork is a candidate to move out of the skill:

| Dimension | Moves to |
|---|---|
| Program | `.dde/thresholds.yaml` — solved here |
| Role | The template — questions owned and next actions |
| Stage | The orchestrator's task dispatch |

If you find yourself about to copy a skill and change three lines, find the dimension and move it out instead.

---

## 8. Failure behaviour

- **Fail loudly. Never degrade silently.** No partial results presented as complete. No fallback to a cheaper model without saying so. No default substituted for a missing required parameter.
- **Return error bodies, not just codes.** An agent can act on "429, retry after 300s, endpoint is single-flight". It cannot act on "request failed".
- **Never synthesize.** No mock data, no placeholder values, no `np.random.normal(86, 6, n_res)` standing in for pLDDT. If a tool cannot run, it exits non-zero with a reason. A CLI that can return a plausible number when the computation did not happen is a fabrication engine, whatever the intent.
- **Rate limiting and retry are central**, in shared code. Not re-implemented per subcommand, and not requested of the agent in prose.

**"Loudly" is measured at the reader, not at the tool.** The first bullet is the oldest
rule in this section and the one most likely to be read as discharged by a non-zero exit
and a good message. It is not. A failure is loud only if it **reaches someone who will
report it, at the moment they would otherwise be misled** — and this project's readers
are mostly agents, which are rewarded for producing an answer and are therefore the
readers least likely to stop. An agent that cannot reach a tool can still write an
entirely plausible finding, and no exit code fires when it does. The boundary is stated
in full under *[Name what would have made the check come out the other
way](#name-what-would-have-made-the-check-come-out-the-other-way)*, including why the
specialist templates' *report blocked, name the capability, and stop* is a behavioural
mitigation for what this bullet treats as a structural property.

Two consequences for the bullets above, from reading them against that criterion rather
than assuming they inherit it:

- **The second bullet gains a qualifier.** A rich error body is what lets an agent act
  correctly on `429, retry after 300s, endpoint is single-flight`, and it is equally
  what lets an agent conclude the obstacle is understood and route around it with
  confidence. Return the body — but do not treat having returned it as having been
  heard. Richness improves the best case and the worst case both.
- **The third bullet is the load-bearing one**, and it is the only one of the four whose
  guarantee does not depend on the reader's behaviour. A tool that cannot synthesize
  cannot be talked into synthesizing by a reader in a hurry. Where a rule can be written
  as a property of the tool rather than as an expectation of its caller, write it that
  way; that is the difference between the third bullet and the first.

  **A test for anything added to this section** (template-builder): if you cannot state
  the rule without the words *the agent should*, you have written an expectation rather
  than a property, and it will need a boundary later. Never-synthesize has needed no
  maintenance in this document; fail-loudly needed a boundary 370 lines below itself.
  That is not luck — one is a property and the other is a hope. The test does not forbid
  expectations, because some defences are irreducibly behavioural and live in a template
  or a skill rather than in a tool. It predicts maintenance: an expectation is a rule
  that will be found to hold only under conditions nobody wrote down, so write the
  conditions down when you write it, rather than after something has been read past
  them.

*Confirming instance rather than a counterexample:* [HTTP status does not classify
retryability](#http-status-does-not-classify-retryability-in-either-direction) below
already argues on the corrected criterion. It ranks a throttled request read as an empty
result above a wasted retry budget precisely because the first **fabricates a negative
finding** that reads as legitimate science, not because it is quieter. That is the
reader-side test, reached independently and before the criterion had a name.

### Resolution is not search: never rank and pick

Many sources answer an identifier query with a **ranked list**, not a record.
A tool that takes row 0 will sometimes return a complete, well-formed, entirely
authoritative answer about the wrong entity. That is worse than an error,
because nothing downstream looks wrong: the values are real, the provenance is
real, and the audit trail is clean.

Two instances found on one afternoon, in unrelated sources:

- `ClinicalTrials.gov`, `query.titles=PALOMA-3` returns two real trials — a
  Janssen amivantamab study in NSCLC and the Pfizer palbociclib study in breast
  cancer — and ranks the NSCLC one first. The breast-cancer record has a null
  `acronym`, so the two cannot be separated by name.
- HPA `search_download.php?search=WEE1` returns both `WEE1` and `WEE2`, whose
  synonym is `WEE1B`. Row 0 yields a clean 50-tissue profile for the wrong gene.

The rule, for any tool that turns a name into a record:

- **Resolve identity as its own step**, before fetching data.
- **Require an exact, unique match.** Prefer a stable identifier — Ensembl ID,
  NCT number, PMID, DOI, UniProt accession — and query by it once resolved.
- **More than one match is a hard refusal**, not a ranked answer with a warning
  attached. Return every candidate and let the caller disambiguate.
- **Record which input resolved to which identifier** in the sidecar.

A search endpoint is a discovery affordance. It is not a lookup, and a tool must
not use it as one.

### HTTP status does not classify retryability, in either direction

Two of the first five endpoints misreport their own failures, and they do it in
opposite directions.

- **A permanent error wearing a transient status.** AlphaGenome answers a missing
  `organism`, a bad interval length, or an oversized response with **502** — the same
  code as a real outage. The cost is a retry budget spent on a request that could never
  have succeeded.
- **A transient error wearing a success status.** gnomAD answers everything with
  **HTTP 200** and a GraphQL `errors` array. "Gene not found" and "service overloaded"
  arrive in the same envelope with the same status. Found live: five queries at 1 qps,
  and the fourth came back overloaded, which the first cut of the tool reported as
  "check the gene symbol" for WEE1 and KRAS.

The second direction is far more dangerous, and it is worth naming why. A wasted retry
budget is loud, slow and obvious. A throttled request read as an empty result is
**silent, and it fabricates a negative finding**: "no constraint data for this gene"
is a legitimate scientific statement, so once written it is indistinguishable from a
true one. Absence of evidence is a finding we genuinely want reported, which is exactly
what makes a counterfeit of it so hard to catch downstream.

So:

- **Classify on the body, not the status**, wherever the source gives you a body to
  classify on. Retry the messages the source uses for load; treat everything else as a
  real answer.
- **A failure to answer is never an answer of "no".** No path in a tool may convert an
  unanswered query into a negative result. This is the same rule as
  `expression.absent_is_not_evidence`, one layer lower down.
- **Record the pathology where the code is**, as AlphaGenome's module docstring does.
  Each of those facts cost a live failure to learn, and the next person to touch the
  file cannot rediscover them from the vendor's documentation.

### Name what would have made the check come out the other way

Before trusting a check that passed, state the condition that would have failed it. If
you cannot name one, you did not run a check — you performed one.

Six times in one day a tool here reported success for having examined nothing. Five had
an **empty subject**: a regex that had stopped matching, a lookup returning `{}`, an
evidence sweep comparing two empty snapshots against each other. Those are catchable by
counting, and every checker in `tools/` now exits non-zero on zero coverage.

The sixth had a full subject and was still vacuous. A loop ran fpocket twice and
compared the results to show the run was deterministic; the values matched, the numbers
were real, and the comparison was genuine. But fpocket seeds its Monte Carlo from
`time(NULL)`, the loop finished inside one second, and so the two runs were *byte*
identical — as they would have been for any program at all. Nothing about that check
could have gone red. Emptiness is a property of the subject and you can count it; this
was a property of the **conditions**, and no coverage guard would ever have touched it.
The question that catches both is the one above: what would have had to be true for
this to fail? (Here: a run crossing a second boundary. Sleeping one second between runs
is the whole fix.)

**The mirror is worth the same attention: a check that could not have come out *green*
is as vacuous, and costs more.** `dde doctor` gained a check that the provisioning
tree had no uncommitted changes. It fired immediately, truthfully, and uselessly — five
agents share this working tree, so `git status` is never clean, and the files that
happened to be dirty were a doc and a checker script, neither of which has any bearing
on whether the environment can be rebuilt. A permanent red is not a finding, it is a
tax on every reader, and it takes the true findings on the page down with it. The
check now looks only at the files that determine what provisioning produces, which can
genuinely be clean and genuinely be dirty.

**Notice where these keep landing.** Of the instances collected so far, three
were not in the work at all — they were in the step where someone verified their
own work. The fpocket loop that proved determinism inside one clock second. A
negative test for exit 2 that passed because `tools/` was already on `sys.path`.
A two-branch credential message tested only in the ambient shell, where the key
is unset, so one of the two strings was never rendered. In each case the subject
was real and the conditions forced a single branch.

That concentration is not a coincidence. A verification step is written by
someone who already believes the thing is correct, immediately after building
it, and its output is read as confirmation rather than as evidence. So the two
questions below are worth asking hardest about the check you just wrote to
reassure yourself, not about the code it points at.

So there are two questions, not one, and a check earns trust only by answering both:

- What would have made this red? (Nothing → it cannot detect the fault it claims to.)
- What would have made this green? (Nothing → it will be ignored within a day, along
  with everything beside it.)

**A check can pass both and still be inverted, which is worse than vacuous** (the case is
tooling-lead's, in `tools/bootstrap-preflight.sh`). A preflight has to know whether a
virtualenv can be created. `import venv` is the obvious proxy and it can plainly come out
either way — except on Debian, where the `venv` module is present and `ensurepip` is the
part that is missing, so the import **succeeds precisely when the thing being checked is
broken**, and fails only when something unrelated is wrong. The preflight would have
reported READY and `install.sh` would have died on its first command. The fix was to stop
proxying and create a real venv.

State the distinction in the strong form, because the weak form does not warn anybody: a
weak proxy degrades gracefully and is simply right less often, whereas **a proxy that is
true in the failing case is not a weak check, it is an inverted one, and it reads green
forever.** So there is a third question, and it is the only one of the three that survives
a check which answers the first two well: *could the thing I am consulting be true for the
same reason the thing I care about is false?* The two are usually near neighbours — module
versus module's bootstrapper, compiler present versus static libraries present, tool
installed versus tool callable — which is exactly why the proxy looked reasonable. This
one was found by refusing to test in the ambient container and injecting a shim instead:
**do not read the healthy case.**

### Refusal is its own exit code

A tool that refuses is not a tool that failed, and neither is a tool that answers "no".
Three outcomes that a shell chain must be able to tell apart:

| Outcome | Meaning | Exit | Retrying the same input |
|---|---|---|---|
| Answer, including a negative one | `not_found` is a completed analysis | 0 | n/a — you have your answer |
| Refusal | The input is under-specified; the tool will not choose | 9 | Pointless until the caller changes the input |
| Failure | The tool could not run | 1–8 | May well succeed |

`dde litref analyze` gets this right in substance: `ambiguous` exits non-zero, so
`dde litref analyze X && ...` cannot proceed on a citation the tool declined to
resolve, while `not_found` exits 0 because "this record does not exist" is the finding
we asked for. A refusal still writes its artifact and still prints its path.

**Give refusal its own code, project-wide.** Reusing a failure code collapses the row
that matters: an agent branching on the exit cannot distinguish "disambiguate and try
again" from "the endpoint is down, wait and retry unchanged" — opposite remedies. And
because refusal is a consequence of *never rank and pick*, every tool that resolves a
name has one. The code must therefore be assigned once in the shared error hierarchy,
not per subcommand, or the same outcome will exit differently depending on which tool
produced it.

In dde that code is **9**, carried by `Refusal` in `core/errors.py`, and raised by
every resolution step that finds no exact match or more than one: `litref analyze` on
an ambiguous name, `expression fetch` on an ambiguous or unknown symbol, `genetics
fetch` on a symbol gnomAD rejects, `alphafold analyze` on an ambiguous isoform. Skills
branching on the exit should read 9 as *change the identifier*, and 6 or 7 as *keep the
identifier and retry later*.

**The test for exit 0 is whether a citable artifact exists, not whether the answer was
positive.** These two look alike and are not. `litref`'s product *is* an existence
answer, so "this citation does not exist" is written to an artifact, can be cited, and
exits 0. `expression` and `genetics` produce measurements; a fetch that resolves nothing
writes no measurement artifact, so exiting 0 would hand a chained caller an empty paths
line beside a success code — the shape most likely to be read as *measured, nothing
there*. That is `absent_is_not_evidence` arriving through the exit code instead of
through the data. Where a negative genuinely is worth recording, the fix is to write the
`not_found` artifact and then exit 0, never to reclassify the exit on its own.

**When you add a cross-cutting outcome class, sweep for the shape, not for the class.**
The dedicated code was assigned after two known call sites; grepping for the
resolve-a-name shape found a third nobody had reported, where an ambiguous AlphaFold
isoform was raising `ArtifactError` — exit 3, *the artifact is malformed* — a third
wrong answer to the same question. Searching by existing error class could not have
found it, because the existing class was the defect.

**The same three-way split applies to a missing artifact, not only to an exit code.**
The reviewer's report carries a Records Compared table — path, `written_by`, `sha256`
for the record under audit and for the reviewer's own re-analysis — and the invariant
that the two `written_by` values must differ (template-builder, `2a47179`). But since
`dd4d65b`, a reviewer who omits `--out` and *agrees* gets no second record written at
all, because the tool correctly declines to duplicate an identical one. So an empty
second row has two opposite causes: the review was never done, or the review was done
and confirmed and the tool refused to write it twice.

A binary check reads both as violation, and the honest reviewer's least-bad move is
then to paste the specialist's record into the row — which is precisely the failure the
table exists to prevent. **A check that cannot tell the honest case from the dishonest
one teaches the honest party to imitate the dishonest one.** Give the honest case its
own named outcome, exactly as refusal gets its own exit code: *a confirmation that was
never recorded* reads neither as accept nor as violation, and carries a one-command
remedy — re-run with `--out`, which writes because the path differs.

The general form, and it is the same defect as a single exclusion list holding two
fields excluded for different reasons: **wherever absence is used as evidence, ask what
else produces absence.** Two states that look identical from the outside for opposite
reasons will be collapsed by any check that only counts them. The first is two things
put together for different reasons; the second is two states told apart by nothing but
their appearance. Both are failures to ask *why*, having asked only *what*.

**The costliest instance found so far is a vacuous comparison** (template-builder,
`32fe5be`). The reviewer's integrity check snapshotted `find "$DDE_PROJECT"/raw` before
and after the review and printed `evidence intact` when the two agreed. With
`DDE_PROJECT` unset, or pointing at the wrong place, or `raw/` empty, both snapshots
are the empty string, they compare equal, and it prints `evidence intact`. Verified in
all three cases. That is the check the entire review rests on — every other claim a
reviewer makes is conditional on *and I did not disturb the evidence* — and it reported
success while aimed at nothing.

The remedy is the same three-way split again: equal and non-empty is intact, unequal is
void, and **equal and empty is also void**. And note what the second half of the fix is:
the file count is captured at snapshot time and carried into the report, so
`evidence intact` is only legible beside the number of files it covered. That is §3's
coverage rule arriving from the other direction — **an instrument must state its
coverage, not only its findings** — which makes stating coverage the general prophylactic
against a vacuous check, not merely good manners. A guard that reports what it examined
cannot quietly examine nothing.

**A guard written in response to a specific incident inherits that incident's
assumptions.** That check was built to catch the destructive-overwrite bug, and its
author was concentrating so hard on catching tampering that nobody asked what it does
when there is nothing to tamper with. The assumption it inherited was that the evidence
exists and is correctly located. Write the incident down next to the guard, so the next
reader can see which case it was shaped by, and therefore which cases it was not.

**Next to the guard, not in the commit message.** A commit message is durable only if
authorship is, and in a shared working tree it is not: `git commit` captures the whole
index, so one agent's commit absorbs whatever another has staged. That happened to two of
template-builder's files on `7771c23`, which carried them under an unrelated subject.
Nothing of substance was lost, for one reason — the reasoning was inside the artifacts, as
a comment above the code it explains and as the template text itself. Only the commit
message went, and the commit message was the part with no reader. Rationale in the file
also survives a rebase, a squash, a file move, and a reader who never runs `git log`.
*(Mechanically: `git commit --only <paths>` commits the named paths and ignores the rest
of the index. Staging by explicit path protects everyone else from your `add`; committing
by explicit path is what protects them from your `commit`.)*

**A credit in a file is durable only if it points at an immutable object — and only some
credits point at all.** The test is whether the credit is doing **pointer work** or
**attribution work** (template-builder). Pointer work means the reader must chase it to
understand the passage, so it must resolve: anchor it to a commit id or a named file, and
check that the thing at the other end actually contains the reasoning, because a pointer
that resolves and disappoints is worse than a bare name — a bare name at least announces
its own limits. Attribution work means the reasoning is already present in the passage and
the name only records who to interrogate before changing it; nothing needs to resolve, and
marking it as unanchored enforces the form where its reason does not hold. Where a credit
does point, prefer *the fix that established it* over *the reasoning is there*: the id
names an event, and the reasoning stays in the file where §8 says it belongs.

**With one edge that inverts the usual safeguard, so it is worth stating beside the
flag.** `--only` takes the named paths from the **working tree**, not from the index
(tooling-lead, `c084c6e`; verified here on a synthetic repo). A file you had staged as a
selected subset of hunks is therefore committed whole, unfinished remainder included.
Partial staging protects you from a bare `git commit` and does **not** protect you from
`git commit --only`, which is the reverse of what the flag's name suggests. So the review
step before committing is `git diff -- <paths>` — what is about to go in — and not `git
diff --cached`, which shows what you selected and is no longer the thing being committed.
The flag is still right: it protects other people from you, which is the failure that
actually occurred, while partial staging only ever protected you from yourself.

Three defects on 2026-08-18 were found by **turning an existing check around rather than
adding a new one** — running coverage backwards, asking what else produces an empty row,
asking what else produces an empty diff. None needed new tooling. Before building another
instrument, invert the ones already built.

**Which is one instance of the only verification move that worked all day: re-derive, do
not re-read.** Three authors found defects in rules they had written themselves within the
hour, and every one of the three found it by re-running the thing — exec'ing the function
over the real line rather than reading its target list, reproducing a flag's behaviour on
a scratch repo rather than trusting the report, applying a freshly written criterion to
their own link. None of the three was found by reading the work again. **Re-reading your
own work is done by someone who already believes it, so it confirms; re-deriving puts you
back in the position of not knowing the answer** (template-builder). This is §7's *a
second party reading the output* reached from the other side: a second party is valuable
because they do not yet believe you, and re-deriving is the cheapest way to arrange that
condition without a second party.

**Check which direction a checker's own errors travel, because they invert.** A checker
has two populations: the things it flags, and the pool it judges them against. An error
in the first is visible and self-correcting — someone investigates a false alarm and
closes it. An error in the *pool* is silent and flips sign: **a false positive in the
entitlement pool is a false negative in the finding.** `check_threshold_names.py` decided
which threshold sets a skill may cite from by scanning for `dde <group>`, over the
whole document. The sentence *all dde project artifacts* registered a tool group named
`project`. Harmless as it stood, because no module provides that group — but any prose
sentence containing a *real* group name would have widened a skill's entitlement and
waved through exactly the misplaced citation the tool exists to catch.

The repair produced its own instance, which is the part to remember. Restricting the scan
to fenced blocks and inline code spans dropped two genuine invocations from a file that
uses four-space indented blocks: one false positive traded for two false negatives.
**Tightening a matcher moves error from one direction to the other; it does not remove
it.** So both directions get a negative test — prose must yield nothing, and each of the
three code contexts must yield its group — and the tests are written as assertions about
inputs the author constructs, not as a re-run against the repo, which would only confirm
that today's corpus happens not to contain the bad case.

**The vacuous pass has a twin: the vacuous failure.** Every guard above stops a checker
reporting success on nothing. The mirror case is a checker reporting *failure* on
nothing, and it had gone unexamined because it does not look like a defect. With the
shared venv absent, `check_threshold_names.py` failed at import for want of `click` and
exited 1 by traceback — the same code it uses for *found a problem*. The two demand
opposite responses: fix the repo, or fix the environment. So **"could not run" gets its
own exit code**, 2, exactly as refusal gets 9 and for the same reason.

The direction of the failure is what makes this worth a separate entry
(template-builder, `26b9859`). Every cache and every vacuous check found so far fails
towards a
**false clean**: a broken system looks healthy. This one fails towards a **false alarm**:
a healthy system looks broken. Their reviewer template hardcoded `8 of 8` for a `doctor`
line; `dde pocket analyze` made it 9, so an auditor comparing the correct run against
the page would open a discrepancy against a system that was working.

That is not the milder of the two. **A false clean is discovered when something breaks; a
false alarm is discovered by being ignored**, and the ignoring generalises — a gate that
cries wolf trains people to wave through the gates beside it. So a checker that cannot
distinguish *cannot run* from *found a problem* damages the checks that do work.

Two remedies, and the second is the durable one. Give cannot-run its own code. Then, where
a count or a capability is being asserted, **state the invariant instead of the value**:
not "expect 8", but "the two counts match each other, and the analyzer you are about to
re-run is in the named list". Writing 9 in place of 8 only re-arms the trap for the next
tool. Narrow the sentence to what is actually stable — the same rule as below, arriving
from the false-alarm side.

**A condition that is always true is not a check, even when it is true.** `doctor`
warned when the working tree was dirty. The warning was accurate every time and worth
nothing, because five agents share this tree and `git status` is never empty — a signal
that never varies carries no information, whatever its truth value. Note that this is not
the false-alarm case: nobody is being told something wrong. They are being told something
constant, and the cost is the same, because a line that never changes stops being read
along with the lines beside it.

**It has two exits, and picking the wrong one loses something.** Either the condition is a
real check asking too broad a question, in which case **rescope** it — dirtiness of
*provisioning inputs* is a genuine question that dirtiness of *the tree* was drowning
(tooling-lead, `112e2b9`). Or it is a standing property of the system wearing a finding's
costume, in which case **relabel and move** it: `doctor`'s six permanent advisories are
constant by nature and cannot be rescoped, so they were given a kind orthogonal to status
and grouped as standing faults that change how you read a result rather than whether you
can produce one (`e82b1d1`). They keep their reader and lose their claim on everyone
else's attention. **Deleting is correct only when the condition has no reader at all.**
The same disposal step reached from the review side: a check that has never once come out
any other way is not a finding, it is a gap in the briefs, and filing it as a finding lets
everyone feel it was examined (template-builder, `729df95`).

That rule was found independently in three tools by three people within an hour — a `git`
status line, a set of CLI advisories, and a reviewer's threshold check. Independent
rediscovery in unrelated objects is better evidence than any one of the three cases, and
better than the retrodiction in §8.6, because the authors did not share a draft.

**Which decides where an instrument is worth building at all: a detector earns its cost
where the failure is silent** (template-builder). A renamed directory makes `ls skills/`
fail in the reader's face at the moment of use, and no gate improves on that — a loud
failure is already reported by the person it happens to. The same rename applied to
`grep -h uri: templates/*/scion-agent.yaml` prints nothing, and *nothing* reads as **no
template grants any skill** rather than **this command stopped working**. Same rename,
same file set, opposite visibility. So the question before adding a checker is not how
important the subject is but **what the failure looks like to the next person**, and the
repair is often in the instrument rather than in a new gate: `grep -c` names every file it
read and prints a count per file, so the rename shows as twelve zeros or `No such file`
instead of as a clean empty result. That is the denominator rule of the next paragraph,
applied to a one-line shell command.

**With the boundary its author stated before it hardened: "loud" is a property of the
failure *and its reader*, not of the failure alone** (template-builder). `ls skills/`
gets reported because the reader is a human at a prompt, blocked, with every incentive
to complain. Change the reader and the same error goes quiet: a broken path in a skill
document errors just as loudly into an agent's context window, and an agent's instinct
is to route around it — glob for a similar name, try the parent, infer the rename — and
continue. Loud failure, silent response, and then a finding that looks normal.

So the operative test is not how loud the failure is but **whether it reaches someone
who will report it, at the moment they would otherwise be misled**. Loudness is a good
proxy for that only where the reader is human and blocked. It is a poor one for
everything in this repository that agents read more than people do — CLI output, `dde
doctor`'s advisories, skill text, artifact paths in a sidecar — and those are precisely
the surfaces where the rule above would otherwise license skipping a gate.

The specialist templates do instruct an agent to report blocked, name the capability and
stop, which is literally an instruction not to route around. Keep that in proportion: it
is a *behavioural* mitigation for something the rule states *structurally*, and it holds
only as well as instruction-following does under pressure to produce a result. Where a
tool can make routing-around impossible rather than merely discouraged, prefer that —
a refusal exit code the caller cannot mistake for a failure, a phase-2 latch that will
not fabricate the input it is missing — and spend the gate where neither the failure nor
the reader can be relied on.

**Know the limit of the structural version, because it is not where you would guess**
(tooling-lead). Refusal codes and latches defeat *fabrication* completely: no tool here
will produce a number whose computation did not happen. They do nothing about
*substitution* — the blocked agent that reports a pocket score where an affinity was
asked for, under a real tool's name, with real provenance. **Loud refusal, silent
substitution, and a finding that looks normal.** No CLI can **detect** this one: the
substituting command is a legitimate invocation on its face, and nothing in the argument
list says it was chosen because a different tool was missing. The defence and the
reasoning live where the missing capability is known, in orchestration-design-guidance
§8.6. Note that **improving refusal makes that section more load-bearing, not less** — the
better this CLI is at refusing, the more often an agent is a blocked reader, and a blocked
reader is the one who substitutes.

**But undetectable is not unaddressable, and the first statement of this rule said "no CLI
can prevent it", which was too strong** (tooling-lead's correction to their own passage,
`7a5a682`). A tool cannot see the substitution and can still **state, at the moment the
number is produced, what the number is not** — in a registered relay, under the tool's own
name, in the sidecar that travels with the value. That does not stop a determined
substituter. It puts a sentence beside the number that contradicts the claim the
substituter needs to make, which is the difference between a misreading that survives
review and one that arrives pre-refuted.

The generalisable part is **which** verdict gets the guard, because the instinct is
backwards. `dde pocket` carried a full set of relays and every one of them guarded the
**negative** verdict — the conformation caveat that fires when a score disappoints. The
positive verdict went out bare, and the positive verdict is the one a role lacking an
affinity tool reaches for. **Guard the direction someone would want to over-read, not the
direction you expect to have to defend** — the same instinct as testing the branch you
expect to fail. Keep it conditional for the reason `gnomad.constraint_is_not_safety` is
conditional: below the cutoff there is no over-claim available to make, and a relay that
fires on every run is quoted and then ignored. Applies to any tool whose output has a
well-known wrong reading.

**Then keep it short, and put the substitution clause first.** A relay competes for the
stdout budget, and a clipped message loses exactly its last clause — so a caveat written
with the over-claim named at the end is the one whose point disappears under truncation,
in precisely the runs verbose enough to be near the limit. Write the shortest sentence
that carries the contradiction and check the rendered length, not the source length.

**A coverage figure is only informative if its denominator comes from somewhere the
numerator did not.** This is the limit on every coverage guard above, and it was found by
the one defect of the day that was not a defect in a comparison. `check_invocations.py`
took candidates from fenced blocks and inline spans only, so indent-style blocks were
invisible: four real invocations were never checked, two of them in
`skills/artifact-conventions`, the file agents read and copy from. The comparison was
correct on every invocation it saw. It printed *checked 76 invocations, 0 problems* the
whole time. 76 was true. It was simply never the denominator anyone read it as — the real
figure was 93.

So distinguish two kinds of denominator. *12 of 12 templates yielded a skills block* is
informative, because the 12 comes from a directory listing and the parser cannot shrink
it — a format change shows up as 11. *93 invocations checked* is not, because the parser
defines the population it then reports on. **An instrument cannot report what it never
saw**, so completeness is not checkable from inside it, however carefully it states its
coverage. Prefer denominators the instrument does not compute: file counts, declared
inventories, counts obtained by a different method.

Where no independent denominator exists, the only defence that worked today was **two
instruments over the same subject with different parsers, and someone comparing the
counts** (template-builder). That cuts against the instinct to factor two checkers into
one canonical parser: shared parsing makes the numbers agree by construction, which is
precisely the agreement that carries no information. The same argument as never letting a
prediction overwrite a measurement — **two independent derivations that disagree are
evidence, and one derivation used twice is not.**

The organising consequence is worth stating, because it is the opposite of how a tidy
person builds a suite: **overlap the checkers deliberately rather than partitioning them
cleanly by subject.** Two tools scanning the same markdown for the same invocations, with
different parsers, looks like duplicated effort right up to the hour it is the only thing
that finds seventeen unchecked invocations. Neither author was careless — both were
careful, in opposite directions — and carefulness is not the faculty that catches this.
A clean partition by subject guarantees that every blind spot is covered by exactly nobody.

**And redundancy is only visible as redundancy afterwards** (tooling-lead). Before the
fact, the second derivation that catches something and the one that wastes an hour are
indistinguishable — that is what it means for the two paths to be independent. So the
tidy-minded pass that removes duplicated effort is not making a poor judgement; it is
making a judgement it has no information to make, and it will remove the useful overlaps
at whatever rate chance dictates. The choice available is a policy about overlap in
general, not a selection among particular overlaps. Three verifications of one `git` flag
in one evening produced a confirmation, a reproduction, and one previously unknown edge
that inverted a safeguard; nobody could have said in advance which of the three would be
the third.

**Before guarding a checker's dependency, ask whether it needs one.** The cannot-run code
above was added because `check_threshold_names.py` could not run without `click`. It
turned out not to need `click` at all: it imported `RELAY_CODES` from `provenance`,
`provenance` imports `output`, and `output` imports `click`, while `thresholds` — the
module it actually uses — is a leaf. The entire dependency was a *constant* read through
a module whose behaviour the checker never invoked. Reading the literal from source
instead removed it, and the gate now runs in any container. **A checker that imports a
module solely to read a constant inherits every dependency that module has, for none of
the benefit** — so shared vocabulary belongs in a leaf module, not behind the execution
layer.

**But the inverse must not be applied by analogy, and this is the more important half.**
`check_invocations.py` cannot drop `click` and should not be made to: it walks the command
tree that `click` *builds*, so the dependency is its subject rather than an incidental
import (template-builder; stated in `tools/check_invocations.py`, module docstring, from
`2b1594a`). Re-deriving that tree from source would turn the checker into a
second cache of the very thing it exists to check — and a cache in a checking position is
§8.5 in its worst position. There, exit 2 with the venv absent is the honest permanent
answer, not a gap waiting to be closed. **The test is whether the checker uses the
module's behaviour or only its data.** Data can be read from source; behaviour cannot be
re-implemented without becoming a rival implementation of the thing under test.

**An existence check will be read as a currency check unless it says otherwise.**
`check_skill_uris.py` asked whether a declared skill is present on the tracking ref,
which matters because skill resolution is remote: on disk is not on the ref, and a skill
that is not on the ref does not exist to provisioning. Correct, and it closed a real
hole. But template-builder found the limit **by using it** — they ran it before wiring
`tournament-corpus`, got exit 0 and *every declared URI resolves to a pushed skill*, and
the pushed copy was 35 insertions and 15 deletions behind the author's local commit. An
agent provisioned in that window would have loaded superseded text with every checker
green.

Note the shape. This is §7's *correct citation supporting a claim narrower than the one
it is used for*, arriving in an instrument instead of in prose. The output was true. It
was true of a narrower question than the reader was asking, and the reader had no way to
see the gap, because the instrument stated its finding and not its scope. **Presence and
currency are different questions, and the one that is cheap to check is not the one that
matters.**

It is also worse than the absence case it sits beside, which inverts the usual intuition
that a missing thing is the more serious fault. Absence fails loudly at provisioning;
staleness provisions cleanly with the wrong content and reports nothing anywhere. A
failure mode that produces a clean run is more expensive than one that produces a crash,
and the cheap check is systematically the one that covers the crash.

The remedy is the three-way split for the third time in one day: *resolvable and
current*, *resolvable but superseded locally*, *not on the ref at all*. The middle
outcome is not an error by default, because the person running the checker often does
not own the skill and cannot push it; it is an error under `--strict`, which is the mode
to run before provisioning against the skill. And the fix template-builder actually
asked for was smaller than the one built — make the output say what it asserts — which
is worth recording on its own: **when an instrument is read too widely, the first fix is
to narrow the sentence, not to widen the instrument.** Widening was justified here only
because the narrower question could be answered locally with `git diff` and one call for
untracked files. Detecting it needed both: a new file inside an already-pushed skill
directory shows in neither the diff nor the tree listing, because git does not track
what it has never been told about.

Two limits are stated in that tool's own docstring rather than left for a reader to
discover. Currency is judged against the *local* remote-tracking ref, so it is only as
fresh as the last `git fetch`, and it fails towards silence. And the closing line now has
two forms: it claims the pushed copy matches the working tree only when every declared
skill was actually compared, and otherwise says in as many words what it is **not**
asserting. tooling-lead's `61cdb25` reports the same limit the same way, for the same
reason, on a different object — a stale `origin/main` can call a pushed commit
unreachable, so the remedy text names `git fetch` first. A known false-alarm direction,
written down, is a diagnosable annoyance; undocumented, it is a mystery that trains
people to ignore the instrument.

### State a rule at the level its object belongs to

§7's cached-value case generalises into an editing rule for this whole document
(tooling-lead). A rule stated over one named object — a threshold, a citation, a count —
does not merely fail to cover its siblings. **It reads as complete, so a reader who knows
it feels covered and stops looking.** *Fourteen warnings* is not a threshold, so *cite
thresholds by name* never fired, and its author had read it that hour. A rule pitched too
narrowly is worse than a missing one: the missing rule leaves you alert.

So for each rule stated over a named object, ask what the object is an instance of, and
whether the rule survives the generalisation. Four in this document do:

- **A threshold in prose → any value in prose.** §7.
- **Database coverage for an entity (§1.1, Test 3) → any lookup: the presence of a source
  is not the presence of an answer for your entity.** The failure is identical whether the
  source is UniProt, a structure database or a local artifact directory.
- **A cited threshold carries the use it was cited for (§7.1) → any borrowed artifact
  carries the scope of its original validation.** Benchmarks, qualified assays and
  validated models all travel outside their scope the same way, and none of them announce
  it.
- **Provenance beside the identifier, not inside it (§2, Rule 4) → an identifier answers
  *same or different*, never *why*.** Loading the answer to the second question into the
  first is what makes an identifier fire on changes that do not matter.

Where the generalisation does **not** survive, write the boundary down, because the next
reader will attempt it and should find out where it stops. Two here:

- **"Drop the dependency you only read data from" does not become "drop dependencies".**
  The boundary is behaviour against data: a checker that walks a tree `click` builds needs
  `click`, and re-deriving that tree would make the checker a rival implementation of its
  own subject.
- **"Overlap the checkers deliberately" does not become "duplicate everything".** Overlap
  buys information only where the two paths could fail *differently*. Two readers sharing
  a parser agree by construction, and that agreement is a cache wearing a cross-check's
  costume — the same distinction as two independent derivations against one derivation
  used twice.

**Generalising is not free, which is why the rule is *state it at the level of its
object*, not *state it as generally as possible*.** A rule at the most abstract level
available loses the example that makes it recognisable in the moment it applies, and an
unrecognisable rule fails the same way a narrow one does. Keep the specific case as the
example directly beneath the general statement.

**Which makes the examples in this document load-bearing, and the warning belongs here
rather than in a preface, because here is where an editor stands when they delete.** The
specific case beneath a general rule is not an illustration of it. It is what makes the
rule recognisable at the moment it applies, and a reader who cannot recognise a rule is in
the same position as one who was never told it. Do not tidy these out; if a case has
genuinely stopped being the clearest instance, **replace it rather than remove it**
(tooling-lead). This one has no detector, and it looks like an improvement while it
happens — shorter, cleaner, more general, and inert.

### Cross-check a derived label when the source ships one

Where a source publishes both the underlying data and its own derived
classification, recompute the classification from the data and compare. The
agreement is a free, continuous check that parsing, column mapping and
thresholds have not drifted, and it runs on every real analysis rather than only
in tests.

`dde expression analyze` does this: it recomputes the HPA specificity class
from the tissue profile and compares it with the curated label in the same
record. A hardcoded tissue list that silently goes stale would break the
agreement and be caught.

### Leases for single-flight resources

The AF3 endpoint is max-one-replica and returns 429 under concurrency. File locks do not help when specialists run in separate containers.

This is the one place a small shared service is the right answer: a lease broker on the shared volume, or as an MCP service, where `dde alphafold predict` acquires, waits, or fails with a queue position. Without it, parallel specialists will collide, and the failure will look like a flaky tool rather than a design gap.

The command named here was "dde af3 run" — no backticks, because it is not an
instruction and never was one — until a checker compared the document with the built
command tree. No such command has ever existed. Worth noting because of where it
survived: a design document is exactly where a plausible command name lives longest,
since nobody runs a document.

---

## 9. Checklist for a new subcommand

- [ ] Identity resolved as its own step; exact unique match required; ambiguity refused rather than ranked
- [ ] Refusal exits on the shared refusal code, distinct from failure; a negative answer exits 0
- [ ] Retryability decided on the response body where the source ships one; no path turns an unanswered query into a negative result
- [ ] Every threshold cites its source, the data version it applies to, and the purpose the source recommended it for; unresolved is preferred to invented
- [ ] Where the source ships a derived label, it is recomputed and compared as a drift check
- [ ] Split at every phase boundary worth auditing separately — or documented as single-phase lookup
- [ ] All emitted files land under `raw/`; nothing a tool writes goes to `findings/`
- [ ] Writes to the preconfigured directory for its artifact class; project root resolved, not CWD
- [ ] Deterministic output filename derived from the query
- [ ] Emits `.meta.json` with tool version, `env_version`, endpoint, parameters, checksums, warnings
- [ ] Any warning that changes what the numbers mean is emitted as a registered relay code
- [ ] `analyze` emits `.analysis.json` citing source and `threshold_set`, and carries forward the relays that still apply
- [ ] Thresholds are named constants, program-overridable, never inline literals
- [ ] stdout within budget; paths printed in a stable position
- [ ] Non-zero exit with a reason on failure; no synthesized values on any path
- [ ] Rate limit or lease handled by shared code
- [ ] Registered in `dde doctor`
