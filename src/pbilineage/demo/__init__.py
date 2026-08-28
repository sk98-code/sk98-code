"""A synthetic tenant, so the pipeline can be run and tested without a tenant."""

from __future__ import annotations

from pbilineage.demo.fixtures import (
    DEMO_CAPACITY_SKUS,
    build_demo_graph,
    demo_calc_dependency_rows,
    demo_layout,
    demo_scan_result,
)

__all__ = [
    "DEMO_CAPACITY_SKUS",
    "build_demo_graph",
    "demo_calc_dependency_rows",
    "demo_layout",
    "demo_scan_result",
]
