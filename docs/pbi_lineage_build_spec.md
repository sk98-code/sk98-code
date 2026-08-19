# Build Spec: Power BI Metadata, Dependency & Lineage Analyzer
### (A "Measure Killer"-class tool — end-to-end build prompt)

> **How to use this file:** paste the whole thing into Claude Code / GitHub Copilot Agent as the
> opening prompt, or feed it section by section as you build each milestone. Sections marked
> `[AGENT]` are direct instructions to the coding agent. Everything else is the domain knowledge
> the agent needs and will not have.

---

## 0. Role and objective

`[AGENT]` You are building a production-grade external tool for Microsoft Power BI and Microsoft
Fabric. The tool analyzes semantic models and reports, determines which measures / columns /
tables are actually referenced, computes reclaimable model size, exposes column-level lineage
from model down to individual visual, and can scan an entire Power BI tenant.

Build it incrementally in the milestone order given in Section 12. Do not attempt the tenant-scale
features before the local analysis engine is correct — accuracy of the dependency resolver is the
whole product. A tool that reports a used measure as unused is worse than no tool at all.

**Non-goals:** do not clone Brunner BI's UI, branding, or copy. Build the capability, not the
product's look and feel.

---

## 1. Product capabilities (target state)

| # | Capability | Data needed |
|---|---|---|
| C1 | List every measure, column, table, hierarchy in a model | TOM / TMDL / TMSCHEMA DMVs |
| C2 | Determine which of those are referenced anywhere | Report layer + DAX dependency graph |
| C3 | Estimate reclaimable size per object (MB) | VertiPaq storage DMVs |
| C4 | Dependency tree: model → report → page → visual → measure → column, expandable and recursive | Joined graph of C1+C2 |
| C5 | Column-level lineage: pick a column, see every visual/filter/calc that consumes it | Reverse index of C4 |
| C6 | Table/view-level upstream lineage (which models touch a given DB object) | M (Power Query) expression index |
| C7 | Safe removal: preview impact, generate TMSL/TMDL delete script, optional apply | TOM writeback / XMLA |
| C8 | Tenant scan: all workspaces, models, reports, permissions, refresh history | Scanner API + Admin REST |
| C9 | Best-practice / health rules (broken visuals, broken DAX, implicit measures, duplicates) | Rule engine over C1–C8 |
| C10 | Export results (JSON / Parquet / Delta) and re-import for sharing | Serialization layer |

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  UI layer (desktop app or web)                                  │
│  - Dependency tree, lineage explorer, size/savings view,        │
│    tenant dashboard, removal preview                            │
└───────────────┬─────────────────────────────────────────────────┘
                │
┌───────────────▼─────────────────────────────────────────────────┐
│  Analysis core (pure, testable, no I/O)                         │
│  - Reference resolver     - Graph builder                       │
│  - DAX dependency walker  - Size attribution                    │
│  - Rule engine            - Impact analyzer                     │
└───────────────┬─────────────────────────────────────────────────┘
                │
┌───────────────▼─────────────────────────────────────────────────┐
│  Connector layer (pluggable, one interface per source)          │
│  ┌──────────┬──────────┬───────────┬──────────┬───────────────┐ │
│  │ PBIX/    │ Local AS │ XMLA      │ REST /   │ Fabric        │ │
│  │ PBIP file│ (Desktop)│ endpoint  │ Scanner  │ notebook mode │ │
│  └──────────┴──────────┴───────────┴──────────┴───────────────┘ │
└───────────────┬─────────────────────────────────────────────────┘
                │
┌───────────────▼─────────────────────────────────────────────────┐
│  Persistence: local SQLite (single scan) / Delta tables (tenant)│
└─────────────────────────────────────────────────────────────────┘
```

`[AGENT]` The connector layer must normalize everything into one internal schema (Section 8).
The analysis core must never know whether data came from a PBIX file or the Scanner API.

---

## 3. Operating modes

| Mode | Source of model | Source of report layer | License needed |
|---|---|---|---|
| **M1 Local file** | PBIX `DataModel` / PBIP TMDL | PBIX `Report/Layout` or PBIR JSON | none |
| **M2 Live Desktop** | Local AS instance (TOM + DMVs) | Open PBIX on disk | none |
| **M3 Shared online** | XMLA read endpoint | Report export via REST | **Premium / PPU / Fabric capacity** |
| **M4 Pro workspace** | `executeQueries` REST + Scanner API `datasetSchema` | `GetFileAsPbix` export | Pro (degraded fidelity) |
| **M5 Tenant scan** | Scanner API (all workspaces) | Optional per-report export | Fabric capacity + admin rights |
| **M6 SSAS / AAS** | XMLA to on-prem or Azure AS | n/a | n/a |

> **Critical constraint:** the XMLA endpoint is **not available on Power BI Pro workspaces**.
> Any tenant-wide capability that depends on full model metadata therefore requires Premium/PPU/
> Fabric capacity. Design M4 as an explicitly degraded mode and label its results as such in the UI
> rather than silently producing lower-confidence output.

---

## 4. Reading the model

### 4.1 PBIX file structure

A `.pbix` is an OPC (zip) package. Relevant parts:

| Part | Contents | Notes |
|---|---|---|
| `Report/Layout` | Report definition JSON | **UTF-16 LE encoded**, no BOM handling in some writers |
| `DataModel` | ABF backup of the VertiPaq model | Binary; not present in live-connect reports |
| `DataMashup` | Power Query (M) queries | Itself a nested zip containing `Formulas/Section1.m` |
| `Metadata`, `Settings`, `Connections` | Connection + misc metadata | `Connections` reveals live-connect target |
| `Report/StaticResources/` | Themes, custom visual packages | Theme JSON useful for standards checks |
| `SecurityBindings`, `Version` | — | Version gates parser behaviour |

`[AGENT]` Prefer **PBIP / PBIR** (`.pbip` project format) when available — the model is plain TMDL
under `<name>.SemanticModel/definition/` and each visual is its own JSON under
`<name>.Report/definition/pages/<page>/visuals/<visual>/visual.json`. This is dramatically easier
and more reliable to parse than the legacy `Report/Layout` blob. Support both; auto-detect.

For legacy `DataModel`: do **not** try to parse the ABF binary. Instead, either
(a) require the file to be open in Power BI Desktop and use mode M2, or
(b) use an existing extractor library (see Section 11).

### 4.2 Live model via local Analysis Services (Mode M2)

Power BI Desktop hosts a local AS instance on a random port.

- Discover port from:
  `%LOCALAPPDATA%\Microsoft\Power BI Desktop\AnalysisServicesWorkspaces\*\Data\msmdsrv.port.txt`
  (Store version path differs — handle `%LOCALAPPDATA%\Packages\Microsoft.MicrosoftPowerBIDesktop_*\LocalCache\...`)
- Connect: `Data Source=localhost:<port>` via `Microsoft.AnalysisServices.Tabular` (TOM) or ADOMD.
- Database name is a GUID; enumerate `server.Databases` and take the single one.

**External Tools integration.** Register the tool by writing a `.pbitool.json` manifest to:
`C:\Program Files (x86)\Common Files\Microsoft Shared\Power BI Desktop\External Tools\`

```json
{
  "version": "1.0",
  "name": "Lineage Analyzer",
  "description": "Model dependency and lineage analysis",
  "path": "C:\\Program Files\\YourTool\\YourTool.exe",
  "arguments": "--server \"%server%\" --database \"%database%\"",
  "iconData": "data:image/png;base64,..."
}
```

Power BI substitutes `%server%` and `%database%` at launch. **Note:** a portable / no-admin install
cannot write to that directory, so it will not appear in the External Tools ribbon — support both a
manifest install and a manual "attach to running Desktop" fallback that does port discovery itself.

### 4.3 Model metadata via DMVs

Query these against the AS instance (local or XMLA). They are the fastest path to a full inventory:

```sql
-- Object inventory
SELECT * FROM $SYSTEM.TMSCHEMA_TABLES
SELECT * FROM $SYSTEM.TMSCHEMA_COLUMNS
SELECT * FROM $SYSTEM.TMSCHEMA_MEASURES
SELECT * FROM $SYSTEM.TMSCHEMA_RELATIONSHIPS
SELECT * FROM $SYSTEM.TMSCHEMA_HIERARCHIES
SELECT * FROM $SYSTEM.TMSCHEMA_LEVELS
SELECT * FROM $SYSTEM.TMSCHEMA_PARTITIONS
SELECT * FROM $SYSTEM.TMSCHEMA_CALCULATION_GROUPS
SELECT * FROM $SYSTEM.TMSCHEMA_CALCULATION_ITEMS
SELECT * FROM $SYSTEM.TMSCHEMA_ROLES
SELECT * FROM $SYSTEM.TMSCHEMA_TABLE_PERMISSIONS   -- RLS/OLS expressions
SELECT * FROM $SYSTEM.TMSCHEMA_EXPRESSIONS         -- shared M expressions / parameters
```

### 4.4 The dependency DMV — your single most valuable query

```sql
SELECT * FROM $SYSTEM.DISCOVER_CALC_DEPENDENCY
```

Returns rows of `OBJECT_TYPE, TABLE, OBJECT, EXPRESSION, REFERENCED_OBJECT_TYPE,
REFERENCED_TABLE, REFERENCED_OBJECT`. This gives you the engine's own resolution of
measure→measure, measure→column, calculated column→column, calculated table→source,
relationship→columns, and M partition→query dependencies.

`[AGENT]` Use this as the **primary** source of intra-model dependencies. It is more accurate than
any parser you write, because it is the engine's own view. Build your DAX parser (Section 6) as a
**supplement** for the cases CALC_DEPENDENCY does not cover — report-level measures (which do not
exist in the model at all) and dynamic reference patterns — and as a cross-check that logs
disagreements rather than silently overriding.

### 4.5 Size attribution (capability C3)

```sql
SELECT * FROM $SYSTEM.DISCOVER_STORAGE_TABLE_COLUMN_SEGMENTS      -- USED_SIZE per segment
SELECT * FROM $SYSTEM.DISCOVER_STORAGE_TABLE_COLUMNS              -- dictionary size
SELECT * FROM $SYSTEM.DISCOVER_STORAGE_TABLE_COLUMN_HIERARCHIES   -- attribute hierarchy size
SELECT * FROM $SYSTEM.DISCOVER_STORAGE_TABLES                     -- row counts
```

Total column cost = data segments + dictionary + attribute hierarchy + user hierarchy share.
This is the same method VertiPaq Analyzer uses. Report savings as a **range**, not a single number:
removing a column also removes its dictionary but may not shrink compressed neighbours predictably.

> Measures cost essentially zero storage. Never present "MB saved" for a measure — present measures
> as a maintainability/clarity win and columns as a size win. Conflating the two is the most common
> way these tools mislead people.

---

## 5. Reading the report layer

This is where the real work is. A field is "used" if it appears in **any** of the following.

### 5.1 Legacy `Report/Layout` parsing

The structure is JSON-in-JSON — several fields are strings containing escaped JSON. Parse
recursively.

```
layout
├── config                      (JSON string → report-level settings, themes, bookmarks)
├── filters                     (JSON string → report-level filters)
├── resourcePackages            (themes, custom visuals)
└── sections[]                  (pages)
    ├── config                  (JSON string → page settings)
    ├── filters                 (JSON string → page-level filters)
    └── visualContainers[]
        ├── config              (JSON string) →
        │   └── singleVisual
        │       ├── visualType
        │       ├── projections{}        ← primary field bucket references
        │       ├── prototypeQuery       ← resolved query: From[] + Select[]
        │       ├── columnProperties{}
        │       ├── objects{}            ← conditional formatting, data labels, titles
        │       ├── vcObjects{}          ← visual container objects
        │       └── syncGroup            ← sync slicers
        ├── filters             (JSON string → visual-level filters)
        ├── query / dataTransforms
        └── x, y, z, width, height
```

`prototypeQuery` is the most reliable place to read actual field references: `From` gives table
aliases, `Select` gives `Column`/`Measure`/`Aggregation` entries with `{ Expression: { SourceRef:
{ Source: "t" } }, Property: "ColumnName" }`. Resolve the alias through `From` to get the real
table.

### 5.2 PBIR (new format) parsing

Each visual is a discrete `visual.json`. Far cleaner. Same conceptual fields, stable schema,
diff-friendly. Parse this preferentially.

### 5.3 Every place a reference can hide

`[AGENT]` Implement a check for each of these. Missing any one of them produces a false "unused"
result, which is the failure mode that destroys trust in the tool. Write one test fixture per item.

**Within visuals**
- [ ] Field wells / projections (Values, Axis, Legend, Tooltips, Small multiples)
- [ ] Visual-level filters, including filters on fields not shown in the visual
- [ ] Conditional formatting rules (`objects` → color/dataBars/icons `→ FillRule`)
- [ ] Dynamic titles / dynamic subtitles / dynamic alt text (measure-driven `expr`)
- [ ] Data labels with custom measure
- [ ] Reference lines, error bars, constant lines driven by measures
- [ ] Sort-by field (`orderBy` may reference an unprojected field)
- [ ] Tooltip fields and report-page tooltips (fields on the *tooltip page*)
- [ ] Custom visual projections — third-party visuals have non-standard capability schemas; fall
      back to a generic deep-scan for `SourceRef`/`Property` pairs anywhere in the container
- [ ] Web URL / image URL fields, drill-through category fields
- [ ] Slicers, including slicers on hidden pages and sync-slicer groups

**Report-scoped**
- [ ] Page-level and report-level filters
- [ ] Drillthrough filters and drillthrough target pages
- [ ] Bookmarks — a bookmark can pin a filter state referencing a field not otherwise visible
- [ ] Hidden pages and hidden visuals (**used, not unused** — flag separately, never auto-delete)
- [ ] Report-level measures (defined in the report, not the model — only exist in the layout)
- [ ] Q&A linguistic schema / synonyms
- [ ] Themes with data-color rules bound to fields

**Model-internal (never mark unused)**
- [ ] Columns participating in relationships (active *or* inactive)
- [ ] Sort-by-column targets (`SortByColumn` property)
- [ ] Columns in hierarchies (and the hierarchy's own usage)
- [ ] RLS / OLS filter expressions in `TMSCHEMA_TABLE_PERMISSIONS`
- [ ] Incremental refresh policy columns (`RangeStart` / `RangeEnd` and the partition column)
- [ ] Field parameter tables — a calculated table using `NAMEOF()` holds references that
      CALC_DEPENDENCY reports but that look like a self-contained table
- [ ] Calculation groups — `SELECTEDMEASURE()` / `SELECTEDMEASURENAME()` means the calc item
      depends on **every** measure; treat as a wildcard edge, not zero edges
- [ ] Dynamic format string expressions
- [ ] Detail Rows expressions
- [ ] Default label / default image columns (`DataCategory`)
- [ ] Key columns, `GroupByColumns` (composite model / calculation group hierarchies)
- [ ] Calculated columns/tables referencing the object
- [ ] What-if parameter tables and their generated measures

**Outside the PBIX entirely — the reason tenant scan exists**
- [ ] Other reports live-connected to this shared model (possibly in other workspaces)
- [ ] Paginated (RDL) reports querying the model — parse the RDL's DAX/MDX query text
- [ ] Excel workbooks connected via Analyze in Excel (Activity log / Scanner data)
- [ ] Composite models chaining on top of this model (DirectQuery for Power BI semantic models)
- [ ] Fabric notebooks / semantic-link (`sempy`) code referencing table and column names
- [ ] Dataflows and downstream Fabric items
- [ ] Embedded reports and API-driven `executeQueries` consumers (unknowable — disclose the gap)

> **Design rule:** never present a binary used/unused. Use a confidence status:
> `Used` · `Unused` · `Unused (remove manually)` · `Indeterminate (dynamic reference)` ·
> `Not analyzable (external consumer)`. Objects in the last two states must be excluded from any
> bulk-delete script by default.

---

### 5.4 Thin reports and shared semantic models — the core problem

A **thin report** is a report with a live connection to a shared semantic model and **no embedded
model of its own**. This is the single hardest and most important case in the entire tool, because
a shared model's usage is the *union* of usage across every thin report connected to it — and those
reports can live in workspaces you have not looked at.

`[AGENT]` Treat this section as the correctness backbone. Everything in §5.3 still applies *per
report*; this section is about doing that across N reports and combining the results safely.

#### 5.4.1 Detecting a thin report

| Signal | Where |
|---|---|
| No `DataModel` part in the PBIX zip | file inspection |
| `Connections` part contains a `pbiazure://api.powerbi.com` connection string with a `datasetId` | PBIX |
| PBIP: `definition.pbir` uses `byConnection` (remote model) rather than `byPath` (local model) | PBIP project |
| Service: `report.datasetId` points to a dataset in a different workspace than the report | Scanner API |
| `report.datasetWorkspaceId` present and ≠ `report.workspaceId` | Scanner API |

Also detect the inverse relationship you will need constantly: for a given `datasetId`, the set of
all reports whose `datasetId` matches. Build this index once per tenant scan and key everything off
it.

#### 5.4.2 Report-level measures — the objects that live nowhere else

A report-level measure is DAX defined **inside the thin report**, not in the model. It is invisible
to TOM, to XMLA, and to `DISCOVER_CALC_DEPENDENCY`. You can only get it by parsing the report file.

- **Legacy `Report/Layout`:** under the report `config` → `modelExtensions[]` → `entities[]` →
  `measures[]`, each with `name`, `expression`, `hidden`, and the `entity` (model table) it is
  attached to.
- **PBIR:** `definition/reportExtensions.json`, same conceptual shape, much easier to read.

Why they matter for correctness:

1. A report-level measure's DAX **references model columns and model measures**. If you skip them,
   you will mark model objects unused that are in fact consumed. This is a guaranteed false positive.
2. They must be fed into the dependency graph as first-class nodes (`measure` with
   `is_report_level = true`, `host_report_id` set) so the graph walk reaches through them.
3. They **cannot be deleted through TOM or XMLA** — they are in the report file. An unused
   report-level measure therefore gets status `Unused (remove manually)`, never an auto-delete
   script entry.
4. They frequently reference columns that appear in *no visual at all* — this is the classic case
   where a naive tool deletes a column and breaks a report.

`[AGENT]` Parse report-level measures **before** running the resolver, for every thin report on the
model, and merge them into the graph. Order matters.

#### 5.4.3 Discovery and retrieval of all thin reports on a model

```
1. Scanner API → build index: dataset_id → [ (report_id, workspace_id, report_type) ]
2. For each report:
     a. Try  GET /groups/{ws}/reports/{rid}/Export           (thin report → PBIX with layout only)
     b. On failure, fall back to admin export / mark as unretrievable
3. Parse each layout with the §5.3 checklist
4. Union all references → single usage verdict per model object
```

Export failures are common and expected. Handle and **record** each cause:

- Report has a sensitivity label with encryption → export blocked
- Report created directly in the Service with certain features
- Paginated (RDL) reports — different endpoint, different parser (see 5.4.5)
- Usage Metrics reports — auto-generated, skip
- Reports in another user's *My Workspace* — visible to admin API, often not exportable
- Reports in an App (the App copy vs the workspace original)
- Export API throttling — long-running export jobs, poll with backoff

#### 5.4.4 The completeness gate — non-negotiable

> **A shared model's objects may only be reported as `Unused` if every single connected report was
> successfully retrieved and parsed.**

`[AGENT]` Implement this as a hard gate in code, not a warning in the UI:

```python
retrievable = all_reports_for(dataset_id)
parsed      = successfully_parsed(retrievable)

if parsed < retrievable:
    # every object's status is capped
    status = "Indeterminate (incomplete report coverage)"
    blocked_from_delete_script = True
    surface_in_ui(missing=retrievable - parsed, reason_per_report=True)
```

The UI must show, per model: *"14 of 17 connected reports analyzed — 3 could not be retrieved"*
with the list and the reason for each. Never aggregate this into a silent percentage. A user who
deletes columns based on 14 of 17 reports will break the other 3, and that is the failure that ends
the tool's adoption inside a company.

Permission reality: a service principal with `Tenant.Read.All` can *enumerate* every report but not
necessarily *export* every one. Run the tool with an account that can do both, and detect and
report the difference explicitly at scan start.

#### 5.4.5 Other consumer types on the same shared model

Each of these consumes model objects and must be unioned into the usage verdict:

| Consumer | How to detect | How to parse |
|---|---|---|
| **Thin PBI reports** | `datasetId` match | Export PBIX → layout (§5.3) |
| **Paginated (RDL)** | Scanner API item type | Download RDL (XML); parse `<CommandText>` for DAX/MDX; resolve `Table[Column]` and `[Measure]` references. Note the false-negative trap: measures referenced as `Table[Measure]` inside a SQL-style query text |
| **Excel — Analyze in Excel** | Activity log events; Scanner `getArtifactUsers` | Cannot parse the workbook (lives outside the tenant). Record as `Not analyzable (external consumer)` and **exclude the whole model from confident unused verdicts** if any AiE connection exists |
| **Composite / DirectQuery-over-PBI** | Downstream model's partitions reference this model | Recurse: the downstream model is itself a consumer, and *its* thin reports are transitive consumers |
| **Fabric notebooks / `sempy`** | Notebook code in Scanner results | Full-text search for table and column names; low confidence, flag only |
| **Embedded / `executeQueries` API** | Activity log only | Unknowable — disclose per §14 |
| **Dataflows / downstream Fabric items** | Scanner lineage | Node in graph, not a field-level consumer |

#### 5.4.6 Impact analysis before deletion

Because one model backs many reports, every proposed deletion needs a blast-radius preview:

```
Proposed: remove column Sales[DiscountCode]
  → breaks 0 visuals
  → breaks 1 report-level measure  (Report "EMEA Margin" → [Adj Margin])
  → breaks 0 RLS expressions
  → 2 reports could not be verified (export failed)
  → VERDICT: BLOCKED — resolve coverage first
```

`[AGENT]` The removal script generator must emit, alongside the TMSL/TMDL, a machine-readable
impact manifest listing every downstream report affected, so it can be attached to a change
request. In an enterprise this artifact is what gets the change approved.

#### 5.4.7 Thin-report-specific analyses worth building

Once you have all thin reports parsed, these fall out almost for free and are high value:

- **Report similarity / duplicates** — compare page + visual + field-reference sets across reports
  on the same model (Jaccard over the reference multiset). Finds the "everyone forked the same
  report" problem.
- **Orphaned thin reports** — connected to a model that no longer exists or was replaced.
- **Reports never viewed** — join to Activity log; a thin report nobody opens is itself deletable.
- **Field usage frequency across the estate** — a column used in 1 visual out of 400 is a
  consolidation candidate even though it is technically "used".
- **Model consolidation candidates** — two models with near-identical schemas each carrying a
  handful of thin reports.

#### 5.4.8 Test fixtures required for this section

Add to the §13 corpus:

1. Thin report whose only reference to a column is inside a **report-level measure**
2. Thin report referencing a column only in a **visual-level filter on a hidden page**
3. Two thin reports on one model where the union is used but each individually looks unused
4. A model with one thin report that **fails to export** → assert the gate blocks all verdicts
5. A composite model chained on the shared model, with its own thin report
6. A paginated report referencing a measure as `Table[Measure]` in its query text
7. A model with an Analyze-in-Excel connection → assert reduced-confidence status

---

## 6. The DAX dependency resolver

`[AGENT]` Do **not** use regular expressions to find references. It will fail on comments, string
literals containing bracket characters, escaped quotes, `VAR` shadowing, and table names with
spaces or reserved words.

Approach, in order of preference:

1. **CALC_DEPENDENCY DMV** for everything already in the model (Section 4.4).
2. **A real tokenizer + parser** for report-level measures and validation. Build a lexer handling:
   - `'Table Name'[Column]`, `[Measure]`, `Table[Column]` (unquoted where legal)
   - String literals `"..."` with `""` escaping — bracket chars inside are **not** references
   - Comments `--`, `//`, `/* */`
   - `VAR name = ... RETURN` — `name` is a local, not a measure; track scope
   - Function-name lookalikes and reserved words
3. **Wildcard/dynamic edges** — patterns you must detect and mark `Indeterminate` rather than
   resolve:
   - `SELECTEDMEASURE()`, `SELECTEDMEASURENAME()` → depends on all measures in scope
   - `NAMEOF()` inside field parameter tables → resolve via CALC_DEPENDENCY
   - String-based lookups (`SWITCH` returning measure values chosen by a slicer value)
   - `EVALUATE` in report-level query overrides
   - Calculation group application (`CALCULATE(SELECTEDMEASURE(), ...)`)

Then build the graph:

```
Node  = (kind, id, name, parent)      kind ∈ {model, table, column, measure, hierarchy,
                                              report, page, visual, filter, role, calc_item}
Edge  = (from_node, to_node, edge_kind, confidence)
        edge_kind ∈ {projects, filters, formats, sorts, relates, defines, wildcard}
```

Reachability = an object is **Used** if a path exists from any *root* to it. Roots are: visuals,
filters, RLS expressions, relationships, refresh policy, and any external consumer discovered in
tenant scan. Walk the graph transitively — a measure used only by another used measure is used.

Column-level lineage (C5) is the **reverse** traversal of this same graph from a chosen column node.
Do not build a second structure for it.

---

## 7. Power BI Service and tenant scan

### 7.1 Authentication
- Entra ID app registration. Support both delegated (MSAL interactive/device code) and
  **service principal** flows.
- Admin APIs require the tenant setting *"Allow service principals to use read-only Power BI admin
  APIs"* to be enabled, with the SP in the permitted security group. Detect and report a clear error
  when it is not — this is the #1 setup failure.
- Scopes: `Tenant.Read.All` for admin read; `Dataset.Read.All`, `Report.Read.All` for delegated.

### 7.2 Scanner API (the tenant-scale workhorse)

```
POST /v1.0/myorg/admin/workspaces/getInfo
     ?lineage=true&datasourceDetails=true&datasetSchema=true
     &datasetExpressions=true&getArtifactUsers=true
     body: { "workspaces": ["<up to 100 workspace ids>"] }
  → returns scanId

GET  /v1.0/myorg/admin/workspaces/scanStatus/{scanId}      → poll until Succeeded
GET  /v1.0/myorg/admin/workspaces/scanResult/{scanId}      → full metadata payload
GET  /v1.0/myorg/admin/workspaces/modified?modifiedSince=  → incremental delta
```

**Limits to design around:** ~500 `getInfo` requests/hour, ~16 concurrent scans, 100 workspaces per
call, and `scanResult` payloads that get very large. Implement: workspace batching, a token-bucket
rate limiter, exponential backoff on 429 honouring `Retry-After`, resumable scans persisted to disk,
and streaming JSON parsing rather than loading the whole payload into memory.

`datasetSchema=true` gives you tables, columns and measures **without** an XMLA connection — this is
how you get partial coverage of Pro workspaces (mode M4). It does not give you DAX expressions for
all object types or VertiPaq sizes, so mark those results as reduced-confidence.

### 7.3 Other Admin REST endpoints worth wiring
- `GET /admin/groups?$top=5000&$expand=users,reports,datasets,dataflows` — workspaces + access
- `GET /admin/datasets/{id}/datasources` — connection details
- `GET /admin/activityevents?startDateTime=&endDateTime=` — usage (30-day retention; archive to
  Delta if you want history). This is how you detect *reports nobody opens* and *Analyze in Excel*
  consumers.
- `GET /admin/capacities`, refreshables endpoints — capacity/CU and refresh history
- `GET /groups/{gid}/reports/{rid}/Export` — download PBIX for report-layer parsing (fails for
  reports with certain connection types; handle gracefully)

### 7.4 Writeback / removal (C7)
- **Desktop:** TOM — `model.Tables[t].Measures.Remove(name)` then `model.SaveChanges()`.
- **Service:** XMLA write endpoint (Premium/PPU/Fabric only), TMSL `alter`/`delete` scripts.
- Removal order matters: drop dependent calculated columns/hierarchies/relationships before the
  column itself, or the commit fails mid-way.
- **Always** generate a reversible script and a pre-change backup (TMSL `backup` or a full model
  serialization) before applying anything. Default the UI to *generate script*, not *apply*.

---

## 8. Internal schema

Persist to SQLite (single scan) or Delta tables (tenant scan) with this shape:

```
tenant(tenant_id, scanned_at)
workspace(workspace_id, name, type, capacity_id, state)
model(model_id, workspace_id, name, storage_mode, size_bytes, created, last_refresh)
table(table_id, model_id, name, is_hidden, is_calculated, row_count, size_bytes)
column(column_id, table_id, name, data_type, is_hidden, is_calculated, is_key,
       sort_by_column_id, dictionary_bytes, data_bytes, hierarchy_bytes, expression)
measure(measure_id, table_id, name, expression, format_string, is_hidden, display_folder,
        is_report_level, host_report_id)
relationship(rel_id, model_id, from_column_id, to_column_id, is_active, cardinality)
report(report_id, workspace_id, name, model_id, report_type)   -- pbi | paginated | excel
page(page_id, report_id, name, display_name, is_hidden, ordinal)
visual(visual_id, page_id, visual_type, title, x, y, w, h, is_hidden)
reference(ref_id, source_node_id, target_node_id, edge_kind, confidence, evidence_json)
usage(object_id, status, reason, reclaimable_bytes)
m_expression(expr_id, model_id, item_name, m_code)   -- for C6 upstream search
finding(finding_id, rule_id, severity, object_id, message)
```

`evidence_json` is not optional. Every edge must record *where* the reference was found (page,
visual, property path) so the UI can justify each verdict. "Trust me" is not acceptable output for a
tool whose recommendation is *delete this*.

---

## 9. Rule engine (C9)

Implement as data-driven rules over the schema, not hardcoded checks:

- Implicit measures in use (aggregations on raw columns instead of explicit measures)
- Broken visuals (reference a field that no longer exists in the model)
- Broken DAX (measure references a deleted object)
- Duplicate / near-duplicate semantic models (compare table+column+measure name sets, Jaccard)
- Duplicate reports (compare page and visual structure)
- Auto date/time tables enabled (large hidden size cost)
- Bi-directional relationships, many-to-many
- Columns with high cardinality and no usage
- Unused custom visuals still packaged in the report
- Models never refreshed / reports never viewed in N days
- Missing display folders, inconsistent format strings, unsorted date columns

---

## 10. Column-level lineage UI (C4/C5)

Two views over one graph:

**Dependency tree (downward)** — expandable hierarchy: model → report → page → visual → measure →
column, recursive. Provide collapsed modes that flatten to a ranked list by a single element type
(report / page / visual / measure) ordered by reclaimable impact, so a user can pick the grain that
matches the deletion decision they are making.

**Lineage explorer (upward + downward)** — select any column or measure; show:
- upstream: source table/view → M query steps → model column (Section 6 of the M index)
- downstream: every measure, calculated column, visual, filter, page, report that consumes it

`[AGENT]` For upstream lineage without a full M parser, ship the pragmatic version first: index
every model's and dataflow's M code, and provide full-text search over it so a user can search a
table or view name and get every item referencing it. This is table/view-grain, not column-grain,
but it is genuinely useful for planning a schema change and it is achievable in days rather than
months. Column-grain upstream lineage requires parsing M step-by-step through renames, merges,
expands, pivots and custom columns — treat that as a separate later project.

---

## 11. Tech stack

**Track A — .NET (recommended for fidelity)**
- C# / .NET 8, WPF or Avalonia (cross-platform) UI
- `Microsoft.AnalysisServices.Tabular` (TOM) — first-class, official, handles writeback
- `Microsoft.AnalysisServices.AdomdClient` — DMV queries
- `Microsoft.Identity.Client` (MSAL) — auth
- `System.IO.Packaging` / `System.IO.Compression` — PBIX
- Serilog, SQLite (`Microsoft.Data.Sqlite`)

**Track B — Python (recommended if it must run in Fabric notebooks)**
- `pythonnet` + TOM assemblies, **or** `sempy` / `semantic-link-labs` (excellent for Fabric-native
  work — already wraps many DMVs and REST calls)
- `pyadomd` for DMV queries, `msal` for auth
- `pbixray` for PBIX/VertiPaq extraction without a live instance
- `httpx` + `tenacity` for REST with retry, `polars`/`duckdb` for graph joins at scale
- Delta tables in a Fabric lakehouse for tenant scan output — then the results are themselves a
  Power BI model you can build a governance report on

**Recommendation:** build the *analysis core* in Python so it runs both on the desktop and inside a
Fabric notebook on a schedule. Use a thin .NET shim only for TOM writeback, which Python cannot do
cleanly. This matches the automation-first direction the commercial tools have taken and fits a
Databricks/PySpark skill set.

---

## 12. Milestones

`[AGENT]` Deliver in this order. Each milestone must ship with tests before moving on.

| M | Deliverable | Definition of done |
|---|---|---|
| **1** | PBIX/PBIP reader | Extracts model inventory + report layout from both formats; handles UTF-16 layout; 20 real-world files parse without error |
| **2** | Local AS connector | Port discovery, DMV queries, full TMSCHEMA + CALC_DEPENDENCY ingest |
| **3** | Reference resolver | Every checklist item in §5.3 has a passing fixture test; no false "unused" across a 10-report regression corpus |
| **4** | Graph + reachability | Dependency tree renders; every verdict carries evidence |
| **5** | Size attribution | Reclaimable bytes per column within 5% of VertiPaq Analyzer for the same model |
| **6** | Removal preview + script | TMSL/TMDL generated; dry-run diff; backup enforced |
| **7** | XMLA / Service mode | Shared online model analysis on a Premium workspace |
| **7b** | **Thin report engine** (§5.4) | dataset→reports index built; report-level measures parsed and in the graph; completeness gate enforced and provably blocking; all 7 fixtures in §5.4.8 passing |
| **8** | Scanner API tenant scan | 500-workspace tenant scans without hitting rate limits; resumable |
| **9** | M expression index | Full-text search over all M; table/view impact analysis |
| **10** | Rule engine + export | Findings persisted; JSON/Delta export; re-import for sharing |
| **11** | Scheduled Fabric notebook mode | Runs headless, writes Delta, feeds a governance Power BI report |

---

## 13. Testing strategy (do not skip)

`[AGENT]` The product's only real asset is correctness. Build:

1. **Fixture corpus** — a set of small PBIX/PBIP files each isolating one edge case from §5.3
   (one with a field parameter, one with a calculation group, one with conditional formatting on an
   unprojected measure, one with a report-level measure, one with a sync slicer, etc.). Each has a
   hand-written expected used/unused manifest.
2. **Golden regression** — a handful of large real reports with reviewed manifests. Any change to
   the resolver that flips a verdict must be explained before merge.
3. **Differential testing** — for models where CALC_DEPENDENCY and your parser both have an opinion,
   assert agreement; log and triage every disagreement.
4. **Destructive test** — actually delete everything the tool marks unused, refresh, and open every
   connected report. Zero broken visuals is the pass condition. Automate this against the corpus.
5. **Scale test** — synthetic tenant with 500 workspaces / 5000 reports; assert scan completes,
   respects rate limits, and resumes correctly after a forced kill.

---

## 14. Known hard limits — disclose these in the UI

Be explicit with users about what the tool cannot know. Silent gaps are how governance tooling
loses credibility.

- API-driven consumers (`executeQueries`, embedded apps, custom clients) are invisible.
- Excel workbooks stored outside the tenant that connect via Analyze in Excel may be missed.
- Dynamic measure selection driven by user input cannot be statically resolved.
- Activity log retention is 30 days; anything older needs prior archival.
- Pro workspaces yield reduced-fidelity results (no XMLA, no VertiPaq sizes).
- A column unused *today* may be used by a report created tomorrow — recommend re-scanning on a
  schedule rather than treating one scan as permanent truth.

---

## 15. First prompt to give the coding agent

> Build Milestone 1 from the attached spec: a Python package `pbi_lineage` with a `readers`
> subpackage exposing `read_pbix(path)` and `read_pbip(path)`, both returning the normalized
> `Model` and `ReportLayout` dataclasses defined in Section 8. Handle the UTF-16 encoding of
> `Report/Layout`, the nested JSON-in-JSON `config` fields, and auto-detection between legacy and
> PBIR formats. Include pytest fixtures for at least five edge cases from Section 5.3. Do not
> implement the resolver yet.
