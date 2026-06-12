"""Tableau Server / Cloud REST collector.

Pulls the server-side feature surface that workbook files cannot carry:
the workbook inventory itself (.twbx downloads for the parsers), refresh
schedules, subscriptions and data-driven alerts (Section 4.1 scheduling
layer). Authentication is PAT-based (the recommended REST method).

The HTTP transport is injectable (callable(method, url, headers, body) ->
(status, headers, bytes)) so the client is unit-testable offline and can
be wrapped with retry/proxy logic per engagement. stdlib-only by design.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

Transport = Callable[[str, str, dict, bytes | None], tuple[int, dict, bytes]]

API_VERSION = "3.22"


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, dict, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
        return response.status, dict(response.headers), response.read()


@dataclass
class CollectedItem:
    kind: str  # workbook | schedule | subscription | alert
    name: str
    payload: dict = field(default_factory=dict)
    local_path: Path | None = None


class TableauServerError(Exception):
    pass


class TableauServerClient:
    def __init__(self, server_url: str, site_content_url: str = "", transport: Transport = urllib_transport):
        self.base = server_url.rstrip("/")
        self.site_content_url = site_content_url
        self.transport = transport
        self._token: str | None = None
        self._site_id: str | None = None

    # -- auth ---------------------------------------------------------------

    def sign_in(self, pat_name: str, pat_secret: str) -> None:
        body = json.dumps(
            {
                "credentials": {
                    "personalAccessTokenName": pat_name,
                    "personalAccessTokenSecret": pat_secret,
                    "site": {"contentUrl": self.site_content_url},
                }
            }
        ).encode()
        payload = self._call("POST", "/auth/signin", body=body, authed=False)
        creds = payload["credentials"]
        self._token = creds["token"]
        self._site_id = creds["site"]["id"]

    def sign_out(self) -> None:
        if self._token:
            self._call("POST", "/auth/signout", parse=False)
            self._token = None

    # -- inventory -------------------------------------------------------------

    def list_workbooks(self) -> list[CollectedItem]:
        return self._paged("workbooks", "workbook", "workbook")

    def list_schedules(self) -> list[CollectedItem]:
        # schedules are server-scoped, not site-scoped
        payload = self._call("GET", "/schedules", site_scoped=False)
        rows = payload.get("schedules", {}).get("schedule", [])
        return [CollectedItem(kind="schedule", name=r.get("name", ""), payload=r) for r in rows]

    def list_subscriptions(self) -> list[CollectedItem]:
        return self._paged("subscriptions", "subscription", "subscription")

    def list_data_alerts(self) -> list[CollectedItem]:
        return self._paged("dataAlerts", "dataAlert", "alert")

    def download_workbook(self, workbook_id: str, out_dir: Path) -> Path:
        status, headers, data = self._raw("GET", self._site_url(f"/workbooks/{workbook_id}/content"))
        if status != 200:
            raise TableauServerError(f"download failed for {workbook_id}: HTTP {status}")
        disposition = headers.get("Content-Disposition", "")
        name = workbook_id + (".twbx" if ".twbx" in disposition else ".twb")
        if 'filename="' in disposition:
            name = disposition.split('filename="')[1].split('"')[0]
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / name
        path.write_bytes(data)
        return path

    def collect_estate(self, out_dir: Path) -> list[CollectedItem]:
        """Download every workbook + capture schedules/subscriptions/alerts."""
        items = []
        for wb in self.list_workbooks():
            wb.local_path = self.download_workbook(wb.payload["id"], out_dir)
            items.append(wb)
        items.extend(self.list_schedules())
        items.extend(self.list_subscriptions())
        items.extend(self.list_data_alerts())
        (out_dir / "server_metadata.json").write_text(
            json.dumps(
                [
                    {"kind": i.kind, "name": i.name, "payload": i.payload}
                    for i in items
                    if i.kind != "workbook"
                ],
                indent=2,
            ),
            encoding="utf-8",
        )
        return items

    # -- plumbing ---------------------------------------------------------------

    def _site_url(self, path: str) -> str:
        if self._site_id is None:
            raise TableauServerError("not signed in")
        return f"{self.base}/api/{API_VERSION}/sites/{self._site_id}{path}"

    def _paged(self, resource: str, item_key: str, kind: str) -> list[CollectedItem]:
        items: list[CollectedItem] = []
        page = 1
        while True:
            payload = self._call("GET", f"/{resource}?pageSize=100&pageNumber={page}")
            block = payload.get(resource, {}) or {}
            rows = block.get(item_key, [])
            items.extend(
                CollectedItem(kind=kind, name=r.get("name", r.get("id", "")), payload=r) for r in rows
            )
            pagination = payload.get("pagination", {})
            total = int(pagination.get("totalAvailable", len(items)))
            if len(items) >= total or not rows:
                return items
            page += 1

    def _call(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        authed: bool = True,
        site_scoped: bool = True,
        parse: bool = True,
    ):
        if authed and site_scoped:
            url = self._site_url(path)
        else:
            url = f"{self.base}/api/{API_VERSION}{path}"
        status, _, data = self._raw(method, url, body, authed=authed)
        if status >= 400:
            raise TableauServerError(f"{method} {path}: HTTP {status}: {data[:200]!r}")
        return json.loads(data) if parse and data else {}

    def _raw(
        self, method: str, url: str, body: bytes | None = None, authed: bool = True
    ) -> tuple[int, dict, bytes]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if authed:
            if self._token is None:
                raise TableauServerError("not signed in")
            headers["X-Tableau-Auth"] = self._token
        return self.transport(method, url, headers, body)
