# bimigrate — Unified BI Migration Platform

Agent-assisted migration of **Tableau, Spotfire, QlikView and Qlik Sense** estates to
**Power BI / Microsoft Fabric** (PBIP + TMDL). The platform discovers, extracts,
analyzes, maps, converts, validates and documents source functionality — not just
dashboards and visuals.

## Operating modes

| Mode | Command | Output |
|---|---|---|
| Discovery | `bimigrate discover <paths>` | inventory + complexity scoring + dedup clusters + Excel/HTML/CSV/JSON reports |
| Assessment | `bimigrate assess <paths>` | discovery + feature mapping matrix + unsupported-feature recommendations + effort estimates |
| Conversion | `bimigrate convert <paths>` | PBIP projects, TMDL semantic models, DAX measures, Power Query M, RLS proposals, per-expression decision log |
| Validation | `bimigrate validate <paths>` | accuracy per tier × complexity band, exception register, metadata round-trip checks |
| Benchmark | `bimigrate benchmark` | conversion-accuracy regression gate over a golden corpus (48 seed cases + pilot-estate extensions) |
| Collect | `bimigrate collect tableau\|qliksense` | server-side estate pull: workbooks/apps + schedules, subscriptions, alerts, streams, reload tasks |
| Web app | `bimigrate web` | interactive React UI: pick the source platform, then a guided wizard — upload → discovery → mappings → expression playground → platform-specific tools (load-script→M, IronPython triage) → convert & PBIP download |

Plus repository management: `bimigrate kb init|stats|add-rule` and `bimigrate mappings export`.

Additional engines beyond expression conversion:
- **IronPython intent classifier** (`convert/ironpython.py`): scripts stay MANUAL
  by policy, but triage is automated — Document API calls are matched against a
  mapping table (export, email, navigation, filter/marking, property writes, data
  refresh, dialogs) and each script gets a Power Automate / bookmarks / Fabric
  rebuild recommendation with per-line annotations.
- **REST collectors** (`collect/`): Tableau Server/Cloud (PAT auth; workbooks,
  schedules, subscriptions, data alerts) and Qlik Sense QRS (header auth; apps,
  streams, reload tasks). Injectable transports, stdlib-only.
- **Benchmark harness** (`validate/benchmark.py`): golden-corpus regression gate
  for the rule set; scores per tier × band, fails CI on tier drift or output
  changes, and accepts engagement-specific corpus files for pilot estates.

## Quick start

```bash
pip install -e ".[dev,web]"      # .[agents] adds the Anthropic-backed assistant
bimigrate web                    # interactive UI on http://127.0.0.1:8400
bimigrate discover ./estate --scrub --workers 8
bimigrate assess   ./estate
bimigrate convert  ./estate --out out/converted
bimigrate validate ./estate
```

## Architecture (three independently shippable tiers)

**Tier 1 — Discovery & Inventory** (`parsers/`, `engine/`, `report/`)
- Parsers: `.twb/.twbx` (full XML), `.dxp` (XML parts + heuristic binary scan),
  `.qvw/.qvs` (load-script + -prj project folders; binary best-effort),
  `.qvf/.json` (Engine-API JSON exports full-fidelity; binary best-effort).
  Every heuristic gap is recorded as a `ParseIssue`, never silently dropped.
- Bulk engine: multiprocessing pool, SQLite job persistence, per-file checkpoint
  commits (crash-safe, resumable), per-file error isolation — one corrupt file
  never kills the batch.
- Complexity scoring with effort-driver weights (nested LODs, alternate states,
  IronPython, macros score far above chart counts), tunable as data.
- Near-duplicate detection: MinHash/LSH over normalized feature shingles —
  "migrate-one, retire-many" clusters with effort-savings estimates.
- Scrub mode: stable-pseudonym servers/principals, credential/email/IP stripping
  applied *before* anything reaches the job store or reports.

**Tier 2 — Translation Engines** (`kb/`, `mapping/`, `convert/`, `emit/`)
- **Knowledge base** (SQLite + JSON + Markdown export): 606 source-function records,
  335 DAX/M target-function records (used for emitter output validation),
  **502 data-driven conversion rules** (rules are rows, not code — extend in the
  field via `bimigrate kb add-rule`).
- **Feature Mapping Repository**: 104 seeded mappings across all four platforms and
  every layer (workbook/dashboard/visual/calculation/data/security/scheduling),
  each with Power BI equivalent (or explicit `None`), complexity, automation level,
  **mandatory fallback strategy**, and confidence score. Exports to Excel/JSON.
- **Expression → DAX**: tokenizer → Pratt parser → AST → KB-driven emitter for
  Tableau (IF/CASE/LOD incl. nested), Qlik (set analysis, Aggr, TOTAL/DISTINCT,
  $-expansions, alternate states) and Spotfire (OVER navigation, THEN chains).
- **Qlik load script → Power Query M**: LOAD/SELECT/RESIDENT/INLINE/AUTOGENERATE,
  JOIN/KEEP/CONCATENATE/MAPPING/CROSSTABLE prefixes, WHERE translation, QVD
  pipeline → Lakehouse-medallion guidance, plus an unconverted-block report so
  every script line is accounted for.
- **Emitters**: PBIP folder per the Microsoft spec (`.pbip`, `definition.pbir`,
  `report.json` with best-effort layout, theme resource), TMDL semantic model
  (tables/columns/measures with display folders/partitions/relationships/roles),
  RLS proposals from Section Access / Tableau user filters / Spotfire restrictions.

**Tier 3 — Agent layer** (`agents/`)
- LLM-pluggable (`callable(prompt) -> str`); Anthropic client behind the
  `[agents]` extra. Capabilities: decision explanation (citing FeatureMapping /
  ConversionRule records), failed-conversion rewrites, DAX/M optimization
  suggestions, visual-equivalent recommendations, runbook generation.
- **Guardrails enforced in code**: agent output is *never* AUTO tier
  (capped at ASSISTED, `produced_by_agent=True`, diff-logged);
  `assert_not_auto` raises on violations.

## Confidence-tier policy (single source of truth: `models/core.py`)

| Tier | Confidence | Behavior |
|---|---|---|
| AUTO | ≥ 0.90 | converted automatically, logged for spot-check |
| ASSISTED | 0.60–0.89 | converted + flagged for human review |
| MANUAL | < 0.60 | **not** auto-converted; fallback strategy + documentation emitted |

Every `ConversionDecision` cites the binding ConversionRule and FeatureMapping
record (traceability), and Pydantic validators reject rules whose declared tier
contradicts their confidence.

## Validation honesty rules (built into the tooling)

- Accuracy reported **per tier × complexity band**, never blended.
- Numeric parity testing with configurable tolerance; structural visual checks;
  metadata round-trip checks.
- Exception Register with reason codes (`NO_EQUIVALENT`, `PARSE_FAILURE`,
  `BELOW_THRESHOLD`, `NUMERIC_MISMATCH`, …) for every MANUAL/rejected item.

## Encoded realism constraints (Sections 6/13 of the spec)

- Nested LODs and table-calc-on-table-calc: confidence capped at ASSISTED.
- Qlik **alternate states**: MANUAL always — bookmark/field-parameter redesign.
- VBScript macros, Spotfire Mods, custom viz extensions: MANUAL always.
- Qlik associative green-white-gray UX and Insight Advisor: documented as UX
  deltas / assessment-only; no mechanical equivalent is pretended.
- Pixel-perfect layout is a non-goal; structural + functional parity is the goal.
- Full-fidelity `.qvw`/`.qvf` extraction requires the -prj folder / Engine JSON
  export paths; binary containers are parsed best-effort with explicit issues.

## Web UI development

The frontend is a Vite + React app in `webui/` (built bundle is committed to
`src/bimigrate/web/static/dist` so `pip install` users need no Node toolchain):

```bash
cd webui && npm install
npm run dev      # dev server on :5173, /api proxied to bimigrate web on :8400
npm run build    # rebuild the bundle served by `bimigrate web`
```

The workspace is scoped to the selected source platform: sidebar steps, file
types, expression dialect and the mapping matrix all follow the selection, and
platform-only tools (Qlik load scripts, Spotfire IronPython) appear only where
they apply.

## Development

```bash
pip install -e ".[dev]"
pytest             # 66 tests: parsers, bulk engine, KB, conversion, emitters, collectors, benchmark, web API, CLI
ruff check src tests && black --check src tests
```

Layout: `src/bimigrate/{models,parsers,engine,kb,mapping,convert,emit,validate,agents,report}` — see module docstrings for per-subsystem design notes.

## pbi_lineage (Power BI metadata & lineage analyzer)

`src/pbi_lineage` is a second, self-contained package: a Power BI
model/report dependency and lineage analyzer (Measure Killer-class tool),
built to `docs/pbi_lineage_build_spec.md`. **All 12 milestones (1–11 plus
7b) are implemented.**

```bash
pbi-lineage ui                                      # local web UI (analyze, lineage, removal preview)
pbi-lineage analyze Sales.pbix --out ./results      # M1: local file
pbi-lineage live --pbix Sales.pbix                  # M2: attach to Desktop
pbi-lineage tenant --client-id … --out ./scan       # M5: whole tenant
pbi-lineage removal-plan Sales.pbip --object "Sales[DiscountCode]"
pbi-lineage search-m Sales.pbip dbo.FactSales       # C6 upstream impact
```

```python
from pbi_lineage import read_any
from pbi_lineage.resolve import analyze_model

result = read_any("Sales.pbip")
analysis = analyze_model(result.model, [result.report])
analysis.verdicts["column:Sales[DiscountCode]"].status   # Used / Unused / …
analysis.graph.consumers_tree("column:Sales[Qty]")       # column-level lineage
```

### What each milestone provides

| M | Module | Capability |
|---|---|---|
| 1 | `readers/` | PBIX (UTF-16 `Report/Layout`, JSON-in-JSON) and PBIP/PBIR readers; TMDL + `model.bim` inventory; thin-report detection; DataMashup M extraction |
| 2 | `connectors/` | Local AS port discovery, External Tools manifest, full TMSCHEMA + `DISCOVER_CALC_DEPENDENCY` ingest behind a pluggable executor |
| 3 | `dax.py`, `resolve.py` | Real DAX tokenizer (strings, comments, `VAR` scope, escapes) and the reference resolver; engine edges primary, parser cross-checks and logs disagreements |
| 4 | `graph.py` | Evidence-carrying dependency graph: reachability = usage, reverse traversal = lineage (C4/C5) |
| 5 | `size.py` | VertiPaq attribution (segments + dictionary + `H$`/`U$` hierarchies) reported as a low/high range; measures never get bytes |
| 6 | `removal.py` | Blast-radius preview, dependents-first TMSL delete script, enforced backup, machine-readable impact manifest |
| 7 | `service/xmla.py` | XMLA connection strings and the Pro-vs-Premium gate — Pro resolves to mode M4 with an explicit degradation notice |
| 7b | `service/thin_reports.py`, `readers/rdl.py` | dataset→consumer index, export with per-cause failure recording, **completeness gate**, paginated/AiE/composite/notebook consumers, report similarity |
| 8 | `service/scanner.py` | Scanner API: 100-workspace batching, token bucket, `Retry-After` backoff, per-batch resumable state, streaming read-back |
| 9 | `mindex.py` | M expression index: source anchors + full-text search, table/view impact at the grain the spec scopes it to |
| 10 | `rules.py`, `persist.py` | Data-driven rule engine; Section 8 SQLite schema (`evidence_json` NOT NULL), JSON export/re-import, Delta-ready row export |
| 11 | `notebook.py`, `scan.py`, `cli.py` | Headless pipeline writing JSON/SQLite or Delta in a Fabric lakehouse, plus the CLI |

### Correctness posture

The spec's rule is that a tool reporting a used object as unused is worse
than no tool, so the resolver never emits a binary verdict. Statuses are
`Used`, `Unused`, `Unused (remove manually)` (report-level measures, which
live in the report file and cannot be deleted via TOM/XMLA),
`Indeterminate (dynamic reference)`, `Indeterminate (incomplete report
coverage)`, and `Not analyzable (external consumer)` — and only exactly
`Unused` is scriptable for deletion. The §5.4.4 completeness gate is code:
one unretrievable connected report caps every would-be-Unused verdict and
blocks the delete script. Model-internal categories from §5.3
(relationship endpoints, sort-by targets, hierarchy levels, RLS
references, key columns, calculation groups, incremental refresh) are
protected with a recorded reason.

`pytest tests/test_pbil_*.py` — 133 tests, including the §5.3 hiding-place
fixtures, all seven §5.4.8 thin-report fixtures, the §13.4 destructive test
(delete everything marked unused, re-analyze, assert zero broken
references) and the §13.5 scale test (500 workspaces / 5000 reports,
rate-limited, resumable after a forced kill).

Optional extras: `.[pbi-file]` (pbixray) to read a `.pbix` model offline, `.[pbi-desktop]` (pyadomd, Windows) for live Desktop and
XMLA, `.[pbi-service]` (msal, httpx) for Service and tenant scans.
