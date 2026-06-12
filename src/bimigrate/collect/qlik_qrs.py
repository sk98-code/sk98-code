"""Qlik Sense QRS (Repository Service) collector.

Pulls the server-side surface for Qlik Sense estates: app inventory,
streams (security layer), reload tasks (scheduling layer) and app
exports (.qvf downloads for the parsers — pair with an Engine JSON
export for full fidelity).

Auth model: virtual-proxy header authentication (Xrf key + user header),
the standard pattern for service integrations. Certificate auth can be
layered by swapping the transport. Transport is injectable for tests.
"""

from __future__ import annotations

import json
import secrets
import string
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

Transport = Callable[[str, str, dict, bytes | None], tuple[int, dict, bytes]]


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, dict, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
        return response.status, dict(response.headers), response.read()


@dataclass
class QrsItem:
    kind: str  # app | stream | reload_task
    name: str
    payload: dict = field(default_factory=dict)
    local_path: Path | None = None


class QlikQrsError(Exception):
    pass


class QlikQrsClient:
    def __init__(
        self,
        server_url: str,
        user_directory: str,
        user_id: str,
        virtual_proxy: str = "",
        transport: Transport = urllib_transport,
    ):
        self.base = server_url.rstrip("/")
        if virtual_proxy:
            self.base = f"{self.base}/{virtual_proxy.strip('/')}"
        self.user_directory = user_directory
        self.user_id = user_id
        self.transport = transport
        alphabet = string.ascii_letters + string.digits
        self._xrf = "".join(secrets.choice(alphabet) for _ in range(16))

    # -- inventory ----------------------------------------------------------

    def list_apps(self) -> list[QrsItem]:
        rows = self._call("GET", "/qrs/app/full")
        return [QrsItem(kind="app", name=r.get("name", ""), payload=r) for r in rows]

    def list_streams(self) -> list[QrsItem]:
        rows = self._call("GET", "/qrs/stream/full")
        return [QrsItem(kind="stream", name=r.get("name", ""), payload=r) for r in rows]

    def list_reload_tasks(self) -> list[QrsItem]:
        rows = self._call("GET", "/qrs/reloadtask/full")
        return [QrsItem(kind="reload_task", name=r.get("name", ""), payload=r) for r in rows]

    def export_app(self, app_id: str, out_dir: Path) -> Path:
        """Two-step QVF export: request a download ticket, then fetch it."""
        ticket = self._call("POST", f"/qrs/app/{app_id}/export/{self._xrf_token()}")
        download_path = ticket.get("downloadPath", "")
        if not download_path:
            raise QlikQrsError(f"no downloadPath in export ticket for {app_id}")
        status, _, data = self._raw("GET", f"{self.base}{download_path}")
        if status != 200:
            raise QlikQrsError(f"export download failed for {app_id}: HTTP {status}")
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{app_id}.qvf"
        path.write_bytes(data)
        return path

    def collect_estate(self, out_dir: Path) -> list[QrsItem]:
        items = []
        for app in self.list_apps():
            try:
                app.local_path = self.export_app(app.payload["id"], out_dir)
            except QlikQrsError as exc:
                app.payload["export_error"] = str(exc)
            items.append(app)
        items.extend(self.list_streams())
        items.extend(self.list_reload_tasks())
        (out_dir / "qrs_metadata.json").write_text(
            json.dumps(
                [{"kind": i.kind, "name": i.name, "payload": i.payload} for i in items if i.kind != "app"],
                indent=2,
            ),
            encoding="utf-8",
        )
        return items

    # -- plumbing --------------------------------------------------------------

    @staticmethod
    def _xrf_token() -> str:
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(16))

    def _call(self, method: str, path: str, body: bytes | None = None):
        sep = "&" if "?" in path else "?"
        url = f"{self.base}{path}{sep}xrfkey={self._xrf}"
        status, _, data = self._raw(method, url, body)
        if status >= 400:
            raise QlikQrsError(f"{method} {path}: HTTP {status}: {data[:200]!r}")
        return json.loads(data) if data else {}

    def _raw(self, method: str, url: str, body: bytes | None = None):
        headers = {
            "X-Qlik-Xrfkey": self._xrf,
            "X-Qlik-User": (f"UserDirectory={self.user_directory}; " f"UserId={self.user_id}"),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        return self.transport(method, url, headers, body)
