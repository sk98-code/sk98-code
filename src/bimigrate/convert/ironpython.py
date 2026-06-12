"""IronPython script intent classifier (Section 7, Spotfire automation layer).

IronPython scripts are MANUAL tier by policy — they are never auto-converted.
What this module automates is the *triage*: classify each script's intent
from its Spotfire Document API usage, cite the API-call mapping table, and
recommend the Power Platform rebuild path (Power Automate flow, bookmarks,
field parameters, Fabric notebook...). Output feeds the exception register
and per-report runbooks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Spotfire Document API surface -> (intent, power_platform_equivalent, confidence)
# This is the API-call mapping table; extend it as new estates surface calls.
API_CALL_MAP: dict[str, tuple[str, str, float]] = {
    # export / output
    r"ExportToPdf|PdfExportSettings|Export\w*Pdf": (
        "export",
        "Power Automate: 'Export To File for Power BI Reports' (PDF)",
        0.80,
    ),
    r"ExportDataToFile|ExportText|DataWriter": (
        "export",
        "Power Automate: 'Run a query against a dataset' + create CSV",
        0.75,
    ),
    r"ExportImage|BitmapRenderer": (
        "export",
        "Power Automate export-to-image, or report subscription snapshot",
        0.70,
    ),
    # email / distribution
    r"smtplib|SmtpClient|MailMessage|SendMail": (
        "email",
        "Power Automate: Outlook 'Send an email' action on a schedule/trigger",
        0.80,
    ),
    # navigation
    r"SetActivePage|ActivePageReference|NavigateTo": (
        "navigation",
        "Page navigator buttons / bookmarks",
        0.85,
    ),
    # filtering & marking
    r"FilteringScheme|FilterPanel|\.Filters\[|SetSelection|CheckBoxFilter": (
        "filter_manipulation",
        "Slicers + bookmark states (Selection-changing " "script logic becomes bookmark swaps)",
        0.65,
    ),
    r"Marking\(|SetMarking|MarkingReference": (
        "marking",
        "Cross-highlight interactions; persisted marking has no " "equivalent — document the UX delta",
        0.50,
    ),
    # document properties
    r"Document\.Properties\[|SetProperty|DocumentProperty": (
        "property_write",
        "What-if parameter / field parameter driven by slicer "
        "(script-driven property writes become user selections)",
        0.65,
    ),
    # data operations
    r"AddDataTables|DataTable\.Add|RefreshAsync|ReloadAllData": (
        "data_refresh",
        "Dataset scheduled/on-demand refresh via Power Automate " "'Refresh a dataset' or Fabric pipeline",
        0.75,
    ),
    r"DataFunctionExecutor|DataFunction\.Execute": (
        "analytics_trigger",
        "Fabric notebook activity in a Data pipeline",
        0.60,
    ),
    # visual configuration
    r"Visuals\.Add|VisualContent|ChartType|TablePlot|BarChart|ScatterPlot": (
        "visual_config",
        "Static report design (runtime visual mutation has no "
        "Power BI equivalent; author the variants as pages/bookmarks)",
        0.45,
    ),
    # layout / dialogs
    r"MessageBox|Prompt|Dialog": (
        "user_dialog",
        "No equivalent; redesign as slicer-driven input or " "Power Apps visual for write-back scenarios",
        0.40,
    ),
}


@dataclass
class ScriptClassification:
    script_name: str
    intents: list[str] = field(default_factory=list)
    api_calls: list[dict] = field(default_factory=list)  # {pattern, intent, recommendation, confidence}
    primary_intent: str = "unknown"
    recommendation: str = ""
    confidence: float = 0.0
    tier: str = "MANUAL"  # always MANUAL by policy (Section 7)
    line_annotations: list[tuple[int, str]] = field(default_factory=list)


def classify_ironpython(name: str, code: str) -> ScriptClassification:
    result = ScriptClassification(script_name=name)
    seen: dict[str, tuple[str, float]] = {}

    for lineno, line in enumerate(code.splitlines(), 1):
        for pattern, (intent, recommendation, confidence) in API_CALL_MAP.items():
            if re.search(pattern, line):
                if intent not in seen or seen[intent][1] < confidence:
                    seen[intent] = (recommendation, confidence)
                result.api_calls.append(
                    {
                        "line": lineno,
                        "pattern": pattern,
                        "intent": intent,
                        "recommendation": recommendation,
                        "confidence": confidence,
                    }
                )
                result.line_annotations.append((lineno, f"{intent}: {recommendation}"))

    result.intents = sorted(seen)
    if seen:
        # primary = the highest-confidence intent detected
        primary = max(seen.items(), key=lambda kv: kv[1][1])
        result.primary_intent = primary[0]
        result.recommendation = primary[1][0]
        result.confidence = primary[1][1]
    else:
        result.recommendation = (
            "No recognized Document API calls; manual review of script intent "
            "required before any rebuild recommendation"
        )
        result.confidence = 0.0
    return result


def classify_inventory_scripts(scripts: list) -> list[ScriptClassification]:
    """Classify every IronPython/VBScript block from an ArtifactInventory."""
    out = []
    for script in scripts:
        if script.language in ("ironpython", "vbscript"):
            out.append(classify_ironpython(script.name, script.code))
    return out
