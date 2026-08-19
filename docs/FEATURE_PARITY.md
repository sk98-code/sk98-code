# Feature parity against the Measure Killer comparison sheet

Mapped from *Compare — Measure Killer Free vs Enterprise vs Automation*.
Status is what **this repo** does today, verified by the test suite unless
noted otherwise.

Legend: **Yes** — built and tested · **Partial** — works with a stated
limit · **Engine only** — implemented and tested, no UI yet ·
**Needs tenant** — cannot be built or verified without a real Power BI
tenant · **No** — not built.

## Scan scope

| Feature | Status | Notes |
|---|---|---|
| Power BI Desktop file (.pbix, .pbip) | **Yes** | `.pbix` model needs the `pbi-file` extra (pbixray) |
| Shared semantic models in Service | **Partial** | `service/xmla.py` + Service UI; XMLA read needs Premium/PPU/Fabric and has never run against a real tenant |
| Online thin / live reports | **Engine only** | `service/thin_reports.py`, export + parse |
| Paginated reports | **Engine only** | `readers/rdl.py`, DAX/MDX query text |
| Excel · Analyze in Excel | **Partial** | Detected from the activity log and used to cap confidence; workbooks live outside the tenant and are never parsed |
| Composite & child models | **Partial** | Detected and treated as an unparseable consumer that caps confidence; recursion into the downstream model is not automatic |
| Personal, Pro and Premium workspaces | **Engine only** | Pro resolves to degraded mode M4 with an explicit notice |
| Microsoft Fabric items | **Partial** | Dataflows and notebooks inventoried; lakehouses/warehouses counted only |
| Analysis Services (SSAS / Azure AS) | **Engine only** | Mode M6 via the XMLA connection string; never run against a real server |
| Workspace-level scan | **Engine only** | `ScannerClient.scan(workspace_ids)` |
| Full tenant-wide scan | **Engine only** | Batching, rate limits, resumable; scale-tested against a synthetic 500-workspace tenant |

## Data lineage

The vendor docs describe lineage at three granularities today, with a
fourth in development. Mapped to this repo:

| Granularity | Their answer | Ours |
|---|---|---|
| Server & database — item level | Lineage tab (2 views), Enterprise | **Lineage tab**, both views |
| Table & view | M Expressions search | **M expressions tab** |
| Column — model to visual | Where-used detail | **Objects + Dependency tree** |
| Column — source to visual, end to end | *coming Sep 2026* | **Source → Visual tab**, shipped |

The last row is now **column grain upstream**, not just table/view grain.
`mtrace.py` walks each partition's M step-by-step and carries an open set of
columns forward through navigation, `Value.NativeQuery` (including SQL
`SELECT a AS b` aliases), `Table.RenameColumns`, `Table.SelectColumns` /
`RemoveColumns`, `Table.AddColumn` (recorded as *computed from* its inputs)
and `Table.ExpandTableColumn`. The **Column Lineage** tab renders the full
chain — server → database → schema → table → source column → semantic model
→ model column → measure / visual / relationship.

The honest limits, and they matter:

- Steps that reshape the row *and* column space — `Table.Pivot`,
  `Table.Unpivot`, `Table.Group`, joins that are never expanded — are
  reported as **untraced** for the columns they touch. The tracer says
  "I lost it here, at this step", it never guesses a mapping.
- `SELECT *` in native SQL is not expanded, because the tool has no
  connection to the warehouse to enumerate the columns.
- Custom PQ functions are traced when they are invoked in a step whose
  column effect is visible; a function whose body the tool cannot see is
  reported as untraced rather than assumed pass-through.
- Sources that are *other semantic models* (`AnalysisServices.Database`,
  `PowerBI.Datamarts`, DirectQuery on a published model) are labelled as
  the upstream source and their tables/columns are shown, but the tracer
  does not yet recurse **into** that model's own partitions to reach its
  warehouse. That recursion is the remaining gap on this row.

| Feature | Status | Notes |
|---|---|---|
| Item-level lineage — data sources view | **Yes** | Source → models → consumers, with source type and downstream counts |
| Item-level lineage — models/dataflows view | **Yes** | Upstream and downstream at once, cross-workspace dependencies called out |
| Column-level lineage — model to visual | **Yes** | Graph + UI tree, both directions |
| Cross-report lineage | **Yes** | Usage is the union across every parsed report |
| End-to-end lineage (source → report) | **Yes** | `lineage.py`, "Source → Visual" tab |
| Export the lineage graph as JSON | **Yes** | `/api/lineage/items` and the JSON export |
| Column-level lineage — data source to visual | **Yes** (with limits) | Column-grain M tracing, **Column Lineage** tab; joins/pivots/opaque functions reported as *untraced*, never guessed — see the note above |
| Downstream / composite model tracking | **Partial** | Tracked as a consumer and shown as an upstream source; the tracer does not recurse into the other model's own partitions |

## Detection & analysis

| Feature | Status | Notes |
|---|---|---|
| Unused columns, measures and tables | **Yes** | Six-state verdict, never a bare used/unused |
| Where-used analysis (27 categories) | **Partial** | ~14 reference scopes today (projection, filter, query Where, sort, formatting, bookmark, RLS, relationship, hierarchy, sort-by, calc column/table, paginated query, report-level measure) |
| Implicit measures & anti-patterns | **Yes** | Rule engine |
| "Clean your model" guide | **Partial** | Findings with severities; no guided walkthrough |
| Table relations analysis | **Yes** | Relationships tab + bidi/many-to-many rules |

## Code search

| Feature | Status | Notes |
|---|---|---|
| Search all DAX expressions | **Yes** | DAX tab + global search |
| Search all M code | **Yes** | M index, comment-aware |
| Tree view of dependencies | **Yes** | Up and down |
| Dataflows | **Engine only** | `MExpressionIndex.add_dataflow` |
| Fabric Notebooks | **Partial** | Inventoried; code is not parsed for field references |

## Governance & tenant analysis

| Feature | Status | Notes |
|---|---|---|
| Best-practice analysis | **Yes** | 12 rules, data-driven |
| Custom (user-defined) rules | **Partial** | `Rule` records are data; no config-file loader yet |
| Tenant summary | **Yes (UI)** | Tenant tab; orphaned workspaces surfaced |
| Access & permissions tracking | **Yes (UI)** | `governance.access_matrix` |
| RLS & OLS | **Partial** | RLS fully; OLS only where the payload exposes it |
| Semantic model inventory + refresh events | **Yes (UI)** | `governance.model_inventory` |
| Dataflow inventory (Gen1+Gen2) + refresh | **Yes (UI)** | `governance.dataflow_inventory` |
| Apps & audiences | **Yes (UI)** | `governance.apps_and_audiences` |
| Find Excel users across the tenant | **Yes (UI)** | `governance.excel_users` |
| Report performance | **No** | Needs the capacity metrics / query-log APIs |
| Page-level usage & consumption | **Yes (UI)** | `governance.page_usage` from ViewReportPage events |
| Custom visual consumption | **Yes (UI)** | `governance.custom_visual_usage` |
| Report subscriptions | **Yes (UI)** | `governance.subscriptions` |
| Broken visuals detection | **Yes** | Rule + `unresolved` references with evidence |
| Broken DAX detection | **Yes** | Rule over unresolved DAX references |
| Capacity metrics with history | **Partial** | Inventory + refresh counts; no historical tracking |
| Fabric Notebook inventory + run events | **Partial** | Inventory yes, run events no |

## Similarity & duplicates

| Feature | Status | Notes |
|---|---|---|
| Semantic model similarity score | **Yes** | Jaccard over object names |
| Report similarity score | **Yes** | Jaccard over field-reference sets |
| Find duplicate DAX expressions | **Yes** | Comment/layout-insensitive, string-literal exact |

## Cleanup & actions

| Feature | Status | Notes |
|---|---|---|
| Export model & report documentation | **Yes** | Markdown, `pbi-lineage docs` |
| Export to JSON for downstream tools | **Yes** | JSON + SQLite + Delta rows |
| Clean TMDL | **Yes** | `pbi-lineage clean-tmdl`; drops only exactly-Unused objects |
| DAX backup & restore | **Yes** | Backup writes JSON; restore is a dry run against the in-memory model |
| Save & resume an analysis | **Yes** | `.pbilineage.json` |
| 1-click cleanup (online removal) | **Deliberately not** | Removal is always preview → script → explicit apply, with a backup enforced. The spec forbids silent bulk deletion |
| Tenant backups of models/reports/dataflows | **No** | |

## Operations

| Feature | Status | Notes |
|---|---|---|
| Manual run (interactive UI) | **Yes** | `pbi-lineage ui` |
| Scheduled automated scans | **Partial** | Headless entry point exists; scheduling is left to cron/Fabric |
| Trigger from a Fabric pipeline / CI | **Yes** | `run_tenant_scan` / CLI are headless |
| Customize per run | **Partial** | CLI flags and rule lists; no per-run config file |
| Write results anywhere | **Partial** | JSON, SQLite, Delta tables; no Teams/warehouse writer |
| Email or Teams alerts | **No** | |
| Track tenant changes over time | **No** | Each scan is a snapshot; `modifiedSince` deltas exist but no history store |

## Service mode

The UI now has a **Power BI Service** mode with two ways in:

- **Replay** — point it at a saved Scanner payload (and optionally an
  activity-log export). Everything downstream is a pure transform, so the
  entire tenant surface is browsable, shareable and testable offline. This
  is how the Service UI is verified in CI and how the screenshots were
  produced; `demo_estate/sample_tenant_scan.json` is a runnable sample.
- **Live** — a bearer token drives a real Scanner run. Get one with
  `az account get-access-token --resource https://analysis.windows.net/powerbi/api`,
  or run `pbi-lineage tenant` with a service principal and replay the saved
  payload afterwards.

**This has still never touched a real tenant.** Every payload shape comes
from the documented APIs, not from observation. The replay path is fully
exercised; the live path's HTTP layer is exercised only against a fake
transport.

## The honest summary

Strong: the analysis core — lineage, verdicts with evidence, the safety
rules around deletion, code search, cleanup exports. That is the part the
spec calls the whole product, and it is the part with tests.

Weak: anything that only exists inside a live tenant. The governance
functions are written and unit-tested against recorded payload shapes, but
**no part of the Service path has ever run against a real Power BI
tenant** — the shapes come from the documented APIs, not from observation.
Expect friction on first contact, in the same way the first real `.pbix`
exposed three genuine bugs.

Not built at all: report performance, capacity history, alerting, tenant
backups, change tracking over time.
