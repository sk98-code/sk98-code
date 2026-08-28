"""Power BI Admin REST APIs: Scanner, GetModifiedWorkspaces, Export.

Requires the `Tenant.Read.All` application permission and the tenant setting
"Service principals can use read-only Power BI admin APIs" (see
`pbilineage.config.ADMIN_API_HINT`).

Three surfaces are used:

* **Scanner API** — `POST admin/workspaces/getInfo` (async) then poll
  `scanStatus` and read `scanResult`. This is the only API that returns the
  dataset *schema* (tables, columns, measures) and the M/DAX expression text.
  Capped at 100 workspaces per call and 16 concurrent scans, so calls are
  batched and the batch count is bounded.
* **GetModifiedWorkspaces** — `GET admin/workspaces/modified`, which drives
  incremental scans. Called with no `modifiedSince` it returns the full
  tenant inventory, which is also how a first full scan is seeded.
* **Export API** — an async job per report that yields the PBIX, which is the
  only route to visual-level bindings.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator, Sequence

from pbilineage.clients.http import (
    ApiError,
    HttpResponse,
    RetryPolicy,
    Transport,
    UrllibTransport,
    request_with_retry,
)
from pbilineage.config import SCANNER_BATCH_SIZE, Settings

if TYPE_CHECKING:  # `auth` imports `clients.http`, so this edge stays type-only
    from pbilineage.auth.credentials import TokenProvider

__all__ = ["PowerBIAdminClient", "ScanHandle", "chunked"]

#: query flags that make the scan actually useful for column lineage
SCAN_QUERY_FLAGS = {
    "lineage": "true",
    "datasourceDetails": "true",
    "datasetSchema": "true",
    "datasetExpressions": "true",
    "getArtifactUsers": "false",
}


def chunked(items: Sequence[str], size: int = SCANNER_BATCH_SIZE) -> Iterator[list[str]]:
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


@dataclass(slots=True)
class ScanHandle:
    scan_id: str
    workspace_ids: list[str] = field(default_factory=list)
    status: str = "NotStarted"

    @property
    def finished(self) -> bool:
        return self.status.lower() in ("succeeded", "failed")

    @property
    def succeeded(self) -> bool:
        return self.status.lower() == "succeeded"


class PowerBIAdminClient:
    """Thin, retrying wrapper over the admin surface. No caching of secrets."""

    def __init__(
        self,
        settings: Settings,
        token_provider: TokenProvider,
        transport: Transport | None = None,
        policy: RetryPolicy | None = None,
    ) -> None:
        self.settings = settings
        self._tokens = token_provider
        self._transport = transport or UrllibTransport()
        self._policy = policy or RetryPolicy(
            max_attempts=settings.max_retries, base_seconds=settings.backoff_base_seconds
        )

    # -- plumbing ---------------------------------------------------------
    def _url(self, path: str, params: dict[str, Any] | None = None) -> str:
        url = f"{self.settings.api_root.rstrip('/')}/{path.lstrip('/')}"
        if params:
            pairs = [f"{k}={v}" for k, v in params.items() if v not in (None, "")]
            if pairs:
                url = f"{url}?{'&'.join(pairs)}"
        return url

    def _send(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: Any = None,
        accept_json: bool = True,
    ) -> HttpResponse:
        headers = dict(self._tokens.get_token().header())
        payload: bytes | None = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if accept_json:
            headers["Accept"] = "application/json"
        return request_with_retry(
            self._transport,
            self._policy,
            method,
            self._url(path, params),
            headers,
            payload,
            timeout=self.settings.request_timeout_seconds,
        )

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = self._send("GET", path, params)
        if not response.ok:
            raise ApiError(
                f"GET {path} failed ({response.status})",
                status=response.status,
                body=response.body.decode("utf-8", errors="replace")[:500],
            )
        return response.json()

    # -- inventory --------------------------------------------------------
    def get_modified_workspaces(self, modified_since: str = "") -> list[str]:
        """Workspace IDs changed since `modified_since` (all of them if empty).

        `modifiedSince` must be a UTC ISO-8601 timestamp within the last 30
        days, e.g. `2026-08-01T00:00:00.0000000Z`.
        """
        params: dict[str, Any] = {
            "excludePersonalWorkspaces": str(self.settings.exclude_personal_workspaces).lower(),
            "excludeInActiveWorkspaces": "true",
        }
        if modified_since:
            params["modifiedSince"] = modified_since
        payload = self._get_json("admin/workspaces/modified", params) or []
        return [str(entry.get("id")) for entry in payload if isinstance(entry, dict) and entry.get("id")]

    def get_capacity_skus(self) -> dict[str, str]:
        """Capacity id -> SKU, which is what decides the XMLA routing."""
        try:
            payload = self._get_json("admin/capacities", {"$top": 5000}) or {}
        except ApiError:
            # Not fatal: the router falls back to "on a capacity => try XMLA".
            return {}
        result: dict[str, str] = {}
        for entry in payload.get("value") or []:
            if isinstance(entry, dict) and entry.get("id"):
                result[str(entry["id"])] = str(entry.get("sku") or "")
        return result

    # -- scanner ----------------------------------------------------------
    def start_scan(self, workspace_ids: Sequence[str]) -> ScanHandle:
        if not workspace_ids:
            raise ValueError("start_scan requires at least one workspace id")
        if len(workspace_ids) > SCANNER_BATCH_SIZE:
            raise ValueError(f"the Scanner API accepts at most {SCANNER_BATCH_SIZE} workspaces per call")
        response = self._send(
            "POST",
            "admin/workspaces/getInfo",
            params=dict(SCAN_QUERY_FLAGS),
            body={"workspaces": list(workspace_ids)},
        )
        if not response.ok:
            raise ApiError(
                f"getInfo failed ({response.status})",
                status=response.status,
                body=response.body.decode("utf-8", errors="replace")[:500],
            )
        payload = response.json() or {}
        scan_id = str(payload.get("id") or "")
        if not scan_id:
            raise ApiError("getInfo returned no scan id", response.status)
        return ScanHandle(
            scan_id=scan_id,
            workspace_ids=list(workspace_ids),
            status=str(payload.get("status") or "NotStarted"),
        )

    def get_scan_status(self, handle: ScanHandle) -> ScanHandle:
        payload = self._get_json(f"admin/workspaces/scanStatus/{handle.scan_id}") or {}
        handle.status = str(payload.get("status") or handle.status)
        return handle

    def wait_for_scan(self, handle: ScanHandle, sleep=time.sleep) -> ScanHandle:
        """Poll until the scan finishes, the timeout elapses, or it fails."""
        deadline = time.monotonic() + self.settings.scan_timeout_seconds
        while not handle.finished:
            if time.monotonic() > deadline:
                raise ApiError(
                    f"scan {handle.scan_id} did not finish within "
                    f"{self.settings.scan_timeout_seconds:.0f}s (last status: {handle.status})"
                )
            sleep(self.settings.scan_poll_seconds)
            self.get_scan_status(handle)
        if not handle.succeeded:
            raise ApiError(f"scan {handle.scan_id} finished with status {handle.status}")
        return handle

    def get_scan_result(self, scan_id: str) -> dict[str, Any]:
        payload = self._get_json(f"admin/workspaces/scanResult/{scan_id}")
        return payload if isinstance(payload, dict) else {}

    def scan_workspaces(self, workspace_ids: Sequence[str], sleep=time.sleep) -> list[dict[str, Any]]:
        """Full scan of the given workspaces, batched and polled to completion."""
        results: list[dict[str, Any]] = []
        batches = list(chunked(list(workspace_ids)))
        # The service allows a bounded number of concurrent scans; batches are
        # started in waves of that size and each wave is drained before the next.
        wave_size = max(1, min(self.settings.max_concurrent_scans, len(batches) or 1))
        for wave_start in range(0, len(batches), wave_size):
            wave = batches[wave_start : wave_start + wave_size]
            handles = [self.start_scan(batch) for batch in wave]
            for handle in handles:
                self.wait_for_scan(handle, sleep=sleep)
                results.append(self.get_scan_result(handle.scan_id))
        return results

    # -- dataflows --------------------------------------------------------
    def export_dataflow(self, dataflow_id: str) -> dict[str, Any] | None:
        """`model.json` for a dataflow — the only place its M scripts live."""
        try:
            payload = self._get_json(f"admin/dataflows/{dataflow_id}/export")
        except ApiError:
            return None
        return payload if isinstance(payload, dict) else None

    # -- report export ----------------------------------------------------
    def export_report(
        self,
        workspace_id: str,
        report_id: str,
        destination: str | Path,
        sleep=time.sleep,
    ) -> Path | None:
        """Export a report's PBIX, returning the file path (None if refused).

        Tries the admin async export first, then the in-group synchronous
        export. A report can legitimately not be exportable — a live-connected
        or protected report, or one whose export the tenant disallows — and
        that is not a scan failure: the report simply ends up in the graph
        with model lineage and no visual layer.
        """
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)

        content = self._export_report_async(workspace_id, report_id, sleep=sleep)
        if content is None:
            content = self._export_report_direct(workspace_id, report_id)
        if content is None:
            return None
        path.write_bytes(content)
        return path

    def _export_report_async(self, workspace_id: str, report_id: str, sleep) -> bytes | None:
        try:
            response = self._send(
                "POST", f"admin/workspaces/{workspace_id}/reports/{report_id}/Export", body={}
            )
        except ApiError:
            return None
        if not response.ok:
            return None

        payload = response.json() or {}
        export_id = str(payload.get("id") or payload.get("exportId") or "")
        if not export_id:
            # Some deployments answer synchronously with the file itself.
            return response.body or None

        deadline = time.monotonic() + self.settings.scan_timeout_seconds
        status = str(payload.get("status") or "Running")
        while status.lower() in ("running", "notstarted", "inprogress"):
            if time.monotonic() > deadline:
                return None
            sleep(self.settings.scan_poll_seconds)
            try:
                payload = self._get_json(f"admin/exports/{export_id}") or {}
            except ApiError:
                return None
            status = str(payload.get("status") or "Failed")
        if status.lower() != "succeeded":
            return None

        try:
            file_response = self._send("GET", f"admin/exports/{export_id}/file", accept_json=False)
        except ApiError:
            return None
        return file_response.body if file_response.ok and file_response.body else None

    def _export_report_direct(self, workspace_id: str, report_id: str) -> bytes | None:
        try:
            response = self._send(
                "GET", f"groups/{workspace_id}/reports/{report_id}/Export", accept_json=False
            )
        except ApiError:
            return None
        return response.body if response.ok and response.body else None
