"""Scrub mode: strip sensitive metadata before output leaves the client
network. Applied to inventories immediately after parsing when enabled,
so nothing sensitive ever reaches the SQLite store or the reports."""

from __future__ import annotations

import hashlib
import re

from bimigrate.models.inventory import ArtifactInventory

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
_CONN_STRING_RE = re.compile(r"(password|pwd|secret|token|apikey|api_key)\s*=\s*[^;\s'\"]+", re.IGNORECASE)
_IP_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def _pseudonym(value: str, prefix: str) -> str:
    return f"{prefix}-{hashlib.sha256(value.encode()).hexdigest()[:8]}"


def _scrub_text(text: str) -> str:
    text = _CONN_STRING_RE.sub(lambda m: f"{m.group(1)}=***", text)
    text = _EMAIL_RE.sub(lambda m: _pseudonym(m.group(0), "user"), text)
    text = _IP_RE.sub("0.0.0.0", text)
    return text


def scrub_inventory(inv: ArtifactInventory) -> ArtifactInventory:
    """Pseudonymize servers, drop credentials/emails/IPs, keep structure.

    Pseudonyms are stable hashes so 'same server across 200 workbooks'
    remains visible in the assessment without revealing the hostname.
    """
    for conn in inv.connections:
        if conn.server:
            conn.server = _pseudonym(conn.server, "server")
        if conn.custom_sql:
            conn.custom_sql = _scrub_text(conn.custom_sql)
    for script in inv.scripts:
        script.code = _scrub_text(script.code)
    for rule in inv.security:
        rule.subject = _pseudonym(rule.subject, "principal") if rule.subject else ""
        rule.definition = _scrub_text(rule.definition)
    for issue in inv.issues:
        issue.message = _scrub_text(issue.message)
    inv.platform_extras = {k: v for k, v in inv.platform_extras.items() if k not in ("data_samples",)}
    inv.platform_extras["scrubbed"] = True
    return inv
