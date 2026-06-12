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

Plus repository management: `bimigrate kb init|stats|add-rule` and `bimigrate mappings export`.

## Quick start

```bash
pip install -e ".[dev]"          # or .[agents] for the Anthropic-backed assistant
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
- **Knowledge base** (SQLite + JSON + Markdown export): 457 source-function records,
  321 DAX/M target-function records (used for emitter output validation),
  **372 data-driven conversion rules** (growing toward the 500+ target; rules are
  rows, not code — extend in the field via `bimigrate kb add-rule`).
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

## Development

```bash
pip install -e ".[dev]"
pytest             # 51 tests: parsers, bulk engine, KB, conversion, emitters, CLI
ruff check src tests && black --check src tests
```

Layout: `src/bimigrate/{models,parsers,engine,kb,mapping,convert,emit,validate,agents,report}` — see module docstrings for per-subsystem design notes.
