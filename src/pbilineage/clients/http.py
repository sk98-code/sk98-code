"""HTTP plumbing: a tiny transport seam plus retry/backoff.

The transport is an interface rather than a hard dependency on a client
library for two reasons: the collectors can then be tested without a network,
and the package stays installable with nothing but the standard library.

Retry policy follows what the Power BI REST APIs actually need: honour
`Retry-After` on 429, exponential backoff with jitter on 5xx and on transport
errors, and never retry a 4xx that is not 429 (a 401 will not fix itself).
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

__all__ = [
    "ApiError",
    "HttpResponse",
    "RetryPolicy",
    "Transport",
    "UrllibTransport",
    "request_with_retry",
]


class ApiError(RuntimeError):
    """A non-retryable API failure, or a retryable one that ran out of attempts."""

    def __init__(self, message: str, status: int = 0, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass(slots=True)
class HttpResponse:
    status: int
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        if not self.body:
            return None
        try:
            return json.loads(self.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ApiError(f"response was not JSON: {exc}", self.status) from exc

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def header(self, name: str, default: str = "") -> str:
        lowered = {k.lower(): v for k, v in self.headers.items()}
        return lowered.get(name.lower(), default)


class Transport(Protocol):
    """One HTTP round trip. Implementations must not raise on HTTP status."""

    def send(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None = None,
        timeout: float = 120.0,
    ) -> HttpResponse: ...


class UrllibTransport:
    """Standard-library transport — no third-party dependency required."""

    def send(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None = None,
        timeout: float = 120.0,
    ) -> HttpResponse:
        request = urllib.request.Request(url, data=body, method=method.upper())
        for key, value in headers.items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return HttpResponse(
                    status=response.status,
                    body=response.read(),
                    headers={k: v for k, v in response.headers.items()},
                )
        except urllib.error.HTTPError as exc:
            return HttpResponse(
                status=exc.code,
                body=exc.read() or b"",
                headers={k: v for k, v in (exc.headers or {}).items()},
            )
        except urllib.error.URLError as exc:
            raise ApiError(f"transport error for {method} {url}: {exc.reason}") from exc


@dataclass(slots=True)
class RetryPolicy:
    max_attempts: int = 5
    base_seconds: float = 2.0
    max_seconds: float = 60.0
    #: statuses worth trying again: throttling and transient server faults
    retry_statuses: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504})
    sleep: Callable[[float], None] = time.sleep

    def delay_for(self, attempt: int, retry_after: str = "") -> float:
        if retry_after:
            try:
                return min(float(retry_after), self.max_seconds)
            except ValueError:
                pass
        backoff = min(self.base_seconds * (2 ** max(attempt - 1, 0)), self.max_seconds)
        # jitter keeps a batch of workspace scans from retrying in lockstep
        return backoff * (0.5 + random.random() / 2)


def request_with_retry(
    transport: Transport,
    policy: RetryPolicy,
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None = None,
    timeout: float = 120.0,
) -> HttpResponse:
    """Send a request, retrying throttles and transient faults with backoff."""
    last: HttpResponse | None = None
    last_error: ApiError | None = None

    for attempt in range(1, policy.max_attempts + 1):
        try:
            response = transport.send(method, url, headers, body, timeout)
        except ApiError as exc:
            last_error = exc
            if attempt == policy.max_attempts:
                raise
            policy.sleep(policy.delay_for(attempt))
            continue

        if response.ok or response.status not in policy.retry_statuses:
            return response

        last = response
        if attempt == policy.max_attempts:
            break
        policy.sleep(policy.delay_for(attempt, response.header("Retry-After")))

    if last is not None:
        raise ApiError(
            f"{method} {url} failed after {policy.max_attempts} attempts " f"(last status {last.status})",
            status=last.status,
            body=last.body.decode("utf-8", errors="replace")[:2000],
        )
    raise ApiError(str(last_error) if last_error else f"{method} {url} failed")
