"""Power BI Service connectors: XMLA (M3/M4), thin report engine (§5.4),
Scanner API tenant scan (M5)."""

from pbi_lineage.service.rest import (
    MsalTokenProvider,
    Response,
    RestClient,
    RetryPolicy,
    StaticToken,
    TokenBucket,
)
from pbi_lineage.service.scanner import (
    ScannerClient,
    ScanState,
    model_from_dataset_schema,
)
from pbi_lineage.service.thin_reports import (
    Consumer,
    ConsumerType,
    FailureCause,
    ModelCoverage,
    ReportFetcher,
    build_consumer_index,
    detect_excel_consumers,
    field_usage_frequency,
    gather_model_usage,
    similar_reports,
    synthetic_layout,
)
from pbi_lineage.service.xmla import (
    Fidelity,
    Mode,
    SourceDecision,
    WorkspaceCapability,
    decide_source,
    workspace_capability_from_scanner,
    xmla_connection_string,
)

__all__ = [
    "Consumer",
    "ConsumerType",
    "FailureCause",
    "Fidelity",
    "Mode",
    "ModelCoverage",
    "MsalTokenProvider",
    "ReportFetcher",
    "Response",
    "RestClient",
    "RetryPolicy",
    "ScanState",
    "ScannerClient",
    "SourceDecision",
    "StaticToken",
    "TokenBucket",
    "WorkspaceCapability",
    "build_consumer_index",
    "decide_source",
    "detect_excel_consumers",
    "field_usage_frequency",
    "gather_model_usage",
    "model_from_dataset_schema",
    "similar_reports",
    "synthetic_layout",
    "workspace_capability_from_scanner",
    "xmla_connection_string",
]
