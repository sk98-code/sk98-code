"""Security -> RLS role mapping (Section 10).

Sources:
* Qlik Section Access (USERID / fields / OMIT)        -> RLS roles + OLS note
* Tableau user filters / data source filters          -> USERPRINCIPALNAME rules
* Spotfire data restrictions / personalized info links -> user-mapping-table rules

The output is always a *proposal*: identity mapping to Entra ID is an
organizational decision, so every role carries annotations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bimigrate.emit.tmdl import TmdlRole
from bimigrate.models.inventory import ArtifactInventory, SecurityRule


@dataclass
class RlsProposal:
    roles: list[TmdlRole] = field(default_factory=list)
    annotations: list[str] = field(default_factory=list)
    needs_user_mapping_table: bool = False


def build_rls(inv: ArtifactInventory, table_hint: str = "Data") -> RlsProposal:
    proposal = RlsProposal()
    for rule in inv.security:
        if rule.kind == "section_access":
            _from_section_access(rule, proposal, table_hint)
        elif rule.kind in ("user_filter", "datasource_filter"):
            _from_tableau_filter(rule, proposal, table_hint)
        elif rule.kind in ("data_restriction",):
            proposal.needs_user_mapping_table = True
            proposal.annotations.append(
                "Spotfire data restriction: build a user-mapping dimension and filter "
                f"'{table_hint}' via LOOKUPVALUE on USERPRINCIPALNAME()"
            )
    return proposal


def _from_section_access(rule: SecurityRule, proposal: RlsProposal, table: str) -> None:
    proposal.needs_user_mapping_table = True
    role = TmdlRole(
        name="SectionAccess_Users",
        table_filters={
            "UserSecurity": "[UserPrincipalName] = USERPRINCIPALNAME()",
        },
    )
    proposal.roles.append(role)
    proposal.annotations.extend(
        [
            "Section Access converted as a dynamic-RLS pattern: load the Section Access "
            "table (minus passwords) as a hidden 'UserSecurity' table related to the "
            "reduction fields, with a both-directions security relationship.",
            "USERID values must be re-mapped from <DOMAIN\\user> to Entra UPNs.",
            "OMIT columns map to object-level security (OLS) — configure via TMSL/Tabular "
            "Editor; OMIT has no TMDL-only equivalent in this generator.",
        ]
    )


def _from_tableau_filter(rule: SecurityRule, proposal: RlsProposal, table: str) -> None:
    definition = rule.definition
    column = rule.subject.strip("[]") or "Owner"
    dax = None
    if re.search(r"USERNAME\s*\(", definition, re.IGNORECASE):
        dax = f"['{column}'] = USERPRINCIPALNAME()" if "'" in column else f"[{column}] = USERPRINCIPALNAME()"
        proposal.annotations.append(
            f"Tableau USERNAME() filter on [{column}]: values must contain UPNs "
            "(user@domain) — verify source data format"
        )
    elif re.search(r"ISMEMBEROF\s*\(", definition, re.IGNORECASE):
        groups = re.findall(r"ISMEMBEROF\s*\(\s*['\"]([^'\"]+)['\"]\s*\)", definition, re.IGNORECASE)
        for group in groups or ["UnknownGroup"]:
            proposal.roles.append(TmdlRole(name=f"Group_{_safe(group)}", table_filters={}))
        proposal.annotations.append(
            "ISMEMBEROF() maps to RLS role *membership* (assign the Entra group to the "
            "role in the Power BI service), not to a DAX rule"
        )
        return
    if dax:
        proposal.roles.append(TmdlRole(name=f"RowFilter_{_safe(column)}", table_filters={table: dax}))


def _safe(name: str) -> str:
    return re.sub(r"\W+", "_", name).strip("_")
