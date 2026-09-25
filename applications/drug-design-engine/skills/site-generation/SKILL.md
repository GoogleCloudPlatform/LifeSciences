# Site Generation

How the project curator builds, post-processes, and verifies the navigable HTML website from the drug discovery program's artifact hierarchy. This skill covers when to trigger a build, how to invoke the CLI, what the build produces, how to apply post-build fixes for known rendering gaps, and how to verify the output. It complements `artifact-conventions` (which governs the source artifacts) and `program-state-management` (which governs the Layer 2 documents that feed the site).

## When to Build

Trigger `dde site build` whenever the source material has changed in a way that should be reflected on the site:

- A new work order has been accepted
- Program state documents have been updated
- A new gate document has been written
- The executive summary has been updated
- New findings have been added

## How to Build

### Basic Build

```bash
dde site build
```

By default, `raw/` artifacts are copied into `_site/raw/` so the built site is self-contained. Viewer `?file=` URLs resolve within the site directory. To disable bundling and revert to the previous layout where viewers reference `raw/` outside the site tree, pass `--no-bundle-raw`:

```bash
dde site build --no-bundle-raw
```

### With Retrospectives (Scion Environments)

When running inside a Scion-managed environment, retrospectives live on the shared volume. Pass them explicitly so the build can include them:

```bash
dde site build --retrospectives-dir /scion-volumes/scratchpad/projects/<program>/retrospectives/
```

### Output

- Output directory: `_site/` (default, configurable via `--output-dir`)
- `_site/raw/` contains a filtered copy of the project's `raw/` data artifacts (excludes hidden files, `__pycache__`, `.pyc`, `.DS_Store`). Omitted when `--no-bundle-raw` is passed.
- Atomic build: writes to a temp directory, then moves into place
- Records build in `publish-state.json`
- Appends a `site.published` event to the event log

## Content Preparation

The CLI renders content — it does not generate it. The agent must prepare and maintain the source material before building.

### Executive Summary

- Write or update `executive/program-summary.md` before building.
- The landing page shows a truncated excerpt with a "Read more" link.

### Program State

- Maintain the four documents in `program-state/` (`active-series.md`, `liability-tracker.md`, `decision-log.md`, `open-questions.md`).
- The CLI renders each as a separate page with markdown-to-HTML conversion.

### Findings Organization

- Organize findings by discipline subdirectory: `findings/<discipline>/`.
- The sidebar groups findings by discipline automatically.
- The CLI renders markdown to HTML.

### Math in Markdown

The build supports LaTeX math rendering via the mistune math plugin and vendored KaTeX.

**Supported delimiters:**
- Inline math: `$...$` or `\(...\)`
- Display math: `$$...$$` or `\[...\]`

**Usage:**
- Standard LaTeX math commands work: `\text{}`, `\times`, `\approx`, subscripts, superscripts, Greek letters, fractions.
- Example: `$K_i \approx 5.6\ \text{nM}$` renders as formatted math.
- Use inline math for values in running text (potency, IC₅₀). Use display blocks for complex equations.

**Avoid:**
- Do not use HTML entities or Unicode workarounds for math that LaTeX handles natively.
- Do not wrap math in code fences or backtick spans — those prevent rendering.
- Do not mix delimiter styles within the same expression.

**Build interaction:**
- The mistune math plugin tokenises math spans during markdown-to-HTML conversion.
- KaTeX auto-render processes the output HTML client-side.
- KaTeX CSS, JS, and fonts are vendored alongside the site (same mechanism as viewers), so relocatable archives and offline copies render math without network access.

## Page Types

The build produces the following page types:

| Page Type | Source | Template | Notes |
|---|---|---|---|
| Landing page | All collected data | `index.html` | Executive excerpt, WO table, finding/artifact links |
| Finding | `findings/**/*.md` via accepted WOs | `finding.html` | Rendered markdown, viewer links |
| Artifact class | `raw/<class>/` via accepted WOs | `artifact.html` | File listing with viewer links |
| Work order | Accepted WOs from controlstore | `workorder.html` | Full WO metadata, linked findings/artifacts |
| Program state | `program-state/*.md` | `program_state.html` | Rendered markdown |
| Gate document | `gates/stage*/*.md` | `gate.html` | Stage metadata, rendered markdown |
| Executive summary | `executive/program-summary.md` | `executive.html` | Rendered markdown |
| Retrospectives | `--retrospectives-dir` | `retrospectives.html` | Rendered markdown, only if flag provided |

## Viewer Catalog

Because `raw/` is bundled into `_site/` by default, viewer `?file=` URLs resolve within the site directory (`../raw/...`). This means viewers work correctly in exported archives and on static file hosts without path patching. When `--no-bundle-raw` is used, viewer URLs revert to the previous external-path layout (`../../raw/...`).

The site includes 13 auto-wired specialized HTML viewers for scientific data files, plus one additional viewer not yet wired to a file extension. Viewers are standalone HTML files that load data via `?file=` query parameters. Viewers listed below are wired automatically by file extension via the `VIEWER_MAP` in `site.py` — the agent does not need to configure viewer wiring. One additional viewer (`contacts-viewer.html`) is included in the site but not yet wired to a file extension.

### Native HTML Viewers (no CDN dependencies)

| Viewer | File Pattern | What It Shows |
|---|---|---|
| `pockets-viewer.html` | `.pockets.json` | Binding pocket properties table |
| `constraint-viewer.html` | `.gnomad-constraint.json` | Genetic constraint metrics card |
| `tournament-viewer.html` | `.tournament.json` | Hypothesis ranking table |
| `brics-viewer.html` | `.brics.json` | BRICS decomposition display |

### highlight.js Viewers (CDN: cdn.jsdelivr.net)

| Viewer | File Pattern | What It Shows |
|---|---|---|
| `json-viewer.html` | `.json` (fallback) | Syntax-highlighted JSON |

### 3Dmol.js Viewers (CDN: cdn.jsdelivr.net)

| Viewer | File Pattern | What It Shows |
|---|---|---|
| `structure-viewer.html` | `.cif` | 3D molecular structure (cartoon) |
| `sdf-viewer.html` | `.3d.sdf` | 3D molecule (stick model) |
| `docking-viewer.html` | `.receptor.pdbqt` + `.poses.pdbqt` | Receptor-ligand overlay |

### Plotly.js Viewers (CDN: cdn.jsdelivr.net)

| Viewer | File Pattern | What It Shows |
|---|---|---|
| `pae-viewer.html` | `.pae.json` | Predicted Aligned Error heatmap |
| `plddt-viewer.html` | `.afdb.json` | pLDDT confidence chart |
| `expression-viewer.html` | `.tissue.json` | Tissue expression bar chart |
| `contacts-viewer.html` | `.contacts.json` *(not yet in VIEWER_MAP)* | Contact heatmap |
| `admet-viewer.html` | `.predict.json` | ADMET prediction chart |
| `docking-scores-viewer.html` | `.docking_result.json` | Docking score comparison |

**CDN constraint:** All external scripts load from `cdn.jsdelivr.net` only.

## Post-Build Processing

After `dde site build` completes and before verification, run a post-build orchestrator to fix known rendering gaps in the build output. Every fix script must be **idempotent** — safe to re-run after every build without accumulating changes.

### Why This Phase Exists

The build tool renders markdown to HTML deterministically but does not handle every rendering pattern. Several gaps recur across programs:

| Gap | Symptom | Fix Category |
|---|---|---|
| Pipe-delimited tables | Raw `\|` text instead of `<table>` elements | Structural HTML |
| Duplicate H1 | Template injects an H1; markdown `# Title` adds a second | Structural HTML |
| Shared scroll context | Sidebar and main content scroll together | CSS/styling |
| `.md` link paths | Internal links retain `.md` extension instead of `.html` | Link resolution |
| Plain-text references | Evidence/artifact references not rendered as clickable links | Link resolution |
| No theme system | Single hardcoded visual style with no user control over appearance | CSS/styling |

### Execution Model

1. Run `postbuild.py` (the orchestrator) against `_site/`.
2. The orchestrator discovers and runs fix scripts in dependency order.
3. Each script applies a **detection guard** — checks whether the fix has already been applied before modifying any file.
4. Recommended execution order:
   - Structural HTML fixes (tables, duplicate headings)
   - CSS/styling fixes (scroll context, layout)
   - Link resolution (`.md` → `.html`, artifact links)
   - Cosmetic passes (whitespace, formatting)
   - Scan/report (summary of remaining issues)

### Invoking Post-Build Processing

```bash
dde site build
python3 postbuild.py _site/
# then verify and serve
```

### Template Orchestrator

A copy-and-adapt template orchestrator is provided at [`references/postbuild-template.py`](references/postbuild-template.py). Copy it into your project's root or `site-tools/` directory, add fix functions for program-specific gaps, and invoke it after every build.

### Drop-in Theme System

[`references/fix_add_themes.py`](references/fix_add_themes.py) provides a ready-to-use theme system. It injects a theme chooser dropdown into site navigation, defines five themes (Clean, Dark, Serif, Ocean, Forest) via CSS custom properties, and persists the user's choice in `localStorage` across pages and sessions. Add its `fix_add_themes` function to the orchestrator's `FIXES` list under the CSS/styling category.

### Auto-Linking Structured Identifiers

[`references/fix_autolink_codes.py`](references/fix_autolink_codes.py) provides a template post-build fix that auto-discovers linkable codes (e.g. WO-001, DEC-005) from built filenames and replaces bare text references with `<a>` links. It avoids double-linking, self-links, and modifications inside HTML attributes, `<code>`, and `<pre>` elements. Register it in your orchestrator's FIXES list under the Link Resolution category.

## Content Boundaries

Stakeholder-facing sites must present scientific substance — not operational noise. Filter content before it reaches the built site.

### What Belongs

**Include:** Scientific methodology, experimental results, decisions and rationale, evidence chains, program flow and gate progression, liability assessments, strategic recommendations.

**Exclude:** Infrastructure failures and stack traces, tool endpoint issues (gateway errors, connection refused), retry/timeout cycles, agent orchestration mechanics (cold-start delays, scheduling artifacts), debugging output, rate-limit incidents, internal tool configuration details.

### Edit Strategy by Content Source

| Content source | Owned by this program? | Edit strategy |
|---|---|---|
| Program findings/decisions | Yes | Edit source markdown — persists across rebuilds |
| Shared-volume retrospectives | No (other agents) | Post-build HTML scrub; re-apply after each rebuild |
| Build-tool generated content | No (template) | Post-build HTML transformation |

When content is owned by this program, fix the source markdown so the correction survives future rebuilds. When it is not — retrospectives from other agents, template-generated markup — apply post-build HTML modifications and re-apply them after every rebuild.

### Scanning for Boundary Violations

Run [`references/fix_infra_refs.py`](references/fix_infra_refs.py) after each build to detect infrastructure details that leaked into generated HTML. The script scans only — it does not modify files. Review its output and apply edits using the strategy table above.

```bash
dde site build
python3 postbuild.py _site/
python3 references/fix_infra_refs.py _site/
```

## Post-Build Verification

After post-build processing, verify:

1. **Exit code** — the build succeeded (check exit code and output message).
2. **Page count** — matches expectations (reported in build output).
3. **Spot-check pages** — does the landing page show the executive excerpt? Do findings render as formatted HTML (not raw markdown)?
4. **Viewer links** — artifact pages should show "View" links for supported file types.
5. **Broken links** — the build aborts on broken internal links. Report any that were found.

## Exporting a Relocatable Archive

`dde site export` packages the built site into a standalone `.zip` archive suitable for upload to any static file host.

```bash
dde site export
dde site export -o custom-name.zip
```

- Prerequisite: run `dde site build` first. The command validates that `index.html` exists in the site directory.
- Default output: `<project-slug>-site.zip` in the project root.
- The archive contains the full `_site/` tree with `index.html` at the archive root. Hidden files and directories are excluded.
- Uses `--site-dir` to override the default `_site` source directory.
- If `raw/` is not bundled in the site directory, the command prints a warning to stderr. Viewers in the exported archive may not resolve data files without bundled `raw/`.

### Localizing CDN Resources for Offline Use

Exported archives reference external CDN resources by default (Google Fonts, 3Dmol.js, Plotly.js, highlight.js). When opened offline or on restricted networks, viewers may not function correctly. Run [`references/fix_localize_cdn.py`](references/fix_localize_cdn.py) as a post-build step to download these resources into a local `vendor/` directory and rewrite HTML references to use local paths:

```bash
dde site build
python3 fix_localize_cdn.py _site/
dde site export
```

The script is idempotent and creates a `vendor/manifest.json` listing all localized resources. See the table below for the known CDN dependencies:

| CDN Domain | Library | Used By |
|---|---|---|
| `fonts.googleapis.com` / `fonts.gstatic.com` | Google Fonts | Theme system, base styles |
| `cdn.jsdelivr.net/npm/3dmol` | 3Dmol.js | structure-viewer, sdf-viewer, docking-viewer |
| `cdn.jsdelivr.net/npm/plotly` | Plotly.js | pae-viewer, plddt-viewer, expression-viewer, admet-viewer, docking-scores-viewer |
| `cdn.jsdelivr.net/gh/highlightjs` | highlight.js | json-viewer |

## Serving the Generated Site

After building, an agent can serve the site locally and expose it through the Scion hub so stakeholders can view it in a browser.

### 1. Serve locally

Python's `http.server` is always available in Scion environments:

> **Reserved port:** Port 8080 is reserved by the Scion Hub in all Scion environments. Agents must NOT bind port 8080 for any purpose. If the chosen port is already in use, try the next candidate (e.g. 8001, 8002) rather than failing.

```bash
# Try ports in order until one binds successfully
for port in 8000 8001 8002 8003; do
  python3 -m http.server "$port" --directory _site/ &
  sleep 1
  if kill -0 $! 2>/dev/null; then echo "Serving on port $port"; break; fi
done
```

### 2. Expose via Scion

Use `sciontool expose` to register the local port with the Hub. The Hub creates a proxied URL that routes external requests to the agent's local server:

```bash
sciontool expose "$port" --label "site preview"
```

Output (port number reflects whichever port was bound by the probe loop above):

```
Port $port exposed.
URL: http://<hub>/api/v1/agents/<agent-id>/ports/$port/proxy/
Base path: /api/v1/agents/<agent-id>/ports/$port/proxy/
```

Share the returned URL with stakeholders. Use `sciontool expose --list` to see all currently exposed ports and their URLs.

### 3. Iterative rebuild-and-serve

For iterative workflows (rebuild → post-process → verify → revise), keep the server running and rebuild in place. Each `dde site build` followed by `python3 postbuild.py _site/` regenerates `_site/` and the served content updates immediately — no server restart required. The agent can stay retained for revisions in this mode.

## What This Skill Does NOT Cover

- **GCS publishing.** The `dde site export` command produces archives suitable for static hosting, but GCS-specific publishing workflow is handled by the `web-builder` template's `gcs-static-site` skill. This skill covers generation, export, and local serving/exposure in Scion environments, not production hosting.
- **Viewer development.** Viewers are checked into the repo and are not modified at build time.
- **Content authorship.** The agent prepares markdown content before building; the CLI renders it deterministically.
