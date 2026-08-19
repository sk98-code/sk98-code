"""Browser sign-in for the Power BI Service, without an app registration.

Pasting a bearer token is a developer's workaround, not a way to sign in.
Two tools that most Power BI users already have log in through the normal
browser prompt and will hand over a token afterwards:

* **Power BI PowerShell** — `Connect-PowerBIServiceAccount` authenticates
  through a Microsoft first-party application, which is why it needs no
  app registration of your own; `Get-PowerBIAccessToken` then returns the
  token for that session.
* **Azure CLI** — `az login` opens the same browser prompt, and
  `az account get-access-token` issues a token for a named resource.

Both are used here strictly as *token sources*: this module never sees a
password, and the browser prompt is Microsoft's own.

A third path, MSAL device code, is kept for tenants that require their own
app registration — it is the only one of the three that does.

Nothing here logs a token. Errors quote the tool's own message and the
exact command that fixes it, because "auth failed" with no next step is
the most common way a tool of this kind wastes someone's afternoon.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # noqa: S404 — invoking the user's own signed-in CLI is the point
from dataclasses import dataclass, field

# The audience Power BI requires; a token minted for anything else is
# rejected with a 401 that says nothing useful.
POWERBI_RESOURCE = "https://analysis.windows.net/powerbi/api"
POWERBI_SCOPE = f"{POWERBI_RESOURCE}/.default"

READY = "ready"  # signed in; a token can be had right now
NEEDS_LOGIN = "needs_login"  # installed, but nobody is signed in
NOT_INSTALLED = "not_installed"
NEEDS_SETUP = "needs_setup"  # usable, but only after work in the Entra portal
UNKNOWN = "unknown"  # the probe itself could not answer


@dataclass
class Provider:
    """One way to obtain a token, and what it needs before it can."""

    id: str
    name: str
    status: str
    detail: str = ""
    login_hint: str = ""  # the exact command that signs the user in
    install_hint: str = ""
    needs_app_registration: bool = False


@dataclass
class TokenResult:
    ok: bool
    token: str = field(default="", repr=False)  # never repr a bearer token
    provider: str = ""
    account: str = ""
    error: str = ""
    hint: str = ""


def _launch(command: list[str]) -> list[str]:
    """The argv that actually starts this program.

    On Windows the Azure CLI is `az.cmd`, a batch file. `CreateProcess`
    cannot start one, so a bare `subprocess.run(["…/az.cmd"])` raises
    WinError 193 — which is how probing for the Azure CLI turned into an
    Internal Server Error on the machine that had it installed. Batch files
    go through the command interpreter.
    """
    if os.name == "nt" and command and command[0].lower().endswith((".cmd", ".bat")):
        return ["cmd.exe", "/c", *command]
    return command


def _run(command: list[str], timeout: float = 120.0) -> subprocess.CompletedProcess:
    """Run a probe. Never raises.

    Every failure here is information about the environment, not a fault:
    the tool is missing, it hung, the machine refused to start it. A probe
    that raises takes out the whole sign-in page and tells the user
    nothing, which is exactly what it did.
    """
    try:
        return subprocess.run(  # noqa: S603 — fixed argv, no shell
            _launch(command), capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            command, 1, "", f"timed out after {timeout:.0f}s"
        )
    except OSError as exc:  # not executable, not found, blocked by policy
        return subprocess.CompletedProcess(command, 1, "", f"could not be started: {exc}")
    except Exception as exc:  # noqa: BLE001 — a probe must not take down the page
        return subprocess.CompletedProcess(command, 1, "", f"probe failed: {exc}")


def _powershell() -> str | None:
    """PowerShell 7 first — it is the cross-platform one and the module
    supports it; Windows PowerShell is the fallback."""
    return shutil.which("pwsh") or shutil.which("powershell")


# ---------------------------------------------------------------------------
# Azure CLI
# ---------------------------------------------------------------------------


def azure_cli_provider() -> Provider:
    if shutil.which("az") is None:
        return Provider(
            "azure-cli",
            "Azure CLI",
            NOT_INSTALLED,
            "the `az` command is not on PATH",
            install_hint="install the Azure CLI from https://aka.ms/installazurecli",
        )
    result = _run(["az", "account", "show", "-o", "json"], timeout=30)
    if result.returncode != 0:
        return Provider(
            "azure-cli",
            "Azure CLI",
            NEEDS_LOGIN,
            _first_line(result.stderr) or "no Azure CLI session",
            login_hint="az login",
        )
    account = ""
    try:
        account = json.loads(result.stdout).get("user", {}).get("name", "")
    except ValueError:
        pass
    return Provider("azure-cli", "Azure CLI", READY, account, login_hint="az login")


def azure_cli_token() -> TokenResult:
    result = _run(
        ["az", "account", "get-access-token", "--resource", POWERBI_RESOURCE, "-o", "json"],
        timeout=120,
    )
    if result.returncode != 0:
        return TokenResult(
            False,
            provider="azure-cli",
            error=_first_line(result.stderr) or "az could not issue a token",
            hint="run `az login` in a terminal, then try again",
        )
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        return TokenResult(False, provider="azure-cli", error="az returned output that is not JSON")
    token = payload.get("accessToken", "")
    if not token:
        return TokenResult(False, provider="azure-cli", error="az returned no accessToken")
    return TokenResult(True, token=token, provider="azure-cli", account=payload.get("subscription", ""))


# ---------------------------------------------------------------------------
# Power BI PowerShell (MicrosoftPowerBIMgmt)
# ---------------------------------------------------------------------------

_PBI_TOKEN_SCRIPT = (
    "$ErrorActionPreference='Stop';"
    "Import-Module MicrosoftPowerBIMgmt.Profile -ErrorAction Stop;"
    "(Get-PowerBIAccessToken -AsString)"
)
_PBI_PROBE_SCRIPT = (
    "if (Get-Module -ListAvailable -Name MicrosoftPowerBIMgmt.Profile) { 'installed' } else { 'missing' }"
)


def powerbi_powershell_provider() -> Provider:
    shell = _powershell()
    if shell is None:
        return Provider(
            "powerbi-powershell",
            "Power BI PowerShell",
            NOT_INSTALLED,
            "no PowerShell on PATH",
            install_hint="install PowerShell 7 from https://aka.ms/powershell",
        )
    probe = _run([shell, "-NoProfile", "-Command", _PBI_PROBE_SCRIPT], timeout=180)
    if "installed" not in probe.stdout:
        return Provider(
            "powerbi-powershell",
            "Power BI PowerShell",
            NOT_INSTALLED,
            "the MicrosoftPowerBIMgmt module is not installed",
            install_hint="Install-Module -Name MicrosoftPowerBIMgmt -Scope CurrentUser",
        )
    result = _run([shell, "-NoProfile", "-Command", _PBI_TOKEN_SCRIPT], timeout=120)
    if result.returncode != 0 or not result.stdout.strip():
        return Provider(
            "powerbi-powershell",
            "Power BI PowerShell",
            NEEDS_LOGIN,
            _first_line(result.stderr) or "no Power BI session",
            login_hint="Connect-PowerBIServiceAccount",
        )
    return Provider(
        "powerbi-powershell",
        "Power BI PowerShell",
        READY,
        "signed in",
        login_hint="Connect-PowerBIServiceAccount",
    )


def powerbi_powershell_token() -> TokenResult:
    shell = _powershell()
    if shell is None:
        return TokenResult(False, provider="powerbi-powershell", error="no PowerShell on PATH")
    result = _run([shell, "-NoProfile", "-Command", _PBI_TOKEN_SCRIPT], timeout=120)
    token = result.stdout.strip()
    if result.returncode != 0 or not token:
        return TokenResult(
            False,
            provider="powerbi-powershell",
            error=_first_line(result.stderr) or "no Power BI session",
            hint="run `Connect-PowerBIServiceAccount` in PowerShell, then try again",
        )
    # the cmdlet may return "Bearer <token>" depending on version
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return TokenResult(True, token=token, provider="powerbi-powershell", account="current PowerShell session")


# ---------------------------------------------------------------------------
# Starting the browser prompt ourselves
# ---------------------------------------------------------------------------

_LOGIN_COMMANDS = {
    "azure-cli": ["az", "login"],
    "powerbi-powershell": None,  # filled in below: needs the resolved shell
}


def begin_browser_login(provider_id: str) -> TokenResult:
    """Run the provider's own login command, which opens the browser.

    This blocks until the person finishes signing in — that is the point,
    and it is why the caller runs it off the request thread.
    """
    if provider_id == "azure-cli":
        if shutil.which("az") is None:
            return TokenResult(False, provider=provider_id, error="the `az` command is not on PATH")
        result = _run(["az", "login"], timeout=600)
        if result.returncode != 0:
            return TokenResult(
                False, provider=provider_id, error=_first_line(result.stderr) or "az login failed"
            )
        return azure_cli_token()

    if provider_id == "powerbi-powershell":
        shell = _powershell()
        if shell is None:
            return TokenResult(False, provider=provider_id, error="no PowerShell on PATH")
        script = (
            "$ErrorActionPreference='Stop';"
            "Import-Module MicrosoftPowerBIMgmt.Profile -ErrorAction Stop;"
            "Connect-PowerBIServiceAccount | Out-Null;"
            "(Get-PowerBIAccessToken -AsString)"
        )
        result = _run([shell, "-NoProfile", "-Command", script], timeout=600)
        token = result.stdout.strip()
        if result.returncode != 0 or not token:
            return TokenResult(
                False,
                provider=provider_id,
                error=_first_line(result.stderr) or "Connect-PowerBIServiceAccount did not complete",
            )
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        return TokenResult(True, token=token, provider=provider_id, account="signed in")

    return TokenResult(False, provider=provider_id, error=f"unknown sign-in provider '{provider_id}'")


def token_from(provider_id: str) -> TokenResult:
    """A token from an *existing* session, without opening a browser."""
    if provider_id == "azure-cli":
        return azure_cli_token()
    if provider_id == "powerbi-powershell":
        return powerbi_powershell_token()
    return TokenResult(False, provider=provider_id, error=f"unknown sign-in provider '{provider_id}'")


def discover_providers() -> list[Provider]:
    """Every sign-in route on this machine, best first.

    Probing costs a subprocess each, which is worth it: telling someone
    'sign in' when the tool is not installed, or offering an app
    registration when they are already signed in to the Azure CLI, is the
    difference between this working and it not.
    """
    providers = [
        _safe_probe(powerbi_powershell_provider, "powerbi-powershell", "Power BI PowerShell"),
        _safe_probe(azure_cli_provider, "azure-cli", "Azure CLI"),
    ]
    providers.append(
        Provider(
            "msal-device-code",
            "Device code",
            # Not READY: nothing about it is ready until someone registers an
            # app in Entra. Showing it as ready next to a signed-in Azure CLI
            # would be telling the user the hardest option is the easiest.
            NEEDS_SETUP,
            "for tenants whose policy requires their own Entra app registration",
            needs_app_registration=True,
        )
    )
    order = {READY: 0, NEEDS_LOGIN: 1, UNKNOWN: 2, NOT_INSTALLED: 3, NEEDS_SETUP: 4}
    return sorted(providers, key=lambda p: (p.needs_app_registration, order.get(p.status, 4)))


def _safe_probe(probe, provider_id: str, name: str) -> Provider:
    """One provider failing to answer must not blank out the others."""
    try:
        return probe()
    except Exception as exc:  # noqa: BLE001 — see _run
        return Provider(provider_id, name, UNKNOWN, f"could not check this machine: {exc}")


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:300]
    return ""
