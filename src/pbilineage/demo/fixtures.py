"""A synthetic two-workspace tenant that exercises every path in the tool.

The estate is deliberately mixed:

* **Finance Analytics** is on P1 capacity, so it takes the XMLA/DMV path and
  its measure dependencies come back `resolved`. Its `FactSales` table has an
  M query we can follow column by column, and `DimCustomer` has one we
  cannot — it ends in a transform the matcher does not know, so those columns
  go `opaque` rather than being guessed at.
* **Sales Self-Service** is Pro-only. No XMLA endpoint, so its DAX is
  tokenized and its dependencies are `heuristic`.

`build_demo_graph()` returns the finished graph, which is what
`pbilineage demo` writes and what most of the tests assert against.
"""

from __future__ import annotations

import json
from typing import Any

from pbilineage.graph.builder import GraphBuilder
from pbilineage.models import LINEAGE_EDGES, Confidence, DatasetSpec, LineageGraph
from pbilineage.parsers.layout import parse_layout
from pbilineage.resolve.base import DependencyResult
from pbilineage.resolve.dax_resolver import DaxDependencyResolver
from pbilineage.scan.normalize import snapshot_from_scan_results

FINANCE_WORKSPACE = "11111111-1111-1111-1111-111111111111"
SALES_WORKSPACE = "22222222-2222-2222-2222-222222222222"
REVENUE_DATASET = "aaaaaaaa-0000-0000-0000-000000000001"
PIPELINE_DATASET = "aaaaaaaa-0000-0000-0000-000000000002"
REVENUE_REPORT = "bbbbbbbb-0000-0000-0000-000000000001"
PIPELINE_REPORT = "bbbbbbbb-0000-0000-0000-000000000002"
CUSTOMER_DATAFLOW = "cccccccc-0000-0000-0000-000000000001"

DEMO_CAPACITY_SKUS = {"cap-premium-p1": "P1"}

#: fully traceable: select, rename, typed transform, derived column
FACT_SALES_M = """
let
    Source = Sql.Database("finance-sql.database.windows.net", "FinanceDW"),
    dbo_FactSales = Source{[Schema="dbo",Item="FactSales"]}[Data],
    #"Removed Other Columns" = Table.SelectColumns(dbo_FactSales,
        {"SalesKey", "CustomerKey", "OrderDate", "SalesAmount", "CostAmount"}),
    #"Renamed Columns" = Table.RenameColumns(#"Removed Other Columns",
        {{"SalesAmount", "Amount"}, {"CostAmount", "Cost"}}),
    #"Changed Type" = Table.TransformColumnTypes(#"Renamed Columns",
        {{"Amount", type number}, {"Cost", type number}, {"OrderDate", type date}}),
    #"Added Margin" = Table.AddColumn(#"Changed Type", "GrossMargin",
        each [Amount] - [Cost], type number),
    #"Filtered Rows" = Table.SelectRows(#"Added Margin", each [Amount] > 0)
in
    #"Filtered Rows"
"""

#: ends in a transform the matcher does not recognise -> opaque downstream
DIM_CUSTOMER_M = """
let
    Source = Sql.Database("finance-sql.database.windows.net", "FinanceDW"),
    dbo_DimCustomer = Source{[Schema="dbo",Item="DimCustomer"]}[Data],
    #"Renamed Columns" = Table.RenameColumns(dbo_DimCustomer,
        {{"CustomerName", "Customer"}, {"CustomerRegion", "Region"}}),
    #"Fuzzy Grouped" = Table.FuzzyGroup(#"Renamed Columns", "Customer", {{"Cluster", each _}})
in
    #"Fuzzy Grouped"
"""

#: a native query, which Phase 4 will hand to sqlglot for source-column lineage
PIPELINE_M = """
let
    Source = Sql.Database("crm-sql.internal", "CRM"),
    Query = Value.NativeQuery(Source,
        "SELECT OpportunityId, OwnerName, StageName, Amount FROM dbo.Opportunity", null,
        [EnableFolding=true]),
    #"Renamed Columns" = Table.RenameColumns(Query, {{"Amount", "PipelineValue"}})
in
    #"Renamed Columns"
"""

DATAFLOW_M = """
let
    Source = Sql.Database("finance-sql.database.windows.net", "FinanceDW"),
    dbo_Customer = Source{[Schema="dbo",Item="CustomerMaster"]}[Data],
    #"Chose Columns" = Table.SelectColumns(dbo_Customer, {"CustomerKey", "Segment"})
in
    #"Chose Columns"
"""


def demo_scan_result() -> dict[str, Any]:
    """A `scanResult` payload in the shape the Scanner API actually returns."""
    return {
        "workspaces": [
            {
                "id": FINANCE_WORKSPACE,
                "name": "Finance Analytics",
                "type": "Workspace",
                "state": "Active",
                "capacityId": "cap-premium-p1",
                "isOnDedicatedCapacity": True,
                "datasets": [
                    {
                        "id": REVENUE_DATASET,
                        "name": "Revenue Model",
                        "configuredBy": "analytics@contoso.com",
                        "tables": [
                            {
                                "name": "FactSales",
                                "source": [{"expression": FACT_SALES_M}],
                                "columns": [
                                    {"name": "SalesKey", "dataType": "Int64", "columnType": "Data"},
                                    {"name": "CustomerKey", "dataType": "Int64", "columnType": "Data"},
                                    {"name": "OrderDate", "dataType": "DateTime", "columnType": "Data"},
                                    {"name": "Amount", "dataType": "Double", "columnType": "Data"},
                                    {"name": "Cost", "dataType": "Double", "columnType": "Data"},
                                    {"name": "GrossMargin", "dataType": "Double", "columnType": "Data"},
                                    {
                                        "name": "MarginBand",
                                        "dataType": "String",
                                        "columnType": "Calculated",
                                        "expression": ('IF(FactSales[GrossMargin] > 1000, "High", "Low")'),
                                    },
                                ],
                                "measures": [
                                    {
                                        "name": "Total Revenue",
                                        "expression": "SUM(FactSales[Amount])",
                                        "description": "Gross revenue before returns",
                                    },
                                    {
                                        "name": "Total Cost",
                                        "expression": "SUM(FactSales[Cost])",
                                    },
                                    {
                                        "name": "Total Margin",
                                        "expression": "[Total Revenue] - [Total Cost]",
                                    },
                                    {
                                        "name": "Margin %",
                                        "expression": "DIVIDE([Total Margin], [Total Revenue])",
                                    },
                                    {
                                        "name": "Revenue LY",
                                        "expression": (
                                            "CALCULATE([Total Revenue], "
                                            "SAMEPERIODLASTYEAR(FactSales[OrderDate]))"
                                        ),
                                    },
                                ],
                            },
                            {
                                "name": "DimCustomer",
                                "source": [{"expression": DIM_CUSTOMER_M}],
                                "columns": [
                                    {"name": "CustomerKey", "dataType": "Int64", "columnType": "Data"},
                                    {"name": "Customer", "dataType": "String", "columnType": "Data"},
                                    {"name": "Region", "dataType": "String", "columnType": "Data"},
                                    {"name": "Cluster", "dataType": "String", "columnType": "Data"},
                                ],
                                "measures": [
                                    {
                                        "name": "Customer Count",
                                        "expression": "DISTINCTCOUNT(DimCustomer[CustomerKey])",
                                    }
                                ],
                            },
                        ],
                        "expressions": [
                            {
                                "name": "ServerName",
                                "expression": '"finance-sql.database.windows.net" meta [IsParameterQuery=true]',
                            }
                        ],
                        "datasourceUsages": [{"datasourceInstanceId": "ds-finance-sql"}],
                    }
                ],
                "reports": [
                    {
                        "id": REVENUE_REPORT,
                        "name": "Revenue Overview",
                        "datasetId": REVENUE_DATASET,
                    }
                ],
                "dataflows": [
                    {
                        "objectId": CUSTOMER_DATAFLOW,
                        "name": "Customer Master",
                        "datasourceUsages": [{"datasourceInstanceId": "ds-finance-sql"}],
                    }
                ],
            },
            {
                "id": SALES_WORKSPACE,
                "name": "Sales Self-Service",
                "type": "Workspace",
                "state": "Active",
                "isOnDedicatedCapacity": False,
                "datasets": [
                    {
                        "id": PIPELINE_DATASET,
                        "name": "Pipeline",
                        "configuredBy": "sales.ops@contoso.com",
                        "tables": [
                            {
                                "name": "Opportunity",
                                "source": [{"expression": PIPELINE_M}],
                                "columns": [
                                    {"name": "OpportunityId", "dataType": "String", "columnType": "Data"},
                                    {"name": "OwnerName", "dataType": "String", "columnType": "Data"},
                                    {"name": "StageName", "dataType": "String", "columnType": "Data"},
                                    {"name": "PipelineValue", "dataType": "Double", "columnType": "Data"},
                                ],
                                "measures": [
                                    {
                                        "name": "Open Pipeline",
                                        "expression": (
                                            "CALCULATE(SUM(Opportunity[PipelineValue]), "
                                            'Opportunity[StageName] <> "Closed")'
                                        ),
                                    },
                                    {
                                        "name": "Average Deal",
                                        "expression": (
                                            "DIVIDE([Open Pipeline], "
                                            "DISTINCTCOUNT(Opportunity[OpportunityId]))"
                                        ),
                                    },
                                ],
                            }
                        ],
                        "datasourceUsages": [{"datasourceInstanceId": "ds-crm-sql"}],
                    }
                ],
                "reports": [
                    {
                        "id": PIPELINE_REPORT,
                        "name": "Pipeline Review",
                        "datasetId": PIPELINE_DATASET,
                    }
                ],
                "dataflows": [],
            },
        ],
        "datasourceInstances": [
            {
                "datasourceType": "Sql",
                "datasourceId": "ds-finance-sql",
                "connectionDetails": {
                    "server": "finance-sql.database.windows.net",
                    "database": "FinanceDW",
                },
            },
            {
                "datasourceType": "Sql",
                "datasourceId": "ds-crm-sql",
                "connectionDetails": {"server": "crm-sql.internal", "database": "CRM"},
            },
        ],
    }


def demo_calc_dependency_rows() -> list[dict[str, Any]]:
    """What `$SYSTEM.DISCOVER_CALC_DEPENDENCY` returns for the Revenue Model."""
    return [
        {
            "DATABASE_NAME": "Revenue Model",
            "OBJECT_TYPE": "MEASURE",
            "TABLE": "FactSales",
            "OBJECT": "Total Revenue",
            "EXPRESSION": "SUM(FactSales[Amount])",
            "REFERENCED_OBJECT_TYPE": "COLUMN",
            "REFERENCED_TABLE": "FactSales",
            "REFERENCED_OBJECT": "Amount",
        },
        {
            "OBJECT_TYPE": "MEASURE",
            "TABLE": "FactSales",
            "OBJECT": "Total Cost",
            "EXPRESSION": "SUM(FactSales[Cost])",
            "REFERENCED_OBJECT_TYPE": "COLUMN",
            "REFERENCED_TABLE": "FactSales",
            "REFERENCED_OBJECT": "Cost",
        },
        {
            "OBJECT_TYPE": "MEASURE",
            "TABLE": "FactSales",
            "OBJECT": "Total Margin",
            "EXPRESSION": "[Total Revenue] - [Total Cost]",
            "REFERENCED_OBJECT_TYPE": "MEASURE",
            "REFERENCED_TABLE": "FactSales",
            "REFERENCED_OBJECT": "Total Revenue",
        },
        {
            "OBJECT_TYPE": "MEASURE",
            "TABLE": "FactSales",
            "OBJECT": "Total Margin",
            "REFERENCED_OBJECT_TYPE": "MEASURE",
            "REFERENCED_TABLE": "FactSales",
            "REFERENCED_OBJECT": "Total Cost",
        },
        {
            "OBJECT_TYPE": "MEASURE",
            "TABLE": "FactSales",
            "OBJECT": "Margin %",
            "REFERENCED_OBJECT_TYPE": "MEASURE",
            "REFERENCED_TABLE": "FactSales",
            "REFERENCED_OBJECT": "Total Margin",
        },
        {
            "OBJECT_TYPE": "MEASURE",
            "TABLE": "FactSales",
            "OBJECT": "Margin %",
            "REFERENCED_OBJECT_TYPE": "MEASURE",
            "REFERENCED_TABLE": "FactSales",
            "REFERENCED_OBJECT": "Total Revenue",
        },
        {
            "OBJECT_TYPE": "MEASURE",
            "TABLE": "FactSales",
            "OBJECT": "Revenue LY",
            "REFERENCED_OBJECT_TYPE": "MEASURE",
            "REFERENCED_TABLE": "FactSales",
            "REFERENCED_OBJECT": "Total Revenue",
        },
        {
            "OBJECT_TYPE": "MEASURE",
            "TABLE": "FactSales",
            "OBJECT": "Revenue LY",
            "REFERENCED_OBJECT_TYPE": "COLUMN",
            "REFERENCED_TABLE": "FactSales",
            "REFERENCED_OBJECT": "OrderDate",
        },
        {
            "OBJECT_TYPE": "CALC_COLUMN",
            "TABLE": "FactSales",
            "OBJECT": "MarginBand",
            "EXPRESSION": 'IF(FactSales[GrossMargin] > 1000, "High", "Low")',
            "REFERENCED_OBJECT_TYPE": "COLUMN",
            "REFERENCED_TABLE": "FactSales",
            "REFERENCED_OBJECT": "GrossMargin",
        },
        {
            "OBJECT_TYPE": "MEASURE",
            "TABLE": "DimCustomer",
            "OBJECT": "Customer Count",
            "REFERENCED_OBJECT_TYPE": "COLUMN",
            "REFERENCED_TABLE": "DimCustomer",
            "REFERENCED_OBJECT": "CustomerKey",
        },
    ]


def demo_layout(dataset: str = "revenue") -> dict[str, Any]:
    """A `Report/Layout` document with projections, a filter and a format rule."""
    if dataset == "revenue":
        entity, measure, category = "FactSales", "Total Revenue", ("DimCustomer", "Region")
        format_measure = "Margin %"
        filter_field = ("FactSales", "OrderDate")
        title = "Revenue by region"
    else:
        entity, measure, category = "Opportunity", "Open Pipeline", ("Opportunity", "StageName")
        format_measure = "Average Deal"
        filter_field = ("Opportunity", "OwnerName")
        title = "Pipeline by stage"

    visual_config = {
        "name": "visual-revenue-by-region",
        "singleVisual": {
            "visualType": "clusteredColumnChart",
            "projections": {
                "Category": [{"queryRef": f"{category[0]}.{category[1]}"}],
                "Y": [{"queryRef": f"{entity}.{measure}"}],
            },
            "prototypeQuery": {
                "Version": 2,
                "From": [
                    {"Name": "c", "Entity": category[0], "Type": 0},
                    {"Name": "f", "Entity": entity, "Type": 0},
                ],
                "Select": [
                    {
                        "Column": {
                            "Expression": {"SourceRef": {"Source": "c"}},
                            "Property": category[1],
                        },
                        "Name": f"{category[0]}.{category[1]}",
                    },
                    {
                        "Measure": {
                            "Expression": {"SourceRef": {"Source": "f"}},
                            "Property": measure,
                        },
                        "Name": f"{entity}.{measure}",
                    },
                ],
            },
            "vcObjects": {
                "title": [{"properties": {"text": {"expr": {"Literal": {"Value": f"'{title}'"}}}}}]
            },
            "objects": {
                "dataPoint": [
                    {
                        "properties": {
                            "fill": {
                                "solid": {
                                    "color": {
                                        "expr": {
                                            "Measure": {
                                                "Expression": {"SourceRef": {"Entity": entity}},
                                                "Property": format_measure,
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                ]
            },
        },
    }

    card_config = {
        "name": "visual-total-card",
        "singleVisual": {
            "visualType": "card",
            "projections": {"Values": [{"queryRef": f"{entity}.{measure}"}]},
            "prototypeQuery": {
                "Version": 2,
                "From": [{"Name": "f", "Entity": entity, "Type": 0}],
                "Select": [
                    {
                        "Measure": {
                            "Expression": {"SourceRef": {"Source": "f"}},
                            "Property": measure,
                        },
                        "Name": f"{entity}.{measure}",
                    }
                ],
            },
        },
    }

    page_filters = [
        {
            "name": "filter-date",
            "expression": {
                "Column": {
                    "Expression": {"SourceRef": {"Entity": filter_field[0]}},
                    "Property": filter_field[1],
                }
            },
            "type": "Categorical",
        }
    ]

    return {
        "id": 0,
        "sections": [
            {
                "name": "ReportSection1",
                "displayName": "Overview",
                "ordinal": 0,
                "filters": json.dumps(page_filters),
                "visualContainers": [
                    {"config": json.dumps(visual_config)},
                    {"config": json.dumps(card_config)},
                ],
            }
        ],
        "filters": "[]",
    }


class _DemoXmlaRunner:
    """Stands in for a live XMLA connection in the demo and in tests."""

    def __init__(self, rows_by_dataset: dict[str, list[dict[str, Any]]]) -> None:
        self._rows = rows_by_dataset

    def query(self, dataset: DatasetSpec, statement: str) -> list[dict[str, Any]]:
        if "DISCOVER_CALC_DEPENDENCY" not in statement:
            raise RuntimeError(f"the demo runner only serves the dependency DMV: {statement}")
        if dataset.id not in self._rows:
            raise RuntimeError(f"no XMLA endpoint for dataset {dataset.name}")
        return self._rows[dataset.id]


def build_demo_graph() -> LineageGraph:
    """Assemble the full demo graph exactly the way a real scan would."""
    from pbilineage.resolve.router import CapacityRouter
    from pbilineage.resolve.xmla_resolver import XmlaDependencyResolver

    snapshot = snapshot_from_scan_results([demo_scan_result()], DEMO_CAPACITY_SKUS)

    # Finance is on capacity: serve it from the (simulated) DMV.
    runner = _DemoXmlaRunner({REVENUE_DATASET: demo_calc_dependency_rows()})
    router = CapacityRouter(
        xmla_resolver=XmlaDependencyResolver(runner),
        dax_resolver=DaxDependencyResolver(),
        capacity_skus=DEMO_CAPACITY_SKUS,
    )

    dependencies: dict[str, DependencyResult] = {}
    for workspace in snapshot.workspaces:
        for dataset in workspace.datasets:
            dependencies[dataset.id] = router.resolve(workspace, dataset)

        for dataflow in workspace.dataflows:
            dataflow.queries = {"CustomerMaster": DATAFLOW_M}

        for report in workspace.reports:
            report.pages = parse_layout(demo_layout("revenue" if report.id == REVENUE_REPORT else "pipeline"))
            report.layout_available = True

    builder = GraphBuilder()
    graph = builder.build(snapshot, dependencies)

    # The demo model's FactSales table is fed by the Customer Master dataflow
    # as well as by SQL, which is what makes the dataflow layer visible.
    finance = snapshot.workspaces[0]
    revenue = finance.datasets[0]
    customer_table = revenue.table("DimCustomer")
    if customer_table is not None:
        builder.link_dataset_to_dataflow(revenue, customer_table, CUSTOMER_DATAFLOW, "CustomerMaster")
    return graph


def demo_confidence_breakdown(graph: LineageGraph) -> dict[str, int]:
    counts: dict[str, int] = {c.value: 0 for c in Confidence}
    for edge in graph.edges:
        if edge.kind in LINEAGE_EDGES:
            counts[edge.confidence.value] += 1
    return counts
