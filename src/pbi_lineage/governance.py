"""Tenant governance analytics over Scanner, Admin and Activity payloads.

Everything here is pure transformation of data the Service already hands
back — `service/scanner.py` fetches it, this module turns it into the
inventories a platform owner is actually asked for: who has access to
what, which models refresh and when, which reports nobody opens, which
custom visuals are in use, what the capacities are doing.

No network calls happen in this module, so every report below is testable
against a recorded payload.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

# Activity-log operations that mean "a human looked at this"
_VIEW_ACTIVITIES = {"ViewReport", "ViewDashboard", "ViewTile", "ViewReportPage"}
_EXCEL_ACTIVITIES = {"AnalyzedByExternalApplication", "AnalyzeInExcel", "ExportReport"}


def _rows(workspaces: Iterable[dict], key: str) -> list[tuple[dict, dict]]:
    """(workspace, item) pairs for one item collection."""
    out = []
    for workspace in workspaces:
        for item in workspace.get(key, []) or []:
            out.append((workspace, item))
    return out


# ---------------------------------------------------------------------------
# Tenant summary
# ---------------------------------------------------------------------------


@dataclass
class TenantSummary:
    workspaces: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    by_capacity: dict[str, int] = field(default_factory=dict)
    items: dict[str, int] = field(default_factory=dict)
    owners: dict[str, int] = field(default_factory=dict)
    users: int = 0
    orphaned_workspaces: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "workspaces": self.workspaces,
            "by_type": self.by_type,
            "by_capacity": self.by_capacity,
            "items": self.items,
            "top_owners": dict(sorted(self.owners.items(), key=lambda kv: -kv[1])[:25]),
            "users": self.users,
            "orphaned_workspaces": self.orphaned_workspaces,
        }


_ITEM_KEYS = (
    "reports",
    "datasets",
    "dataflows",
    "dashboards",
    "datamarts",
    "notebooks",
    "lakehouses",
    "warehouses",
    "pipelines",
)


def tenant_summary(scan: dict) -> TenantSummary:
    workspaces = scan.get("workspaces", []) or []
    summary = TenantSummary(workspaces=len(workspaces))
    people: set[str] = set()

    for workspace in workspaces:
        summary.by_type[workspace.get("type", "Workspace")] = (
            summary.by_type.get(workspace.get("type", "Workspace"), 0) + 1
        )
        capacity = workspace.get("capacitySku") or (
            "Shared" if not workspace.get("capacityId") else "Dedicated"
        )
        summary.by_capacity[capacity] = summary.by_capacity.get(capacity, 0) + 1

        admins = [
            user
            for user in workspace.get("users", []) or []
            if str(user.get("groupUserAccessRight", user.get("datasetUserAccessRight", ""))).lower()
            in ("admin", "owner")
        ]
        if not admins:
            summary.orphaned_workspaces.append(workspace.get("name") or workspace.get("id", ""))
        for user in workspace.get("users", []) or []:
            identifier = user.get("emailAddress") or user.get("identifier") or user.get("displayName")
            if identifier:
                people.add(str(identifier))

        for key in _ITEM_KEYS:
            items = workspace.get(key, []) or []
            if items:
                summary.items[key] = summary.items.get(key, 0) + len(items)
            for item in items:
                owner = item.get("configuredBy") or item.get("createdBy") or item.get("modifiedBy")
                if owner:
                    summary.owners[str(owner)] = summary.owners.get(str(owner), 0) + 1

    summary.users = len(people)
    return summary


# ---------------------------------------------------------------------------
# Access & permissions
# ---------------------------------------------------------------------------


def access_matrix(scan: dict) -> list[dict]:
    """Every principal's access, per workspace and per item."""
    rows: list[dict] = []
    for workspace in scan.get("workspaces", []) or []:
        for user in workspace.get("users", []) or []:
            rows.append(
                {
                    "scope": "workspace",
                    "workspace": workspace.get("name", ""),
                    "item": "",
                    "principal": user.get("emailAddress")
                    or user.get("displayName")
                    or user.get("identifier", ""),
                    "principal_type": user.get("principalType", ""),
                    "right": user.get("groupUserAccessRight", ""),
                }
            )
        for key in ("reports", "datasets", "dataflows"):
            for workspace_item, item in _rows([workspace], key):
                for user in item.get("users", []) or []:
                    rows.append(
                        {
                            "scope": key[:-1],
                            "workspace": workspace_item.get("name", ""),
                            "item": item.get("name", ""),
                            "principal": user.get("emailAddress")
                            or user.get("displayName")
                            or user.get("identifier", ""),
                            "principal_type": user.get("principalType", ""),
                            "right": user.get("datasetUserAccessRight")
                            or user.get("reportUserAccessRight")
                            or user.get("dataflowUserAccessRight", ""),
                        }
                    )
    return rows


def security_rules(scan: dict) -> list[dict]:
    """RLS roles and OLS (object-level) restrictions per model."""
    rows: list[dict] = []
    for workspace, dataset in _rows(scan.get("workspaces", []) or [], "datasets"):
        for role in dataset.get("roles", []) or []:
            members = [
                m.get("memberName") or m.get("identityProvider") or m.get("memberId", "")
                for m in role.get("members", []) or []
            ]
            for permission in role.get("tablePermissions", []) or []:
                rows.append(
                    {
                        "workspace": workspace.get("name", ""),
                        "model": dataset.get("name", ""),
                        "role": role.get("name", ""),
                        "kind": "RLS" if permission.get("filterExpression") else "OLS",
                        "table": permission.get("name", ""),
                        "expression": permission.get("filterExpression", ""),
                        "members": members,
                    }
                )
            if not role.get("tablePermissions"):
                rows.append(
                    {
                        "workspace": workspace.get("name", ""),
                        "model": dataset.get("name", ""),
                        "role": role.get("name", ""),
                        "kind": "role",
                        "table": "",
                        "expression": "",
                        "members": members,
                    }
                )
    return rows


# ---------------------------------------------------------------------------
# Inventories + refresh events
# ---------------------------------------------------------------------------


def model_inventory(scan: dict, refreshables: list[dict] | None = None) -> list[dict]:
    by_id = {str(r.get("id")): r for r in (refreshables or [])}
    rows = []
    for workspace, dataset in _rows(scan.get("workspaces", []) or [], "datasets"):
        refresh = by_id.get(str(dataset.get("id")), {})
        rows.append(
            {
                "workspace": workspace.get("name", ""),
                "model": dataset.get("name", ""),
                "id": dataset.get("id", ""),
                "owner": dataset.get("configuredBy", ""),
                "created": dataset.get("createdDate", ""),
                "storage_mode": dataset.get("targetStorageMode", ""),
                "tables": len(dataset.get("tables", []) or []),
                "last_refresh": refresh.get("lastRefresh", {}).get("endTime", ""),
                "last_refresh_status": refresh.get("lastRefresh", {}).get("status", ""),
                "refresh_count": refresh.get("refreshCount", 0),
                "refresh_failures": refresh.get("refreshFailures", 0),
                "refresh_schedule": (refresh.get("refreshSchedule") or {}).get("enabled"),
            }
        )
    return rows


def dataflow_inventory(scan: dict) -> list[dict]:
    rows = []
    for workspace, dataflow in _rows(scan.get("workspaces", []) or [], "dataflows"):
        rows.append(
            {
                "workspace": workspace.get("name", ""),
                "dataflow": dataflow.get("name", ""),
                "id": dataflow.get("objectId") or dataflow.get("id", ""),
                "generation": "Gen2" if dataflow.get("generation") == 2 else "Gen1",
                "owner": dataflow.get("configuredBy", ""),
                "modified": dataflow.get("modifiedDateTime", ""),
                "entities": len(dataflow.get("entities", []) or []),
            }
        )
    return rows


def notebook_inventory(scan: dict) -> list[dict]:
    rows = []
    for workspace, notebook in _rows(scan.get("workspaces", []) or [], "notebooks"):
        rows.append(
            {
                "workspace": workspace.get("name", ""),
                "notebook": notebook.get("name", ""),
                "id": notebook.get("id", ""),
                "owner": notebook.get("createdBy") or notebook.get("modifiedBy", ""),
                "modified": notebook.get("lastUpdatedDate", ""),
                "upstream_datasets": [
                    u.get("targetDatasetId") for u in notebook.get("upstreamDatasets", []) or []
                ],
            }
        )
    return rows


def apps_and_audiences(scan: dict) -> list[dict]:
    rows = []
    for workspace, app in _rows(scan.get("workspaces", []) or [], "apps"):
        audiences = app.get("audiences", []) or []
        rows.append(
            {
                "workspace": workspace.get("name", ""),
                "app": app.get("name", ""),
                "id": app.get("id", ""),
                "published_by": app.get("publishedBy", ""),
                "audiences": [a.get("name", "") for a in audiences],
                "users": sum(len(a.get("users", []) or []) for a in audiences)
                + len(app.get("users", []) or []),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Activity-derived analytics
# ---------------------------------------------------------------------------


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def report_usage(scan: dict, activity: list[dict]) -> list[dict]:
    """Views per report, distinct viewers, last view — and the reports with
    no views at all, which is what makes this worth running."""
    views: Counter = Counter()
    viewers: dict[str, set] = defaultdict(set)
    last_seen: dict[str, datetime] = {}
    for event in activity:
        if event.get("Activity") not in _VIEW_ACTIVITIES:
            continue
        report_id = str(event.get("ReportId") or "")
        if not report_id:
            continue
        views[report_id] += 1
        user = str(event.get("UserId") or event.get("UserKey") or "")
        if user:
            viewers[report_id].add(user)
        stamp = _parse_time(event.get("CreationTime"))
        if stamp and (report_id not in last_seen or stamp > last_seen[report_id]):
            last_seen[report_id] = stamp

    rows = []
    for workspace, report in _rows(scan.get("workspaces", []) or [], "reports"):
        report_id = str(report.get("id", ""))
        rows.append(
            {
                "workspace": workspace.get("name", ""),
                "report": report.get("name", ""),
                "id": report_id,
                "type": report.get("reportType", "PowerBIReport"),
                "views": views.get(report_id, 0),
                "distinct_viewers": len(viewers.get(report_id, ())),
                "last_viewed": last_seen[report_id].isoformat() if report_id in last_seen else "",
                "never_viewed": views.get(report_id, 0) == 0,
            }
        )
    return sorted(rows, key=lambda r: (r["views"], r["report"]))


def page_usage(activity: list[dict]) -> list[dict]:
    """Page-level consumption from ViewReportPage events."""
    counts: Counter = Counter()
    for event in activity:
        if event.get("Activity") != "ViewReportPage":
            continue
        key = (
            str(event.get("ReportName") or event.get("ReportId") or ""),
            str(event.get("ReportPageName") or event.get("SectionName") or ""),
        )
        counts[key] += 1
    return [
        {"report": report, "page": page, "views": count} for (report, page), count in counts.most_common()
    ]


def excel_users(activity: list[dict]) -> list[dict]:
    """Who is pulling models into Excel — the consumers a model-cleanup
    exercise cannot see any other way (§5.4.5)."""
    rows: dict[tuple[str, str], dict] = {}
    for event in activity:
        if event.get("Activity") not in _EXCEL_ACTIVITIES:
            continue
        key = (str(event.get("DatasetId") or ""), str(event.get("UserId") or ""))
        entry = rows.setdefault(
            key,
            {
                "dataset_id": key[0],
                "dataset": event.get("DatasetName", ""),
                "user": key[1],
                "events": 0,
                "last_seen": "",
            },
        )
        entry["events"] += 1
        stamp = _parse_time(event.get("CreationTime"))
        if stamp and (not entry["last_seen"] or stamp.isoformat() > entry["last_seen"]):
            entry["last_seen"] = stamp.isoformat()
    return sorted(rows.values(), key=lambda r: -r["events"])


def subscriptions(admin_subscriptions: list[dict]) -> list[dict]:
    rows = []
    for item in admin_subscriptions or []:
        rows.append(
            {
                "title": item.get("title", ""),
                "artifact": item.get("artifactDisplayName", ""),
                "type": item.get("artifactType", ""),
                "owner": item.get("ownerEmail") or (item.get("owner") or {}).get("emailAddress", ""),
                "recipients": [u.get("emailAddress", "") for u in item.get("users", []) or []],
                "frequency": item.get("frequency", ""),
                "enabled": item.get("isEnabled", True),
            }
        )
    return rows


def custom_visual_usage(scan: dict) -> list[dict]:
    """Which custom visuals are packaged where — the license-compliance
    question ('who is using this paid visual?')."""
    usage: dict[str, dict] = {}
    for workspace, report in _rows(scan.get("workspaces", []) or [], "reports"):
        for visual in report.get("customVisuals", []) or []:
            name = visual if isinstance(visual, str) else visual.get("name", str(visual))
            entry = usage.setdefault(name, {"custom_visual": name, "reports": [], "workspaces": set()})
            entry["reports"].append(report.get("name", ""))
            entry["workspaces"].add(workspace.get("name", ""))
    rows = []
    for entry in usage.values():
        rows.append(
            {
                "custom_visual": entry["custom_visual"],
                "report_count": len(entry["reports"]),
                "workspace_count": len(entry["workspaces"]),
                "reports": sorted(entry["reports"])[:50],
            }
        )
    return sorted(rows, key=lambda r: -r["report_count"])


def capacity_metrics(capacities: list[dict], refreshables: list[dict] | None = None) -> list[dict]:
    """Capacity inventory with the workload signals the Admin API returns."""
    load: Counter = Counter()
    for item in refreshables or []:
        capacity_id = str((item.get("capacity") or {}).get("id") or item.get("capacityId") or "")
        if capacity_id:
            load[capacity_id] += int(item.get("refreshCount", 0) or 0)
    rows = []
    for capacity in capacities or []:
        capacity_id = str(capacity.get("id", ""))
        rows.append(
            {
                "capacity": capacity.get("displayName", ""),
                "id": capacity_id,
                "sku": capacity.get("sku", ""),
                "region": capacity.get("region", ""),
                "state": capacity.get("state", ""),
                "admins": capacity.get("admins", []) or [],
                "refreshes": load.get(capacity_id, 0),
            }
        )
    return rows
