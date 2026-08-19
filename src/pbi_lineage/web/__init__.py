"""Local web UI for pbi_lineage.

Served by `pbi-lineage ui`. Runs entirely on the machine it is started on —
no data leaves the box.
"""

from pbi_lineage.web.app import create_app

__all__ = ["create_app"]
