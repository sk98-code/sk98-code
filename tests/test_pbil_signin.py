"""Browser sign-in: what is offered, and what each route needs first.

The contract is that a user is never told to do the hard thing when an
easier one is already available, and never told "auth failed" without the
command that fixes it.
"""

import subprocess

import pytest

from pbi_lineage.service import signin


class FakeRun:
    """Stands in for the subprocess calls, keyed by the first two argv words."""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def __call__(self, command, timeout=120.0):
        self.calls.append(command)
        for key, value in self.table.items():
            if " ".join(command).startswith(key) or key in " ".join(command):
                return subprocess.CompletedProcess(command, *value)
        return subprocess.CompletedProcess(command, 1, "", "not configured in the fake")


@pytest.fixture
def fake(monkeypatch):
    def install(table, *, which=None):
        runner = FakeRun(table)
        monkeypatch.setattr(signin, "_run", runner)
        monkeypatch.setattr(signin.shutil, "which", lambda name: (which or {}).get(name))
        return runner

    return install


def test_azure_cli_absent_is_reported_with_how_to_install(fake):
    fake({})
    provider = signin.azure_cli_provider()
    assert provider.status == signin.NOT_INSTALLED
    assert "installazurecli" in provider.install_hint


def test_azure_cli_present_but_signed_out_names_the_login_command(fake):
    fake({"az account show": (1, "", "Please run 'az login' to setup account.")}, which={"az": "/usr/bin/az"})
    provider = signin.azure_cli_provider()
    assert provider.status == signin.NEEDS_LOGIN
    assert provider.login_hint == "az login"


def test_azure_cli_signed_in_reports_the_account(fake):
    fake(
        {"az account show": (0, '{"user": {"name": "ada@corp.com"}}', "")},
        which={"az": "/usr/bin/az"},
    )
    provider = signin.azure_cli_provider()
    assert provider.status == signin.READY
    assert provider.detail == "ada@corp.com"


def test_azure_cli_token_asks_for_the_power_bi_audience(fake):
    """A token minted for any other resource is rejected with a bare 401."""
    runner = fake(
        {"az account get-access-token": (0, '{"accessToken": "abc", "subscription": "s"}', "")},
        which={"az": "/usr/bin/az"},
    )
    result = signin.azure_cli_token()
    assert result.ok and result.token == "abc"
    assert signin.POWERBI_RESOURCE in runner.calls[0]


def test_azure_cli_token_failure_carries_the_fix(fake):
    fake({"az account get-access-token": (1, "", "Please run 'az login'")}, which={"az": "/usr/bin/az"})
    result = signin.azure_cli_token()
    assert not result.ok
    assert "az login" in result.hint


def test_powershell_module_missing_is_not_reported_as_signed_out(fake):
    fake({"Get-Module -ListAvailable": (0, "missing", "")}, which={"pwsh": "/usr/bin/pwsh"})
    provider = signin.powerbi_powershell_provider()
    assert provider.status == signin.NOT_INSTALLED
    assert "Install-Module" in provider.install_hint


def test_powershell_signed_out_names_connect(fake):
    fake(
        {
            "Get-Module -ListAvailable": (0, "installed", ""),
            "Get-PowerBIAccessToken": (1, "", "Login first"),
        },
        which={"pwsh": "/usr/bin/pwsh"},
    )
    provider = signin.powerbi_powershell_provider()
    assert provider.status == signin.NEEDS_LOGIN
    assert provider.login_hint == "Connect-PowerBIServiceAccount"


def test_powershell_token_strips_the_bearer_prefix(fake):
    fake({"Get-PowerBIAccessToken": (0, "Bearer eyJabc\n", "")}, which={"pwsh": "/usr/bin/pwsh"})
    result = signin.powerbi_powershell_token()
    assert result.ok and result.token == "eyJabc"


def test_device_code_is_never_offered_ahead_of_a_working_sign_in(fake, monkeypatch):
    monkeypatch.setattr(
        signin,
        "powerbi_powershell_provider",
        lambda: signin.Provider("powerbi-powershell", "Power BI PowerShell", signin.READY),
    )
    monkeypatch.setattr(
        signin, "azure_cli_provider", lambda: signin.Provider("azure-cli", "Azure CLI", signin.NEEDS_LOGIN)
    )
    providers = signin.discover_providers()
    assert [p.id for p in providers] == ["powerbi-powershell", "azure-cli", "msal-device-code"]
    device = providers[-1]
    assert device.status == signin.NEEDS_SETUP, "an app registration is not a ready state"
    assert device.needs_app_registration


def test_a_token_is_never_in_a_repr():
    """A bearer token in a traceback or a log line is a leaked credential."""
    result = signin.TokenResult(True, token="secret-token-value", provider="azure-cli")
    assert "secret-token-value" not in repr(result)
