"""Power BI theme JSON from source color palettes."""

from __future__ import annotations

DEFAULT_PALETTE = [
    "#118DFF",
    "#12239E",
    "#E66C37",
    "#6B007B",
    "#E044A7",
    "#744EC2",
    "#D9B300",
    "#D64550",
]


def build_theme(name: str, colors: list[str] | None = None) -> dict:
    palette = [c for c in (colors or []) if c.startswith("#")] or DEFAULT_PALETTE
    return {
        "name": f"{name} (migrated)",
        "dataColors": palette[:12],
        "background": "#FFFFFF",
        "foreground": "#252423",
        "tableAccent": palette[0],
        "visualStyles": {
            "*": {
                "*": {
                    "title": [{"fontSize": 12, "fontFamily": "Segoe UI Semibold"}],
                }
            }
        },
    }
