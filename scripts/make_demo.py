"""Generate a demo PBIP project so the tool can be tried without a .pbix.

The model is deliberately shaped to exercise the column-lineage engine:
a native-SQL query with a column alias, a rename, a computed column, and
a column nothing consumes.

    python scripts/make_demo.py            # writes ./demo/RetailDemo
    python scripts/make_demo.py C:\\work    # writes C:\\work\\RetailDemo
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from pbil_fixtures import (  # noqa: E402
    column_expr,
    entity_ref,
    measure_expr,
    pbir_visual,
    write_pbip,
)

ORDERS = '''table 02_Orders_NativeSQL
\tcolumn order_id
\t\tdataType: string
\t\tsourceColumn: order_id

\tcolumn GrossAmount
\t\tdataType: double
\t\tsourceColumn: GrossAmount

\tcolumn AdjustedRiskAmount
\t\tdataType: double
\t\tsourceColumn: AdjustedRiskAmount

\tcolumn status
\t\tdataType: string
\t\tsourceColumn: status

\tmeasure 'Total Gross' = SUM('02_Orders_NativeSQL'[GrossAmount])
\t\tformatString: #,0.00

\tpartition 02_Orders_NativeSQL-p = m
\t\tmode: import
\t\tsource =
\t\t\tlet
\t\t\t\tSource = PostgreSQL.Database("localhost", "retail_demo"),
\t\t\t\tnav = Source{[Schema="sales",Item="orders"]}[Data],
\t\t\t\t#"Renamed" = Table.RenameColumns(nav,{{"total_amount","GrossAmount"}}),
\t\t\t\t#"Added Risk" = Table.AddColumn(#"Renamed", "AdjustedRiskAmount", each [GrossAmount] * 1.15),
\t\t\t\t#"Kept" = Table.SelectColumns(#"Added Risk", {"order_id","GrossAmount","AdjustedRiskAmount","status"})
\t\t\tin
\t\t\t\t#"Kept"
'''

CUSTOMERS = '''table 03_Customers
\tcolumn customer_id
\t\tdataType: string
\t\tsourceColumn: customer_id

\tcolumn country
\t\tdataType: string
\t\tsourceColumn: country

\tpartition 03_Customers-p = m
\t\tmode: import
\t\tsource =
\t\t\tlet
\t\t\t\tSource = PostgreSQL.Database("localhost", "retail_demo"),
\t\t\t\tnav = Source{[Schema="sales",Item="customers"]}[Data]
\t\t\tin
\t\t\t\tnav
'''


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("demo")
    orders = entity_ref("02_Orders_NativeSQL")
    pages = {
        "p1": {
            "page": {"name": "p1", "displayName": "Overview"},
            "visuals": {
                "v1": pbir_visual(
                    name="lineChart",
                    visual_type="lineChart",
                    projections={
                        "Values": [measure_expr(orders, "Total Gross")],
                        "Category": [column_expr(orders, "status")],
                    },
                    filters=[{"field": column_expr(orders, "GrossAmount")}],
                )
            },
        }
    }
    path = write_pbip(
        root,
        name="RetailDemo",
        pages=pages,
        tmdl_files={
            "tables/02_Orders_NativeSQL.tmdl": ORDERS,
            "tables/03_Customers.tmdl": CUSTOMERS,
        },
    )
    print(f"created: {path}")
    print("now run:  pbi-lineage ui     and paste that path into the box")


if __name__ == "__main__":
    main()
