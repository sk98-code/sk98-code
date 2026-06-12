"""Complexity scoring and migration-readiness banding.

Weights reflect *migration effort drivers*, not object counts: a workbook
with three nested LODs is harder to migrate than one with thirty bar
charts. Weights are data (module-level dict) so engagements can tune them.
"""

from __future__ import annotations

from bimigrate.models.inventory import ArtifactInventory, ComplexityBand

# feature_flag -> effort weight per occurrence
FEATURE_WEIGHTS: dict[str, float] = {
    # cheap, fully automatable
    "worksheets": 0.5,
    "sheets": 0.5,
    "pages": 0.5,
    "dashboards": 1.0,
    "stories": 2.0,
    "parameters": 0.5,
    "document_properties": 0.5,
    "calculated_fields": 1.0,
    "calculated_columns": 1.0,
    "master_measures": 1.0,
    "variables": 0.8,
    "joins": 1.0,
    "relationships": 1.0,
    "live_connections": 1.0,
    "extracts": 1.5,
    "qvd_files": 1.0,
    # medium drivers
    "table_calculations": 3.0,
    "lod_expressions": 3.0,
    "set_analysis": 3.0,
    "over_expressions": 3.0,
    "custom_sql": 2.5,
    "filter_action": 1.5,
    "highlight_action": 1.5,
    "url_action": 1.5,
    "information_links": 2.5,
    "data_functions": 4.0,
    "user_filters": 2.0,
    "section_access": 4.0,
    "load_script_lines": 0.05,
    # heavy drivers — these dominate real migration effort
    "nested_lod": 8.0,
    "alternate_states": 10.0,
    "ironpython_scripts": 8.0,
    "macros": 12.0,
    "custom_mods": 10.0,
    "custom_extensions": 8.0,
    "synthetic_key_risk": 5.0,
    "group_filters": 2.0,
    "dual_axis": 1.5,
}

# score thresholds for banding
MEDIUM_THRESHOLD = 25.0
COMPLEX_THRESHOLD = 75.0


def score_inventory(inv: ArtifactInventory) -> ArtifactInventory:
    """Mutates and returns inv with complexity_score / complexity_band set."""
    score = 0.0
    for flag, count in inv.feature_flags.items():
        score += FEATURE_WEIGHTS.get(flag, 0.5) * count
    # custom visuals outside the flag system
    score += sum(8.0 for ws in inv.worksheets for v in ws.visuals if v.is_custom)
    inv.complexity_score = round(score, 2)
    if score >= COMPLEX_THRESHOLD:
        inv.complexity_band = ComplexityBand.COMPLEX
    elif score >= MEDIUM_THRESHOLD:
        inv.complexity_band = ComplexityBand.MEDIUM
    else:
        inv.complexity_band = ComplexityBand.SIMPLE
    return inv


def effort_hours(inv: ArtifactInventory) -> float:
    """Rough effort model for assessment-mode TCO inputs: score -> hours.

    Calibration: a simple workbook (score ~10) is roughly half a day of
    convert+validate work; effort grows superlinearly with complexity
    because review/rework dominates at the top end.
    """
    base = inv.complexity_score * 0.4
    if inv.complexity_band == ComplexityBand.MEDIUM:
        base *= 1.4
    elif inv.complexity_band == ComplexityBand.COMPLEX:
        base *= 2.0
    return round(max(base, 1.0), 1)
