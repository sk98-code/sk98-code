# Quickstart — run everything locally

Two independent tools live in this repo:

1. **`pbi_lineage`** — Power BI metadata, dependency & lineage analyzer
   (finds unused measures/columns, computes lineage, generates safe removal
   scripts). This is the main tool.
2. **`bimigrate`** — the older BI migration platform (Tableau / Spotfire /
   Qlik → Power BI), including an optional local web UI.

## 1. Install

Requires **Python 3.11+**.

```bash
cd sk98-code

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -e ".[dev]"
```

That installs both packages plus the test tooling, and puts two commands on
your PATH: `pbi-lineage` and `bimigrate`.

## 2. Verify it works

```bash
pytest -q
```

Expected: **183 passed**. If that passes, everything is wired correctly.

## 3. Run the lineage analyzer

```bash
# analyze a Power BI file (.pbix, .pbip, or a PBIP project folder)
pbi-lineage analyze /path/to/Sales.pbix --out ./results

# preview what deleting an object would break (never deletes anything)
pbi-lineage removal-plan /path/to/Sales.pbip --object "Sales[DiscountCode]"

# find which models/dataflows touch a warehouse table
pbi-lineage search-m /path/to/Sales.pbip dbo.FactSales

# attach to a model open in Power BI Desktop  (Windows only)
pip install -e ".[pbi-desktop]"
pbi-lineage live --pbix /path/to/Sales.pbix

# scan a whole Power BI tenant  (needs an Entra app registration)
pip install -e ".[pbi-service]"
pbi-lineage tenant --client-id <id> --tenant-id <tenant> --out ./scan
```

`--out` writes `pbi_lineage_export.json` and `pbi_lineage.sqlite` you can
open in any SQLite browser.

### No .pbix handy? Generate a demo project

```bash
python - <<'EOF'
import sys; sys.path.insert(0, "tests")
from pathlib import Path
from pbil_fixtures import write_pbip, pbir_visual, column_expr, measure_expr, entity_ref

SALES = """table Sales
\tcolumn Qty
\t\tdataType: int64
\t\tsummarizeBy: sum
\t\tsourceColumn: Qty

\tcolumn Region
\t\tdataType: string
\t\tsourceColumn: Region

\tcolumn DiscountCode
\t\tdataType: string
\t\tsourceColumn: DiscountCode

\tmeasure 'Total Sales' = SUM(Sales[Qty])
\t\tformatString: #,0

\tpartition Sales-p = m
\t\tmode: import
\t\tsource =
\t\t\tlet
\t\t\t\tSource = Sql.Database("dwh.corp.local", "SalesDW"),
\t\t\t\tdbo_FactSales = Source{[Schema="dbo",Item="FactSales"]}[Data]
\t\t\tin
\t\t\t\tdbo_FactSales
"""

pages = {"p1": {"page": {"name": "p1", "displayName": "Overview"},
  "visuals": {"v1": pbir_visual(name="v1", projections={
      "Values": [measure_expr(entity_ref("Sales"), "Total Sales")],
      "Category": [column_expr(entity_ref("Sales"), "Region")]})}}}

write_pbip(Path("demo"), name="SalesDemo", pages=pages,
           tmdl_files={"tables/Sales.tmdl": SALES})
print("created: demo/SalesDemo")
EOF

pbi-lineage analyze demo/SalesDemo --out ./results
```

You should see `Sales[DiscountCode]` reported as **Unused** while `Qty` and
`Region` stay **Used** — `Qty` because the `Total Sales` measure consumes
it, `Region` because a visual projects it.

## 4. Run the migration tool + web UI (optional)

```bash
pip install -e ".[web]"
bimigrate web                      # then open http://127.0.0.1:8000
```

Or point the CLI at a folder of Tableau/Qlik/Spotfire files:

```bash
bimigrate discover demo_estate --out ./out
```

## Notes

- **Windows-only features:** `pbi-lineage live` and XMLA write-back need
  `pyadomd` + the ADOMD.NET client, which exist only on Windows with Power
  BI Desktop installed. Every other command runs anywhere.
- **Nothing is ever deleted for you.** `removal-plan` prints a TMSL script
  and an impact manifest; applying it is a separate, explicit step.
- Full design notes: `docs/pbi_lineage_build_spec.md`, and the
  `pbi_lineage` section of `README.md`.
