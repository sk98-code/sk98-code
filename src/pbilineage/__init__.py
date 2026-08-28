"""pbilineage — column-level lineage for Power BI / Microsoft Fabric tenants.

Traces source table/column -> Power Query (M) transform -> semantic model
column/measure -> report visual/filter, by talking to the live service:
the Power BI Admin REST APIs (Scanner, Export, GetModifiedWorkspaces) and,
where the workspace sits on capacity, the XMLA endpoint.

Every lineage edge carries a confidence tag so the UI can tell certain
lineage (the engine told us) from best-effort lineage (we parsed it):

    resolved   $SYSTEM.DISCOVER_CALC_DEPENDENCY, layout field bindings
    heuristic  DAX tokenizer / M step matcher recognised the construct
    opaque     we saw the construct and refuse to guess at it
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
