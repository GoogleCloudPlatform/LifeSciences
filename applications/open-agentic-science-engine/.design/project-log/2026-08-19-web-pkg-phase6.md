# Phase 6: Site-Generation Skill + Template Updates

**Date:** 2026-08-19
**Branch:** `scion/web-pkg-p6`
**Issue:** #129

## What Was Done

Final phase of the web package work: created the `site-generation` skill and updated the `project-curator` template to reference it.

### Deliverables

1. **`skills/site-generation/SKILL.md`** — New skill document covering:
   - When to trigger a site build (5 trigger conditions)
   - How to invoke `venter site build` (basic and with retrospectives)
   - Content preparation requirements (executive summary, program state, findings)
   - Complete page type catalog (8 page types)
   - Full viewer catalog (14 viewers across 3 categories: native HTML, 3Dmol.js, Plotly.js)
   - Post-build verification checklist
   - Explicit scope boundaries (does not cover publishing, viewer development, or content authorship)

2. **`templates/project-curator/scion-agent.yaml`** — Added `site-generation` skill URI alongside `artifact-conventions`. Removed the hardcoded `VENTER_PROJECT` comment block (cleanup per #75/PR #106 pattern).

3. **`templates/project-curator/agents.md`** — Updated "What You Do" to reference the skill for build mechanics. Removed the inline "Website Structure" section (now owned by the skill). Retained role description, output contract, exclusions, retrospective, and communication sections.

## Design Decisions

- **Skill owns the HOW, agents.md owns the WHAT and WHEN.** The agent instructions retain enough context that the agent knows it has site-building capability and when to use it, but delegates the detailed build mechanics to the skill.
- **Followed existing skill format.** Used `program-state-management/SKILL.md` as the structural reference — same pattern of an opening paragraph, then sections documenting the contract.
- **No duplication between agents.md and skill.** The inline site directory tree was removed from agents.md since the skill now owns the page type specification. This prevents maintenance burden where CLI changes would require updates in two places.
