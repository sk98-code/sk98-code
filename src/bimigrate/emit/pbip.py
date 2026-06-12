"""PBIP project emitter (Section 10).

Generates the folder layout per the Microsoft PBIP spec:

    <Name>.pbip
    <Name>.Report/
        definition.pbir
        report.json           (best-effort visual placement)
        StaticResources/RegisteredResources/<theme>.json
    <Name>.SemanticModel/
        definition.pbism
        definition/           (TMDL via TmdlModel)
        diagramLayout.json

Visual placement maps source layout order onto a 1280x720 grid; pixel
fidelity is explicitly a non-goal (Section 13) — structural parity is.
"""

from __future__ import annotations

import json
from pathlib import Path

from bimigrate.emit.theme import build_theme
from bimigrate.emit.tmdl import TmdlModel
from bimigrate.models.inventory import ArtifactInventory

# normalized visual type -> Power BI visual type name in report.json
VISUAL_TYPE_MAP = {
    "bar": "barChart",
    "line": "lineChart",
    "area": "areaChart",
    "pie": "pieChart",
    "scatter": "scatterChart",
    "combo": "lineStackedColumnComboChart",
    "table": "tableEx",
    "pivot_table": "pivotTable",
    "text_table": "tableEx",
    "heat_map": "pivotTable",
    "tree_map": "treemap",
    "map": "map",
    "gauge": "gauge",
    "kpi": "card",
    "funnel": "funnel",
    "waterfall": "waterfallChart",
    "histogram": "columnChart",
    "box_plot": "boxWhisker",  # custom visual placeholder
    "bullet": "kpi",
    "gantt": "barChart",
    "text": "textbox",
    "filter": "slicer",
    "auto": "columnChart",
    "custom": "textbox",  # placeholder with annotation
}

PAGE_W, PAGE_H = 1280, 720
GRID_COLS = 3


class PbipEmitter:
    def __init__(self, out_dir: Path):
        self.out_dir = out_dir

    def emit(self, inv: ArtifactInventory, model: TmdlModel, theme_colors: list[str] | None = None) -> Path:
        name = _safe(inv.artifact_name)
        root = self.out_dir / name
        report_dir = root / f"{name}.Report"
        model_dir = root / f"{name}.SemanticModel"
        report_dir.mkdir(parents=True, exist_ok=True)
        model_dir.mkdir(parents=True, exist_ok=True)

        (root / f"{name}.pbip").write_text(
            json.dumps(
                {
                    # Desktop validates this against the pbipProperties pattern:
                    # ^https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.x.y/schema.json$
                    "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
                    "version": "1.0",
                    "artifacts": [{"report": {"path": f"{name}.Report"}}],
                    "settings": {"enableAutoRecovery": True},
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        (report_dir / "definition.pbir").write_text(
            json.dumps(
                {
                    "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/1.0.0/schema.json",
                    "version": "1.0",
                    "datasetReference": {"byPath": {"path": f"../{name}.SemanticModel"}},
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        (report_dir / "report.json").write_text(
            json.dumps(self._build_report_layout(inv), indent=2), encoding="utf-8"
        )

        theme = build_theme(inv.artifact_name, theme_colors)
        resources = report_dir / "StaticResources" / "RegisteredResources"
        resources.mkdir(parents=True, exist_ok=True)
        (resources / "migrated_theme.json").write_text(json.dumps(theme, indent=2), encoding="utf-8")

        (model_dir / "definition.pbism").write_text(
            json.dumps(
                {
                    "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
                    "version": "1.0",
                    "settings": {},
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        (model_dir / "diagramLayout.json").write_text(
            json.dumps(
                {
                    "version": "1.1.0",
                    "diagrams": [],
                }
            ),
            encoding="utf-8",
        )

        model.write(model_dir / "definition")
        return root

    # -- report layout (best-effort) ----------------------------------------

    def _build_report_layout(self, inv: ArtifactInventory) -> dict:
        sections = []
        pages = inv.worksheets or []
        for page_idx, ws in enumerate(pages):
            visuals = []
            count = max(len(ws.visuals), 1)
            cols = min(GRID_COLS, count)
            rows = -(-count // cols)
            w, h = PAGE_W // cols, PAGE_H // rows
            for i, visual in enumerate(ws.visuals):
                pbi_type = VISUAL_TYPE_MAP.get(visual.visual_type, "textbox")
                config = {
                    "name": f"visual{page_idx}_{i}",
                    "singleVisual": {
                        "visualType": pbi_type,
                        "projections": {},
                        "objects": {},
                    },
                }
                if visual.is_custom:
                    config["singleVisual"][
                        "migrationNote"
                    ] = f"custom source visual '{visual.raw_type}' — MANUAL rebuild required"
                visuals.append(
                    {
                        "x": (i % cols) * w,
                        "y": (i // cols) * h,
                        "width": w,
                        "height": h,
                        "z": i,
                        "config": json.dumps(config),
                    }
                )
            sections.append(
                {
                    "name": f"section{page_idx}",
                    "displayName": ws.name,
                    "ordinal": page_idx,
                    "visualContainers": visuals,
                    "width": PAGE_W,
                    "height": PAGE_H,
                    "displayOption": 1,
                }
            )
        # legacy report.json format: no $schema property (Desktop rejects
        # unknown schema URLs); theming travels inside the config string
        return {
            "themeCollection": {
                "customTheme": {"name": "migrated_theme.json", "type": "RegisteredResources"}
            },
            "sections": sections,
            "config": json.dumps(
                {
                    "version": "5.43",
                    "themeCollection": {
                        "customTheme": {"name": "migrated_theme.json", "type": "RegisteredResources"}
                    },
                }
            ),
            "layoutOptimization": 0,
        }


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in " -_" else "_" for c in name).strip() or "Migrated"
