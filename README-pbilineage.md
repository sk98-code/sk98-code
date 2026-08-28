# pbilineage — column-level lineage for Power BI / Microsoft Fabric

Traces a column end to end across a whole tenant:

```
source table/column → Power Query (M) transform → semantic model column/measure → report visual/filter
```

It connects to the **live service** — the Power BI Admin REST APIs and, where the
workspace is on capacity, the XMLA endpoint. There are no PBIX or PBIP files to
collect first.

The thing that makes the output usable is that **every lineage edge is tagged
with how it was obtained**, so certain lineage is visually distinguishable from
best-effort lineage:

| Tag | Means | Where it comes from |
|---|---|---|
| `resolved` | The engine told us, or it is a literal binding in metadata | `$SYSTEM.DISCOVER_CALC_DEPENDENCY`, report layout field bindings, `datasetId` |
| `heuristic` | Our parser recognised the construct and inferred the reference | DAX tokenizer, matched M transforms |
| `opaque` | We saw something we will not guess at | unrecognised M functions, unresolvable `[Name]` references |

A path's confidence is the **weakest link on it**. One opaque hop makes the whole
chain opaque, because that is what a reviewer needs to know before trusting an
impact answer.

## Hosted viewer (GitHub Pages)

The **viewer** is deployed as a static site; **scanning is not, and cannot be.**
That split is deliberate rather than a limitation of the hosting:

* A static page cannot hold a service-principal secret. Anything in the bundle
  is readable by anyone who opens devtools, and `Tenant.Read.All` is read
  access to every workspace's metadata in the tenant.
* The XMLA endpoint speaks MSOLAP over TCP. A browser cannot open that
  connection at any privilege level.
* The Admin REST APIs are app-only, server-to-server surfaces.

So scanning runs on the machine that owns the credentials, and the hosted page
does the part that needs no secrets:

1. `pbilineage scan` locally → `out/lineage/graph.json`
2. Open the hosted viewer, choose **Open graph.json** (or drop the file on the
   source bar)
3. The file is read with the browser's File API and **never uploaded** — no
   request leaves the page

The site loads the synthetic demo tenant on arrival, so the link is useful to
someone who has not scanned anything yet.

### Enabling it — one manual step, required

`.github/workflows/pages.yml` builds and deploys on every push that touches the
UI or the package, but it **cannot turn Pages on for you**. Creating a Pages
site needs repository admin, and the workflow token does not have it — the API
answers `Resource not accessible by integration`, with or without the action's
`enablement` option. So do this once:

> **Settings → Pages → Build and deployment → Source: *GitHub Actions***
>
> <https://github.com/sk98-code/sk98-code/settings/pages>

Until then the workflow fails on purpose at its "Check that Pages is enabled"
step, with a link to that page — a silent skip would hide the fact that nothing
deployed. The build itself still runs and **attaches the finished site as the
`lineage-viewer-site` artifact**, so the output is never lost to a settings
flag. Once Pages is on, re-run the workflow and it publishes to
`https://<owner>.github.io/<repo>/`.

The workflow runs the Python graph/parser tests and the UI's own test suite
before it builds, so a broken pipeline does not ship a viewer for itself. It
also regenerates the bundled demo graph from the current code, which means the
demo can never drift from the parsers.

To build the static bundle yourself:

```bash
pbilineage demo --out lineage-ui/public/demo-graph.json
cd lineage-ui && npm ci
BASE=/your-repo/ STATIC=1 OUT_DIR=../dist-pages npm run build
```

`STATIC=1` tells the app there is no API behind it, so it skips the health
probe entirely — the built bundle contains no `/api/` calls at all.

## Quick start (no tenant required)

```bash
pip install -e ".[dev,api]"
pbilineage demo                                  # synthetic two-workspace tenant
pbilineage serve --graph out/lineage/demo-graph.json
```

Then open <http://127.0.0.1:8000>. To build the UI first:

```bash
cd lineage-ui && npm install && npm run build    # builds into src/pbilineage/web/dist
```

The demo estate is deliberately mixed: one Premium workspace that takes the
DMV path and one Pro-only workspace that falls back to the DAX parser, plus an M
query that traces cleanly and another that ends in a transform we refuse to
guess at.

## Scanning a real tenant

```bash
cp .env.example .env      # fill in tenant/client/secret; .env is gitignored
pbilineage doctor         # checks config, credentials and optional deps
pbilineage scan           # full tenant scan
pbilineage scan --incremental
```

### What the service principal needs

One Azure AD app registration covers both surfaces, but they are permissioned
separately:

* **Admin REST APIs** (Scanner, Export, `GetModifiedWorkspaces`) — the
  `Tenant.Read.All` application permission with admin consent, **and** the tenant
  setting *"Service principals can use read-only Power BI admin APIs"* enabled
  with the app's security group added to it.
* **XMLA endpoint** — the service principal must be a member of each workspace
  (Viewer is enough for read), the workspace must be on Premium/PPU/Fabric
  capacity, and the capacity's XMLA endpoint must be set to Read or Read Write.

## Capturing a redacted API sample

`pbilineage capture` records how *your* tenant answers, in a form that is safe
to share when someone needs to check the tool's reading of the API contract:

```bash
pbilineage capture --workspace <workspace-id> --out capture.json
```

It collects the raw Scanner API result, what the normalizer made of it, the DMV
column names and a few sample rows, and how both report-export endpoints
respond (status codes and response keys only — never a PBIX body).

Redaction is structure-preserving. **Kept**, because they are the contract:
every JSON key, Microsoft's own vocabulary (`columnType: "Calculated"`,
`datasourceType: "Sql"`, `state: "Active"`), and the structure of M and DAX —
function calls, step order, argument shapes. **Replaced**, consistently, so
cross-references still resolve: object names, servers, databases, emails and
GUIDs. Descriptions are dropped outright rather than pseudonymized.

```
let
    Source = Sql.Database("Host1", "Database1"),
    Name50 = Source{[Schema="Name34",Item="Name3"]}[Name35],
    #"Name36" = Table.SelectColumns(Name50, {"Name4", "Name5", "Name37"}),
    #"Name39" = Table.RenameColumns(#"Name36", {{"Name37", "Name7"}})
in
    #"Name39"
```

Expression text is rewritten structurally, not by substring replacement — the
capture is regression-tested to leak nothing from the demo estate and to parse
into exactly the same steps before and after redaction. `--no-scrub` keeps real
values, for a tenant whose contents you may disclose. Either way, read the file
before you share it.

## The key design choice: one interface, two implementations

A workspace has an XMLA endpoint only if it sits on Premium, PPU or Fabric
capacity. Pro-only workspaces do not. So dependency resolution is an interface
with two implementations, and `CapacityRouter` picks per workspace:

| Path | Applies to | Mechanism | Confidence |
|---|---|---|---|
| `xmla-dmv` | Premium / PPU / Fabric | `$SYSTEM.DISCOVER_CALC_DEPENDENCY` over XMLA — the engine returns fully resolved measure, calculated-column, calculated-table and RLS dependencies, so there is no DAX to parse | `resolved` |
| `dax-parser` | Pro-only (no XMLA) | Scanner API `datasetSchema` + `datasetExpressions`, then our DAX tokenizer resolves references against the model's own schema | `heuristic` |

The router also **degrades rather than failing**: a workspace that should have
XMLA but whose endpoint is unreachable (disabled at tenant level, service
principal not in the workspace, capacity paused) falls back to the parser path,
and the reason is recorded on the run.

## What each layer does

**Semantic model.** On capacity, the `TMSCHEMA_*` DMVs also give the
authoritative schema — including each partition's type, which the Scanner API
does not report and which is what distinguishes a DAX calculated table from an M
query.

**Power Query (M).** There is no dependency DMV for M, so the M text is read.
This is explicitly *not* an interpreter: it tokenizes the `let … in` block,
splits it into steps, and pattern-matches the table functions it knows
(`Table.SelectColumns`, `RenameColumns`, `TransformColumns`, `AddColumn`,
`ExpandTableColumn`, `Group`, `Unpivot*`, `Sql.Database`, `Value.NativeQuery`, …).
It maintains a live column set through the steps, so a column renamed twice
still traces back to its source column. Anything unrecognised **taints the flow
to `opaque`** — the step is still reported by name, but no lineage is invented
for it.

```
$ pbilineage explain m fact_sales.m
column       from source column(s)     confidence  transforms
Amount       SalesAmount               heuristic   select -> rename(SalesAmount -> Amount) -> retype
GrossMargin  CostAmount, SalesAmount   heuristic   add(Table.AddColumn)
```

**Reports and visuals.** No Admin API exposes visual layout, so each report is
pulled through the Export API (async job, then poll), unzipped, and its
`Report/Layout` parsed for visual field bindings, filters and conditional
formatting. The newer PBIR folder format is handled too. This layer **degrades
gracefully**: a report that will not export — live connections, protection
policies, export disabled — still appears in the graph with its model lineage,
and the gap is recorded as a warning rather than failing the scan.

**Scan orchestration.** Tenant inventory and schema come from the async Scanner
API (`getInfo` → poll `scanStatus` → `scanResult`), batched at the API's 100-
workspace limit with backoff and retry that honours `Retry-After`. Incremental
runs use `GetModifiedWorkspaces` against a checkpoint watermark, and re-scan only
what changed — plus anything that failed last time, so a transient failure is
retried rather than silently skipped forever.

## The graph

Nodes: `DataSource`, `Table`, `Column`, `Dataflow`, `Measure`, `Report`, `Page`,
`Visual`, plus `Workspace` and `SemanticModel` as containers.

Edges: `derives_from` (derived object → its input) and `used_in` (producer →
consumer), each carrying `confidence` and `evidence`; `contains` for structural
containment, which is excluded from lineage traversal by default.

Node ids are deterministic and case-folded, so re-scanning a workspace MERGEs
onto the same nodes instead of duplicating them.

Two stores implement the same interface:

* **In-memory + JSON** — the default. `scan` writes it, `serve` reads it, no
  database needed.
* **Neo4j** — `pbilineage push` loads it; `pbilineage serve --neo4j` serves from
  it. Alongside the typed relationships, every lineage edge is also written as a
  single canonical `FLOWS_TO` relationship pointing upstream → downstream,
  because a variable-length Cypher pattern cannot mix per-hop directions.
  Traversal uses `FLOWS_TO`; the typed relationships are what the model means.

## API

```
GET /api/health                 liveness + which backend is behind it
GET /api/stats                  node/edge counts, confidence breakdown
GET /api/workspaces             workspaces with their capacity tier
GET /api/search?q=&kinds=       find columns/measures/reports by name
GET /api/nodes/{id}             one node, with neighbour counts
GET /api/nodes/{id}/expand      one hop out, for expand-on-click
GET /api/lineage/{id}           ?direction=upstream|downstream|both&depth=&min_confidence=
GET /api/impact/{id}            "what breaks if this changes"
GET /api/warnings               where the scan could not see clearly
```

## UI

React + react-flow (`lineage-ui/`). Search on the left, graph in the middle,
details and downstream impact on the right. Confidence is carried by edge colour
*and* line style — solid resolved, dashed heuristic, dotted opaque — so it
survives greyscale and colour-blindness. Click a node for details, double-click
to expand or collapse its neighbours; a single expansion is capped so a
high-fan-out node cannot freeze the canvas.

The same app runs against either backing, chosen at startup:

| | Served by `pbilineage serve` | Hosted static build |
|---|---|---|
| Data | FastAPI over the graph store | a `graph.json` in the browser tab |
| Traversal | `graph/traversal.py` | `lineage-ui/src/graph/queries.js` |
| Network | calls `/api/*` | none at all |

The client-side port is a real reimplementation of the traversal, search and
impact logic, so the hosted viewer answers the same questions with no server.
Because the two have to agree, the JS port is tested separately
(`npm test`) against the same rules the Python enforces — edge direction,
weakest-link confidence, containment exclusion, depth limits and cycle
termination.

## CLI

```
pbilineage doctor                      check config, credentials and optional deps
pbilineage demo                        build the synthetic tenant graph (no network)
pbilineage capture --workspace ID      redacted sample of how this tenant's APIs answer
pbilineage scan [--incremental]        full or incremental tenant scan
pbilineage serve [--neo4j]             run the API (and the UI, if built)
pbilineage push                        load a scanned graph into Neo4j
pbilineage search TERM                 find nodes by name
pbilineage lineage NODE_ID             upstream/downstream subgraph in the terminal
pbilineage impact NODE_ID              what breaks downstream
pbilineage warnings                    everywhere the scan could not see clearly
pbilineage explain dax "EXPR"          what the DAX tokenizer makes of an expression
pbilineage explain m FILE              steps, sources and column trace of an M query
```

## Install extras

The core package needs only the standard library plus pydantic — it can parse
DAX, M and PBIX layouts and build a graph with nothing else installed. Each
extra turns on one live surface:

```bash
pip install -e ".[api]"      # FastAPI + uvicorn
pip install -e ".[live]"     # msal: certificate auth + token caching
pip install -e ".[xmla]"     # pyadomd: the DMV path (needs .NET + ADOMD.NET)
pip install -e ".[neo4j]"    # the Neo4j store
pip install -e ".[sql]"      # sqlglot, for phase 4
```

`pyadomd` needs the .NET runtime and the ADOMD.NET client libraries, which are
not available on every host. That is treated as a normal condition, not an
error: `XmlaClient.available` is False, the router routes everything to the
parser path, and `pbilineage doctor` says so.

## Phasing

1. **Semantic model** — DMV path + DAX-parser fallback. *Done.*
2. **Report/visual layer** — Export API + PBIX layout parsing. *Done.*
3. **M/Power Query** — step matching and column-flow tracing. *Done.*
4. **Tenant-wide** — incremental scanning and checkpointing. *Done.* Source-level
   SQL lineage via `sqlglot` over captured `Sql.Database` / `Value.NativeQuery`
   text is the remaining stretch goal; the native-query text is already captured
   and stored on the `DataSource` node, so it is there when that lands.

### Deliberately out of scope

* True source-database column lineage (phase 4+, see above).
* Paginated reports and Excel workbook lineage.
* Full M-language interpretation. The matcher is best-effort **by design** —
  when it does not recognise something it says `opaque` rather than guessing.

## Development

```bash
PYTHONPATH=src python3 -m pytest tests/test_lineage_*.py -q   # 186 Python tests
cd lineage-ui && npm test                                     # 32 JS tests
cd lineage-ui && npm run dev            # UI dev server, proxies /api to :8000
```

Tests run against a fake HTTP transport and a synthetic tenant — no network, no
credentials, no Neo4j. CI (`.github/workflows/ci.yml`) runs both suites, plus
ruff and black, on every push.
