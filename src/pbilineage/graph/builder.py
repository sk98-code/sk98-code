"""Turn collected metadata into the lineage graph.

This is where the four layers are stitched together:

    DataSource -> (M steps) -> model Table/Column -> Measure -> Visual

Each layer contributes edges with its own confidence, and the builder never
upgrades one: a measure dependency from the DMV stays `resolved`, a column
traced through an unrecognised M step stays `opaque`, and the UI can tell
them apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pbilineage.models import (
    Confidence,
    DataflowSpec,
    DataSourceSpec,
    DatasetSpec,
    EdgeKind,
    LineageEdge,
    LineageGraph,
    LineageNode,
    NodeKind,
    ReportSpec,
    TableSpec,
    TenantSnapshot,
    VisualFieldSpec,
    WorkspaceSpec,
    node_id,
)
from pbilineage.parsers.m_query import MQueryAnalysis, MSourceRef, analyze_m_query
from pbilineage.resolve.base import DependencyResult, ObjectRef, ObjectType

__all__ = ["GraphBuilder"]

#: DMV object types that live on a table but are not columns
_MEASURE_TYPES = {ObjectType.MEASURE}
_COLUMN_TYPES = {ObjectType.COLUMN, ObjectType.CALC_COLUMN}


def _source_key(source: MSourceRef | DataSourceSpec) -> str:
    if isinstance(source, DataSourceSpec):
        return f"{source.kind}:{source.server}:{source.database}:{source.path}"
    return f"{source.kind}:{source.server}:{source.database}"


@dataclass(slots=True)
class GraphBuilder:
    """Accumulates nodes and edges; `graph` is the result."""

    graph: LineageGraph = field(default_factory=LineageGraph)

    # -- containers -------------------------------------------------------
    def add_workspace(self, workspace: WorkspaceSpec) -> str:
        identifier = node_id(NodeKind.WORKSPACE, workspace.id)
        self.graph.add_node(
            LineageNode(
                id=identifier,
                kind=NodeKind.WORKSPACE,
                name=workspace.name,
                qualified_name=workspace.name,
                workspace_id=workspace.id,
                properties={
                    "capacity_id": workspace.capacity_id,
                    "capacity_sku": workspace.capacity_sku,
                    "tier": workspace.tier.value,
                    "has_xmla": workspace.tier.has_xmla,
                    "is_personal": workspace.is_personal,
                    "state": workspace.state,
                },
            )
        )
        return identifier

    def add_dataset(self, workspace: WorkspaceSpec, dataset: DatasetSpec) -> str:
        workspace_node = self.add_workspace(workspace)
        identifier = node_id(NodeKind.SEMANTIC_MODEL, dataset.id)
        self.graph.add_node(
            LineageNode(
                id=identifier,
                kind=NodeKind.SEMANTIC_MODEL,
                name=dataset.name,
                qualified_name=f"{workspace.name} / {dataset.name}",
                workspace_id=workspace.id,
                properties={
                    "dataset_id": dataset.id,
                    "configured_by": dataset.configured_by,
                    "resolution_path": dataset.resolution_path,
                    "table_count": len(dataset.tables),
                },
            )
        )
        self._contains(workspace_node, identifier)

        for table in dataset.tables:
            self._add_table(workspace, dataset, table, identifier)
        return identifier

    def _add_table(
        self,
        workspace: WorkspaceSpec,
        dataset: DatasetSpec,
        table: TableSpec,
        model_node: str,
    ) -> str:
        identifier = self.table_id(dataset, table.name)
        self.graph.add_node(
            LineageNode(
                id=identifier,
                kind=NodeKind.TABLE,
                name=table.name,
                qualified_name=f"{workspace.name} / {dataset.name} / {table.name}",
                workspace_id=workspace.id,
                properties={
                    "dataset_id": dataset.id,
                    "is_calculated": table.is_calculated,
                    "column_count": len(table.columns),
                    "measure_count": len(table.measures),
                    "description": table.description,
                },
            )
        )
        self._contains(model_node, identifier)

        for column in table.columns:
            column_node = self.column_id(dataset, table.name, column.name)
            self.graph.add_node(
                LineageNode(
                    id=column_node,
                    kind=NodeKind.COLUMN,
                    name=column.name,
                    qualified_name=(f"{workspace.name} / {dataset.name} / {table.name}[{column.name}]"),
                    workspace_id=workspace.id,
                    properties={
                        "dataset_id": dataset.id,
                        "table": table.name,
                        "data_type": column.data_type,
                        "is_calculated": column.is_calculated,
                        "is_hidden": column.is_hidden,
                        "expression": column.expression,
                    },
                )
            )
            self._contains(identifier, column_node)

        for measure in table.measures:
            measure_node = self.measure_id(dataset, table.name, measure.name)
            self.graph.add_node(
                LineageNode(
                    id=measure_node,
                    kind=NodeKind.MEASURE,
                    name=measure.name,
                    qualified_name=(f"{workspace.name} / {dataset.name} / {table.name}[{measure.name}]"),
                    workspace_id=workspace.id,
                    properties={
                        "dataset_id": dataset.id,
                        "table": table.name,
                        "expression": measure.expression,
                        "is_hidden": measure.is_hidden,
                        "description": measure.description,
                    },
                )
            )
            self._contains(identifier, measure_node)
        return identifier

    # -- identity helpers -------------------------------------------------
    def table_id(self, dataset: DatasetSpec, table: str) -> str:
        return node_id(NodeKind.TABLE, dataset.id, table)

    def column_id(self, dataset: DatasetSpec, table: str, column: str) -> str:
        return node_id(NodeKind.COLUMN, dataset.id, table, column)

    def measure_id(self, dataset: DatasetSpec, table: str, measure: str) -> str:
        return node_id(NodeKind.MEASURE, dataset.id, table, measure)

    def _contains(self, parent: str, child: str) -> None:
        self.graph.add_edge(
            LineageEdge(
                source=parent,
                target=child,
                kind=EdgeKind.CONTAINS,
                confidence=Confidence.RESOLVED,
                evidence="metadata",
            )
        )

    # -- semantic-model dependencies --------------------------------------
    def add_dependencies(self, dataset: DatasetSpec, result: DependencyResult) -> None:
        """Add measure / calculated-object edges from a resolver's output."""
        for dependency in result.dependencies:
            source = self._node_for_ref(dataset, dependency.source)
            target = self._node_for_ref(dataset, dependency.target)
            if source is None or target is None or source == target:
                continue
            self.graph.add_edge(
                LineageEdge(
                    source=source,
                    target=target,
                    kind=EdgeKind.DERIVES_FROM,
                    confidence=dependency.confidence,
                    evidence=dependency.evidence,
                    properties={
                        "path": result.path,
                        "note": dependency.note,
                        "referenced_type": dependency.target.object_type.value,
                    },
                )
            )
        self.graph.warnings.extend(result.warnings)

    def _node_for_ref(self, dataset: DatasetSpec, ref: ObjectRef) -> str | None:
        if not ref.table and not ref.name:
            return None
        if ref.object_type in _MEASURE_TYPES:
            return self.measure_id(dataset, ref.table, ref.name)
        if ref.object_type in _COLUMN_TYPES:
            return self.column_id(dataset, ref.table, ref.name)
        if ref.object_type in (ObjectType.TABLE, ObjectType.CALC_TABLE):
            return self.table_id(dataset, ref.table or ref.name)
        if ref.object_type in (ObjectType.PARTITION, ObjectType.M_EXPRESSION):
            return self.table_id(dataset, ref.table) if ref.table else None
        if ref.object_type in (ObjectType.ROWS_ALLOWED, ObjectType.RELATIONSHIP):
            # Both hang off a table; the object name is the role, not a column.
            return self.table_id(dataset, ref.table) if ref.table else None
        # Unknown object type: fall back on shape — a name implies a column.
        if ref.name and ref.table:
            return self.column_id(dataset, ref.table, ref.name)
        return self.table_id(dataset, ref.table or ref.name)

    # -- Power Query / source lineage -------------------------------------
    def add_data_source(self, source: MSourceRef | DataSourceSpec) -> str:
        key = _source_key(source)
        identifier = node_id(NodeKind.DATA_SOURCE, key)
        self.graph.add_node(
            LineageNode(
                id=identifier,
                kind=NodeKind.DATA_SOURCE,
                name=source.display(),
                qualified_name=source.display(),
                properties={
                    "source_kind": source.kind,
                    "server": getattr(source, "server", ""),
                    "database": getattr(source, "database", ""),
                    "native_query": getattr(source, "native_query", ""),
                },
            )
        )
        return identifier

    def _source_table(self, source: MSourceRef, table_name: str) -> str:
        source_node = self.add_data_source(source)
        identifier = node_id(NodeKind.TABLE, f"source:{_source_key(source)}", table_name)
        self.graph.add_node(
            LineageNode(
                id=identifier,
                kind=NodeKind.TABLE,
                name=table_name,
                qualified_name=f"{source.connection_display()} / {table_name}",
                properties={"is_source": True, "source_kind": source.kind},
            )
        )
        self._contains(source_node, identifier)
        return identifier

    def _source_column(self, source: MSourceRef, table_name: str, column: str) -> str:
        table_node = self._source_table(source, table_name)
        identifier = node_id(NodeKind.COLUMN, f"source:{_source_key(source)}", table_name, column)
        self.graph.add_node(
            LineageNode(
                id=identifier,
                kind=NodeKind.COLUMN,
                name=column,
                qualified_name=f"{source.connection_display()} / {table_name}[{column}]",
                properties={"is_source": True, "source_kind": source.kind},
            )
        )
        self._contains(table_node, identifier)
        return identifier

    def add_power_query_lineage(self, dataset: DatasetSpec, table: TableSpec) -> MQueryAnalysis | None:
        """Trace a table's columns back through its M partition to the source.

        Returns the analysis so the caller can report on opaque steps.
        """
        partition = next((p for p in table.partitions if p.source_type == "m" and p.expression), None)
        if partition is None:
            return None

        analysis = analyze_m_query(partition.expression, query_name=table.name)
        table_node = self.table_id(dataset, table.name)

        primary = analysis.sources[0] if analysis.sources else None
        if primary is None:
            self.graph.warnings.append(
                f"{dataset.name}/{table.name}: no data source recognised in the M query"
            )
            return analysis

        source_table_name = primary.item or table.name
        self.graph.add_edge(
            LineageEdge(
                source=table_node,
                target=self._source_table(primary, source_table_name),
                kind=EdgeKind.DERIVES_FROM,
                confidence=analysis.confidence,
                evidence=f"m:{primary.function or primary.kind}",
                properties={
                    "steps": len(analysis.steps),
                    "opaque_steps": sum(1 for s in analysis.steps if s.is_opaque),
                    "unrecognized": sorted(set(analysis.unrecognized)),
                    "native_query": primary.native_query,
                },
            )
        )
        for extra in analysis.sources[1:]:
            self.graph.add_edge(
                LineageEdge(
                    source=table_node,
                    target=self.add_data_source(extra),
                    kind=EdgeKind.DERIVES_FROM,
                    confidence=analysis.confidence,
                    evidence=f"m:{extra.function or extra.kind}",
                    properties={"secondary_source": True},
                )
            )

        for column in table.columns:
            if column.is_calculated:
                continue  # DAX calculated columns come from the dependency resolver
            column_node = self.column_id(dataset, table.name, column.name)
            lineage = analysis.lineage_for(column.name)

            if lineage is not None and lineage.source_columns:
                for source_column in sorted(lineage.source_columns):
                    self.graph.add_edge(
                        LineageEdge(
                            source=column_node,
                            target=self._source_column(primary, source_table_name, source_column),
                            kind=EdgeKind.DERIVES_FROM,
                            confidence=lineage.confidence,
                            evidence="m:" + (lineage.ops[-1] if lineage.ops else "passthrough"),
                            properties={"transform_chain": lineage.ops},
                        )
                    )
                continue

            if analysis.opaque:
                # We know where the table came from but not this column's path.
                self.graph.add_edge(
                    LineageEdge(
                        source=column_node,
                        target=self.add_data_source(primary),
                        kind=EdgeKind.DERIVES_FROM,
                        confidence=Confidence.OPAQUE,
                        evidence="m:opaque",
                        properties={
                            "reason": "an unrecognised M transform breaks the column trace",
                            "unrecognized": sorted(set(analysis.unrecognized)),
                        },
                    )
                )
                continue

            if analysis.column_set_known:
                # The query named its columns and this one was not among them;
                # it most likely arrived by a path we did not model.
                continue

            # No step touched this column: a straight pass-through from source.
            self.graph.add_edge(
                LineageEdge(
                    source=column_node,
                    target=self._source_column(primary, source_table_name, column.name),
                    kind=EdgeKind.DERIVES_FROM,
                    confidence=Confidence.HEURISTIC,
                    evidence="m:passthrough-assumed",
                    properties={"assumed": True},
                )
            )
        return analysis

    # -- dataflows --------------------------------------------------------
    def add_dataflow(self, workspace: WorkspaceSpec, dataflow: DataflowSpec) -> str:
        workspace_node = self.add_workspace(workspace)
        identifier = node_id(NodeKind.DATAFLOW, dataflow.id)
        self.graph.add_node(
            LineageNode(
                id=identifier,
                kind=NodeKind.DATAFLOW,
                name=dataflow.name,
                qualified_name=f"{workspace.name} / {dataflow.name}",
                workspace_id=workspace.id,
                properties={"dataflow_id": dataflow.id, "entity_count": len(dataflow.queries)},
            )
        )
        self._contains(workspace_node, identifier)

        for entity, script in dataflow.queries.items():
            entity_node = node_id(NodeKind.TABLE, f"dataflow:{dataflow.id}", entity)
            self.graph.add_node(
                LineageNode(
                    id=entity_node,
                    kind=NodeKind.TABLE,
                    name=entity,
                    qualified_name=f"{workspace.name} / {dataflow.name} / {entity}",
                    workspace_id=workspace.id,
                    properties={"is_dataflow_entity": True, "dataflow_id": dataflow.id},
                )
            )
            self._contains(identifier, entity_node)
            if not script:
                continue

            analysis = analyze_m_query(script, query_name=entity)
            for source in analysis.sources:
                self.graph.add_edge(
                    LineageEdge(
                        source=entity_node,
                        target=self._source_table(source, source.item or entity),
                        kind=EdgeKind.DERIVES_FROM,
                        confidence=analysis.confidence,
                        evidence=f"m:{source.function or source.kind}",
                        properties={"opaque": analysis.opaque},
                    )
                )
                for name, lineage in analysis.columns.items():
                    column_node = node_id(NodeKind.COLUMN, f"dataflow:{dataflow.id}", entity, name)
                    self.graph.add_node(
                        LineageNode(
                            id=column_node,
                            kind=NodeKind.COLUMN,
                            name=name,
                            qualified_name=(f"{workspace.name} / {dataflow.name} / {entity}[{name}]"),
                            workspace_id=workspace.id,
                            properties={"is_dataflow_entity": True},
                        )
                    )
                    self._contains(entity_node, column_node)
                    for source_column in sorted(lineage.source_columns):
                        self.graph.add_edge(
                            LineageEdge(
                                source=column_node,
                                target=self._source_column(source, source.item or entity, source_column),
                                kind=EdgeKind.DERIVES_FROM,
                                confidence=lineage.confidence,
                                evidence="m:" + (lineage.ops[-1] if lineage.ops else "passthrough"),
                                properties={"transform_chain": lineage.ops},
                            )
                        )
                break  # column lineage is attributed to the primary source only
        return identifier

    def link_dataset_to_dataflow(
        self, dataset: DatasetSpec, table: TableSpec, dataflow_id: str, entity: str
    ) -> None:
        """A Direct Lake / dataflow-backed table derives from a dataflow entity."""
        self.graph.add_edge(
            LineageEdge(
                source=self.table_id(dataset, table.name),
                target=node_id(NodeKind.TABLE, f"dataflow:{dataflow_id}", entity),
                kind=EdgeKind.DERIVES_FROM,
                confidence=Confidence.RESOLVED,
                evidence="partition:entity",
                properties={"dataflow_id": dataflow_id, "entity": entity},
            )
        )

    # -- reports ----------------------------------------------------------
    def add_report(self, workspace: WorkspaceSpec, report: ReportSpec, dataset: DatasetSpec | None) -> str:
        workspace_node = self.add_workspace(workspace)
        identifier = node_id(NodeKind.REPORT, report.id)
        self.graph.add_node(
            LineageNode(
                id=identifier,
                kind=NodeKind.REPORT,
                name=report.name,
                qualified_name=f"{workspace.name} / {report.name}",
                workspace_id=workspace.id,
                properties={
                    "report_id": report.id,
                    "dataset_id": report.dataset_id,
                    "layout_available": report.layout_available,
                    "page_count": len(report.pages),
                },
            )
        )
        self._contains(workspace_node, identifier)

        if report.dataset_id:
            self.graph.add_edge(
                LineageEdge(
                    source=identifier,
                    target=node_id(NodeKind.SEMANTIC_MODEL, report.dataset_id),
                    kind=EdgeKind.DERIVES_FROM,
                    confidence=Confidence.RESOLVED,
                    evidence="metadata:datasetId",
                )
            )

        if not report.layout_available:
            self.graph.warnings.append(
                f"report '{report.name}' has no exported layout: model lineage is present "
                "but its visuals are not"
            )

        for field_ref in report.fields:
            self._bind_field(dataset, field_ref, identifier, "report filter")

        for page in report.pages:
            page_node = node_id(NodeKind.PAGE, report.id, page.id)
            self.graph.add_node(
                LineageNode(
                    id=page_node,
                    kind=NodeKind.PAGE,
                    name=page.name,
                    qualified_name=f"{workspace.name} / {report.name} / {page.name}",
                    workspace_id=workspace.id,
                    properties={"ordinal": page.ordinal, "visual_count": len(page.visuals)},
                )
            )
            self._contains(identifier, page_node)
            for field_ref in page.fields:
                self._bind_field(dataset, field_ref, page_node, "page filter")

            for visual in page.visuals:
                visual_node = node_id(NodeKind.VISUAL, report.id, page.id, visual.id)
                self.graph.add_node(
                    LineageNode(
                        id=visual_node,
                        kind=NodeKind.VISUAL,
                        name=visual.title or visual.visual_type or visual.id,
                        qualified_name=(
                            f"{workspace.name} / {report.name} / {page.name} / "
                            f"{visual.title or visual.visual_type}"
                        ),
                        workspace_id=workspace.id,
                        properties={
                            "visual_type": visual.visual_type,
                            "title": visual.title,
                            "field_count": len(visual.fields),
                        },
                    )
                )
                self._contains(page_node, visual_node)
                for field_ref in visual.fields:
                    self._bind_field(dataset, field_ref, visual_node, field_ref.role)
        return identifier

    def _bind_field(
        self,
        dataset: DatasetSpec | None,
        field_ref: VisualFieldSpec,
        consumer_node: str,
        role: str,
    ) -> None:
        """Point a model object at the visual / page / report that consumes it."""
        if dataset is None or not field_ref.field:
            return
        table = dataset.table(field_ref.table)
        if table is None:
            self.graph.warnings.append(
                f"visual binding '{field_ref.table}'[{field_ref.field}] does not match any "
                f"table in model '{dataset.name}'"
            )
            return

        measure = next((m for m in table.measures if m.name.lower() == field_ref.field.lower()), None)
        column = table.column(field_ref.field)

        if measure is not None and field_ref.field_kind != "column":
            producer = self.measure_id(dataset, table.name, measure.name)
        elif column is not None:
            producer = self.column_id(dataset, table.name, column.name)
        elif measure is not None:
            producer = self.measure_id(dataset, table.name, measure.name)
        else:
            self.graph.warnings.append(
                f"visual binding '{field_ref.table}'[{field_ref.field}] matches no column or "
                f"measure in model '{dataset.name}'"
            )
            return

        self.graph.add_edge(
            LineageEdge(
                source=producer,
                target=consumer_node,
                kind=EdgeKind.USED_IN,
                confidence=Confidence.RESOLVED,
                evidence="report-layout",
                properties={"role": role, "aggregation": field_ref.aggregation},
            )
        )

    # -- whole-snapshot convenience ---------------------------------------
    def build(
        self,
        snapshot: TenantSnapshot,
        dependencies: dict[str, DependencyResult] | None = None,
        include_power_query: bool = True,
    ) -> LineageGraph:
        """Build the graph for a whole snapshot.

        `dependencies` maps dataset id -> resolver output; datasets missing
        from it simply contribute no calculated-object edges.
        """
        results = dependencies or {}
        # Reports can live in a different workspace from their model, so the
        # dataset lookup has to be tenant-wide.
        datasets_by_id = {
            dataset.id: dataset for workspace in snapshot.workspaces for dataset in workspace.datasets
        }
        for workspace in snapshot.workspaces:
            self.add_workspace(workspace)

            for dataflow in workspace.dataflows:
                self.add_dataflow(workspace, dataflow)

            for dataset in workspace.datasets:
                self.add_dataset(workspace, dataset)
                result = results.get(dataset.id)
                if result is not None:
                    self.add_dependencies(dataset, result)
                if include_power_query:
                    for table in dataset.tables:
                        self.add_power_query_lineage(dataset, table)
                        for partition in table.partitions:
                            if partition.source_type == "entity" and partition.dataflow_id:
                                self.link_dataset_to_dataflow(
                                    dataset,
                                    table,
                                    partition.dataflow_id,
                                    partition.entity_name or table.name,
                                )

            for report in workspace.reports:
                self.add_report(workspace, report, datasets_by_id.get(report.dataset_id))

        self.graph.warnings.extend(snapshot.warnings)
        self.graph.scanned_at = snapshot.scanned_at
        return self.graph
