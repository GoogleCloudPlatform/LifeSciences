# DDE Skill Design Guidance

**Status**: v0.1. Expect revision after the pilot conversion (co-scientist, AlphaFold, AlphaGenome).
**Purpose**: the standard for writing dde skills, and the reference for converting `google-deepmind/science-skills` one at a time.
**Companion**: [`tool-design-guidance.md`](tool-design-guidance.md). That document is normative for paths, sidecars, and thresholds.
**Context**: [`dde-plan.md`](dde-plan.md) §5 and §7.

This document's specialist-neutral grouping rule applies to **science capability
skills**. Procedural orchestration skills may bind to the Science Program Lead or
Research Operations Controller because those roles have deliberately different
authority. They retain the same computation/prose boundary, but their inventory and
placement rules are defined in
[`orchestration-design-guidance.md`](orchestration-design-guidance.md) §8.

---

## 1. What a dde skill is

A dde science skill is a routing and interpretation contract for a **capability** — a group of tools that share the rules for reading their results.

A skill is not a manual for a tool. A skill is also not the property of one specialist. The same capability serves different roles, asking different questions, at different stages. The role-specific part belongs in the template. See §3.

The split with the CLI is absolute:

| | Skill | CLI |
|---|---|---|
| Answers | When to run it, what to run, where results land, what they mean | How the computation happens |
| Contains | Prose | Code |
| Loaded | Description always; body on demand | Never in context |
| Owns | Judgment | Determinism |

Two rules follow:

1. Anything that can be computed belongs in the CLI.
2. Anything that is a threshold belongs in CLI configuration.

What remains is what a model cannot get from the tool output alone: whether to run the tool, and what the result licenses you to claim.

---

## 2. Group by shared interpretation contract; name by capability

Upstream science-skills uses one skill per database. That fits a general-purpose corpus, where any user might want gnomAD alone. DDE is a pipeline. Its tools cluster.

### Grouping test

Group tools into one skill when both conditions hold:

1. They share an interpretation contract. The rules for reading their results overlap.
2. A specialist would rarely cite one without the others in the same finding.

Keep a tool separate when it is a general-purpose instrument used across many decisions.

### Naming rule

Name a skill for what it lets you **find out**. Do not name it for what you **conclude**.

The grouping test selects the right cluster. The name is where the error enters. A skill named for a decision encodes one specialist's question into an artifact that four specialists will use. The other three will not find it, or will find an interpretation section written for somebody else.

The tell is grammatical. A judgment word in the name — tractability, triage, viability, suitability — means the name absorbed a question. Capability names describe available evidence. Decision names describe a verdict on that evidence.

### Worked grouping

| Skill | Tools | Shared interpretation contract |
|---|---|---|
| `protein-structure-confidence` | AlphaFold fetch/analyze, PDB, fpocket | How far can model geometry be trusted, globally and per residue? |
| `target-genetic-evidence` | Open Targets, gnomAD, GTEx, ClinVar | What human genetic evidence exists, and how strong is it? |
| `compound-property-profile` | SMILES validation, RDKit descriptors, PAINS/Brenk filters | What are this compound's computed properties and structural alerts? |
| `structural-homology-search` | Foldseek | Standalone. Used in target ID, selectivity, and off-target work, each with different consequences. |
| `pymol` | PyMOL | Standalone. A rendering and measurement instrument. |

Do not group tools because they belong to the same scientific field. Foldseek and AlphaFold are both structural. They answer different questions.

### Expected count

Applied across the corpus, the grouping test should give **8 to 12 dde skills**, not a 1:1 conversion of the ~38 upstream skills.

That number is an outcome of applying the test, not a target. At 30, the test is not being applied. At 4, contexts are being merged that do not share an interpretation contract.

---

## 3. One capability, many roles

One capability skill serves more than one specialist, at more than one stage. This is the normal case.

| Capability | Roles | Stages |
|---|---|---|
| `protein-structure-confidence` | Structural biologist, computational biologist, computational chemist, medicinal chemist | 1, 2, 3 |
| `bioactivity-landscape` | Computational chemist, medicinal chemist, ADMET scientist, toxicologist | 2, 3, 4 |
| `target-genetic-evidence` | Computational biologist, toxicologist | 1 and 4 |

The last row is the clearest case. The computational biologist uses those four APIs in Stage 1 to ask whether causal evidence exists for the target. The toxicologist uses the same four in Stage 4 to ask whether human loss-of-function carriers predict on-target toxicity. Same tools, same output file, unrelated decisions.

### Why the skill stays specialist-neutral

Nine things make up a skill. Six are scoped to the tool and do not vary by role:

- the invocation table
- the preconditions
- what the numbers mean
- universal consequences
- failure pathologies
- the anti-fabrication guard

Two vary by role: which question is being asked, and what happens next. One varies by program: the threshold.

That last one is the precedent. Thresholds vary by program, so they moved out of the skill into `.dde/thresholds.yaml`. Nobody forked a skill per program. Question and consequence vary by role, so they move out too — into the template. The `skills:` list in `scion-agent.yaml` is the capability grant. The always-loaded descriptions do the routing.

Bake a role into a skill and one cluster becomes four skills. Each restates the same invocation table and the same output paths. Four copies of the artifact contract is four chances for it to drift. A drifted invocation table makes an agent guess a path, then invent what it would have found there.

### The placement test

For any sentence you are about to write in a skill, ask:

> **Would this be wrong for a different specialist?**
>
> No → it is a universal consequence. Put it in the skill.
> Yes → it is a role-specific next action. Put it in the template's `agents.md`.

"A highly disordered protein should not be docked whole" is true for everyone. It goes in the skill.

"If the pocket is disordered, tell the medicinal chemist before the next DMTA round" is specific to one role. It goes in the template.

Applied honestly, most consequence content is universal, and templates stay short.

---

## 4. Required sections

Six sections, in this order. A seventh section usually means the skill is carrying something that belongs in the CLI.

### 4.1 Frontmatter description — routing

This is the only part always in context. It does the most work per token.

Three clauses: what the capability finds out, when to use it, and an explicit negative naming an alternative.

```yaml
---
name: protein-structure-confidence
description: >
  Retrieve protein structures and quantify how far their geometry can be
  trusted — global and per-residue model confidence, disorder, domain
  boundaries, and pocket detection. Use when any structural claim depends
  on model reliability: assessing target druggability, deciding whether a
  binding site is well enough resolved to dock into, or checking whether a
  variant falls in an ordered region. Do not use to find structural
  homologs (use structural-homology-search), to interpret a co-crystal you
  already have (use binding-mode-analysis), or when you only have a gene
  name — resolve it to a UniProt accession first.
---
```

Note what the *use when* clause does. It names three specialists' entry points into the same capability, without naming the specialists. A description that lists only the first use case makes the skill invisible to everybody else.

The negative clause is not optional. It is the cheapest protection against mis-routing, because it stops an agent loading the wrong body.

### 4.2 When to use, and when not

The body expansion of the description. Name off-ramps as links to sibling skills. This makes the skill library a routed graph instead of a bag of tools.

### 4.3 Preconditions

What must be true before invoking: input form, required credential, cost, concurrency limit.

Cost and concurrency belong here because they change planning, not just execution. An agent needs to know that AF3 is single-flight **before** it designs a 40-compound campaign.

### 4.4 Tool invocations — the routed subset

This section connects the skill to the CLI. Write it as a table: question, command, output location.

```markdown
## Tool Invocations

| Question | Run | Writes to |
|---|---|---|
| How confident is the fold? | `dde alphafold fetch <UNIPROT>`<br>`dde alphafold analyze <UNIPROT>` | `raw/structures/AF-<id>.cif`<br>`raw/structures/AF-<id>.meta.json`<br>`raw/structures/AF-<id>.analysis.json` |
| Is there an experimental structure? | `dde pdb search --uniprot <UNIPROT>` | `raw/structures/pdb-search-<id>.json` |
| Is the pocket druggable? | `dde fpocket run raw/structures/AF-<id>.cif` | `raw/structures/AF-<id>.pockets.json`<br>`raw/structures/AF-<id>.pockets.analysis.json` |

Run `fetch` before `analyze`. `analyze` reads from disk and can be re-run without re-querying.
```

Three rules:

1. **Every capability the skill claims needs a row.** No row means the skill must not claim it. An agent asked for a pocket volume with no pocket tool will produce a plausible invented number.
2. **Show the paths.** The agent must not guess where output went. It should cite the path in a Layer 1 finding without re-deriving it. Everything in the table lands under `raw/`. Model outputs, sidecars, and `.analysis.json` are all Layer 0.
3. **Do not restate `--help`.** Give two or three canonical invocations. `dde <tool> --help` is the option reference. A second copy will drift.

### 4.5 Interpretation contract

The largest section, and the only one that cannot move into code: what the numbers mean for a decision.

Include:

- **Mandatory relays.** Warnings that must reach the report unchanged. Isoform substitution is the model case — analyzing an isoform instead of the canonical sequence invalidates everything downstream.

  **Name the code, not the warning text.** Relays carry registered codes such as `afdb.partial_coverage`, emitted top-level in the sidecar and `.analysis.json`. Cite the code and state what it obliges the specialist to do. Do not restate the message: prose is reworded between builds, and a skill that matched on the old wording goes quietly stale. This is the same rule as thresholds, for the same reason — cite the name, let the CLI own the value. Run `dde relays` for the registry, and see [`tool-design-guidance.md`](tool-design-guidance.md) §5.1.

  A relay is an instruction, not a caveat. `afdb.partial_coverage` does not mean "mention that coverage is partial"; it means the confidence metrics describe the modelled region, so any claim about the protein must be scoped to that region or withheld.
- **Synthesis rules.** How to combine outputs across the grouped tools into one judgment.
- **Consequence rules.** When a result should stop or redirect downstream work. Example: if the protein is highly disordered, advise against whole-protein structural analysis, and restrict further work to the ordered residue boundaries.
- **Cross-disciplinary consequences.** What the result means for any downstream specialist, stated without naming who is reading. "Local confidence below the threshold makes docking scores unreliable in that region" is universal and belongs here. "Tell the medicinal chemist" is a next action and belongs in the template. The T-shaped liability handoff in `artifact-conventions` fires from the template, using this section as its evidence.

Do not restate threshold values. Cite them by name, such as the configured `plddt_confident` threshold. Values live in CLI config and are stamped into `.analysis.json`. Prose thresholds cannot be enforced, versioned, or varied per program.

**Say where the number comes from instead, or the prohibition will not hold.** A skill author who needs to state a criterion and is told only what not to write will write the value anyway, correct on the day and stale by the next data release. Where a finding must give the number, the rule is **quote `thresholds_applied` from the analysis being cited**, together with `threshold_sources` where it matters whether a default or a program override was in force. This is not a style preference: a value quoted from the artifact cannot disagree with the run it describes, and a value typed into prose eventually will. See `tool-design-guidance.md` §7.

When a threshold referenced by the skill is UNRESOLVED in the CLI, the skill must state this and carry the "do not invent" instruction — the agent must not supply a value the source declined to publish. See `tool-design-guidance.md` §7 for the full rationale.

**What this section produces.** Following the interpretation contract produces the specialist's prose conclusion — a Layer 1 finding in `findings/`. Everything the tools emitted stays in `raw/`. A finding that reformats an `.analysis.json` adds no judgment and defeats the layer separation.

### 4.6 Failure modes and the anti-fabrication guard

Two parts.

**The guard, stated explicitly:**

> Do not compute these values yourself. The value is whatever the tool emitted. If the tool did not run, there is no value. If a required tool is unavailable, report the task as blocked. Do not estimate, and do not proceed on an assumed result.

**Named pathologies**, not "handle errors gracefully". List the specific things that look like results but are not: ipTM below 0.5 on a predicted complex, a docking score inconsistent with ligand heavy-atom count, an isoform substituted for a canonical entry, a pocket detected entirely within a disordered region.

---

## 5. What never goes in a skill

| Content | Where it belongs | Why |
|---|---|---|
| Install instructions | Shared environment; `dde doctor` | If it is not installed, fail loudly. Do not let the agent improvise a fix. |
| Endpoint URLs, project IDs | CLI environment config | `pharma_skills` ships two hardcoded endpoints with mismatched project numbers. This failure is not hypothetical. |
| Threshold values | CLI config, stamped into output | Must be program-configurable and testable. |
| API parameter enumerations | `references/`, loaded on demand, or `--help` | Progressive disclosure keeps the always-loaded surface small. |
| General domain knowledge | Nowhere. The model has it. | The lean-role principle. |
| Rate limits, retry logic | CLI | Cross-cutting. Must be enforced, not requested. |
| Role-specific next actions | The template's `agents.md` | One capability serves several roles. See §3. |

---

## 6. Converting a science skill

In order:

1. **Decide the capability.** Does this become its own dde skill, or fold into a group? Most database skills fold. Then list every role that could use it. If the list has more than one name, make sure no part of the skill assumes only the first.
2. **Move the scripts into the CLI** as `<tool> fetch` and `<tool> analyze` subcommands. Keep the logic. Change the I/O contract per `tool-design-guidance.md`.
3. **Lift threshold constants out of the script** into CLI config. `analyze_plddt.py` has five module constants. Those become named, overridable, and cited in output.
4. **Keep the interpretation prose.** The upstream `## Interpreting the Output` sections are the valuable content and mostly survive intact.
5. **Rewrite the description** with a negative clause naming dde siblings, not upstream skill names.
6. **Build the invocation table** from the new CLI subcommands.
7. **Delete** prerequisites, install steps, and the license side-effect block. Handle licensing once, centrally.
8. **Record provenance.** Upstream repo, commit, and license, in frontmatter or a `NOTICE`. Upstream is Apache 2.0 for code and CC-BY 4.0 for materials. Attribution is required and cheap.

### The pilot subset

Convert a small subset end to end before batching the rest. The pilot is **co-scientist, AlphaFold, and AlphaGenome**. The three stress different parts of the contract:

| Pilot tool | What it stresses |
|---|---|
| AlphaFold | The canonical two-phase case. Cheap fetch, deterministic analyze, thresholds, disorder advisories that redirect downstream work. |
| AlphaGenome | Variant interpretation feeding a different specialist's decision. Exercises the cross-disciplinary handoff. |
| Co-scientist | The expensive long-running case (~2 h). Proves the phase split earns its keep, and forces the question of how a skill describes a tool an agent must wait on. |

Run each against a real target. Produce a Layer 1 finding with provenance links. Then revise this document and `tool-design-guidance.md` before converting anything else.

Guidance written before a worked example is guesswork. Finding that out at skill 30 instead of skill 3 costs the whole batch.

---

## 7. Checklist

- [ ] Description has a positive clause, a use-when clause, and a negative clause naming an alternative
- [ ] The use-when clause covers every role that will use the capability
- [ ] The skill covers one capability — tools that share the rules for reading their results
- [ ] The name describes what you can find out, not what you conclude
- [ ] No sentence in the skill would be wrong for a different specialist
- [ ] Every claimed capability has a row in the invocation table
- [ ] Every invocation row shows where output lands, and every path is under `raw/`
- [ ] No threshold values in prose
- [ ] Any threshold referenced as UNRESOLVED carries the "do not invent" instruction
- [ ] No install instructions, endpoints, or credentials
- [ ] Interpretation section names every relay code the tools can emit, and what each obliges
- [ ] Relays are cited by code, not by restated warning text
- [ ] Interpretation section states consequence rules
- [ ] Anti-fabrication guard present
- [ ] Named failure pathologies, not generic error advice
- [ ] Upstream provenance and license recorded
- [ ] Body under about 200 lines. Move overflow to `references/`.
