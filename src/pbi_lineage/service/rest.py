"""Power BI REST plumbing: auth, rate limiting, retry (build spec §7.1).

The Scanner API's documented limits are the design constraint (§7.2):
~500 `getInfo` requests/hour, ~16 concurrent scans, 100 workspaces per
call. This module supplies the token bucket and the 429 handling that
honours `Retry-After`; the Scanner and thin-report engines sit on top.

Everything is injectable: `Transport` is any callable-ish object with
`request(...) -> Response`, so tests drive the whole stack without a
network. The live transport (httpx) is imported lazily.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

API_ROOT = "https://api.powerbi.com/v1.0/myorg"

# Scopes per §7.1
SCOPE_TENANT_READ = "https://analysis.windows.net/powerbi/api/Tenant.Read.All"
SCOPE_DATASET_READ = "https://analysis.windows.net/powerbi/api/Dataset.Read.All"
SCOPE_REPORT_READ = "https://analysis.windows.net/powerbi/api/Report.Read.All"


@dataclass
class Response:
    status: int
    body: Any = None  # parsed JSON when the response was JSON
    content: bytes = b""  # raw bytes (report exports)
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class Transport(Protocol):
    def request(self, method: str, url: str, *, headers: dict[str, str], json: Any = None) -> Response: ...


class TokenProvider(Protocol):
    def token(self) -> str: ...


@dataclass
class StaticToken:
    """A bearer token obtained elsewhere (or in tests)."""

    value: str

    def token(self) -> str:
        return self.value


class MsalTokenProvider:
    """Delegated (device code) or service-principal auth via MSAL (§7.1).

    Service principals additionally require the tenant setting *"Allow
    service principals to use read-only Power BI admin APIs"* with the SP
    in the permitted security group — the #1 setup failure, which
    `AdminClient.preflight` detects and reports explicitly.
    """

    def __init__(
        self,
        *,
        client_id: str,
        authority: str,
        client_secret: str | None = None,
        scopes: list[str] | None = None,
    ):
        self.client_id = client_id
        self.authority = authority
        self.client_secret = client_secret
        self.scopes = scopes or [SCOPE_TENANT_READ]
        self._cached: tuple[str, float] | None = None

    def token(self) -> str:  # pragma: no cover - requires a real tenant
        if self._cached and self._cached[1] > time.time() + 60:
            return self._cached[0]
        try:
            import msal  # noqa: PLC0415 — optional dependency
        except ImportError as exc:
            raise RuntimeError("msal is required for Service auth: pip install msal") from exc
        if self.client_secret:
            app = msal.ConfidentialClientApplication(
                self.client_id, authority=self.authority, client_credential=self.client_secret
            )
            result = app.acquire_token_for_client(scopes=[".default"])
        else:
            app = msal.PublicClientApplication(self.client_id, authority=self.authority)
            flow = app.initiate_device_flow(scopes=self.scopes)
            print(flow.get("message", ""))  # noqa: T201 — device-code UX
            result = app.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            raise RuntimeError(f"auth failed: {result.get('error_description', result)}")
        self._cached = (result["access_token"], time.time() + int(result.get("expires_in", 3600)))
        return self._cached[0]


class TokenBucket:
    """Requests-per-hour limiter (Scanner getInfo is ~500/hour)."""

    def __init__(self, capacity: int, per_seconds: float, *, sleep=time.sleep, clock=time.monotonic):
        self.capacity = capacity
        self.per_seconds = per_seconds
        self.tokens = float(capacity)
        self._sleep = sleep
        self._clock = clock
        self._last = clock()

    def take(self, count: int = 1) -> float:
        """Consume `count` tokens, sleeping if needed. Returns slept seconds."""
        slept = 0.0
        while True:
            now = self._clock()
            self.tokens = min(
                float(self.capacity),
                self.tokens + (now - self._last) * self.capacity / self.per_seconds,
            )
            self._last = now
            if self.tokens >= count:
                self.tokens -= count
                return slept
            deficit = count - self.tokens
            wait = deficit * self.per_seconds / self.capacity
            self._sleep(wait)
            slept += wait


@dataclass
class RetryPolicy:
    max_attempts: int = 5
    base_delay: float = 1.0
    max_delay: float = 60.0


class RestClient:
    """Authenticated REST client with 429/5xx backoff honouring Retry-After."""

    def __init__(
        self,
        transport: Transport,
        token_provider: TokenProvider,
        *,
        bucket: TokenBucket | None = None,
        retry: RetryPolicy | None = None,
        sleep=time.sleep,
        root: str = API_ROOT,
    ):
        self.transport = transport
        self.token_provider = token_provider
        self.bucket = bucket
        self.retry = retry or RetryPolicy()
        self._sleep = sleep
        self.root = root
        self.request_count = 0
        self.throttle_waits: list[float] = []

    def _url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return f"{self.root}/{path.lstrip('/')}"

    def request(self, method: str, path: str, *, json: Any = None, cost: int = 1) -> Response:
        if self.bucket is not None:
            waited = self.bucket.take(cost)
            if waited:
                self.throttle_waits.append(waited)
        headers = {
            "Authorization": f"Bearer {self.token_provider.token()}",
            "Content-Type": "application/json",
        }
        delay = self.retry.base_delay
        last: Response | None = None
        for attempt in range(1, self.retry.max_attempts + 1):
            self.request_count += 1
            response = self.transport.request(method, self._url(path), headers=headers, json=json)
            last = response
            if response.ok:
                return response
            if response.status == 429 or 500 <= response.status < 600:
                if attempt == self.retry.max_attempts:
                    break
                retry_after = response.headers.get("Retry-After") or response.headers.get("retry-after")
                wait = float(retry_after) if retry_after and str(retry_after).isdigit() else delay
                wait = min(wait, self.retry.max_delay)
                self.throttle_waits.append(wait)
                self._sleep(wait)
                delay = min(delay * 2, self.retry.max_delay)
                continue
            break  # 4xx other than 429: not retryable
        assert last is not None
        return last

    def get(self, path: str, *, cost: int = 1) -> Response:
        return self.request("GET", path, cost=cost)

    def post(self, path: str, *, json: Any = None, cost: int = 1) -> Response:
        return self.request("POST", path, json=json, cost=cost)


class HttpxTransport:  # pragma: no cover - network path
    """Live transport. Kept thin so the whole stack above is testable."""

    def __init__(self, timeout: float = 120.0):
        try:
            import httpx  # noqa: PLC0415 — optional dependency
        except ImportError as exc:
            raise RuntimeError("httpx is required for live Service calls") from exc
        self._client = httpx.Client(timeout=timeout)

    def request(self, method: str, url: str, *, headers: dict[str, str], json: Any = None) -> Response:
        raw = self._client.request(method, url, headers=headers, json=json)
        parsed = None
        if raw.headers.get("content-type", "").startswith("application/json"):
            try:
                parsed = raw.json()
            except ValueError:
                parsed = None
        return Response(status=raw.status_code, body=parsed, content=raw.content, headers=dict(raw.headers))
