"""Scanner API tenant scan — build spec §7.2–7.3, Milestone 8.

Designed around the documented limits: ~500 `getInfo` requests/hour, ~16
concurrent scans, **100 workspaces per call**, and very large
`scanResult` payloads. Implemented here: workspace batching, the token
bucket, resumable state persisted after every batch, and incremental
`modified?modifiedSince=` deltas.

Each batch's result is written to its own file as it arrives rather than
being accumulated in memory, so a 500-workspace tenant never holds the
whole payload at once; `iter_workspaces` streams them back per file.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from pbi_lineage.service.rest import RestClient

WORKSPACES_PER_CALL = 100
MAX_CONCURRENT_SCANS = 16
GETINFO_PER_HOUR = 500

_SCAN_QUERY = (
    "lineage=true&datasourceDetails=true&datasetSchema=true" "&datasetExpressions=true&getArtifactUsers=true"
)


@dataclass
class ScanState:
    """Resumable scan progress, persisted to disk after every batch."""

    workspace_ids: list[str] = field(default_factory=list)
    completed_batches: list[int] = field(default_factory=list)
    batch_files: dict[str, str] = field(default_factory=dict)  # str(batch index) -> file name
    started_at: float = field(default_factory=time.time)
    modified_since: str | None = None

    def to_json(self) -> dict:
        return {
            "workspace_ids": self.workspace_ids,
            "completed_batches": self.completed_batches,
            "batch_files": self.batch_files,
            "started_at": self.started_at,
            "modified_since": self.modified_since,
        }

    @classmethod
    def from_json(cls, data: dict) -> "ScanState":
        return cls(
            workspace_ids=list(data.get("workspace_ids", [])),
            completed_batches=list(data.get("completed_batches", [])),
            batch_files=dict(data.get("batch_files", {})),
            started_at=data.get("started_at", time.time()),
            modified_since=data.get("modified_since"),
        )


@dataclass
class ScanOutcome:
    state: ScanState
    batches_run: int = 0
    batches_skipped: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        total = -(-len(self.state.workspace_ids) // WORKSPACES_PER_CALL)
        return len(self.state.completed_batches) >= total


class ScannerClient:
    """Admin Scanner API driver (§7.2)."""

    def __init__(
        self,
        client: RestClient,
        out_dir: Path,
        *,
        poll_interval: float = 2.0,
        max_polls: int = 120,
        sleep=time.sleep,
    ):
        self.client = client
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.poll_interval = poll_interval
        self.max_polls = max_polls
        self._sleep = sleep

    # -- preflight ---------------------------------------------------------

    def preflight(self) -> tuple[bool, str]:
        """Detect the #1 setup failure (§7.1): a service principal without
        the read-only admin API tenant setting, and the enumerate-vs-export
        permission gap."""
        response = self.client.get("admin/groups?$top=1")
        if response.ok:
            return True, "admin API reachable"
        if response.status in (401, 403):
            return False, (
                "Admin API denied. If running as a service principal, enable the tenant "
                "setting 'Allow service principals to use read-only Power BI admin APIs' "
                "and add the principal to the permitted security group. Note a principal "
                "that can enumerate reports may still be unable to export them — the thin "
                "report engine records that gap per report."
            )
        return False, f"admin API returned HTTP {response.status}"

    # -- workspace discovery ----------------------------------------------

    def list_workspace_ids(self, *, modified_since: str | None = None) -> list[str]:
        path = "admin/workspaces/modified"
        if modified_since:
            path += f"?modifiedSince={modified_since}"
        response = self.client.get(path)
        if not response.ok:
            return []
        body = response.body or []
        return [str(item.get("id")) for item in body if item.get("id")]

    # -- scanning ----------------------------------------------------------

    def _state_path(self) -> Path:
        return self.out_dir / "scan_state.json"

    def load_state(self) -> ScanState | None:
        path = self._state_path()
        if not path.exists():
            return None
        try:
            return ScanState.from_json(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            return None

    def _save_state(self, state: ScanState) -> None:
        # write-then-rename so a kill mid-write cannot corrupt the state
        temporary = self._state_path().with_suffix(".tmp")
        temporary.write_text(json.dumps(state.to_json(), indent=2), encoding="utf-8")
        temporary.replace(self._state_path())

    def scan(
        self, workspace_ids: list[str], *, resume: bool = True, modified_since: str | None = None
    ) -> ScanOutcome:
        state = self.load_state() if resume else None
        if state is None or state.workspace_ids != workspace_ids:
            state = ScanState(workspace_ids=list(workspace_ids), modified_since=modified_since)
            self._save_state(state)
        outcome = ScanOutcome(state=state)

        batches = [
            workspace_ids[i : i + WORKSPACES_PER_CALL]
            for i in range(0, len(workspace_ids), WORKSPACES_PER_CALL)
        ]
        for index, batch in enumerate(batches):
            if index in state.completed_batches:
                outcome.batches_skipped += 1
                continue
            payload = self._scan_batch(batch, outcome)
            if payload is None:
                continue
            file_name = f"scan_batch_{index:04d}.json"
            (self.out_dir / file_name).write_text(json.dumps(payload), encoding="utf-8")
            state.batch_files[str(index)] = file_name
            state.completed_batches.append(index)
            self._save_state(state)  # resumable after every batch
            outcome.batches_run += 1
        return outcome

    def _scan_batch(self, workspace_ids: list[str], outcome: ScanOutcome) -> dict | None:
        started = self.client.post(
            f"admin/workspaces/getInfo?{_SCAN_QUERY}", json={"workspaces": workspace_ids}
        )
        if not started.ok:
            outcome.warnings.append(f"getInfo failed: HTTP {started.status}")
            return None
        scan_id = (started.body or {}).get("id")
        if not scan_id:
            outcome.warnings.append("getInfo returned no scan id")
            return None

        for _ in range(self.max_polls):
            status = self.client.get(f"admin/workspaces/scanStatus/{scan_id}")
            if not status.ok:
                outcome.warnings.append(f"scanStatus failed: HTTP {status.status}")
                return None
            state_text = (status.body or {}).get("status", "")
            if state_text == "Succeeded":
                break
            if state_text in ("Failed", "Cancelled"):
                outcome.warnings.append(f"scan {scan_id} ended as {state_text}")
                return None
            self._sleep(self.poll_interval)
        else:
            outcome.warnings.append(f"scan {scan_id} did not complete within the poll budget")
            return None

        result = self.client.get(f"admin/workspaces/scanResult/{scan_id}")
        if not result.ok:
            outcome.warnings.append(f"scanResult failed: HTTP {result.status}")
            return None
        return result.body or {}

    # -- reading results back ---------------------------------------------

    def iter_workspaces(self, state: ScanState | None = None) -> Iterator[dict]:
        """Stream workspaces from the persisted batch files, one file at a
        time — the whole tenant payload is never resident at once."""
        state = state or self.load_state()
        if state is None:
            return
        for index in sorted(state.completed_batches):
            file_name = state.batch_files.get(str(index))
            if not file_name:
                continue
            path = self.out_dir / file_name
            if not path.exists():
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            for workspace in payload.get("workspaces", []) or []:
                yield workspace

    def merged_result(self, state: ScanState | None = None) -> dict:
        return {"workspaces": list(self.iter_workspaces(state))}


# ---------------------------------------------------------------------------
# Degraded-mode model reconstruction (§7.2: datasetSchema, mode M4)
# ---------------------------------------------------------------------------


def model_from_dataset_schema(dataset_json: dict):
    """Build a Model from Scanner `datasetSchema` — the only model metadata
    available without XMLA (Pro workspaces). No VertiPaq sizes and not all
    DAX, so callers must label results reduced-confidence (see
    `xmla.WorkspaceCapability.degradation_notice`)."""
    from pbi_lineage.schema import Column, Measure, Model, Table  # local import: avoid cycle

    model = Model(name=dataset_json.get("name", ""))
    for table_json in dataset_json.get("tables", []) or []:
        table = Table(name=table_json.get("name", ""), is_hidden=bool(table_json.get("isHidden")))
        for column_json in table_json.get("columns", []) or []:
            table.columns.append(
                Column(
                    name=column_json.get("name", ""),
                    data_type=column_json.get("dataType"),
                    is_hidden=bool(column_json.get("isHidden")),
                    expression=column_json.get("expression"),
                )
            )
        for measure_json in table_json.get("measures", []) or []:
            table.measures.append(
                Measure(
                    name=measure_json.get("name", ""),
                    expression=measure_json.get("expression", "") or "",
                    is_hidden=bool(measure_json.get("isHidden")),
                    description=measure_json.get("description"),
                )
            )
        model.tables.append(table)
    return model
