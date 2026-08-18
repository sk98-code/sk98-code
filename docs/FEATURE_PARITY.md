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
| Shared semantic models in Service | **Engine only** | `service/xmla.py`; needs Premium/PPU/Fabric |
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

The last row needs a caveat rather than a victory lap: our upstream half
resolves the *source object* a table loads from (server/database/schema
object, or the file), so the chain is unbroken source → column → visual —
but it is **table/view grain upstream**, not column grain. Following a
specific warehouse *column* through M renames, merges, pivots and custom
columns needs a full M evaluator, which the build spec scopes out as a
separate project. That is presumably the harder half they are still
building.

| Feature | Status | Notes |
|---|---|---|
| Item-level lineage — data sources view | **Yes** | Source → models → consumers, with source type and downstream counts |
| Item-level lineage — models/dataflows view | **Yes** | Upstream and downstream at once, cross-workspace dependencies called out |
| Column-level lineage — model to visual | **Yes** | Graph + UI tree, both directions |
| Cross-report lineage | **Yes** | Usage is the union across every parsed report |
| End-to-end lineage (source → report) | **Yes** | `lineage.py`, "Source → Visual" tab |
| Export the lineage graph as JSON | **Yes** | `/api/lineage/items` and the JSON export |
| Column-level lineage — data source to visual | **Partial** | Chain is unbroken, but upstream resolution is table/view grain — see the note above |
| Downstream / composite model tracking | **Partial** | Tracked as a consumer; field-level recursion not implemented |

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
| Tenant summary | **Engine only** | `governance.tenant_summary`, incl. orphaned workspaces |
| Access & permissions tracking | **Engine only** | `governance.access_matrix` |
| RLS & OLS | **Partial** | RLS fully; OLS only where the payload exposes it |
| Semantic model inventory + refresh events | **Engine only** | `governance.model_inventory` |
| Dataflow inventory (Gen1+Gen2) + refresh | **Engine only** | `governance.dataflow_inventory` |
| Apps & audiences | **Engine only** | `governance.apps_and_audiences` |
| Find Excel users across the tenant | **Engine only** | `governance.excel_users` |
| Report performance | **No** | Needs the capacity metrics / query-log APIs |
| Page-level usage & consumption | **Engine only** | `governance.page_usage` from ViewReportPage events |
| Custom visual consumption | **Engine only** | `governance.custom_visual_usage` |
| Report subscriptions | **Engine only** | `governance.subscriptions` |
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
